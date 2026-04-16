'use client';

import { useEffect, useState } from 'react';
import { formatChange, getChangeColor, formatNumber } from '@/lib/utils';
import type { TrendingStock } from '@/lib/types';

const COUNTRY_FILTERS = [
  { key: 'all', label: '전체' },
  { key: 'US',  label: '🇺🇸 미국' },
  { key: 'KR',  label: '🇰🇷 한국' },
  { key: 'CN',  label: '🇨🇳 중국' },
];

export default function TrendingPage() {
  const [stocks, setStocks] = useState<TrendingStock[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    fetch('/api/stocks/trending')
      .then(r => r.json())
      .then(d => {
        setStocks(d.trending || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const filtered = filter === 'all' ? stocks : stocks.filter(s => s.country === filter);

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-bold text-white">🔥 이슈 종목</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          지금 가장 많이 거래되고 주목받는 종목들
        </p>
      </div>

      {/* 필터 */}
      <div className="flex flex-wrap gap-2">
        {COUNTRY_FILTERS.map(c => (
          <button
            key={c.key}
            onClick={() => setFilter(c.key)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors
              ${filter === c.key
                ? 'bg-[#3b82f6] text-white'
                : 'bg-[#111318] text-[#9ca3af] border border-[#252932] hover:text-white'
              }`}
          >
            {c.label}
          </button>
        ))}
        {loading && <span className="text-xs text-[#6b7280] self-center">불러오는 중...</span>}
      </div>

      {/* 카드 그리드 */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="card animate-pulse h-32" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((stock, i) => {
            const isPos = stock.changePercent > 0;
            const isNeg = stock.changePercent < 0;
            return (
              <div
                key={stock.symbol}
                className={`card hover:border-[#374151] transition-all relative overflow-hidden
                  ${isPos ? 'hover:shadow-[0_0_16px_rgba(34,197,94,0.1)]' : ''}
                  ${isNeg ? 'hover:shadow-[0_0_16px_rgba(239,68,68,0.1)]' : ''}
                `}
              >
                {/* 배경 */}
                <div className={`absolute inset-0 opacity-[0.04] pointer-events-none
                  ${isPos ? 'bg-green-500' : isNeg ? 'bg-red-500' : ''}`} />

                <div className="relative">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <span className="font-bold text-white font-mono">{stock.symbol}</span>
                      <p className="text-xs text-[#6b7280] truncate max-w-[140px] mt-0.5">{stock.name}</p>
                    </div>
                    <span className={`text-xs px-1.5 py-0.5 rounded
                      ${stock.country === 'KR' ? 'bg-[#1a1d24] text-[#9ca3af]' : ''}
                      ${stock.country === 'US' ? 'bg-[#0c1a3d] text-blue-400' : ''}
                      ${stock.country === 'CN' ? 'bg-red-950 text-red-400' : ''}
                    `}>
                      {stock.country === 'US' ? '🇺🇸' : stock.country === 'KR' ? '🇰🇷' : '🇨🇳'}
                    </span>
                  </div>

                  <div className="flex items-end justify-between">
                    <div>
                      <p className="text-xl font-bold text-white tabular-nums">
                        {stock.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </p>
                      <p className={`text-sm font-semibold tabular-nums ${getChangeColor(stock.changePercent)}`}>
                        {isPos ? '▲' : isNeg ? '▼' : '—'} {Math.abs(stock.changePercent).toFixed(2)}%
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs text-[#6b7280]">거래량</p>
                      <p className="text-sm text-[#9ca3af] tabular-nums">{formatNumber(stock.volume, 0)}</p>
                    </div>
                  </div>

                  <div className="mt-2 flex items-center gap-1">
                    <span className="badge badge-gold text-xs">#{i + 1}</span>
                    {stock.reason && (
                      <span className="text-xs text-[#6b7280] truncate">{stock.reason}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div className="card text-center py-12 text-[#6b7280]">
          <p className="text-4xl mb-3">📈</p>
          <p>트렌딩 데이터를 불러오지 못했습니다.</p>
          <p className="text-sm mt-1">Yahoo Finance 연결을 확인하세요.</p>
        </div>
      )}
    </div>
  );
}
