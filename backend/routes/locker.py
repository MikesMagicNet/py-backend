import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..utils import DATA, new_id, now_iso

router = APIRouter()
TEMPLATES = DATA / "templates"
V4 = "/api/locker/v4/{deployment_id}"


def _template(name: str) -> dict:
    return json.loads((TEMPLATES / f"{name}.json").read_text(encoding="utf-8"))


def _path(account_id: str, name: str):
    return DATA / "profiles" / account_id / f"{name}.json"


def _load(account_id: str, name: str) -> dict:
    p = _path(account_id, name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    data = _template(name)
    if name == "lockerv4":
        group = data["activeLoadoutGroup"]
        group.update(accountId=account_id, creationTime=now_iso(), updatedTime=now_iso())
    _save(account_id, name, data)
    return data


def _save(account_id: str, name: str, data: dict):
    p = _path(account_id, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


@router.get(V4 + "/account/{account_id}/items")
async def items(deployment_id: str, account_id: str):
    return _load(account_id, "lockerv4")


@router.put(V4 + "/account/{account_id}/active-loadout-group")
async def active_group(deployment_id: str, account_id: str, request: Request):
    body = await request.json()
    data = _load(account_id, "lockerv4")
    group = data["activeLoadoutGroup"]
    if body.get("equippedPresetId"):
        group["equippedPresetId"] = body["equippedPresetId"]
    else:
        group.pop("equippedPresetId", None)
    group["loadouts"] = body.get("loadouts", group.get("loadouts", {}))
    group["updatedTime"] = now_iso()
    _save(account_id, "lockerv4", data)
    return group


@router.put(V4 + "/account/{account_id}/loadout-group-preset/index/{index}")
async def group_preset(deployment_id: str, account_id: str, index: int, request: Request):
    body = await request.json()
    data = _load(account_id, "lockerv4")
    presets = data["loadoutGroupPresets"]
    preset = next((p for p in presets if p.get("presetIndex") == index), None)
    if not preset:
        preset = {"accountId": account_id, "deploymentId": deployment_id, "presetId": new_id(),
                  "presetIndex": index, "creationTime": now_iso(), "presetFavoriteStatus": "EMPTY"}
        presets.append(preset)
    preset.update(athenaItemId=body.get("athenaItemId"), loadouts=body.get("loadouts"),
                  displayName=body.get("displayName", ""), updatedTime=now_iso())
    _save(account_id, "lockerv4", data)
    return preset


@router.put(V4 + "/loadout/{loadout_type}/account/{account_id}/loadout-preset/index/{index}")
async def loadout_preset(deployment_id: str, loadout_type: str, account_id: str, index: int, request: Request):
    body = await request.json()
    data = _load(account_id, "lockerv4")
    presets = data["loadoutPresets"]
    preset = next((p for p in presets if p.get("presetIndex") == index and p.get("loadoutType") == loadout_type), None)
    if not preset:
        preset = {"deploymentId": deployment_id, "accountId": account_id, "loadoutType": loadout_type,
                  "presetId": new_id(), "presetIndex": index, "creationTime": now_iso(),
                  "presetFavoriteStatus": "EMPTY", "displayName": ""}
        presets.append(preset)
    preset.update(athenaItemId=body.get("athenaItemId"), loadoutSlots=body.get("loadoutSlots"), updatedTime=now_iso())
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
