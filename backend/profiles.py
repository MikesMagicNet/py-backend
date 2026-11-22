import json
import threading

import httpx

from .utils import CONFIG, DATA, now_iso

PROFILES_DIR = DATA / "profiles"
COSMETICS_CACHE = DATA / "cosmetics.json"
_lock = threading.Lock()
_cosmetics: dict[str, dict] = {}


def load_cosmetics() -> None:
    """Pull every BR cosmetic from fortnite-api.com once and cache it."""
    global _cosmetics
    if COSMETICS_CACHE.exists():
        _cosmetics = json.loads(COSMETICS_CACHE.read_text())
        print(f"[Cosmetics] Loaded {len(_cosmetics)} items from cache")
        return
    try:
        r = httpx.get("https://fortnite-api.com/v2/cosmetics/br", timeout=60)
        r.raise_for_status()
        items = {}
        for c in r.json()["data"]:
            backend = c.get("type", {}).get("backendValue")
            if not backend:
                continue
            variants = []
            for v in c.get("variants") or []:
                opts = v.get("options") or []
                if opts:
                    variants.append({"channel": v.get("channel"), "active": opts[0]["tag"],
                                     "owned": [o["tag"] for o in opts]})
            items[f"{backend}:{c['id'].lower()}"] = _item(f"{backend}:{c['id'].lower()}", variants)
        DATA.mkdir(exist_ok=True)
        COSMETICS_CACHE.write_text(json.dumps(items))
        _cosmetics = items
        print(f"[Cosmetics] Fetched {len(items)} items")
    except Exception as e:
        print(f"[Cosmetics] Fetch failed, using defaults only: {e}")


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
        "book_level": 100, "season_num": 0, "book_xp": 0, "permissions": [],
        "book_purchased": True, "lifetime_wins": 0, "party_assist_quest": "",
        "purchased_battle_pass_tier_offers": [], "rested_xp_exchange": 0, "level": 100,
        "xp_overflow": 0, "rested_xp": 0, "rested_xp_mult": 0, "accountLevel": 100,
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
            return json.loads(p.read_text())
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
