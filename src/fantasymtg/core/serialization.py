"""Convert a Game aggregate to and from JSON-compatible dicts."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .config import GameConfig
from .models import (
    Game,
    GameStatus,
    Holding,
    PendingAction,
    PendingKind,
    PendingStatus,
    Player,
    TradeRecord,
)
from .pricing import CardQuote
from .results import (
    BudgetToppedOff,
    GameEnded,
    Leaderboard,
    Notification,
    OutboxEntry,
    PendingTimedOut,
    Standing,
    WeeklyLeaderboard,
)

# Bump when the stored format changes, and teach game_from_dict to read older versions.
#   1: initial format
#   2: adds outbox, outbox_seq, final_prices_due_since
SCHEMA_VERSION = 2
SUPPORTED_VERSIONS = {1, 2}


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def game_to_dict(game: Game) -> dict[str, Any]:
    cfg = asdict(game.config)
    cfg["end_date"] = _dt(game.config.end_date)
    return {
        "schema": SCHEMA_VERSION,
        "id": game.id,
        "channel_key": game.channel_key,
        "admin_id": game.admin_id,
        "created_at": _dt(game.created_at),
        "config": cfg,
        "status": game.status.value,
        "started_at": _dt(game.started_at),
        "ended_at": _dt(game.ended_at),
        "players": [
            {
                "user_id": p.user_id,
                "name": p.name,
                "balance": p.balance,
                "holdings": [asdict(h) for h in p.holdings.values()],
            }
            for p in game.players
        ],
        "pending": [_pending_to_dict(p) for p in game.pending.values()],
        "trades": [{**asdict(t), "executed_at": _dt(t.executed_at)} for t in game.trades],
        "last_top_off_at": _dt(game.last_top_off_at),
        "last_leaderboard_at": _dt(game.last_leaderboard_at),
        "pending_seq": game.pending_seq,
        "outbox": [_outbox_to_dict(e) for e in game.outbox],
        "outbox_seq": game.outbox_seq,
        "final_prices_due_since": _dt(game.final_prices_due_since),
    }


def _leaderboard_to_dict(board: Leaderboard) -> dict[str, Any]:
    return {"standings": [asdict(s) for s in board.standings], "final": board.final}


def _leaderboard_from_dict(data: dict[str, Any]) -> Leaderboard:
    return Leaderboard([Standing(**s) for s in data["standings"]], data["final"])


def _notification_to_dict(n: Notification) -> dict[str, Any]:
    if isinstance(n, PendingTimedOut):
        return {"kind": "pending_timed_out", "channel_key": n.channel_key,
                "pending": _pending_to_dict(n.pending)}
    if isinstance(n, BudgetToppedOff):
        return {"kind": "budget_topped_off", "channel_key": n.channel_key,
                "amount": n.amount, "at": _dt(n.at)}
    if isinstance(n, WeeklyLeaderboard):
        return {"kind": "weekly_leaderboard", "channel_key": n.channel_key,
                "leaderboard": _leaderboard_to_dict(n.leaderboard)}
    if isinstance(n, GameEnded):
        return {"kind": "game_ended", "channel_key": n.channel_key,
                "leaderboard": _leaderboard_to_dict(n.leaderboard)}
    raise TypeError(f"cannot serialize {n!r}")


def _notification_from_dict(d: dict[str, Any]) -> Notification:
    kind, key = d["kind"], d["channel_key"]
    if kind == "pending_timed_out":
        return PendingTimedOut(key, _pending_from_dict(d["pending"]))
    if kind == "budget_topped_off":
        return BudgetToppedOff(key, d["amount"], _parse_dt(d["at"]))
    if kind == "weekly_leaderboard":
        return WeeklyLeaderboard(key, _leaderboard_from_dict(d["leaderboard"]))
    if kind == "game_ended":
        return GameEnded(key, _leaderboard_from_dict(d["leaderboard"]))
    raise ValueError(f"unknown notification kind: {kind}")


def _outbox_to_dict(e: OutboxEntry) -> dict[str, Any]:
    return {"id": e.id, "game_id": e.game_id, "created_at": _dt(e.created_at),
            "notification": _notification_to_dict(e.notification)}


def _outbox_from_dict(d: dict[str, Any]) -> OutboxEntry:
    return OutboxEntry(d["id"], d["game_id"], _parse_dt(d["created_at"]),
                       _notification_from_dict(d["notification"]))


def _pending_to_dict(p: PendingAction) -> dict[str, Any]:
    return {
        "id": p.id,
        "kind": p.kind.value,
        "user_id": p.user_id,
        "created_at": _dt(p.created_at),
        "expires_at": _dt(p.expires_at),
        "status": p.status.value,
        "card": asdict(p.card) if p.card else None,
        "quantity": p.quantity,
        "unit_price": p.unit_price,
        "fee": p.fee,
        "setting_name": p.setting_name,
        "setting_value": p.setting_value,
        "external_ref": p.external_ref,
        "timeout_announced": p.timeout_announced,
        "resolved_at": _dt(p.resolved_at),
    }


def _pending_from_dict(p: dict[str, Any]) -> PendingAction:
    return PendingAction(
        id=p["id"],
        kind=PendingKind(p["kind"]),
        user_id=p["user_id"],
        created_at=_parse_dt(p["created_at"]),
        expires_at=_parse_dt(p["expires_at"]),
        status=PendingStatus(p["status"]),
        card=CardQuote(**p["card"]) if p["card"] else None,
        quantity=p["quantity"],
        unit_price=p["unit_price"],
        fee=p["fee"],
        setting_name=p["setting_name"],
        setting_value=p["setting_value"],
        external_ref=p["external_ref"],
        timeout_announced=p["timeout_announced"],
        resolved_at=_parse_dt(p["resolved_at"]),
    )


def game_from_dict(data: dict[str, Any]) -> Game:
    if data.get("schema") not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"unsupported game schema version {data.get('schema')}; "
            "this database was written by a newer version of Fantasy MTG"
        )
    cfg = dict(data["config"])
    cfg["end_date"] = _parse_dt(cfg.get("end_date"))
    players = [
        Player(
            user_id=p["user_id"],
            name=p["name"],
            balance=p["balance"],
            holdings={h["card_id"]: Holding(**h) for h in p["holdings"]},
        )
        for p in data["players"]
    ]
    pending = {p["id"]: _pending_from_dict(p) for p in data["pending"]}
    trades = [
        TradeRecord(**{**t, "executed_at": _parse_dt(t["executed_at"])}) for t in data["trades"]
    ]
    return Game(
        id=data["id"],
        channel_key=data["channel_key"],
        admin_id=data["admin_id"],
        created_at=_parse_dt(data["created_at"]),
        config=GameConfig(**cfg),
        status=GameStatus(data["status"]),
        started_at=_parse_dt(data["started_at"]),
        ended_at=_parse_dt(data["ended_at"]),
        players=players,
        pending=pending,
        trades=trades,
        last_top_off_at=_parse_dt(data["last_top_off_at"]),
        last_leaderboard_at=_parse_dt(data["last_leaderboard_at"]),
        pending_seq=data["pending_seq"],
        # added in schema 2
        outbox=[_outbox_from_dict(e) for e in data.get("outbox", [])],
        outbox_seq=data.get("outbox_seq", 0),
        final_prices_due_since=_parse_dt(data.get("final_prices_due_since")),
    )
