# SearXNG Home Assistant Add-on (Persistent Settings, Built-in MCP Server & Smart Presets)

Run a **free, privacy-friendly search engine** on your Home Assistant OS device with **persistent server-side configuration**, a **built-in Model Context Protocol (MCP) server**, and **Preconfigured Search Tasks / Aliases (Presets)** that give your LLM one-shot deep search workflows.

## Key Features

1. **Built-in MCP Server (`:8889/mcp` and `:8889/sse`) — Works Out of the Box!**
   - Starts automatically on port `8889` alongside SearXNG (`:8888`).
   - Supports both **Streamable HTTP** (`http://homeassistant.local:8889/mcp`) and **SSE** (`http://homeassistant.local:8889/sse`).
2. **Preconfigured Search Tasks & Aliases (Presets)**
   - Define custom search aliases (like `local_news`, `it_news`, `tech_docs`) in the **Home Assistant Add-on UI** or in `/addon_configs/<repo>_searxng/presets.yml`.
   - **Dedicated MCP Tool per Alias:** Every preset is automatically exposed to your LLM as its own MCP tool (`search_local_news`, `search_it_news`, `search_tech_docs`, etc.) **and** as a `preset` option on `web_search`.
   - **Domain Restriction (`sites`):** Automatically restricts searches to specific domains (e.g. `pannonrtv.com, pannonrtv.rs`).
   - **Auto-Read Full Articles (`fetch_top_n`):** Automatically fetches and extracts the full readable article text of the top `N` results in parallel so your LLM gets the complete story in **a single tool call**.
   - **Live Reloading:** Editing `/addon_configs/<repo>_searxng/presets.yml` takes effect immediately on the next MCP tool call without restarting the container!
3. **Global Server-Side Configuration (No Cookies Needed)**
   - Configure default language, SafeSearch, autocomplete, theme style, and enabled/disabled search engines directly in the **Home Assistant Add-on Configuration UI** or `/addon_configs/<repo>_searxng/settings.yml`.

---

## Installation

1. Open Home Assistant -> **Settings -> Add-ons -> Add-on Store**.
2. Click the three-dot menu (top right) -> **Repositories**.
3. Add:
   ```text
   https://github.com/cs-gergely04/ha-searxng-addon
   ```
4. Install **SearXNG** and click **Start**.

---

## Connect Your LLM via MCP

As soon as the add-on starts, the built-in MCP server is live on port `8889`:

- **Streamable HTTP URL:** `http://homeassistant.local:8889/mcp` (or `http://HOME_ASSISTANT_IP:8889/mcp`)
- **SSE URL:** `http://homeassistant.local:8889/sse` (or `http://HOME_ASSISTANT_IP:8889/sse`)

### Built-in Default Presets (Exposed Automatically as MCP Tools)
| MCP Tool Name | Alias | What It Does Out of the Box |
| :--- | :--- | :--- |
| `search_local_news` | `local_news` | Searches local Vojvodina/Subotica news on `pannonrtv.com` & `pannonrtv.rs` (`lang: hu`, `time_range: week`) and **automatically extracts the full text of the top 3 articles**. Works even with an empty query! |
| `search_it_news` | `it_news` | Searches latest IT, software, AI & cybersecurity news (`categories: it,news`, `lang: en`, `time_range: week`) and **auto-reads the top 2 full articles**. |
| `search_tech_docs` | `tech_docs` | Searches `github.com`, `stackoverflow.com`, and `home-assistant.io` docs/community (`categories: it,general`) and **auto-reads the top 2 pages**. |
| `web_search` | *(ad-hoc)* | General SearXNG web search (also accepts `preset="<alias>"` and `fetch_top_n`). |
| `fetch_url` | *(reader)* | Extracts readable plain text from any URL. |

### Adding Your Own Presets (Home Assistant UI or `/config/presets.yml`)
You can add as many aliases as you like in the add-on **Configuration** tab under `search_presets`, or edit `/addon_configs/<repo>_searxng/presets.yml`:

```yaml
presets:
  - alias: "local_news"
    description: "Search local Vojvodina and Subotica news in Hungarian (Pannon RTV). Automatically extracts the full text of the top 3 news articles."
    sites:
      - "pannonrtv.com"
      - "pannonrtv.rs"
    default_query: "hírek vajdaság szabadka"
    categories: "general,news"
    language: "hu"
    time_range: "week"
    max_results: 6
    fetch_top_n: 3
```
