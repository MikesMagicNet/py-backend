import re
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import catalog
from ..utils import CONFIG, ROOT, DATA, get_version, new_id, now_iso

router = APIRouter()
CLOUD = ROOT / "cloudstorage"
USER_CLOUD = DATA / "cloudstorage"
END = "9999-12-31T00:00:00.000Z"


# Status / version

@router.get("/lightswitch/api/service/bulk/status")
async def lightswitch_bulk():
    return await lightswitch("bulk")


@router.get("/lightswitch/api/service/{svc}/status")
async def lightswitch(svc: str = "Fortnite"):
    entry = {"serviceInstanceId": "fortnite", "status": "UP", "message": "Developer build is up",
             "maintenanceUri": None, "overrideCatalogIds": ["a7f138b2e51945ffbfdacc1af0541053"],
             "allowedActions": ["PLAY", "DOWNLOAD"], "banned": False,
             "launcherInfoDTO": {"appName": "Fortnite", "catalogItemId": "4fe75bbc5a674f4f9b356b5c90567da5",
                                 "namespace": "fn"}}
    return [entry] if svc == "bulk" else entry


@router.get("/fortnite/api/version")
@router.get("/fortnite/api/v2/versioncheck/{platform}")
@router.get("/fortnite/api/versioncheck/{platform}")
async def version(platform: str = ""):
    return {"type": "NO_UPDATE", "app": "fortnite", "serverDate": now_iso(), "overridePropertiesVersion": "unknown",
            "cln": "dev", "build": "dev", "moduleName": "Fortnite-Core", "buildDate": now_iso(),
            "version": "dev", "branch": "Release-Dev", "modules": {}}


# Calendar

@router.get("/fortnite/api/calendar/v1/timeline")
async def timeline(request: Request):
    v = get_version(request)
    events = [{"eventType": f"EventFlag.Season{v.season}", "activeUntil": END, "activeSince": "2020-01-01T00:00:00.000Z"},
              {"eventType": f"EventFlag.{v.lobby}", "activeUntil": END, "activeSince": "2020-01-01T00:00:00.000Z"}]
    return {
        "channels": {
            "client-matchmaking": {"states": [], "cacheExpire": END},
            "client-events": {"states": [{
                "validFrom": "2020-01-01T00:00:00.000Z", "activeEvents": events,
                "state": {"activeStorefronts": [], "eventNamedWeights": {}, "seasonNumber": v.season,
                          "seasonTemplateId": f"AthenaSeason:athenaseason{v.season}", "matchXpBonusPoints": 0,
                          "seasonBegin": "2020-01-01T00:00:00Z", "seasonEnd": END,
                          "seasonDisplayedEnd": END, "weeklyStoreEnd": END,
                          "stwEventStoreEnd": END, "stwWeeklyStoreEnd": END,
                          "sectionStoreEnds": {"Featured": END}, "dailyStoreEnd": END}}],
                "cacheExpire": END},
        },
        "eventsTimeOffsetHrs": 0, "cacheIntervalMins": 10, "currentTime": now_iso(),
    }


# Cloudstorage

def _system_bytes(path) -> bytes:
    # The XMPP address in the ini has to be the backend's public address once it runs on a VPS
    data = path.read_bytes()
    host = CONFIG.get("publicHost", "127.0.0.1")
    return data.replace(b'ServerAddr="ws://127.0.0.1"', f'ServerAddr="ws://{host}"'.encode())


def _file_entry(path, unique):
    data = _system_bytes(path) if path.suffix == ".ini" else path.read_bytes()
    return {"uniqueFilename": unique, "filename": path.name, "hash": hashlib.sha1(data).hexdigest(),
            "hash256": hashlib.sha256(data).hexdigest(), "length": len(data), "contentType": "application/octet-stream",
            "uploaded": now_iso(), "storageType": "S3", "storageIds": {}, "doNotCache": True}


@router.get("/fortnite/api/cloudstorage/system")
async def system_list():
    CLOUD.mkdir(exist_ok=True)
    return [_file_entry(p, p.name) for p in CLOUD.iterdir() if p.suffix == ".ini"]


@router.get("/fortnite/api/cloudstorage/system/config")
async def system_config():
    return {"lastUpdated": now_iso(), "disableV2": True, "isAuthenticated": True,
            "enumerateFilesPath": "/api/cloudstorage/system", "enableMigration": False,
            "enableWrites": False, "epicAppName": "Live", "transports": {}}


@router.get("/fortnite/api/cloudstorage/user/config")
async def user_config():
    return {"lastUpdated": now_iso(), "disableV2": True, "isAuthenticated": True,
            "enumerateFilesPath": "/api/cloudstorage/user", "enableMigration": False,
            "enableWrites": True, "epicAppName": "Live", "transports": {}}


@router.get("/fortnite/api/cloudstorage/system/{name}")
async def system_file(name: str):
    p = CLOUD / name
    if p.parent != CLOUD or not p.exists():
        return Response(status_code=200)
    return Response(_system_bytes(p), media_type="application/octet-stream")


@router.get("/fortnite/api/cloudstorage/user/{account_id}")
async def user_list(account_id: str, request: Request):
    p = USER_CLOUD / account_id / f"ClientSettings-{get_version(request).season}.Sav"
    return [_file_entry(p, "ClientSettings.Sav")] if p.exists() else []


@router.get("/fortnite/api/cloudstorage/user/{account_id}/{name}")
async def user_file(account_id: str, name: str, request: Request):
    p = USER_CLOUD / account_id / f"ClientSettings-{get_version(request).season}.Sav"
    return Response(p.read_bytes() if p.exists() else b"", media_type="application/octet-stream")


@router.put("/fortnite/api/cloudstorage/user/{account_id}/{name}")
async def user_put(account_id: str, name: str, request: Request):
    d = USER_CLOUD / account_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"ClientSettings-{get_version(request).season}.Sav").write_bytes(await request.body())
    return Response(status_code=204)


@router.get("/fortnite/api/cloudstorage/storage/{account_id}/info")
async def storage_info(account_id: str):
    return {"accountId": account_id, "totalStorage": 0, "totalFiles": 0}


# Storefront

@router.get("/fortnite/api/storefront/v2/catalog")
async def get_catalog():
    return catalog.get_catalog()


# Empty keychain counts as a failed download, so always send at least one key
KEYCHAIN_FILE = DATA / "keychain.json"
DEFAULT_KEYCHAIN = ["46159C748E2E4CF4B5E3F7C1A3D2B6E1:5Ue6Zp2Bq9rM0Hn8aVwXc3Ld1Ty4Ks7Gf6Jh2Np5Qr8=:Dev_Keychain_Placeholder"]


@router.get("/fortnite/api/storefront/v2/keychain")
async def keychain():
    if KEYCHAIN_FILE.exists():
        keys = json.loads(KEYCHAIN_FILE.read_text())
        if keys:
            return keys
    return DEFAULT_KEYCHAIN


@router.get("/catalog/api/shared/bulk/offers")
async def bulk_offers():
    return {}


# Stubs the client hits on login

@router.get("/fortnite/api/game/v2/enabled_features")
async def enabled_features():
    return []


@router.post("/fortnite/api/game/v2/tryPlayOnPlatform/account/{account_id}")
async def try_play(account_id: str):
    return PlainTextResponse("true")


@router.get("/fortnite/api/game/v2/world/info")
async def world_info():
    return {}


@router.get("/fortnite/api/receipts/v1/account/{account_id}/receipts")
async def receipts(account_id: str):
    return []


@router.api_route("/fortnite/api/game/v2/privacy/account/{account_id}", methods=["GET", "POST"])
async def privacy(account_id: str):
    return {"accountId": account_id, "optOutOfPublicLeaderboards": False}


@router.get("/fortnite/api/game/v2/br-inventory/account/{account_id}")
async def br_inventory(account_id: str):
    return {"stash": {"globalcash": 0}}


@router.get("/fortnite/api/game/v2/events/tournamentandhistory/{rest:path}")
@router.get("/api/v1/events/Fortnite/download/{account_id}")
async def events(account_id: str = "", rest: str = ""):
    return {"events": [], "player": {}, "templates": [], "resolvedWindowLocations": {}}


@router.get("/fortnite/api/statsv2/account/{account_id}")
@router.get("/statsproxy/api/statsv2/account/{account_id}")
async def stats(account_id: str):
    return {"startTime": 0, "endTime": 0, "stats": {}, "accountId": account_id}


# Terms: shared agreement exists, account has nothing left to accept
EULA_VERSION = 99


def _agreement(key: str) -> dict:
    return {"id": f"dev-{key}", "key": key, "version": EULA_VERSION, "revision": 1, "title": "Developer Terms",
            "body": "Developer build terms.", "locale": "en", "createdTimestamp": "2020-01-01T00:00:00.000Z",
            "lastModifiedTimestamp": "2020-01-01T00:00:00.000Z", "agentUserName": "dev", "status": "ACTIVE",
            "description": "", "hasResponse": True, "custom": False, "url": "", "wasDeclined": False}


@router.get("/eulatracking/api/shared/agreements/{key}")
async def eula_shared(key: str):
    return _agreement(key)


@router.get("/eulatracking/api/public/agreements/{key}/account/{account_id}")
async def eula_account(key: str, account_id: str):
    # No content = already accepted
    return Response(status_code=204)


@router.post("/eulatracking/api/public/agreements/{key}/version/{ver}/account/{account_id}/accept")
async def eula_accept(key: str, ver: str, account_id: str):
    return Response(status_code=204)


@router.get("/waitingroom/api/waitingroom")
@router.post("/fortnite/api/game/v2/chat/{rest:path}")
@router.post("/fortnite/api/game/v2/grant_access/{account_id}")
async def no_content(rest: str = "", account_id: str = ""):
    return Response(status_code=204)


@router.get("/fortnite/api/game/v2/creative/{rest:path}")
@router.get("/presence/api/v1/_/{account_id}/settings/subscriptions")
@router.get("/socialban/api/public/v1/{account_id}")
async def empty_obj(rest: str = "", account_id: str = ""):
    return {"bans": [], "warnings": []} if account_id else {}


# Install bundles (newer builds need these before login)

@router.get("/launcher/api/public/distributionpoints")
@router.get("/launcher/api/public/distributionpoints/")
async def distribution_points():
    return {"distributions": [
        "https://epicgames-download1.akamaized.net/",
        "https://fastly-download.epicgames.com/",
        "https://download.epicgames.com/",
    ]}


@router.get("/api/v1/installation/installations")
@router.get("/api/v1/installation/operations")
async def installation_stub():
    return []


@router.get("/unknown")
async def unknown():
    return Response(status_code=204)


@router.post("/telemetry/data/")
@router.post("/telemetry/data/{rest:path}")
async def telemetry(rest: str = ""):
    return Response(status_code=204)


# Newer build login extras

@router.post("/publickey/v2/publickey")
@router.post("/publickey/v2/publickey/")
async def public_key(request: Request):
    from .auth import account_from_request, make_jwt
    try:
        body = await request.json()
    except Exception:
        body = {}
    acc = account_from_request(request) or {}
    account_id = acc.get("accountId", "")
    key_guid = new_id()
    jwt = make_jwt({"account_id": account_id, "generated": now_iso(), "key": body.get("key", ""),
                    "key_guid": key_guid, "kid": "20230621", "expiration": "9999-12-31T23:59:59.999Z"})
    return {"key": body.get("key", ""), "accountId": account_id, "key_guid": key_guid, "kid": "20230621",
            "expiration": "9999-12-31T23:59:59.999Z", "jwt": jwt, "type": "legacyPublicKey"}


@router.put("/profile/play_region")
@router.post("/api/v1/installation/login")
async def no_content_extra():
    return Response(status_code=204)


# EOS SDK builds its service URLs from these, empty ones become /unknown
@router.get("/sdk/v1/default")
async def sdk_default():
    return _local_sockets(json.loads((DATA / "sdk_default.json").read_text()))


@router.get("/sdk/v1/product/{product}")
async def sdk_product(product: str):
    return _local_sockets(json.loads((DATA / "sdk_product.json").read_text()))


def _local_sockets(cfg):
    # The SDK's websockets skip the proxy, so point them straight at us
    base = f"ws://127.0.0.1:{CONFIG['port']}"
    text = re.sub(r"wss://[^/\"`]+", base, json.dumps(cfg))
    text = text.replace('"ServerPort": 443', f'"ServerPort": {CONFIG["port"]}')
    return json.loads(text)


@router.get("/hotconfigs/v2/{name}")
async def hotconfigs(name: str):
    return {"HotConfigData": []}


@router.get("/api/v1/games/fortnite/tracks/query")
async def tracks_query():
    return []


@router.post("/datarouter/api/v1/public/data")
async def datarouter(request: Request):
    # Client error events land here, keep them for debugging
    body = await request.body()
    with open(ROOT / "datarouter.log", "ab") as f:
        f.write(body + b"\n")
    return Response(status_code=204)


# Lobby extras that 404'd on 43.10

@router.get("/api/content/v2/launch-data")
@router.get("/api/content/v4/launch-data")
@router.post("/api/v1/fortnite-br/channel/interstitials/target")
@router.put("/profile/privacy_settings")
@router.put("/profile/languages")
async def lobby_no_content():
    return Response(status_code=204)


# EOS player inventories (island save data such as POIDiscoveryPlayerPersistence). A 404 reads as EOS_NotFound
# and the old {"items": ...} stub as EOS_UnrecognizedResponse; both fail the save load before a match.
# Field names come from the EOS SDK strings (player, inventoryName, versionId, dateCreated, data).
INVENTORIES = DATA / "inventories"


def _inv_path(account_id: str, name: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip("/")) or "default"
    return INVENTORIES / account_id / f"{safe}.json"


def _inventory(account_id: str, name: str) -> dict:
    p = _inv_path(account_id, name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"player": account_id, "inventoryName": name.strip("/"), "versionId": "0",
            "dateCreated": now_iso(), "data": {}}


def _inv_log(request: Request, body: bytes = b""):
    with open(ROOT / "inventory.log", "ab") as f:
        f.write(f"{now_iso()} {request.method} {request.url.path}?{request.url.query} "
                f"headers={ {k: v for k, v in request.headers.items() if k.startswith('x-epic')} } ".encode()
                + body[:4000] + b"\n")


@router.get("/api/inventory/v3/{deployment_id}/players/{account_id}/{name:path}")
async def player_inventory(deployment_id: str, account_id: str, name: str, request: Request):
    _inv_log(request)
    inv = _inventory(account_id, name)
    return JSONResponse(inv, headers={"x-epic-lock-id": new_id()})


@router.api_route("/api/inventory/{version}/{deployment_id}/players", methods=["POST", "PUT", "PATCH"])
@router.api_route("/api/inventory/{version}/{deployment_id}/players/{rest:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
async def player_inventory_write(version: str, deployment_id: str, request: Request, rest: str = ""):
    # Writes, locks and unlocks: logged so the real body shape can be read back, then accepted
    _inv_log(request, await request.body())
    return Response(status_code=204)


@router.get("/api/v1/games/fortnite/tracks/activeBy/{when}")
@router.get("/api/v1/games/fortnite/trackprogress/{account_id}")
async def track_progress(account_id: str = "", when: str = ""):
    return []


@router.get("/api/quest/v3/{deployment_id}/progress/account/{account_id}")
async def quest_progress(deployment_id: str, account_id: str):
    return {"quests": [], "accountId": account_id}


@router.get("/followers/api/v1/{ns}/{account_id}/followed")
@router.get("/api/v2/interactions/latest/Fortnite/{account_id}")
@router.get("/api/v2/interactions/aggregated/_/{account_id}")
async def empty_list(account_id: str, ns: str = ""):
    return []


@router.get("/app_installation/status")
async def app_installation():
    return {}


@router.post("/api/v1/assets/{rest:path}")
async def assets(rest: str):
    return {"FortCreativeDiscoverySurface": {"meta": {"promotion": 0}, "assets": {}}}


# Match/profile verification before queueing: a 404 here is what shows the "unsafe environment" popup
@router.post("/api/verify/match")
async def verify_match(request: Request):
    body = await request.body()
    with open(ROOT / "matchmaking.log", "ab") as f:
        f.write(b"verify/match " + body[:2000] + b"\n")
    return {"verified": True, "result": "ALLOW", "status": "OK"}


@router.post("/fortnite/api/game/v2/profileToken/verify/{account_id}")
async def profile_token_verify(account_id: str):
    return Response(status_code=204)
