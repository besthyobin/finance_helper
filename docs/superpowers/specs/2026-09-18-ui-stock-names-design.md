# 웹 UI 종목명 표시 기능 설계

작성일: 2026-09-18
목표: 모의투자 웹 화면(UI)에서 종목코드(예: 005930) 대신 또는 함께 이해하기 쉬운 종목명(예: 삼성전자, SK하이닉스)을 표시하여 사용성을 개선한다.

---

## 1. 요구사항 및 배경

- **문제점**:
  - 현재 웹 대시보드(`http://127.0.0.1:8765`)는 `paper_status`와 `paper_trades` 테이블의 `symbol`(종목코드)만 받아와 화면에 표시함.
  - 사용자가 드롭다운(`select#symbol`), 현재 상태 표, 오늘 체결 표에서 숫자 코드만 보고 무슨 종목인지 즉각 알아보기 어려움.
- **요구사항**:
  - 종목 선택 드롭다운에 `종목명 (종목코드)` 형식으로 표시 (예: `삼성전자 (005930)`).
  - 현재 상태 테이블의 종목 열에 종목명과 종목코드 함께 표시.
  - 오늘 체결 테이블의 종목 열에 종목명과 종목코드 함께 표시.

---

## 2. 시스템 아키텍처 및 데이터 흐름

```text
[paper_symbols.txt (# 주석)] ─┐
                             ├─> [load_symbol_names] ─> [web.py API (/api/status, /api/trades)]
[selection_candidates (DB)]  ─┘                                   │ (name 필드 추가)
                                                                  ▼
                                                      [web/app.js (UI 렌더링)]
                                                      - 드롭다운: 종목명 (코드)
                                                      - 현재 상태 표: 종목명 (코드)
                                                      - 체결 내역 표: 종목명 (코드)
```

1. **백엔드 (`web.py`)**:
   - `paper.py`의 `read_symbol_names` 및 `load_symbol_names` 헬퍼 함수를 재사용.
   - `/api/status`: 각 상태 행에 `name` 필드(`종목명` 또는 코드로 폴백) 추가.
   - `/api/trades`: 각 체결 행에 `name` 필드 추가.
2. **프론트엔드 (`web/app.js`)**:
   - 드롭다운: `new Option(s.name ? `${s.name} (${s.symbol})` : s.symbol, s.symbol)`
   - 현재 상태 표: `s.name ? `${s.name} (${s.symbol})` : s.symbol`
   - 오늘 체결 표: `t.name ? `${t.name} (${t.symbol})` : t.symbol`

---

## 3. API 및 스키마 변경 사항

- **DB 스키마 변경**: 없음 (기존 `paper_symbols.txt` 주석 및 `selection_candidates` 테이블 조회)
- **API 응답 변경**:
  - `/api/status` 응답 객체에 `name` (string) 필드 추가
  - `/api/trades` 응답 객체에 `name` (string) 필드 추가

---

## 4. 엣지 케이스 및 위험 요소

- 종목명이 없는 경우(임시 종목, 매핑 실패): 종목코드(`symbol`) 자체로 안전하게 폴백.
- 기존 단위 테스트 호환성: 기존 `test_web.py`의 tuple assert와 호환되도록 필드 추가 방식 적용.
