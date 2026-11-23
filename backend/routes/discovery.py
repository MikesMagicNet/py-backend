import copy
import json

from fastapi import APIRouter, Request

from ..utils import DATA, get_version

router = APIRouter()
DISCOVERY = DATA / "discovery"
LINKS = json.loads((DISCOVERY / "links.json").read_text(encoding="utf-8"))
SURFACE = json.loads((DISCOVERY / "surface.json").read_text(encoding="utf-8"))
FIGMENT = json.loads((DISCOVERY / "figment_season10.json").read_text(encoding="utf-8"))

# Codes the client asks for that the link data names differently
ALIASES = {"experience_br": "set_br_playlists"}

# Playlists switched on per build
EXTRA_ACTIVE = {27.11: ["playlist_durian"], 32.11: ["playlist_quail"], 35.20: ["playlist_ripehoneydew"],
                37.51: ["playlist_limerock"], 38.11: ["playlist_skymango"], 40.41: ["playlist_smarttunapre"],
                41.00: ["playlist_figment_doofy"]}


def _links(build: float) -> dict:
    links = {x["mnemonic"]: x for x in copy.deepcopy(LINKS)}

    def activate(code):
        if code in links:
            links[code].update(active=True, disabled=False)

    if build >= 33.00:
        activate("set_figment_playlists")
        if build >= 42.10 and "set_figment_playlists" in links:
            links["set_figment_playlists"]["metadata"].update(FIGMENT)
    if build >= 36.10:
        activate("set_forbiddenfruit_nobuild_playlists")
    for code in EXTRA_ACTIVE.get(build, []):
        activate(code)
    if build >= 38.11:
        for code, ref in (("ref_panel_byepicfeeder_1", "ByEpicFeeder"),
                          ("ref_panel_byepicfeederreload_1", "ByEpicConvergenceReload"),
                          ("ref_panel_byepicfeedermoremodes_1", "ByEpicConvergenceMoreModes")):
            if code in links:
                links[code]["metadata"]["ref_id"] = f"CreativeDiscoverySurface_FrontendV2:{ref}"
    if build >= 42:
        for code in ("set_br_playlists", "playlist_defaultsolo", "playlist_defaultduo", "playlist_trios"):
            if code in links:
                links[code]["metadata"]["product_tag"] = "Product.BR"
    return links


def _find(links: dict, code: str) -> dict | None:
    link = links.get(code) or links.get(ALIASES.get(code, ""))
    if link and code not in links:
        link = dict(link, mnemonic=code)
    return link


@router.post("/links/api/fn/mnemonic")
async def mnemonic_links(request: Request):
    return list(_links(get_version(request).build).values())


@router.get("/links/api/fn/mnemonic/{code}/related")
async def related(code: str, request: Request):
    links = _links(get_version(request).build)
    out = {"parentLinks": [], "links": {}}
    link = _find(links, code)
    if not link:
        return out
    meta = link.get("metadata", {})
    if meta.get("sub_link_codes"):
        out["parentLinks"].append(link)
        for sub in meta["sub_link_codes"]:
            if sub in links:
                out["links"][sub] = links[sub]
    else:
        out["links"][code] = link
        parent = links.get(meta.get("parent_set", ""))
        if parent:
            out["parentLinks"].append(parent)
            for sub in parent["metadata"].get("sub_link_codes", []):
                if sub in links and sub not in out["links"]:
                    out["links"][sub] = links[sub]
    return out


@router.get("/links/api/fn/mnemonic/{code}")
async def mnemonic(code: str, request: Request):
    return _find(_links(get_version(request).build), code) or {}


@router.post("/api/v2/discovery/surface/{surface}")
async def surface(surface: str, request: Request):
    links = _links(get_version(request).build)

    def live(code):
        link = links.get(code)
        return not link or link.get("linkType") == "Reference" or (link.get("active", True) and not link.get("disabled"))

    # Event rows for other builds have no live playlist, hide them
    out = copy.deepcopy(SURFACE)
    out["panels"] = [p for p in out["panels"]
                     if any(live(r["linkCode"]) for r in p["firstPage"]["results"])]
    return out


@router.post("/api/v2/discovery/surface/{surface}/page")
async def surface_page(surface: str):
    return {"results": [], "hasMore": False, "panelTargetName": None, "pageMarker": None}


@router.post("/api/v1/links/favorites/{account_id}/check")
async def favorites(account_id: str):
    return {"results": [], "hasMore": False}


@router.post("/api/v1/links/lock-status/{account_id}/check")
async def lock_status(account_id: str, request: Request):
    body = await request.json()
    return {"results": [{"playerId": account_id, "linkCode": c, "lockStatus": "UNLOCKED",
                         "lockStatusReason": "NONE", "isVisible": True} for c in body.get("linkCodes", [])],
            "hasMore": False}


@router.post("/api/v2/discovery/link-entries")
async def link_entries(request: Request):
    body = await request.json()
    return {c: {"lastVisited": None, "linkCode": c, "isFavorite": False, "globalCCU": 1, "likePercent": None,
                "refId": None, "lockStatus": "UNLOCKED", "lockStatusReason": "NONE", "isVisible": True,
                "favoriteStatus": "NONE"} for c in body.get("linkCodes", [])}
