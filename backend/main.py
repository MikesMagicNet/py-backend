import uvicorn
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException

from .profiles import load_cosmetics
from .routes import auth, content, eos, matchmaking, mcp, misc
from .utils import CONFIG, ROOT, epic_error

app = FastAPI(title="Halcyon Dev Backend", docs_url=None, redoc_url=None)
for r in (auth, eos, mcp, content, misc, matchmaking):
    app.include_router(r.router)


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
    uvicorn.run(app, host=CONFIG["host"], port=CONFIG["port"], log_level="warning")


if __name__ == "__main__":
    run()
