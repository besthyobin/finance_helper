import { NextResponse } from 'next/server';
import { buildFmpUrl } from '@/lib/utils';

// 주요 헤지펀드 목록 (SEC EDGAR CIK)
const HEDGE_FUNDS = [
  { name: 'Bridgewater Associates', manager: '레이 달리오', cik: '0001350694' },
  { name: 'Citadel', manager: '켄 그리핀', cik: '0001423528' },
  { name: 'Renaissance Technologies', manager: '짐 사이먼스', cik: '0001037389' },
  { name: 'Two Sigma', manager: '존 오버데크', cik: '0001450122' },
  { name: 'Pershing Square', manager: '빌 애크먼', cik: '0001336528' },
  { name: 'Tiger Global', manager: '체이스 콜먼', cik: '0001167483' },
  { name: 'Viking Global', manager: '앤드루 할라블라', cik: '0001103804' },
  { name: 'Third Point', manager: '댄 로브', cik: '0001040570' },
];

async function getFundTopHoldings(cik: string, fundName: string) {
  try {
    const paddedCik = cik.replace(/^0+/, '').padStart(10, '0');
    const url = `https://data.sec.gov/submissions/CIK${paddedCik}.json`;

    const res = await fetch(url, {
      headers: { 'User-Agent': 'FinanceHelper finance@example.com' },
      next: { revalidate: 86400 },
    });
    if (!res.ok) return null;

    const data = await res.json();
    const forms: string[] = data.filings?.recent?.form || [];
    const dates: string[] = data.filings?.recent?.filingDate || [];
    const accessions: string[] = data.filings?.recent?.accessionNumber || [];

    // 최신 13F-HR 찾기
    let filingDate = '';
    let accessionNumber = '';
    for (let i = 0; i < forms.length; i++) {
      if (forms[i] === '13F-HR') {
        filingDate = dates[i];
        accessionNumber = accessions[i];
        break;
      }
    }

    const aum = data.entityType ? data.ein : null;
    return {
      name: fundName,
      filingDate,
      accessionNumber,
      cik: paddedCik,
      totalAssets: null as number | null,
      aum,
    };
  } catch {
    return null;
  }
}

// FMP 기관 보유 뉴스 (헤지펀드 관련)
async function fetchHedgeFundNews() {
  try {
    const url = buildFmpUrl('/stock_news', {
      tickers: 'BRK.B,SPY,QQQ',
      limit: '10',
    });
    const res = await fetch(url, { next: { revalidate: 300 } });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export async function GET() {
  const [fundData, news] = await Promise.all([
    Promise.all(HEDGE_FUNDS.map(f => getFundTopHoldings(f.cik, f.name).then(d => ({
      ...f,
      ...d,
    })))),
    fetchHedgeFundNews(),
  ]);

  const funds = fundData.filter(f => f !== null).map(f => ({
    name: f.name,
    manager: f.manager,
    cik: f.cik,
    filingDate: f.filingDate || null,
    accessionNumber: f.accessionNumber || null,
  }));

  return NextResponse.json({ funds, news });
}
