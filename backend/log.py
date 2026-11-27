"""Colored console logs. Usernames get a stable color each, important events stand out."""
import os
import sys
import zlib
from datetime import datetime

_ON = sys.stdout.isatty() and not os.environ.get("NO_COLOR") or bool(os.environ.get("FORCE_COLOR"))
if _ON and os.name == "nt":
    os.system("")  # turns on ANSI escapes in the Windows console

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
NAME_COLORS = [33, 35, 36, 92, 93, 94, 95, 96, 91, 32, 34]
TAGS = {"Auth": 92, "Bot": 95, "Ban": 91, "Matchmaker": 96, "MMSv2": 96, "Party": 94, "Friends": 94,
        "XMPP": 36, "Connect": 36, "Presence": 36, "Backend": 92, "Missing": 90, "Cosmetics": 93}
LEVELS = {"info": None, "good": 92, "warn": 93, "error": 91}


def _c(code, text: str) -> str:
    return f"\033[{code}m{text}{RESET}" if _ON and code else text


def user(name: str) -> str:
    """A username in its own color (same name, same color every time)."""
    return _c(f"1;{NAME_COLORS[zlib.crc32(str(name).lower().encode()) % len(NAME_COLORS)]}", str(name))


def log(tag: str, text: str, level: str = "info") -> None:
    stamp = _c(DIM[2:-1], datetime.now().strftime("%H:%M:%S"))
    head = _c(f"1;{TAGS.get(tag, 37)}", f"[{tag}]")
    body = _c(LEVELS.get(level), text) if level != "info" else text
    if level == "error":
        head = _c("1;41;97", f"[{tag}]")
    print(f"{stamp} {head} {body}", flush=True)
