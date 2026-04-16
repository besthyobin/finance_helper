'use client';

import { useEffect, useState } from 'react';
import StockTable from '@/components/stocks/StockTable';
import MarketIndexCard from '@/components/stocks/MarketIndexCard';
import type { StockQuote, MarketIndex } from '@/lib/types';

interface MarketPageClientProps {
  country: 'US' | 'KR' | 'CN';
}

const MARKET_META = {
  US: {
    flag: '🇺🇸',
    title: '미국 주식 시장',
    desc: 'NASDAQ · NYSE 시총 상위 100개 종목',
    exchanges: ['NASDAQ', 'NYSE'],
    currency: 'USD' as const,
    indices: ['^GSPC', '^IXIC', '^DJI'],
  },
  KR: {
    flag: '🇰🇷',
    title: '한국 주식 시장',
    desc: 'KOSPI · KOSDAQ 시총 상위 100개 종목',
    exchanges: ['KOSPI', 'KOSDAQ'],
    currency: 'KRW' as const,
    indices: ['^KS11', '^KQ11'],
  },
  CN: {
    flag: '🇨🇳',
    title: '중국 주식 시장',
    desc: '상하이 · 선전 시총 상위 100개 종목',
    exchanges: ['SSE', 'SZSE'],
    currency: 'CNY' as const,
    indices: ['000001.SS', '399001.SZ', '^HSI'],
  },
};

export default function MarketPageClient({ country }: MarketPageClientProps) {
  const [stocks, setStocks] = useState<StockQuote[]>([]);
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const meta = MARKET_META[country];

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch(`/api/stocks/top100/${country.toLowerCase()}`).then(r => r.json()),
      fetch('/api/stocks/indices').then(r => r.json()),
    ])
      .then(([stockData, indexData]) => {
        setStocks(stockData.stocks || []);
        const allIndices: MarketIndex[] = indexData.indices || [];
        setIndices(allIndices.filter(i => i.country === country));
        if (stockData.error) setError(stockData.error);
        setLoading(false);
      })
      .catch(() => {
        setError('데이터를 불러오지 못했습니다.');
        setLoading(false);
      });
  }, [country]);

  return (
    <div className="space-y-6 fade-in">
      {/* 헤더 */}
      <div>
        <h1 className="text-2xl font-bold text-white">
          {meta.flag} {meta.title}
        </h1>
        <p className="text-[#9ca3af] text-sm mt-1">{meta.desc}</p>
      </div>

      {/* 지수 카드 */}
      {indices.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {indices.map(idx => (
            <MarketIndexCard key={idx.symbol} {...idx} />
          ))}
        </div>
      )}

      {/* 거래소 뱃지 */}
      <div className="flex items-center gap-2">
        {meta.exchanges.map(ex => (
          <span key={ex} className="badge badge-blue">{ex}</span>
        ))}
        {!loading && (
          <span className="text-xs text-[#6b7280] ml-auto">
            {stocks.length}개 종목
          </span>
        )}
      </div>

      {/* API Key 안내 */}
      {error && (
        <div className="card border-yellow-900 bg-yellow-950/20">
          <p className="text-yellow-400 text-sm font-medium mb-1">⚠️ FMP API 키 필요</p>
          <p className="text-[#9ca3af] text-xs">
            {country !== 'US'
              ? `${meta.title} 데이터는 Financial Modeling Prep API 키가 필요합니다. `
              : ''}
            <a href="https://financialmodelingprep.com/developer/docs" target="_blank" rel="noopener noreferrer"
              className="text-yellow-400 underline">
              무료 키 발급하기
            </a>
            {' '}후 .env.local에 FMP_API_KEY를 설정하세요.
            {country === 'US' && stocks.length > 0 && ' (현재 Yahoo Finance fallback 사용 중)'}
          </p>
        </div>
      )}

      {/* 테이블 */}
      {loading ? (
        <div className="card animate-pulse h-96" />
      ) : (
        <StockTable stocks={stocks} currency={meta.currency} />
      )}
    </div>
  );
}
