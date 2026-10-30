# SearXNG Home Assistant Add-on (Persistent Settings, Built-in MCP Server, Smart Presets & Live Search Inspector)

Run a **free, privacy-friendly search engine** on your Home Assistant OS device with **persistent server-side configuration**, a **built-in Model Context Protocol (MCP) server**, **Randomized Digest vs. Detailed Search Presets**, and a **Live Search Execution Inspector** at `http://homeassistant.local:8889/logs`.

## Key Features

1. **Built-in MCP Server (`:8889/mcp` and `:8889/sse`) — Voice Satellite & Gemini Live Ready!**
   - Starts automatically on port `8889` alongside SearXNG (`:8888`).
   - Supports both **Streamable HTTP** (`http://homeassistant.local:8889/mcp`) and **SSE** (`http://homeassistant.local:8889/sse`).
2. **Customizable `digest` vs. `detailed` Search Presets (No More Repeated Headlines or `1001` Errors!)**
   - **`response_mode: "digest"`** + **`shuffle_results: true`** (default for `local_news` and `it_news`):
     - Pulls up to 20 candidate results, filters out generic homepages, rotates away from recently shown URLs, and randomly picks **4 stories**.
     - Sends back **only the Headline, the first ~25 words (`digest_words: 25`), and the URL** (< 800 chars total, < 1 second) with a directive telling your AI to give a 1-sentence summary of each and let you ask about a specific one.
   - **`response_mode: "detailed"`** (default for `detailed_search` and `tech_docs`):
     - Finds the top matching result and extracts its full article text (`fetch_top_n: 1`) when you ask a specific question or want the full context of a story.
3. **Live Search Execution Inspector (`http://homeassistant.local:8889/logs`)**
   - Every search records a complete `SearchExecutionObject` (in `/addon_configs/<repo>_searxng/search_history.jsonl`, the Home Assistant Add-on Log tab, and the visual web inspector at `:8889/logs`).
   - Inspect exact LLM arguments, effective SearXNG queries, all candidate URLs vs. randomized selections, original vs. shuffled ranks, and the exact text returned to the LLM.

---

## Built-in Default Presets (Exposed Automatically as MCP Tools)

| MCP Tool Name | Alias | Mode | Shuffle | What It Does Out of the Box |
| :--- | :--- | :--- | :--- | :--- |
| `search_images` | *(images)* | `images` | configurable | Searches SearXNG Images (`categories=images`) and returns the exact `results` (`image_url`, `thumbnail_url`, `title`) & `auto_display` payload that **Voice Satellite Card** needs to pop up the thumbnail grid or fullscreen lightbox on screen! |
| `search_local_news` | `local_news` | `digest` | `true` | Picks **4 randomized** local Vojvodina/Subotica news stories from `pannonrtv.com` & `pannonrtv.rs` (`lang: hu`) with headline + first 25 words so your AI can summarize each in 1 sentence (plus `featured_image` thumbnail). |
| `search_it_news` | `it_news` | `digest` | `true` | Picks **4 randomized** IT, software, AI & cybersecurity news stories (`categories: it,news`) with headline + first 25 words. |
| `search_detailed_search` | `detailed_search` | `detailed` | `false` | Deep search for a specific story or question — extracts the full readable text of the top result (`fetch_top_n: 1`). |
| `search_tech_docs` | `tech_docs` | `detailed` | `false` | Searches `github.com`, `stackoverflow.com`, and `home-assistant.io` docs and extracts the full text of the top result. |
| `fetch_url` | *(reader)* | `detailed` | `false` | Reads the full article text of any specific URL when you ask the AI to tell you more about one of the digest stories. |
| `web_search` | *(ad-hoc)* | configurable | configurable | General SearXNG web search supporting both `digest` and `detailed` modes. |

---

## Inspecting Searches & Debugging
- **Visual Web Inspector:** Open `http://homeassistant.local:8889/logs` in your browser to inspect every search, test presets with 1 click, and view full `SearchExecutionObject` JSON records.
- **Persistent JSONL Log:** `/addon_configs/<repo>_searxng/search_history.jsonl`
- **Container Logs:** Check **Settings -> Add-ons -> SearXNG -> Log** in Home Assistant.
