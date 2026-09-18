import json
from decimal import Decimal
from pathlib import Path

import pytest

import policy


def test_default_policy():
    """기본 매매 정책은 orb 전략과 손절 -1.0%, 익절 +2.0%, 시초 09:30, 청산 15:15이다."""
    assert policy.DEFAULT_POLICY == {
        "strategy": "orb",
        "stop_pct": -1.0,
        "target_pct": 2.0,
        "range_end": "09:30",
        "exit_at": "15:15",
    }


def test_load_policy_returns_default_when_file_missing(tmp_path):
    """파일이 없으면 기본 정책을 반환한다."""
    p = policy.load_policy(tmp_path / "non_existent.json")
    assert p == policy.DEFAULT_POLICY


def test_validate_policy_adjusts_positive_stop_pct():
    """손절 수치가 양수로 입력되면 자동으로 음수로 보정한다."""
    p = policy.validate_policy({"stop_pct": 1.5, "target_pct": 2.5})
    assert p["stop_pct"] == -1.5
    assert p["target_pct"] == 2.5


def test_validate_policy_rejects_out_of_range():
    """손절률이 0이거나 너무 극단적인 값이면 ValueError를 발생시킨다."""
    with pytest.raises(ValueError, match="손절"):
        policy.validate_policy({"stop_pct": 0})
    with pytest.raises(ValueError, match="손절"):
        policy.validate_policy({"stop_pct": -60.0})
    with pytest.raises(ValueError, match="익절"):
        policy.validate_policy({"target_pct": -1.0})
    with pytest.raises(ValueError, match="익절"):
        policy.validate_policy({"target_pct": 0})


def test_save_and_load_policy_round_trip(tmp_path):
    """정책을 저장하고 다시 불러오면 값이 유지된다."""
    target_path = tmp_path / "policy.json"
    saved = policy.save_policy({"stop_pct": -2.5, "target_pct": 3.0}, target_path)
    assert saved["stop_pct"] == -2.5
    assert saved["target_pct"] == 3.0
    loaded = policy.load_policy(target_path)
    assert loaded["stop_pct"] == -2.5
    assert loaded["target_pct"] == 3.0
    assert loaded["strategy"] == "orb"
