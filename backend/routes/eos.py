import time

from fastapi import APIRouter, Request

from ..utils import account_id_from_name, new_id, now_iso
from .auth import _expiry, account_from_request, make_jwt, read_jwt

router = APIRouter()

APP_ID = "fghi4567FNFBKFz3E4TROb0bmPS8h1GW"
DEPLOYMENT = "62a9473a2dca46b29ccf17577fcf42d7"


async def _account(request: Request) -> dict:
    """EOS logins carry the game's eg1 token or an exchange code."""
    form = await request.form()
    for key in ("exchange_code", "external_auth_token", "refresh_token"):
        claims = read_jwt(str(form.get(key, "")).replace("eg1~", ""))
        if claims and "sub" in claims:
            return {"accountId": claims["sub"], "displayName": claims.get("dn", claims["sub"])}
    acc = account_from_request(request)
    if acc:
        return acc
    from .auth import EXCHANGE
    name = EXCHANGE.get(str(form.get("exchange_code", "")), "Player")
    return {"accountId": account_id_from_name(name), "displayName": name}


CLIENT_ID = "ec684b8c687f479fadea3cb2ad83f5c6"


def _token(acc: dict, kind: str) -> str:
    now = int(time.time())
    claims = {"aud": CLIENT_ID, "sub": acc["accountId"], "t": "epic_id", "appid": APP_ID,
              "scope": "basic_profile friends_list openid presence", "dn": acc["displayName"],
              "pfpid": "prod-fn", "pfsid": "fn", "pfdid": DEPLOYMENT, "iat": now, "exp": now + 28800,
              "jti": new_id(), "iss": "https://api.epicgames.dev/epic/oauth/v1"}
    if kind.startswith("connect"):
        claims.update({"iss": "https://api.epicgames.dev/auth/v1/oauth", "sbid": "fn", "nonce": new_id(),
                       "act": {"eat": "epicgames_access_token", "eaid": acc["accountId"],
                               "pltfm": "other", "type": "user"}})
        claims.pop("scope")
    if kind.endswith("refresh"):
        claims["t"] = "epic_refresh"
    return make_jwt(claims)


# EOS account login
@router.post("/epic/oauth/v2/token")
async def eos_token(request: Request):
    acc = await _account(request)
    return {
        "scope": "basic_profile friends_list openid presence",
        "token_type": "bearer",
        "access_token": _token(acc, "epic_access"),
        "refresh_token": _token(acc, "epic_refresh"),
        "id_token": _token(acc, "epic_id"),
        "expires_in": 28800, "expires_at": _expiry(8),
        "refresh_expires_in": 86400, "refresh_expires_at": _expiry(24),
        "account_id": acc["accountId"], "client_id": CLIENT_ID,
        "application_id": APP_ID, "selected_account_id": acc["accountId"],
        "merged_accounts": [], "acr": "urn:epic:loa:aal1", "auth_time": now_iso(),
    }


@router.post("/epic/oauth/v2/tokenInfo")
async def eos_token_info(request: Request):
    form = await request.form()
    claims = read_jwt(str(form.get("token", ""))) or {}
    return {"active": True, "scope": "basic_profile friends_list openid presence", "token_type": "bearer",
            "expires_in": 28800, "expires_at": _expiry(8), "account_id": claims.get("sub", ""),
            "client_id": "ec684b8c687f479fadea3cb2ad83f5c6", "application_id": APP_ID}


FEATURES = ["AntiCheat", "Connect", "ContentService", "Ecom", "EpicConnect", "Inventories", "LockerService",
            "Matchmaking Service", "ExchangeCodeCreation", "Achievements", "Leaderboards", "Matchmaking", "Metrics",
            "PlayerReports", "Sanctions", "Stats", "TitleStorage", "Voice", "CommerceService", "FNResonanceService",
            "MagpieService", "PCBService", "QuestService"]


# EOS Connect login
@router.post("/auth/v1/oauth/token")
async def connect_token(request: Request):
    form = await request.form()
    if form.get("grant_type") == "client_credentials":
        now = int(time.time())
        token = make_jwt({"clientId": CLIENT_ID, "role": "GameClient", "productId": "prod-fn", "iss": "eos",
                          "env": "prod", "organizationId": "o-aa83a0a9bc45e98c80c1b1c9d92e9e",
                          "features": FEATURES, "deploymentId": DEPLOYMENT, "sandboxId": "fn",
                          "tokenType": "clientToken", "exp": now + 3600, "iat": now, "jti": new_id()})
        return {"access_token": token, "token_type": "bearer", "expires_at": _expiry(1), "expires_in": 3599,
                "features": FEATURES, "organization_id": "o-aa83a0a9bc45e98c80c1b1c9d92e9e",
                "product_id": "prod-fn", "sandbox_id": "fn", "deployment_id": DEPLOYMENT}
    acc = await _account(request)
    return {
        "access_token": _token(acc, "connect"),
        "token_type": "bearer", "expires_in": 3599, "expires_at": _expiry(1), "nonce": new_id(),
        "features": FEATURES,
        "organization_id": "o-aa83a0a9bc45e98c80c1b1c9d92e9e", "product_id": "prod-fn", "sandbox_id": "fn",
        "deployment_id": DEPLOYMENT, "organization_user_id": acc["accountId"],
        "product_user_id": acc["accountId"], "product_user_id_created": False,
        "id_token": _token(acc, "connect_id"),
    }


@router.get("/epic/id/v2/sso")
@router.get("/epic/id/v2/sso/")
async def sso():
    return {"sid": new_id()}


@router.delete("/epic/oauth/v2/sessions")
@router.delete("/epic/oauth/v2/sessions/{rest:path}")
async def eos_logout(rest: str = ""):
    return None


@router.get("/epic/id/v2/sdk/accounts")
async def sdk_accounts(request: Request):
    ids = request.query_params.getlist("accountId")
    return [{"accountId": i, "displayName": i, "preferredLanguage": "en", "linkedAccounts": [],
             "cabinedMode": False, "empty": False} for i in ids]


@router.post("/user/v9/product-users/search")
async def product_users(request: Request):
    body = await request.json()
    acc = account_from_request(request) or {}
    return {"productUsers": {pid: {"accounts": [{"accountId": acc.get("accountId", pid),
                                                 "displayName": acc.get("displayName", pid),
                                                 "identityProviderId": "epicgames", "lastLogin": now_iso()}]}
                             for pid in body.get("productUserIds", [])}}


@router.get("/epic/friends/v1/{account_id}/blocklist")
async def eos_blocklist(account_id: str):
    return {"blockedUsers": []}


@router.patch("/epic/presence/v1/{ns}/{account_id}/presence/{presence_id}")
async def eos_presence(ns: str, account_id: str, presence_id: str):
    return {"own": {"accountId": account_id, "status": "online", "perNs": []}, "connections": []}


@router.get("/epic/chat/v1/public/_/users/{account_id}/summary")
async def chat_summary(account_id: str):
    return {"conversationCount": 0, "unreadMessages": 0, "unreadPendingInvites": 0,
            "hasMoreUnreadMessages": False, "hasMoreUnreadPendingInvites": False}


@router.get("/epic/chat/v1/public/_/users/hybrid/conversations")
async def chat_conversations():
    return {"conversations": []}


@router.get("/epic/party/internal/v2/users/{account_id}")
async def eos_party(account_id: str):
    return {"invites": [], "join_requests": []}


@router.post("/datarouter/api/v1/public/data/clients")
async def datarouter_clients():
    return {}
