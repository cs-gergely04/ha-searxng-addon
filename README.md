# SearXNG Home Assistant Add-on (with Persistent Settings & Built-in MCP Server)

Run a **free, privacy-friendly search engine** on your Home Assistant OS device with **persistent server-side configuration** and a **built-in Model Context Protocol (MCP) server** that works out of the box with your LLM.

## Key Features

1. **Built-in MCP Server (`:8889/mcp` and `:8889/sse`) — Works Out of the Box!**
   - Starts automatically on port `8889` alongside SearXNG.
   - Supports both **Streamable HTTP** (`http://homeassistant.local:8889/mcp`) and **SSE** (`http://homeassistant.local:8889/sse`).
   - Exposes two tools to your LLM:
     - `web_search`: Search the web via your local SearXNG instance (automatically using your server-side language, SafeSearch, and engine settings).
     - `fetch_url`: Read and extract clean plain text from any web page URL found in search results.
2. **Global Server-Side Configuration (No Cookies Needed)**
   - Configure default language, SafeSearch, autocomplete, theme style, and enabled/disabled search engines directly in the **Home Assistant Add-on Configuration UI**.
   - Persists `/addon_configs/<repo>_searxng/settings.yml` (`/config/settings.yml`) across restarts so manual edits are never wiped out.

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

## Connect Your LLM via MCP (Zero Extra Containers Needed)

As soon as the add-on starts, the built-in MCP server is live on port `8889`:

- **Streamable HTTP URL:** `http://homeassistant.local:8889/mcp` (or `http://HOME_ASSISTANT_IP:8889/mcp`)
- **SSE URL:** `http://homeassistant.local:8889/sse` (or `http://HOME_ASSISTANT_IP:8889/sse`)

### Home Assistant Native MCP Client Integration
1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Select **Model Context Protocol**.
3. Enter `http://homeassistant.local:8889/mcp` (or `http://127.0.0.1:8889/mcp` / `http://homeassistant.local:8889/sse`).
4. Enable tool access in your Home Assistant Conversation Agent (Assist / OpenAI / Ollama / Google Generative AI / Local LLM).

### OpenWebUI / AnythingLLM / n8n / Claude / Cursor
Point any MCP client supporting Streamable HTTP or SSE directly to:
```text
http://HOME_ASSISTANT_IP:8889/mcp
```
or
```text
http://HOME_ASSISTANT_IP:8889/sse
```

---

## How to Configure SearXNG Globally

### 1. Via the Home Assistant Add-on Configuration Tab
- `mcp_enabled`: Enable/disable the built-in MCP server on port `8889` (default: `true`).
- `mcp_max_results`: Default number of search results returned to the LLM (default: `8`).
- `default_lang`: Default search result language for all browsers and LLM tool calls (e.g. `auto`, `all`, `en`, `hu`, `de`).
- `default_locale`: Default web UI language (e.g. `en`, `hu`, `de`).
- `autocomplete`: `duckduckgo`, `google`, `brave`, `startpage`, `wikipedia`, `none`, etc.
- `safe_search`: `0` (None), `1` (Moderate), `2` (Strict).
- `theme_style`: `auto`, `light`, `dark`, `black`.
- `enabled_engines` / `disabled_engines`: Lists of engine names (e.g., `google`, `duckduckgo`, `brave`, `bing`) to enable or disable server-wide.

### 2. Via Persistent `settings.yml`
You can also edit `/addon_configs/<repo>_searxng/settings.yml` directly via File Editor, Studio Code Server, or Samba. Your custom edits are preserved across restarts.
