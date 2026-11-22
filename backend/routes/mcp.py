import json

from fastapi import APIRouter, Request

from .. import catalog
from ..profiles import get_profile, save_profile
from ..utils import epic_error, new_id, now_iso

router = APIRouter()


class Changes:
    def __init__(self, profile: dict):
        self.profile = profile
        self.list: list[dict] = []

    def stat(self, name, value):
        self.profile["stats"]["attributes"][name] = value
        self.list.append({"changeType": "statModified", "name": name, "value": value})

    def attr(self, item_id, name, value):
        self.profile["items"][item_id]["attributes"][name] = value
        self.list.append({"changeType": "itemAttrChanged", "itemId": item_id,
                          "attributeName": name, "attributeValue": value})

    def add(self, item_id, item):
        self.profile["items"][item_id] = item
        self.list.append({"changeType": "itemAdded", "itemId": item_id, "item": item})

    def quantity(self, item_id, qty):
        self.profile["items"][item_id]["quantity"] = qty
        self.list.append({"changeType": "itemQuantityChanged", "itemId": item_id, "quantity": qty})


def _apply_variants(ch: Changes, item_id: str, updates):
    if not updates or item_id not in ch.profile["items"]:
        return
    variants = ch.profile["items"][item_id]["attributes"].setdefault("variants", [])
    for u in updates:
        for v in variants:
            if v.get("channel") == u.get("channel"):
                v["active"] = u.get("active")
                break
        else:
            variants.append({"channel": u.get("channel"), "active": u.get("active"), "owned": u.get("owned", [])})
    ch.attr(item_id, "variants", variants)


def _find_item(profile: dict, template_id: str) -> str:
    """Item ids are template ids here, but match loosely in case of case or empty slots."""
    if not template_id or template_id in profile["items"]:
        return template_id
    low = template_id.lower()
    for k, v in profile["items"].items():
        if v["templateId"].lower() == low:
            return k
    return template_id


# Operations

def op_equip_br(ch: Changes, body: dict, **_):
    slot = body.get("slotName", "")
    item = _find_item(ch.profile, body.get("itemToSlot", ""))
    index = int(body.get("indexWithinSlot", 0) or 0)
    attrs = ch.profile["stats"]["attributes"]

    if slot in ("Dance", "ItemWrap"):
        key = "favorite_dance" if slot == "Dance" else "favorite_itemwraps"
        arr = attrs.get(key) or [""] * (6 if slot == "Dance" else 7)
        if index == -1:
            arr = [item] * len(arr)
        elif 0 <= index < len(arr):
            arr[index] = item
        ch.stat(key, arr)
    else:
        ch.stat(f"favorite_{slot.lower()}", item)
    _apply_variants(ch, item, body.get("variantUpdates"))


def op_set_locker_slot(ch: Changes, body: dict, **_):
    locker = body.get("lockerItem", "sandbox_loadout")
    if locker not in ch.profile["items"]:
        return
    category = body.get("category", "")
    item = _find_item(ch.profile, body.get("itemToSlot", ""))
    index = int(body.get("slotIndex", 0) or 0)

    slots = ch.profile["items"][locker]["attributes"]["locker_slots_data"]["slots"]
    slot = slots.setdefault(category, {"items": [""], "activeVariants": [None]})
    items = slot["items"]
    if index == -1:
        slot["items"] = [item] * len(items)
    else:
        while len(items) <= index:
            items.append("")
        items[index] = item

    updates = body.get("variantUpdates") or []
    if updates:
        active = slot.setdefault("activeVariants", [None] * len(slot["items"]))
        while len(active) <= max(index, 0):
            active.append(None)
        active[max(index, 0)] = {"variants": updates}
        _apply_variants(ch, item, updates)

    ch.attr(locker, "locker_slots_data", ch.profile["items"][locker]["attributes"]["locker_slots_data"])


def op_set_locker_banner(ch: Changes, body: dict, **_):
    locker = body.get("lockerItem", "sandbox_loadout")
    if locker in ch.profile["items"]:
        ch.attr(locker, "banner_icon_template", body.get("bannerIconTemplateName", ""))
        ch.attr(locker, "banner_color_template", body.get("bannerColorTemplateName", ""))


def op_set_locker_name(ch: Changes, body: dict, **_):
    locker = body.get("lockerItem", "sandbox_loadout")
    if locker in ch.profile["items"]:
        ch.attr(locker, "locker_name", body.get("name", ""))


def op_set_br_banner(ch: Changes, body: dict, **_):
    ch.stat("banner_icon", body.get("homebaseBannerIconId", ""))
    ch.stat("banner_color", body.get("homebaseBannerColorId", ""))


def op_mark_seen(ch: Changes, body: dict, **_):
    for i in body.get("itemIds", []):
        if i in ch.profile["items"]:
            ch.attr(i, "item_seen", True)


def op_favorite(ch: Changes, body: dict, **_):
    i = body.get("targetItemId", "")
    if i in ch.profile["items"]:
        ch.attr(i, "favorite", bool(body.get("bFavorite")))


def op_favorite_batch(ch: Changes, body: dict, **_):
    for i, fav in zip(body.get("itemIds", []), body.get("itemFavStatus", [])):
        if i in ch.profile["items"]:
            ch.attr(i, "favorite", bool(fav))


def op_archive_batch(ch: Changes, body: dict, **_):
    for i in body.get("itemIds", []):
        if i in ch.profile["items"]:
            ch.attr(i, "archived", bool(body.get("archived")))


def op_mtx_platform(ch: Changes, body: dict, **_):
    ch.stat("current_mtx_platform", body.get("newPlatform", "EpicPC"))


def op_affiliate(ch: Changes, body: dict, **_):
    ch.stat("mtx_affiliate", body.get("affiliateName", ""))
    ch.stat("mtx_affiliate_set_time", now_iso())


def op_modular_loadout(ch: Changes, body: dict, **_):
    # 30.00+ lockers send the whole loadout as a JSON string
    key = f"{body.get('loadoutType', 'unknown')}:{body.get('presetId', 0)}"
    loadouts = ch.profile["stats"]["attributes"].get("modular_loadouts", {})
    data = body.get("loadoutData", "{}")
    loadouts[key] = json.loads(data) if isinstance(data, str) else data
    ch.stat("modular_loadouts", loadouts)


def op_purchase(ch: Changes, body: dict, account_id: str, multi: list, notifications: list, **_):
    offer = catalog.find_offer(body.get("offerId", ""))
    if not offer:
        raise ValueError("Offer not found")
    qty = max(1, int(body.get("purchaseQuantity", 1) or 1))
    price = sum(p.get("finalPrice", 0) for p in offer.get("prices", [])) * qty

    mtx = ch.profile["items"].get("Currency:MtxPurchased")
    if mtx:
        if mtx["quantity"] < price:
            raise ValueError("Not enough V-Bucks")
        ch.quantity("Currency:MtxPurchased", mtx["quantity"] - price)

    athena = get_profile(account_id, "athena")
    athena_changes = []
    loot = []
    for grant in offer.get("itemGrants", []):
        tid = grant["templateId"]
        item_id = tid
        if item_id not in athena["items"]:
            item = {"templateId": tid, "attributes": {"item_seen": False, "variants": [], "favorite": False},
                    "quantity": grant.get("quantity", 1)}
            athena["items"][item_id] = item
            athena_changes.append({"changeType": "itemAdded", "itemId": item_id, "item": item})
        loot.append({"itemType": tid, "itemGuid": item_id, "itemProfile": "athena", "quantity": 1})

    if athena_changes:
        athena["rvn"] += 1
        athena["commandRevision"] += 1
        save_profile(athena)
        multi.append({"profileRevision": athena["rvn"], "profileId": "athena",
                      "profileChangesBaseRevision": athena["rvn"] - 1, "profileChanges": athena_changes,
                      "profileCommandRevision": athena["commandRevision"]})
    notifications.append({"type": "CatalogPurchase", "primary": True,
                          "lootResult": {"items": loot}})


OPS = {
    "EquipBattleRoyaleCustomization": op_equip_br,
    "SetCosmeticLockerSlot": op_set_locker_slot,
    "SetCosmeticLockerBanner": op_set_locker_banner,
    "SetCosmeticLockerName": op_set_locker_name,
    "SetBattleRoyaleBanner": op_set_br_banner,
    "MarkItemSeen": op_mark_seen,
    "SetItemFavoriteStatus": op_favorite,
    "SetItemFavoriteStatusBatch": op_favorite_batch,
    "SetItemArchivedStatusBatch": op_archive_batch,
    "SetMtxPlatform": op_mtx_platform,
    "SetAffiliateName": op_affiliate,
    "PutModularCosmeticLoadout": op_modular_loadout,
    "PurchaseCatalogEntry": op_purchase,
}


@router.post("/fortnite/api/game/v2/profile/{account_id}/{route}/{operation}")
async def mcp(account_id: str, route: str, operation: str, request: Request):
    profile_id = request.query_params.get("profileId", "athena")
    rvn = int(request.query_params.get("rvn", -1))
    try:
        body = await request.json()
    except Exception:
        body = {}

    profile = get_profile(account_id, profile_id)
    base_rvn = profile["rvn"]
    ch = Changes(profile)
    multi, notifications = [], []

    handler = OPS.get(operation)
    if handler:
        try:
            handler(ch, body, account_id=account_id, multi=multi, notifications=notifications)
        except ValueError as e:
            return epic_error("errors.com.epicgames.modules.gamesubcatalog.purchase_not_allowed", str(e), 400, 28004)
    elif operation not in ("QueryProfile", "ClientQuestLogin", "RefreshExpeditions", "SetReceiveGiftsEnabled"):
        print(f"[MCP] Unhandled operation {operation} ({profile_id})")

    if ch.list:
        profile["rvn"] += 1
        profile["commandRevision"] += 1
        save_profile(profile)

    # Client out of sync: send the whole profile
    changes = ch.list
    if rvn != base_rvn:
        changes = [{"changeType": "fullProfileUpdate", "profile": profile}]

    resp = {
        "profileRevision": profile["rvn"],
        "profileId": profile_id,
        "profileChangesBaseRevision": base_rvn,
        "profileChanges": changes,
        "profileCommandRevision": profile["commandRevision"],
        "serverTime": now_iso(),
        "multiUpdate": multi,
        "responseVersion": 1,
    }
    if notifications:
        resp["notifications"] = notifications
    return resp
