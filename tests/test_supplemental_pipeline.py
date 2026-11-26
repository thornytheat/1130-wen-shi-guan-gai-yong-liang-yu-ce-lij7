"""补灌放行清单的端到端规则：挂起不算补、泵班排不下不算补、封档不改口。"""
import json
from pathlib import Path

import pytest

from greenhouse.pipeline import run
from greenhouse.supplemental_release.analysis import (
    STATE_RELEASED,
    STATE_TELEMETRY_GAP,
    STATE_WET_ROOT,
)
from greenhouse.supplemental_release.ledger import ReleaseLedger
from greenhouse.supplemental_release.service import build_candidate, run_adjudication
from greenhouse.irrigation_quota.scheduling import schedule, schedule_coverage
from greenhouse.irrigation_quota.contracts import IrrigationOrder


def _history(dry_days=4, last_moisture=0.31):
    rows = [(0.30 + 0.01 * index, 700.0 + 10 * index) for index in range(dry_days)]
    return rows[:-1] + [(last_moisture, rows[-1][1])]


def test_live_pipeline_releases_zone_with_missing_rootzone_reading(tmp_path):
    ledger_path = tmp_path / "release.ndjson"
    result = run(31, ledger_path=ledger_path)
    # 补灌环节独立成段，插在偏差检验与分区拨水之间。
    titles = [stage["title"] for stage in result["stages"]]
    assert titles.index("补灌放行") == titles.index("分区拨水") - 1
    rows = result["release"]["rows"]
    zone_05 = next(row for row in rows if row["棚区"] == "棚-05")
    assert zone_05["裁决"] == STATE_RELEASED
    # 放行量只取保守下限（806.5 升干燥日中位 -> 800 升）。
    assert zone_05["放行额度（升）"] == 800
    # 主配水单仍把缺读棚区挂起，常规结论不被补灌清单改写。
    main_row = next(row for row in result["rows"] if row["zone"] == "棚-05")
    assert main_row["reason"] == "根区探头不足" and main_row["amount_l"] == 0
    # 补灌额度在泵班表独立排入，排入量才计入“已补”。
    assert result["metrics"]["补灌放行（升）"] == 800
    assert result["metrics"]["补灌已排（升）"] == 800
    assert result["release"]["coverage"][0]["unassigned_l"] == 0


def test_telemetry_gap_zone_is_held_and_never_released():
    candidate = build_candidate(
        zone="棚-03", crop="彩椒", pump="西渠", gap=60, history=_history(),
        asset_limit=1800.0, per_min=42, window=40,
    )
    verdict, = run_adjudication([candidate])
    assert verdict.state == STATE_TELEMETRY_GAP
    assert verdict.conservative_l == 0


def test_wet_rootzone_zone_is_held_even_with_clean_telemetry():
    candidate = build_candidate(
        zone="棚-09", crop="彩椒", pump="北井", gap=0, history=_history(last_moisture=0.72),
        asset_limit=1800.0, per_min=42, window=40,
    )
    verdict, = run_adjudication([candidate])
    assert verdict.state == STATE_WET_ROOT
    assert verdict.conservative_l == 0


def test_pump_timetable_shortfall_is_not_counted_as_supplemented():
    candidate = build_candidate(
        zone="棚-05", crop="番茄", pump="西渠", gap=0, history=_history(),
        asset_limit=1800.0, per_min=42, window=40,
    )
    verdict, = run_adjudication([candidate])
    assert verdict.conservative_l == 700
    order = IrrigationOrder.parse(dict(
        zone=verdict.zone, pump=verdict.pump, crop=verdict.crop, state=STATE_RELEASED,
        upper=1800.0, lower=0.0, urgency=1.0, per_min=42, window=40,
    ))
    approvals = [{"zone": verdict.zone, "amount_l": verdict.conservative_l}]
    # 泵组只开放 5 分钟，最多 210 升，其余 490 升排不进班表。
    runs, unscheduled = schedule([order], approvals, {"西渠": [(450, 455)]}, {})
    coverage = schedule_coverage(runs, approvals)
    assert coverage[0]["scheduled_l"] == 210
    assert coverage[0]["unassigned_l"] == 490
    assert unscheduled[0]["未排入（升）"] == 490
    # 实际补过的只有排入班表的 210 升，未排部分不得计成已补。
    assert sum(line.litres for line in runs) == 210


def test_same_day_rerun_keeps_verdict_and_quota_immutable(tmp_path):
    ledger_path = tmp_path / "release.ndjson"
    first = run(31, ledger_path=ledger_path)
    sealed_before = ReleaseLedger(ledger_path).sealed_for_day(29)
    second = run(85, ledger_path=ledger_path)
    # 无论班次额度怎么变，补灌清单同日结论与额度都不新增、不改口。
    assert first["release"]["rows"] == second["release"]["rows"]
    assert first["release"]["scheduled"] == second["release"]["scheduled"]
    assert ReleaseLedger(ledger_path).sealed_for_day(29) == sealed_before
    lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["zone"] == "棚-05"


def test_late_report_only_feeds_later_recompute_and_never_rewrites_today(tmp_path):
    ledger_path = tmp_path / "release.ndjson"
    run(31, ledger_path=ledger_path)
    ledger = ReleaseLedger(ledger_path)
    # 第 29 天已封档；即使之后出现迟到补报，封档结论保持原样。
    sealed = ledger.sealed_for_day(29)["棚-05"]
    assert sealed.state == STATE_RELEASED and sealed.conservative_l == 800
    ledger.seal_day(30, [{
        "棚区": "棚-05", "作物": "番茄", "泵组": "西渠", "裁决": STATE_RELEASED,
        "参考补水量（升）": 699.9, "放行额度（升）": 600, "最近根区含水": 0.31,
        "当期断档分钟": 0, "历史干燥样本": 5, "裁决依据": "次日重算",
    }])
    assert ledger.sealed_for_day(29)["棚-05"].conservative_l == 800
    assert ledger.sealed_for_day(30)["棚-05"].conservative_l == 600
