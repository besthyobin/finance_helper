# Finance Helper

주식 투자를 위한 종합 정보 대시보드 — Next.js 기반, Vercel 배포 최적화

## 주요 기능

| 페이지 | 설명 | 데이터 소스 |
|--------|------|------------|
| 📊 대시보드 | 전체 시장 개요, 주요 지수, 최신 뉴스 | Yahoo Finance, RSS |
| 📰 글로벌 뉴스 | 미국・한국・중국 금융 뉴스 | 주요 언론사 RSS |
| 🔥 이슈 종목 | 지금 뜨는 트렌딩 주식 | Yahoo Finance |
| 🇺🇸 미국 시장 | NASDAQ/NYSE Top 100 + S&P500/NASDAQ/DOW | Yahoo Finance, FMP |
| 🇰🇷 한국 시장 | KOSPI/KOSDAQ Top 100 + 주요 지수 | FMP |
| 🇨🇳 중국 시장 | 상하이/선전 Top 100 + 지수 | FMP |
| 🏦 헤지펀드 | 8대 헤지펀드 13F 보고서 현황 | SEC EDGAR (무료) |
| 🚀 IPO 캘린더 | 상장 예정 및 최근 IPO | FMP |
| 💼 투자자 포트폴리오 | 버핏, 달리오 등 13F 포트폴리오 | SEC EDGAR (무료) |

## 기술 스택

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS (다크 테마)
- **Stock Data**: `yahoo-finance2` (API 키 불필요)
- **SEC Data**: SEC EDGAR API (무료, API 키 불필요)
- **Deployment**: Vercel

## 시작하기

### 1. 의존성 설치
```bash
npm install
```

### 2. 환경 변수 설정
```bash
cp .env.example .env.local
```

`.env.local` 파일을 열어 API 키를 입력하세요:

| 변수 | 필수 | 설명 | 발급 URL |
|------|------|------|---------|
| `FMP_API_KEY` | ⭐ 권장 | 한국/중국 시장, IPO 캘린더 | [financialmodelingprep.com](https://financialmodelingprep.com/developer/docs) |
| `GNEWS_API_KEY` | 선택 | 추가 뉴스 소스 | [gnews.io](https://gnews.io) |
| `NEWS_API_KEY` | 선택 | NewsAPI 뉴스 | [newsapi.org](https://newsapi.org) |

> **FMP 무료 플랜**: 하루 250 API 호출 (캐시 적용으로 충분)
> **SEC EDGAR, Yahoo Finance**: 완전 무료, 키 불필요

### 3. 개발 서버 실행
```bash
npm run dev
```

브라우저에서 `http://localhost:3000` 접속

## Vercel 배포

```bash
npm install -g vercel
vercel
```

또는 GitHub에 push 후 [vercel.com](https://vercel.com)에서 연결

**환경 변수 설정**: Vercel 대시보드 → Settings → Environment Variables에 API 키 추가

## API Routes

| 경로 | 설명 | 캐시 |
|------|------|------|
| `GET /api/news?category=all` | 글로벌 뉴스 RSS | 5분 |
| `GET /api/stocks/indices` | 주요 지수 현황 | 1분 |
| `GET /api/stocks/trending` | 트렌딩 종목 | 1분 |
| `GET /api/stocks/top100/[country]` | 국가별 Top 100 | 10분 |
| `GET /api/investors?investor=buffett` | 투자자 포트폴리오 | 24시간 |
| `GET /api/hedge-funds` | 헤지펀드 현황 | 24시간 |
| `GET /api/ipo` | IPO 캘린더 | 1시간 |
