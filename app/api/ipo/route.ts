import { NextResponse } from 'next/server';
import { buildFmpUrl } from '@/lib/utils';

export async function GET() {
  try {
    const url = buildFmpUrl('/ipo_calendar', {
      from: getDateString(0),
      to: getDateString(90),
    });

    const res = await fetch(url, { next: { revalidate: 3600 } }); // 1시간 캐시
    if (!res.ok) throw new Error(`FMP error ${res.status}`);
    const data = await res.json();

    const ipos = Array.isArray(data) ? data.map((item: Record<string, unknown>) => ({
      symbol: item.symbol as string,
      companyName: (item.company || item.companyName) as string,
      exchange: item.exchange as string,
      country: guessCountry(String(item.exchange || '')),
      ipoDate: item.date as string,
      priceRange: item.priceRange as string,
      shares: item.shares as number,
      estimatedMarketCap: item.marketCap as number,
      sector: item.industry as string,
      status: getStatus(item.date as string),
    })) : [];

    return NextResponse.json({ ipos });
  } catch (err) {
    console.warn('IPO calendar error:', err);
    // 샘플 fallback 데이터
    return NextResponse.json({ ipos: [], error: 'FMP API key required' });
  }
}

function getDateString(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().split('T')[0];
}

function guessCountry(exchange: string): 'US' | 'KR' | 'CN' {
  const ex = exchange.toUpperCase();
  if (ex.includes('KRX') || ex.includes('KOSPI') || ex.includes('KOSDAQ')) return 'KR';
  if (ex.includes('SHA') || ex.includes('SHE') || ex.includes('HKEX')) return 'CN';
  return 'US';
}

function getStatus(dateStr: string): 'upcoming' | 'priced' | 'filed' {
  if (!dateStr) return 'filed';
  return new Date(dateStr) > new Date() ? 'upcoming' : 'priced';
}
