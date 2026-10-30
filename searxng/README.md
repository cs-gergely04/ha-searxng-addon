# SearXNG Add-on (with Persistent Settings & Built-in MCP Server)

Run SearXNG as a Home Assistant add-on with persistent server-side configuration and a built-in Model Context Protocol (MCP) server for LLMs.

## Built-in MCP Server (Works Out of the Box)

The add-on automatically starts a lightweight MCP server on port **8889** alongside SearXNG on port **8888**.

- **Streamable HTTP endpoint:** `http://homeassistant.local:8889/mcp`
- **SSE endpoint:** `http://homeassistant.local:8889/sse`
- **Health check:** `http://homeassistant.local:8889/health`

### Provided MCP Tools
1. **`web_search`**: Queries your local SearXNG instance (`query`, optional `max_results`, `categories`, `language`, `time_range`) and returns clean, LLM-friendly formatted results (titles, URLs, snippets, direct answers).
2. **`fetch_url`**: Fetches and extracts readable plain text from any webpage URL (`url`, optional `max_length`) so your LLM can read full pages found in search results.

### Connecting to Home Assistant LLM / Assist
1. Go to **Settings -> Devices & Services -> Add Integration -> Model Context Protocol**.
2. Enter `http://homeassistant.local:8889/mcp` (or `http://homeassistant.local:8889/sse`).
3. Your LLM agent now has live web search and page reading capabilities!

---

## Server-Wide Configuration (No Cookies Required)

Configure options directly in the add-on's **Configuration** tab:

```yaml
secret_key: ""
mcp_enabled: true
mcp_max_results: 8
instance_name: "SearXNG"
default_lang: "auto"
default_locale: ""
autocomplete: "duckduckgo"
safe_search: 0
theme_style: "auto"
infinite_scroll: false
results_on_new_tab: false
search_on_category_select: true
query_in_title: false
image_proxy: true
enabled_engines: []
disabled_engines: []
reset_settings_yml: false
```

All settings configured here (or manually in `/addon_configs/<repo>_searxng/settings.yml`) apply globally to both **web browsers** and **MCP LLM tool calls**.
