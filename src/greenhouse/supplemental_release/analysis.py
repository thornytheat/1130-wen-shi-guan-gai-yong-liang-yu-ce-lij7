from dataclasses import dataclass
from statistics import median

from .contracts import ReleaseCandidate

# 与偏差检验环节保持同一套联锁阈值，避免补灌清单与常规配水口径不一致。
TELEMETRY_GAP_LIMIT = 40
ROOTZONE_WET_LIMIT = 0.68
DRY_MOISTURE_LIMIT = 0.40
MIN_HISTORY_SUPPORT = 3
# 保守下限以百升为粒度向下取整，宁少补不冒补。
FLOOR_STEP_L = 100

# 终局状态：只有“准予补灌”可以进入后续额度与排班环节。
STATE_RELEASED = "准予补灌"
STATE_TELEMETRY_GAP = "挂起-遥测断档"
STATE_WET_ROOT = "挂起-根区过湿"
STATE_HISTORY_WEAK = "挂起-历史证据不足"


@dataclass(frozen=True, slots=True)
class ReleaseVerdict:
    zone: str
    crop: str
    pump: str
    state: str
    requested_l: int
    conservative_l: int
    last_moisture: float
    gap: int
    history_support: int
    reasons: tuple[str, ...]

    def evidence(self) -> dict:
        return {
            "棚区": self.zone,
            "作物": self.crop,
            "泵组": self.pump,
            "裁决": self.state,
            "参考补水量（升）": self.requested_l,
            "放行额度（升）": self.conservative_l,
            "最近根区含水": round(self.last_moisture, 3),
            "当期断档分钟": self.gap,
            "历史干燥样本": self.history_support,
            "裁决依据": "、".join(self.reasons),
        }


def conservative_floor(litres: float, *, step: int = FLOOR_STEP_L) -> int:
    """放行量只取保守下限：按百升向下取整，且不为负。"""
    if litres < 0:
        raise ValueError("REFERENCE_LITRES_NEGATIVE")
    if step < 1:
        raise ValueError("FLOOR_STEP_INVALID")
    return int(litres) // step * step


def dry_day_reference(history: list[tuple[float, float]], *, dry_limit: float = DRY_MOISTURE_LIMIT) -> tuple[float, int]:
    """从历史干燥日（含水率低于阈值）的补水量取中位数作为保守参考。

    返回 (参考升数, 干燥日样本数)。样本不足时参考量为 0，由裁决环节挂起。
    """
    dry_litres = sorted(litres for moisture, litres in history if moisture <= dry_limit)
    if not dry_litres:
        return 0.0, 0
    return float(median(dry_litres)), len(dry_litres)


def adjudicate(candidate: ReleaseCandidate) -> ReleaseVerdict:
    """对单个根区缺读棚区做出补灌裁决。

    硬联锁优先：遥测断档、根区过湿一律不得放行；证据链不完整同样挂起，
    挂起不等于已补。通过全部联锁后，额度只给保守下限。
    """
    reasons: list[str] = []
    if candidate.gap > TELEMETRY_GAP_LIMIT:
        state = STATE_TELEMETRY_GAP
        reasons.append(f"遥测断档 {candidate.gap} 分钟，超过 {TELEMETRY_GAP_LIMIT} 分钟联锁")
    elif candidate.last_moisture > ROOTZONE_WET_LIMIT:
        state = STATE_WET_ROOT
        reasons.append(f"最近可见含水率 {round(candidate.last_moisture, 3)} 高于过湿线 {ROOTZONE_WET_LIMIT}")
    elif candidate.history_support < MIN_HISTORY_SUPPORT:
        state = STATE_HISTORY_WEAK
        reasons.append(f"历史干燥日仅 {candidate.history_support} 条，少于 {MIN_HISTORY_SUPPORT} 条证据要求")
    else:
        state = STATE_RELEASED
        capped = min(candidate.dry_reference_l, candidate.asset_limit)
        reasons.append(
            f"遥测连续（断档 {candidate.gap} 分钟），最近含水率 {round(candidate.last_moisture, 3)} 未过湿，"
            f"依据 {candidate.history_support} 个历史干燥日中位补水 {round(candidate.dry_reference_l, 1)} 升"
        )
        quota = conservative_floor(capped)
        return ReleaseVerdict(
            zone=candidate.zone,
            crop=candidate.crop,
            pump=candidate.pump,
            state=state,
            requested_l=round(min(candidate.dry_reference_l, candidate.asset_limit), 1),
            conservative_l=quota,
            last_moisture=candidate.last_moisture,
            gap=candidate.gap,
            history_support=candidate.history_support,
            reasons=tuple(reasons),
        )
    return ReleaseVerdict(
        zone=candidate.zone,
        crop=candidate.crop,
        pump=candidate.pump,
        state=state,
        requested_l=0,
        conservative_l=0,
        last_moisture=candidate.last_moisture,
        gap=candidate.gap,
        history_support=candidate.history_support,
        reasons=tuple(reasons),
    )


def adjudicate_batch(candidates: list[ReleaseCandidate]) -> list[ReleaseVerdict]:
    return [adjudicate(item) for item in sorted(candidates, key=lambda item: item.zone)]
