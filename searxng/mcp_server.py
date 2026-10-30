#!/usr/bin/env python3
"""
Built-in MCP (Model Context Protocol) Server for Home Assistant SearXNG Add-on.
Supports BOTH:
  - Streamable HTTP (2025-03-26 / 2024-11-05): POST /mcp, POST /sse, POST /
  - Legacy HTTP + SSE (2024-11-05): GET /sse (or GET /mcp with Accept: text/event-stream) + POST /messages

Zero external dependencies beyond Python standard library + lxml/urllib available in SearXNG.
"""

import html
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEARXNG_BASE_URL = os.environ.get("SEARXNG_INTERNAL_URL", "http://127.0.0.1:8080")
MCP_PORT = int(os.environ.get("MCP_PORT", "8889"))
MCP_MAX_RESULTS = int(os.environ.get("MCP_MAX_RESULTS", "8"))

# Active SSE sessions: session_id -> queue.Queue of JSON-RPC message strings
SSE_SESSIONS = {}
SSE_LOCK = threading.Lock()

SERVER_INFO = {
    "name": "searxng-ha-mcp",
    "version": "1.2.0",
}

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the web using the local privacy-respecting SearXNG metasearch engine. "
            "Returns titles, URLs, and text snippets for current events, facts, documentation, or general web queries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 8, max: 20).",
                    "default": MCP_MAX_RESULTS,
                },
                "categories": {
                    "type": "string",
                    "description": "Optional comma-separated categories (e.g. 'general', 'news', 'it', 'science', 'images', 'videos').",
                },
                "language": {
                    "type": "string",
                    "description": "Optional language code override (e.g. 'en', 'hu', 'de', 'all'). Leave empty to use server default.",
                },
                "time_range": {
                    "type": "string",
                    "description": "Optional time range filter: 'day', 'week', 'month', or 'year'.",
                    "enum": ["", "day", "week", "month", "year"],
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch and extract the readable plain-text content of a web page URL. "
            "Useful for reading full articles or documentation found via web_search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The HTTP/HTTPS URL of the webpage to read.",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum number of characters to return (default: 8000).",
                    "default": 8000,
                },
            },
            "required": ["url"],
        },
    },
]


def perform_searxng_search(args: dict) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: 'query' parameter is required."

    max_results = min(max(int(args.get("max_results") or MCP_MAX_RESULTS), 1), 25)
    params = {
        "q": query,
        "format": "json",
    }
    if args.get("categories"):
        params["categories"] = str(args["categories"]).strip()
    if args.get("language"):
        params["language"] = str(args["language"]).strip()
    if args.get("time_range"):
        params["time_range"] = str(args["time_range"]).strip()

    url = f"{SEARXNG_BASE_URL}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "HomeAssistant-SearXNG-MCP/1.2",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return f"Error querying SearXNG at {SEARXNG_BASE_URL}: {exc}"

    results = data.get("results") or []
    infoboxes = data.get("infoboxes") or []
    answers = data.get("answers") or []

    output_lines = []

    if answers:
        output_lines.append("### Direct Answers")
        for ans in answers:
            if isinstance(ans, dict):
                output_lines.append(f"- {ans.get('answer', ans)}")
            else:
                output_lines.append(f"- {ans}")
        output_lines.append("")

    if infoboxes:
        for box in infoboxes[:2]:
            title = box.get("infobox") or "Info"
            content = box.get("content") or ""
            if content:
                output_lines.append(f"### {title}\n{content}\n")

    if not results and not output_lines:
        return f"No results found for query: {query}"

    for idx, item in enumerate(results[:max_results], start=1):
        title = item.get("title") or "Untitled"
        link = item.get("url") or ""
        snippet = item.get("content") or item.get("snippet") or ""
        published = item.get("publishedDate") or ""
        engine = item.get("engine") or ""

        header = f"{idx}. **{title}**"
        if published:
            header += f" ({published})"
        output_lines.append(header)
        output_lines.append(f"   URL: {link}")
        if snippet:
            output_lines.append(f"   Snippet: {snippet}")
        if engine:
            output_lines.append(f"   Source: {engine}")
        output_lines.append("")

    return "\n".join(output_lines).strip()


def html_to_text(raw_html: str) -> str:
    # Remove script/style/noscript/svg/head tags
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg|head|header|footer|nav)\b.*?>.*?</\1>", " ", raw_html)
    # Replace block tags with newlines
    cleaned = re.sub(r"(?i)</?(p|div|br|li|tr|h[1-6]|blockquote|section|article)\b[^>]*>", "\n", cleaned)
    # Strip remaining tags
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    # Collapse whitespace
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in cleaned.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def perform_fetch_url(args: dict) -> str:
    target_url = str(args.get("url") or "").strip()
    if not target_url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"

    max_length = min(max(int(args.get("max_length") or 8000), 500), 50000)
    req = urllib.request.Request(
        target_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SearXNG-MCP/1.2)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read(512 * 1024)  # Read up to 512 KB
            raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"Error fetching {target_url}: {exc}"

    if "html" in content_type.lower() or "<html" in raw_text[:1000].lower():
        text = html_to_text(raw_text)
    else:
        text = raw_text.strip()

    if len(text) > max_length:
        text = text[:max_length] + "\n\n... [Content truncated]"
    return text or "(No readable text extracted)"


def handle_jsonrpc_message(msg: dict) -> dict | None:
    """Process a single JSON-RPC 2.0 request or notification."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id) do not return a response object
    if msg_id is None:
        return None

    if method == "initialize":
        client_Version = params.get("protocolVersion") or "2024-11-05"
        supported_versions = {"2024-11-05", "2025-03-26", "2025-06-18"}
        negotiated = client_Version if client_Version in supported_versions else "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {},
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS,
            },
        }

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}

        if tool_name in ("web_search", "searxng_web_search", "search"):
            text_out = perform_searxng_search(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_out}],
                    "isError": False,
                },
            }
        elif tool_name in ("fetch_url", "web_url_read"):
            text_out = perform_fetch_url(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_out}],
                    "isError": False,
                },
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32602,
                    "message": f"Unknown tool: {tool_name}",
                },
            }

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


class MCPHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Keep logs clean
        pass

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Session-Id, Last-Event-ID")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        # Streamable HTTP session termination
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        accept = self.headers.get("Accept", "")

        # Health/info endpoint if opened in browser without SSE Accept header on /health
        if parsed.path == "/health":
            body = json.dumps({"status": "ok", "server": SERVER_INFO, "tools": [t["name"] for t in TOOLS]}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Legacy MCP SSE transport (or GET /sse, GET /mcp, GET /)
        if parsed.path in ("/sse", "/mcp", "/") or "text/event-stream" in accept:
            session_id = uuid.uuid4().hex
            q = queue.Queue()
            with SSE_LOCK:
                SSE_SESSIONS[session_id] = q

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            try:
                # Send the endpoint event per MCP 2024-11-05 HTTP+SSE spec
                endpoint_url = f"/messages?session_id={session_id}"
                init_event = f"event: endpoint\ndata: {endpoint_url}\n\n"
                self.wfile.write(init_event.encode("utf-8"))
                self.wfile.flush()

                while True:
                    try:
                        msg_str = q.get(timeout=15)
                        payload = f"event: message\ndata: {msg_str}\n\n"
                        self.wfile.write(payload.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        # Keep-alive comment
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with SSE_LOCK:
                    SSE_SESSIONS.pop(session_id, None)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed.query)
        session_id = (query_params.get("session_id") or query_params.get("sessionId") or [None])[0]

        content_len = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace") if content_len > 0 else ""

        try:
            payload = json.loads(raw_body)
        except Exception as exc:
            err = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}).encode("utf-8")
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        # Handle batch or single JSON-RPC message
        if isinstance(payload, list):
            responses = []
            for item in payload:
                if isinstance(item, dict):
                    resp = handle_jsonrpc_message(item)
                    if resp is not None:
                        responses.append(resp)
            response_obj = responses if responses else None
        elif isinstance(payload, dict):
            response_obj = handle_jsonrpc_message(payload)
        else:
            response_obj = None

        # If client is using Legacy SSE transport (/messages?session_id=...), push response to SSE queue
        if session_id:
            with SSE_LOCK:
                q = SSE_SESSIONS.get(session_id)
            if q is not None:
                if response_obj is not None:
                    q.put(json.dumps(response_obj))
                self.send_response(202)
                self._send_cors_headers()
                body = b"Accepted"
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        # Streamable HTTP transport (POST /mcp, POST /sse, POST /)
        mcp_session_header = self.headers.get("Mcp-Session-Id") or uuid.uuid4().hex
        if response_obj is None:
            # Notification accepted
            self.send_response(202)
            self._send_cors_headers()
            self.send_header("Mcp-Session-Id", mcp_session_header)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = json.dumps(response_obj).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Mcp-Session-Id", mcp_session_header)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", MCP_PORT), MCPHTTPHandler)
    print(
        f"INFO: SearXNG MCP Server listening on 0.0.0.0:{MCP_PORT} "
        f"(endpoints: /mcp, /sse) -> querying {SEARXNG_BASE_URL}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
