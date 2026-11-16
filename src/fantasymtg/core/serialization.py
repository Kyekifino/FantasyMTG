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

SCHEMA_VERSION = 1


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
    }


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


def game_from_dict(data: dict[str, Any]) -> Game:
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"unsupported game schema version: {data.get('schema')}")
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
    pending = {}
    for p in data["pending"]:
        pending[p["id"]] = PendingAction(
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
    )
