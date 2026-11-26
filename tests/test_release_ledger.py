import json

from greenhouse.supplemental_release.ledger import ReleaseLedger
from greenhouse.supplemental_release.analysis import STATE_RELEASED, STATE_TELEMETRY_GAP


def _verdict(zone, state=STATE_RELEASED, requested=699.9, quota=600, basis="首次裁决"):
    return {
        "棚区": zone,
        "作物": "番茄",
        "泵组": "西渠",
        "裁决": state,
        "参考补水量（升）": requested,
        "放行额度（升）": quota,
        "最近根区含水": 0.31,
        "当期断档分钟": 0,
        "历史干燥样本": 4,
        "裁决依据": basis,
    }


def test_first_run_seals_and_rerun_replays_identical_conclusion(tmp_path):
    ledger = ReleaseLedger(tmp_path / "release.ndjson")
    first = ledger.seal_day(29, [_verdict("棚-05")])
    assert [entry.zone for entry in first] == ["棚-05"]
    assert first[0].conservative_l == 600
    assert first[0].state == STATE_RELEASED

    # 同一天重跑：即便上游算出不同额度，台账仍回放首次结论，不新增、不改口。
    changed = [_verdict("棚-05", requested=999.0, quota=900, basis="重算结果")]
    rerun = ledger.seal_day(29, changed)
    assert rerun == first
    assert rerun[0].conservative_l == 600
    lines = [json.loads(line) for line in (tmp_path / "release.ndjson").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1


def test_late_data_only_affects_later_days_never_rewrites_sealed_day(tmp_path):
    ledger = ReleaseLedger(tmp_path / "release.ndjson")
    ledger.seal_day(29, [_verdict("棚-05", STATE_TELEMETRY_GAP, requested=0, quota=0)])

    # 次日补报数据到达后重新裁决：新业务日可以得出新结论，但第 29 天封档不动。
    next_day = ledger.seal_day(30, [_verdict("棚-05", STATE_RELEASED, quota=600)])
    assert next_day[0].day == 30 and next_day[0].conservative_l == 600
    sealed_29 = ledger.sealed_for_day(29)
    assert sealed_29["棚-05"].state == STATE_TELEMETRY_GAP
    assert sealed_29["棚-05"].conservative_l == 0

    lines = [json.loads(line) for line in (tmp_path / "release.ndjson").read_text(encoding="utf-8").splitlines()]
    assert [(line["day"], line["zone"], line["conservative_l"]) for line in lines] == [
        (29, "棚-05", 0),
        (30, "棚-05", 600),
    ]


def test_missing_ledger_file_means_nothing_sealed(tmp_path):
    ledger = ReleaseLedger(tmp_path / "absent.ndjson")
    assert ledger.sealed_for_day(29) == {}
