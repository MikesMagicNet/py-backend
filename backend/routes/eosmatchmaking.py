"""EOS matchmaking v2 (newer builds): HTTP ticket calls, updates pushed over Epic Connect."""
import asyncio
import re
import json

from fastapi import APIRouter, Request

from .. import matchmaker as mm, stomp
from ..utils import ROOT, new_id
from ..log import log, user
from .. import accounts

router = APIRouter()
PREFIX = "/epic/matchmaking/v2/{deployment}/ticket"
KEY = "/Fortnite.com/Matchmaking:"
LOG = open(ROOT / "matchmaking.log", "a", buffering=1, encoding="utf-8")


def _log(text: str):
    log("MMSv2", f"{text}")
    LOG.write(re.sub(r"\x1b\[[0-9;]*m", "", text) + "\n")


async def _push(account: str, kind: str, payload: dict):
    if not stomp.online(account):
        _log(f"{accounts.cname(account)} has no Epic Connect socket, {kind} not delivered (set epicConnect true)")
        return
    _log(f"-> {accounts.cname(account)} {kind} {json.dumps(payload)[:300]}")
    await stomp.push(account, kind, payload)


async def _assigned(t: mm.Ticket):
    s = t.server
    address = f"{s.ip}:{s.port}"
    # The game reads the connection from session.attributes; the key it wants isn't confirmed yet
    attributes = {
        "serverAddress": s.ip, "serverPort": s.port, "ConnectionString": address, "connectionString": address,
        "ADDRESS_s": s.ip, "PORT_l": s.port, "SERVERADDRESS_s": s.ip, "SERVERPORT_l": s.port,
        "sessionId": s.id, "SESSIONID_s": s.id, "PLAYLISTNAME_s": s.playlist, "REGION_s": s.region,
    }
    # bucketId is a JSON object to the SDK, a plain string fails the parse
    bucket = {KEY + "LinkCode": t.playlist or s.playlist, KEY + "Region": t.region or s.region}
    await asyncio.sleep(1)  # let the join reply land first
    payload = {"ticketId": t.id, "bucketId": bucket,
               "match": {"matchId": new_id(), "playerAttributes": {}},
               "session": {"id": s.id, "attributes": attributes}}  # id is required by the SDK
    for account in t.party:
        await _push(account, "mms/v2.matchmaking_status/v1", {"ticketId": t.id, "stage": "SessionAssignment"})
        await _push(account, "mms/v2.player_match_assignment/v1", payload)


async def _queue_updates(t: mm.Ticket):
    while not t.assigned.is_set() and t in mm.QUEUE:
        for account in t.party:
            await _push(account, "mms/v2.queue_status/v1",
                        {"ticketId": t.id, "stats": {"size": mm.queued_for(t.playlist)}, "allowedActions": []})
        await asyncio.sleep(5)


@router.post(PREFIX + "/join")
async def join(deployment: str, request: Request):
    body = await request.json()
    attrs = body.get("attributes", {})
    ticket_id = request.query_params.get("ticketId") or new_id()
    account = body.get("playerId", "")
    playlist = str(attrs.get(KEY + "LinkCode", "")).lower()
    region = str(attrs.get(KEY + "Region", "")).upper()
    t = mm.Ticket(account, playlist, region, body.get("partyMembers"), ticket_id, _assigned)
    mm.enqueue(t)
    _log(f"{accounts.cname(account)} joined {ticket_id[:8]} for {playlist} in {region} ({len(t.party)} players)")

    async def started():
        await asyncio.sleep(0.5)
        if t.assigned.is_set():
            return
        for a in t.party:
            await _push(a, "mms/v2.matchmaking_status/v1", {"ticketId": t.id, "stage": "Queued"})
        await _queue_updates(t)
    asyncio.create_task(started())
    return {"ticketId": ticket_id, "ticketState": "MATCHMAKING"}


@router.api_route(PREFIX + "/{ticket_id}/heartbeat", methods=["PUT", "POST"])
async def heartbeat(deployment: str, ticket_id: str):
    t = mm.find(ticket_id)
    return {"ticketId": ticket_id, "ticketState": "MATCHMAKING" if t else "MATCH_ASSIGNED"}


@router.api_route(PREFIX + "/{ticket_id}/close", methods=["PUT", "POST", "DELETE"])
async def close(deployment: str, ticket_id: str):
    t = mm.find(ticket_id)
    if t:
        mm.leave(t)
        _log(f"{accounts.cname(t.account_id)} closed {ticket_id[:8]}")
    return {"ticketId": ticket_id}


@router.api_route(PREFIX + "/{ticket_id}/pauseQueue", methods=["PUT", "POST"])
@router.api_route(PREFIX + "/{ticket_id}/resumeQueue", methods=["PUT", "POST"])
@router.api_route(PREFIX + "/{ticket_id}/popQueue", methods=["PUT", "POST"])
async def queue_op(deployment: str, ticket_id: str):
    return {"ticketId": ticket_id}
