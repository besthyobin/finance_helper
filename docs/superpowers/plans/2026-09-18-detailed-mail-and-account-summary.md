# 매매 알림 메일 상세화 및 웹 UI 계좌 총액 요약 구현 계획서

## 1. 개요
- 모의투자 매수, 매도, 마감 알림 메일 메시지를 풍부한 정보(종목명, 단가, 수량, 총금액, 적용 정책, 손익률, 보유시간 등)로 전면 개편.
- 모의투자 웹 대시보드에 현재 총 자산, 현금 예수금, 보유주식 평가액, 당일 총 손익 및 수익률을 한눈에 볼 수 있는 계좌 요약 카드 및 `/api/account` 백엔드 엔드포인트 구현.

---

## 2. 작업 단위 (Tasks)

### Task 1: 메일 포맷팅 함수 분리 및 상세화 (TDD)
- [ ] 파일: `trader/paper.py`, `trader/tests/test_paper.py`
- [ ] `Paper` 클래스에 `self.names` 주입 지원 (`Paper(conn, symbols, capital, today, policy_data=None, names=None)`)
- [ ] `format_buy_mail(symbol, name, bar, qty, price, budget, policy)` 헬퍼 구현
- [ ] `format_sell_mail(symbol, name, trade, qty, pnl, saved, costs)` 헬퍼 구현
- [ ] `format_close_mail(today, capital, runners, qty, saved, diffs, names, policy)` 헬퍼 구현
- [ ] `test_paper.py`에 상세 메일 포맷팅 테스트 케이스 작성 및 검증.

### Task 2: 백엔드 계좌 요약 API (`GET /api/account`) 구현 (TDD)
- [ ] 파일: `trader/web.py`, `trader/tests/test_web.py`
- [ ] `calculate_account_summary(conn, capital)` 함수 구현:
  - `PAPER_CAPITAL` (1,000만 원) 기준
  - `realized_pnl`, `unrealized_pnl`, `stock_eval`, `stock_cost`, `cash`, `total_assets`, `total_pnl`, `return_pct` 계산
- [ ] `Handler.do_GET`에 `/api/account` 라우트 추가
- [ ] `tests/test_web.py`에 `test_api_account` 테스트 작성 및 통과 검증.

### Task 3: 프론트엔드 UI 계좌 총액 요약 카드 구현
- [ ] 파일: `trader/web/index.html`, `trader/web/style.css`, `trader/web/app.js`
- [ ] `index.html`: 매매 정책 카드 아래에 `#account-card` 추가
  - 총 평가 자산 (큰 폰트, 당일 손익 뱃지)
  - 현금 잔고(예수금), 보유 주식 평가액, 시작 원금, 실현 손익 / 평가 손익 4분할 그리드
- [ ] `style.css`: 프리미엄 계좌 요약 카드 스타일 추가
- [ ] `app.js`: `refresh()` 주기(5초)마다 `/api/account`를 호출하여 요약 카드를 실시간 갱신

### Task 4: 웹 서버 재시작 및 브라우저 E2E 검증
- [ ] 기존 데몬 종료 및 최신 `web.py` 데몬 재시작
- [ ] `browser_subagent`로 `http://127.0.0.1:8765/` 접속하여 계좌 총액 카드 렌더링 확인 및 스크린샷 캡처

### Task 5: 전체 회귀 테스트 및 워크스루 작성
- [ ] 전체 pytest 테스트 스위트 통과 확인
- [ ] [walkthrough.md](file:///c%3A/Users/hbkim/.gemini/antigravity-ide/brain/0444ac39-ecb8-445c-9618-79e0f142bfa8/walkthrough.md) 및 [task.md](file:///c%3A/Users/hbkim/.gemini/antigravity-ide/brain/0444ac39-ecb8-445c-9618-79e0f142bfa8/task.md) 갱신 및 사용자 보고
