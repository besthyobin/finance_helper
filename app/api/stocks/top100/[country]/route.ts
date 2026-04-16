import { NextResponse } from 'next/server';
import { buildFmpUrl } from '@/lib/utils';
import { fetchQuotes } from '@/lib/yahoo-finance';

// 나라별 Top 100 종목 (FMP API + Yahoo Finance fallback)
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ country: string }> }
) {
  const { country: countryRaw } = await params;
  const country = (countryRaw || 'us').toUpperCase() as 'US' | 'KR' | 'CN';

  try {
    // FMP API: 시총 상위 종목
    const exchangeMap: Record<string, string> = {
      US: 'NASDAQ,NYSE',
      KR: 'KSC,KSQ',   // FMP는 KSC = KOSPI, KSQ = KOSDAQ
      CN: 'SHH,SHZ',   // SHH = 상하이, SHZ = 선전
    };
    const exchange = exchangeMap[country] || 'NASDAQ,NYSE';

    // FMP 스크리너 API
    const url = buildFmpUrl('/stock-screener', {
      exchange,
      limit: '100',
      sort: 'marketCap',
      order: 'desc',
    });

    const res = await fetch(url, { next: { revalidate: 600 } }); // 10분 캐시
    if (!res.ok) throw new Error(`FMP error ${res.status}`);

    const data = await res.json();
    if (!Array.isArray(data) || data.length === 0) throw new Error('empty FMP response');

    const stocks = data.slice(0, 100).map((item: Record<string, unknown>, idx: number) => ({
      rank: idx + 1,
      symbol: item.symbol as string,
      name: (item.companyName || item.name) as string,
      price: (item.price as number) ?? 0,
      change: (item.changesPercentage as number) ?? 0,
      changePercent: (item.changesPercentage as number) ?? 0,
      volume: (item.volume as number) ?? 0,
      marketCap: (item.marketCap as number) ?? 0,
      country,
      exchange: item.exchangeShortName as string,
      sector: item.sector as string,
    }));

    return NextResponse.json({ stocks, country });
  } catch (err) {
    // Fallback: Yahoo Finance (미국만 지원)
    console.warn('FMP fallback for country top100:', err);
    if (country === 'US') {
      return fallbackUS();
    }
    return NextResponse.json({ stocks: [], country, error: 'API key required for this market' }, { status: 200 });
  }
}

async function fallbackUS() {
  try {
    const sp500Top = [
      'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','BRK-B','LLY','UNH',
      'JPM','V','XOM','AVGO','MA','PG','HD','COST','JNJ','ABBV',
      'WMT','MRK','BAC','NFLX','CRM','CVX','AMD','ORCL','KO','PEP',
      'WFC','ACN','TMO','DHR','MCD','ADBE','LIN','CSCO','IBM','GE',
      'ABT','TXN','INTU','CAT','RTX','AMGN','GS','MS','ISRG','AXP',
      'T','VZ','SPGI','BLK','NOW','DE','BKNG','SYK','UBER','PGR',
      'ADI','ELV','MDLZ','ETN','VRTX','MMC','AMAT','CB','MU','SO',
      'REGN','C','TJX','DUK','CL','ITW','EOG','ZTS','BSX','CME',
      'FI','BDX','ICE','USB','PLD','AON','MCO','KLAC','SHW','HCA',
    ];
    const quotesArr = await fetchQuotes(sp500Top);
    const stocks = quotesArr
      .filter(q => q?.regularMarketPrice)
      .map((q, i) => ({
        rank: i + 1,
        symbol: q.symbol,
        name: q.longName || q.shortName || q.symbol,
        price: q.regularMarketPrice ?? 0,
        change: q.regularMarketChange ?? 0,
        changePercent: q.regularMarketChangePercent ?? 0,
        volume: q.regularMarketVolume ?? 0,
        marketCap: q.marketCap ?? 0,
        country: 'US',
        exchange: q.fullExchangeName ?? '',
        sector: '',
      }))
      .sort((a, b) => b.marketCap - a.marketCap)
      .map((s, i) => ({ ...s, rank: i + 1 }));

    return NextResponse.json({ stocks, country: 'US' });
  } catch {
    return NextResponse.json({ stocks: [], country: 'US' });
  }
}
