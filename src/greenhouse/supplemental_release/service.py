from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .contracts import ReleaseCandidate, RecordError, Rejection
from .analysis import (
    ReleaseVerdict,
    adjudicate,
    adjudicate_batch,
    conservative_floor,
    dry_day_reference,
)


@dataclass(frozen=True, slots=True)
class Intake:
    records: tuple[ReleaseCandidate, ...]
    rejected: tuple[Rejection, ...]
    read_count: int

    @property
    def accepted_count(self) -> int:
        return len(self.records)

    def rejection_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(item.reason for item in self.rejected).items()))

    def evidence(self) -> dict:
        return {
            "read": self.read_count,
            "accepted": self.accepted_count,
            "excluded": len(self.rejected),
            "reason_counts": self.rejection_counts(),
            "rejected": [item.evidence() for item in self.rejected],
        }


def inspect(documents: Iterable[Mapping]) -> Intake:
    accepted: list[ReleaseCandidate] = []
    refused: list[Rejection] = []
    read_count = 0
    for read_count, document in enumerate(documents, start=1):
        try:
            entry = ReleaseCandidate.parse(document)
        except RecordError as exc:
            raw_key = document.get('zone') if isinstance(document, Mapping) else None
            identifier = str(raw_key)[:140] if raw_key is not None else ""
            refused.append(Rejection(read_count, identifier, exc.field, exc.reason))
        else:
            accepted.append(entry)
    return Intake(tuple(accepted), tuple(refused), read_count)


def build_candidate(
    *,
    zone: str,
    crop: str,
    pump: str,
    gap: int,
    history: list[tuple[float, float]],
    asset_limit: float,
    per_min: int,
    window: int,
) -> ReleaseCandidate:
    """把流水线各环节的原始证据组装成补灌裁决候选。

    history 为该棚区在业务日之前已发布的 (含水率中位数, 当日补水量升) 序列，
    最近一条的含水率用于过湿联锁，干燥日子集给出保守补水参考。
    """
    reference, support = dry_day_reference(history)
    last_moisture = history[-1][0] if history else 0.0
    return ReleaseCandidate(
        zone=zone,
        crop=crop,
        pump=pump,
        gap=gap,
        history_support=support,
        last_moisture=last_moisture,
        dry_reference_l=reference,
        asset_limit=asset_limit,
        per_min=per_min,
        window=window,
    )


def run_adjudication(candidates: Iterable[ReleaseCandidate]) -> list[ReleaseVerdict]:
    return adjudicate_batch(list(candidates))
