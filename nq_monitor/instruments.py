"""Explicit instrument resolution; malformed labels are never silently fixed."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    requested_label: str
    source_symbol: str | None
    status: str
    note: str


def resolve_instrument(label: str) -> Instrument:
    normalized = label.upper()
    if normalized == "NQ":
        return Instrument(label, "NQ=F", "VERIFIED_SYMBOL", "CME E-mini Nasdaq-100 proxy")
    if normalized == "MNQ":
        return Instrument(label, "MNQ=F", "VERIFIED_SYMBOL", "CME Micro E-mini Nasdaq-100 proxy")
    if normalized == "NMQ":
        return Instrument(label, None, "UNRESOLVED_SYMBOL", "No standard futures symbol verified; MNQ or NNQ are possible intended symbols and require confirmation")
    return Instrument(label, None, "UNRESOLVED_SYMBOL", "No configured read-only source symbol")
