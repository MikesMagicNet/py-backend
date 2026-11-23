"""Friends, blocklist, search, presence and the party service."""
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import accounts, xmpp
from ..social import Friends, Parties
from ..utils import epic_error, now_iso
from .auth import account_from_request

router = APIRouter()
P = "/party/api/v1/{ns}"


def _caller(request: Request) -> str:
    return (account_from_request(request) or {}).get("accountId", "")


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, (dict, list)) else {}
    except Exception:
        return {}


def _no_account(account_id: str):
    return epic_error("errors.com.epicgames.account.account_not_found",
                      f"Sorry, we couldn't find an account for {account_id}", 404, 18007, [account_id])


# Friends

def _summary(account_id: str) -> dict:
    me = Friends.of(account_id)
    return {
        "friends": [{"accountId": f, "groups": [], "mutual": 0, "alias": "", "note": "", "favorite": False,
                     "created": c} for f, c in me["friends"].items()],
        "incoming": [{"accountId": f, "mutual": 0, "favorite": False, "created": c} for f, c in me["incoming"].items()],
        "outgoing": [{"accountId": f, "favorite": False, "created": c} for f, c in me["outgoing"].items()],
        "suggested": [],
        "blocklist": [{"accountId": b} for b in me["blocked"]],
        "settings": {"acceptInvites": "public", "mutualPrivacy": "ALL"},
        "limitsReached": {"incoming": False, "outgoing": False, "accepted": False},
    }


@router.get("/friends/api/v1/{account_id}/summary")
@router.get("/epic/friends/v1/{account_id}")
async def friends_summary(account_id: str):
    return _summary(account_id)


@router.get("/friends/api/v1/{account_id}/friends")
async def friends_v1(account_id: str):
    return _summary(account_id)["friends"]


@router.get("/friends/api/v1/{account_id}/incoming")
async def incoming_v1(account_id: str):
    return _summary(account_id)["incoming"]


@router.get("/friends/api/v1/{account_id}/outgoing")
async def outgoing_v1(account_id: str):
    return _summary(account_id)["outgoing"]


@router.get("/friends/api/v1/{account_id}/suggested")
@router.get("/friends/api/v1/{account_id}/recent/{ns}")
@router.get("/friends/api/public/list/fortnite/{account_id}/recentPlayers")
@router.get("/friends/api/public/list/fortnite/{account_id}/recentPlayers/")
@router.get("/friends/api/v1/{account_id}/friends/{friend_id}/mutual")
async def empty_friend_list(account_id: str, ns: str = "", friend_id: str = ""):
    return []


@router.get("/friends/api/v1/{account_id}/friends/{friend_id}")
async def friend_entry(account_id: str, friend_id: str):
    created = Friends.of(account_id)["friends"].get(friend_id)
    if not created:
        return epic_error("errors.com.epicgames.friends.friendship_not_found",
                          f"Friendship between {account_id} and {friend_id} does not exist", 404, 14004)
    return {"accountId": friend_id, "groups": [], "mutual": 0, "alias": "", "note": "", "favorite": False,
            "created": created}


@router.get("/friends/api/public/friends/{account_id}")
async def friends_public(account_id: str):
    me = Friends.of(account_id)
    return ([{"accountId": f, "status": "ACCEPTED", "direction": "INBOUND", "created": c, "favorite": False}
             for f, c in me["friends"].items()] +
            [{"accountId": f, "status": "PENDING", "direction": "INBOUND", "created": c, "favorite": False}
             for f, c in me["incoming"].items()] +
            [{"accountId": f, "status": "PENDING", "direction": "OUTBOUND", "created": c, "favorite": False}
             for f, c in me["outgoing"].items()])


@router.get("/friends/api/v1/{account_id}/settings")
@router.get("/friends/api/public/friends/{account_id}/settings")
async def friend_settings(account_id: str):
    return {"acceptInvites": "public", "mutualPrivacy": "ALL"}


@router.put("/friends/api/v1/{account_id}/settings")
async def friend_settings_put(account_id: str, request: Request):
    return {"acceptInvites": "public", "mutualPrivacy": "ALL", **(await _json(request))}


@router.post("/friends/api/v1/{account_id}/friends/{friend_id}")
@router.post("/friends/api/public/friends/{account_id}/{friend_id}")
@router.post("/epic/friends/v1/{account_id}/friends/{friend_id}")
async def friend_add(account_id: str, friend_id: str):
    if not accounts.get(friend_id):
        return _no_account(friend_id)
    result = Friends.add(account_id, friend_id)
    errors = {
        "self": ("errors.com.epicgames.friends.cannot_friend_due_to_target_settings",
                 "Cannot send a friend request to yourself", 14004),
        "blocked": ("errors.com.epicgames.friends.cannot_friend_due_to_target_settings",
                    "Cannot send a friend request to this account", 14004),
        "already_friends": ("errors.com.epicgames.friends.duplicate_friendship",
                            f"Friendship between {account_id} and {friend_id} already exists", 14009),
        "already_sent": ("errors.com.epicgames.friends.friend_request_already_sent",
                         f"Friend request from {account_id} to {friend_id} already sent", 14014),
    }
    if result in errors:
        code, msg, num = errors[result]
        return epic_error(code, msg, 409 if result.startswith("already") else 400, num, [friend_id])
    print(f"[Friends] {accounts.display_name(account_id)} -> {accounts.display_name(friend_id)}: {result}")
    await xmpp.friend_added(account_id, friend_id, result)
    return Response(status_code=204)


@router.delete("/friends/api/v1/{account_id}/friends/{friend_id}")
@router.delete("/friends/api/public/friends/{account_id}/{friend_id}")
@router.delete("/epic/friends/v1/{account_id}/friends/{friend_id}")
async def friend_remove(account_id: str, friend_id: str):
    kind = Friends.remove(account_id, friend_id)
    if not kind:
        return epic_error("errors.com.epicgames.friends.friendship_not_found",
                          f"Friendship between {account_id} and {friend_id} does not exist", 404, 14004)
    print(f"[Friends] {accounts.display_name(account_id)} removed {accounts.display_name(friend_id)} ({kind})")
    await xmpp.friend_removed(account_id, friend_id, kind)
    return Response(status_code=204)


@router.delete("/friends/api/v1/{account_id}/friends")
async def friend_remove_all(account_id: str):
    for friend_id in list(Friends.of(account_id)["friends"]):
        Friends.remove(account_id, friend_id)
        await xmpp.friend_removed(account_id, friend_id, "friends")
    return Response(status_code=204)


@router.put("/friends/api/v1/{account_id}/friends/{friend_id}/alias")
@router.put("/friends/api/v1/{account_id}/friends/{friend_id}/note")
@router.delete("/friends/api/v1/{account_id}/friends/{friend_id}/alias")
@router.delete("/friends/api/v1/{account_id}/friends/{friend_id}/note")
async def friend_alias(account_id: str, friend_id: str):
    return Response(status_code=204)


# Blocklist

@router.get("/friends/api/v1/{account_id}/blocklist")
@router.get("/friends/api/public/blocklist/{account_id}")
async def blocklist(account_id: str, request: Request):
    blocked = Friends.of(account_id)["blocked"]
    if "/public/" in request.url.path:
        return {"blockedUsers": blocked}
    return [{"accountId": b, "created": now_iso()} for b in blocked]


@router.get("/epic/friends/v1/{account_id}/blocklist")
async def eos_blocklist(account_id: str):
    return {"blockedUsers": Friends.of(account_id)["blocked"]}


@router.post("/friends/api/v1/{account_id}/blocklist/{target}")
@router.post("/friends/api/public/blocklist/{account_id}/{target}")
@router.post("/epic/friends/v1/{account_id}/blocklist/{target}")
async def block(account_id: str, target: str):
    kind = Friends.block(account_id, target)
    if kind:
        await xmpp.friend_removed(account_id, target, kind)
    await xmpp.block_changed(account_id, target, True)
    return Response(status_code=204)


@router.delete("/friends/api/v1/{account_id}/blocklist/{target}")
@router.delete("/friends/api/public/blocklist/{account_id}/{target}")
@router.delete("/epic/friends/v1/{account_id}/blocklist/{target}")
async def unblock(account_id: str, target: str):
    Friends.unblock(account_id, target)
    await xmpp.block_changed(account_id, target, False)
    return Response(status_code=204)


# Search, avatars, presence

@router.get("/api/v1/search/{account_id}")
async def user_search(account_id: str, request: Request):
    prefix = request.query_params.get("prefix", "").strip()
    if not prefix:
        return []
    return [{"accountId": a["accountId"],
             "matches": [{"value": a["displayName"], "platform": "epic"}],
             "matchType": "exact" if a["displayName"].lower() == prefix.lower() else "prefix",
             "epicMutuals": 0, "sortPosition": i}
            for i, a in enumerate(accounts.search(prefix, exclude=account_id))]


@router.get("/v1/avatar/{ns}/ids")
async def avatars(ns: str, request: Request):
    ids = [i for i in request.query_params.get("accountIds", "").split(",") if i]
    return [{"accountId": i, "namespace": ns, "avatarId": "ATHENACHARACTER:cid_001_athena_commando_f_default"}
            for i in ids]


@router.get("/presence/api/v1/_/{account_id}/last-online")
async def last_online(account_id: str):
    return {f: [{"last_online": now_iso() if xmpp.is_online(f) else c}]
            for f, c in Friends.of(account_id)["friends"].items()}


@router.get("/presence/api/v1/_/{account_id}/settings/subscriptions")
@router.get("/presence/api/v1/Fortnite/{account_id}/subscriptions/nudged")
async def presence_subs(account_id: str):
    return []


# Party service

def _party_error(party_id: str = ""):
    return epic_error("errors.com.epicgames.social.party.party_not_found",
                      f"Sorry, we couldn't find a party by id {party_id}", 404, 51002, [party_id])


def _member_error(account_id: str, party_id: str):
    return epic_error("errors.com.epicgames.social.party.member_not_found",
                      f"Sorry, we couldn't find a member {account_id} in party {party_id}", 404, 51004,
                      [account_id, party_id])


async def _leave(account_id: str, kicked_by: str | None = None):
    result = Parties.leave(account_id)
    if result:
        await xmpp.member_left(result, account_id, kicked_by)


@router.get(P + "/user/{account_id}")
async def party_user(ns: str, account_id: str):
    party = Parties.of_member(account_id)
    pending = [p for p in Parties.parties.values()
               if any(a["account_id"] == account_id for a in p["applicants"])]
    return {"current": [party] if party else [], "pending": pending,
            "invites": Parties.invites_for(account_id), "pings": Parties.pings_for(account_id)}


@router.get(P + "/user/{account_id}/settings/privacy")
async def party_privacy(ns: str, account_id: str):
    return {"receiveInvites": "ALL", "receiveIntentions": "ALL"}


@router.put(P + "/user/{account_id}/settings/privacy")
async def party_privacy_put(ns: str, account_id: str, request: Request):
    return {"receiveInvites": "ALL", "receiveIntentions": "ALL", **(await _json(request))}


@router.get(P + "/user/{account_id}/notifications/undelivered/count")
async def party_undelivered(ns: str, account_id: str):
    return {"pings": len(Parties.pings_for(account_id)), "invites": len(Parties.invites_for(account_id))}


@router.post(P + "/parties")
async def party_create(ns: str, request: Request):
    body = await _json(request)
    party, left = Parties.create(body)
    leader = party["members"][0]["account_id"]
    if left and left["party"]:
        await xmpp.member_left(left, leader)
    print(f"[Party] {accounts.display_name(leader)} created {party['id']}")
    return party


@router.get(P + "/parties/{party_id}")
async def party_get(ns: str, party_id: str):
    return Parties.get(party_id) or _party_error(party_id)


@router.patch(P + "/parties/{party_id}")
async def party_patch(ns: str, party_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    body = await _json(request)
    if body.get("config"):
        party["config"].update(body["config"])
    meta = body.get("meta") or {}
    Parties.update_meta(party, meta.get("update"), meta.get("delete"))
    await xmpp.party_updated(party, meta.get("update"), meta.get("delete"))
    return Response(status_code=204)


@router.delete(P + "/parties/{party_id}")
async def party_delete(ns: str, party_id: str):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    for m in list(party["members"]):
        await _leave(m["account_id"])
    return Response(status_code=204)


@router.patch(P + "/parties/{party_id}/members/{account_id}/meta")
async def member_meta(ns: str, party_id: str, account_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    body = await _json(request)
    member = Parties.update_member_meta(party, account_id, body.get("update"), body.get("delete"))
    if not member:
        return _member_error(account_id, party_id)
    await xmpp.member_updated(party, member, body.get("update"), body.get("delete"))
    return Response(status_code=204)


@router.post(P + "/parties/{party_id}/members/{account_id}/join")
@router.post(P + "/parties/{party_id}/members/{account_id}")
async def member_join(ns: str, party_id: str, account_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    body = await _json(request)
    existing = Parties.member(party, account_id)
    if existing:
        return {"status": "JOINED", "party_id": party_id}

    invited = any(i["sent_to"] == account_id for i in party["invites"])
    captain = (Parties.captain(party) or {}).get("account_id", "")
    if len(party["members"]) >= party["config"].get("max_size", 16):
        return epic_error("errors.com.epicgames.social.party.party_is_full",
                          f"Party {party_id} is full", 403, 51010, [party_id])
    if party["config"].get("join_confirmation") and not invited:
        party["applicants"] = [a for a in party["applicants"] if a["account_id"] != account_id]
        party["applicants"].append({"account_id": account_id, "connection": body.get("connection"),
                                    "meta": body.get("meta", {}), "requested_at": now_iso()})
        await xmpp.member_requested_join(party, account_id, body.get("meta", {}))
        return {"status": "PENDING_CONFIRMATION", "party_id": party_id}

    await _join(party, account_id, body.get("connection"), body.get("meta"))
    Parties.clear_ping(account_id, captain)
    return {"status": "JOINED", "party_id": party_id}


async def _join(party: dict, account_id: str, connection: dict | None, meta: dict | None):
    old = Parties.of_member(account_id)
    if old and old["id"] != party["id"]:
        await _leave(account_id)
    member = Parties.add_member(party, account_id, connection, meta)
    party["applicants"] = [a for a in party["applicants"] if a["account_id"] != account_id]
    for m in party["members"]:
        Parties.clear_ping(account_id, m["account_id"])
        Parties.clear_intention(m["account_id"], account_id)
    print(f"[Party] {accounts.display_name(account_id)} joined {party['id']}")
    await xmpp.member_joined(party, member)


@router.post(P + "/parties/{party_id}/members/{account_id}/confirm")
async def member_confirm(ns: str, party_id: str, account_id: str):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    applicant = next((a for a in party["applicants"] if a["account_id"] == account_id), None)
    if not applicant:
        return _member_error(account_id, party_id)
    await _join(party, account_id, applicant.get("connection"), applicant.get("meta"))
    return Response(status_code=204)


@router.post(P + "/parties/{party_id}/members/{account_id}/reject")
async def member_reject(ns: str, party_id: str, account_id: str):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    party["applicants"] = [a for a in party["applicants"] if a["account_id"] != account_id]
    await xmpp.admin_message(account_id, {"type": xmpp.PARTY + "MEMBER_REJECTED", "ns": "Fortnite",
                                          "party_id": party_id, "account_id": account_id, "sent": now_iso()})
    return Response(status_code=204)


@router.delete(P + "/parties/{party_id}/members/{account_id}")
async def member_leave(ns: str, party_id: str, account_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    if not Parties.member(party, account_id):
        return _member_error(account_id, party_id)
    caller = _caller(request)
    kicked_by = caller if caller and caller != account_id else None
    await _leave(account_id, kicked_by)
    return Response(status_code=204)


@router.post(P + "/parties/{party_id}/members/{account_id}/promote")
async def member_promote(ns: str, party_id: str, account_id: str):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    if not Parties.promote(party, account_id):
        return _member_error(account_id, party_id)
    await xmpp.new_captain(party, account_id)
    return Response(status_code=204)


@router.post(P + "/parties/{party_id}/members/{account_id}/conferences/connection")
async def voice_connection(ns: str, party_id: str, account_id: str):
    return {"providers": {}}


@router.post(P + "/parties/{party_id}/invites/{account_id}")
async def invite(ns: str, party_id: str, account_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    sent_by = _caller(request) or (Parties.captain(party) or {}).get("account_id", "")
    inv = Parties.invite(party, sent_by, account_id, await _json(request))
    print(f"[Party] {accounts.display_name(sent_by)} invited {accounts.display_name(account_id)}")
    await xmpp.party_invite(party, inv)
    if request.query_params.get("sendPing", "").lower() == "true":
        ping = Parties.ping(sent_by, account_id)
        await xmpp.party_ping(sent_by, account_id, ping)
    return Response(status_code=204)


@router.delete(P + "/parties/{party_id}/invites/{account_id}")
async def invite_cancel(ns: str, party_id: str, account_id: str, request: Request):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    Parties.drop_invite(party, account_id)
    await xmpp.party_invite_cancelled(party, account_id, _caller(request))
    return Response(status_code=204)


@router.post(P + "/parties/{party_id}/invites/{account_id}/decline")
async def invite_decline(ns: str, party_id: str, account_id: str):
    party = Parties.get(party_id)
    if not party:
        return _party_error(party_id)
    inv = next((i for i in party["invites"] if i["sent_to"] == account_id), None)
    Parties.drop_invite(party, account_id)
    if inv:
        await xmpp.party_invite_declined(party, account_id, inv["sent_by"])
    return Response(status_code=204)


# Pings: "invite me" from a friend, and the friend's party lookup

@router.post(P + "/user/{account_id}/pings/{pinger_id}")
async def ping(ns: str, account_id: str, pinger_id: str, request: Request):
    entry = Parties.ping(pinger_id, account_id, await _json(request))
    await xmpp.party_ping(pinger_id, account_id, entry)
    return entry


@router.delete(P + "/user/{account_id}/pings/{pinger_id}")
async def ping_delete(ns: str, account_id: str, pinger_id: str):
    Parties.clear_ping(account_id, pinger_id)
    return Response(status_code=204)


@router.get(P + "/user/{account_id}/pings/{pinger_id}/parties")
async def ping_parties(ns: str, account_id: str, pinger_id: str):
    party = Parties.of_member(pinger_id)
    return [party] if party else []


@router.post(P + "/user/{account_id}/pings/{pinger_id}/join")
async def ping_join(ns: str, account_id: str, pinger_id: str, request: Request):
    party = Parties.of_member(pinger_id)
    if not party:
        return _party_error()
    body = await _json(request)
    await _join(party, account_id, body.get("connection"), body.get("meta"))
    return {"status": "JOINED", "party_id": party["id"]}


# Request to join: requester asks a friend to be let into their party

@router.post(P + "/members/{first}/intentions/{second}")
async def intention(ns: str, first: str, second: str, request: Request):
    caller = _caller(request)
    # Path order differs between builds, the token says who is asking
    requester, requestee = (first, second) if caller == first else (second, first)
    if not Friends.are_friends(requester, requestee):
        return epic_error("errors.com.epicgames.social.party.intention_forbidden",
                          "You can only request to join friends", 403, 51024)
    entry = Parties.intention(requester, requestee, await _json(request))
    print(f"[Party] {accounts.display_name(requester)} asked to join {accounts.display_name(requestee)}")
    await xmpp.party_intention(entry)
    return {**entry, "requester_dn": accounts.display_name(requester),
            "requestee_dn": accounts.display_name(requestee)}


@router.get(P + "/members/{account_id}/intentions")
async def intentions_list(ns: str, account_id: str):
    return Parties.intentions.get(account_id, [])

