import asyncio
import base64
import json

from fastapi import APIRouter, Request, WebSocket

from ..utils import CONFIG, new_id

router = APIRouter()


@router.get("/fortnite/api/game/v2/matchmakingservice/ticket/player/{account_id}")
async def ticket(account_id: str, request: Request):
    host = request.headers.get("host", f"127.0.0.1:{CONFIG['port']}")
    payload = base64.b64encode(json.dumps({"playerId": account_id}).encode()).decode()
    return {"serviceUrl": f"ws://{host}/matchmaker", "ticketType": "mms-player",
            "payload": payload, "signature": "halcyon-dev"}


@router.get("/fortnite/api/game/v2/matchmaking/account/{account_id}/session/{session_id}")
async def session_account(account_id: str, session_id: str):
    return {"accountId": account_id, "sessionId": session_id, "key": "none"}


@router.get("/fortnite/api/matchmaking/session/{session_id}")
async def session(session_id: str):
    gs = CONFIG["gameServer"]
    return {
        "id": session_id, "ownerId": new_id().upper(), "ownerName": "[DS]halcyon-dev", "serverName": "[DS]halcyon-dev",
        "serverAddress": gs["ip"], "serverPort": gs["port"], "maxPublicPlayers": 100, "openPublicPlayers": 100,
        "maxPrivatePlayers": 0, "openPrivatePlayers": 0, "attributes": {}, "publicPlayers": [], "privatePlayers": [],
        "totalPlayers": 0, "allowJoinInProgress": False, "shouldAdvertise": False, "isDedicated": False,
        "usesStats": False, "allowInvites": False, "usesPresence": False, "allowJoinViaPresence": True,
        "allowJoinViaPresenceFriendsOnly": False, "buildUniqueId": "0", "lastUpdated": "2020-01-01T00:00:00.000Z",
        "started": False,
    }


@router.post("/fortnite/api/matchmaking/session/{session_id}/join")
async def session_join(session_id: str):
    return None


@router.post("/fortnite/api/matchmaking/session/matchMakingRequest")
async def mm_request():
    return []


@router.websocket("/matchmaker")
async def matchmaker(ws: WebSocket):
    await ws.accept()
    ticket_id, match_id, session_id = new_id(), new_id(), new_id()
    steps = [
        {"payload": {"state": "Connecting"}, "name": "StatusUpdate"},
        {"payload": {"totalPlayers": 1, "connectedPlayers": 1, "state": "Waiting"}, "name": "StatusUpdate"},
        {"payload": {"ticketId": ticket_id, "queuedPlayers": 0, "estimatedWaitSec": 0, "status": {},
                     "state": "Queued"}, "name": "StatusUpdate"},
        {"payload": {"matchId": match_id, "state": "SessionAssignment"}, "name": "StatusUpdate"},
        {"payload": {"matchId": match_id, "sessionId": session_id, "joinDelaySec": 1}, "name": "Play"},
    ]
    try:
        for s in steps:
            await ws.send_text(json.dumps(s))
            await asyncio.sleep(0.5)
        while True:
            await ws.receive_text()
    except Exception:
        pass
