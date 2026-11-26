"""补灌放行台账：按业务日固化首次裁决。

台账是只追加的 NDJSON 文件，每个业务日只承认当天首次写入的裁决。同一天重跑
清单时直接回放既有结论与额度，不新增、不改口；迟到补报的数据只能影响之后
业务日的重算，无法追改已封档日期的结论。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    day: int
    zone: str
    state: str
    requested_l: int
    conservative_l: int
    basis: str
    sealed_at: str

    def to_line(self) -> dict:
        return {
            "day": self.day,
            "zone": self.zone,
            "state": self.state,
            "requested_l": self.requested_l,
            "conservative_l": self.conservative_l,
            "basis": self.basis,
            "sealed_at": self.sealed_at,
        }

    @classmethod
    def from_line(cls, line: dict) -> "LedgerEntry":
        return cls(
            day=int(line["day"]),
            zone=str(line["zone"]),
            state=str(line["state"]),
            conservative_l=int(line["conservative_l"]),
            requested_l=int(line["requested_l"]),
            basis=str(line.get("basis", "")),
            sealed_at=str(line.get("sealed_at", "")),
        )


class ReleaseLedger:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def _read_all(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(LedgerEntry.from_line(json.loads(line)))
        return entries

    def sealed_for_day(self, day: int) -> dict[str, LedgerEntry]:
        """返回指定业务日已经封档的裁决，按棚区索引。"""
        return {entry.zone: entry for entry in self._read_all() if entry.day == day}

    def seal_day(self, day: int, verdicts: list[dict]) -> list[LedgerEntry]:
        """为业务日封档。

        已有封档记录时原样回放，保持同日重跑结论与额度完全一致；否则把本次
        裁决原子追加写入。verdicts 为 analysis.ReleaseVerdict.evidence() 形态。
        """
        existing = self.sealed_for_day(day)
        if existing:
            return [existing[zone] for zone in sorted(existing)]
        sealed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fresh = [
            LedgerEntry(
                day=day,
                zone=row["棚区"],
                state=row["裁决"],
                requested_l=int(row["参考补水量（升）"]),
                conservative_l=int(row["放行额度（升）"]),
                basis=row["裁决依据"],
                sealed_at=sealed_at,
            )
            for row in sorted(verdicts, key=lambda item: item["棚区"])
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as tmp:
            for entry in fresh:
                tmp.write(json.dumps(entry.to_line(), ensure_ascii=False, allow_nan=False) + "\n")
            tmp_path = Path(tmp.name)
        # 只追加：历史业务日的封档行原样保留。
        with self.path.open("a", encoding="utf-8") as handle:
            for line in tmp_path.read_text(encoding="utf-8").splitlines():
                handle.write(line + "\n")
        tmp_path.unlink(missing_ok=True)
        return fresh
