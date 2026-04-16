'use client';

import { useState } from 'react';
import { formatMarketCap, formatChange, getChangeColor, formatNumber } from '@/lib/utils';
import type { StockQuote } from '@/lib/types';

interface StockTableProps {
  stocks: StockQuote[];
  showRank?: boolean;
  currency?: 'USD' | 'KRW' | 'CNY';
}

type SortKey = 'rank' | 'price' | 'changePercent' | 'volume' | 'marketCap';

export default function StockTable({ stocks, showRank = true, currency = 'USD' }: StockTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'rank' ? 'asc' : 'desc');
    }
  };

  const sorted = [...stocks].sort((a, b) => {
    const valA = a[sortKey as keyof StockQuote] as number;
    const valB = b[sortKey as keyof StockQuote] as number;
    return sortDir === 'asc' ? valA - valB : valB - valA;
  });

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (sortKey !== col) return <span className="text-[#374151] ml-1">↕</span>;
    return <span className="text-[#3b82f6] ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  const priceSymbol = currency === 'KRW' ? '' : currency === 'CNY' ? '¥' : '$';

  return (
    <div className="overflow-x-auto rounded-xl border border-[#252932]">
      <table className="data-table">
        <thead>
          <tr>
            {showRank && (
              <th className="cursor-pointer select-none w-12 text-center" onClick={() => handleSort('rank')}>
                # <SortIcon col="rank" />
              </th>
            )}
            <th>종목</th>
            <th className="cursor-pointer select-none" onClick={() => handleSort('price')}>
              현재가 <SortIcon col="price" />
            </th>
            <th className="cursor-pointer select-none" onClick={() => handleSort('changePercent')}>
              등락률 <SortIcon col="changePercent" />
            </th>
            <th className="cursor-pointer select-none hidden md:table-cell" onClick={() => handleSort('volume')}>
              거래량 <SortIcon col="volume" />
            </th>
            <th className="cursor-pointer select-none hidden lg:table-cell" onClick={() => handleSort('marketCap')}>
              시가총액 <SortIcon col="marketCap" />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(stock => (
            <tr key={stock.symbol} className="group">
              {showRank && (
                <td className="text-center text-[#6b7280] font-mono text-sm">{stock.rank}</td>
              )}
              <td>
                <div>
                  <span className="font-semibold text-white font-mono text-sm">{stock.symbol}</span>
                  <p className="text-xs text-[#6b7280] truncate max-w-[160px]">{stock.name}</p>
                </div>
              </td>
              <td className="tabular-nums font-mono">
                {priceSymbol}{stock.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                {currency === 'KRW' && '원'}
              </td>
              <td>
                <span className={`inline-flex items-center gap-0.5 text-sm font-semibold tabular-nums ${getChangeColor(stock.changePercent)}`}>
                  {stock.changePercent > 0 ? '▲' : stock.changePercent < 0 ? '▼' : '—'}{' '}
                  {Math.abs(stock.changePercent).toFixed(2)}%
                </span>
              </td>
              <td className="hidden md:table-cell text-[#9ca3af] tabular-nums text-sm">
                {formatNumber(stock.volume, 0)}
              </td>
              <td className="hidden lg:table-cell text-[#9ca3af] text-sm">
                {formatMarketCap(stock.marketCap)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {sorted.length === 0 && (
        <div className="text-center py-12 text-[#6b7280]">
          데이터를 불러오는 중이거나 API 키가 필요합니다.
        </div>
      )}
    </div>
  );
}
