#!/bin/sh
set -e

# Use SearXNG's virtualenv Python so PyYAML is available
PYTHON_BIN="/usr/local/searxng/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

SETTINGS_PATH="${__SEARXNG_SETTINGS_PATH:-/etc/searxng/settings.yml}"
CONFIG_DIR="$(dirname "$SETTINGS_PATH")"
PERSISTENT_DIR="/config"
PERSISTENT_SETTINGS="${PERSISTENT_DIR}/settings.yml"
SECRET_KEY_FILE="${PERSISTENT_DIR}/.secret_key"

mkdir -p "$CONFIG_DIR" "$PERSISTENT_DIR"

"$PYTHON_BIN" - <<'PYEOF'
import copy
import json
import os
import secrets
import yaml

options_path = "/data/options.json"
default_settings_path = "/usr/local/searxng/searx/settings.yml"
persistent_settings_path = "/config/settings.yml"
secret_key_file = "/config/.secret_key"

options = {}
if os.path.exists(options_path):
    with open(options_path, "r", encoding="utf-8") as f:
        options = json.load(f)

# Load SearXNG's built-in defaults so we have the full plugins list
builtin_defaults = {}
if os.path.exists(default_settings_path):
    with open(default_settings_path, "r", encoding="utf-8") as f:
        builtin_defaults = yaml.safe_load(f) or {}

default_plugins = copy.deepcopy(builtin_defaults.get("plugins") or {
    "searx.plugins.calculator.SXNGPlugin": {"active": True},
    "searx.plugins.infinite_scroll.SXNGPlugin": {"active": False},
    "searx.plugins.hash_plugin.SXNGPlugin": {"active": True},
    "searx.plugins.self_info.SXNGPlugin": {"active": True},
    "searx.plugins.unit_converter.SXNGPlugin": {"active": True},
    "searx.plugins.ahmia_filter.SXNGPlugin": {"active": True},
    "searx.plugins.hostnames.SXNGPlugin": {"active": True},
    "searx.plugins.time_zone.SXNGPlugin": {"active": True},
    "searx.plugins.oa_doi_rewrite.SXNGPlugin": {"active": False},
    "searx.plugins.tor_check.SXNGPlugin": {"active": False},
    "searx.plugins.tracker_url_remover.SXNGPlugin": {"active": True},
})

# 1. Resolve secret_key (from UI option, or persistent file in /config, or generate new)
secret_key = (options.get("secret_key") or "").strip()
if not secret_key:
    if os.path.exists(secret_key_file):
        with open(secret_key_file, "r", encoding="utf-8") as f:
            secret_key = f.read().strip()
    if not secret_key:
        secret_key = secrets.token_hex(32)
        with open(secret_key_file, "w", encoding="utf-8") as f:
            f.write(secret_key)
        print("INFO: Generated and saved a persistent secret_key in /config/.secret_key")
    else:
        print("INFO: Loaded persistent secret_key from /config/.secret_key")
else:
    print("INFO: Using secret_key from Home Assistant add-on configuration")

reset_settings = bool(options.get("reset_settings_yml", False))

# 2. Initialize /config/settings.yml if missing or reset requested
if reset_settings or not os.path.exists(persistent_settings_path):
    if reset_settings:
        print("WARNING: reset_settings_yml is enabled! Regenerating /config/settings.yml...")
    else:
        print("INFO: Creating initial /config/settings.yml...")

    cfg = {
        "use_default_settings": True,
        "general": {
            "instance_name": "SearXNG",
        },
        "search": {
            "safe_search": 0,
            "autocomplete": "duckduckgo",
            "default_lang": "auto",
            "formats": ["html", "json"],
        },
        "server": {
            "bind_address": "0.0.0.0",
            "port": 8080,
            "secret_key": secret_key,
            "limiter": False,
            "image_proxy": True,
        },
        "ui": {
            "default_theme": "simple",
            "default_locale": "",
            "query_in_title": False,
            "results_on_new_tab": False,
            "search_on_category_select": True,
            "theme_args": {
                "simple_style": "auto",
            },
        },
        "plugins": copy.deepcopy(default_plugins),
    }
else:
    print("INFO: Loading existing persistent /config/settings.yml...")
    with open(persistent_settings_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

# Ensure top-level sections exist
cfg.setdefault("use_default_settings", True)
general = cfg.setdefault("general", {})
search = cfg.setdefault("search", {})
server = cfg.setdefault("server", {})
ui = cfg.setdefault("ui", {})
theme_args = ui.setdefault("theme_args", {})

# Ensure all default plugins are present when plugins dict is defined
# (SearXNG replaces the entire plugins dict when 'plugins' is in settings.yml)
plugins = cfg.get("plugins")
if not isinstance(plugins, dict):
    plugins = copy.deepcopy(default_plugins)
    cfg["plugins"] = plugins
else:
    for k, v in default_plugins.items():
        plugins.setdefault(k, copy.deepcopy(v))

# 3. Apply Home Assistant Add-on UI options onto cfg
if "instance_name" in options and options["instance_name"]:
    general["instance_name"] = str(options["instance_name"])

if "safe_search" in options and options["safe_search"] is not None:
    search["safe_search"] = int(options["safe_search"])

if "autocomplete" in options and options["autocomplete"] is not None:
    ac = str(options["autocomplete"]).strip()
    search["autocomplete"] = "" if ac.lower() == "none" else ac

if "default_lang" in options and options["default_lang"] is not None:
    dl = str(options["default_lang"]).strip()
    search["default_lang"] = dl if dl else "auto"

formats = search.setdefault("formats", ["html", "json"])
if "html" not in formats:
    formats.append("html")
if "json" not in formats:
    formats.append("json")

server["bind_address"] = "0.0.0.0"
server["port"] = 8080
server["secret_key"] = secret_key
server["limiter"] = False
if "image_proxy" in options and options["image_proxy"] is not None:
    server["image_proxy"] = bool(options["image_proxy"])

if "default_locale" in options and options["default_locale"] is not None:
    ui["default_locale"] = str(options["default_locale"]).strip()

if "query_in_title" in options and options["query_in_title"] is not None:
    ui["query_in_title"] = bool(options["query_in_title"])

if "results_on_new_tab" in options and options["results_on_new_tab"] is not None:
    ui["results_on_new_tab"] = bool(options["results_on_new_tab"])

if "search_on_category_select" in options and options["search_on_category_select"] is not None:
    ui["search_on_category_select"] = bool(options["search_on_category_select"])

if "theme_style" in options and options["theme_style"]:
    theme_args["simple_style"] = str(options["theme_style"]).strip()

if "infinite_scroll" in options and options["infinite_scroll"] is not None:
    inf_plugin = plugins.setdefault("searx.plugins.infinite_scroll.SXNGPlugin", {})
    inf_plugin["active"] = bool(options["infinite_scroll"])

# Handle enabled_engines and disabled_engines from UI options
enabled_engines = [str(e).strip() for e in (options.get("enabled_engines") or []) if str(e).strip()]
disabled_engines = [str(e).strip() for e in (options.get("disabled_engines") or []) if str(e).strip()]

if enabled_engines or disabled_engines:
    engines_list = cfg.setdefault("engines", [])
    if not isinstance(engines_list, list):
        engines_list = []
        cfg["engines"] = engines_list

    existing_by_name = {
        item.get("name"): item
        for item in engines_list
        if isinstance(item, dict) and "name" in item
    }

    for eng_name in enabled_engines:
        if eng_name in existing_by_name:
            existing_by_name[eng_name]["disabled"] = False
        else:
            entry = {"name": eng_name, "disabled": False}
            engines_list.append(entry)
            existing_by_name[eng_name] = entry

    for eng_name in disabled_engines:
        if eng_name in existing_by_name:
            existing_by_name[eng_name]["disabled"] = True
        else:
            entry = {"name": eng_name, "disabled": True}
            engines_list.append(entry)
            existing_by_name[eng_name] = entry

header_comment = (
    "# SearXNG Persistent Configuration for Home Assistant\n"
    "# Located at /addon_configs/<repo>_searxng/settings.yml (inside container: /config/settings.yml)\n"
    "# You can edit this file directly to customize engines, plugins, and advanced settings.\n"
    "# Standard options set in the Home Assistant Add-on Configuration UI are merged on startup.\n\n"
)

with open(persistent_settings_path, "w", encoding="utf-8") as f:
    f.write(header_comment)
    yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f"INFO: Successfully updated persistent configuration at {persistent_settings_path}")
PYEOF

# Copy to /etc/searxng/settings.yml AND symlink back?
# Wait: entrypoint.sh runs:
#   volume_handler "$__SEARXNG_CONFIG_PATH"
#   check_file "$__SEARXNG_CONFIG_PATH/settings.yml"
# Putting a symlink at /etc/searxng/settings.yml -> /config/settings.yml works with `[ -f "$target" ]` and `chown -R searxng:searxng /etc/searxng`
# Let's also chown /config/settings.yml to searxng:searxng (977:977) so the searxng process can read it.
chmod 644 "$PERSISTENT_SETTINGS"
chown 977:977 "$PERSISTENT_SETTINGS" 2>/dev/null || true
rm -f "$SETTINGS_PATH"
ln -s "$PERSISTENT_SETTINGS" "$SETTINGS_PATH"
chown -h 977:977 "$SETTINGS_PATH" 2>/dev/null || true

echo "INFO: Linked $SETTINGS_PATH -> $PERSISTENT_SETTINGS"

# Run the base image entrypoint
exec /usr/local/searxng/entrypoint.sh
