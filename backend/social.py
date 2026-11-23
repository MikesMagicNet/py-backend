"""Friends (saved to disk) and parties, pings and intentions (memory only)."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from .utils import DATA, new_id, now_iso

FILE = DATA / "social.json"


def iso_in(seconds: int) -> str:
    t = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Friends:
    # accountId -> {friends: {id: created}, incoming: {...}, outgoing: {...}, blocked: [ids]}
    data: dict[str, dict] = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}

    @classmethod
    def save(cls):
        DATA.mkdir(exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cls.data, indent=1), encoding="utf-8")
        os.replace(tmp, FILE)

    @classmethod
    def of(cls, account_id: str) -> dict:
        return cls.data.setdefault(account_id, {"friends": {}, "incoming": {}, "outgoing": {}, "blocked": []})

    @classmethod
    def are_friends(cls, a: str, b: str) -> bool:
        return b in cls.of(a)["friends"]

    @classmethod
    def add(cls, me_id: str, target: str) -> str:
        """Sends a request, or accepts theirs. Returns requested, accepted or a failure reason."""
        if me_id == target:
            return "self"
        me, them = cls.of(me_id), cls.of(target)
        if target in me["blocked"] or me_id in them["blocked"]:
            return "blocked"
        if target in me["friends"]:
            return "already_friends"
        if target in me["outgoing"]:
            return "already_sent"
        now = now_iso()
        if target in me["incoming"]:
            del me["incoming"][target]
            them["outgoing"].pop(me_id, None)
            me["friends"][target] = now
            them["friends"][me_id] = now
            cls.save()
            return "accepted"
        me["outgoing"][target] = now
        them["incoming"][me_id] = now
        cls.save()
        return "requested"

    @classmethod
    def remove(cls, me_id: str, target: str) -> str | None:
        """Removes a friend, declines or cancels a request. Returns what it was."""
        me, them = cls.of(me_id), cls.of(target)
        kind = next((k for k in ("friends", "incoming", "outgoing") if target in me[k]), None)
        for key in ("friends", "incoming", "outgoing"):
            me[key].pop(target, None)
            them[key].pop(me_id, None)
        if kind:
            cls.save()
        return kind

    @classmethod
    def block(cls, me_id: str, target: str) -> str | None:
        kind = cls.remove(me_id, target)
        me = cls.of(me_id)
        if target not in me["blocked"]:
            me["blocked"].append(target)
        cls.save()
        return kind

    @classmethod
    def unblock(cls, me_id: str, target: str):
        me = cls.of(me_id)
        if target in me["blocked"]:
            me["blocked"].remove(target)
            cls.save()


class Parties:
    parties: dict[str, dict] = {}
    member_party: dict[str, str] = {}
    pings: dict[str, list[dict]] = {}       # pinged account -> pings
    intentions: dict[str, list[dict]] = {}  # requestee -> join requests

    @classmethod
    def get(cls, party_id: str) -> dict | None:
        return cls.parties.get(party_id)

    @classmethod
    def of_member(cls, account_id: str) -> dict | None:
        return cls.parties.get(cls.member_party.get(account_id, ""))

    @staticmethod
    def member(party: dict, account_id: str) -> dict | None:
        return next((m for m in party["members"] if m["account_id"] == account_id), None)

    @staticmethod
    def captain(party: dict) -> dict | None:
        return next((m for m in party["members"] if m["role"] == "CAPTAIN"), None)

    @classmethod
    def touch(cls, party: dict):
        party["revision"] += 1
        party["updated_at"] = now_iso()

    @classmethod
    def create(cls, body: dict) -> tuple[dict, dict | None]:
        """Returns the party and the leave result of the old party, if any."""
        conn = body["join_info"]["connection"]
        leader = conn["id"].split("@")[0]
        left = cls.leave(leader)
        now = now_iso()
        party = {"id": new_id(), "created_at": now, "updated_at": now, "config": body.get("config", {}),
                 "members": [], "applicants": [], "meta": body.get("meta", {}), "invites": [], "revision": 0,
                 "intentions": []}
        cls.parties[party["id"]] = party
        cls.add_member(party, leader, conn, body["join_info"].get("meta"), "CAPTAIN")
        return party, left

    @classmethod
    def add_member(cls, party: dict, account_id: str, conn: dict | None, meta: dict | None, role: str = "MEMBER"):
        existing = cls.member(party, account_id)
        if existing:
            return existing
        now = now_iso()
        conn = conn or {}
        member = {"account_id": account_id, "meta": meta or {}, "connections": [{
                      "id": conn.get("id", account_id), "connected_at": now, "updated_at": now,
                      "yield_leadership": bool(conn.get("yield_leadership", False)), "meta": conn.get("meta", {})}],
                  "revision": 0, "updated_at": now, "joined_at": now, "role": role}
        party["members"].append(member)
        cls.member_party[account_id] = party["id"]
        party["invites"] = [i for i in party["invites"] if i["sent_to"] != account_id]
        cls.touch(party)
        return member

    @classmethod
    def leave(cls, account_id: str) -> dict | None:
        """Returns {party_id, party (None once empty), new_captain}."""
        party = cls.of_member(account_id)
        cls.member_party.pop(account_id, None)
        if not party:
            return None
        was_captain = (cls.member(party, account_id) or {}).get("role") == "CAPTAIN"
        party["members"] = [m for m in party["members"] if m["account_id"] != account_id]
        if not party["members"]:
            del cls.parties[party["id"]]
            return {"party_id": party["id"], "party": None, "new_captain": None}
        new_captain = None
        if was_captain:
            party["members"][0]["role"] = "CAPTAIN"
            new_captain = party["members"][0]["account_id"]
        cls.touch(party)
        return {"party_id": party["id"], "party": party, "new_captain": new_captain}

    @classmethod
    def promote(cls, party: dict, account_id: str) -> bool:
        target = cls.member(party, account_id)
        if not target:
            return False
        for m in party["members"]:
            m["role"] = "MEMBER"
        target["role"] = "CAPTAIN"
        cls.touch(party)
        return True

    @classmethod
    def update_meta(cls, party: dict, update: dict | None, remove: list | None):
        party["meta"].update(update or {})
        for key in remove or []:
            party["meta"].pop(key, None)
        cls.touch(party)

    @classmethod
    def update_member_meta(cls, party: dict, account_id: str, update: dict | None, remove: list | None):
        member = cls.member(party, account_id)
        if not member:
            return None
        member["meta"].update(update or {})
        for key in remove or []:
            member["meta"].pop(key, None)
        member["revision"] += 1
        member["updated_at"] = now_iso()
        cls.touch(party)
        return member

    @classmethod
    def invite(cls, party: dict, sent_by: str, sent_to: str, meta: dict) -> dict:
        now = now_iso()
        party["invites"] = [i for i in party["invites"] if i["sent_to"] != sent_to]
        invite = {"party_id": party["id"], "sent_by": sent_by, "meta": meta or {}, "sent_to": sent_to,
                  "sent_at": now, "updated_at": now,
                  "expires_at": iso_in(party["config"].get("invite_ttl", 14400)), "status": "SENT"}
        party["invites"].append(invite)
        return invite

    @classmethod
    def invites_for(cls, account_id: str) -> list[dict]:
        return [i for p in cls.parties.values() for i in p["invites"] if i["sent_to"] == account_id]

    @classmethod
    def drop_invite(cls, party: dict, account_id: str):
        party["invites"] = [i for i in party["invites"] if i["sent_to"] != account_id]

    @classmethod
    def ping(cls, sent_by: str, sent_to: str, meta: dict | None = None) -> dict:
        entry = {"sent_by": sent_by, "sent_to": sent_to, "sent_at": now_iso(), "expires_at": iso_in(3600),
                 "meta": meta or {}}
        cls.pings[sent_to] = [p for p in cls.pings_for(sent_to) if p["sent_by"] != sent_by] + [entry]
        return entry

    @classmethod
    def pings_for(cls, account_id: str) -> list[dict]:
        now = time.time()
        live = [p for p in cls.pings.get(account_id, []) if _ts(p["expires_at"]) > now]
        cls.pings[account_id] = live
        return live

    @classmethod
    def clear_ping(cls, sent_to: str, sent_by: str):
        cls.pings[sent_to] = [p for p in cls.pings.get(sent_to, []) if p["sent_by"] != sent_by]

    @classmethod
    def intention(cls, requester: str, requestee: str, meta: dict) -> dict:
        entry = {"requester_id": requester, "requester_pl": "WIN", "requestee_id": requestee, "meta": meta or {},
                 "sent_at": now_iso(), "expires_at": iso_in(60)}
        cls.intentions[requestee] = [i for i in cls.intentions.get(requestee, [])
                                     if i["requester_id"] != requester] + [entry]
        return entry

    @classmethod
    def clear_intention(cls, requestee: str, requester: str):
        cls.intentions[requestee] = [i for i in cls.intentions.get(requestee, []) if i["requester_id"] != requester]


def _ts(iso: str) -> float:
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
