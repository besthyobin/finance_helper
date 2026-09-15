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

## 누락 확인

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status <> 'ok' OR bar_count < 300
ORDER BY trade_date DESC, symbol;
```

## 키 발급 후 수동 검증 (1회)

1. `symbols.txt`를 005930, 000660 두 종목으로 두고 평일 15:35 이후 `.\.venv\Scripts\python collector.py` 실행
2. 종목당 약 381개 저장 확인: `SELECT symbol, count(*) FROM minute_bars GROUP BY symbol;`
3. 임의 봉 3개를 HTS/MTS 1분 차트와 시가·고가·저가·종가·거래량 대조
4. 응답 필드명, 오류 코드(`EGW00123`, `EGW00133`, `EGW00201`), 호출 한도, 주식일별분봉조회 API 사용 가능 여부가 설계와 다르면 스펙과 코드 수정
5. 작업 스케줄러 등록 후 하루 동안 두 번째 실행부터 `skipped`만 나오는지 로그 확인
6. 5영업일 연속 `collect_runs`에 전 종목 `ok`(공휴일은 `empty`) 확인
