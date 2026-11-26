from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from unicodedata import normalize


class RecordError(ValueError):
    def __init__(self, field: str, reason: str):
        super().__init__(f"{field}:{reason}")
        self.field = field
        self.reason = reason


def label(document: Mapping, key: str, *, max_chars: int) -> str:
    raw = document.get(key)
    if not isinstance(raw, str):
        raise RecordError(key, "TEXT_REQUIRED")
    value = normalize("NFKC", raw).strip()
    if not value or len(value) > max_chars:
        raise RecordError(key, "TEXT_LENGTH")
    if not all(ch.isprintable() for ch in value):
        raise RecordError(key, "TEXT_CONTROL")
    return value


def magnitude(document: Mapping, key: str, lower: float, upper: float, *, whole: bool) -> int | float:
    raw = document.get(key)
    if isinstance(raw, bool) or raw is None:
        raise RecordError(key, "NUMBER_REQUIRED")
    try:
        number = float(raw)
    except (ValueError, TypeError, OverflowError) as error:
        raise RecordError(key, "NUMBER_INVALID") from error
    if not isfinite(number):
        raise RecordError(key, "NUMBER_NONFINITE")
    if number < lower or number > upper:
        raise RecordError(key, "NUMBER_RANGE")
    if whole:
        if not number.is_integer():
            raise RecordError(key, "INTEGER_REQUIRED")
        return int(number)
    return number


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """根区读数缺失但仍可能缺水、等待补灌裁决的棚区。"""

    zone: str
    crop: str
    pump: str
    gap: int
    history_support: int
    last_moisture: float
    dry_reference_l: float
    asset_limit: float
    per_min: int
    window: int

    @classmethod
    def parse(cls, document: Mapping) -> "ReleaseCandidate":
        if not isinstance(document, Mapping):
            raise RecordError("document", "OBJECT_REQUIRED")
        zone = label(document, 'zone', max_chars=32)
        crop = label(document, 'crop', max_chars=28)
        pump = label(document, 'pump', max_chars=20)
        gap = magnitude(document, 'gap', 0, 1440, whole=True)
        history_support = magnitude(document, 'history_support', 0, 400, whole=True)
        last_moisture = magnitude(document, 'last_moisture', 0, 1, whole=False)
        dry_reference_l = magnitude(document, 'dry_reference_l', 0, 100000, whole=False)
        asset_limit = magnitude(document, 'asset_limit', 0, 100000, whole=False)
        per_min = magnitude(document, 'per_min', 1, 1000, whole=True)
        window = magnitude(document, 'window', 0, 480, whole=True)
        return cls(
            zone=zone,
            crop=crop,
            pump=pump,
            gap=gap,
            history_support=history_support,
            last_moisture=last_moisture,
            dry_reference_l=dry_reference_l,
            asset_limit=asset_limit,
            per_min=per_min,
            window=window,
        )

    def evidence(self) -> dict:
        return {
            'zone': self.zone,
            'crop': self.crop,
            'pump': self.pump,
            'gap': self.gap,
            'history_support': self.history_support,
            'last_moisture': self.last_moisture,
            'dry_reference_l': self.dry_reference_l,
            'asset_limit': self.asset_limit,
            'per_min': self.per_min,
            'window': self.window,
        }


@dataclass(frozen=True, slots=True)
class Rejection:
    row: int
    source: str
    field: str
    reason: str

    def evidence(self) -> dict:
        return {"row": self.row, "source": self.source, "field": self.field, "reason": self.reason}
