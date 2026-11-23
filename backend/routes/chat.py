"""EOS text chat (TextChatClientFN): whispers, DMs, group and party conversations."""
import json
import os
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import accounts, stomp
from ..social import Friends
from ..utils import DATA, ROOT, epic_error, now_iso
from .auth import account_from_request

router = APIRouter()
C = "/epic/chat/v1/public/{ns}"
FILE = DATA / "chat.json"
LOG = open(ROOT / "chat.log", "a", buffering=1, encoding="utf-8")
KEEP = 200  # messages kept per conversation

_store = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {"conversations": {}, "read": {}}
conversations: dict[str, dict] = _store["conversations"]
last_read: dict[str, dict] = _store["read"]   # accountId -> conversationId -> msgId


def _save():
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_store), encoding="utf-8")
    os.replace(tmp, FILE)


def _dm_id(a: str, b: str) -> str:
    return "dm-" + uuid.uuid5(uuid.NAMESPACE_OID, ":".join(sorted((a, b)))).hex


def _not_found(cid: str):
    return epic_error("errors.com.epicgames.social.chat.conversation_not_found",
                      f"Conversation {cid} not found", 404, 0, [cid])


def _view(conv: dict, viewer: str, last: bool = True) -> dict:
    msgs = conv["messages"]
    read = last_read.get(viewer, {}).get(conv["conversationId"])
    ids = [m["msgId"] for m in msgs]
    after = msgs[ids.index(read) + 1:] if read in ids else msgs
    unread = sum(1 for m in after if m["senderId"] != viewer)
    members = [{"accountId": m, "joinedAt": conv["createdAt"], "isInvited": False} for m in conv["members"]]
    out = {"conversationId": conv["conversationId"], "type": conv["type"], "conversationType": conv["type"],
           "title": conv.get("title", ""), "members": members, "isActive": True, "isReportable": False,
           "isE2EE": False, "createdAt": conv["createdAt"], "conversationCreatedAt": conv["createdAt"],
           "lastActivity": msgs[-1]["sentAt"] if msgs else conv["createdAt"], "lastActivityId":
           msgs[-1]["msgId"] if msgs else "", "unreadCount": unread, "lastReadMsgId": read or "",
           "parentConversationId": conv.get("parentConversationId", ""), "allowedRecipients": [],
           "platformOnlyFriends": False}
    if last and msgs:
        out["lastMessage"] = msgs[-1]
    return out


def _caller(request: Request, fallback: str = "") -> str:
    return (account_from_request(request) or {}).get("accountId", "") or fallback


async def _body(request: Request) -> dict:
    raw = await request.body()
    LOG.write(f"{now_iso()} {request.method} {request.url.path}?{request.url.query} {raw[:1500]!r}\n")
    try:
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _text(body: dict) -> str:
    msg = body.get("message") if isinstance(body.get("message"), dict) else body
    return str(msg.get("body") or msg.get("text") or msg.get("messageBody") or "")


def _get_dm(a: str, b: str) -> dict:
    cid = _dm_id(a, b)
    if cid not in conversations:
        conversations[cid] = {"conversationId": cid, "type": "dm", "members": sorted([a, b]),
                              "createdAt": now_iso(), "messages": []}
        _save()
    return conversations[cid]


async def _post(conv: dict, sender: str, body: dict, kind: str) -> dict:
    text = _text(body)
    msg = {"msgId": uuid.uuid4().hex, "id": None, "senderId": sender, "body": text, "text": text,
           "sentAt": now_iso(), "conversationId": conv["conversationId"],
           "sourceMsgId": (body.get("message") or {}).get("sourceMsgId", "") if isinstance(body.get("message"), dict)
           else body.get("sourceMsgId", ""), "isReportable": False, "metadata": body.get("metadata", {})}
    msg["id"] = msg["msgId"]
    conv["messages"] = (conv["messages"] + [msg])[-KEEP:]
    last_read.setdefault(sender, {})[conv["conversationId"]] = msg["msgId"]
    _save()
    print(f"[Chat] {accounts.display_name(sender)} -> {conv['conversationId']}: {text[:60]}")
    for member in conv["members"]:
        if member == sender:
            continue
        payload = {"conversation": _view(conv, member, last=False), "message": msg,
                   "conversationId": conv["conversationId"], "conversationType": conv["type"],
                   "senderId": sender, "msgId": msg["msgId"], "body": text, "text": text,
                   "sentAt": msg["sentAt"], "fromAccountId": sender, "toAccountId": member}
        await stomp.push(member, kind, payload)
    return msg


# Summary and lists

@router.get(C + "/users/{account_id}/summary")
async def summary(ns: str, account_id: str):
    mine = [_view(c, account_id) for c in conversations.values() if account_id in c["members"]]
    unread = sum(v["unreadCount"] for v in mine)
    return {"conversationCount": len(mine), "unreadMessages": unread, "totalUnreadMessages": unread,
            "unreadPendingInvites": 0, "totalPendingInvites": 0, "hasMoreUnreadMessages": False,
            "hasMoreUnreadPendingInvites": False, "hasMorePendingInvites": False}


@router.get(C + "/users/{account_id}/conversations")
async def user_conversations(ns: str, account_id: str, request: Request):
    types = [t for t in request.query_params.get("conversationType", "").split(",") if t]
    include_last = request.query_params.get("includeLastMessage", "true") != "false"
    mine = [c for c in conversations.values()
            if account_id in c["members"] and (not types or c["type"] in types) and c["messages"]]
    mine.sort(key=lambda c: c["messages"][-1]["sentAt"], reverse=True)
    limit = int(request.query_params.get("limit", "50") or 50)
    return {"conversations": [_view(c, account_id, include_last) for c in mine[:limit]],
            "nextPageToken": None, "hasNextPage": False}


@router.get(C + "/users/{account_id}/conversations/dm/{to_id}")
async def user_dm(ns: str, account_id: str, to_id: str):
    return _view(_get_dm(account_id, to_id), account_id)


@router.get(C + "/users/{account_id}/conversations/{cid}")
async def user_conversation(ns: str, account_id: str, cid: str):
    conv = conversations.get(cid)
    return _view(conv, account_id) if conv and account_id in conv["members"] else _not_found(cid)


@router.put(C + "/users/{account_id}/conversations/{cid}")
@router.patch(C + "/users/{account_id}/conversations/{cid}")
async def update_last_read(ns: str, account_id: str, cid: str, request: Request):
    body = await _body(request)
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    msg_id = body.get("lastReadMsgId") or body.get("msgId") or (conv["messages"][-1]["msgId"] if conv["messages"] else "")
    last_read.setdefault(account_id, {})[cid] = msg_id
    _save()
    return _view(conv, account_id)


# Direct messages

@router.get(C + "/conversations/dm/{from_id}/{to_id}")
@router.get(C + "/dm/{from_id}/{to_id}")
async def dm_details(ns: str, from_id: str, to_id: str):
    return _view(_get_dm(from_id, to_id), from_id)


@router.post(C + "/whisper/{from_id}/{to_id}")
@router.post(C + "/dm/{from_id}/{to_id}")
@router.post(C + "/conversations/dm/{from_id}/{to_id}")
async def whisper(ns: str, from_id: str, to_id: str, request: Request):
    body = await _body(request)
    if not accounts.get(to_id):
        return epic_error("errors.com.epicgames.social.chat.member_not_found", "Recipient not found", 404)
    if to_id in Friends.of(from_id)["blocked"] or from_id in Friends.of(to_id)["blocked"]:
        return epic_error("errors.com.epicgames.social.chat.recipient_restricted", "Recipient restricted", 403)
    if not Friends.are_friends(from_id, to_id):
        return epic_error("errors.com.epicgames.social.chat.not_friends_restricted", "Not friends", 403)
    msg = await _post(_get_dm(from_id, to_id), from_id, body, "social.chat.v1.NEW_WHISPER")
    return {"msgId": msg["msgId"], "id": msg["msgId"], "sentAt": msg["sentAt"],
            "conversationId": msg["conversationId"], "allowedRecipients": [to_id]}


# Group and party conversations

@router.post(C + "/conversations")
async def create_conversation(ns: str, request: Request):
    body = await _body(request)
    creator = _caller(request)
    raw = body.get("members") or body.get("memberIds") or []
    members = {m.get("accountId") if isinstance(m, dict) else m for m in raw} | ({creator} if creator else set())
    members.discard(None)
    kind = body.get("type") or body.get("conversationType") or "group"
    cid = body.get("conversationId") or f"{kind}-{uuid.uuid4().hex}"
    conv = conversations.get(cid)
    if not conv:
        conv = {"conversationId": cid, "type": kind, "members": sorted(members), "createdAt": now_iso(),
                "title": body.get("title", ""), "messages": [],
                "parentConversationId": body.get("parentConversationId", "")}
        conversations[cid] = conv
        _save()
        for m in conv["members"]:
            if m != creator:
                await stomp.push(m, "social.chat.v1.CONVERSATION_CREATED",
                                 {"conversation": _view(conv, m), "conversationId": cid, "createdBy": creator})
    return _view(conv, creator)


@router.get(C + "/conversations/{cid}")
async def conversation(ns: str, cid: str, request: Request):
    conv = conversations.get(cid)
    return _view(conv, _caller(request)) if conv else _not_found(cid)


@router.patch(C + "/conversations/{cid}")
@router.put(C + "/conversations/{cid}")
async def update_conversation(ns: str, cid: str, request: Request):
    body = await _body(request)
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    if "title" in body:
        old, conv["title"] = conv.get("title", ""), body["title"]
        _save()
        for m in conv["members"]:
            await stomp.push(m, "social.chat.v1.TITLE_CHANGE", {"conversationId": cid, "title": conv["title"],
                                                                 "previousTitle": old})
    return _view(conv, _caller(request))


@router.get(C + "/conversations/{cid}/messages")
async def messages(ns: str, cid: str, request: Request):
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    limit = int(request.query_params.get("limit", "50") or 50)
    msgs = conv["messages"]
    before = request.query_params.get("before") or request.query_params.get("beforeMs")
    if before:
        msgs = [m for m in msgs if m["sentAt"] < before]
    return {"messages": msgs[-limit:], "nextPageToken": None, "hasNextPage": len(msgs) > limit}


@router.post(C + "/conversations/{cid}/messages")
async def send_message(ns: str, cid: str, request: Request):
    body = await _body(request)
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    sender = _caller(request) or request.query_params.get("fromAccountId", "")
    if sender not in conv["members"]:
        return epic_error("errors.com.epicgames.social.chat.non_member_restricted", "Not a member", 403)
    msg = await _post(conv, sender, body, "social.chat.v1.NEW_MESSAGE")
    return {"msgId": msg["msgId"], "id": msg["msgId"], "sentAt": msg["sentAt"], "conversationId": cid,
            "allowedRecipients": [m for m in conv["members"] if m != sender]}


@router.get(C + "/conversations/{cid}/reportable-messages")
async def reportable(ns: str, cid: str):
    return {"messages": []}


@router.post(C + "/conversations/{cid}/members")
async def add_members(ns: str, cid: str, request: Request):
    body = await _body(request)
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    raw = body.get("members") or body.get("memberIds") or body.get("accountIds") or []
    added = [m.get("accountId") if isinstance(m, dict) else m for m in raw]
    added = [m for m in added if m and m not in conv["members"]]
    conv["members"] = sorted(set(conv["members"]) | set(added))
    _save()
    for m in conv["members"]:
        await stomp.push(m, "social.chat.v1.MEMBERS_JOIN", {"conversationId": cid, "membersJoined": added,
                                                             "conversation": _view(conv, m, last=False)})
    return {"added": added, "notAdded": []}


@router.delete(C + "/conversations/{cid}/members")
@router.delete(C + "/conversations/{cid}/members/{account_id}")
async def remove_members(ns: str, cid: str, request: Request, account_id: str = ""):
    body = await _body(request)
    conv = conversations.get(cid)
    if not conv:
        return _not_found(cid)
    gone = [account_id] if account_id else [m.get("accountId") if isinstance(m, dict) else m
                                             for m in (body.get("members") or body.get("accountIds") or [])]
    notify = list(conv["members"])
    conv["members"] = [m for m in conv["members"] if m not in gone]
    _save()
    for m in notify:
        await stomp.push(m, "social.chat.v1.MEMBERS_LEFT", {"conversationId": cid, "members": gone})
    return Response(status_code=204)


@router.api_route("/epic/chat/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def chat_unknown(rest: str, request: Request):
    # Logged so missing chat calls show up in chat.log
    await _body(request)
    print(f"[Chat] unhandled {request.method} /epic/chat/{rest}")
    return {}
