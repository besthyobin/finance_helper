# 토스증권 1분봉 수집기, 백테스터, 모의투자, 종목 선정

`symbols.txt` 종목의 1분봉을 토스증권 Open API에서 받아 PostgreSQL에 저장하고, 저장한 봉으로 전략을 백테스트하고, 장중 실시간 봉으로 모의투자하고, 매일 아침 모의투자 종목을 고른다.
설계: `docs/superpowers/specs/2026-09-15-toss-collector-design.md`, `docs/superpowers/specs/2026-09-15-backtester-design.md`, `docs/superpowers/specs/2026-09-17-paper-trader-design.md`, `docs/superpowers/specs/2026-09-17-daily-selector-design.md`

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
   - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`: 토스증권 WTS 설정 > Open API에서 발급 (시세 조회만 사용)
   - 같은 메뉴 하단 **허용 IP 관리**에 이 PC의 공인 IP를 등록한다. 없으면 403 `access_denied`로 실행 실패
   - `TOSS_RPS`: 초당 호출 수. 차트 API 한도(초당 20회)보다 낮게 둔다. 기본 15
   - `GMAIL_ADDRESS`: 알림을 보내고 받을 Gmail 주소(나에게 보내기)
   - `GMAIL_APP_PASSWORD`: Google 계정 > 보안에서 2단계 인증을 켠 뒤 `https://myaccount.google.com/apppasswords`에서 발급한 16자리 앱 비밀번호(공백 없이). 계정 비밀번호가 아니다. 비어 있으면 알림 없이 로그만 남긴다

4. 테스트: `.\.venv\Scripts\python -m pytest tests -v`

## 운영 DB 스키마 갱신

`minute_bars.source` 컬럼과 백테스트 결과 테이블이 필요하다. 수집기를 처음 실행하기 전에 적용한다(여러 번 적용해도 안전, 기존 데이터 보존).

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql
```

확인: `SELECT source, count(*) FROM minute_bars GROUP BY 1;`

## 수집기

```powershell
.\.venv\Scripts\python collector.py
```

한 번 실행하면 종목마다 최근 1461일(약 4년)의 평일 중 `collect_runs`에 `ok`·`empty`가 없는 날짜를 최신 날짜부터 받는다. 20:10 이후 실행이면 오늘도 포함한다.

- 저장: `minute_bars.source='toss'`, 받은 봉 전부(08:00~19:59). NXT 미거래 종목은 09:00~15:29만 오고, NXT 출범 전 날짜는 확장 시간이 거래량 0인 채움 봉이다. `ts`는 봉 시작 시각, 가격은 수정주가 미적용 원래 체결가
- 기록: 종목·날짜마다 `collect_runs`에 `ok`(봉 수) / `empty`(휴장일, 상장 전 등) / `error`. `error` 날짜는 다음 실행에서 다시 받는다
- 한 종목에서 오류가 5번 연속 나면 이번 실행에서는 그 종목의 남은 날짜를 건너뛴다
- 날짜 오류가 있으면 실행 끝에 메일 알림 1통을 보낸다. 인증·DB·설정 오류로 실행 전체가 실패하면 실행 실패 알림을 보내고 종료 코드 1
- 첫 백필은 종목당 약 4분(50종목 3~4시간)이다. 작업 스케줄러 등록 전에 수동으로 한 번 실행한다
- 동시에 두 개를 실행하지 않는다. 수동 실행은 스케줄 시각(20:30)을 피한다
- 로그: `logs/collector-YYYY-MM-DD.log`

## 작업 스케줄러 등록

평일 20:30에 1회 실행한다(NXT 마감 20:00 이후 당일 완성본). PC가 꺼져 있어 놓친 날은 다음 실행이 채운다. 관리자 PowerShell에서 `trader` 폴더 기준으로 실행:

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "collector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 20:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName "토스 분봉 수집기" -Action $action -Trigger $trigger -Settings $settings
```

예전에 `KIS 분봉 수집기` 작업을 등록했다면 지운다: `Unregister-ScheduledTask -TaskName "KIS 분봉 수집기" -Confirm:$false`

## 누락 확인

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status = 'error' OR (status = 'ok' AND bar_count < 390)
ORDER BY trade_date DESC, symbol;
```

정상 거래일 봉 수는 NXT 거래 종목 720, 정규장만 거래하는 종목 390이다. 수능일·연초 개장일 같은 단축 거래일은 더 적을 수 있다.

## 백테스트

```powershell
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source toss --from 2026-08-17 --to 2026-09-14
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--strategy` | (필수) | 쉼표 구분. 전략마다 실행 1건 저장 |
| `--source` | (필수) | `toss` |
| `--from`, `--to` | (필수) | KST 날짜, 양끝 포함 |
| `--symbols` | 전 종목 | 쉼표 구분 종목코드 |
| `--param key=value` | 전략 기본값 | 여러 번 지정. 그 키를 가진 전략에만 적용 |
| `--fee` | 0.00015 | 매수·매도 각각 |
| `--tax` | 0.002 | 매도 거래세 (실제 세율 확인 필요) |
| `--slippage` | 0.0005 | 매수가↑·매도가↓ |
| `--exit-at` | 15:15 | 이후 진입 금지, 보유분 그 봉 시가에 청산 |
| `--session` | regular | `regular`: 09:00~15:29 시작 봉만, `all`: 저장된 봉 전부(08:00~19:59) |

`--session all`이면 08시대 봉부터 전략에 들어가고, `--exit-at` 기본값(15:15) 이후에는 진입하지 않고 청산한다. 확장 시간까지 거래하려면 `--exit-at 19:45`처럼 확장 시간에 맞게 지정한다.

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

## 모의투자

```powershell
.\.venv\Scripts\python paper.py
```

`paper_symbols.txt` 종목을 orb 기본 파라미터로 장중 가상 체결한다. 실주문은 하지 않는다.

- 처음 실행 전 위 "운영 DB 스키마 갱신"으로 `schema.sql`을 적용한다(`paper_status`, `paper_trades` 테이블). 여러 번 적용해도 안전하다
- 설정: `.env`의 `PAPER_CAPITAL`(모의 총액, 예 2000000). 종목당 금액 = 총액 ÷ 종목 수, 수량은 슬리피지 반영 매수가로 나눈 정수 주(내림). 0주면 거래하지 않는다
- 비용: 수수료 0.015%, 매도세 0.20%, 슬리피지 0.05%, 15:15 강제 청산, 정규장 봉만 (백테스트 기본값과 같음)
- 흐름: 휴장·정규장 시간 변경일이면 바로 종료 → 오늘 이미 끝난 봉으로 따라잡기 → 매분 :15초에 끝난 봉 조회 → 15:31에 오늘 봉으로 백테스트를 다시 돌려 실시간 거래와 비교 → 메일 요약 후 종료
- 알림(Gmail, 첫 줄이 메일 제목): 시작(따라잡기 완료), 매수·매도 체결, 같은 종목 5분 연속 조회 실패, 일일 요약(마감 비교 `일치`/`불일치`). 재시작 따라잡기 중 체결은 다시 알리지 않는다
- 기록: `paper_status`(종목별 현재 상태), `paper_trades`(완결 거래). 중간에 꺼졌다 다시 켜면 같은 상태로 복구되고 거래는 중복 저장되지 않는다
- 차트용으로 장중에 받은 정규장 봉을 `minute_bars.source='toss_live'`로 저장한다. 수집기 확정 봉(`toss`)과 섞이지 않으며 백테스트는 `--source toss`로 확정 봉만 쓴다
- 로그: `logs/paper-YYYY-MM-DD.log`
- 토스 토큰은 클라이언트당 1개라 두 프로세스가 동시에 재발급하면 서로 무효화한다. 장중(08:55~15:31)에는 `collector.py`를 수동 실행하지 않는다. PC가 20:30에 꺼져 있었다면 수집기 작업(StartWhenAvailable)이 다음 날 장중에 켜질 때 실행될 수 있으니, 그런 날은 모의투자 알림을 확인한다
- 봉 확정 판단은 PC 시계를 쓴다. Windows 시간 동기화를 켜 둔다

작업 스케줄러 등록(관리자 PowerShell, `trader` 폴더 기준):

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "paper.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:55
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName "토스 모의투자" -Action $action -Trigger $trigger -Settings $settings
```

거래 확인:

```sql
SELECT (exit_ts AT TIME ZONE 'Asia/Seoul')::date AS day, symbol, qty, entry_price, exit_price, pnl_krw, exit_reason
FROM paper_trades ORDER BY exit_ts DESC LIMIT 20;
```

화면(이 PC 브라우저 전용, 읽기 전용):

```powershell
.\.venv\Scripts\python web.py
```

`http://127.0.0.1:8765`에서 종목별 현재 상태(보유·평가손익·마지막 봉), 고른 종목의 오늘 1분봉 캔들 차트(매수 ▲·매도 ▼ 표시, 확대 슬라이더), 오늘 체결, 일별 손익과 누적 손익 차트를 5초마다 갱신한다. 캔들은 장중 수신 봉(`toss_live`)을, 없으면 수집기 확정 봉을 쓴다(20:30 전까지 장 마감 후에는 비어 있을 수 있다). 차트는 ECharts(Apache 2.0)를 jsDelivr CDN에서 받으므로 인터넷 연결이 필요하다. 장중에 2분 넘게 상태 갱신이 없으면 "모의투자 프로세스 멈춤"을 띄운다(휴장일에도 뜰 수 있다). `paper.py`와 따로 실행하므로 장 마감 후에도 볼 수 있다.

## 매일 종목 선정

```powershell
.\.venv\Scripts\python selector.py
```

평일 07:30에 실행해 모의투자 종목을 최대 5개 고르고 `paper_symbols.txt`를 갱신한 뒤 결과를 메일로 보낸다.

- 후보: 시장 거래대금 1년 상위 100(투자 유의 제외) 중 보통주·상장 중·전일 종가 10만 원 이하
- 위험 제외: 거래정지, 활성 경고(정리매매·단기과열·투자경고·투자위험), 최근 5일 평균 공매도 거래대금 비중 10% 이상, 신용융자 잔고율 8% 이상, 최근 20일 일간 수익률 표준편차 5% 이상, 최근 20일 일평균 거래대금 100억 원 미만(일봉 부족 포함)
- 백필: 남은 후보의 최근 365일 1분봉 중 없는 날짜만 받는다(`collect_runs` 재사용)
- 백테스트: 모의투자와 같은 조건(orb 기본값, 정규장, 수수료·세금·슬리피지). 청산일 기준 91일 전까지는 선정 구간(거래 20건 이상·평균 플러스), 이후는 확인 구간(거래 있음·평균 플러스)
- 선정: 확인 구간 평균 수익률 순 최대 5개. 없으면 파일을 바꾸지 않는다(전날 종목 유지)
- 마감: 08:45를 넘기면 멈추고 전날 종목 유지 메일을 보낸다
- 기록: `selection_candidates`(날짜·종목별 상태, 제외 사유, 지표·백테스트 수치), 로그 `logs/selector-YYYY-MM-DD.log`
- 첫 실행은 후보 1년치 백필에 1~2시간 걸리므로 작업 스케줄러 등록 전에 `.\.venv\Scripts\python selector.py --no-deadline`으로 수동 실행한다. 장중(08:55~15:31)과 수집기 시각(20:30)을 피한다

작업 스케줄러 등록(`trader` 폴더 기준, 관리자 권한 불필요):

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "selector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 07:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "토스 종목 선정" -Action $action -Trigger $trigger -Settings $settings
```

결과 확인:

```sql
SELECT status, reason, count(*) FROM selection_candidates WHERE run_date = CURRENT_DATE GROUP BY 1, 2 ORDER BY 1, 3 DESC;
```

## 첫 실행 수동 검증 (1회)

1. 위 "운영 DB 스키마 갱신" 적용
2. 토스 허용 IP 등록 확인, `.env`에 `TOSS_*` 값 확인. 예전 KIS 토큰 캐시 `.token.json`이 있으면 지운다
3. `symbols.txt`를 005930, 000660 두 종목으로 두고 `.\.venv\Scripts\python collector.py` (약 8분)
4. `SELECT trade_date, status, bar_count FROM collect_runs WHERE symbol='005930' ORDER BY trade_date DESC LIMIT 10;` — 최근 거래일 720, 주말 행 없음, 휴장일 `empty`
5. 임의 봉 3개를 토스 앱 1분 차트와 시가·고가·저가·종가·거래량 대조. 앱에서 `09:01`로 보이는 봉이 DB의 `09:00` 봉이어야 한다
6. 곧바로 다시 실행 → 받을 날짜 0개(20:10 이후면 오늘 1개 이하)
7. `.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source toss --from <1개월 전> --to <어제>` 결과 확인
8. 작업 스케줄러 등록 후 다음 영업일 로그 확인, 5영업일 연속 실패 알림 없음
