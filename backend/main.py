import asyncio

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.exceptions import HTTPException

from .profiles import load_cosmetics
from . import stomp, xmpp
from .routes import auth, chat, content, discovery, eos, images, locker, matchmaking, mcp, misc, social
from .utils import CONFIG, ROOT, epic_error

app = FastAPI(title="Halcyon Dev Backend", docs_url=None, redoc_url=None)
# Social first so it wins over the older stubs
for r in (social, chat, auth, eos, locker, discovery, images, mcp, content, misc, matchmaking):
    app.include_router(r.router)


@app.websocket("/{path:path}")
async def sockets(ws: WebSocket, path: str):
    # Every Epic socket lands here through the proxy, the subprotocol says which one it is
    protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    print(f"[Socket] /{path} protocols={protocols}")
    if "xmpp" in protocols or not protocols:
        await xmpp.serve(ws)
    elif any("stomp" in p for p in protocols) or path.strip("/") in ("", "v2") or "notifications" in path:
        if not CONFIG.get("epicConnect", False):
            # Refused like Neonite: EOS then drops its own presence and parties for XMPP and the v1 party service
            await ws.close(code=1008)
            return
        await stomp.serve(ws, protocols)
    else:
        await ws.close(code=1008)  # lobby and cerberus sockets are not served


LOG = open(ROOT / "requests.log", "a", buffering=1, encoding="utf-8")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/epic/", "/auth/", "/telemetry/")):
        body = await request.body()
        with open(ROOT / "eos.log", "ab") as f:
            head = f"\n== {request.method} {path}?{request.url.query} auth={request.headers.get('authorization', '')[:40]}\n"
            f.write(head.encode() + body[:4000])
    resp = await call_next(request)
    LOG.write(f"{resp.status_code} {request.method} {request.headers.get('x-original-host') or request.headers.get('host', '')} {request.url.path}?{request.url.query}\n")
    return resp


@app.exception_handler(HTTPException)
async def not_found(request: Request, exc: HTTPException):
    if exc.status_code == 404 and CONFIG.get("logUnknownRoutes", True):
        print(f"[Missing] {request.method} {request.url.path}")
    return epic_error("errors.com.epicgames.common.not_found",
                      "Sorry the resource you were trying to find could not be found", exc.status_code, 1004)


@app.on_event("startup")
async def startup():
    load_cosmetics()
    print(f"[Backend] Listening on {CONFIG['host']}:{CONFIG['port']}")


def run():
    # The game's XMPP client dials ws://127.0.0.1 on port 80, so listen there too
    ports = [CONFIG["port"]] + [p for p in CONFIG.get("extraPorts", [80]) if p != CONFIG["port"]]
    servers = [uvicorn.Server(uvicorn.Config(app, host=CONFIG["host"], port=p, log_level="warning")) for p in ports]
    for s in servers[1:]:
        s.install_signal_handlers = lambda: None

    async def serve_all():
        await asyncio.gather(*(_serve(s) for s in servers))

    asyncio.run(serve_all())


async def _serve(server):
    try:
        await server.serve()
    except SystemExit:
        print(f"[Backend] Could not listen on port {server.config.port} (in use or needs admin)")


if __name__ == "__main__":
    run()
