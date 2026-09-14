# KIS 1분봉 수집기 설계

- 작성일: 2026-09-14
- 상태: 설계 승인, 구현 계획 작성 전

## 1. 배경

한국 주식 분봉 단타 자동매매 시스템을 만든다. 전체 시스템은 아래 하위 프로젝트로 나누어 각각 스펙 → 계획 → 구현 사이클을 거친다.

| 순서 | 하위 프로젝트 | 상태 |
|---|---|---|
| 1 | **KIS 1분봉 수집기 (이 문서)** | 설계 완료 |
| 2 | 백테스터 | 데이터 축적 중 설계 |
| 3 | 전략(매매 신호) | 백테스터와 함께 설계 |
| 4 | 모의투자 주문 실행 + 리스크 관리 | 백테스트 검증 후 |
| 5 | 대시보드 연동(finance_helper Next.js) | 필요 시 |

실전 매매는 모의투자 검증 이후 별도로 결정한다.

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 1차 목표 | 백테스트 → 모의투자 | 검증 없는 실계좌 연결 방지 |
| 시장 / 증권사 | 한국 주식 / 한국투자증권 KIS Open API | REST 기반, 모의계좌 제공 |
| 매매 주기 | 분봉(당일 매매) | 사용자 선택 |
| 백테스트 데이터 | 당일 1분봉을 매일 직접 수집 | KIS는 과거 분봉을 길게 제공하지 않음, 무료, 수집기가 실시간 엔진에 재사용됨 |
| 언어 / 위치 | Python, 저장소 내 `trader/` | KIS 공식 샘플, pandas 생태계 |
| 실행 환경 | 사용자 Windows PC, 작업 스케줄러 | 사용자 선택 |
| 수집 대상 | `symbols.txt`에 직접 지정 | 사용자 선택 |
| 저장소 | PostgreSQL(Windows 서비스), TimescaleDB 미사용 | 이후 대시보드 연동 대비. 1천만 행/년 규모는 인덱스로 충분 |
| 알림 | 텔레그램 봇 | 당일분봉은 다음 날 복구 불가 → 누락을 당일 인지해야 함 |

### 전제와 미확인 사항

- 사용자는 아직 KIS 계좌/API 키가 없다. 구현과 자동 테스트는 KIS 공식 문서·GitHub 샘플 응답 기준으로 진행하고, 실제 호출 검증은 키 발급 후 수행한다(9.2절).
- 수집에는 **실전 계좌 키**를 쓴다(시세 조회만, 주문 없음). 모의 키는 호출 한도가 낮다.
- 아래 값은 키 발급 후 실제 응답으로 확인하고 다르면 스펙과 코드를 수정한다.
  - 당일분봉 API 응답 필드명 (`stck_cntg_hour`, `stck_oprc`, `stck_hgpr`, `stck_lwpr`, `stck_prpr`, `cntg_vol`)
  - 오류 코드 (`EGW00123`, `EGW00133`, `EGW00201`)
  - 실전/모의 초당 호출 한도
  - 과거 분봉 조회 API(주식일별분봉조회)의 제공 기간과 사용 가능 여부 — 쓸 수 있으면 과거 날짜 수집을 별도 작업으로 추가한다.

## 2. 범위

**포함**
- 평일 장 마감 후 `symbols.txt` 종목의 당일 1분봉(OHLCV)을 PostgreSQL에 저장
- 종목·날짜별 수집 결과 기록, 반복 실행 시 미완료 종목만 재수집
- 당일 최종 실행 후 실패 종목이 남으면 텔레그램 알림

**제외**
- 과거 날짜 수집(`--date` 옵션 없음), 호가·체결 틱 데이터, 실시간 WebSocket 수집
- 휴장일 달력 관리, 수정주가 처리, 종목 자동 선정
- ORM, 비동기 처리, 설정 클래스, 재시도 라이브러리

## 3. 구성 요소

```
trader/
├── .env                 # 비밀/환경값 (git 제외)
├── .env.example         # 키 이름만 적은 예시
├── symbols.txt          # 종목코드, 한 줄에 하나 (# 주석 허용)
├── requirements.txt     # requests, psycopg[binary], python-dotenv, pytest
├── schema.sql           # 테이블 DDL
├── kis.py               # KIS API 클라이언트
├── store.py             # DB 저장/조회
├── notify.py            # 텔레그램 전송
├── collector.py         # 진입점
├── logs/                # 일자별 로그 (git 제외)
└── tests/
    ├── fixtures/        # KIS 샘플 응답 JSON
    └── test_collector.py
```

### 3.1 `.env`

| 키 | 설명 |
|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS 실전 앱키 |
| `KIS_BASE_URL` | 기본 `https://openapi.koreainvestment.com:9443` |
| `KIS_RPS` | 초당 최대 호출 수, 기본 15 |
| `DATABASE_URL` | 예: `postgresql://trader:***@localhost:5432/trader` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 알림 대상 |

`.env`, `.token.json`, `logs/`는 `.gitignore`에 추가한다.

### 3.2 `kis.py`

KIS 통신만 담당한다.

- `Bar`: `ts`(KST `datetime`), `open`, `high`, `low`, `close`(`Decimal`), `volume`(`int`)
- `get_token() -> str`: `.token.json`에 캐시된 토큰이 유효하면 재사용, 아니면 `/oauth2/tokenP`로 발급 후 만료시각과 함께 저장
- `fetch_minute_bars(symbol: str) -> list[Bar]`: 당일 1분봉 전체를 시각 오름차순으로 반환 (4.2절)
- 모든 HTTP 호출은 하나의 내부 함수를 거치며, 이 함수가 호출 간격 제한(4.3절)과 재시도(5.1절)를 처리한다. 테스트에서 HTTP 전송 함수를 주입해 대체할 수 있어야 한다.

### 3.3 `store.py`

DB 작업만 담당한다. `psycopg` 연결을 인자로 받는다.

- `save_bars(conn, symbol, bars)`: 한 트랜잭션으로 `INSERT ... ON CONFLICT (symbol, ts) DO NOTHING`
- `record_run(conn, symbol, trade_date, bar_count, status, error=None)`: `ON CONFLICT (symbol, trade_date) DO UPDATE`로 최신 결과를 덮어씀
- `done_symbols(conn, trade_date) -> set[str]`: 해당 날짜 `status IN ('ok','empty')` 종목
- `failed_runs(conn, trade_date) -> list[(symbol, error)]`: 해당 날짜 `status = 'error'` 종목

### 3.4 `notify.py`

- `send_telegram(text)`: Bot API `sendMessage` 1회 호출. 실패 시 예외를 올리지 않고 로그만 남긴다.

### 3.5 `collector.py`

진입점. 인자 없이 `python collector.py`로 실행한다. 흐름은 4.1절.

### 3.6 스키마 (`schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol  text        NOT NULL,
    ts      timestamptz NOT NULL,
    open    numeric     NOT NULL,
    high    numeric     NOT NULL,
    low     numeric     NOT NULL,
    close   numeric     NOT NULL,
    volume  bigint      NOT NULL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS collect_runs (
    symbol        text        NOT NULL,
    trade_date    date        NOT NULL,
    bar_count     int         NOT NULL,
    status        text        NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    error         text,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);
```

## 4. 수집 흐름

### 4.1 실행 1회

1. `.env` 로드, 로그 파일 `logs/collector-YYYY-MM-DD.log` 설정, DB 연결
2. `today` = 현재 KST 날짜. 토요일·일요일이거나 현재 시각이 15:35 KST 이전이면 로그만 남기고 종료 (자정 이후 밀린 실행이 장 시작 전 빈 응답을 `empty`로 기록해 그날 수집을 건너뛰게 되는 것을 막음)
3. 토큰 확보 (`get_token`)
4. 대상 = `symbols.txt` 종목 − `done_symbols(today)`
5. 종목마다:
   1. `bars = fetch_minute_bars(symbol)`
   2. `bars`가 비어 있으면 `record_run(..., 0, 'empty')`
   3. 아니면 `save_bars` 후 `record_run(..., len(bars), 'ok')`
   4. 예외 발생 시 `record_run(..., 0, 'error', 메시지)` 후 다음 종목
6. 요약 로그: `ok N / empty N / error N / skipped N`
7. 실행 시작 시각이 23:00 KST 이후이고 `failed_runs(today)`가 비어 있지 않으면 텔레그램 알림 (5.4절)
8. 종목 단위 오류만 있었다면 종료 코드 0, 실행 전체 실패(5.3절)는 1

공휴일에는 KIS가 빈 응답을 주므로 전 종목이 `empty`로 기록된다. 휴장일 달력은 관리하지 않는다.

### 4.2 `fetch_minute_bars` 페이지 순회

- API: `GET /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` (TR `FHKST03010200`)
- 파라미터: `FID_ETC_CLS_CODE=""`, `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD=<종목>`, `FID_INPUT_HOUR_1=<HHMMSS>`, `FID_PW_DATA_INCU_YN=Y`
- 응답 `output2`: 기준 시각 이전 최대 30개 봉

순회 규칙:
1. 기준 시각 `153000`으로 시작
2. 응답 봉을 `Bar`로 변환해 누적. 날짜 필드가 `today`가 아닌 봉은 버린다
3. 응답이 비었거나, 이번 페이지 최소 시각이 `090000` 이하이거나, 최소 시각이 이전 기준 시각보다 작아지지 않으면 종료
4. 아니면 다음 기준 시각 = 이번 페이지 최소 시각으로 2번 반복
5. 최대 20페이지. 초과 시 예외
6. `ts` 기준 중복 제거 후 오름차순 정렬해 반환

정상 거래일 종목당 약 381개(09:00–15:20, 15:30), 13–14페이지.

### 4.3 호출 간격

모든 KIS 호출 전에 직전 호출로부터 `1 / KIS_RPS`초가 지나도록 대기한다. 100종목 × 약 14회 ≈ 1,400회, `KIS_RPS=15`에서 약 2분.

### 4.4 작업 스케줄러

- 트리거: 월–금 16:00 시작, 1시간마다 반복, 반복 기간 7시간 (마지막 실행 23:00)
- 설정: "예약된 시작 시간을 놓친 경우 가능한 대로 빨리 작업 시작" 활성화, "이미 실행 중이면 새 인스턴스 시작 안 함"
- 동작: `trader\.venv\Scripts\python.exe collector.py`, 시작 위치 `trader\`
- 첫 실행이 성공하면 이후 반복 실행은 대상이 없어 즉시 종료된다.
- 사용자는 평일 장 마감 후 23:00 전까지 PC를 켜 두어야 한다. PC가 꺼져 있던 날의 데이터는 복구할 수 없다.

## 5. 오류와 누락 처리

원칙: 종목 하나의 실패는 다른 종목을 막지 않는다. 스크립트 안의 재시도는 짧게, 나머지는 1시간 뒤 반복 실행이 맡는다.

### 5.1 KIS 호출 (`kis.py`)

| 상황 | 처리 |
|---|---|
| 토큰 만료 `EGW00123` | 토큰 재발급 후 같은 호출 1회 재시도 |
| 토큰 발급 1분 제한 `EGW00133` | 60초 대기 후 1회 재시도 |
| 호출 한도 초과 `EGW00201` | 1초 대기 후 최대 3회 재시도 |
| 네트워크 오류, 타임아웃(10초), HTTP 5xx | 1·2·4초 간격 최대 3회 재시도 |
| 재시도 소진, 그 밖의 오류 코드(`rt_cd != "0"`) | 오류 코드·메시지를 담아 예외 |

### 5.2 부분 수집

- 한 종목의 모든 페이지를 받은 뒤에만 저장한다. 순회 중 예외가 나면 해당 종목은 아무것도 저장하지 않고 `error`로 기록한다.
- 봉 개수로 실패를 판정하지 않는다(거래정지·VI 등 정상 사유). `bar_count`로 사후 확인한다.

### 5.3 실행 전체 실패

`.env` 필수 키 누락, DB 연결 실패, 토큰 발급 실패는 로그를 남기고 종료 코드 1로 즉시 끝낸다. 23:00 이후 실행에서 이런 실패가 나면 텔레그램으로 실행 실패 사실을 알린다(텔레그램 설정이 있는 경우).

### 5.4 알림

- 조건: 실행 시작 시각 ≥ 23:00 KST, 그리고 오늘 `error` 종목이 남아 있거나 5.3절 실패 발생
- 내용: 날짜, 실패 종목 수, `종목: 오류 메시지` 목록(최대 20줄, 초과분은 개수만)
- 전송 실패는 로그만 남긴다.

### 5.5 로그와 보안

- 표준 `logging`, 파일과 콘솔 동시 출력. 종목별 결과 1줄, 마지막 요약
- 앱키·시크릿·토큰·텔레그램 토큰은 로그와 예외 메시지에 넣지 않는다.

### 5.6 누락 확인 쿼리

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status <> 'ok' OR bar_count < 300
ORDER BY trade_date DESC, symbol;
```

## 6. 설치 (README에 기록)

1. PostgreSQL 설치(Windows 서비스, 자동 시작), `trader`·`trader_test` DB와 사용자 생성
2. `psql -f schema.sql`로 두 DB에 스키마 생성
3. `python -m venv .venv` → `pip install -r requirements.txt`
4. `.env.example`을 복사해 `.env` 작성
5. 텔레그램 봇 생성(BotFather), chat id 확인
6. 작업 스케줄러 등록(4.4절)

## 7. 테스트

- 도구: `pytest`만 사용. 모킹 라이브러리 없이 HTTP 전송 함수를 주입해 대체한다.
- KIS 응답: `tests/fixtures/`의 JSON (공식 샘플 기준)
- DB: 로컬 `trader_test`에 실제 연결. 각 테스트 전에 테이블을 비운다.
- 시각: 현재 시각을 인자로 받을 수 있게 해 23:00 조건을 테스트한다.

### 7.1 자동 테스트

| # | 대상 | 검증 |
|---|---|---|
| 1 | 페이지 순회 | 15:30부터 거꾸로 받아 09:00에서 종료, 겹친 봉 제거, 오름차순, 시각이 줄지 않으면 종료, 20페이지 초과 시 예외 |
| 2 | 재시도 | `EGW00201` 후 성공, 토큰 만료 후 재발급해 성공, 네트워크 오류 3회 연속 시 예외 |
| 3 | 저장 | 같은 봉 2회 저장 시 행 수 동일, `record_run` 같은 날 덮어쓰기, `done_symbols`가 `ok`·`empty`만 반환 |
| 4 | 수집기 | 15:35 이전·주말 실행은 아무것도 기록하지 않음, `done` 종목 건너뜀, A 실패해도 B 저장, 순회 중 실패한 종목 봉 0건 + `error` 기록, 빈 응답은 `empty` |
| 5 | 알림 | 23:00 이후 + `error` 존재 시에만 전송, 23:00 이전이나 전부 성공이면 미전송, 전송 실패해도 종료 코드 영향 없음 |

### 7.2 키 발급 후 수동 검증 (1회)

1. `symbols.txt`에 2종목(005930, 000660)만 두고 장 마감 후 실행
2. 종목당 약 381개 저장 확인
3. 임의 봉 3개를 HTS/MTS 1분 차트와 OHLCV 대조
4. 필드명·오류 코드·호출 한도가 1절 미확인 사항과 다르면 스펙과 코드 수정
5. 작업 스케줄러 등록 후 하루 동안 반복 실행이 대상 없이 종료되는지 확인

## 8. 완료 기준

- 7.1절 자동 테스트 전부 통과
- 7.2절 수동 검증 완료
- 5영업일 연속 `collect_runs`에 전 종목 `ok`(또는 공휴일 `empty`) 기록
