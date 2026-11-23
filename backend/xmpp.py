"""XMPP over websocket (RFC 7395 framing). Presence, direct chat, party rooms and admin notifications."""
import base64
import json
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from fastapi import WebSocket, WebSocketDisconnect

from . import accounts
from .social import Friends, Parties, iso_in
from .utils import ROOT, now_iso

DOMAIN = "prod.ol.epicgames.com"
MUC = f"muc.{DOMAIN}"
ADMIN = f"xmpp-admin@{DOMAIN}"
FRAMING = "urn:ietf:params:xml:ns:xmpp-framing"
PARTY = "com.epicgames.social.party.notification.v0."
FRIEND = "com.epicgames.friends.core.apiobjects.Friend"
FRIEND_REMOVAL = "com.epicgames.friends.core.apiobjects.FriendRemoval"
BLOCK_ADDED = "com.epicgames.friends.core.apiobjects.BlockListEntryAdded"
BLOCK_REMOVED = "com.epicgames.friends.core.apiobjects.BlockListEntryRemoved"

LOG = open(ROOT / "xmpp.log", "a", buffering=1, encoding="utf-8")


class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.account_id = ""
        self.resource = ""
        self.jid = ""
        self.authed = False
        self.ready = False
        self.status: str | None = None   # last presence status JSON, None when unavailable
        self.show = ""
        self.rooms: set[str] = set()

    @property
    def bare(self) -> str:
        return f"{self.account_id}@{DOMAIN}"


clients: dict[str, set[Client]] = {}   # accountId -> connections
rooms: dict[str, dict[str, Client]] = {}  # room -> nick -> client


def _log(direction: str, who: str, xml: str):
    LOG.write(f"{now_iso()} {direction} [{who or '?'}] {xml[:600]}\n")


async def _send(c: Client, xml: str):
    _log("->", c.account_id, xml)
    try:
        await c.ws.send_text(xml)
    except Exception:
        pass


async def send_to(account_id: str, xml_for):
    """xml_for(client) builds the stanza per connection so `to` can carry the full jid."""
    for c in list(clients.get(account_id, ())):
        if c.ready:
            await _send(c, xml_for(c))


def is_online(account_id: str) -> bool:
    return any(c.ready and c.status is not None for c in clients.get(account_id, ()))


async def admin_message(account_id: str, body: dict):
    text = escape(json.dumps(body, separators=(",", ":")))
    await send_to(account_id, lambda c: f'<message xmlns="jabber:client" from="{ADMIN}" to="{c.jid}" '
                                         f'id="{uuid.uuid4().hex.upper()}"><body>{text}</body></message>')


def _presence(c: Client, to: str) -> str:
    show = f"<show>{escape(c.show)}</show>" if c.show else ""
    status = f"<status>{escape(c.status or '')}</status>"
    delay = f'<delay xmlns="urn:xmpp:delay" stamp="{now_iso()}"/>'
    return f'<presence xmlns="jabber:client" from="{c.jid}" to="{to}">{show}{status}{delay}</presence>'


def _unavailable(c: Client, to: str) -> str:
    return f'<presence xmlns="jabber:client" from="{c.jid}" to="{to}" type="unavailable"/>'


async def _broadcast_presence(c: Client, gone: bool = False):
    for friend in list(Friends.of(c.account_id)["friends"]):
        await send_to(friend, lambda other: _unavailable(c, other.jid) if gone else _presence(c, other.jid))


async def _friend_presence_to(c: Client):
    for friend in list(Friends.of(c.account_id)["friends"]):
        for other in list(clients.get(friend, ())):
            if other.ready and other.status is not None:
                await _send(c, _presence(other, c.jid))


async def share_presence(a: str, b: str):
    """New friends see each other straight away."""
    for x, y in ((a, b), (b, a)):
        for c in list(clients.get(x, ())):
            if c.ready and c.status is not None:
                await send_to(y, lambda other: _presence(c, other.jid))


async def hide_presence(a: str, b: str):
    for x, y in ((a, b), (b, a)):
        for c in list(clients.get(x, ())):
            if c.ready:
                await send_to(y, lambda other: _unavailable(c, other.jid))


def _tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((ch for ch in el if _tag(ch) == name), None)


async def _handle(c: Client, raw: str):
    _log("<-", c.account_id, raw)
    try:
        el = ET.fromstring(raw)
    except ET.ParseError:
        return
    tag = _tag(el)

    if tag == "open":
        await _send(c, f'<open xmlns="{FRAMING}" from="{DOMAIN}" id="{uuid.uuid4()}" version="1.0" xml:lang="en"/>')
        if not c.authed:
            features = ('<mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl"><mechanism>PLAIN</mechanism>'
                        '</mechanisms><ver xmlns="urn:xmpp:features:rosterver"/>'
                        '<starttls xmlns="urn:ietf:params:xml:ns:xmpp-tls"/>')
        else:
            features = ('<ver xmlns="urn:xmpp:features:rosterver"/><starttls xmlns="urn:ietf:params:xml:ns:xmpp-tls"/>'
                        '<bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"/>'
                        '<session xmlns="urn:ietf:params:xml:ns:xmpp-session"/>')
        await _send(c, f'<stream:features xmlns:stream="http://etherx.jabber.org/streams">{features}</stream:features>')

    elif tag == "auth":
        # PLAIN: \0accountId\0token
        try:
            parts = base64.b64decode(el.text or "").decode().split("\0")
        except Exception:
            parts = []
        account_id = parts[1] if len(parts) > 2 else ""
        if not account_id or not accounts.get(account_id):
            await _send(c, '<failure xmlns="urn:ietf:params:xml:ns:xmpp-sasl"><not-authorized/></failure>')
            await c.ws.close()
            return
        c.account_id, c.authed = account_id, True
        await _send(c, '<success xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>')

    elif tag == "iq":
        iq_id = el.get("id", "")
        bind = _child(el, "bind")
        if bind is not None and c.authed:
            res = _child(bind, "resource")
            c.resource = (res.text if res is not None and res.text else uuid.uuid4().hex)
            c.jid = f"{c.bare}/{c.resource}"
            for old in list(clients.get(c.account_id, ())):
                if old.resource == c.resource:
                    await old.ws.close()
            clients.setdefault(c.account_id, set()).add(c)
            c.ready = True
            await _send(c, f'<iq xmlns="jabber:client" type="result" id={quoteattr(iq_id)} to="{c.jid}">'
                           f'<bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{c.jid}</jid></bind></iq>')
            print(f"[XMPP] {accounts.display_name(c.account_id)} connected")
        elif _child(el, "ping") is not None or el.get("type") in ("get", "set"):
            await _send(c, f'<iq xmlns="jabber:client" type="result" id={quoteattr(iq_id)} from="{DOMAIN}" to="{c.jid}"/>')

    elif tag == "presence" and c.ready:
        to = el.get("to", "")
        if to.endswith(MUC) or "@" + MUC in to:
            await _room_presence(c, el, to)
            return
        if el.get("type") == "unavailable":
            if c.status is not None:
                await _broadcast_presence(c, gone=True)
            c.status = None
            return
        status, show = _child(el, "status"), _child(el, "show")
        first = c.status is None
        c.status = status.text if status is not None and status.text else "{}"
        c.show = show.text if show is not None and show.text else ""
        await _broadcast_presence(c)
        if first:
            await _friend_presence_to(c)

    elif tag == "message" and c.ready:
        to = el.get("to", "")
        body = _child(el, "body")
        if body is None or body.text is None:
            return
        if el.get("type") == "groupchat":
            await _room_message(c, to.split("/")[0], body.text, el.get("id"))
            return
        target = to.split("@")[0]
        if not Friends.are_friends(c.account_id, target):
            return
        mid = el.get("id") or uuid.uuid4().hex
        text = escape(body.text)
        await send_to(target, lambda other: f'<message xmlns="jabber:client" from="{c.jid}" to="{other.jid}" '
                                            f'type="chat" id={quoteattr(mid)}><body>{text}</body></message>')

    elif tag == "close":
        await _send(c, f'<close xmlns="{FRAMING}"/>')
        await c.ws.close()


# Party rooms (Party-<id>@muc...)

async def _room_presence(c: Client, el: ET.Element, to: str):
    room, _, nick = to.partition("/")
    nick = nick or c.jid
    members = rooms.setdefault(room, {})
    if el.get("type") == "unavailable":
        members.pop(nick, None)
        c.rooms.discard(room)
        for other in list(members.values()):
            await _send(other, f'<presence xmlns="jabber:client" from={quoteattr(room + "/" + nick)} to="{other.jid}" '
                               f'type="unavailable"><x xmlns="http://jabber.org/protocol/muc#user">'
                               f'<item affiliation="none" role="none"/></x></presence>')
        if not members:
            rooms.pop(room, None)
        return
    members[nick] = c
    c.rooms.add(room)
    item = (f'<x xmlns="http://jabber.org/protocol/muc#user"><item nick={quoteattr(nick)} jid="{c.jid}" '
            f'role="participant" affiliation="none"/>')
    for other_nick, other in list(members.items()):
        # Everyone already in the room to the new member, and the new member to everyone
        await _send(c, f'<presence xmlns="jabber:client" from={quoteattr(room + "/" + other_nick)} to="{c.jid}">'
                       f'<x xmlns="http://jabber.org/protocol/muc#user"><item nick={quoteattr(other_nick)} '
                       f'jid="{other.jid}" role="participant" affiliation="none"/></x></presence>')
        if other is not c:
            await _send(other, f'<presence xmlns="jabber:client" from={quoteattr(room + "/" + nick)} '
                               f'to="{other.jid}">{item}</x></presence>')
    await _send(c, f'<presence xmlns="jabber:client" from={quoteattr(room + "/" + nick)} to="{c.jid}">{item}'
                   f'<status code="110"/><status code="100"/><status code="170"/></x></presence>')


async def _room_message(c: Client, room: str, text: str, mid: str | None):
    members = rooms.get(room, {})
    nick = next((n for n, m in members.items() if m is c), None)
    if nick is None:
        return
    mid = mid or uuid.uuid4().hex
    for other in list(members.values()):
        await _send(other, f'<message xmlns="jabber:client" from={quoteattr(room + "/" + nick)} to="{other.jid}" '
                           f'type="groupchat" id={quoteattr(mid)}><body>{escape(text)}</body></message>')


async def serve(ws: WebSocket):
    await ws.accept(subprotocol="xmpp")
    c = Client(ws)
    try:
        while True:
            await _handle(c, await ws.receive_text())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        conns = clients.get(c.account_id)
        if conns and c in conns:
            conns.discard(c)
            if not conns:
                clients.pop(c.account_id, None)
            if c.status is not None:
                await _broadcast_presence(c, gone=True)
            for room in list(c.rooms):
                members = rooms.get(room, {})
                for nick in [n for n, m in members.items() if m is c]:
                    members.pop(nick)
            print(f"[XMPP] {accounts.display_name(c.account_id)} disconnected")


# Notifications sent from the REST side

def _dn(account_id: str) -> str:
    return accounts.display_name(account_id)


async def friend_added(from_id: str, to_id: str, result: str):
    now = now_iso()
    if result == "requested":
        await admin_message(to_id, {"type": FRIEND, "timestamp": now, "payload": {
            "accountId": from_id, "status": "PENDING", "direction": "INBOUND", "created": now, "favorite": False}})
        await admin_message(from_id, {"type": FRIEND, "timestamp": now, "payload": {
            "accountId": to_id, "status": "PENDING", "direction": "OUTBOUND", "created": now, "favorite": False}})
    elif result == "accepted":
        await admin_message(to_id, {"type": FRIEND, "timestamp": now, "payload": {
            "accountId": from_id, "status": "ACCEPTED", "direction": "OUTBOUND", "created": now, "favorite": False}})
        await admin_message(from_id, {"type": FRIEND, "timestamp": now, "payload": {
            "accountId": to_id, "status": "ACCEPTED", "direction": "INBOUND", "created": now, "favorite": False}})
        await share_presence(from_id, to_id)


async def friend_removed(from_id: str, to_id: str, kind: str):
    now = now_iso()
    theirs = {"friends": "DELETED", "incoming": "ABORTED", "outgoing": "REJECTED"}[kind]
    mine = {"friends": "DELETED", "incoming": "REJECTED", "outgoing": "ABORTED"}[kind]
    await admin_message(to_id, {"type": FRIEND_REMOVAL, "timestamp": now,
                                "payload": {"accountId": from_id, "reason": theirs}})
    await admin_message(from_id, {"type": FRIEND_REMOVAL, "timestamp": now,
                                  "payload": {"accountId": to_id, "reason": mine}})
    if kind == "friends":
        await hide_presence(from_id, to_id)


async def block_changed(account_id: str, target: str, added: bool):
    await admin_message(account_id, {"type": BLOCK_ADDED if added else BLOCK_REMOVED, "timestamp": now_iso(),
                                     "payload": {"accountId": target}})


def _strip_epv(meta: dict) -> dict:
    # Invites marked epv=1 are dropped as duplicates of an EOS party invite
    out = dict(meta or {})
    key = "urn:epic:invite:platformdata_s"
    if isinstance(out.get(key), str):
        out[key] = ",".join(p for p in out[key].split(",") if not p.startswith("epv=")) or ""
    return out


def _member_view(m: dict) -> dict:
    return {"account_id": m["account_id"], "account_dn": _dn(m["account_id"]), "meta": m["meta"],
            "connections": m["connections"], "revision": m["revision"], "updated_at": m["updated_at"],
            "joined_at": m["joined_at"], "role": m["role"]}


async def party_ping(from_id: str, to_id: str, ping: dict):
    await admin_message(to_id, {"type": PARTY + "PING", "ns": "Fortnite", "pinger_id": from_id,
                                "pinger_dn": _dn(from_id), "sent": ping["sent_at"],
                                "expires": ping["expires_at"], "meta": ping.get("meta", {})})


async def party_invite(party: dict, invite: dict):
    await admin_message(invite["sent_to"], {
        "type": PARTY + "INITIAL_INVITE", "ns": "Fortnite", "party_id": party["id"],
        "inviter_id": invite["sent_by"], "inviter_dn": _dn(invite["sent_by"]), "invitee_id": invite["sent_to"],
        "sent_by": invite["sent_by"], "meta": _strip_epv(invite["meta"]),
        "members": [_member_view(m) for m in party["members"]], "friends_ids": [],
        "sent": invite["sent_at"], "updated_at": invite["updated_at"], "created_at": party["created_at"],
        "expires_at": invite["expires_at"]})


async def party_invite_cancelled(party: dict, invitee: str, inviter: str):
    await admin_message(invitee, {"type": PARTY + "INVITE_CANCELLED", "ns": "Fortnite", "party_id": party["id"],
                                  "inviter_id": inviter, "inviter_dn": _dn(inviter), "invitee_id": invitee,
                                  "sent": now_iso(), "expires": iso_in(0)})


async def party_invite_declined(party: dict, invitee: str, inviter: str):
    await admin_message(inviter, {"type": PARTY + "INVITE_DECLINED", "ns": "Fortnite", "party_id": party["id"],
                                  "inviter_id": inviter, "invitee_id": invitee, "invitee_dn": _dn(invitee),
                                  "sent": now_iso()})


async def party_intention(intention: dict):
    """Request to join: goes to the friend whose party the requester wants into."""
    await admin_message(intention["requestee_id"], {
        "type": PARTY + "INITIAL_INTENTION", "ns": "Fortnite",
        "requester_id": intention["requester_id"], "requester_dn": _dn(intention["requester_id"]),
        "requester_pl": "WIN", "requester_pl_dn": _dn(intention["requester_id"]),
        "requestee_id": intention["requestee_id"], "requestee_dn": _dn(intention["requestee_id"]),
        "meta": _strip_epv(intention["meta"]), "sent_at": intention["sent_at"], "sent": intention["sent_at"],
        "expires_at": intention["expires_at"]})


async def _to_party(party: dict, body: dict, also: tuple[str, ...] = ()):
    msg = {"ns": "Fortnite", "party_id": party["id"], "revision": party["revision"], "sent": now_iso(), **body}
    for account_id in {m["account_id"] for m in party["members"]} | set(also):
        await admin_message(account_id, msg)


async def member_joined(party: dict, member: dict):
    await _to_party(party, {"type": PARTY + "MEMBER_JOINED", "account_id": member["account_id"],
                            "account_dn": _dn(member["account_id"]), "connection": member["connections"][0],
                            "member_state_updated": member["meta"], "joined_at": member["joined_at"],
                            "updated_at": member["updated_at"]})


async def member_left(result: dict, account_id: str, kicked_by: str | None = None):
    party = result["party"]
    body = {"type": PARTY + ("MEMBER_KICKED" if kicked_by else "MEMBER_LEFT"), "account_id": account_id,
            "member_state_update": {}}
    if party is None:
        await admin_message(account_id, {"ns": "Fortnite", "party_id": result["party_id"], "revision": 0,
                                         "sent": now_iso(), **body})
        return
    await _to_party(party, body, also=(account_id,))
    if result["new_captain"]:
        await new_captain(party, result["new_captain"])


async def new_captain(party: dict, account_id: str):
    await _to_party(party, {"type": PARTY + "MEMBER_NEW_CAPTAIN", "account_id": account_id,
                            "account_dn": _dn(account_id), "member_state_updated": {}, "joined_at": now_iso(),
                            "updated_at": now_iso()})


async def member_updated(party: dict, member: dict, update: dict | None, remove: list | None):
    await _to_party(party, {"type": PARTY + "MEMBER_STATE_UPDATED", "account_id": member["account_id"],
                            "account_dn": _dn(member["account_id"]), "member_state_updated": update or {},
                            "member_state_removed": remove or [], "joined_at": member["joined_at"],
                            "updated_at": member["updated_at"]})


async def party_updated(party: dict, update: dict | None, remove: list | None):
    captain = Parties.captain(party) or {}
    cfg = party["config"]
    await _to_party(party, {"type": PARTY + "PARTY_UPDATED", "captain_id": captain.get("account_id"),
                            "party_state_updated": update or {}, "party_state_removed": remove or [],
                            "party_privacy_type": cfg.get("joinability"),
                            "max_number_of_members": cfg.get("max_size"), "party_sub_type": cfg.get("sub_type"),
                            "party_type": cfg.get("type"), "invite_ttl_seconds": cfg.get("invite_ttl"),
                            "intention_ttl_seconds": cfg.get("intention_ttl", 60),
                            "discoverability": cfg.get("discoverability"), "created_at": party["created_at"],
                            "updated_at": party["updated_at"], "party_state_overrides": {}})


async def member_requested_join(party: dict, account_id: str, meta: dict):
    """Join confirmation: the captain gets asked to approve."""
    captain = Parties.captain(party)
    if captain:
        await admin_message(captain["account_id"], {
            "type": PARTY + "MEMBER_REQUIRE_CONFIRMATION", "ns": "Fortnite", "party_id": party["id"],
            "account_id": account_id, "account_dn": _dn(account_id), "member_state_updated": meta or {},
            "joined_at": now_iso(), "updated_at": now_iso(), "revision": party["revision"], "sent": now_iso()})
