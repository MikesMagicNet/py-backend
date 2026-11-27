import asyncio
import time

from .utils import CONFIG, new_id
from .log import log, user
from . import accounts

SETTINGS = CONFIG.get("matchmaker", {})
SERVER_TIMEOUT = SETTINGS.get("serverTimeoutSec", 15)
RESERVE_TIMEOUT = SETTINGS.get("reserveTimeoutSec", 90)


class Server:
    def __init__(self, sid):
        self.id = sid
        self.ip = "127.0.0.1"
        self.port = 7777
        self.playlist = ""
        self.region = ""
        self.state = "loading"  # loading, joinable, started
        self.players = 0
        self.max_players = 100
        self.seen = time.time()
        self.reserved = {}  # account id -> time it was handed this server

    def free_slots(self):
        now = time.time()
        self.reserved = {a: t for a, t in self.reserved.items() if now - t < RESERVE_TIMEOUT}
        return self.max_players - self.players - len(self.reserved)

    def alive(self):
        return time.time() - self.seen < SERVER_TIMEOUT

    def info(self):
        return {"id": self.id, "ip": self.ip, "port": self.port, "playlist": self.playlist, "region": self.region,
                "state": self.state, "players": self.players, "maxPlayers": self.max_players,
                "reserved": list(self.reserved)}


class Ticket:
    def __init__(self, account_id, playlist, region, party, ticket_id=None, on_assign=None):
        self.id = ticket_id or new_id()
        self.on_assign = on_assign  # async callback, EOS v2 tickets push their assignment
        self.account_id = account_id
        self.playlist = playlist
        self.region = region
        self.party = party or [account_id]
        self.created = time.time()
        self.server = None
        self.assigned = asyncio.Event()


SERVERS: dict[str, Server] = {}
QUEUE: list[Ticket] = []
SESSIONS: dict[str, Server] = {}  # session id the client asks about -> server


def heartbeat(body: dict) -> Server:
    sid = body.get("id") or new_id()
    server = SERVERS.get(sid) or Server(sid)
    SERVERS[sid] = server
    server.ip = body.get("ip", server.ip)
    server.port = int(body.get("port", server.port))
    server.playlist = body.get("playlist", server.playlist).lower()
    server.region = body.get("region", server.region).upper()
    server.max_players = int(body.get("maxPlayers", server.max_players))
    server.seen = time.time()

    players = int(body.get("players", server.players))
    if players > server.players:
        # Joined players stop holding a reservation
        for account in list(server.reserved)[:players - server.players]:
            server.reserved.pop(account, None)
    server.players = players

    state = body.get("state", server.state)
    if state != server.state:
        log("Matchmaker", f"{server.id[:8]} {server.playlist} {server.ip}:{server.port} is {state}")
    server.state = state
    SESSIONS[server.id] = server
    return server


def remove(sid: str):
    if SERVERS.pop(sid, None):
        log("Matchmaker", f"{sid[:8]} closed")


def enqueue(ticket: Ticket):
    QUEUE.append(ticket)


def leave(ticket: Ticket):
    if ticket in QUEUE:
        QUEUE.remove(ticket)


def find(ticket_id: str):
    return next((t for t in QUEUE if t.id == ticket_id), None)


def queued_for(playlist: str) -> int:
    return sum(len(t.party) for t in QUEUE if t.playlist == playlist)


def _pick(ticket: Ticket):
    best = None
    for s in SERVERS.values():
        if not s.alive() or s.state != "joinable" or s.free_slots() < len(ticket.party):
            continue
        if s.playlist and ticket.playlist and s.playlist != ticket.playlist:
            continue
        if SETTINGS.get("matchRegion", False) and s.region and ticket.region and s.region != ticket.region:
            continue
        # Fill the fullest server first so matches start sooner
        if not best or s.players + len(s.reserved) > best.players + len(best.reserved):
            best = s
    return best


async def run():
    while True:
        for sid in [sid for sid, s in SERVERS.items() if not s.alive()]:
            log("Matchmaker", f"{sid[:8]} stopped sending heartbeats", "warn")
            SERVERS.pop(sid)
        for ticket in list(QUEUE):
            server = _pick(ticket)
            if not server:
                continue
            for account in ticket.party:
                server.reserved[account] = time.time()
            ticket.server = server
            QUEUE.remove(ticket)
            ticket.assigned.set()
            if ticket.on_assign:
                asyncio.create_task(ticket.on_assign(ticket))
            log("Matchmaker", f"{accounts.cname(ticket.account_id)} (+{len(ticket.party) - 1}) -> {server.id[:8]} {server.playlist}", "good")
        await asyncio.sleep(1)
