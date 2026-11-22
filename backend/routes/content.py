from fastapi import APIRouter, Request

from ..utils import get_version, now_iso

router = APIRouter()

# Dev build styling for every news surface
BRAND = "Fortnite Developer Build"
TAG = "Developer Preview"


def _motd(title: str, body: str, idx: int) -> dict:
    return {
        "entryType": "Website", "image": "", "tileImage": "", "hidden": False,
        "videoMute": False, "tabTitleOverride": title, "_type": "CommonUI Simple Message MOTD",
        "title": title, "body": body, "offerAction": "ShowOfferDetails", "videoLoop": False,
        "videoStreamingEnabled": False, "sortingPriority": 100 - idx, "id": f"dev-motd-{idx}",
        "videoAutoplay": False, "videoFullscreen": False, "spotlight": False, "websiteURL": "",
        "websiteButtonText": "", "buttonTextOverride": "",
    }


def _messages(v) -> list[dict]:
    return [
        {"image": "", "hidden": False, "_type": "CommonUI Simple Message Base", "adspace": "DEV",
         "title": BRAND, "body": f"{TAG}\nBuild {v.build or v.season} | CL {v.cl or 'unknown'}",
         "spotlight": False, "messagetype": "normal"},
        {"image": "", "hidden": False, "_type": "CommonUI Simple Message Base", "adspace": "QA",
         "title": "QA Notes", "body": "Report crashes with the build CL and repro steps.",
         "spotlight": False, "messagetype": "normal"},
    ]


@router.get("/content/api/pages/fortnite-game")
@router.get("/content/api/pages/fortnite-game/{rest:path}")
async def fortnite_game(request: Request, rest: str = ""):
    v = get_version(request)
    msgs = _messages(v)
    motds = [_motd(BRAND, f"{TAG}\nSeason {v.season} test environment.", 0),
             _motd("Backend", "All cosmetics unlocked for testing.", 1)]
    news_block = {"_type": "Battle Royale News", "messages": msgs, "motds": motds}

    return {
        "_title": "Fortnite Game", "_activeDate": "2017-08-30T03:20:48.050Z",
        "lastModified": now_iso(), "_locale": "en-US",
        "battleroyalenews": {"news": news_block, "_title": "battleroyalenews", "header": BRAND,
                             "style": "None", "_noIndex": False, "alwaysShow": True,
                             "_activeDate": "2018-08-17T16:00:00.000Z", "lastModified": now_iso(),
                             "_locale": "en-US"},
        "battleroyalenewsv2": {"news": {"motds": motds, "_type": "Battle Royale News v2"},
                               "_title": "battleroyalenewsv2", "header": BRAND,
                               "_activeDate": "2018-08-17T16:00:00.000Z", "lastModified": now_iso(),
                               "_locale": "en-US"},
        "subgameinfo": {"battleroyale": {"image": "", "color": "1164c1", "_type": "Subgame Info",
                                         "description": TAG, "subgame": "battleroyale",
                                         "standardMessageLine2": "", "title": BRAND,
                                         "standardMessageLine1": ""},
                        "_title": "SubgameInfo", "_activeDate": "2018-04-25T15:05:39.956Z",
                        "lastModified": now_iso(), "_locale": "en-US"},
        "emergencynotice": {"news": {"platform_messages": [], "_type": "Battle Royale News",
                                     "messages": []},
                            "_title": "emergencynotice", "_activeDate": "2018-08-06T19:00:26.217Z",
                            "lastModified": now_iso(), "_locale": "en-US"},
        "emergencynoticev2": {"emergencynotices": {"_type": "Emergency Notices", "emergencynotices": []},
                              "_title": "emergencynoticev2", "_activeDate": "2018-08-06T19:00:26.217Z",
                              "lastModified": now_iso(), "_locale": "en-US"},
        "dynamicbackgrounds": {"backgrounds": {"backgrounds": [
                                   {"stage": f"season{v.season}", "_type": "DynamicBackground", "key": "lobby"},
                                   {"stage": f"season{v.season}", "_type": "DynamicBackground", "key": "vault"}],
                                   "_type": "DynamicBackgroundList"},
                               "_title": "dynamicbackgrounds", "_noIndex": False,
                               "_activeDate": "2019-08-21T15:59:59.342Z", "lastModified": now_iso(),
                               "_locale": "en-US"},
        "shopSections": {"sectionList": {"_type": "ShopSectionList", "sections": [
                             {"bSortOffersByOwnership": False, "bShowIneligibleOffersIfGiftable": False,
                              "bEnableToastNotification": True, "background": {}, "_type": "ShopSection",
                              "landingPriority": 70, "bHidden": False, "sectionId": "Featured",
                              "bShowTimer": True, "sectionDisplayName": "Dev Featured",
                              "bShowIneligibleOffers": True},
                             {"bSortOffersByOwnership": False, "bShowIneligibleOffersIfGiftable": False,
                              "bEnableToastNotification": True, "background": {}, "_type": "ShopSection",
                              "landingPriority": 60, "bHidden": False, "sectionId": "Daily",
                              "bShowTimer": True, "sectionDisplayName": "Dev Daily",
                              "bShowIneligibleOffers": True}]},
                         "_title": "shop-sections", "_activeDate": "2022-12-01T23:45:00.000Z",
                         "lastModified": now_iso(), "_locale": "en-US"},
    }


@router.post("/api/v1/fortnite-br/surfaces/motd/target")
@router.post("/api/v1/fortnite-br/{surface}/target")
async def motd_target(request: Request, surface: str = "motd"):
    # 20.00+ news comes from here instead of the pages blob
    v = get_version(request)
    return {"contentType": "collection", "contentId": "fortnite-br-br-motd-collection",
            "tcId": "dev", "contentMeta": "{}", "contentItems": [
                {"contentType": "content-item", "contentId": f"dev-{i}", "tcId": f"dev-{i}",
                 "contentFields": {"Buttons": [], "FullScreenBackground": {}, "FullScreenBody": m["body"],
                                   "FullScreenTitle": m["title"], "TeaserTitle": m["title"],
                                   "TileImage": {}, "VerticalTextLayout": False},
                 "contentSchemaName": "DynamicMotd"} for i, m in enumerate(_messages(v))]}
