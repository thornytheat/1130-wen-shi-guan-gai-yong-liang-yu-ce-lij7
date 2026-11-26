import pytest

from greenhouse.supplemental_release.service import (
    inspect,
    build_candidate,
    run_adjudication,
)
from greenhouse.supplemental_release.contracts import ReleaseCandidate, RecordError
from greenhouse.supplemental_release.analysis import (
    STATE_RELEASED,
    STATE_TELEMETRY_GAP,
    STATE_WET_ROOT,
    STATE_HISTORY_WEAK,
    conservative_floor,
    dry_day_reference,
    adjudicate,
)


@pytest.fixture
def document():
    return {
        'zone': '棚-X', 'crop': '番茄', 'pump': '西渠', 'gap': 0,
        'history_support': 4, 'last_moisture': 0.31, 'dry_reference_l': 620.0,
        'asset_limit': 1800.0, 'per_min': 42, 'window': 40,
    }


def test_contract_round_trip(document):
    entry = ReleaseCandidate.parse(document)
    assert entry.evidence() == document
    assert ReleaseCandidate.parse(entry.evidence()) == entry


def test_record_identity_normalizes_fullwidth(document):
    document['zone'] = "　棚－Ａ１　"
    entry = ReleaseCandidate.parse(document)
    assert getattr(entry, 'zone') == "棚-A1"


def test_missing_identity_isolated(document):
    missing = dict(document)
    del missing['zone']
    intake = inspect([missing, document])
    assert intake.read_count == 2
    assert intake.accepted_count == 1
    assert intake.rejection_counts() == {"TEXT_REQUIRED": 1}
    assert intake.rejected[0].field == 'zone'


def test_boolean_is_not_magnitude(document):
    document['gap'] = True
    with pytest.raises(RecordError, match="NUMBER_REQUIRED"):
        ReleaseCandidate.parse(document)


def test_nonfinite_measure_isolated(document):
    corrupted = {**document, 'dry_reference_l': "nan"}
    batch = inspect([document, corrupted, document])
    assert [item.evidence() for item in batch.records] == [document, document]
    assert batch.rejected[0].row == 2
    assert batch.evidence()["reason_counts"]["NUMBER_NONFINITE"] == 1


def test_invalid_range_reserves_no_entry(document):
    document['last_moisture'] = 1.5
    intake = inspect([document])
    assert intake.accepted_count == 0
    assert intake.rejected[0].field == 'last_moisture'
    assert intake.rejected[0].reason == "NUMBER_RANGE"


def test_conservative_floor_rounds_down_to_hectolitres():
    # 放行量只取保守下限：百升向下取整，绝不向上加量。
    assert conservative_floor(699.9) == 600
    assert conservative_floor(600) == 600
    assert conservative_floor(99.9) == 0
    assert conservative_floor(0) == 0


def test_dry_day_reference_uses_only_dry_history_median():
    history = [(0.45, 300.0), (0.30, 700.0), (0.25, 900.0), (0.40, 500.0), (0.70, 100.0)]
    reference, support = dry_day_reference(history)
    # 干燥日为含水率 <= 0.40 的三条：500、700、900，中位数 700。
    assert support == 3
    assert reference == 700.0


def test_dry_day_reference_empty_history_is_weak_evidence():
    assert dry_day_reference([]) == (0.0, 0)
    assert dry_day_reference([(0.6, 100.0)]) == (0.0, 0)


def _candidate(**overrides):
    base = dict(
        zone='棚-05', crop='番茄', pump='西渠', gap=0,
        history_support=4, last_moisture=0.31, dry_reference_l=620.0,
        asset_limit=1800.0, per_min=42, window=40,
    )
    base.update(overrides)
    return ReleaseCandidate.parse(base)


def test_telemetry_gap_never_released_even_if_soil_looks_dry():
    verdict = adjudicate(_candidate(gap=41, last_moisture=0.2))
    assert verdict.state == STATE_TELEMETRY_GAP
    assert verdict.conservative_l == 0
    assert "遥测断档" in verdict.reasons[0]


def test_wet_rootzone_never_released():
    verdict = adjudicate(_candidate(last_moisture=0.69))
    assert verdict.state == STATE_WET_ROOT
    assert verdict.conservative_l == 0


def test_weak_history_is_held_not_released():
    verdict = adjudicate(_candidate(history_support=2))
    assert verdict.state == STATE_HISTORY_WEAK
    assert verdict.conservative_l == 0


def test_release_takes_only_conservative_lower_bound():
    verdict = adjudicate(_candidate(dry_reference_l=699.9, asset_limit=1800.0))
    assert verdict.state == STATE_RELEASED
    assert verdict.conservative_l == 600
    assert verdict.requested_l == 699.9


def test_asset_limit_caps_reference_before_flooring():
    verdict = adjudicate(_candidate(dry_reference_l=2000.0, asset_limit=480.0))
    assert verdict.state == STATE_RELEASED
    assert verdict.conservative_l == 400


def test_held_zone_is_not_counted_as_supplemented():
    verdicts = run_adjudication([
        _candidate(zone='棚-03', gap=60),
        _candidate(zone='棚-05'),
    ])
    released = [v for v in verdicts if v.state == STATE_RELEASED]
    assert [v.zone for v in released] == ['棚-05']
    assert sum(v.conservative_l for v in verdicts if v.state != STATE_RELEASED) == 0


def test_build_candidate_extracts_last_moisture_and_dry_reference():
    history = [(0.55, 200.0), (0.30, 700.0), (0.25, 900.0), (0.40, 500.0)]
    candidate = build_candidate(
        zone='棚-05', crop='番茄', pump='西渠', gap=0, history=history,
        asset_limit=1800.0, per_min=42, window=40,
    )
    assert candidate.last_moisture == 0.40
    assert candidate.history_support == 3
    verdict = adjudicate(candidate)
    assert verdict.state == STATE_RELEASED
    assert verdict.conservative_l == 700


def test_build_candidate_without_history_is_held():
    candidate = build_candidate(
        zone='棚-99', crop='番茄', pump='西渠', gap=0, history=[],
        asset_limit=1800.0, per_min=42, window=40,
    )
    assert adjudicate(candidate).state == STATE_HISTORY_WEAK
