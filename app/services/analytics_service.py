"""Analytics: team, phone, chat, ticket, message and member metrics.

Data sources
- MongoDB ``messages``: from_me, timestamp (naive UTC), chat_id, phone_id,
  is_flagged (set on inbound messages by AI auto-flag) and sent_by_agent_id
  (set only when an agent sends from the CRM inbox; messages sent from the
  phone itself, bulk jobs, automations, the AI agent or the public API carry
  no agent, so they count in totals but in no member's row).
- MongoDB ``chats``: created_at (when the chat first reached the CRM), is_group.
- MongoDB ``group_events`` (join/add/leave/remove/...), may not exist yet.
- MySQL ``tickets`` (created_at, resolved_at, status, assigned_to),
  ``activity_logs`` (who moved a ticket to resolved/closed) and
  ``agent_sessions`` (websocket presence spans, for uptime).

Every query is scoped to the phones the caller may access; see ``Scope``.

Definitions
- Active chats: chats with >= 1 message in range.
- Messages sent: outgoing messages (per member where attributable).
- Chats initiated: chats whose first message in range is outgoing.
- Responses (to flagged messages): an outgoing message that answers a
  flagged inbound message still waiting for a reply in that chat.
- Median first response time: median delay from a flagged inbound message
  to the next outgoing message in the chat. When no inbound message in the
  scope is flagged (flagging unused), any inbound message that follows an
  outgoing one starts the clock instead (``frt_basis`` says which).
- User uptime: time with >= 1 open websocket (agent_sessions).
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.agent import Agent
from app.models.agent_session import AgentSession
from app.models.phone import Phone
from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)

# Notification/system entries that are not real conversation messages
SYSTEM_MESSAGE_TYPES = [
    "revoke", "e2e_notification", "notification_template", "protocol",
    "call_log", "gp2", "notification", "ciphertext",
]
_CLOSED = (TicketStatus.RESOLVED, TicketStatus.CLOSED)
_OPEN = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS)
# How far before the range start to look for an unanswered inbound message
RESPONSE_LOOKBACK = timedelta(days=7)
MAX_RESPONSE_SCAN = 300_000
ALLOW_DISK = {"allowDiskUse": True}


# ── Scope & time buckets ────────────────────────────────────────── #

@dataclass
class Scope:
    frm: datetime            # naive UTC, inclusive
    to: datetime             # naive UTC, exclusive
    tz: str                  # IANA zone used for bucket boundaries
    bucket: str              # "hour" | "day"
    phone_ids: list[int] | None   # None = every phone (unrestricted caller, no filter)
    chat_id: int | None = None
    agent_ids: list[int] | None = None  # None = every member

    def mongo_base(self, ts_field: str = "timestamp", frm: datetime | None = None) -> dict:
        m: dict = {ts_field: {"$gte": frm or self.frm, "$lt": self.to}}
        if self.phone_ids is not None:
            m["phone_id"] = {"$in": self.phone_ids}
        if self.chat_id is not None:
            m["chat_id"] = self.chat_id
        return m

    def message_match(self, frm: datetime | None = None) -> dict:
        return {**self.mongo_base("timestamp", frm), "message_type": {"$nin": SYSTEM_MESSAGE_TYPES}}

    def trunc(self, field: str = "$timestamp") -> dict:
        return {"$dateTrunc": {"date": field, "unit": self.bucket, "timezone": self.tz}}

    def agent_ok(self, agent_id) -> bool:
        return self.agent_ids is None or agent_id in self.agent_ids


def bucket_starts(scope: Scope) -> list[datetime]:
    """Naive-UTC start of every local hour/day bucket overlapping the range."""
    tz = ZoneInfo(scope.tz)
    local = scope.frm.replace(tzinfo=timezone.utc).astimezone(tz)
    if scope.bucket == "hour":
        cur = local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        step = lambda d: d + timedelta(hours=1)  # noqa: E731
    else:
        day = local.date()
        cur = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)

        def step(d):
            nd = d.astimezone(tz).date() + timedelta(days=1)
            return datetime(nd.year, nd.month, nd.day, tzinfo=tz).astimezone(timezone.utc)
    end = scope.to.replace(tzinfo=timezone.utc)
    out: list[datetime] = []
    while cur < end and len(out) < 2000:
        out.append(cur.replace(tzinfo=None))
        cur = step(cur)
    return out


class _Buckets:
    def __init__(self, scope: Scope) -> None:
        self.starts = bucket_starts(scope)
        self._pos = {s: i for i, s in enumerate(self.starts)}

    def zeros(self) -> list[int]:
        return [0] * len(self.starts)

    def index(self, ts: datetime | None) -> int | None:
        """Bucket for a raw timestamp (Python-side bucketing)."""
        if ts is None or not self.starts or ts < self.starts[0]:
            return None
        return bisect.bisect_right(self.starts, ts) - 1

    def key(self, trunc: datetime | None) -> int | None:
        """Bucket for a $dateTrunc result from Mongo."""
        if trunc is None:
            return None
        return self._pos.get(trunc.replace(tzinfo=None) if trunc.tzinfo else trunc)

    def labels(self) -> list[str]:
        return [s.isoformat() + "Z" for s in self.starts]


def _median_seconds(vals: list[float]) -> float | None:
    return round(median(vals), 1) if vals else None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ── Service ─────────────────────────────────────────────────────── #

class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @property
    def mdb(self):
        from app.db.mongo import get_mongo_db
        return get_mongo_db()

    def _range(self, scope: Scope) -> dict:
        return {"from": _iso(scope.frm), "to": _iso(scope.to), "bucket": scope.bucket, "tz": scope.tz}

    # ── Members / phones helpers ── #

    def _agents(self, scope: Scope) -> list[Agent]:
        q = self.db.query(Agent).filter(Agent.is_active == True)  # noqa: E712
        if scope.agent_ids is not None:
            q = q.filter(Agent.id.in_(scope.agent_ids or [0]))
        return q.order_by(Agent.name.asc()).all()

    @staticmethod
    def _agent_info(a: Agent, online: set[int]) -> dict:
        return {"agent_id": a.id, "name": a.name, "email": a.email,
                "avatar_color": a.avatar_color, "online": a.id in online}

    def _phones(self, scope: Scope, with_data: set | None = None) -> list[Phone]:
        """Active phones in scope, plus inactive ones that still have data in range."""
        keep = [int(p) for p in (with_data or ()) if isinstance(p, int)]
        cond = Phone.is_active == True  # noqa: E712
        q = self.db.query(Phone).filter(or_(cond, Phone.id.in_(keep)) if keep else cond)
        if scope.phone_ids is not None:
            q = q.filter(Phone.id.in_(scope.phone_ids or [0]))
        return q.order_by(Phone.id.asc()).all()

    async def _scoped_chat_ids(self, scope: Scope) -> list[int] | None:
        """Mongo chat ids in scope for SQL tables keyed by chat_id (None = all)."""
        if scope.chat_id is not None:
            if scope.phone_ids is None:
                return [scope.chat_id]
            ok = await self.mdb.chats.count_documents(
                {"id": scope.chat_id, "phone_id": {"$in": scope.phone_ids}})
            return [scope.chat_id] if ok else []
        if scope.phone_ids is None:
            return None
        ids = await self.mdb.chats.distinct("id", {"phone_id": {"$in": scope.phone_ids}})
        return [int(i) for i in ids]

    # ── Message aggregates (one pipeline, several facets) ── #

    async def _message_facets(self, scope: Scope) -> dict:
        pipeline = [
            {"$match": scope.message_match()},
            {"$project": {"_id": 0, "chat_id": 1, "phone_id": 1, "from_me": 1,
                          "a": "$sent_by_agent_id", "timestamp": 1, "b": scope.trunc()}},
            {"$facet": {
                "by_bucket": [
                    {"$group": {"_id": {"b": "$b", "fm": "$from_me", "a": "$a"}, "n": {"$sum": 1}}},
                ],
                "by_bucket_chat": [
                    {"$group": {"_id": {"b": "$b", "c": "$chat_id"},
                                "agents": {"$addToSet": {"$cond": ["$from_me", "$a", None]}}}},
                ],
                "by_chat": [
                    {"$group": {"_id": {"c": "$chat_id", "p": "$phone_id", "fm": "$from_me", "a": "$a"},
                                "n": {"$sum": 1}}},
                ],
                "initiated": [
                    {"$sort": {"chat_id": 1, "timestamp": 1}},
                    {"$group": {"_id": "$chat_id", "fm": {"$first": "$from_me"},
                                "a": {"$first": "$a"}, "p": {"$first": "$phone_id"}}},
                    {"$match": {"fm": True}},
                ],
            }},
        ]
        res = await self.mdb.messages.aggregate(pipeline, **ALLOW_DISK).to_list(1)
        return res[0] if res else {"by_bucket": [], "by_bucket_chat": [], "by_chat": [], "initiated": []}

    async def _response_pairs(self, scope: Scope) -> tuple[list[tuple], str]:
        """(reply_ts, seconds, agent_id, phone_id) per answered inbound message.

        Returns the flagged-message pairs when any inbound message in scope is
        flagged, else the any-inbound pairs; plus the basis used.
        """
        match = scope.message_match(frm=scope.frm - RESPONSE_LOOKBACK)
        cursor = self.mdb.messages.aggregate([
            {"$match": match},
            {"$project": {"_id": 0, "c": "$chat_id", "p": "$phone_id", "fm": "$from_me",
                          "f": "$is_flagged", "a": "$sent_by_agent_id", "t": "$timestamp"}},
            {"$sort": {"c": 1, "t": 1}},
            {"$limit": MAX_RESPONSE_SCAN},
        ], **ALLOW_DISK)
        flagged: list[tuple] = []
        anyin: list[tuple] = []
        saw_flag = False
        cur_chat = object()
        pend_flag = pend_any = None
        async for m in cursor:
            ts = m.get("t")
            if not isinstance(ts, datetime):
                continue
            if m.get("c") != cur_chat:
                cur_chat, pend_flag, pend_any = m.get("c"), None, None
            if not m.get("fm"):
                if pend_any is None:
                    pend_any = ts
                if m.get("f"):
                    if ts >= scope.frm:
                        saw_flag = True
                    if pend_flag is None:
                        pend_flag = ts
                continue
            if ts >= scope.frm:
                a, p = m.get("a"), m.get("p")
                if pend_flag is not None:
                    flagged.append((ts, max(0.0, (ts - pend_flag).total_seconds()), a, p))
                if pend_any is not None:
                    anyin.append((ts, max(0.0, (ts - pend_any).total_seconds()), a, p))
            pend_flag = pend_any = None
        if saw_flag or flagged:
            return flagged, "flagged"
        return anyin, "all_inbound"

    # ── SQL helpers ── #

    def _uptime(self, scope: Scope, agent_ids: list[int]) -> tuple[dict[int, float], bool]:
        """Seconds online per agent in range, and whether tracking covered the range."""
        try:
            first = self.db.query(func.min(AgentSession.started_at)).scalar()
        except Exception as exc:  # table not created yet
            logger.warning("agent_sessions unavailable: %s", exc)
            self.db.rollback()
            return {}, False
        if first is None or first >= scope.to:
            return {}, False
        now = datetime.utcnow()
        stale = now - timedelta(minutes=3)
        rows = (
            self.db.query(AgentSession.agent_id, AgentSession.started_at,
                          AgentSession.ended_at, AgentSession.last_seen_at)
            .filter(AgentSession.agent_id.in_(agent_ids or [0]),
                    AgentSession.started_at < scope.to,
                    or_(AgentSession.ended_at.is_(None), AgentSession.ended_at > scope.frm))
            .all()
        )
        out: dict[int, float] = {}
        for aid, start, end, seen in rows:
            if end is None:  # open span: alive if heartbeat is recent
                end = now if (seen and seen >= stale) else (seen or start)
            s, e = max(start, scope.frm), min(end, scope.to)
            if e > s:
                out[aid] = out.get(aid, 0.0) + (e - s).total_seconds()
        return out, True

    def _tickets_closed_by(self, scope: Scope, chat_ids: list[int] | None) -> dict[int, int]:
        """Who moved a ticket to resolved/closed in range (from the audit log)."""
        logs = (
            self.db.query(ActivityLog.agent_id, ActivityLog.entity_id, ActivityLog.metadata_)
            .filter(ActivityLog.action == "ticket_updated",
                    ActivityLog.created_at >= scope.frm, ActivityLog.created_at < scope.to,
                    ActivityLog.agent_id.isnot(None))
            .all()
        )
        closing = [(aid, tid) for aid, tid, meta in logs
                   if isinstance(meta, dict) and meta.get("status") in ("resolved", "closed")]
        if chat_ids is not None and closing:
            ok = {tid for (tid,) in self.db.query(Ticket.id).filter(
                Ticket.id.in_({t for _, t in closing}), Ticket.chat_id.in_(chat_ids or [0]))}
            closing = [(a, t) for a, t in closing if t in ok]
        seen: set[tuple] = set()
        out: dict[int, int] = {}
        for aid, tid in closing:
            if (aid, tid) in seen:
                continue
            seen.add((aid, tid))
            out[aid] = out.get(aid, 0) + 1
        return out

    def _ticket_query(self, chat_ids: list[int] | None):
        q = self.db.query(Ticket)
        if chat_ids is not None:
            q = q.filter(Ticket.chat_id.in_(chat_ids or [0]))
        return q

    # ── 1. Team ── #

    async def team(self, scope: Scope, online: set[int]) -> dict:
        facets = await self._message_facets(scope)
        pairs, basis = await self._response_pairs(scope)
        agents = self._agents(scope)
        ids = [a.id for a in agents]
        chat_ids = await self._scoped_chat_ids(scope)
        uptime, tracked = self._uptime(scope, ids)
        closed_by = self._tickets_closed_by(scope, chat_ids)

        sent: dict[int, int] = {}
        chats_by: dict[int, set] = {}
        all_chats: set = set()
        out_total = unattributed = 0
        for d in facets["by_chat"]:
            k, n = d["_id"], d["n"]
            all_chats.add(k.get("c"))
            if k.get("fm"):
                out_total += n
                a = k.get("a")
                if a is None:
                    unattributed += n
                else:
                    sent[a] = sent.get(a, 0) + n
                    chats_by.setdefault(a, set()).add(k.get("c"))
        initiated: dict[int, int] = {}
        for d in facets["initiated"]:
            if d.get("a") is not None:
                initiated[d["a"]] = initiated.get(d["a"], 0) + 1
        resp: dict[int, list[float]] = {}
        for _, secs, a, _p in pairs:
            if a is not None:
                resp.setdefault(a, []).append(secs)

        rows = []
        for a in agents:
            r = resp.get(a.id, [])
            rows.append({
                **self._agent_info(a, online),
                "active_chats": len(chats_by.get(a.id, ())),
                "messages_sent": sent.get(a.id, 0),
                "chats_initiated": initiated.get(a.id, 0),
                "tickets_closed": closed_by.get(a.id, 0),
                "responses_flagged": len(r) if basis == "flagged" else 0,
                "median_frt_seconds": _median_seconds(r),
                "uptime_seconds": round(uptime.get(a.id, 0.0)) if tracked else None,
            })

        if scope.agent_ids is None:
            tq = self._ticket_query(chat_ids).filter(
                Ticket.status.in_(_CLOSED), Ticket.resolved_at >= scope.frm, Ticket.resolved_at < scope.to)
            total = {
                "active_chats": len(all_chats),
                "messages_sent": out_total,
                "chats_initiated": len(facets["initiated"]),
                "tickets_closed": tq.count(),
                "responses_flagged": len(pairs) if basis == "flagged" else 0,
                "median_frt_seconds": _median_seconds([p[1] for p in pairs]),
            }
        else:
            sel = set(scope.agent_ids)
            total = {
                "active_chats": len(set().union(*[chats_by.get(i, set()) for i in sel]) if sel else set()),
                "messages_sent": sum(r["messages_sent"] for r in rows),
                "chats_initiated": sum(r["chats_initiated"] for r in rows),
                "tickets_closed": sum(r["tickets_closed"] for r in rows),
                "responses_flagged": sum(r["responses_flagged"] for r in rows),
                "median_frt_seconds": _median_seconds([p[1] for p in pairs if p[2] in sel]),
            }
        total["uptime_seconds"] = round(sum(uptime.values())) if tracked else None
        total["unattributed_messages"] = unattributed if scope.agent_ids is None else 0
        return {"range": self._range(scope), "frt_basis": basis, "total": total, "rows": rows}

    # ── 2. Phones ── #

    async def phones(self, scope: Scope) -> dict:
        facets = await self._message_facets(scope)
        pairs, basis = await self._response_pairs(scope)
        new_chats = await self.mdb.chats.aggregate([
            {"$match": self._chat_match(scope)},
            {"$group": {"_id": "$phone_id", "n": {"$sum": 1}}},
        ]).to_list(None)
        new_by = {d["_id"]: d["n"] for d in new_chats}
        sent_by: dict[int, int] = {}
        for d in facets["by_chat"]:
            k = d["_id"]
            if k.get("fm"):
                sent_by[k.get("p")] = sent_by.get(k.get("p"), 0) + d["n"]
        resp: dict[int, list[float]] = {}
        for _, secs, _a, p in pairs:
            resp.setdefault(p, []).append(secs)
        rows = []
        for ph in self._phones(scope, set(new_by) | set(sent_by) | set(resp)):
            r = resp.get(ph.id, [])
            rows.append({
                "phone_id": ph.id, "name": ph.name, "phone_number": ph.phone_number,
                "status": ph.waha_status,
                "new_chats": new_by.get(ph.id, 0),
                "messages_sent": sent_by.get(ph.id, 0),
                "responses_flagged": len(r) if basis == "flagged" else 0,
                "median_frt_seconds": _median_seconds(r),
            })
        total = {
            "new_chats": sum(new_by.values()),
            "messages_sent": sum(sent_by.values()),
            "responses_flagged": len(pairs) if basis == "flagged" else 0,
            "median_frt_seconds": _median_seconds([p[1] for p in pairs]),
        }
        return {"range": self._range(scope), "frt_basis": basis, "total": total, "rows": rows}

    # ── 3. Chats ── #

    def _chat_match(self, scope: Scope) -> dict:
        m: dict = {"created_at": {"$gte": scope.frm, "$lt": scope.to}}
        if scope.phone_ids is not None:
            m["phone_id"] = {"$in": scope.phone_ids}
        if scope.chat_id is not None:
            m["id"] = scope.chat_id
        return m

    async def chats(self, scope: Scope) -> dict:
        bk = _Buckets(scope)
        grouped = await self.mdb.chats.aggregate([
            {"$match": self._chat_match(scope)},
            {"$group": {"_id": {"b": scope.trunc("$created_at"), "g": {"$eq": ["$is_group", True]}},
                        "n": {"$sum": 1}}},
        ]).to_list(None)
        ind, grp = bk.zeros(), bk.zeros()
        n_ind = n_grp = 0
        for d in grouped:
            i = bk.key(d["_id"].get("b"))
            if d["_id"].get("g"):
                n_grp += d["n"]
                if i is not None:
                    grp[i] += d["n"]
            else:
                n_ind += d["n"]
                if i is not None:
                    ind[i] += d["n"]
        top = await self.mdb.messages.aggregate([
            {"$match": scope.message_match()},
            {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ], **ALLOW_DISK).to_list(10)
        names = {}
        if top:
            async for c in self.mdb.chats.find({"id": {"$in": [t["_id"] for t in top]}},
                                               {"id": 1, "name": 1, "chat_wid": 1, "is_group": 1}):
                names[c["id"]] = c
        most = []
        for t in top:
            c = names.get(t["_id"]) or {}
            most.append({"chat_id": t["_id"], "name": c.get("name") or (c.get("chat_wid") or "").split("@")[0],
                         "is_group": bool(c.get("is_group")), "messages": t["n"]})
        return {
            "range": self._range(scope),
            "total": {"new_chats": n_ind + n_grp, "new_individual": n_ind, "new_groups": n_grp},
            "series": {"buckets": bk.labels(), "individual": ind, "group": grp},
            "most_active": most,
        }

    # ── 4. Tickets ── #

    async def tickets(self, scope: Scope) -> dict:
        bk = _Buckets(scope)
        chat_ids = await self._scoped_chat_ids(scope)
        now = datetime.utcnow()
        base = self._ticket_query(chat_ids)
        if scope.agent_ids is not None:
            base = base.filter(Ticket.assigned_to.in_(scope.agent_ids or [0]))
        in_range = base.filter(Ticket.created_at >= scope.frm, Ticket.created_at < scope.to).all()

        def is_closed(t: Ticket) -> bool:
            return t.status in _CLOSED

        def res_secs(t: Ticket) -> float | None:
            if is_closed(t) and t.resolved_at and t.created_at:
                return max(0.0, (t.resolved_at - t.created_at).total_seconds())
            return None

        resolved_in_range = base.filter(Ticket.status.in_(_CLOSED), Ticket.resolved_at >= scope.frm,
                                        Ticket.resolved_at < scope.to).all()
        res_times = [s for s in (res_secs(t) for t in resolved_in_range) if s is not None]
        total = {
            "total": len(in_range),
            "unresolved": sum(1 for t in in_range if not is_closed(t)),
            "resolved": sum(1 for t in in_range if is_closed(t)),
            "unassigned": sum(1 for t in in_range if not is_closed(t) and not t.assigned_to),
            "avg_resolution_seconds": round(sum(res_times) / len(res_times), 1) if res_times else None,
        }

        # Series: created / closed per bucket; unresolved = open at bucket end
        created, closed, unresolved = bk.zeros(), bk.zeros(), bk.zeros()
        for t in in_range:
            i = bk.index(t.created_at)
            if i is not None:
                created[i] += 1
        for t in resolved_in_range:
            i = bk.index(t.resolved_at)
            if i is not None:
                closed[i] += 1
        live = base.filter(Ticket.created_at < scope.to, or_(
            Ticket.status.in_(_OPEN), Ticket.resolved_at.is_(None), Ticket.resolved_at >= scope.frm,
        )).with_entities(Ticket.created_at, Ticket.resolved_at, Ticket.status).all()
        ends = bk.starts[1:] + [scope.to]
        for i, end in enumerate(ends):
            edge = min(end, now)
            unresolved[i] = sum(
                1 for c, r, st in live
                if c and c < edge and not (st in _CLOSED and r and r < edge)
            )

        # Per assignee (tickets created in range)
        agents = {a.id: a for a in self._agents(scope)}
        per: dict = {}
        for t in in_range:
            key = t.assigned_to if t.assigned_to in agents else None
            if t.assigned_to and key is None:
                key = t.assigned_to  # inactive / filtered-out assignee keeps its own row
            per.setdefault(key, []).append(t)

        def row_for(ts: list[Ticket]) -> dict:
            open_ = [t for t in ts if not is_closed(t)]
            rt = [s for s in (res_secs(t) for t in ts) if s is not None]
            age = {"lt_1h": 0, "lt_24h": 0, "lt_7d": 0, "gt_7d": 0}
            for t in open_:
                h = (now - t.created_at).total_seconds() / 3600 if t.created_at else 0
                age["lt_1h" if h < 1 else "lt_24h" if h < 24 else "lt_7d" if h < 168 else "gt_7d"] += 1
            return {"total": len(ts), "open": len(open_), "closed": len(ts) - len(open_),
                    "avg_resolution_seconds": round(sum(rt) / len(rt), 1) if rt else None,
                    "unresolved_age": age}

        online: set[int] = set()
        rows = []
        for aid, a in agents.items():
            rows.append({**self._agent_info(a, online), **row_for(per.get(aid, []))})
        extra = [k for k in per if k is not None and k not in agents]
        if extra:
            names = {a.id: a for a in self.db.query(Agent).filter(Agent.id.in_(extra))}
            for k in extra:
                a = names.get(k)
                info = self._agent_info(a, online) if a else {"agent_id": k, "name": f"Member #{k}",
                                                              "email": "", "avatar_color": None, "online": False}
                rows.append({**info, **row_for(per[k])})
        if per.get(None):
            rows.append({"agent_id": None, "name": "Unassigned", "email": "", "avatar_color": None,
                         "online": False, **row_for(per[None])})
        return {
            "range": self._range(scope),
            "total": {**total, **{k: v for k, v in row_for(in_range).items() if k == "unresolved_age"}},
            "series": {"buckets": bk.labels(), "created": created, "closed": closed, "unresolved": unresolved},
            "rows": rows,
        }

    # ── 5. Messages ── #

    async def messages(self, scope: Scope) -> dict:
        bk = _Buckets(scope)
        facets = await self._message_facets(scope)
        pairs, basis = await self._response_pairs(scope)
        out_s, in_s, act_s, resp_s = bk.zeros(), bk.zeros(), bk.zeros(), bk.zeros()
        n_out = n_in = 0
        for d in facets["by_bucket"]:
            k, n = d["_id"], d["n"]
            i = bk.key(k.get("b"))
            if k.get("fm"):
                if scope.agent_ids is not None and k.get("a") not in scope.agent_ids:
                    continue
                n_out += n
                if i is not None:
                    out_s[i] += n
            else:
                n_in += n
                if i is not None:
                    in_s[i] += n
        for d in facets["by_bucket_chat"]:
            if scope.agent_ids is not None and not (set(d.get("agents") or []) & set(scope.agent_ids)):
                continue
            i = bk.key(d["_id"].get("b"))
            if i is not None:
                act_s[i] += 1

        chats_by: dict[int, set] = {}
        sent: dict[int, int] = {}
        all_chats: set = set()
        for d in facets["by_chat"]:
            k = d["_id"]
            all_chats.add(k.get("c"))
            if k.get("fm") and k.get("a") is not None:
                sent[k["a"]] = sent.get(k["a"], 0) + d["n"]
                chats_by.setdefault(k["a"], set()).add(k.get("c"))

        sel_pairs = [p for p in pairs if scope.agent_ok(p[2])] if scope.agent_ids is not None else pairs
        if basis == "flagged":
            for ts, *_ in sel_pairs:
                i = bk.index(ts)
                if i is not None:
                    resp_s[i] += 1
        resp: dict[int, list[float]] = {}
        for _, secs, a, _p in pairs:
            if a is not None:
                resp.setdefault(a, []).append(secs)
        rows = []
        for a in self._agents(scope):
            r = resp.get(a.id, [])
            rows.append({
                **self._agent_info(a, set()),
                "active_chats": len(chats_by.get(a.id, ())),
                "messages_sent": sent.get(a.id, 0),
                "responses_flagged": len(r) if basis == "flagged" else 0,
                "median_frt_seconds": _median_seconds(r),
            })
        active = (len(all_chats) if scope.agent_ids is None else
                  len(set().union(*[chats_by.get(i, set()) for i in scope.agent_ids]) if scope.agent_ids else set()))
        return {
            "range": self._range(scope),
            "frt_basis": basis,
            "total": {
                "active_chats": active,
                "outgoing": n_out,
                "incoming": n_in,
                "responses_flagged": len(sel_pairs) if basis == "flagged" else 0,
                "median_frt_seconds": _median_seconds([p[1] for p in sel_pairs]),
            },
            "series": {"buckets": bk.labels(), "active_chats": act_s, "outgoing": out_s,
                       "incoming": in_s, "responses_flagged": resp_s},
            "rows": rows,
        }

    # ── 6. Members (group_events) ── #

    async def members(self, scope: Scope) -> dict:
        bk = _Buckets(scope)
        kinds = {"join": "joined", "add": "joined", "leave": "left", "remove": "removed"}
        series = {"joined": bk.zeros(), "left": bk.zeros(), "removed": bk.zeros()}
        total = {"joined": 0, "left": 0, "removed": 0}
        try:
            grouped = await self.mdb.group_events.aggregate([
                {"$match": {**scope.mongo_base("timestamp"), "type": {"$in": list(kinds)}}},
                {"$group": {"_id": {"b": scope.trunc(), "t": "$type"}, "n": {"$sum": 1}}},
            ]).to_list(None)
        except Exception as exc:  # collection missing / unexpected shape
            logger.warning("group_events aggregation failed: %s", exc)
            grouped = []
        for d in grouped:
            key = kinds.get(d["_id"].get("t"))
            if not key:
                continue
            total[key] += d["n"]
            i = bk.key(d["_id"].get("b"))
            if i is not None:
                series[key][i] += d["n"]
        return {"range": self._range(scope), "total": total,
                "series": {"buckets": bk.labels(), **series}}

    # ── Chat picker ── #

    async def chat_options(self, phone_ids: list[int] | None, q: str, limit: int = 20) -> list[dict]:
        import re
        m: dict = {}
        if phone_ids is not None:
            m["phone_id"] = {"$in": phone_ids}
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            m["$or"] = [{"name": rx}, {"chat_wid": rx}]
        docs = await self.mdb.chats.find(m, {"id": 1, "name": 1, "chat_wid": 1, "is_group": 1, "phone_id": 1}) \
            .sort("last_message_at", -1).limit(limit).to_list(limit)
        return [{"id": d["id"], "name": d.get("name") or (d.get("chat_wid") or "").split("@")[0],
                 "is_group": bool(d.get("is_group")), "phone_id": d.get("phone_id")} for d in docs]

    # ── Legacy: used by the AI agent's workspace summary ── #

    async def get_dashboard_metrics(self) -> dict:
        chats = self.mdb.chats
        total_chats = await chats.count_documents({})
        unread_chats = await chats.count_documents({"unread_count": {"$gt": 0}})
        flagged_chats = await chats.count_documents({"is_flagged": True})
        open_tickets = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.OPEN).scalar() or 0
        in_progress = self.db.query(func.count(Ticket.id)).filter(Ticket.status == TicketStatus.IN_PROGRESS).scalar() or 0
        active_agents = self.db.query(func.count(Agent.id)).filter(Agent.is_active == True).scalar() or 0  # noqa: E712
        return {
            "total_chats": total_chats,
            "unread_chats": unread_chats,
            "flagged_chats": flagged_chats,
            "open_tickets": open_tickets,
            "in_progress_tickets": in_progress,
            "online_agents": active_agents,
        }


async def ensure_analytics_indexes() -> None:
    """Indexes for the range scans above; idempotent, never fails startup."""
    from app.db.mongo import _ensure_index, get_mongo_db
    db = get_mongo_db()
    await _ensure_index(db.messages, [("phone_id", 1), ("timestamp", 1)])
    await _ensure_index(db.chats, [("phone_id", 1), ("created_at", 1)])
    await _ensure_index(db.group_events, [("phone_id", 1), ("timestamp", 1)])


async def analytics_startup() -> None:
    """Lifespan hook: analytics indexes + presence tracking (dangling spans, heartbeat)."""
    from app.services.presence_service import start_presence_tracking
    try:
        await ensure_analytics_indexes()
    except Exception as exc:
        logger.warning("Analytics index setup failed: %s", exc)
    await start_presence_tracking()
