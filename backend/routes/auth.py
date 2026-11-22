import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..utils import account_id_from_name, epic_error, new_id, now_iso

router = APIRouter()

# token -> account info
TOKENS: dict[str, dict] = {}
# exchange code -> display name
EXCHANGE: dict[str, str] = {}


JWT_KEY = b"halcyon-dev-key"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(claims: dict) -> str:
    # Newer clients parse eg1 tokens as JWT
    head = _b64(json.dumps({"kid": "dev", "typ": "JWT", "alg": "HS256"}).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(JWT_KEY, f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def read_jwt(token: str) -> dict | None:
    try:
        body = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None


def _expiry(hours: int) -> str:
    t = datetime.now(timezone.utc) + timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _client_id(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            return base64.b64decode(auth[6:]).decode().split(":")[0]
        except Exception:
            pass
    return "ec684b8c687f479fadea3cb2ad83f5c6"


def account_from_request(request: Request) -> dict | None:
    auth = request.headers.get("authorization", "")
    token = auth.split(" ", 1)[-1].replace("eg1~", "")
    if token in TOKENS:
        return TOKENS[token]
    claims = read_jwt(token)
    if claims and "sub" in claims:
        return {"displayName": claims.get("dn", claims["sub"]), "accountId": claims["sub"],
                "clientId": claims.get("clid", "")}
    return None


@router.post("/account/api/oauth/token")
async def oauth_token(request: Request):
    form = await request.form()
    grant = form.get("grant_type", "")
    client_id = _client_id(request)

    if grant == "client_credentials":
        token = new_id()
        return {
            "access_token": token,
            "expires_in": 14400,
            "expires_at": _expiry(4),
            "token_type": "bearer",
            "client_id": client_id,
            "internal_client": True,
            "client_service": "fortnite",
        }

    if grant == "password":
        name = str(form.get("username", "")).split("@")[0] or "Player"
    elif grant == "exchange_code":
        code = str(form.get("exchange_code", ""))
        name = EXCHANGE.pop(code, None) or code or "Player"
    elif grant == "refresh_token":
        old = TOKENS.get(str(form.get("refresh_token", "")))
        if not old:
            return epic_error("errors.com.epicgames.account.auth_token.invalid_refresh_token",
                              "Invalid refresh token", 400, 18036)
        name = old["displayName"]
    elif grant == "device_auth":
        name = str(form.get("account_id", "Player"))
    else:
        return epic_error("errors.com.epicgames.common.oauth.unsupported_grant_type",
                          f"Unsupported grant type: {grant}", 400, 1016)

    name = name[:32]
    account_id = account_id_from_name(name)
    now = int(time.time())
    device = new_id()
    base = {"sub": account_id, "dvid": device, "clid": client_id, "dn": name, "am": grant,
            "iai": account_id, "sec": 1, "clsvc": "fortnite", "t": "s", "ic": True, "app": "fortnite",
            "pfpid": "prod-fn", "pfsid": "fn", "pfdid": "62a9473a2dca46b29ccf17577fcf42d7",
            "iss": "eg1", "iat": now}
    access = make_jwt({**base, "jti": new_id(), "exp": now + 28800})
    refresh = make_jwt({"sub": account_id, "dn": name, "clid": client_id, "t": "r", "am": grant,
                        "jti": new_id(), "iat": now, "exp": now + 86400, "iss": "eg1"})
    info = {"displayName": name, "accountId": account_id, "clientId": client_id}
    TOKENS[access] = info
    TOKENS[refresh] = info
    print(f"[Auth] {name} logged in ({grant})")

    return {
        "access_token": f"eg1~{access}",
        "expires_in": 28800,
        "expires_at": _expiry(8),
        "token_type": "bearer",
        "refresh_token": refresh,
        "refresh_expires": 86400,
        "refresh_expires_at": _expiry(24),
        "account_id": account_id,
        "client_id": client_id,
        "internal_client": True,
        "client_service": "fortnite",
        "displayName": name,
        "app": "fortnite",
        "in_app_id": account_id,
        "device_id": device,
        "scope": ["basic_profile", "friends_list", "openid", "presence"],
        "product_id": "prod-fn",
        "sandbox_id": "fn",
        "deployment_id": "62a9473a2dca46b29ccf17577fcf42d7",
        "application_id": "fghi4567FNFBKFz3E4TROb0bmPS8h1GW",
        "acr": "urn:epic:loa:aal1",
        "auth_time": now_iso(),
    }


@router.get("/account/api/oauth/verify")
async def oauth_verify(request: Request):
    acc = account_from_request(request) or {"displayName": "Player", "accountId": account_id_from_name("Player"),
                                            "clientId": _client_id(request)}
    token = request.headers.get("authorization", "").split(" ", 1)[-1]
    return {
        "token": token,
        "session_id": new_id(),
        "token_type": "bearer",
        "client_id": acc["clientId"],
        "internal_client": True,
        "client_service": "fortnite",
        "account_id": acc["accountId"],
        "expires_in": 28800,
        "expires_at": _expiry(8),
        "auth_method": "exchange_code",
        "display_name": acc["displayName"],
        "app": "fortnite",
        "in_app_id": acc["accountId"],
        "device_id": new_id(),
    }


@router.get("/account/api/oauth/exchange")
async def oauth_exchange(request: Request):
    acc = account_from_request(request)
    code = new_id()
    EXCHANGE[code] = acc["displayName"] if acc else "Player"
    return {"expiresInSeconds": 300, "code": code, "creatingClientId": _client_id(request)}


@router.delete("/account/api/oauth/sessions/kill")
@router.delete("/account/api/oauth/sessions/kill/{token}")
async def kill_session(token: str | None = None):
    return Response(status_code=204)


@router.get("/account/api/public/account/{account_id}")
async def get_account(account_id: str, request: Request):
    acc = account_from_request(request)
    name = acc["displayName"] if acc and acc["accountId"] == account_id else account_id
    return {
        "id": account_id,
        "displayName": name,
        "name": name,
        "email": f"{account_id}@halcyon.local",
        "failedLoginAttempts": 0,
        "lastLogin": now_iso(),
        "numberOfDisplayNameChanges": 0,
        "ageGroup": "UNKNOWN",
        "headless": False,
        "country": "US",
        "lastName": "Player",
        "preferredLanguage": "en",
        "canUpdateDisplayName": False,
        "tfaEnabled": False,
        "emailVerified": True,
        "minorVerified": False,
        "minorExpected": False,
        "minorStatus": "UNKNOWN",
    }


@router.get("/account/api/public/account")
async def get_accounts(request: Request):
    ids = request.query_params.getlist("accountId")
    return [{"id": i, "displayName": i, "externalAuths": {}} for i in ids]


@router.get("/account/api/public/account/displayName/{name}")
async def get_by_name(name: str):
    return {"id": account_id_from_name(name), "displayName": name, "externalAuths": {}}


@router.get("/account/api/public/account/{account_id}/externalAuths")
async def external_auths(account_id: str):
    return []


@router.api_route("/account/api/public/account/{account_id}/deviceAuth", methods=["GET", "POST"])
async def device_auth(account_id: str):
    return {"accountId": account_id, "deviceId": new_id(), "secret": new_id(), "created": {"dateTime": now_iso()}}


@router.get("/account/api/epicdomains/ssodomains")
async def sso_domains():
    return []
