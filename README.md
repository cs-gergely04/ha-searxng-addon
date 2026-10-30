# SearXNG Home Assistant Add-on

Run a **free, privacy-friendly search engine** on your Home Assistant OS device with **persistent server-side configuration**.

This add-on installs [SearXNG](https://github.com/searxng/searxng), a metasearch engine that respects your privacy and does not track you. Unlike browser-only cookie settings, this add-on allows you to configure SearXNG globally for all devices on your LAN — both directly from the **Home Assistant Add-on Configuration UI** and via a persistent `settings.yml` file in `/addon_configs/`.

## Installation

1. Open Home Assistant.
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Click the three-dot menu (top right) -> **Repositories**.
4. Add this repository:

   ```text
   https://github.com/cs-gergely04/ha-searxng-addon
   ```

5. Install the **SearXNG** add-on.
6. Configure your desired defaults in the **Configuration** tab and start the add-on.

## How to Configure SearXNG Globally (No Cookies Needed)

You have two ways to configure SearXNG so settings apply to **every browser and API client** by default:

### 1. Via the Home Assistant Add-on UI (Configuration Tab)
You can directly configure the most common server-wide defaults in Home Assistant:
- **Instance Name** (`instance_name`): Custom title for your SearXNG instance.
- **Default Search Language** (`default_lang`): e.g. `auto`, `all`, `en`, `hu`, `de`.
- **Default UI Language** (`default_locale`): e.g. `en`, `hu`, `de` (leave empty for browser auto-detect).
- **Default Autocomplete** (`autocomplete`): `duckduckgo`, `google`, `brave`, `startpage`, `wikipedia`, `none`, etc.
- **Default SafeSearch** (`safe_search`): `0` (None), `1` (Moderate), `2` (Strict).
- **Default Theme Style** (`theme_style`): `auto`, `light`, `dark`, or `black`.
- **Infinite Scroll** (`infinite_scroll`), **Open Results in New Tab** (`results_on_new_tab`), **Search on Category Select** (`search_on_category_select`), **Show Query in Page Title** (`query_in_title`), **Image Proxy** (`image_proxy`).
- **Enabled / Disabled Engines** (`enabled_engines` / `disabled_engines`): Lists of SearXNG engine names (e.g. `google`, `duckduckgo`, `brave`, `bing`) to enable or disable server-wide for all users.

### 2. Via Persistent `settings.yml` in `/addon_configs/`
When the add-on starts for the first time, it creates a persistent `settings.yml` inside the add-on's configuration folder:
- **Host path (File Editor / Studio Code Server / Samba):** `/addon_configs/<repo_id>_searxng/settings.yml`
- **Container path:** `/config/settings.yml`

Any custom sections, engines, or plugins you add to this `settings.yml` file are **preserved across add-on restarts and updates**. If you ever want to reset `settings.yml` back to clean defaults, toggle `reset_settings_yml: true` in the add-on Configuration tab and restart.

## Usage

After installation, open:

```text
http://homeassistant.local:8888
```

or:

```text
http://HOME_ASSISTANT_IP:8888
```

### Test JSON output (for OpenClaw / LLM integrations)

```bash
curl "http://homeassistant.local:8888/search?q=test&format=json"
```

## OpenClaw integration

Configure OpenClaw to use SearXNG with:

```yaml
searxng:
  baseUrl: http://homeassistant.local:8888
```
