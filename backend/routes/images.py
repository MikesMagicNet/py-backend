import base64

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..utils import DATA

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
        for url in (f"https://fortnite-api.com/images/cosmetics/br/{cosmetic}/icon.png",
                    f"https://fortnite-api.com/images/cosmetics/br/{cosmetic}/smallicon.png"):
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) > 500:
                    cached.write_bytes(r.content)
                    return _png(r.content)
            except httpx.HTTPError:
                pass
    return _png(FALLBACK.read_bytes())
