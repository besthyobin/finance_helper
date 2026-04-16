/**
 * Yahoo Finance API 직접 호출 헬퍼
 * yahoo-finance2 npm 패키지 대신 REST API를 직접 사용하여 TypeScript 호환성 문제 해결
 */

const YF_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
  'Accept': 'application/json',
};

export interface YfQuote {
  symbol: string;
  shortName?: string;
  longName?: string;
  regularMarketPrice?: number;
  regularMarketChange?: number;
  regularMarketChangePercent?: number;
  regularMarketVolume?: number;
  marketCap?: number;
  regularMarketPreviousClose?: number;
  fullExchangeName?: string;
}

/** 여러 심볼의 시세를 한 번에 조회 */
export async function fetchQuotes(symbols: string[]): Promise<YfQuote[]> {
  if (symbols.length === 0) return [];
  const chunks = chunkArray(symbols, 20); // 한 번에 최대 20개
  const results: YfQuote[] = [];

  for (const chunk of chunks) {
    try {
      const url = `https://query1.finance.yahoo.com/v8/finance/quote?symbols=${chunk.join(',')}&fields=regularMarketPrice,regularMarketChange,regularMarketChangePercent,regularMarketVolume,marketCap,regularMarketPreviousClose,shortName,longName,fullExchangeName`;
      const res = await fetch(url, { headers: YF_HEADERS, next: { revalidate: 60 } });
      if (!res.ok) continue;
      const data = await res.json() as { quoteResponse?: { result?: YfQuote[] } };
      const quotes = data?.quoteResponse?.result || [];
      results.push(...quotes);
    } catch {
      continue;
    }
  }
  return results;
}

/** 트렌딩 심볼 조회 */
export async function fetchTrending(region: string): Promise<string[]> {
  try {
    const url = `https://query1.finance.yahoo.com/v1/finance/trending/${region}?count=25`;
    const res = await fetch(url, { headers: YF_HEADERS, next: { revalidate: 300 } });
    if (!res.ok) return [];
    const data = await res.json() as { finance?: { result?: Array<{ quotes?: Array<{ symbol: string }> }> } };
    const quotes = data?.finance?.result?.[0]?.quotes || [];
    return quotes.map((q) => q.symbol);
  } catch {
    return [];
  }
}

function chunkArray<T>(arr: T[], size: number): T[][] {
  return Array.from({ length: Math.ceil(arr.length / size) }, (_, i) =>
    arr.slice(i * size, i * size + size)
  );
}
