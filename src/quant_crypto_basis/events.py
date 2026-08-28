"""Strict decoding of QDK v2 normalized fixture payloads into QDK MarketEvent values."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from quant_data_kit import (
    AggressorSide,
    BookAction,
    BookDeltaEvent,
    BookLevel,
    BookSide,
    BookSnapshotEvent,
    FixedPoint,
    FundingRateEvent,
    MarketEvent,
    MarkPriceEvent,
    QuoteEvent,
    TradeEvent,
    ensure_utc_datetime,
)
from quant_data_kit.exceptions import ValidationError


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO-8601 string") from exc
    return ensure_utc_datetime(parsed, field=field)


def _fixed(value: object, field: str) -> FixedPoint:
    if not isinstance(value, Mapping) or set(value) != {"units", "scale"}:
        raise ValidationError(f"{field} must be a FixedPoint payload")
    units, scale = value["units"], value["scale"]
    if isinstance(units, bool) or not isinstance(units, int):
        raise ValidationError(f"{field}.units must be an integer")
    if isinstance(scale, bool) or not isinstance(scale, int):
        raise ValidationError(f"{field}.scale must be an integer")
    return FixedPoint(units, scale)


def _base(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        trading_day = date.fromisoformat(str(payload["trading_day"]))
        return {
            "event_id": str(payload["event_id"]),
            "instrument_id": str(payload["instrument_id"]),
            "event_time": _time(payload["event_time"], "event_time"),
            "received_at": _time(payload["received_at"], "received_at"),
            "available_at": _time(payload["available_at"], "available_at"),
            "source": str(payload["source"]),
            "trading_day": trading_day,
            "session_id": str(payload["session_id"]),
            "sequence": payload["sequence"],
        }
    except (KeyError, ValueError) as exc:
        raise ValidationError("normalized event is missing a valid identity field") from exc


def market_event_from_payload(payload: Mapping[str, Any]) -> MarketEvent:
    """Decode one already-schema-validated QDK payload without changing semantics."""
    if not isinstance(payload, Mapping):
        raise ValidationError("normalized event payload must be a mapping")
    event_type = payload.get("event_type")
    base = _base(payload)
    try:
        if event_type == "quote":
            return QuoteEvent(
                **base,
                bid_price=_fixed(payload["bid_price"], "bid_price"),
                bid_quantity=_fixed(payload["bid_quantity"], "bid_quantity"),
                ask_price=_fixed(payload["ask_price"], "ask_price"),
                ask_quantity=_fixed(payload["ask_quantity"], "ask_quantity"),
            )
        if event_type == "trade":
            return TradeEvent(
                **base,
                price=_fixed(payload["price"], "price"),
                quantity=_fixed(payload["quantity"], "quantity"),
                aggressor_side=AggressorSide(str(payload["aggressor_side"])),
            )
        if event_type == "book_snapshot":
            return BookSnapshotEvent(
                **base,
                bids=tuple(_book_level(item) for item in payload["bids"]),
                asks=tuple(_book_level(item) for item in payload["asks"]),
            )
        if event_type == "book_delta":
            return BookDeltaEvent(
                **base,
                side=BookSide(str(payload["side"])),
                action=BookAction(str(payload["action"])),
                price=_fixed(payload["price"], "price"),
                quantity=_fixed(payload["quantity"], "quantity"),
                previous_sequence=int(payload["previous_sequence"]),
            )
        if event_type == "funding_rate":
            return FundingRateEvent(
                **base,
                rate=float(payload["rate"]),
                interval_start=_time(payload["interval_start"], "interval_start"),
                interval_end=_time(payload["interval_end"], "interval_end"),
            )
        if event_type == "mark_price":
            return MarkPriceEvent(**base, price=_fixed(payload["price"], "price"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"invalid normalized {event_type!r} event payload") from exc
    raise ValidationError(f"unsupported normalized fixture event_type: {event_type!r}")


def _book_level(payload: Mapping[str, Any]) -> BookLevel:
    if not isinstance(payload, Mapping):
        raise ValidationError("book level must be a mapping")
    try:
        return BookLevel(
            price=_fixed(payload["price"], "book.price"),
            quantity=_fixed(payload["quantity"], "book.quantity"),
            order_count=payload["order_count"],
        )
    except KeyError as exc:
        raise ValidationError("book level is incomplete") from exc
