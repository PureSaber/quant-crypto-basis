from __future__ import annotations

from copy import deepcopy

import pytest
from quant_data_kit import (
    BookDeltaEvent,
    BookSnapshotEvent,
    FundingRateEvent,
    MarkPriceEvent,
    QuoteEvent,
    TradeEvent,
)
from quant_data_kit.exceptions import ValidationError

from quant_crypto_basis.events import market_event_from_payload
from quant_crypto_basis.fixtures import FixtureLoader


def test_all_normalized_fixture_types_decode_to_qdk_market_events() -> None:
    batch = FixtureLoader().load("binance")
    decoded = [market_event_from_payload(record) for record in batch.records]
    assert {type(event) for event in decoded} == {
        QuoteEvent,
        TradeEvent,
        BookSnapshotEvent,
        BookDeltaEvent,
        FundingRateEvent,
        MarkPriceEvent,
    }
    assert tuple(decoded) == batch.events
    assert all(event.received_at == event.available_at for event in decoded)


def test_decoder_rejects_unknown_incomplete_and_invalid_fixed_payloads() -> None:
    record = dict(FixtureLoader().load("binance").records[0])
    unknown = dict(record, event_type="mystery")
    with pytest.raises(ValidationError, match="unsupported"):
        market_event_from_payload(unknown)
    incomplete = deepcopy(record)
    del incomplete["event_id"]
    with pytest.raises(ValidationError, match="identity"):
        market_event_from_payload(incomplete)
    bad_fixed = deepcopy(record)
    bad_fixed["price"] = {"units": True, "scale": 2}
    with pytest.raises(ValidationError, match="units"):
        market_event_from_payload(bad_fixed)
    with pytest.raises(ValidationError, match="mapping"):
        market_event_from_payload([])  # type: ignore[arg-type]


def test_decoder_rejects_incomplete_book_level_and_invalid_enum() -> None:
    records = FixtureLoader().load("okx").records
    book = deepcopy(next(dict(item) for item in records if item["event_type"] == "book_snapshot"))
    book["bids"][0] = {"price": book["bids"][0]["price"]}
    with pytest.raises(ValidationError, match="book level"):
        market_event_from_payload(book)
    trade = deepcopy(next(dict(item) for item in records if item["event_type"] == "trade"))
    trade["aggressor_side"] = "invalid"
    with pytest.raises(ValidationError, match="invalid normalized"):
        market_event_from_payload(trade)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event_time", 1, "event_time must be an ISO-8601 string"),
        ("event_time", "not-a-time", "event_time must be an ISO-8601 string"),
        ("price", {"units": True, "scale": 2}, "price.units"),
        ("price", {"units": 1, "scale": False}, "price.scale"),
    ],
)
def test_decoder_rejects_invalid_temporal_and_fixed_point_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    record = dict(
        next(
            item
            for item in FixtureLoader().load("binance").records
            if item["event_type"] == "trade"
        )
    )
    record[field] = value
    with pytest.raises(ValidationError, match=message):
        market_event_from_payload(record)


def test_decoder_rejects_invalid_base_identity_and_book_level_shape() -> None:
    record = dict(FixtureLoader().load("binance").records[0])
    del record["trading_day"]
    with pytest.raises(ValidationError, match="identity"):
        market_event_from_payload(record)

    book = dict(
        next(
            item
            for item in FixtureLoader().load("binance").records
            if item["event_type"] == "book_snapshot"
        )
    )
    book["bids"][0] = []
    with pytest.raises(ValidationError, match="book level"):
        market_event_from_payload(book)
