# SearXNG Add-on (Persistent Settings, Built-in MCP Server, Smart Presets & Search Inspector)

Run SearXNG as a Home Assistant add-on with persistent server-side configuration, a built-in Model Context Protocol (MCP) server, customizable **Digest vs. Detailed Search Presets**, and a **Live Search Execution Inspector**.

## Endpoints

- **SearXNG Web UI:** `http://homeassistant.local:8888`
- **MCP Server (Streamable HTTP):** `http://homeassistant.local:8889/mcp`
- **MCP Server (SSE):** `http://homeassistant.local:8889/sse`
- **Live Search Inspector UI:** `http://homeassistant.local:8889/logs`
- **Health & Active Tools JSON:** `http://homeassistant.local:8889/health`

## Preset Configuration (`digest` vs. `detailed`)

Each preset in `search_presets` (or `/addon_configs/<repo>_searxng/presets.yml`) supports:
- `response_mode`:
  - `"digest"`: Returns **only the headline, the first `digest_words` (e.g. 25) words, and the URL** for 3–5 stories (< 800 chars total, < 1s). Prevents Voice Satellite `1001` errors and instructs the AI to give a 1-sentence summary of each item so you can ask about a specific one.
  - `"detailed"`: Extracts the full readable article text of the top `fetch_top_n` result(s) (used by `detailed_search` and `tech_docs`).
- `shuffle_results`: When `true`, randomly selects items from the top 20 candidate pool and rotates away from recently shown URLs so repeated news requests never repeat the same top headline all day.
