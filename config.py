"""Configuration & credential manager for the Gmail ↔ Telegram bridge."""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
CLIENT_SECRET_PATH = BASE_DIR / "client_secret.json"
TOKEN_PATH = BASE_DIR / "token.json"
STATE_PATH = BASE_DIR / "state.json"

DEFAULT_PORT = 8765
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_PORT}/oauth2callback"

GMAIL_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return True
    placeholders = (
        "replace",
        "changeme",
        "your-",
        "your_",
        "example",
        "todo",
        "xxx",
        "paste",
    )
    return any(p in lowered for p in placeholders)


def _clean_client_id(value: Optional[str]) -> str:
    client_id = (value or "").strip()
    if not client_id or _looks_like_placeholder(client_id):
        return ""
    if not client_id.endswith(".apps.googleusercontent.com"):
        return ""
    return client_id


def load_env() -> Dict[str, str]:
    """Load key=value pairs from credentials.json, then .env, then process env."""
    env_vars: Dict[str, str] = {
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_REDIRECT_URI": DEFAULT_REDIRECT_URI,
        "GCP_QUOTA_PROJECT": "",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_BOT_USERNAME": "",
        "AGENT_PORT": str(DEFAULT_PORT),
        "POLL_INTERVAL_SECONDS": "30",
        "SARABYX_GMAIL_ADDRESS": "",
        "GEMINI_API_KEY": "",
        "GEMINI_MODEL": "gemini-3.1-flash-lite",
    }

    if CREDENTIALS_PATH.exists():
        try:
            data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if v is not None and str(v).strip():
                        env_vars[str(k)] = str(v).strip()
        except Exception:
            pass

    if ENV_PATH.exists():
        try:
            for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v:
                    env_vars[k] = v
        except Exception:
            pass

    redirect = env_vars.get("GOOGLE_REDIRECT_URI", "")
    if redirect.startswith("http://127.0.0.1:"):
        env_vars["GOOGLE_REDIRECT_URI"] = DEFAULT_REDIRECT_URI

    for key in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "GCP_QUOTA_PROJECT",
        "SARABYX_GMAIL_ADDRESS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_BOT_USERNAME",
        "POLL_INTERVAL_SECONDS",
        "AGENT_PORT",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
    ):
        if os.environ.get(key):
            env_vars[key] = os.environ[key]

    env_vars["GOOGLE_CLIENT_ID"] = _clean_client_id(env_vars.get("GOOGLE_CLIENT_ID"))
    secret = (env_vars.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not env_vars["GOOGLE_CLIENT_ID"] or _looks_like_placeholder(secret):
        env_vars["GOOGLE_CLIENT_SECRET"] = "" if not env_vars["GOOGLE_CLIENT_ID"] else secret
        if not env_vars["GOOGLE_CLIENT_ID"]:
            env_vars["GOOGLE_CLIENT_SECRET"] = ""

    return env_vars


def oauth_configured() -> bool:
    env = load_env()
    return bool(env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET"))


def save_env_updates(updates: Dict[str, str]) -> Dict[str, str]:
    """Merge updates into credentials.json and .env. Never invent OAuth secrets."""
    current = load_env()
    for k, v in updates.items():
        if v is None:
            continue
        value = str(v).strip()
        if k == "GOOGLE_CLIENT_ID":
            value = _clean_client_id(value)
        current[k] = value

    normalized = {
        "AGENT_PORT": current.get("AGENT_PORT") or str(DEFAULT_PORT),
        "POLL_INTERVAL_SECONDS": current.get("POLL_INTERVAL_SECONDS") or "30",
        "GOOGLE_CLIENT_ID": _clean_client_id(current.get("GOOGLE_CLIENT_ID")),
        "GOOGLE_CLIENT_SECRET": (current.get("GOOGLE_CLIENT_SECRET") or "").strip(),
        "GOOGLE_REDIRECT_URI": DEFAULT_REDIRECT_URI,
        "GCP_QUOTA_PROJECT": (current.get("GCP_QUOTA_PROJECT") or "").strip(),
        "SARABYX_GMAIL_ADDRESS": current.get("SARABYX_GMAIL_ADDRESS", ""),
        "TELEGRAM_BOT_TOKEN": (current.get("TELEGRAM_BOT_TOKEN") or "").strip(),
        "TELEGRAM_CHAT_ID": current.get("TELEGRAM_CHAT_ID", ""),
        "TELEGRAM_BOT_USERNAME": (current.get("TELEGRAM_BOT_USERNAME") or "").strip(),
        "GEMINI_API_KEY": (current.get("GEMINI_API_KEY") or "").strip(),
        "GEMINI_MODEL": (current.get("GEMINI_MODEL") or "gemini-3.1-flash-lite").strip(),
    }
    if not normalized["GOOGLE_CLIENT_ID"]:
        normalized["GOOGLE_CLIENT_SECRET"] = ""

    CREDENTIALS_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")

    lines = [
        "# Gmail ↔ Telegram bridge — local secrets (do not commit)",
        f"AGENT_PORT={normalized['AGENT_PORT']}",
        f"POLL_INTERVAL_SECONDS={normalized['POLL_INTERVAL_SECONDS']}",
        f"GOOGLE_CLIENT_ID={normalized['GOOGLE_CLIENT_ID']}",
        f"GOOGLE_CLIENT_SECRET={normalized['GOOGLE_CLIENT_SECRET']}",
        f"GOOGLE_REDIRECT_URI={normalized['GOOGLE_REDIRECT_URI']}",
        f"GCP_QUOTA_PROJECT={normalized['GCP_QUOTA_PROJECT']}",
        f"SARABYX_GMAIL_ADDRESS={normalized['SARABYX_GMAIL_ADDRESS']}",
        f"TELEGRAM_BOT_TOKEN={normalized['TELEGRAM_BOT_TOKEN']}",
        f"TELEGRAM_CHAT_ID={normalized['TELEGRAM_CHAT_ID']}",
        f"TELEGRAM_BOT_USERNAME={normalized['TELEGRAM_BOT_USERNAME']}",
        f"GEMINI_API_KEY={normalized['GEMINI_API_KEY']}",
        f"GEMINI_MODEL={normalized['GEMINI_MODEL']}",
        "",
    ]
    try:
        ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    except PermissionError:
        pass
    return normalized


def get_gcp_oauth_client_config() -> Dict[str, str]:
    """Return OAuth client fields. Empty client_id means not configured."""
    env = load_env()
    return {
        "client_id": env.get("GOOGLE_CLIENT_ID") or "",
        "client_secret": env.get("GOOGLE_CLIENT_SECRET") or "",
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "quota_project": env.get("GCP_QUOTA_PROJECT") or "",
    }


def load_token() -> Optional[Dict[str, Any]]:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_token(token_data: Dict[str, Any]) -> None:
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2), encoding="utf-8")


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"notified_ids": [], "telegram_offset": 0}


def save_state(state: Dict[str, Any]) -> None:
    if "notified_ids" in state and isinstance(state["notified_ids"], list):
        state["notified_ids"] = state["notified_ids"][-500:]
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
