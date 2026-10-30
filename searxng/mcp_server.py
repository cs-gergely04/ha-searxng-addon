#!/usr/bin/env python3
"""
Built-in MCP (Model Context Protocol) Server for Home Assistant SearXNG Add-on.
Supports BOTH:
  - Streamable HTTP (2025-03-26 / 2024-11-05): POST /mcp, POST /sse, POST /
  - Legacy HTTP + SSE (2024-11-05): GET /sse (or GET /mcp with Accept: text/event-stream) + POST /messages

Features:
  - Dynamic Preconfigured Search Tasks & Aliases (Presets) loaded live from /config/presets.yml
  - Dedicated MCP tool per preset (e.g., search_local_news, search_it_news, search_tech_docs)
  - Parallel full-article text extraction (fetch_top_n) so LLMs get complete context in 1 tool call
"""

import concurrent.futures
import html
import json
import os
import queue
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import yaml
except ImportError:
    yaml = None

SEARXNG_BASE_URL = os.environ.get("SEARXNG_INTERNAL_URL", "http://127.0.0.1:8080")
MCP_PORT = int(os.environ.get("MCP_PORT", "8889"))
MCP_MAX_RESULTS = int(os.environ.get("MCP_MAX_RESULTS", "8"))
PRESETS_PATH = os.environ.get("SEARXNG_PRESETS_PATH", "/config/presets.yml")

# Active SSE sessions: session_id -> queue.Queue of JSON-RPC message strings
SSE_SESSIONS = {}
SSE_LOCK = threading.Lock()

SERVER_INFO = {
    "name": "searxng-ha-mcp",
    "version": "1.3.1",
}

DEFAULT_PRESETS = [
    {
        "alias": "local_news",
        "description": (
            "Search local Vojvodina and Subotica news in Hungarian (Pannon RTV). "
            "Automatically extracts the full text of the top 3 news articles."
        ),
        "sites": ["pannonrtv.com", "pannonrtv.rs"],
        "default_query": "hírek vajdaság szabadka",
        "query_template": "",
        "categories": "general,news",
        "engines": "",
        "language": "hu",
        "time_range": "week",
        "max_results": 6,
        "fetch_top_n": 3,
    },
    {
        "alias": "it_news",
        "description": (
            "Search latest IT, software engineering, AI, cybersecurity, and technology news. "
            "Automatically extracts full article text for top results."
        ),
        "sites": [],
        "default_query": "technology software AI cybersecurity news",
        "query_template": "",
        "categories": "it,news",
        "engines": "",
        "language": "en",
        "time_range": "week",
        "max_results": 8,
        "fetch_top_n": 2,
    },
    {
        "alias": "tech_docs",
        "description": (
            "Search technical documentation, GitHub issues, StackOverflow, and Home Assistant guides "
            "with full-page content extraction."
        ),
        "sites": ["github.com", "stackoverflow.com", "docs.home-assistant.io", "community.home-assistant.io"],
        "default_query": "",
        "query_template": "",
        "categories": "it,general",
        "engines": "",
        "language": "en",
        "time_range": "",
        "max_results": 6,
        "fetch_top_n": 2,
    },
]


def normalize_alias(raw_alias: str) -> str:
    """Normalize an alias string like 'IT-News' or 'local news' into 'it_news' / 'local_news'."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw_alias or "").strip().lower()).strip("_")
    return cleaned or "custom"


def tool_name_for_alias(alias: str) -> str:
    norm = normalize_alias(alias)
    if norm.startswith("search_"):
        return norm
    return f"search_{norm}"


def load_presets() -> dict[str, dict]:
    """Load presets dynamically from /config/presets.yml (with fallback to DEFAULT_PRESETS)."""
    raw_list = None
    if yaml is not None and os.path.exists(PRESETS_PATH):
        try:
            with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
                if isinstance(doc, dict):
                    raw_list = doc.get("presets")
                elif isinstance(doc, list):
                    raw_list = doc
        except Exception:
            raw_list = None

    if not raw_list:
        raw_list = DEFAULT_PRESETS

    presets_map = {}
    for item in raw_list:
        if not isinstance(item, dict) or not item.get("alias"):
            continue
        raw_alias = str(item["alias"]).strip()
        norm = normalize_alias(raw_alias)
        sites_val = item.get("sites") or []
        if isinstance(sites_val, str):
            sites_list = [s.strip() for s in sites_val.split(",") if s.strip()]
        elif isinstance(sites_val, list):
            sites_list = [str(s).strip() for s in sites_val if str(s).strip()]
        else:
            sites_list = []

        presets_map[norm] = {
            "alias": raw_alias,
            "norm_alias": norm,
            "tool_name": tool_name_for_alias(norm),
            "description": str(item.get("description") or f"Preconfigured search task for {raw_alias}"),
            "sites": sites_list,
            "default_query": str(item.get("default_query") or "").strip(),
            "query_template": str(item.get("query_template") or "").strip(),
            "categories": str(item.get("categories") or "").strip(),
            "engines": str(item.get("engines") or "").strip(),
            "language": str(item.get("language") or "").strip(),
            "time_range": str(item.get("time_range") or "").strip(),
            "max_results": int(item.get("max_results") if item.get("max_results") is not None else MCP_MAX_RESULTS),
            "fetch_top_n": int(item.get("fetch_top_n") if item.get("fetch_top_n") is not None else 0),
        }

    return presets_map


def build_tools_list() -> list[dict]:
    """Dynamically build the MCP tools list including web_search, fetch_url, and one tool per preset."""
    presets = load_presets()
    preset_aliases = list(presets.keys())
    preset_summary = ", ".join(
        f"'{p['norm_alias']}' ({p['description'][:70]}...)" if len(p["description"]) > 70 else f"'{p['norm_alias']}' ({p['description']})"
        for p in presets.values()
    )

    tools = [
        {
            "name": "web_search",
            "description": (
                "Search the web using the local privacy-respecting SearXNG metasearch engine. "
                "Supports ad-hoc queries OR preconfigured search presets ("
                f"available presets: {preset_summary}). "
                "Can also automatically fetch and extract the full text of the top N results via 'fetch_top_n'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string. Optional if a 'preset' with a default_query is selected.",
                    },
                    "preset": {
                        "type": "string",
                        "description": f"Optional preset alias to apply domain filters, language, and deep article extraction. Available: {', '.join(preset_aliases)}",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Maximum number of results to return (default: {MCP_MAX_RESULTS}, max: 25).",
                        "default": MCP_MAX_RESULTS,
                    },
                    "fetch_top_n": {
                        "type": "integer",
                        "description": "Number of top search result URLs to automatically fetch and extract full readable article text from (0-10). Default: 0 (or preset default).",
                        "default": 0,
                    },
                    "sites": {
                        "type": "string",
                        "description": "Optional comma-separated domain restriction (e.g. 'pannonrtv.com, pannonrtv.rs').",
                    },
                    "categories": {
                        "type": "string",
                        "description": "Optional comma-separated categories (e.g. 'general', 'news', 'it', 'science').",
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional language code override (e.g. 'en', 'hu', 'de', 'all').",
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Optional time range filter: 'day', 'week', 'month', or 'year'. Omit for any time.",
                        "enum": ["day", "week", "month", "year"],
                    },
                },
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

    # Add a dedicated MCP tool for each configured preset!
    for norm_alias, preset in presets.items():
        details = []
        if preset["sites"]:
            details.append(f"sites: {', '.join(preset['sites'])}")
        if preset["language"]:
            details.append(f"lang: {preset['language']}")
        if preset["time_range"]:
            details.append(f"time_range: {preset['time_range']}")
        if preset["fetch_top_n"] > 0:
            details.append(f"auto-reads top {preset['fetch_top_n']} full articles")
        meta_suffix = f" [Preset config: {'; '.join(details)}]" if details else ""

        tools.append(
            {
                "name": preset["tool_name"],
                "description": f"{preset['description']}{meta_suffix}",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Optional specific search topic or keywords. "
                                f"Leave empty to search general/latest items using preset defaults ('{preset['default_query']}')."
                            ),
                        },
                        "time_range": {
                            "type": "string",
                            "description": f"Optional time range override ('day', 'week', 'month', 'year'). Default: '{preset['time_range'] or 'any'}'.",
                            "enum": ["day", "week", "month", "year"],
                        },
                        "fetch_top_n": {
                            "type": "integer",
                            "description": f"Number of top articles to automatically read in full (0-10). Default for this preset: {preset['fetch_top_n']}.",
                            "default": preset["fetch_top_n"],
                        },
                    },
                },
            }
        )

    return tools


def html_to_text(raw_html: str) -> str:
    # Remove script/style/noscript/svg/head/nav/footer tags
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg|head|header|footer|nav|aside|form)\b.*?>.*?</\1>", " ", raw_html)
    # Replace block tags with newlines
    cleaned = re.sub(r"(?i)</?(p|div|br|li|tr|h[1-6]|blockquote|section|article|main)\b[^>]*>", "\n", cleaned)
    # Strip remaining tags
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    # Collapse whitespace
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in cleaned.splitlines()]
    lines = [line for line in lines if len(line) > 1]
    return "\n".join(lines)


def fetch_single_url_text(target_url: str, max_length: int = 4500, timeout: int = 10) -> str:
    if not target_url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"

    req = urllib.request.Request(
        target_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read(512 * 1024)
            raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"[Could not fetch full text from {target_url}: {exc}]"

    if "html" in content_type.lower() or "<html" in raw_text[:1000].lower():
        text = html_to_text(raw_text)
    else:
        text = raw_text.strip()

    if len(text) > max_length:
        text = text[:max_length] + "\n... [Article truncated]"
    return text or "(No readable text extracted)"


def perform_fetch_url(args: dict) -> str:
    target_url = str(args.get("url") or "").strip()
    max_length = min(max(int(args.get("max_length") or 8000), 500), 50000)
    return fetch_single_url_text(target_url, max_length=max_length, timeout=15)


def build_effective_query(user_query: str, default_query: str, query_template: str, sites: list[str]) -> str:
    base_q = user_query.strip() if user_query.strip() else default_query.strip()
    if query_template and "{query}" in query_template:
        base_q = query_template.replace("{query}", base_q).strip()
    elif query_template and base_q:
        base_q = f"{base_q} {query_template}".strip()
    elif query_template:
        base_q = query_template.strip()

    clean_sites = [s.strip() for s in sites if s.strip()]
    if clean_sites:
        if len(clean_sites) == 1:
            site_expr = f"site:{clean_sites[0]}"
        else:
            site_expr = "(" + " OR ".join(f"site:{s}" for s in clean_sites) + ")"
        if base_q:
            return f"{base_q} {site_expr}"
        return site_expr

    return base_q


def query_searxng_json(q: str, categories: str = "", engines: str = "", language: str = "", time_range: str = "") -> dict:
    params = {
        "q": q,
        "format": "json",
    }
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range

    url = f"{SEARXNG_BASE_URL}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "HomeAssistant-SearXNG-MCP/1.3",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def execute_search_with_preset(args: dict, forced_preset_alias: str | None = None) -> str:
    presets = load_presets()
    preset_key = forced_preset_alias or args.get("preset")
    preset = None
    if preset_key:
        norm_key = normalize_alias(str(preset_key))
        preset = presets.get(norm_key)
        if not preset and norm_key.startswith("search_"):
            preset = presets.get(norm_key[len("search_"):])

    user_query = str(args.get("query") or "").strip()
    default_query = preset["default_query"] if preset else ""
    query_template = preset["query_template"] if preset else ""

    raw_sites_arg = args.get("sites")
    if raw_sites_arg:
        sites = [s.strip() for s in str(raw_sites_arg).split(",") if s.strip()]
    elif preset:
        sites = preset["sites"]
    else:
        sites = []

    effective_query = build_effective_query(user_query, default_query, query_template, sites)
    if not effective_query:
        return "Error: Please provide a 'query' or use a preset that defines 'default_query' or 'sites'."

    categories = str(args.get("categories") if args.get("categories") is not None else (preset["categories"] if preset else "")).strip()
    engines = str(args.get("engines") if args.get("engines") is not None else (preset["engines"] if preset else "")).strip()
    language = str(args.get("language") if args.get("language") is not None else (preset["language"] if preset else "")).strip()
    time_range = str(args.get("time_range") if args.get("time_range") is not None else (preset["time_range"] if preset else "")).strip()
    if time_range.lower() in ("any", "none", "all"):
        time_range = ""

    default_max = preset["max_results"] if preset else MCP_MAX_RESULTS
    max_results = min(max(int(args.get("max_results") if args.get("max_results") is not None else default_max), 1), 25)

    default_fetch_n = preset["fetch_top_n"] if preset else 0
    fetch_top_n = min(max(int(args.get("fetch_top_n") if args.get("fetch_top_n") is not None else default_fetch_n), 0), 10)

    try:
        data = query_searxng_json(
            effective_query,
            categories=categories,
            engines=engines,
            language=language,
            time_range=time_range,
        )
        results = data.get("results") or []

        # Smart fallback: if a strict time_range (like 'day' or 'week') or default_query with site: returned 0 results,
        # retry once without time_range (or with just the user query + site:) so local news sites never return empty!
        if not results and time_range:
            data = query_searxng_json(
                effective_query,
                categories=categories,
                engines=engines,
                language=language,
                time_range="",
            )
            results = data.get("results") or []

        if not results and sites and not user_query and default_query:
            site_only_query = build_effective_query("", "", "", sites)
            data = query_searxng_json(
                site_only_query,
                categories="general",
                engines=engines,
                language=language,
                time_range="",
            )
            results = data.get("results") or []
    except Exception as exc:
        return f"Error querying SearXNG at {SEARXNG_BASE_URL}: {exc}"

    infoboxes = data.get("infoboxes") or []
    answers = data.get("answers") or []
    top_results = results[:max_results]

    output_lines = []
    if preset:
        output_lines.append(f"### Preset: `{preset['alias']}` | Executed Query: `{effective_query}`")
        output_lines.append("")

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

    if not top_results and not answers and not infoboxes:
        return f"No results found for query: {effective_query}"

    output_lines.append("### Search Results")
    for idx, item in enumerate(top_results, start=1):
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

    # Deep Mode: Fetch full article text for the top N results in parallel
    if fetch_top_n > 0 and top_results:
        urls_to_fetch = []
        seen_urls = set()
        for item in top_results:
            u = (item.get("url") or "").strip()
            if u and u not in seen_urls:
                seen_urls.add(u)
                urls_to_fetch.append((item.get("title") or u, u))
            if len(urls_to_fetch) >= fetch_top_n:
                break

        if urls_to_fetch:
            output_lines.append(f"### Extracted Full Article Contents (Top {len(urls_to_fetch)})")
            output_lines.append("")
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls_to_fetch), 5)) as pool:
                ordered_futures = [
                    pool.submit(fetch_single_url_text, url, 4000, 10)
                    for _, url in urls_to_fetch
                ]
                for idx, ((title, url), fut) in enumerate(zip(urls_to_fetch, ordered_futures), start=1):
                    try:
                        article_text = fut.result(timeout=12)
                    except Exception as exc:
                        article_text = f"[Timed out or failed reading {url}: {exc}]"
                    output_lines.append(f"#### [{idx}] {title}")
                    output_lines.append(f"Source URL: {url}")
                    output_lines.append(article_text)
                    output_lines.append("")

    return "\n".join(output_lines).strip()


def handle_jsonrpc_message(msg: dict) -> dict | None:
    """Process a single JSON-RPC 2.0 request or notification."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id) do not return a response object
    if msg_id is None:
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion") or "2024-11-05"
        supported_versions = {"2024-11-05", "2025-03-26", "2025-06-18"}
        negotiated = client_version if client_version in supported_versions else "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": True},
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
                "tools": build_tools_list(),
            },
        }

    if method == "tools/call":
        tool_name = str(params.get("name") or "").strip()
        tool_args = params.get("arguments") or {}
        presets = load_presets()

        if tool_name in ("web_search", "searxng_web_search", "search"):
            text_out = execute_search_with_preset(tool_args, forced_preset_alias=None)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_out}],
                    "isError": False,
                },
            }

        if tool_name in ("fetch_url", "web_url_read"):
            text_out = perform_fetch_url(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_out}],
                    "isError": False,
                },
            }

        # Check if tool_name matches any configured preset tool (e.g. search_local_news or local_news)
        norm_tool = normalize_alias(tool_name)
        matched_alias = None
        if norm_tool in presets:
            matched_alias = norm_tool
        elif norm_tool.startswith("search_") and norm_tool[len("search_"):] in presets:
            matched_alias = norm_tool[len("search_"):]

        if matched_alias:
            text_out = execute_search_with_preset(tool_args, forced_preset_alias=matched_alias)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_out}],
                    "isError": False,
                },
            }

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
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        accept = self.headers.get("Accept", "")

        if parsed.path == "/health":
            tools = build_tools_list()
            body = json.dumps({
                "status": "ok",
                "server": SERVER_INFO,
                "tools": [t["name"] for t in tools],
                "presets": list(load_presets().keys()),
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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

        mcp_session_header = self.headers.get("Mcp-Session-Id") or uuid.uuid4().hex
        if response_obj is None:
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
    presets = load_presets()
    server = ThreadingHTTPServer(("0.0.0.0", MCP_PORT), MCPHTTPHandler)
    print(
        f"INFO: SearXNG MCP Server v1.3.0 listening on 0.0.0.0:{MCP_PORT} "
        f"(endpoints: /mcp, /sse) with {len(presets)} presets ({', '.join(presets.keys())})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
