"""Local config + path constants.

BoloDB uses Google Gemini for every AI operation, so the config is small:
which Gemini model to use and the API key for it. The key can also be supplied
via the ``GEMINI_API_KEY`` environment variable (handy for Docker deployments)
— an explicit key saved from Settings always wins over the environment.

Stored at ``~/.bolodb/config.json``. Older config files that named a different
provider (ollama/claude/openai/groq) are migrated to Gemini on load.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~")) / ".bolodb"
CONFIG_FILE = CONFIG_DIR / "config.json"
KB_FILE = CONFIG_DIR / "knowledge.db"

DEFAULT_MODEL = "gemini-2.5-flash"

# Models the Settings API accepts. Ordered cheapest → most capable.
ALLOWED_MODELS = (
    "gemini-2.5-flash-lite",  # cheapest; fine for small, simple databases
    "gemini-2.5-flash",  # default; best cost/accuracy balance
    "gemini-2.5-pro",  # most accurate; for large schemas / hard questions
)

DEFAULTS = {
    "provider": "gemini",
    "model": DEFAULT_MODEL,
    "api_keys": {"gemini": ""},
    "last_db_url": "",
}


def ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    ensure_dir()
    d = {}
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not isinstance(d, dict):
        d = {}

    cfg = {**DEFAULTS, **d}

    raw_keys = d.get("api_keys", {})
    if not isinstance(raw_keys, dict):
        raw_keys = {}
    cfg["api_keys"] = {"gemini": raw_keys.get("gemini", "")}

    # Migration: configs written before the Gemini-only switch may name another
    # provider or a non-Gemini model. Coerce both so the app always starts in a
    # valid state instead of erroring on an unknown provider.
    if cfg.get("provider") != "gemini":
        cfg["provider"] = "gemini"
    if not str(cfg.get("model", "")).startswith("gemini-"):
        cfg["model"] = DEFAULT_MODEL

    # Env fallback: lets deployments inject the key without touching the file.
    if not cfg["api_keys"]["gemini"] and os.environ.get("GEMINI_API_KEY"):
        cfg["api_keys"]["gemini"] = os.environ["GEMINI_API_KEY"]

    return cfg


def save_config(cfg):
    ensure_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def public_config(cfg):
    """Config as exposed to the frontend — never includes the actual API key,
    only whether one is set."""
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model", ""),
        "api_keys_set": {
            k: ("set" if v else "") for k, v in cfg.get("api_keys", {}).items()
        },
        "last_db_url": cfg.get("last_db_url", ""),
    }
