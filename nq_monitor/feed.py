"""Read-only Yahoo Finance 1-minute adapter with strict normalization."""

from datetime import datetime, timezone
from math import isfinite
from typing import Iterable, Sequence

from .models import Candle


class FeedError(RuntimeError):
    pass


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be datetime or ISO string")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def bars_from_records(records: Iterable[dict], symbol: str) -> list[Candle]:
    by_timestamp: dict[datetime, Candle] = {}
    for row in records:
        timestamp = _parse_timestamp(row["timestamp"])
        values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
        if not all(isfinite(v) for v in values):
            raise ValueError("non-finite OHLCV value")
        open_, high, low, close, volume = values
        if volume < 0 or high < max(open_, close) or low > min(open_, close) or low > high:
            raise ValueError("invalid OHLCV relationship")
        by_timestamp[timestamp] = Candle(timestamp, open_, high, low, close, volume, symbol)
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def align_by_timestamp(a: Sequence[Candle], b: Sequence[Candle]) -> tuple[list[Candle], list[Candle]]:
    b_by_ts = {bar.timestamp: bar for bar in b}
    pairs = [(bar, b_by_ts[bar.timestamp]) for bar in a if bar.timestamp in b_by_ts]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _frame_records(frame, symbol: str):
    if frame is None or frame.empty:
        return []
    records = []
    for timestamp, row in frame.iterrows():
        def value(name):
            item = row[name]
            if hasattr(item, "iloc"):
                item = item.iloc[0]
            return float(item)
        records.append({
            "timestamp": timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp,
            "open": value("Open"),
            "high": value("High"),
            "low": value("Low"),
            "close": value("Close"),
            "volume": value("Volume"),
        })
    return bars_from_records(records, symbol)


class YahooMinuteFeed:
    """Yahoo Finance is a delayed, read-only proxy, not an execution feed."""

    def __init__(self, period: str = "2d"):
        self.period = period

    def fetch(self, source_symbol: str, display_symbol: str, now: datetime | None = None) -> list[Candle]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise FeedError("yfinance is not installed in the monitor interpreter") from exc
        try:
            frame = yf.download(
                source_symbol,
                period=self.period,
                interval="1m",
                auto_adjust=False,
                prepost=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            raise FeedError(f"Yahoo fetch failed for {source_symbol}: {type(exc).__name__}: {exc}") from exc
        try:
            return _frame_records(frame, display_symbol)
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedError(f"Yahoo response invalid for {source_symbol}: {exc}") from exc
