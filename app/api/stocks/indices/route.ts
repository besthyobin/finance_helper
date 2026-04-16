import { NextResponse } from 'next/server';
import { fetchQuotes } from '@/lib/yahoo-finance';
import { INDEX_SYMBOLS } from '@/lib/utils';

export async function GET() {
  try {
    const symbols = Object.values(INDEX_SYMBOLS);
    const quotesArr = await fetchQuotes(symbols);

    const labelMap: Record<string, { name: string; country: 'US' | 'KR' | 'CN' | 'JP' }> = {
      '^GSPC':     { name: 'S&P 500',        country: 'US' },
      '^IXIC':     { name: 'NASDAQ',          country: 'US' },
      '^DJI':      { name: 'Dow Jones',       country: 'US' },
      '^KS11':     { name: 'KOSPI',           country: 'KR' },
      '^KQ11':     { name: 'KOSDAQ',          country: 'KR' },
      '000001.SS': { name: '\uc0c1\ud558\uc774 \uc885\ud569',     country: 'CN' },
      '399001.SZ': { name: '\uc120\uc804 \uc131\ubd84',       country: 'CN' },
      '^HSI':      { name: '\ud56d\uc148 \uc9c0\uc218',       country: 'CN' },
    };

    const indices = quotesArr
      .filter(q => q && q.regularMarketPrice)
      .map(q => ({
        symbol: q.symbol,
        name: labelMap[q.symbol]?.name || q.shortName || q.symbol,
        value: q.regularMarketPrice ?? 0,
        change: q.regularMarketChange ?? 0,
        changePercent: q.regularMarketChangePercent ?? 0,
        country: labelMap[q.symbol]?.country || 'US',
        previousClose: q.regularMarketPreviousClose ?? 0,
      }));

    return NextResponse.json({ indices });
  } catch (err) {
    console.error('Market indices error:', err);
    return NextResponse.json({ indices: [], error: 'Failed to fetch indices' }, { status: 200 });
  }
}
