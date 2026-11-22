import json

from .utils import DATA

CATALOG_FILE = DATA / "catalog.json"

DEFAULT_CATALOG = {
    "refreshIntervalHrs": 24,
    "dailyPurchaseHrs": 24,
    "expiration": "9999-12-31T00:00:00.000Z",
    "storefronts": [
        {"name": "BRDailyStorefront", "catalogEntries": []},
        {"name": "BRWeeklyStorefront", "catalogEntries": []},
    ],
}


def get_catalog() -> dict:
    if CATALOG_FILE.exists():
        return json.loads(CATALOG_FILE.read_text())
    DATA.mkdir(exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(DEFAULT_CATALOG, indent=2))
    return DEFAULT_CATALOG


def find_offer(offer_id: str) -> dict | None:
    for front in get_catalog()["storefronts"]:
        for entry in front["catalogEntries"]:
            if entry.get("offerId") == offer_id:
                return entry
    return None
