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
PERSISTENT_PRESETS="${PERSISTENT_DIR}/presets.yml"
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
persistent_presets_path = "/config/presets.yml"
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
disabled_engines = [str(e).strip() for e in (options.get("disabled_engines") or ["wikidata"]) if str(e).strip()]

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

# 4. Initialize & Sync /config/presets.yml (Preconfigured Search Tasks & Aliases)
default_presets = [
    {
        "alias": "local_news",
        "description": "Get a randomized digest of 4 local Vojvodina and Subotica news stories in Hungarian (Pannon RTV) with headlines and short leads. Summarize each in 1 sentence and let the user pick one to read in detail.",
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
        "description": "Get a randomized digest of 4 latest IT, software engineering, AI, and technology news stories with headlines and short leads. Summarize each in 1 sentence and let the user pick one to explore.",
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
        "description": "Deep detailed search for a specific topic, news story, or question. Finds the top matching result and extracts its full article text so you can give a comprehensive answer.",
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
        "description": "Detailed technical documentation search across GitHub, StackOverflow, and Home Assistant docs with full-page content extraction of the top result.",
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

existing_presets = []
if not reset_settings and os.path.exists(persistent_presets_path):
    try:
        with open(persistent_presets_path, "r", encoding="utf-8") as f:
            loaded_presets_doc = yaml.safe_load(f) or {}
            if isinstance(loaded_presets_doc, dict):
                existing_presets = loaded_presets_doc.get("presets") or []
            elif isinstance(loaded_presets_doc, list):
                existing_presets = loaded_presets_doc
    except Exception as exc:
        print(f"WARNING: Could not parse existing {persistent_presets_path}: {exc}")

presets_by_alias = {}
for item in (existing_presets or default_presets):
    if isinstance(item, dict) and item.get("alias"):
        alias_key = str(item["alias"]).strip()
        presets_by_alias[alias_key] = copy.deepcopy(item)

# Ensuredetailed_search is added if upgrading from v1.3.x
for dp in default_presets:
    if dp["alias"] not in presets_by_alias:
        presets_by_alias[dp["alias"]] = copy.deepcopy(dp)

# Merge presets from Home Assistant UI (options["search_presets"])
ui_presets = options.get("search_presets")
if isinstance(ui_presets, list) and len(ui_presets) > 0:
    for ui_item in ui_presets:
        if not isinstance(ui_item, dict) or not ui_item.get("alias"):
            continue
        alias_key = str(ui_item["alias"]).strip()
        raw_sites = ui_item.get("sites", "")
        if isinstance(raw_sites, list):
            sites_list = [str(s).strip() for s in raw_sites if str(s).strip()]
        else:
            sites_list = [s.strip() for s in str(raw_sites or "").split(",") if s.strip()]

        merged = presets_by_alias.get(alias_key, {})
        raw_tr = str(ui_item.get("time_range") if ui_item.get("time_range") is not None else merged.get("time_range", "")).strip()
        if raw_tr.lower() in ("any", "none", "all"):
            raw_tr = ""

        # Determine response_mode & shuffle defaults gracefully even if upgrading from v1.3.x options.json
        default_is_news = alias_key in ("local_news", "it_news")
        resp_mode = str(ui_item.get("response_mode") or merged.get("response_mode") or ("digest" if default_is_news else "detailed")).strip().lower()
        if resp_mode not in ("digest", "detailed"):
            resp_mode = "digest"

        if "shuffle_results" in ui_item and ui_item["shuffle_results"] is not None:
            shuf = bool(ui_item["shuffle_results"])
        elif "shuffle_results" in merged:
            shuf = bool(merged["shuffle_results"])
        else:
            shuf = default_is_news

        if "digest_words" in ui_item and ui_item["digest_words"] is not None:
            d_words = int(ui_item["digest_words"])
        else:
            d_words = int(merged.get("digest_words", 25))

        if "fetch_top_n" in ui_item and ui_item["fetch_top_n"] is not None:
            f_top = int(ui_item["fetch_top_n"])
            # If upgrading from v1.3.x where local_news had fetch_top_n=3 and no response_mode field,
            # respect digest mode (0 full fetches) so Voice Satellite doesn't hit 1001!
            if "response_mode" not in ui_item and resp_mode == "digest":
                f_top = 0
        else:
            f_top = 0 if resp_mode == "digest" else int(merged.get("fetch_top_n", 1))

        merged.update({
            "alias": alias_key,
            "description": str(ui_item.get("description") or merged.get("description") or f"Preconfigured search preset '{alias_key}'"),
            "sites": sites_list,
            "default_query": str(ui_item.get("default_query") if ui_item.get("default_query") is not None else merged.get("default_query", "")),
            "query_template": str(ui_item.get("query_template") if ui_item.get("query_template") is not None else merged.get("query_template", "")),
            "categories": str(ui_item.get("categories") if ui_item.get("categories") is not None else merged.get("categories", "general")),
            "engines": str(ui_item.get("engines") if ui_item.get("engines") is not None else merged.get("engines", "")),
            "language": str(ui_item.get("language") if ui_item.get("language") is not None else merged.get("language", "")),
            "time_range": raw_tr,
            "response_mode": resp_mode,
            "shuffle_results": shuf,
            "max_results": int(ui_item.get("max_results") if ui_item.get("max_results") is not None else merged.get("max_results", 4)),
            "digest_words": d_words,
            "fetch_top_n": f_top,
        })
        presets_by_alias[alias_key] = merged

if not presets_by_alias:
    for item in default_presets:
        presets_by_alias[item["alias"]] = item

presets_header = (
    "# SearXNG MCP Preconfigured Search Tasks & Aliases (Presets)\n"
    "# Located at /addon_configs/<repo>_searxng/presets.yml (inside container: /config/presets.yml)\n"
    "# Each preset here is automatically exposed as a dedicated MCP tool (e.g. search_local_news, search_detailed_search)\n"
    "# AND can be edited live without restarting the add-on!\n\n"
)

with open(persistent_presets_path, "w", encoding="utf-8") as f:
    f.write(presets_header)
    yaml.safe_dump({"presets": list(presets_by_alias.values())}, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f"INFO: Successfully synced {len(presets_by_alias)} search presets to {persistent_presets_path}")
PYEOF

chmod 644 "$PERSISTENT_SETTINGS" "$PERSISTENT_PRESETS" 2>/dev/null || true
chown 977:977 "$PERSISTENT_SETTINGS" "$PERSISTENT_PRESETS" 2>/dev/null || true
rm -f "$SETTINGS_PATH"
ln -s "$PERSISTENT_SETTINGS" "$SETTINGS_PATH"
chown -h 977:977 "$SETTINGS_PATH" 2>/dev/null || true

echo "INFO: Linked $SETTINGS_PATH -> $PERSISTENT_SETTINGS"

# Check if built-in MCP server is enabled (default: true)
MCP_ENABLED=$("$PYTHON_BIN" -c "import json, os; d = json.load(open('/data/options.json')) if os.path.exists('/data/options.json') else {}; print('true' if d.get('mcp_enabled', True) else 'false')")
MCP_MAX_RESULTS=$("$PYTHON_BIN" -c "import json, os; d = json.load(open('/data/options.json')) if os.path.exists('/data/options.json') else {}; print(int(d.get('mcp_max_results', 5)))")

if [ "$MCP_ENABLED" = "true" ]; then
  export MCP_PORT=8889
  export MCP_MAX_RESULTS="$MCP_MAX_RESULTS"
  export SEARXNG_INTERNAL_URL="http://127.0.0.1:8080"
  export SEARXNG_PRESETS_PATH="$PERSISTENT_PRESETS"
  export SEARXNG_HISTORY_PATH="/config/search_history.jsonl"
  "$PYTHON_BIN" /mcp_server.py &
  echo "INFO: Started built-in MCP server on port 8889 (MCP: /mcp, /sse | Live Search Inspector: http://homeassistant.local:8889/logs)"
fi

# Run the base image entrypoint
exec /usr/local/searxng/entrypoint.sh
