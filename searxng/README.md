# SearXNG Add-on

Run SearXNG as a Home Assistant add-on with persistent server-side configuration.

## Server-Wide Configuration (No Cookies Required)

By default, changes made inside the SearXNG web UI (**Preferences**) are stored only as browser cookies on your current device.

This add-on solves that by providing **two ways to configure SearXNG globally on the server** so your preferences apply to every device, browser, and API integration automatically:

### 1. Home Assistant Configuration UI

Configure options directly in the add-on's **Configuration** tab:

```yaml
secret_key: ""
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

- **`secret_key`**: Optional. If left empty, a random secret key is generated once and persisted in `/config/.secret_key` so sessions remain stable across restarts.
- **`default_lang`**: Default search language (`auto`, `all`, `en`, `hu`, `de`, etc.).
- **`default_locale`**: Default UI language (`""` for browser auto-detect, or `en`, `hu`, `de`, etc.).
- **`autocomplete`**: Autocomplete provider (`duckduckgo`, `google`, `brave`, `startpage`, `wikipedia`, `none`, etc.).
- **`safe_search`**: `0` = None, `1` = Moderate, `2` = Strict.
- **`theme_style`**: `auto`, `light`, `dark`, or `black`.
- **`enabled_engines` / `disabled_engines`**: Add engine names (such as `google`, `duckduckgo`, `brave`, `bing`, `wikipedia`) to enable or disable them server-wide for all users.
- **`reset_settings_yml`**: Set to `true` and restart if you want to overwrite `/config/settings.yml` with a fresh default configuration.

### 2. Direct Editing of Persistent `settings.yml`

The add-on stores its active configuration file at:
- **Home Assistant path:** `/addon_configs/<repo>_searxng/settings.yml`
- **Container path:** `/config/settings.yml` (linked to `/etc/searxng/settings.yml`)

You can edit this file using Studio Code Server, File Editor, or Samba/SSH to configure advanced SearXNG settings, custom search engines, or `preferences.lock` rules. Your manual edits in `settings.yml` are preserved across restarts, while the options from the Home Assistant Configuration UI are merged in on startup.

## OpenClaw / API Compatibility

Both `html` and `json` output formats are enabled by default (`format=json`), and the rate limiter is disabled for single-container private LAN operation.

```yaml
searxng:
  baseUrl: http://homeassistant.local:8888
```
