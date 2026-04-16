'use client';

import { useEffect, useState } from 'react';
import type { IpoListing } from '@/lib/types';

const STATUS_LABELS: Record<string, string> = {
  upcoming: '예정',
  priced:   '가격 확정',
  filed:    '신청',
};
const STATUS_BADGE: Record<string, string> = {
  upcoming: 'badge-blue',
  priced:   'badge-green',
  filed:    'badge-gold',
};
const COUNTRY_FILTERS = [
  { key: 'all', label: '전체' },
  { key: 'US',  label: '🇺🇸 미국' },
  { key: 'KR',  label: '🇰🇷 한국' },
  { key: 'CN',  label: '🇨🇳 중국' },
];

export default function IpoPage() {
  const [ipos, setIpos] = useState<IpoListing[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/ipo')
      .then(r => r.json())
      .then(d => {
        setIpos(d.ipos || []);
        if (d.error) setError(d.error);
        setLoading(false);
      })
      .catch(() => {
        setError('IPO 데이터를 불러오지 못했습니다.');
        setLoading(false);
      });
  }, []);

  const filtered = filter === 'all' ? ipos : ipos.filter(i => i.country === filter);

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-bold text-white">🚀 IPO 캘린더</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          미국・한국・중국 상장 예정 및 최근 IPO 종목
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
      </div>

      {/* API Key 안내 */}
      {error && (
        <div className="card border-yellow-900 bg-yellow-950/20">
          <p className="text-yellow-400 text-sm font-medium mb-1">⚠️ FMP API 키 필요</p>
          <p className="text-[#9ca3af] text-xs">
            IPO 캘린더 데이터는 Financial Modeling Prep API 키가 필요합니다.{' '}
            <a href="https://financialmodelingprep.com/developer/docs" target="_blank" rel="noopener noreferrer"
              className="text-yellow-400 underline">
              무료 키 발급
            </a>
            {' '}후 .env.local의 FMP_API_KEY를 설정하세요.
          </p>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card animate-pulse h-32" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="card text-center py-12 text-[#6b7280]">
          <p className="text-4xl mb-3">🚀</p>
          <p>현재 표시할 IPO 데이터가 없습니다.</p>
          <p className="text-sm mt-1">FMP API 키를 설정하면 실제 데이터를 볼 수 있습니다.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[#252932]">
          <table className="data-table">
            <thead>
              <tr>
                <th>회사명</th>
                <th>심볼</th>
                <th className="hidden sm:table-cell">거래소</th>
                <th>국가</th>
                <th>상장일</th>
                <th className="hidden md:table-cell">공모가 범위</th>
                <th>상태</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((ipo, i) => (
                <tr key={i}>
                  <td>
                    <div>
                      <p className="font-semibold text-white text-sm">{ipo.companyName}</p>
                      {ipo.sector && <p className="text-xs text-[#6b7280]">{ipo.sector}</p>}
                    </div>
                  </td>
                  <td className="font-mono text-sm text-[#9ca3af]">{ipo.symbol || '—'}</td>
                  <td className="hidden sm:table-cell text-sm text-[#9ca3af]">{ipo.exchange}</td>
                  <td>
                    {ipo.country === 'US' ? '🇺🇸' : ipo.country === 'KR' ? '🇰🇷' : '🇨🇳'}
                  </td>
                  <td className="text-sm tabular-nums">{ipo.ipoDate}</td>
                  <td className="hidden md:table-cell text-sm text-[#9ca3af]">
                    {ipo.priceRange || '—'}
                  </td>
                  <td>
                    <span className={`badge text-xs ${STATUS_BADGE[ipo.status] || ''}`}>
                      {STATUS_LABELS[ipo.status] || ipo.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
