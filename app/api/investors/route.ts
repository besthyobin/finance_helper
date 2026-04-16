import { NextResponse } from 'next/server';
import { BERKSHIRE_CIK } from '@/lib/utils';

// SEC EDGAR API를 이용한 투자자 포트폴리오 (13F 보고서)
// 완전 무료, API 키 불필요

interface EdgarFiling {
  accessionNumber: string;
  filingDate: string;
  reportDate: string;
  form: string;
}

interface Holding {
  nameOfIssuer: string;
  titleOfClass: string;
  cusip: string;
  value: number;      // 천 달러 단위
  sshPrnamt: number;  // 주식 수
  putCall?: string;
}

const FAMOUS_INVESTORS: Record<string, { name: string; fund: string; cik: string }> = {
  buffett:   { name: '워런 버핏',    fund: 'Berkshire Hathaway',   cik: BERKSHIRE_CIK },
  ackman:    { name: '빌 애크먼',    fund: 'Pershing Square',      cik: '0001336528' },
  paulson:   { name: '존 폴슨',     fund: 'Paulson & Co.',        cik: '0001035674' },
  simons:    { name: '짐 사이먼스',  fund: 'Renaissance Technologies', cik: '0001037389' },
  dalio:     { name: '레이 달리오',  fund: 'Bridgewater Associates', cik: '0001350694' },
  tepper:    { name: '데이비드 테퍼', fund: 'Appaloosa Management', cik: '0000315066' },
};

// CUSIP → 심볼 매핑 (주요 종목 하드코딩)
const CUSIP_TO_SYMBOL: Record<string, string> = {
  '037833100': 'AAPL',  '594918104': 'MSFT',  '02079K305': 'GOOGL',
  '023135106': 'AMZN',  '88160R101': 'TSLA',  '67066G104': 'NVDA',
  '30303M102': 'META',  '191216100': 'KO',    '254687106': 'DVA',
  '404121106': 'HPQ',   '097023105': 'BOA',   '172967424': 'C',
  '808513105': 'SCHW',  '92204A306': 'VZ',    '084670702': 'BYD',
  '92826C839': 'V',     '58733R102': 'MA',    '713448108': 'PG',
};

async function getLatest13F(cik: string): Promise<{ filingDate: string; accessionNumber: string } | null> {
  const paddedCik = cik.replace(/^0+/, '').padStart(10, '0');
  const url = `https://data.sec.gov/submissions/CIK${paddedCik}.json`;

  const res = await fetch(url, {
    headers: { 'User-Agent': 'FinanceHelper finance@example.com' },
    next: { revalidate: 86400 }, // 24시간 캐시
  });
  if (!res.ok) return null;

  const data = await res.json();
  const filings: { form: string[]; filingDate: string[]; accessionNumber: string[] } = data.filings?.recent || {};

  const forms: string[] = filings.form || [];
  const dates: string[] = filings.filingDate || [];
  const accessions: string[] = filings.accessionNumber || [];

  for (let i = 0; i < forms.length; i++) {
    if (forms[i] === '13F-HR') {
      return { filingDate: dates[i], accessionNumber: accessions[i] };
    }
  }
  return null;
}

async function parse13FHoldings(cik: string, accessionNumber: string): Promise<Holding[]> {
  const paddedCik = cik.replace(/^0+/, '').padStart(10, '0');
  const acc = accessionNumber.replace(/-/g, '');
  const baseUrl = `https://www.sec.gov/Archives/edgar/data/${parseInt(paddedCik)}/${acc}`;

  // 인덱스 파일에서 XML 파일명 찾기
  const indexUrl = `${baseUrl}/${accessionNumber}-index.htm`;
  const indexRes = await fetch(indexUrl, {
    headers: { 'User-Agent': 'FinanceHelper finance@example.com' },
    next: { revalidate: 86400 },
  });

  if (!indexRes.ok) return [];
  const indexHtml = await indexRes.text();

  // infotable XML 파일 이름 추출
  const xmlMatch = indexHtml.match(/href="([^"]*infotable[^"]*\.xml)"/i)
    || indexHtml.match(/href="([^"]*form13fInfoTable[^"]*\.xml)"/i);
  if (!xmlMatch) return [];

  const xmlPath = xmlMatch[1].replace(/^\/Archives\//, '');
  const xmlUrl = `https://www.sec.gov/Archives/${xmlPath}`;

  const xmlRes = await fetch(xmlUrl, {
    headers: { 'User-Agent': 'FinanceHelper finance@example.com' },
    next: { revalidate: 86400 },
  });
  if (!xmlRes.ok) return [];

  const xml = await xmlRes.text();
  const holdings: Holding[] = [];

  const entryRegex = /<infoTable>([\s\S]*?)<\/infoTable>/gi;
  let match;
  while ((match = entryRegex.exec(xml)) !== null) {
    const block = match[1];
    const name  = extractXml(block, 'nameOfIssuer');
    const title = extractXml(block, 'titleOfClass');
    const cusip = extractXml(block, 'cusip');
    const value = parseFloat(extractXml(block, 'value') || '0');
    const shares = parseFloat(extractXml(block, 'sshPrnamt') || '0');
    const putCall = extractXml(block, 'putCall') || undefined;

    if (name) {
      holdings.push({ nameOfIssuer: name, titleOfClass: title, cusip, value, sshPrnamt: shares, putCall });
    }
  }

  return holdings.sort((a, b) => b.value - a.value);
}

function extractXml(xml: string, tag: string): string {
  const m = xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return m ? m[1].trim() : '';
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const investorKey = searchParams.get('investor') || 'buffett';
  const investor = FAMOUS_INVESTORS[investorKey] || FAMOUS_INVESTORS['buffett'];

  try {
    const latest = await getLatest13F(investor.cik);
    if (!latest) throw new Error('No 13F filing found');

    const rawHoldings = await parse13FHoldings(investor.cik, latest.accessionNumber);

    const totalValue = rawHoldings.reduce((sum, h) => sum + h.value, 0);

    const holdings = rawHoldings.slice(0, 50).map((h, i) => {
      const symbol = CUSIP_TO_SYMBOL[h.cusip];
      return {
        rank: i + 1,
        symbol: symbol || null,
        name: h.nameOfIssuer,
        value: h.value, // 천 달러
        shares: h.sshPrnamt,
        portfolioPercent: totalValue > 0 ? (h.value / totalValue) * 100 : 0,
        cusip: h.cusip,
        titleOfClass: h.titleOfClass,
        putCall: h.putCall,
      };
    });

    return NextResponse.json({
      investor: investor.name,
      fund: investor.fund,
      cik: investor.cik,
      filingDate: latest.filingDate,
      totalValue,          // 천 달러
      holdingsCount: rawHoldings.length,
      holdings,
      availableInvestors: Object.entries(FAMOUS_INVESTORS).map(([key, v]) => ({
        key, name: v.name, fund: v.fund,
      })),
    });
  } catch (err) {
    console.error('Investor portfolio error:', err);
    return NextResponse.json(
      { error: 'SEC EDGAR 데이터를 불러오는 중 오류가 발생했습니다.', holdings: [] },
      { status: 200 }
    );
  }
}
