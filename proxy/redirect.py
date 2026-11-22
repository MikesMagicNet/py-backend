"""mitmproxy addon: send every Epic API host (EOS included) to the backend.

Run: mitmdump -s proxy/redirect.py --listen-port 8080
Then set the Windows proxy to 127.0.0.1:8080 and trust the mitmproxy CA (http://mitm.it).
"""
import json
from pathlib import Path

from mitmproxy import http

PORT = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())["port"]
EPIC = (".epicgames.com", ".epicgames.dev", ".epicgames.net")
DOWNLOAD = ("download", "fastly-download")


def _redirect(host: str) -> bool:
    if not host.endswith(EPIC):
        return False
    return not host.startswith(DOWNLOAD)


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if _redirect(host):
        flow.request.scheme = "http"
        flow.request.host = "127.0.0.1"
        flow.request.port = PORT
        flow.request.headers["Host"] = host
