"""Account store. One JSON file, keyed by account id."""
import hashlib
import json
import os
import threading

from .utils import CONFIG, DATA, account_id_from_name, now_iso

FILE = DATA / "accounts.json"
_lock = threading.Lock()
_accounts: dict[str, dict] = {}


def _load():
    global _accounts
    if FILE.exists():
        _accounts = json.loads(FILE.read_text(encoding="utf-8"))


def _save():
    DATA.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_accounts, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


def get(account_id: str) -> dict | None:
    return _accounts.get(account_id)


def by_name(name: str) -> dict | None:
    name = name.lower()
    for acc in _accounts.values():
        if acc["displayName"].lower() == name:
            return acc
    return None


def by_email(email: str) -> dict | None:
    email = email.lower()
    for acc in _accounts.values():
        if acc.get("email", "").lower() == email:
            return acc
    return None


def display_name(account_id: str) -> str:
    acc = _accounts.get(account_id)
    return acc["displayName"] if acc else account_id


def search(prefix: str, exclude: str = "", limit: int = 20) -> list[dict]:
    prefix = prefix.lower()
    hits = [a for a in _accounts.values()
            if a["accountId"] != exclude and a["displayName"].lower().startswith(prefix)]
    hits.sort(key=lambda a: (a["displayName"].lower() != prefix, a["displayName"].lower()))
    return hits[:limit]


def create(name: str, email: str = "", password: str = "", discord_id: str = "") -> dict:
    with _lock:
        if by_name(name):
            raise ValueError("display name taken")
        account_id = account_id_from_name(name)
        acc = {"accountId": account_id, "displayName": name, "email": email or f"{account_id}@halcyon.local",
               "created": now_iso(), "discordId": discord_id, "banned": False}
        if password:
            acc["salt"] = os.urandom(16).hex()
            acc["password"] = _hash(password, acc["salt"])
        _accounts[account_id] = acc
        _save()
        return acc


def update(account_id: str, **fields) -> dict:
    with _lock:
        acc = _accounts[account_id]
        if "password" in fields:
            acc["salt"] = os.urandom(16).hex()
            acc["password"] = _hash(fields.pop("password"), acc["salt"])
        acc.update(fields)
        _save()
        return acc


def check_password(acc: dict, password: str) -> bool:
    if "password" not in acc:
        return True
    return _hash(password, acc["salt"]) == acc["password"]


def login(name_or_email: str, password: str = "") -> dict | None:
    """Password login. Unknown names are created when autoCreateAccounts is on."""
    acc = by_email(name_or_email) if "@" in name_or_email and not name_or_email.endswith("@halcyon.local") else None
    if not acc:
        acc = by_name(name_or_email.split("@")[0])
    if acc:
        return acc if check_password(acc, password) and not acc.get("banned") else None
    if CONFIG.get("autoCreateAccounts", True):
        return create(name_or_email.split("@")[0][:32])
    return None


def ensure(name: str) -> dict:
    """Account for a name from a token or exchange code."""
    return by_name(name) or create(name[:32])


_load()
