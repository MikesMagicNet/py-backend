import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = json.loads((ROOT / "config.json").read_text())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    return uuid.uuid4().hex


def account_id_from_name(name: str) -> str:
    # Stable id per display name
    return uuid.uuid5(uuid.NAMESPACE_DNS, name.lower()).hex


def epic_error(code: str, message: str, status: int = 400, numeric: int = 1000, vars_=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers={"X-Epic-Error-Name": code, "X-Epic-Error-Code": str(numeric)},
        content={
            "errorCode": code,
            "errorMessage": message,
            "messageVars": vars_ or [],
            "numericErrorCode": numeric,
            "originatingService": "fortnite",
            "intent": "prod",
        },
    )


class Version:
    def __init__(self, season: int, build: float, cl: int, lobby: str):
        self.season = season
        self.build = build
        self.cl = cl
        self.lobby = lobby


def get_version(request: Request) -> Version:
    ua = request.headers.get("user-agent", "")
    cl_match = re.search(r"-CL-(\d+)", ua)
    cl = int(cl_match.group(1)) if cl_match else 0

    rel = re.search(r"Release-(\d+)\.(\d+)", ua)
    if rel:
        season = int(rel.group(1))
        build = float(f"{rel.group(1)}.{rel.group(2)}")
    else:
        # Pre-Season 2 builds have no Release tag
        season, build = (0, 0.0) if cl and cl < 3724489 else (1, 1.0)

    lobby = "LobbyWinterDecor" if season == 0 else f"LobbySeason{season}"
    return Version(season, build, cl, lobby)
