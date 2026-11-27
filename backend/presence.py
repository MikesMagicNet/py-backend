"""EOS presence: what each Epic Connect connection set, pushed live to friends as presence.v1.UPDATE."""
from . import stomp
from .social import Friends
from .utils import now_iso

UPDATE = "presence.v1.UPDATE"

# account_id -> connection_id -> {ns, status, activity, props, conn_props, updated}
state: dict[str, dict[str, dict]] = {}
versions: dict[str, int] = {}


def view(account_id: str) -> dict:
    conns = state.get(account_id, {})
    if not conns:
        return {"accountId": account_id, "status": "offline", "activity": {}, "props": {}, "perNs": [],
                "ver": versions.get(account_id, 0)}
    latest = max(conns.values(), key=lambda p: p["updated"])
    per_ns: dict[str, dict] = {}
    for cid, p in sorted(conns.items(), key=lambda kv: kv[1]["updated"]):
        entry = per_ns.setdefault(p["ns"], {"ns": p["ns"], "conns": []})
        entry.update(status=p["status"], activity=p["activity"], props=p["props"])
        entry["conns"].append({"id": cid, "props": p["conn_props"], "lastOnline": p["updated"]})
    status = "online" if any(p["status"] == "online" for p in conns.values()) else latest["status"]
    return {"accountId": account_id, "status": status, "activity": latest["activity"], "props": latest["props"],
            "perNs": list(per_ns.values()), "ver": versions.get(account_id, 0)}


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def set_presence(account_id: str, ns: str, connection_id: str, body: dict) -> tuple[dict, bool]:
    """Returns the merged presence and whether this is a new connection (it then needs its friends' presence)."""
    conns = state.setdefault(account_id, {})
    came_online = connection_id not in conns
    cur = conns.setdefault(connection_id, {"ns": ns, "status": "online", "activity": {}, "props": {},
                                           "conn_props": {}, "updated": now_iso()})
    cur["ns"] = ns
    if body.get("status"):
        cur["status"] = body["status"]
    if "activity" in body:
        cur["activity"] = _dict(body["activity"])
    cur["props"].update(_dict(body.get("props")))
    for key in body.get("removeProps") or []:
        cur["props"].pop(key, None)
    cur["conn_props"].update(_dict(_dict(body.get("conn")).get("props")))
    cur["updated"] = now_iso()
    versions[account_id] = versions.get(account_id, 0) + 1
    return view(account_id), came_online


def clear(account_id: str, connection_id: str | None = None) -> bool:
    """Drops one connection (or all). Returns True when the account is now offline."""
    conns = state.get(account_id)
    if not conns:
        return False
    if connection_id is None:
        conns.clear()
    else:
        conns.pop(connection_id, None)
    if conns:
        return False
    state.pop(account_id, None)
    versions[account_id] = versions.get(account_id, 0) + 1
    return True


async def broadcast(account_id: str):
    payload = view(account_id)
    for friend in list(Friends.of(account_id)["friends"]):
        await stomp.push(friend, UPDATE, payload)


async def send_friends_to(account_id: str):
    """A user coming online gets everyone already online."""
    for friend in list(Friends.of(account_id)["friends"]):
        if state.get(friend):
            await stomp.push(account_id, UPDATE, view(friend))


async def share(a: str, b: str):
    """New friends see each other straight away."""
    for x, y in ((a, b), (b, a)):
        if state.get(x):
            await stomp.push(y, UPDATE, view(x))


async def hide(a: str, b: str):
    for x, y in ((a, b), (b, a)):
        await stomp.push(y, UPDATE, {"accountId": x, "status": "offline", "activity": {}, "props": {},
                                     "perNs": [], "ver": versions.get(x, 0)})


async def _on_close(account_id: str, connection_id: str):
    had = bool(state.get(account_id))
    clear(account_id, connection_id)
    if not stomp.online(account_id):
        clear(account_id)
    if had:
        await broadcast(account_id)


stomp.on_close.append(_on_close)
