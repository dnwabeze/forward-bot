import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val.strip()


def _list(key: str) -> list[str]:
    raw = os.getenv(key, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


API_ID: int = int(_require("API_ID"))
API_HASH: str = _require("API_HASH")
PHONE_NUMBER: str = _require("PHONE_NUMBER")
# Optional until groups are chosen; required once monitoring starts
SIGMA_BOT: str = os.getenv("SIGMA_BOT_USERNAME", "").strip()
MONITORED_GROUPS: list[str] = _list("MONITORED_GROUPS")
DEDUP_FILE: str = os.getenv("DEDUP_FILE", "seen_cas.txt").strip()
SESSION_NAME: str = os.getenv("SESSION_NAME", "sigma_watcher").strip()
BRIDGE_PORT: int = int(os.getenv("BRIDGE_PORT", "5050"))
