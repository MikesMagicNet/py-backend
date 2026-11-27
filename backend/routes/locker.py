import json
import zlib

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..utils import DATA, new_id, now_iso
from ..log import log, user
from .. import accounts

router = APIRouter()
TEMPLATES = DATA / "templates"
V4 = "/api/locker/v4/{deployment_id}"


def _template(name: str) -> dict:
    return json.loads((TEMPLATES / f"{name}.json").read_text(encoding="utf-8"))


async def _body(request: Request) -> dict:
    # 43.11+ may send compressed or empty bodies
    raw = await request.body()
    if not raw:
        return {}
    for decode in (lambda b: b, lambda b: zlib.decompress(b, 47), lambda b: zlib.decompress(b, -15)):
        try:
            data = json.loads(decode(raw))
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    try:
        import zstandard
        return json.loads(zstandard.ZstdDecompressor().decompress(raw, max_output_size=16 << 20))
    except Exception:
        log("Locker", f"Unreadable body ({request.headers.get('content-encoding')}): {raw[:32]!r}")
        return {}


def _path(account_id: str, name: str):
    return DATA / "profiles" / account_id / f"{name}.json"


def _load(account_id: str, name: str) -> dict:
    p = _path(account_id, name)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if name == "lockerv4":
            _clean(data["activeLoadoutGroup"].get("loadouts"))
            for preset in data.get("loadoutGroupPresets", []):
                _clean(preset.get("loadouts"))
        return data
    data = _template(name)
    if name == "lockerv4":
        group = data["activeLoadoutGroup"]
        group.update(accountId=account_id, creationTime=now_iso(), updatedTime=now_iso())
    _save(account_id, name, data)
    return data


def _clean_id(item_id):
    # The client sends "Type:id:Type:id" (template id + item id, which are the same here)
    if isinstance(item_id, str):
        parts = item_id.split(":")
        if len(parts) == 4 and parts[:2] == parts[2:]:
            return ":".join(parts[:2])
    return item_id


def _clean(loadouts):
    for loadout in (loadouts or {}).values():
        for slot in loadout.get("loadoutSlots") or []:
            if slot.get("equippedItemId"):
                slot["equippedItemId"] = _clean_id(slot["equippedItemId"])
    return loadouts


def _save(account_id: str, name: str, data: dict):
    p = _path(account_id, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


@router.get(V4 + "/account/{account_id}/items")
async def items(deployment_id: str, account_id: str):
    return _load(account_id, "lockerv4")


@router.put(V4 + "/account/{account_id}/active-loadout-group")
async def active_group(deployment_id: str, account_id: str, request: Request):
    body = await _body(request)
    data = _load(account_id, "lockerv4")
    group = data["activeLoadoutGroup"]
    if body.get("equippedPresetId"):
        group["equippedPresetId"] = body["equippedPresetId"]
    else:
        group.pop("equippedPresetId", None)
    group["loadouts"] = _clean(body.get("loadouts", group.get("loadouts", {})))
    group["updatedTime"] = now_iso()
    _save(account_id, "lockerv4", data)
    log("Locker", f"{accounts.cname(account_id)} equipped " + ", ".join(
        s["equippedItemId"] for l in group["loadouts"].values() for s in l.get("loadoutSlots", []) if s.get("equippedItemId")))
    return group


@router.put(V4 + "/account/{account_id}/loadout-group-preset/index/{index}")
async def group_preset(deployment_id: str, account_id: str, index: int, request: Request):
    body = await _body(request)
    data = _load(account_id, "lockerv4")
    presets = data["loadoutGroupPresets"]
    preset = next((p for p in presets if p.get("presetIndex") == index), None)
    if not preset:
        preset = {"accountId": account_id, "deploymentId": deployment_id, "presetId": new_id(),
                  "presetIndex": index, "creationTime": now_iso(), "presetFavoriteStatus": "EMPTY"}
        presets.append(preset)
    preset.update(athenaItemId=body.get("athenaItemId"), loadouts=_clean(body.get("loadouts")),
                  displayName=body.get("displayName", ""), updatedTime=now_iso())
    _save(account_id, "lockerv4", data)
    return preset


@router.put(V4 + "/loadout/{loadout_type}/account/{account_id}/loadout-preset/index/{index}")
async def loadout_preset(deployment_id: str, loadout_type: str, account_id: str, index: int, request: Request):
    body = await _body(request)
    data = _load(account_id, "lockerv4")
    presets = data["loadoutPresets"]
    preset = next((p for p in presets if p.get("presetIndex") == index and p.get("loadoutType") == loadout_type), None)
    if not preset:
        preset = {"deploymentId": deployment_id, "accountId": account_id, "loadoutType": loadout_type,
                  "presetId": new_id(), "presetIndex": index, "creationTime": now_iso(),
                  "presetFavoriteStatus": "EMPTY", "displayName": ""}
        presets.append(preset)
    slots = body.get("loadoutSlots")
    _clean({"x": {"loadoutSlots": slots}})
    preset.update(athenaItemId=body.get("athenaItemId"), loadoutSlots=slots, updatedTime=now_iso())
    if body.get("displayName"):
        preset["displayName"] = body["displayName"]
    _save(account_id, "lockerv4", data)
    return preset


@router.post(V4 + "/account/{account_id}/lock-in-immutable-item/{companion}")
@router.patch(V4 + "/account/{account_id}/companion-name")
async def companion(deployment_id: str, account_id: str, companion: str = ""):
    return Response(status_code=204)


@router.get(V4 + "/account/{account_id}/cosmetic-data")
async def cosmetic_data(deployment_id: str, account_id: str):
    data = _load(account_id, "cosmetic_data")
    data["requestTime"] = now_iso()
    data.pop("nextToken", None)
    return data


@router.api_route(V4 + "/account/{account_id}/cosmetic-usage", methods=["GET", "POST", "PUT"])
async def cosmetic_usage(deployment_id: str, account_id: str):
    return {"accountId": account_id, "cosmeticUsage": []}


@router.api_route(V4 + "/namespace/{namespace}/account/{account_id}/shuffle", methods=["GET", "POST", "PUT"])
async def shuffle(deployment_id: str, namespace: str, account_id: str):
    return {"accountId": account_id, "namespace": namespace, "shuffledLoadouts": []}
