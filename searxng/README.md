# SearXNG Add-on (Persistent Settings, Built-in MCP Server & Smart Presets)

Run SearXNG as a Home Assistant add-on with persistent server-side configuration, a built-in Model Context Protocol (MCP) server, and customizable **Search Presets (Aliases)**.

## Built-in MCP Server & Smart Search Presets

The add-on automatically starts a lightweight MCP server on port **8889** alongside SearXNG on port **8888**:
- **Streamable HTTP endpoint:** `http://homeassistant.local:8889/mcp`
- **SSE endpoint:** `http://homeassistant.local:8889/sse`
- **Health & active tools check:** `http://homeassistant.local:8889/health`

### How Search Presets Work
Every preset defined in the Home Assistant UI (`search_presets`) or in `/addon_configs/<repo>_searxng/presets.yml` is automatically turned into its own **dedicated MCP tool** (`search_<alias>`):
- **`search_local_news`** (`alias: local_news`): Searches `pannonrtv.com` and `pannonrtv.rs` in Hungarian (`hu`) and automatically fetches the full text of the top 3 articles (`fetch_top_n: 3`) in parallel. If the LLM calls `search_local_news()` with no query, it automatically runs `default_query` (`hírek vajdaság szabadka`).
- **`search_it_news`** (`alias: it_news`): Searches latest IT, software, AI, and cybersecurity news (`categories: it,news`) and automatically extracts the top 2 full articles.
- **`search_tech_docs`** (`alias: tech_docs`): Searches `github.com`, `stackoverflow.com`, `docs.home-assistant.io`, and `community.home-assistant.io` and extracts the top 2 pages.

### Customizing Presets
You can configure presets directly in the Home Assistant **Configuration** tab or edit `/addon_configs/<repo>_searxng/presets.yml` (changes to `presets.yml` are loaded live without restarting):

```yaml
search_presets:
  - alias: "local_news"
    description: "Search local Vojvodina and Subotica news in Hungarian (Pannon RTV)."
    sites: "pannonrtv.com, pannonrtv.rs"
    default_query: "hírek vajdaság szabadka"
    categories: "general,news"
    language: "hu"
    time_range: "week"
    max_results: 6
    fetch_top_n: 3
```

---

## Server-Wide Configuration (No Cookies Required)

All settings configured in the Home Assistant **Configuration** tab (or in `/addon_configs/<repo>_searxng/settings.yml`) apply globally to both **web browsers** and **MCP LLM tool calls**.
