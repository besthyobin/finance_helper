# 웹 UI 종목명 표시 기능 구현 계획

스펙 문서: [2026-09-18-ui-stock-names-design.md](file:///d:/dev/antigravity_workspace/finance_helper/docs/superpowers/specs/2026-09-18-ui-stock-names-design.md)

---

## 태스크 목록

- [ ] **Task 1: 단위 테스트 작성 (Red)**
  - `tests/test_web.py`에 `/api/status` 및 `/api/trades`가 `name` 필드를 반환하는지 검증하는 테스트 추가
- [ ] **Task 2: 백엔드 API 구현 (Green)**
  - `web.py`에서 `load_symbol_names` 및 `read_symbol_names`를 활용하여 `/api/status`, `/api/trades` 결과에 `name` 필드 추가
  - `pytest tests/test_web.py -v` 통과 확인
- [ ] **Task 3: 프론트엔드 UI 렌더링 갱신**
  - `web/app.js`에서 종목 드롭다운(`fillSymbols`), 현재 상태 표(`fill("status")`), 오늘 체결 표(`fill("trades")`)에 `종목명 (종목코드)` 표시
- [ ] **Task 4: 실행 중인 web.py 재기동 및 브라우저 검증**
  - `web.py` 재시작 후 `http://127.0.0.1:8765`에서 종목명이 정상 표시되는지 브라우저 서브에이전트로 확인
