"""Epic Connect: STOMP over websocket, used by the EOS SDK for chat and social pushes."""
import json
import time
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from . import accounts
from .routes.auth import read_jwt
from .utils import ROOT, now_iso

LOG = open(ROOT / "stomp.log", "a", buffering=1, encoding="utf-8")


class Conn:
    def __init__(self, ws: WebSocket, account_id: str):
        self.ws = ws
        self.account_id = account_id
        self.id = uuid.uuid4().hex
        self.subs: dict[str, str] = {}  # subscription id -> destination


conns: dict[str, set[Conn]] = {}


def _log(direction: str, who: str, text: str):
    LOG.write(f"{now_iso()} {direction} [{who or '?'}] {text[:800]!r}\n")


def _frame(command: str, headers: dict, body: str = "") -> str:
    head = "".join(f"{k}:{v}\n" for k, v in headers.items())
    return f"{command}\n{head}\n{body}\0"


def _parse(raw: str) -> tuple[str, dict, str]:
    raw = raw.lstrip("\r\n")
    head, _, body = raw.partition("\n\n")
    lines = head.replace("\r", "").split("\n")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers.setdefault(k.strip().lower(), v.strip())
    return lines[0].strip(), headers, body.rstrip("\0")


def _account(token: str) -> str:
    token = token.split(" ", 1)[-1].replace("eg1~", "").strip()
    claims = read_jwt(token) or {}
    sub = claims.get("sub", "")
    if not accounts.get(sub):
        sub = (claims.get("act") or {}).get("eaid", "")
    return sub if accounts.get(sub) else ""


async def _send(c: Conn, text: str):
    _log("->", c.account_id, text)
    try:
        await c.ws.send_text(text)
    except Exception:
        pass


async def push(account_id: str, kind: str, payload: dict):
    """Sends one Epic Connect message to every connection of an account."""
    for c in list(conns.get(account_id, ())):
        body = json.dumps({"correlationId": uuid.uuid4().hex, "timestamp": int(time.time() * 1000),
                           "id": uuid.uuid4().hex, "connectionId": c.id, "type": kind, "payload": payload},
                          separators=(",", ":"))
        dests = c.subs or {"sub-0": "launcher"}
        for sub_id, dest in dests.items():
            await _send(c, _frame("MESSAGE", {"destination": dest, "message-id": uuid.uuid4().hex,
                                              "subscription": sub_id, "content-type": "application/json",
                                              "content-length": len(body.encode())}, body))


def online(account_id: str) -> bool:
    return bool(conns.get(account_id))


async def serve(ws: WebSocket, protocols: list[str]):
    proto = next((p for p in ("v12.stomp", "v11.stomp", "v10.stomp") if p in protocols), protocols[0] if protocols else None)
    _log("upgrade", "", f"path={ws.url.path} headers={dict(ws.headers)}")
    account_id = _account(ws.headers.get("authorization", ""))
    await ws.accept(subprotocol=proto)
    c = Conn(ws, account_id)
    _log("open", account_id, f"protocols={protocols} path={ws.url.path}")
    try:
        while True:
            raw = await ws.receive_text()
            if raw.strip("\r\n\0") == "":
                await ws.send_text("\n")  # heartbeat
                continue
            _log("<-", c.account_id, raw)
            command, headers, body = _parse(raw)
            if command in ("CONNECT", "STOMP"):
                if not c.account_id:
                    c.account_id = _account(headers.get("authorization", "") or headers.get("passcode", ""))
                if not c.account_id:
                    await _send(c, _frame("ERROR", {"message": "unauthorized"}))
                    await ws.close()
                    return
                conns.setdefault(c.account_id, set()).add(c)
                await _send(c, _frame("CONNECTED", {"version": "1.2", "heart-beat": "30000,0",
                                                    "server": "halcyon-connect", "session": c.id,
                                                    "connection-id": c.id}))
                print(f"[Connect] {accounts.display_name(c.account_id)} connected")
            elif command == "SUBSCRIBE":
                c.subs[headers.get("id", f"sub-{len(c.subs)}")] = headers.get("destination", "launcher")
            elif command == "UNSUBSCRIBE":
                c.subs.pop(headers.get("id", ""), None)
            elif command == "DISCONNECT":
                if "receipt" in headers:
                    await _send(c, _frame("RECEIPT", {"receipt-id": headers["receipt"]}))
                await ws.close()
                return
            if "receipt" in headers and command not in ("DISCONNECT",):
                await _send(c, _frame("RECEIPT", {"receipt-id": headers["receipt"]}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        group = conns.get(c.account_id)
        if group and c in group:
            group.discard(c)
            if not group:
                conns.pop(c.account_id, None)
            print(f"[Connect] {accounts.display_name(c.account_id)} disconnected")
