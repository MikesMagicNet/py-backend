import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import accounts, presence, stomp
from ..utils import DATA, new_id, now_iso
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
    acc = accounts.ensure(EXCHANGE.get(str(form.get("exchange_code", "")), "Player"))
    return {"accountId": acc["accountId"], "displayName": acc["displayName"]}


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


# No AntiCheat: with it the SDK expects EAC, and queueing without it ends in the "corrupted data" popup
FEATURES = ["Connect", "ContentService", "Ecom", "EpicConnect", "Inventories", "LockerService",
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
    return [{"accountId": i, "displayName": accounts.display_name(i), "preferredLanguage": "en", "linkedAccounts": [],
             "cabinedMode": False, "empty": False} for i in ids]


@router.post("/user/v9/product-users/search")
async def product_users(request: Request):
    # Product user ids are account ids here
    body = await request.json()
    return {"productUsers": {pid: {"accounts": [{"accountId": pid, "displayName": accounts.display_name(pid),
                                                 "identityProviderId": "epicgames", "lastLogin": now_iso()}]}
                             for pid in body.get("productUserIds", [])}}


def _conn_id(presence_id: str) -> str:
    # "<connectionId>#sub-eas-..." (the # arrives url-decoded)
    return presence_id.split("#")[0].split("%23")[0]


@router.patch("/epic/presence/v1/{ns}/{account_id}/presence/{presence_id}")
async def eos_presence(ns: str, account_id: str, presence_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    cid = _conn_id(presence_id)
    stomp.bind(cid, account_id)
    view, came_online = presence.set_presence(account_id, ns, cid, body if isinstance(body, dict) else {})
    await presence.broadcast(account_id)
    if came_online:
        await presence.send_friends_to(account_id)
    return {"own": view, "connections": [{"id": cid, "props": {}}]}


@router.delete("/epic/presence/v1/{ns}/{account_id}/presence/{presence_id}")
async def eos_presence_delete(ns: str, account_id: str, presence_id: str):
    presence.clear(account_id, _conn_id(presence_id))
    await presence.broadcast(account_id)
    return Response(status_code=204)


@router.get("/epic/presence/v1/{ns}/{account_id}/presence")
@router.get("/epic/presence/v1/{ns}/{account_id}/presence/{presence_id}")
async def eos_presence_get(ns: str, account_id: str, presence_id: str = ""):
    return presence.view(account_id)


@router.post("/epic/presence/v1/{ns}/_/presence")
@router.get("/epic/presence/v1/{ns}/{account_id}/subscriptions")
async def eos_presence_query(ns: str, request: Request, account_id: str = ""):
    ids = request.query_params.getlist("accountId")
    if not ids and request.method == "POST":
        try:
            body = await request.json()
            ids = body.get("accountIds", []) if isinstance(body, dict) else list(body)
        except Exception:
            ids = []
    if not ids and account_id:
        from ..social import Friends
        ids = list(Friends.of(account_id)["friends"])
    return [presence.view(i) for i in ids]


@router.post("/datarouter/api/v1/public/data/clients")
async def datarouter_clients():
    return {}


TEMPLATES = DATA / "templates"


# Supervised settings, login fails without these
@router.get("/v1/epic-settings/public/users/{account_id}/{rest:path}")
@router.patch("/v1/epic-settings/public/users/{account_id}/{rest:path}")
async def epic_settings(account_id: str, rest: str):
    data = json.loads((TEMPLATES / "epic_settings.json").read_text(encoding="utf-8"))
    # Drop parent limits so the account reads as unsupervised
    for setting in data["response"]["settings"]:
        for key in [k for k in setting if k.startswith("parentLimit")]:
            del setting[key]
    return data


@router.options("/v1/epic-settings/public/users/{account_id}/{rest:path}")
async def epic_settings_options(account_id: str, rest: str):
    return Response(status_code=204)


@router.post("/epic/socs/v1/users/{account_id}/settings/query")
async def socs_settings(account_id: str):
    return json.loads((TEMPLATES / "socs_settings.json").read_text(encoding="utf-8"))


@router.post("/epic/oauth/v2/revoke")
async def eos_revoke():
    return Response(status_code=204)
