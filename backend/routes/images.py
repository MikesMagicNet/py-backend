import base64

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..profiles import IMAGES
from ..utils import DATA
from ..log import log, user

router = APIRouter()
CACHE = DATA / "cache" / "images"
FALLBACK = DATA / "templates" / "fallback.png"


def _png(data: bytes) -> Response:
    # Client needs a real Content-Length, chunked replies show as blank icons
    return Response(data, media_type="image/png", headers={"Content-Length": str(len(data))})


# Cosmetic icons, d= is base64 of fn/<build>/<Type:id>/<image type>/...
@router.get("/v1/item/{rest:path}")
async def item_image(rest: str, request: Request):
    try:
        parts = base64.b64decode(request.query_params.get("d", "") + "==").decode("utf-8", "ignore").split("/")
    except Exception:
        parts = []
    cosmetic = parts[2].split(":")[-1] if len(parts) > 2 else "unknown"
    kind = parts[3] if len(parts) > 3 else "icon"

    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{cosmetic}_{kind}.png"
    if cached.exists() and cached.stat().st_size > 500:
        return _png(cached.read_bytes())

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        known = IMAGES.get(cosmetic.lower())
        for url in ([known] if known else []) + [f"https://fortnite-api.com/images/cosmetics/br/{cosmetic}/icon.png",
                    f"https://fortnite-api.com/images/cosmetics/br/{cosmetic}/smallicon.png"]:
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) > 500:
                    cached.write_bytes(r.content)
                    return _png(r.content)
            except httpx.HTTPError:
                pass
    log("Images", f"No icon for {cosmetic} ({kind})")
    return _png(FALLBACK.read_bytes())


# Lobby backgrounds in the link data point at localhost/cdn2-unrealengine/<file>, mirrored from cdn2.unrealengine.com
BG_CACHE = DATA / "cache" / "cdn2"


@router.get("/cdn2-unrealengine/{name:path}")
async def cdn2(name: str):
    name = name.replace("..", "").lstrip("/")
    media = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    cached = BG_CACHE / name.replace("/", "_")
    if not cached.exists():
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            try:
                r = await client.get(f"https://cdn2.unrealengine.com/{name}")
            except httpx.HTTPError:
                r = None
        if r is None or r.status_code != 200:
            return Response(status_code=404)
        BG_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
    data = cached.read_bytes()
    return Response(data, media_type=media, headers={"Content-Length": str(len(data))})
