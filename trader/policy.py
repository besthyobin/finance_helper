"""매매 정책(전략 및 손절·익절 파라미터) 관리 모듈."""
import json
import logging
import os
from datetime import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "policy.json"

log = logging.getLogger("policy")

DEFAULT_POLICY = {
    "strategy": "orb",
    "stop_pct": -1.0,
    "target_pct": 2.0,
    "range_end": "09:30",
    "exit_at": "15:15",
}


def validate_policy(updates, base=None):
    """정책 변경값을 검증하고 기본값 또는 기존 값과 머지한 dict를 반환한다."""
    merged = dict(base or DEFAULT_POLICY)
    for k, v in updates.items():
        if k in DEFAULT_POLICY:
            merged[k] = v

    # 손절 수치 검증 및 자동 음수 보정
    if "stop_pct" in merged:
        try:
            stop = float(merged["stop_pct"])
        except (ValueError, TypeError):
            raise ValueError("손절 수치는 숫자여야 합니다.")
        if stop == 0 or stop < -50.0 or stop > 50.0:
            raise ValueError("손절 수치는 -50.0% ~ -0.1% 범위여야 합니다.")
        # 양수로 들어오면 음수로 자동 보정
        merged["stop_pct"] = -abs(stop)

    # 목표 익절 수치 검증
    if "target_pct" in merged:
        try:
            target = float(merged["target_pct"])
        except (ValueError, TypeError):
            raise ValueError("익절 수치는 숫자여야 합니다.")
        if target <= 0 or target > 100.0:
            raise ValueError("익절 수치는 0.1% ~ 100.0% 범위의 양수여야 합니다.")
        merged["target_pct"] = target

    # 시각 포맷 검증
    for time_key in ("range_end", "exit_at"):
        if time_key in merged:
            try:
                time.fromisoformat(merged[time_key])
            except Exception:
                raise ValueError(f"{time_key}는 HH:MM 형식이어야 합니다.")

    return merged


def load_policy(path=None):
    """policy.json 파일에서 정책을 읽어 반환한다. 파일이 없거나 오류면 DEFAULT_POLICY를 반환한다."""
    path = Path(path or POLICY_PATH)
    if not path.exists():
        return dict(DEFAULT_POLICY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_policy(data)
    except Exception as e:
        log.warning("policy.json 로드 실패, 기본값 사용: %s", e)
        return dict(DEFAULT_POLICY)


def save_policy(updates, path=None):
    """정책을 검증한 뒤 원자적으로 파일에 저장하고 갱신된 정책을 반환한다."""
    path = Path(path or POLICY_PATH)
    current = load_policy(path)
    validated = validate_policy(updates, base=current)

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(validated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    log.info("매매 정책 저장 완료: %s", validated)
    return validated
