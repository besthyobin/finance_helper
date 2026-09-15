# KIS 1분봉 수집기

평일 장 마감 후 `symbols.txt` 종목의 당일 1분봉을 한국투자증권 Open API에서 받아 PostgreSQL에 저장한다.
설계: `docs/superpowers/specs/2026-09-14-kis-minute-collector-design.md`

## 설치

1. PostgreSQL 15 서비스(`postgresql-x64-15`, 포트 5432)에 DB와 사용자 생성

   ```powershell
   D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U postgres -c "CREATE USER trader WITH PASSWORD '비밀번호'" -c "CREATE DATABASE trader OWNER trader" -c "CREATE DATABASE trader_test OWNER trader"
   D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -f schema.sql
   ```

2. 가상환경과 의존성

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```

3. `.env.example`을 `.env`로 복사해 값 입력
   - `KIS_APP_KEY`, `KIS_APP_SECRET`: KIS Developers에서 발급한 **실전** 앱키 (시세 조회만 사용)
   - `TELEGRAM_BOT_TOKEN`: BotFather로 만든 봇 토큰
   - `TELEGRAM_CHAT_ID`: 봇에게 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`의 `chat.id`

4. 테스트: `.\.venv\Scripts\python -m pytest tests -v`

## 작업 스케줄러 등록

평일 16:00부터 1시간마다 23:00까지 실행한다. 관리자 PowerShell에서 `trader` 폴더 기준으로 실행:

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "collector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 16:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 16:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
Register-ScheduledTask -TaskName "KIS 분봉 수집기" -Action $action -Trigger $trigger -Settings $settings
```

- 평일 장 마감 후 23:00 전까지 PC가 켜져 있어야 한다. 당일분봉은 다음 날 다시 받을 수 없다.
- 로그: `logs/collector-YYYY-MM-DD.log`

## 운영 DB 스키마 갱신

백테스터 추가로 `minute_bars`에 `source` 컬럼과 결과 테이블이 생겼다. 기존 데이터는 `source='kis'`로 보존된다.

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql
```

코드 갱신 직후, 수집기 다음 실행(평일 16:00~23:00) 전에 적용한다. 적용 전에는 수집기가 모든 종목을 error로 기록한다(당일분봉은 다음 날 복구 불가). 적용 후 확인: SELECT source, count(*) FROM minute_bars GROUP BY 1;

## Yahoo 임시 데이터 적재

KIS 데이터가 쌓이기 전 개발·검증용. 최근 약 7거래일, 하루 360봉(09:00~14:59, 15시 이후 봉 없음). 비공식 API라 언제든 막힐 수 있다.

```powershell
.\.venv\Scripts\python load_yahoo.py              # symbols.txt 전 종목
.\.venv\Scripts\python load_yahoo.py 005930 000660
```

## 백테스트

```powershell
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source yahoo --from 2026-09-04 --to 2026-09-14
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--strategy` | (필수) | 쉼표 구분. 전략마다 실행 1건 저장 |
| `--source` | (필수) | `kis` 또는 `yahoo` |
| `--from`, `--to` | (필수) | KST 날짜, 양끝 포함 |
| `--symbols` | 전 종목 | 쉼표 구분 종목코드 |
| `--param key=value` | 전략 기본값 | 여러 번 지정. 그 키를 가진 전략에만 적용 |
| `--fee` | 0.00015 | 매수·매도 각각 |
| `--tax` | 0.002 | 매도 거래세 (실제 세율 확인 필요) |
| `--slippage` | 0.0005 | 매수가↑·매도가↓ |
| `--exit-at` | 15:15 | 이후 진입 금지, 보유분 그 봉 시가에 청산 |

전략 (`strategies.py`):
- `ma_cross` (`short=5`, `long=20`): 종가 단기 이동평균이 장기를 상향 돌파하면 매수, 하향 돌파하면 매도
- `orb` (`range_end=09:30`, `stop_pct=-1.0`, `target_pct=2.0`): 범위 시간 고가를 종가로 돌파하면 하루 1회 매수, 손절·목표 도달 시 매도

체결 규칙: 신호 다음 봉 시가 체결, 매수만, 종목당 1포지션, 장 마감 전 강제 청산(데이터가 먼저 끝나면 마지막 봉 종가).

새 전략 추가: `Strategy`를 상속해 `name`, `defaults`, `on_bar(bar, entry_price)`를 구현하고 `STRATEGIES`에 등록.

결과 조회 예 (일별·시간대별):

```sql
SELECT (entry_ts AT TIME ZONE 'Asia/Seoul')::date AS day,
       CASE WHEN (entry_ts AT TIME ZONE 'Asia/Seoul')::time < '12:00' THEN '09-12' ELSE '12-15' END AS slot,
       count(*) AS trades, avg(return_pct) AS avg_ret, sum(return_pct) AS sum_ret
FROM backtest_trades
WHERE run_id = 1
GROUP BY 1, 2
ORDER BY 1, 2;
```

## 누락 확인

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status <> 'ok' OR bar_count < 300
ORDER BY trade_date DESC, symbol;
```

## 키 발급 후 수동 검증 (1회)

1. `symbols.txt`를 005930, 000660 두 종목으로 두고 평일 15:35 이후 `.\.venv\Scripts\python collector.py` 실행
2. 종목당 약 381개 저장 확인: `SELECT symbol, count(*) FROM minute_bars WHERE source = 'kis' GROUP BY symbol;`
3. 임의 봉 3개를 HTS/MTS 1분 차트와 시가·고가·저가·종가·거래량 대조
4. 응답 필드명, 오류 코드(`EGW00123`, `EGW00133`, `EGW00201`), 호출 한도, 주식일별분봉조회 API 사용 가능 여부가 설계와 다르면 스펙과 코드 수정
5. 작업 스케줄러 등록 후 하루 동안 두 번째 실행부터 `skipped`만 나오는지 로그 확인
6. 5영업일 연속 `collect_runs`에 전 종목 `ok`(공휴일은 `empty`) 확인
