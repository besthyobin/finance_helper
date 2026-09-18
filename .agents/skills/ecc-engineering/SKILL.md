---
name: ecc-engineering
description: >-
  Provides ECC (Everything Claude Code) engineering standards, safety guardrails,
  trading system precautions, and verification practices for the finance_helper repository.
---

# ECC (Everything Claude Code) Engineering Standards & Guardrails

이 스킬은 ECC 프레임워크의 핵심 엔지니어링 원칙과 트레이딩 시스템 안전 가드레일을 제공합니다.

---

## 1. 보안 가드레일 (AgentShield)

- **비밀키 및 시크릿 엄격 격리**:
  - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `GMAIL_APP_PASSWORD`, `DATABASE_URL` 등 민감 정보는 어떠한 경우에도 커밋하거나 외부로 유출하지 않습니다.
  - 로그 파일이나 콘솔 출력 시 민감한 연결 문자열은 마스킹하거나 종류만 표기합니다.
- **`.gitignore` 준수**:
  - `.env`, `.token.json`, `paper_symbols.txt`, 가상환경(`.venv`), 로그(`logs/`)가 Git 추적 대상에 들어가지 않도록 검증합니다.

---

## 2. 트레이딩 도메인 안전 수칙

- **토스증권 API 토큰 충돌 방지**:
  - 토스 Open API는 클라이언트당 유효 토큰이 1개입니다. 두 프로세스가 동시에 새 토큰을 발급받으면 기존 토큰이 즉시 만료되어 다른 프로세스가 `401 Unauthorized` 오류를 일으킵니다.
  - 장중(평일 08:55 ~ 15:31)에는 `collector.py`나 `selector.py` 수동 실행을 피하고, 모의투자 프로세스가 단독으로 토큰을 유지하도록 보장합니다.
- **Windows 작업 스케줄러 상태 제어**:
  - 모의투자 프로세스를 재기동할 때는 `Stop-ScheduledTask` → 잔여 프로세스 정리 → `Start-ScheduledTask`의 절차를 따라 좀비 프로세스가 발생하지 않도록 합니다.
- **DB 데이터 무결성 보장**:
  - `minute_bars` 및 `paper_trades`, `paper_status`는 항상 `ON CONFLICT` 구문을 사용하여 중복 삽입 에러를 방지하고 멱등성을 유지합니다.

---

## 3. 코드 품질 및 최소 침습 원칙

- **기존 설계 사양 보존**:
  - 수수료(fee 0.015%), 거래세(tax 0.2%), 슬리피지(slippage 0.05%), 15:15 강제 청산 등 백테스트와 일치해야 하는 파라미터는 변경하지 않습니다.
- **단위 테스트 기반 회귀 검증**:
  - 코드 변경 후에는 반드시 관련 단위 테스트 및 전체 테스트 스위트를 검증합니다:
    ```powershell
    .\.venv\Scripts\python -m pytest tests -v
    ```
