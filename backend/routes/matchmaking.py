import asyncio
import base64
import json

from fastapi import APIRouter, Request, WebSocket

from .. import matchmaker as mm
from ..utils import CONFIG, epic_error, new_id
from ..log import log, user
from .. import accounts

router = APIRouter()


def _playlist_from_bucket(bucket: str) -> tuple[str, str]:
    # buildUniqueId:0:region:playlist
    parts = bucket.split(":")
    region = parts[2] if len(parts) > 2 else ""
    playlist = parts[3] if len(parts) > 3 else ""
    return playlist.lower(), region.upper()


@router.get("/fortnite/api/game/v2/matchmakingservice/ticket/player/{account_id}")
async def ticket(account_id: str, request: Request):
    q = request.query_params
    playlist, region = _playlist_from_bucket(q.get("bucketId", ""))
    party = [p for p in q.get("partyPlayerIds", "").split(",") if p] or [account_id]
    host = request.headers.get("host", f"127.0.0.1:{CONFIG['port']}")
    data = {"playerId": account_id, "playlist": playlist, "region": region, "party": party}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"serviceUrl": f"ws://{host}/matchmaker", "ticketType": "mms-player",
            "payload": payload, "signature": "halcyon-dev"}


@router.get("/fortnite/api/game/v2/matchmaking/account/{account_id}/session/{session_id}")
async def session_account(account_id: str, session_id: str):
    return {"accountId": account_id, "sessionId": session_id, "key": "none"}


@router.get("/fortnite/api/matchmaking/session/{session_id}")
async def session(session_id: str):
    server = mm.SESSIONS.get(session_id)
    if server:
        ip, port, playlist, region, max_players = server.ip, server.port, server.playlist, server.region, server.max_players
    else:
        # No PoloniumV3 server behind this id: the fixed one from config.json
        gs = CONFIG["gameServer"]
        ip, port, playlist, region, max_players = gs["ip"], gs["port"], "", "", 100
    return {
        "id": session_id, "ownerId": new_id().upper(), "ownerName": "[DS]polonium", "serverName": "[DS]polonium",
        "serverAddress": ip, "serverPort": port, "maxPublicPlayers": max_players, "openPublicPlayers": max_players,
        "maxPrivatePlayers": 0, "openPrivatePlayers": 0,
        "attributes": {"PLAYLISTNAME_s": playlist, "REGION_s": region},
        "publicPlayers": [], "privatePlayers": [],
        "totalPlayers": server.players if server else 0, "allowJoinInProgress": False, "shouldAdvertise": False,
        "isDedicated": False, "usesStats": False, "allowInvites": False, "usesPresence": False,
        "allowJoinViaPresence": True, "allowJoinViaPresenceFriendsOnly": False, "buildUniqueId": "0",
        "lastUpdated": "2020-01-01T00:00:00.000Z", "started": False,
    }


@router.post("/fortnite/api/matchmaking/session/{session_id}/join")
async def session_join(session_id: str):
    return None


@router.post("/fortnite/api/matchmaking/session/matchMakingRequest")
async def mm_request():
    return []


# PoloniumV3 game servers report in here
@router.post("/polonium/server/heartbeat")
async def server_heartbeat(request: Request):
    try:
        body = await request.json()
    except Exception:
        return epic_error("errors.com.epicgames.common.json_parse", "Bad heartbeat body")
    return mm.heartbeat(body).info()


@router.post("/polonium/server/{server_id}/close")
async def server_close(server_id: str):
    mm.remove(server_id)
    return None


@router.get("/polonium/servers")
async def servers():
    return {"servers": [s.info() for s in mm.SERVERS.values()],
            "queue": [{"accountId": t.account_id, "playlist": t.playlist, "party": t.party} for t in mm.QUEUE]}


def _read_ticket(ws: WebSocket) -> dict:
    # Authorization: Epic-Signed mms-player <payload> <signature>
    parts = ws.headers.get("authorization", "").split(" ")
    try:
        return json.loads(base64.b64decode(parts[2]))
    except Exception:
        return {}


async def _send(ws: WebSocket, name: str, payload: dict):
    await ws.send_text(json.dumps({"payload": payload, "name": name}))


async def _drain(ws: WebSocket):
    while True:
        await ws.receive_text()


@router.websocket("/matchmaker")
async def matchmaker(ws: WebSocket):
    await ws.accept()
    data = _read_ticket(ws)
    account = data.get("playerId", new_id())
    t = mm.Ticket(account, data.get("playlist", ""), data.get("region", ""), data.get("party"))
    closed = assigned = None
    try:
        await _send(ws, "StatusUpdate", {"state": "Connecting"})
        await _send(ws, "StatusUpdate", {"totalPlayers": len(t.party), "connectedPlayers": len(t.party),
                                         "state": "Waiting"})
        mm.enqueue(t)
        log("Matchmaker", f"{accounts.cname(account)} queued for {t.playlist or 'any'} ({len(t.party)} players)")

        # The socket closing means the player cancelled
        closed = asyncio.create_task(_drain(ws))
        assigned = asyncio.create_task(t.assigned.wait())
        while not assigned.done():
            await _send(ws, "StatusUpdate", {"ticketId": t.id, "queuedPlayers": mm.queued_for(t.playlist),
                                             "estimatedWaitSec": 0, "status": {}, "state": "Queued"})
            await asyncio.wait({closed, assigned}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
            if closed.done():
                return

        match_id = new_id()
        await _send(ws, "StatusUpdate", {"matchId": match_id, "state": "SessionAssignment"})
        await asyncio.sleep(1)
        await _send(ws, "Play", {"matchId": match_id, "sessionId": t.server.id, "joinDelaySec": 1})
        await closed
    except Exception:
        pass
    finally:
        for task in (closed, assigned):
            if task:
                task.cancel()
        if not t.assigned.is_set():
            mm.leave(t)
            log("Matchmaker", f"{accounts.cname(account)} left the queue")
