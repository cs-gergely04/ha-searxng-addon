#!/usr/bin/env python3
"""
Built-in MCP (Model Context Protocol) Server & Live Search Inspector for Home Assistant SearXNG Add-on.
Supports:
  - Streamable HTTP (2025-03-26 / 2024-11-05): POST /mcp, POST /sse, POST /
  - Legacy HTTP + SSE (2024-11-05): GET /sse (or GET /mcp with Accept: text/event-stream) + POST /messages
  - Live Search Inspector Dashboard: GET /logs (and JSON API at GET /logs/json, POST /logs/clear)
  - Native Voice Satellite Card Image & Media Panel Rendering:
      * Dedicated `search_images` tool returning top-level `results` ([{image_url, title, thumbnail_url, ...}])
        and `auto_display` so Voice Satellite Card automatically renders the image grid or fullscreen lightbox!
      * Automatic `featured_image` extraction on web/news searches so Voice Satellite displays article thumbnails.
"""

from collections import deque
import concurrent.futures
from datetime import datetime, timezone
import html
import json
import os
import queue
import random
import re
import threading
import time
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
MCP_MAX_RESULTS = int(os.environ.get("MCP_MAX_RESULTS", "5"))
PRESETS_PATH = os.environ.get("SEARXNG_PRESETS_PATH", "/config/presets.yml")
HISTORY_PATH = os.environ.get("SEARXNG_HISTORY_PATH", "/config/search_history.jsonl")
MAX_HISTORY_ITEMS = 150

# Active SSE sessions: session_id -> queue.Queue of JSON-RPC message strings
SSE_SESSIONS = {}
SSE_LOCK = threading.Lock()

# Recently shown URLs per preset/query so repeated calls rotate to unseen stories/images first
RECENTLY_SHOWN_URLS = deque(maxlen=50)
HISTORY_LOCK = threading.Lock()
SEARCH_HISTORY = deque(maxlen=MAX_HISTORY_ITEMS)

SERVER_INFO = {
    "name": "searxng-ha-mcp",
    "version": "1.5.0",
}

DEFAULT_PRESETS = [
    {
        "alias": "local_news",
        "description": (
            "Get a randomized digest of 4 local Vojvodina and Subotica news stories in Hungarian (Pannon RTV) "
            "with headlines and short leads. Summarize each in 1 sentence and let the user pick one to read in detail."
        ),
        "sites": ["pannonrtv.com", "pannonrtv.rs"],
        "default_query": "hírek vajdaság szabadka",
        "query_template": "",
        "categories": "general,news",
        "engines": "",
        "language": "hu",
        "time_range": "week",
        "response_mode": "digest",
        "shuffle_results": True,
        "max_results": 4,
        "digest_words": 25,
        "fetch_top_n": 0,
    },
    {
        "alias": "it_news",
        "description": (
            "Get a randomized digest of 4 latest IT, software engineering, AI, and technology news stories "
            "with headlines and short leads. Summarize each in 1 sentence and let the user pick one to explore."
        ),
        "sites": [],
        "default_query": "technology software AI cybersecurity news",
        "query_template": "",
        "categories": "it,news",
        "engines": "",
        "language": "en",
        "time_range": "week",
        "response_mode": "digest",
        "shuffle_results": True,
        "max_results": 4,
        "digest_words": 25,
        "fetch_top_n": 0,
    },
    {
        "alias": "detailed_search",
        "description": (
            "Deep detailed search for a specific topic, news story, or question. Finds the top matching result "
            "and extracts its full article text so you can give a comprehensive answer."
        ),
        "sites": [],
        "default_query": "",
        "query_template": "",
        "categories": "general,news",
        "engines": "",
        "language": "",
        "time_range": "",
        "response_mode": "detailed",
        "shuffle_results": False,
        "max_results": 3,
        "digest_words": 35,
        "fetch_top_n": 1,
    },
    {
        "alias": "tech_docs",
        "description": (
            "Detailed technical documentation search across GitHub, StackOverflow, and Home Assistant docs "
            "with full-page content extraction of the top result."
        ),
        "sites": ["github.com", "stackoverflow.com", "docs.home-assistant.io", "community.home-assistant.io"],
        "default_query": "",
        "query_template": "",
        "categories": "it,general",
        "engines": "",
        "language": "en",
        "time_range": "",
        "response_mode": "detailed",
        "shuffle_results": False,
        "max_results": 3,
        "digest_words": 35,
        "fetch_top_n": 1,
    },
]


def load_history_from_disk():
    if not os.path.exists(HISTORY_PATH):
        return
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        SEARCH_HISTORY.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass


def record_search_execution(exec_obj: dict):
    """Save SearchExecutionObject to in-memory ring buffer, /config/search_history.jsonl, and stdout log."""
    with HISTORY_LOCK:
        SEARCH_HISTORY.append(exec_obj)
        try:
            parent_dir = os.path.dirname(HISTORY_PATH)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                for item in SEARCH_HISTORY:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"WARNING: Could not write search history to {HISTORY_PATH}: {exc}", flush=True)

    req_info = exec_obj.get("effective_searxng_request") or {}
    sel_info = exec_obj.get("selection_and_randomization") or {}
    selected_titles = [
        f"#{r.get('rank')}(orig #{r.get('original_searx_rank')}): {r.get('title', '')[:45]}"
        for r in (sel_info.get("selected_results") or [])
    ]
    print(
        f"[MCP SEARCH {exec_obj.get('search_id')}] "
        f"tool={exec_obj.get('tool_name')} | preset={exec_obj.get('preset_used')} | "
        f"mode={sel_info.get('response_mode')} | shuffle={sel_info.get('shuffle_enabled')} | "
        f"query={json.dumps(req_info.get('effective_query', ''), ensure_ascii=False)} | "
        f"candidates={exec_obj.get('searxng_raw_stats', {}).get('total_candidates_returned', 0)} -> "
        f"selected={len(selected_titles)} {json.dumps(selected_titles, ensure_ascii=False)} | "
        f"chars={exec_obj.get('final_llm_response_chars', 0)} | "
        f"duration={exec_obj.get('duration_ms')}ms",
        flush=True,
    )


def normalize_alias(raw_alias: str) -> str:
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

        default_is_news = norm in ("local_news", "it_news")
        resp_mode = str(item.get("response_mode") or ("digest" if default_is_news else "detailed")).strip().lower()
        if resp_mode not in ("digest", "detailed"):
            resp_mode = "digest"

        if "shuffle_results" in item and item["shuffle_results"] is not None:
            shuffle_val = bool(item["shuffle_results"])
        else:
            shuffle_val = default_is_news

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
            "response_mode": resp_mode,
            "shuffle_results": shuffle_val,
            "max_results": int(item.get("max_results") if item.get("max_results") is not None else (4 if resp_mode == "digest" else 3)),
            "digest_words": int(item.get("digest_words") if item.get("digest_words") is not None else 25),
            "fetch_top_n": int(item.get("fetch_top_n") if item.get("fetch_top_n") is not None else (0 if resp_mode == "digest" else 1)),
        }

    return presets_map


def truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def is_generic_landing_url(url: str) -> bool:
    """Detect bare domain homepages or generic category index URLs so we prefer real article URLs."""
    try:
        parsed = urllib.parse.urlparse(url)
        path = (parsed.path or "").strip("/")
        if not path:
            return True
        segments = [s for s in path.split("/") if s]
        if len(segments) == 1 and len(segments[0]) < 16 and "-" not in segments[0]:
            return True
        if segments[0].lower() in ("rovat", "category", "tag", "topic", "rubrika", "author") and len(segments) <= 2:
            return True
    except Exception:
        pass
    return False


def extract_thumbnail_from_item(item: dict) -> str:
    """Extract a valid HTTP/HTTPS image or thumbnail URL from a SearXNG result item."""
    for key in ("img_src", "thumbnail", "thumbnail_src"):
        val = (item.get(key) or "").strip()
        if val.startswith(("http://", "https://")):
            return val
    return ""


def select_and_randomize_candidates(
    raw_results: list[dict],
    max_results: int,
    shuffle_enabled: bool,
    filter_landing_pages: bool = True,
) -> tuple[list[dict], dict]:
    """
    Filter, deduplicate, and optionally randomize results from a larger candidate pool (up to 22 items)
    while rotating away from recently shown URLs.
    """
    candidates = []
    landing_fallbacks = []
    seen_urls = set()
    seen_titles = set()

    for idx, item in enumerate(raw_results[:24], start=1):
        url = (item.get("img_src") or item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url:
            continue
        norm_title = re.sub(r"\W+", "", title.lower()) if title else ""
        if url in seen_urls or (norm_title and len(norm_title) > 12 and norm_title in seen_titles):
            continue
        seen_urls.add(url)
        if norm_title:
            seen_titles.add(norm_title)

        entry = dict(item)
        entry["_original_rank"] = idx
        if filter_landing_pages and is_generic_landing_url(item.get("url") or ""):
            landing_fallbacks.append(entry)
        else:
            candidates.append(entry)

    if len(candidates) < max_results:
        candidates.extend(landing_fallbacks[: max_results - len(candidates)])

    if not shuffle_enabled or len(candidates) <= 1:
        selected = candidates[:max_results]
        return selected, {
            "pool_size": len(candidates),
            "unseen_pool_size": len(candidates),
            "rotated_away_from_recent": 0,
        }

    recent_set = set(RECENTLY_SHOWN_URLS)
    unseen = [c for c in candidates if (c.get("img_src") or c.get("url")) not in recent_set]
    seen_recently = [c for c in candidates if (c.get("img_src") or c.get("url")) in recent_set]

    random.shuffle(unseen)
    random.shuffle(seen_recently)

    combined_pool = unseen + seen_recently
    selected = combined_pool[:max_results]

    for s in selected:
        u = s.get("img_src") or s.get("url")
        if u:
            RECENTLY_SHOWN_URLS.append(u)

    return selected, {
        "pool_size": len(candidates),
        "unseen_pool_size": len(unseen),
        "rotated_away_from_recent": len(seen_recently),
    }


def build_tools_list() -> list[dict]:
    """Dynamically build the MCP tools list including search_images, web_search, fetch_url, and one tool per preset."""
    presets = load_presets()
    preset_aliases = list(presets.keys())
    preset_summary = ", ".join(
        f"'{p['norm_alias']}' ({p['response_mode']})"
        for p in presets.values()
    )

    tools = [
        {
            "name": "search_images",
            "description": (
                "Search the internet for images matching a query and display them visually on the user's "
                "Voice Satellite screen (thumbnail grid or fullscreen). "
                "ALWAYS call this tool when the user asks to find, search for, show, or see images/pictures/photos."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The image search query (e.g. 'golden retriever', 'Eiffel Tower at night').",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of image results to display (1-10, default: 4).",
                        "default": 4,
                    },
                    "auto_display": {
                        "type": "boolean",
                        "description": (
                            "Set to true when the user wants to see a single specific image displayed fullscreen "
                            "immediately (e.g. 'show me the Mona Lisa'). Set to false when browsing multiple images."
                        ),
                        "default": False,
                    },
                    "shuffle_results": {
                        "type": "boolean",
                        "description": "If true, picks a randomized set of matching images so repeated requests show fresh pictures.",
                        "default": False,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "web_search",
            "description": (
                "Search the web using the local SearXNG metasearch engine. "
                f"Available presets: {preset_summary}. "
                "Use response_mode='digest' for a randomized 3-5 item headline+lead overview, or response_mode='detailed' to extract full context of the top result."
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
                        "description": f"Optional preset alias to apply. Available: {', '.join(preset_aliases)}",
                    },
                    "response_mode": {
                        "type": "string",
                        "description": "'digest' returns headlines + first ~25 words of 3-5 items; 'detailed' extracts full text of the top result.",
                        "enum": ["digest", "detailed"],
                    },
                    "shuffle_results": {
                        "type": "boolean",
                        "description": "If true, randomly picks results from the candidate pool so repeated searches show fresh stories.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Number of results to return (default: {MCP_MAX_RESULTS}, max: 15).",
                        "default": MCP_MAX_RESULTS,
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
                "Fetch and extract the readable plain-text content of a specific web page URL. "
                "Call this when the user asks to hear more about one of the headlines from a news digest."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP/HTTPS URL of the webpage/article to read in detail.",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum number of characters to return (default: 2200 for voice safety).",
                        "default": 2200,
                    },
                },
                "required": ["url"],
            },
        },
    ]

    for norm_alias, preset in presets.items():
        details = [f"mode: {preset['response_mode']}"]
        if preset["shuffle_results"]:
            details.append("randomized rotation")
        if preset["sites"]:
            details.append(f"sites: {', '.join(preset['sites'])}")
        if preset["language"]:
            details.append(f"lang: {preset['language']}")
        meta_suffix = f" [{' | '.join(details)}]"

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
                                f"Leave empty for general/latest items ('{preset['default_query']}')."
                            ),
                        },
                        "response_mode": {
                            "type": "string",
                            "description": f"Override output style ('digest' for short headlines + first {preset['digest_words']} words, or 'detailed' for full article text). Default: '{preset['response_mode']}'.",
                            "enum": ["digest", "detailed"],
                        },
                        "time_range": {
                            "type": "string",
                            "description": f"Optional time range override ('day', 'week', 'month', 'year'). Default: '{preset['time_range'] or 'any'}'.",
                            "enum": ["day", "week", "month", "year"],
                        },
                    },
                },
            }
        )

    return tools


def html_to_text(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg|head|header|footer|nav|aside|form)\b.*?>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?i)</?(p|div|br|li|tr|h[1-6]|blockquote|section|article|main)\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in cleaned.splitlines()]
    lines = [line for line in lines if len(line) > 25]
    return "\n".join(lines)


def fetch_single_url_text(target_url: str, max_length: int = 2000, timeout: int = 5) -> str:
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
            raw_bytes = resp.read(256 * 1024)
            raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"[Could not fetch full text from {target_url}: {exc}]"

    if "html" in content_type.lower() or "<html" in raw_text[:1000].lower():
        text = html_to_text(raw_text)
    else:
        text = raw_text.strip()

    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text or "(No readable text extracted)"


def perform_fetch_url(args: dict, tool_name: str = "fetch_url") -> dict:
    start_ts = time.time()
    search_id = f"fetch_{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
    target_url = str(args.get("url") or "").strip()
    max_length = min(max(int(args.get("max_length") or 2200), 300), 10000)
    extracted = fetch_single_url_text(target_url, max_length=max_length, timeout=6)

    duration_ms = int((time.time() - start_ts) * 1000)
    exec_obj = {
        "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "tool_name": tool_name,
        "preset_used": None,
        "raw_llm_arguments": args,
        "effective_searxng_request": {"url": target_url, "effective_query": f"DIRECT_FETCH: {target_url}"},
        "searxng_raw_stats": {"total_candidates_returned": 1},
        "selection_and_randomization": {
            "response_mode": "detailed",
            "shuffle_enabled": False,
            "selected_results": [{"rank": 1, "original_searx_rank": 1, "title": target_url, "url": target_url}],
        },
        "fetched_articles": [{"url": target_url, "char_length": len(extracted), "extracted_text": extracted}],
        "final_llm_response_chars": len(extracted),
        "final_llm_response_text": extracted,
    }
    record_search_execution(exec_obj)
    return {
        "content": [{"type": "text", "text": extracted}],
        "isError": False,
    }


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


def query_searxng_json(q: str, categories: str = "", engines: str = "", language: str = "", time_range: str = "") -> tuple[dict, str]:
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
            "User-Agent": "HomeAssistant-SearXNG-MCP/1.5",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace")), url


def execute_image_search(args: dict, tool_name: str = "search_images") -> dict:
    """
    Search SearXNG for images (categories=images) and return the exact structure
    Voice Satellite Card (`src/pipeline/events.js`) expects to render the image grid or lightbox:
      - Top-level `results`: [{image_url, title, thumbnail_url, source_url, source, width, height}]
      - Top-level `auto_display`: bool
    """
    start_ts = time.time()
    search_id = f"img_{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

    query = str(args.get("query") or "").strip()
    if not query:
        err_text = "Error: 'query' parameter is required for search_images."
        return {"content": [{"type": "text", "text": err_text}], "isError": True}

    num_results = min(max(int(args.get("num_results") or 4), 1), 10)
    auto_display = bool(args.get("auto_display", False))
    shuffle_enabled = bool(args.get("shuffle_results", False))

    searx_url = ""
    try:
        data, searx_url = query_searxng_json(query, categories="images")
        raw_results = data.get("results") or []
    except Exception as exc:
        err_msg = f"Image search failed querying SearXNG: {exc}"
        record_search_execution({
            "search_id": search_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.time() - start_ts) * 1000),
            "tool_name": tool_name,
            "preset_used": "images",
            "raw_llm_arguments": args,
            "effective_searxng_request": {"url": searx_url, "effective_query": query, "categories": "images"},
            "error": err_msg,
            "final_llm_response_chars": len(err_msg),
            "final_llm_response_text": err_msg,
        })
        return {"content": [{"type": "text", "text": err_msg}], "isError": True}

    # Filter only items with a valid http(s) img_src
    valid_image_items = [
        item for item in raw_results
        if (item.get("img_src") or "").strip().startswith(("http://", "https://"))
    ]

    selected_items, rand_stats = select_and_randomize_candidates(
        valid_image_items,
        max_results=num_results,
        shuffle_enabled=shuffle_enabled,
        filter_landing_pages=False,
    )

    vs_results = []
    debug_selected = []
    for idx, item in enumerate(selected_items, start=1):
        img_src = (item.get("img_src") or "").strip()
        thumb_src = (item.get("thumbnail_src") or item.get("thumbnail") or img_src).strip()
        title = (item.get("title") or "").strip()
        source_url = (item.get("url") or "").strip()
        engine = (item.get("engine") or "").strip()

        vs_results.append({
            "image_url": img_src,
            "title": title,
            "thumbnail_url": thumb_src if thumb_src.startswith(("http://", "https://")) else img_src,
            "source_url": source_url,
            "source": engine,
            "width": None,
            "height": None,
        })
        debug_selected.append({
            "rank": idx,
            "original_searx_rank": item.get("_original_rank", idx),
            "title": title,
            "url": source_url,
            "image_url": img_src,
            "thumbnail_url": thumb_src,
            "engine": engine,
            "short_snippet": f"[Image] {img_src}",
        })

    instruction_text = (
        f"Found {len(vs_results)} images for '{query}'. "
        "Do NOT list individual image titles, URLs, sources, or use markdown image syntax. "
        "The images are displayed visually on the Voice Satellite screen — the user can already see them. "
        "Respond with a single brief sentence summarizing what you found, e.g. "
        f"'Here are some pictures of {query}.'"
    )

    duration_ms = int((time.time() - start_ts) * 1000)
    exec_obj = {
        "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "tool_name": tool_name,
        "preset_used": "images",
        "raw_llm_arguments": args,
        "effective_searxng_request": {
            "url": searx_url,
            "effective_query": query,
            "categories": "images",
        },
        "searxng_raw_stats": {
            "total_candidates_returned": len(valid_image_items),
            "unresponsive_engines": data.get("unresponsive_engines") or [],
        },
        "selection_and_randomization": {
            "response_mode": "images",
            "shuffle_enabled": shuffle_enabled,
            "auto_display": auto_display,
            "pool_stats": rand_stats,
            "selected_results": debug_selected,
        },
        "voice_satellite_media_payload": {
            "auto_display": auto_display,
            "results_count": len(vs_results),
            "results": vs_results,
        },
        "final_llm_response_chars": len(instruction_text),
        "final_llm_response_text": instruction_text,
    }
    record_search_execution(exec_obj)

    # Return BOTH standard MCP `content` AND top-level `results` & `auto_display`
    # so Home Assistant's MCP client passes `results` directly into `tool_result` for Voice Satellite Card!
    return {
        "content": [{"type": "text", "text": instruction_text}],
        "isError": False,
        "source": "searxng",
        "query": query,
        "num_results": len(vs_results),
        "auto_display": auto_display,
        "results": vs_results,
        "instruction": instruction_text,
    }


def execute_search_with_preset(args: dict, tool_name: str = "web_search", forced_preset_alias: str | None = None) -> dict:
    start_ts = time.time()
    search_id = f"srch_{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

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
        err_msg = "Error: Please provide a 'query' or use a preset that defines 'default_query' or 'sites'."
        return {"content": [{"type": "text", "text": err_msg}], "isError": True}

    categories = str(args.get("categories") if args.get("categories") is not None else (preset["categories"] if preset else "")).strip()
    engines = str(args.get("engines") if args.get("engines") is not None else (preset["engines"] if preset else "")).strip()
    language = str(args.get("language") if args.get("language") is not None else (preset["language"] if preset else "")).strip()
    time_range = str(args.get("time_range") if args.get("time_range") is not None else (preset["time_range"] if preset else "")).strip()
    if time_range.lower() in ("any", "none", "all"):
        time_range = ""

    # If the user or preset explicitly asked for categories="images", delegate to execute_image_search!
    if categories.lower() == "images":
        img_args = dict(args)
        img_args["query"] = effective_query
        return execute_image_search(img_args, tool_name=tool_name)

    response_mode = str(args.get("response_mode") or (preset["response_mode"] if preset else "digest")).strip().lower()
    if response_mode not in ("digest", "detailed"):
        response_mode = "digest"

    if "shuffle_results" in args and args["shuffle_results"] is not None:
        shuffle_enabled = bool(args["shuffle_results"])
    elif preset is not None:
        shuffle_enabled = bool(preset["shuffle_results"])
    else:
        shuffle_enabled = False

    default_max = preset["max_results"] if preset else MCP_MAX_RESULTS
    max_results = min(max(int(args.get("max_results") if args.get("max_results") is not None else default_max), 1), 15)
    digest_words = int(preset["digest_words"] if preset else 25)

    if response_mode == "digest":
        fetch_top_n = 0
    else:
        default_fetch_n = max(int(preset["fetch_top_n"] if preset else 1), 1)
        fetch_top_n = min(max(int(args.get("fetch_top_n") if args.get("fetch_top_n") is not None else default_fetch_n), 1), 3)

    fallback_steps = []
    searx_url = ""
    try:
        data, searx_url = query_searxng_json(
            effective_query,
            categories=categories,
            engines=engines,
            language=language,
            time_range=time_range,
        )
        raw_results = data.get("results") or []

        if not raw_results and time_range:
            fallback_steps.append(f"retried_without_time_range({time_range}->any)")
            data, searx_url = query_searxng_json(
                effective_query,
                categories=categories,
                engines=engines,
                language=language,
                time_range="",
            )
            raw_results = data.get("results") or []

        if not raw_results and sites and not user_query and default_query:
            site_only_query = build_effective_query("", "", "", sites)
            fallback_steps.append(f"retried_site_only({site_only_query})")
            data, searx_url = query_searxng_json(
                site_only_query,
                categories="general",
                engines=engines,
                language=language,
                time_range="",
            )
            raw_results = data.get("results") or []
    except Exception as exc:
        err_msg = f"Error querying SearXNG at {SEARXNG_BASE_URL}: {exc}"
        record_search_execution({
            "search_id": search_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.time() - start_ts) * 1000),
            "tool_name": tool_name,
            "preset_used": preset["norm_alias"] if preset else None,
            "raw_llm_arguments": args,
            "effective_searxng_request": {
                "url": searx_url,
                "effective_query": effective_query,
                "categories": categories,
                "language": language,
                "time_range": time_range,
                "fallbacks": fallback_steps,
            },
            "error": err_msg,
            "final_llm_response_chars": len(err_msg),
            "final_llm_response_text": err_msg,
        })
        return {"content": [{"type": "text", "text": err_msg}], "isError": True}

    selected_results, rand_stats = select_and_randomize_candidates(
        raw_results,
        max_results=max_results,
        shuffle_enabled=shuffle_enabled,
    )

    output_lines = []
    selected_debug_records = []
    fetched_articles_debug = []
    featured_image = None

    for item in selected_results:
        thumb = extract_thumbnail_from_item(item)
        if thumb:
            featured_image = thumb
            break

    if not selected_results:
        final_text = f"No results found for query: {effective_query}"
    elif response_mode == "digest":
        output_lines.append(
            f"[INSTRUCTION: Below are {len(selected_results)} randomized stories. "
            "Briefly mention a 1-sentence summary for EACH of them (do NOT read only the first one!), "
            "then let the user ask about a specific story. If the user asks for details on one, call `fetch_url` or `search_detailed_search`.]"
        )
        for idx, item in enumerate(selected_results, start=1):
            title = (item.get("title") or "Untitled").strip()
            link = (item.get("url") or "").strip()
            raw_snippet = (item.get("content") or item.get("snippet") or "").strip()
            short_words = truncate_to_words(raw_snippet, digest_words) if raw_snippet else "(No snippet)"
            orig_rank = item.get("_original_rank", idx)
            thumb = extract_thumbnail_from_item(item)

            output_lines.append(f"{idx}. {title} — {short_words} [URL: {link}]")
            selected_debug_records.append({
                "rank": idx,
                "original_searx_rank": orig_rank,
                "title": title,
                "url": link,
                "thumbnail_url": thumb,
                "engine": item.get("engine") or "",
                "short_snippet": short_words,
                "full_snippet": raw_snippet,
            })
        final_text = "\n".join(output_lines).strip()
    else:
        for idx, item in enumerate(selected_results, start=1):
            title = (item.get("title") or "Untitled").strip()
            link = (item.get("url") or "").strip()
            raw_snippet = (item.get("content") or item.get("snippet") or "").strip()
            orig_rank = item.get("_original_rank", idx)
            thumb = extract_thumbnail_from_item(item)

            output_lines.append(f"{idx}. **{title}** ({link})")
            if raw_snippet:
                output_lines.append(f"   Snippet: {truncate_to_words(raw_snippet, 45)}")
            selected_debug_records.append({
                "rank": idx,
                "original_searx_rank": orig_rank,
                "title": title,
                "url": link,
                "thumbnail_url": thumb,
                "engine": item.get("engine") or "",
                "short_snippet": truncate_to_words(raw_snippet, 45),
                "full_snippet": raw_snippet,
            })

        urls_to_fetch = []
        for item in selected_results[:fetch_top_n]:
            u = (item.get("url") or "").strip()
            if u:
                urls_to_fetch.append((item.get("title") or u, u))

        if urls_to_fetch:
            output_lines.append("\n### Full Context of Top Result:")
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls_to_fetch), 3)) as pool:
                ordered_futures = [
                    pool.submit(fetch_single_url_text, url, 2000, 5)
                    for _, url in urls_to_fetch
                ]
                for idx, ((title, url), fut) in enumerate(zip(urls_to_fetch, ordered_futures), start=1):
                    try:
                        article_text = fut.result(timeout=6)
                    except Exception as exc:
                        article_text = f"[Timed out reading {url}: {exc}]"
                    output_lines.append(f"#### {title} ({url})")
                    output_lines.append(article_text)
                    fetched_articles_debug.append({
                        "url": url,
                        "title": title,
                        "char_length": len(article_text),
                        "extracted_text": article_text,
                    })

        final_text = "\n".join(output_lines).strip()

    duration_ms = int((time.time() - start_ts) * 1000)

    exec_obj = {
        "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "tool_name": tool_name,
        "preset_used": preset["norm_alias"] if preset else None,
        "raw_llm_arguments": args,
        "effective_searxng_request": {
            "url": searx_url,
            "effective_query": effective_query,
            "categories": categories,
            "engines": engines,
            "language": language,
            "time_range": time_range,
            "fallbacks": fallback_steps,
        },
        "searxng_raw_stats": {
            "total_candidates_returned": len(raw_results),
            "unresponsive_engines": data.get("unresponsive_engines") or [],
            "all_candidates": [
                {
                    "searx_rank": i + 1,
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "engine": r.get("engine"),
                }
                for i, r in enumerate(raw_results[:20])
            ],
        },
        "selection_and_randomization": {
            "response_mode": response_mode,
            "shuffle_enabled": shuffle_enabled,
            "digest_words": digest_words,
            "fetch_top_n": fetch_top_n,
            "featured_image": featured_image,
            "pool_stats": rand_stats,
            "selected_results": selected_debug_records,
        },
        "fetched_articles": fetched_articles_debug,
        "final_llm_response_chars": len(final_text),
        "final_llm_response_text": final_text,
    }
    record_search_execution(exec_obj)

    mcp_result = {
        "content": [{"type": "text", "text": final_text}],
        "isError": False,
    }
    if featured_image:
        mcp_result["featured_image"] = featured_image
    return mcp_result


INSPECTOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SearXNG MCP Search Inspector</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #0f172a;
      --panel: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text);
    }
    header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 14px 20px; background: var(--panel); border-bottom: 1px solid var(--border);
    }
    h1 { margin: 0; font-size: 18px; display: flex; align-items: center; gap: 10px; }
    .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    button {
      background: #2563eb; color: #fff; border: none; padding: 7px 12px;
      border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500;
    }
    button.secondary { background: #334155; }
    button.danger { background: #dc2626; }
    .layout {
      display: grid; grid-template-columns: 420px 1fr; height: calc(100vh - 57px);
    }
    .sidebar {
      border-right: 1px solid var(--border); overflow-y: auto; background: #131c31;
    }
    .search-item {
      padding: 12px 16px; border-bottom: 1px solid var(--border); cursor: pointer;
    }
    .search-item:hover, .search-item.active { background: var(--panel); }
    .badge {
      display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px;
      font-weight: 600; background: #0284c7; color: #fff; margin-right: 6px;
    }
    .badge.digest { background: #0d9488; }
    .badge.detailed { background: #7c3aed; }
    .badge.images { background: #ec4899; }
    .badge.shuffle { background: #d97706; }
    .meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .query-preview { font-size: 13px; margin-top: 6px; word-break: break-word; color: #e2e8f0; }
    .detail { padding: 20px; overflow-y: auto; }
    .card {
      background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
      padding: 14px 16px; margin-bottom: 16px;
    }
    .card h3 { margin: 0 0 10px 0; font-size: 14px; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px; }
    pre {
      background: #0b1120; padding: 12px; border-radius: 6px; overflow-x: auto;
      font-size: 12.5px; line-height: 1.45; color: #e2e8f0; margin: 0; white-space: pre-wrap;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .img-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
    .img-card { background: #0b1120; border: 1px solid var(--border); border-radius: 6px; padding: 8px; width: 180px; }
    .img-card img { width: 100%; height: 120px; object-fit: cover; border-radius: 4px; }
    .img-card div { font-size: 11px; color: var(--muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  </style>
</head>
<body>
  <header>
    <h1>SearXNG MCP Search Execution Inspector <span style="font-size:12px;color:var(--muted)">v1.5.0</span></h1>
    <div class="controls">
      <button onclick="runTest('local_news')">Test local_news</button>
      <button onclick="runTest('it_news')">Test it_news</button>
      <button onclick="runImageTest()">Test search_images</button>
      <button class="secondary" onclick="loadLogs()">Refresh</button>
      <button class="danger" onclick="clearLogs()">Clear History</button>
    </div>
  </header>
  <div class="layout">
    <div class="sidebar" id="list"></div>
    <div class="detail" id="detail">
      <div style="color:var(--muted);padding:40px;text-align:center;">Select a search execution on the left to inspect its full SearchExecutionObject.</div>
    </div>
  </div>
  <script>
    let logs = [];
    let selectedId = null;

    async function loadLogs() {
      const res = await fetch('/logs/json');
      const data = await res.json();
      logs = (data.history || []).slice().reverse();
      renderList();
      if (!selectedId && logs.length > 0) {
        selectItem(logs[0].search_id);
      } else if (selectedId) {
        selectItem(selectedId);
      }
    }

    function renderList() {
      const el = document.getElementById('list');
      if (!logs.length) {
        el.innerHTML = '<div style="padding:20px;color:var(--muted)">No searches recorded yet. Trigger a search from your LLM or click a Test button above.</div>';
        return;
      }
      el.innerHTML = logs.map(item => {
        const sel = item.selection_and_randomization || {};
        const req = item.effective_searxng_request || {};
        const active = item.search_id === selectedId ? 'active' : '';
        return `<div class="search-item ${active}" onclick="selectItem('${item.search_id}')">
          <div>
            <span class="badge">${item.tool_name}</span>
            <span class="badge ${sel.response_mode || ''}">${sel.response_mode || 'n/a'}</span>
            ${sel.shuffle_enabled ? '<span class="badge shuffle">shuffled</span>' : ''}
          </div>
          <div class="query-preview"><strong>Query:</strong> ${escapeHtml(req.effective_query || '')}</div>
          <div class="meta">${item.timestamp} • ${item.duration_ms}ms • ${item.final_llm_response_chars || 0} chars</div>
        </div>`;
      }).join('');
    }

    function escapeHtml(s) {
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function selectItem(id) {
      selectedId = id;
      renderList();
      const item = logs.find(x => x.search_id === id);
      if (!item) return;
      const req = item.effective_searxng_request || {};
      const sel = item.selection_and_randomization || {};
      const stats = item.searxng_raw_stats || {};
      const vsMedia = item.voice_satellite_media_payload;

      let mediaPreviewHtml = '';
      if (vsMedia && Array.isArray(vsMedia.results) && vsMedia.results.length > 0) {
        const cards = vsMedia.results.map(r => `
          <div class="img-card">
            <a href="${escapeHtml(r.image_url)}" target="_blank"><img src="${escapeHtml(r.thumbnail_url || r.image_url)}" alt=""></a>
            <div title="${escapeHtml(r.title)}">${escapeHtml(r.title || r.source)}</div>
          </div>
        `).join('');
        mediaPreviewHtml = `
          <div class="card">
            <h3>Voice Satellite Media Panel Payload (${vsMedia.results.length} images, auto_display=${vsMedia.auto_display})</h3>
            <div class="img-grid">${cards}</div>
          </div>
        `;
      } else if (sel.featured_image) {
        mediaPreviewHtml = `
          <div class="card">
            <h3>Voice Satellite Featured Image</h3>
            <div class="img-grid">
              <div class="img-card">
                <a href="${escapeHtml(sel.featured_image)}" target="_blank"><img src="${escapeHtml(sel.featured_image)}" alt=""></a>
              </div>
            </div>
          </div>
        `;
      }

      const selectedRows = (sel.selected_results || []).map(r => `
        <tr>
          <td>#${r.rank} <span style="color:var(--muted)">(was #${r.original_searx_rank})</span></td>
          <td><strong>${escapeHtml(r.title)}</strong><br><a href="${escapeHtml(r.image_url || r.url)}" target="_blank">${escapeHtml(r.image_url || r.url)}</a></td>
          <td>${escapeHtml(r.short_snippet || '')}</td>
          <td>${escapeHtml(r.engine || '')}</td>
        </tr>
      `).join('');

      document.getElementById('detail').innerHTML = `
        ${mediaPreviewHtml}
        <div class="card">
          <h3>1. Final Output Sent to LLM (${item.final_llm_response_chars || 0} chars — Voice Safe)</h3>
          <pre>${escapeHtml(item.final_llm_response_text || '')}</pre>
        </div>
        <div class="card">
          <h3>2. LLM Call & Effective SearXNG Query</h3>
          <pre>${escapeHtml(JSON.stringify({
            search_id: item.search_id,
            tool_name: item.tool_name,
            preset_used: item.preset_used,
            raw_llm_arguments: item.raw_llm_arguments,
            effective_searxng_request: req,
            duration_ms: item.duration_ms
          }, null, 2))}</pre>
        </div>
        <div class="card">
          <h3>3. Selected & Randomized Results (from ${stats.total_candidates_returned || 0} raw candidates)</h3>
          <table>
            <thead><tr><th>Rank</th><th>Title & URL</th><th>Snippet / Media</th><th>Engine</th></tr></thead>
            <tbody>${selectedRows || '<tr><td colspan="4">None</td></tr>'}</tbody>
          </table>
        </div>
        <div class="card">
          <h3>4. Complete SearchExecutionObject (Raw JSON)</h3>
          <pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>
        </div>
      `;
    }

    async function clearLogs() {
      await fetch('/logs/clear', {method: 'POST'});
      selectedId = null;
      await loadLogs();
      document.getElementById('detail').innerHTML = '<div style="color:var(--muted);padding:40px;text-align:center;">History cleared.</div>';
    }

    async function runTest(preset) {
      await fetch('/mcp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          jsonrpc: '2.0', id: Date.now(), method: 'tools/call',
          params: { name: 'search_' + preset, arguments: {} }
        })
      });
      selectedId = null;
      await loadLogs();
    }

    async function runImageTest() {
      await fetch('/mcp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          jsonrpc: '2.0', id: Date.now(), method: 'tools/call',
          params: { name: 'search_images', arguments: { query: 'golden retriever puppy', num_results: 4 } }
        })
      });
      selectedId = null;
      await loadLogs();
    }

    loadLogs();
    setInterval(loadLogs, 5000);
  </script>
</body>
</html>
"""


def handle_jsonrpc_message(msg: dict) -> dict | None:
    """Process a single JSON-RPC 2.0 request or notification."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

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

        if tool_name in ("search_images", "searxng_image_search", "image_search"):
            res_dict = execute_image_search(tool_args, tool_name=tool_name)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res_dict,
            }

        if tool_name in ("web_search", "searxng_web_search", "search", "search_web"):
            res_dict = execute_search_with_preset(tool_args, tool_name=tool_name, forced_preset_alias=None)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res_dict,
            }

        if tool_name in ("fetch_url", "web_url_read"):
            res_dict = perform_fetch_url(tool_args, tool_name=tool_name)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res_dict,
            }

        norm_tool = normalize_alias(tool_name)
        matched_alias = None
        if norm_tool in presets:
            matched_alias = norm_tool
        elif norm_tool.startswith("search_") and norm_tool[len("search_"):] in presets:
            matched_alias = norm_tool[len("search_"):]

        if matched_alias:
            res_dict = execute_search_with_preset(tool_args, tool_name=tool_name, forced_preset_alias=matched_alias)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res_dict,
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
                "inspector_url": f"http://<host>:{MCP_PORT}/logs",
                "tools": [t["name"] for t in tools],
                "presets": list(load_presets().keys()),
                "recorded_searches": len(SEARCH_HISTORY),
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/logs":
            body = INSPECTOR_HTML.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/logs/json":
            with HISTORY_LOCK:
                hist_list = list(SEARCH_HISTORY)
            body = json.dumps({"count": len(hist_list), "history": hist_list}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
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

        if parsed.path == "/logs/clear":
            with HISTORY_LOCK:
                SEARCH_HISTORY.clear()
                RECENTLY_SHOWN_URLS.clear()
                try:
                    if os.path.exists(HISTORY_PATH):
                        os.remove(HISTORY_PATH)
                except Exception:
                    pass
            body = json.dumps({"status": "cleared"}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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
    load_history_from_disk()
    presets = load_presets()
    server = ThreadingHTTPServer(("0.0.0.0", MCP_PORT), MCPHTTPHandler)
    print(
        f"INFO: SearXNG MCP Server v1.5.0 listening on 0.0.0.0:{MCP_PORT} "
        f"(MCP: /mcp, /sse | Live Inspector: /logs) with search_images & {len(presets)} presets ({', '.join(presets.keys())})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
