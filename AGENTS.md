# Antigravity 에이전트 개발 지침: Superpowers SDD & ECC 엔지니어링 표준

이 프로젝트([finance_helper](file:///d:/dev/antigravity_workspace/finance_helper))에서는 **Superpowers의 Spec-Driven Development (SDD)** 방법론과 **ECC (Everything Claude Code)**의 엔지니어링 가드레일을 결합하여 고품질의 안정적인 개발을 수행합니다.

---

## 1. Superpowers: 스펙 주도 개발 (SDD) 원칙

새로운 기능이나 주요 리팩토링을 수행할 때, 즉시 코드를 작성하지 않고 반드시 아래 4단계 사이클을 준수합니다.

### 1단계: 브레인스토밍 및 스펙(Spec) 작성
- 사용자의 요구사항과 시스템 제약사항을 분석하여 `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`에 설계 문서를 작성합니다.
- 스펙 문서 필수 항목:
  1. 목표 및 배경
  2. 시스템 구조 및 데이터 흐름
  3. API / 스키마 변경 사항
  4. 엣지 케이스 및 위험 요소

### 2단계: 실행 계획서(Implementation Plan) 수립
- 스펙이 확정되면 `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`에 태스크 단위 계획서를 작성합니다.
- 각 태스크는 5~10분 이내로 검증 가능한 작은 단위로 분할합니다.
- 사용자 검토 및 승인 후에 구현을 시작합니다.

### 3단계: 엄격한 TDD (Test-Driven Development)
- **Red**: 구현 코드 작성 전, 요구사항을 검증하는 실패하는 테스트 케이스를 먼저 작성합니다.
- **Green**: 테스트를 통과시키는 최소한의 구현 코드를 작성합니다.
- **Refactor**: 중복을 제거하고 가독성과 성능을 개선합니다.

### 4단계: 검증 및 회고 (Verification & Walkthrough)
- 전체 테스트 스위트를 실행하여 기존 기능의 회귀(Regression)가 없는지 확인합니다.
- 완료된 작업 결과와 테스트 로그를 요약하여 사용자에게 보고합니다.

---

## 2. ECC 엔지니어링 가드레일 및 보안 수칙

### 보안 및 환경 변수 보호
- `.env`, `.env.local` 등 민감한 API 키(`TOSS_CLIENT_*`, Gmail 앱 비밀번호, DB 접속 URL)는 절대 로그나 설명, 아티팩트에 원문 그대로 노출하지 않습니다.
- 커밋 또는 파일 변경 시 `.gitignore`에 정의된 파일이 추적되지 않도록 주의합니다.

### 트레이딩 시스템 안전 수칙
- **장중 동시 실행 금지**: 토스 Open API 토큰은 클라이언트당 1개만 유효하므로, 장중(08:55~15:31)에는 `collector.py`와 `paper.py`가 동시에 토큰을 재발급하여 서로를 무효화하지 않도록 프로세스 상태를 반드시 확인합니다.
- **최소 침습 원칙**: 기존 비즈니스 로직과 주석은 의도된 사양(슬리피지, 수수료 계산 등)이므로, 명시적인 요청이 없는 한 임의로 수정하거나 삭제하지 않습니다.
- **DB 정합성 유지**: 모의투자(`paper_status`, `paper_trades`) 및 1분봉(`minute_bars`) 테이블 작업 시 멱등성(Upsert / ON CONFLICT)을 철저히 보장합니다.

---

## 3. 언어 및 커뮤니케이션 규칙
- 사용자와의 모든 설명, 보고서 및 마크다운 문서는 **한국어**로 작성합니다.
- 파일 경로는 항상 클릭 가능한 마크다운 링크(`[basename](file:///path)`) 형식을 사용합니다.
