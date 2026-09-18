# 매매 정책 조회 및 손절 설정 기능 구현 계획

스펙 문서: [2026-09-18-policy-settings-design.md](file:///d:/dev/antigravity_workspace/finance_helper/docs/superpowers/specs/2026-09-18-policy-settings-design.md)

---

## 태스크 목록

- [ ] **Task 1: 정책 관리 모듈 및 단위 테스트 (TDD)**
  - `trader/policy.py`: `load_policy`, `save_policy`, `validate_policy` 구현
  - `tests/test_policy.py`: 로드/저장/유효성 검사 단위 테스트 작성 및 통과
- [ ] **Task 2: 모의투자 프로세스 실시간 정책 동기화 (TDD)**
  - `trader/paper.py`: `Paper.sync_policy()` 추가하여 루프마다 `policy.json` 변경 시 `runner.strategy` 파라미터 동적 갱신
  - `tests/test_paper.py`: 정책 변경 시 전략 손절선이 갱신되는지 단위 테스트 작성 및 통과
- [ ] **Task 3: 웹 서버 API 구현 (GET/POST /api/policy)**
  - `trader/web.py`: `GET /api/policy` 및 `POST /api/policy` 핸들러 구현
  - `tests/test_web.py`: API 조회 및 변경 요청 검증 단위 테스트 작성 및 통과
- [ ] **Task 4: 웹 UI 프론트엔드 구현**
  - `web/index.html`: 매매 정책 카드 섹션 및 손절/익절 입력 폼 추가
  - `web/style.css`: 정책 카드 및 인풋/버튼 스타일링
  - `web/app.js`: 정책 로드, 변경 폼 제출 및 적용 피드백 처리
- [ ] **Task 5: 서버 재기동 및 브라우저 E2E 검증**
  - `web.py` 재시작, 브라우저 서브에이전트로 UI 확인 및 실제 손절 수치 변경 테스트
