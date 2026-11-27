import json
import threading
import time

import httpx

from .utils import CONFIG, DATA, now_iso
from .log import log, user

PROFILES_DIR = DATA / "profiles"
COSMETICS_CACHE = DATA / "cosmetics.json"
IMAGES_CACHE = DATA / "cosmetic_images.json"
IMAGES: dict[str, str] = {}  # lowercase cosmetic id -> icon url
NAMES_CACHE = DATA / "cosmetic_names.json"
NAMES: dict[str, str] = {}  # template id -> display name
_lock = threading.Lock()
_cosmetics: dict[str, dict] = {}


# fortnite-api.com sections that have no type.backendValue of their own
SECTION_TYPES = {"tracks": "SparksSong", "beans": "CosmeticMimosa"}
SKIP_SECTIONS = {"lego"}  # LEGO styles of BR outfits, granted with the outfit
CACHE_MAX_AGE = 24 * 3600


def _variants(c: dict) -> list:
    out = []
    for v in c.get("variants") or []:
        opts = v.get("options") or []
        if opts:
            out.append({"channel": v.get("channel"), "active": opts[0]["tag"], "owned": [o["tag"] for o in opts]})
    return out


def _fetch() -> dict[str, dict]:
    r = httpx.get("https://fortnite-api.com/v2/cosmetics", timeout=120)
    r.raise_for_status()
    items = {}
    for section, entries in r.json()["data"].items():
        if section in SKIP_SECTIONS or not isinstance(entries, list):
            continue
        for c in entries:
            backend = (c.get("type") or {}).get("backendValue") or SECTION_TYPES.get(section)
            if not backend or not c.get("id"):
                continue
            tid = f"{backend}:{c['id'].lower()}"
            items[tid] = _item(tid, _variants(c))
            NAMES[tid] = c.get("name") or c.get("title") or c["id"]
            imgs = c.get("images") or {}
            url = imgs.get("icon") or imgs.get("smallIcon") or imgs.get("large") or imgs.get("small") or c.get("albumArt")
            if url:
                IMAGES[c["id"].lower()] = url
    return items


def load_cosmetics() -> None:
    """Every cosmetic (BR, jam tracks, instruments, cars, LEGO kits, beans) from fortnite-api.com, cached a day."""
    global _cosmetics
    cached = json.loads(COSMETICS_CACHE.read_text()) if COSMETICS_CACHE.exists() else {}
    fresh = COSMETICS_CACHE.exists() and time.time() - COSMETICS_CACHE.stat().st_mtime < CACHE_MAX_AGE
    # Older caches held BR items only, those get refetched
    if fresh and IMAGES_CACHE.exists() and NAMES_CACHE.exists() and any(k.startswith("SparksSong:") for k in cached):
        _cosmetics = cached
        IMAGES.update(json.loads(IMAGES_CACHE.read_text()))
        NAMES.update(json.loads(NAMES_CACHE.read_text()))
        log("Cosmetics", f"Loaded {len(_cosmetics)} items from cache")
        return
    try:
        _cosmetics = _fetch()
        DATA.mkdir(exist_ok=True)
        COSMETICS_CACHE.write_text(json.dumps(_cosmetics))
        IMAGES_CACHE.write_text(json.dumps(IMAGES))
        NAMES_CACHE.write_text(json.dumps(NAMES))
        log("Cosmetics", f"Fetched {len(_cosmetics)} items")
    except Exception as e:
        _cosmetics = cached
        if IMAGES_CACHE.exists():
            IMAGES.update(json.loads(IMAGES_CACHE.read_text()))
        if NAMES_CACHE.exists():
            NAMES.update(json.loads(NAMES_CACHE.read_text()))
        log("Cosmetics", f"Fetch failed, using {len(cached)} cached items: {e}", "error")


def _grant_all(profile: dict) -> bool:
    """Adds cosmetics released since the profile was made. Returns True if anything was added."""
    added = False
    for tid, item in _cosmetics.items():
        if tid not in profile["items"]:
            profile["items"][tid] = json.loads(json.dumps(item))
            added = True
    return added


def find_cosmetics(query: str, limit: int = 25) -> list[str]:
    """Template ids matching a name or id, exact matches first."""
    q = query.strip().lower()
    hits = []
    for tid in _cosmetics:
        name = NAMES.get(tid, "").lower()
        if q in name or q in tid.lower():
            exact = q in (name, tid.lower(), tid.split(":", 1)[1].lower())
            hits.append((not exact, not name.startswith(q), name, tid))
    return [h[3] for h in sorted(hits)[:limit]]


def grant(profile: dict, template_id: str) -> bool:
    """Adds one cosmetic. False if it's unknown or already owned."""
    if template_id not in _cosmetics or template_id in profile["items"]:
        return False
    item = json.loads(json.dumps(_cosmetics[template_id]))
    item["attributes"]["item_seen"] = False
    profile["items"][template_id] = item
    return True


def _item(template_id: str, variants=None, quantity: int = 1) -> dict:
    return {
        "templateId": template_id,
        "attributes": {
            "max_level_bonus": 0, "level": 1, "item_seen": True, "xp": 0,
            "variants": variants or [], "favorite": False,
        },
        "quantity": quantity,
    }


DEFAULT_ITEMS = [
    "AthenaCharacter:cid_001_athena_commando_f_default",
    "AthenaPickaxe:defaultpickaxe",
    "AthenaGlider:defaultglider",
    "AthenaDance:eid_dancemoves",
]

LOCKER_SLOTS = {
    "Character": 1, "Backpack": 1, "Pickaxe": 1, "Glider": 1, "SkyDiveContrail": 1,
    "MusicPack": 1, "LoadingScreen": 1, "Dance": 6, "ItemWrap": 7,
}


def _base(profile_id: str, account_id: str, items: dict, attrs: dict) -> dict:
    return {
        "_id": account_id, "created": now_iso(), "updated": now_iso(), "rvn": 1, "wipeNumber": 1,
        "accountId": account_id, "profileId": profile_id, "version": "halcyon",
        "items": items, "stats": {"attributes": attrs}, "commandRevision": 0,
    }


def _athena(account_id: str) -> dict:
    items = {tid: _item(tid) for tid in DEFAULT_ITEMS}
    if CONFIG.get("everythingUnlocked", True):
        items.update(json.loads(json.dumps(_cosmetics)))

    items["sandbox_loadout"] = {
        "templateId": "CosmeticLocker:cosmeticlocker_athena",
        "attributes": {
            "locker_slots_data": {"slots": {
                k: {"items": [""] * n, "activeVariants": [None] * n} for k, n in LOCKER_SLOTS.items()
            }},
            "use_count": 0, "banner_icon_template": "", "banner_color_template": "",
            "locker_name": "", "item_seen": False, "favorite": False,
        },
        "quantity": 1,
    }
    items["sandbox_loadout"]["attributes"]["locker_slots_data"]["slots"]["Character"]["items"] = [DEFAULT_ITEMS[0]]
    items["sandbox_loadout"]["attributes"]["locker_slots_data"]["slots"]["Pickaxe"]["items"] = [DEFAULT_ITEMS[1]]

    attrs = {
        "past_seasons": [], "season_match_boost": 0, "loadouts": ["sandbox_loadout"],
        "mfa_reward_claimed": True, "rested_xp_overflow": 0, "quest_manager": {},
        "book_level": 1, "season_num": 0, "book_xp": 0, "permissions": [],
        "book_purchased": False, "lifetime_wins": 0, "party_assist_quest": "",
        "purchased_battle_pass_tier_offers": [], "rested_xp_exchange": 0, "level": 1,
        "xp_overflow": 0, "rested_xp": 0, "rested_xp_mult": 0, "accountLevel": 1,
        "competitive_identity": {}, "inventory_limit_bonus": 0, "last_applied_loadout": "sandbox_loadout",
        "daily_rewards": {}, "xp": 0, "season_friend_match_boost": 0, "active_loadout_index": 0,
        "favorite_musicpack": "", "favorite_glider": "", "favorite_pickaxe": DEFAULT_ITEMS[1],
        "favorite_skydivecontrail": "", "favorite_backpack": "", "favorite_dance": [""] * 6,
        "favorite_itemwraps": [""] * 7, "favorite_character": DEFAULT_ITEMS[0], "favorite_loadingscreen": "",
        "banner_icon": "BRSeason01", "banner_color": "DefaultColor1",
    }
    return _base("athena", account_id, items, attrs)


def _common_core(account_id: str) -> dict:
    vbucks = CONFIG.get("startingVbucks", 0)
    items = {
        "Currency:MtxPurchased": {"templateId": "Currency:MtxPurchased",
                                  "attributes": {"platform": "EpicPC"}, "quantity": vbucks},
    }
    for color in range(1, 22):
        tid = f"HomebaseBannerColor:defaultcolor{color}"
        items[tid] = {"templateId": tid, "attributes": {"item_seen": True}, "quantity": 1}
    attrs = {
        "survey_data": {}, "personal_offers": {}, "intro_game_played": True, "import_friends_claimed": {},
        "mtx_purchase_history": {"refundsUsed": 0, "refundCredits": 3, "purchases": []},
        "undo_cooldowns": [], "mtx_affiliate_set_time": "", "inventory_limit_bonus": 0,
        "current_mtx_platform": "EpicPC", "mtx_affiliate": "", "forced_intro_played": "Coconut",
        "weekly_purchases": {}, "daily_purchases": {}, "ban_history": {}, "in_app_purchases": {},
        "permissions": [], "undo_timeout": "min", "monthly_purchases": {},
        "allowed_to_send_gifts": True, "mfa_enabled": True, "allowed_to_receive_gifts": True,
        "gift_history": {},
    }
    return _base("common_core", account_id, items, attrs)


TEMPLATES = {"athena": _athena, "common_core": _common_core}


def _path(account_id: str, profile_id: str):
    return PROFILES_DIR / account_id / f"{profile_id}.json"


def get_profile(account_id: str, profile_id: str) -> dict:
    with _lock:
        p = _path(account_id, profile_id)
        if p.exists():
            profile = json.loads(p.read_text())
            if profile_id == "athena" and CONFIG.get("everythingUnlocked", True) and _grant_all(profile):
                p.write_text(json.dumps(profile))
            return profile
        maker = TEMPLATES.get(profile_id)
        profile = maker(account_id) if maker else _base(profile_id, account_id, {}, {})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(profile))
        return profile


def save_profile(profile: dict) -> None:
    with _lock:
        profile["updated"] = now_iso()
        p = _path(profile["accountId"], profile["profileId"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(profile))
