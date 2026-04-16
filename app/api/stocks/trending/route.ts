import { NextResponse } from 'next/server';
import { fetchQuotes, fetchTrending } from '@/lib/yahoo-finance';

export async function GET() {
  try {
    // 미국・한국・중국 트렌딩 심볼 수집
    const [usSymbols, krSymbols, cnSymbols] = await Promise.all([
      fetchTrending('US'),
      fetchTrending('KR'),
      fetchTrending('CN'),
    ]);

    let allSymbols = [
      ...usSymbols.slice(0, 15),
      ...krSymbols.slice(0, 15),
      ...cnSymbols.slice(0, 15),
    ];

    if (allSymbols.length === 0) {
      // fallback: 유명 종목
      allSymbols = ['AAPL', 'NVDA', 'MSFT', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD', '005930.KS', '000660.KS', 'BABA', 'PDD'];
    }

    const quotesArr = await fetchQuotes(allSymbols);

    const trending = quotesArr
      .filter(q => q && q.regularMarketPrice)
      .map((q, i) => {
        const isKr = String(q.symbol).endsWith('.KS') || String(q.symbol).endsWith('.KQ');
        const isCn = String(q.symbol).endsWith('.SS') || String(q.symbol).endsWith('.SZ');
        return {
          rank: i + 1,
          symbol: q.symbol,
          name: q.longName || q.shortName || q.symbol,
          price: q.regularMarketPrice ?? 0,
          change: q.regularMarketChange ?? 0,
          changePercent: q.regularMarketChangePercent ?? 0,
          volume: q.regularMarketVolume ?? 0,
          country: isKr ? 'KR' : isCn ? 'CN' : 'US',
        };
      })
      .sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent));

    return NextResponse.json({ trending });
  } catch (err) {
    console.error('Trending stocks error:', err);
    return NextResponse.json({ trending: [], error: 'Failed to fetch trending stocks' }, { status: 200 });
  }
}
