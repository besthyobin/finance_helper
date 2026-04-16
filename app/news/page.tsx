'use client';

import { useState, useEffect } from 'react';
import NewsCard from '@/components/news/NewsCard';
import type { NewsItem } from '@/lib/types';

const CATEGORIES = [
  { key: 'all',    label: '전체' },
  { key: 'global', label: '🌍 글로벌' },
  { key: 'us',     label: '🇺🇸 미국' },
  { key: 'kr',     label: '🇰🇷 한국' },
  { key: 'cn',     label: '🇨🇳 중국' },
];

export default function NewsPage() {
  const [category, setCategory] = useState('all');
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    setError('');
    fetch(`/api/news?category=${category}`)
      .then(r => r.json())
      .then(d => {
        setNews(d.news || []);
        setLoading(false);
      })
      .catch(() => {
        setError('뉴스를 불러오는 중 오류가 발생했습니다.');
        setLoading(false);
      });
  }, [category]);

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-bold text-white">글로벌 금융 뉴스</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          미국・한국・중국 주요 뉴스를 실시간으로 확인하세요
        </p>
      </div>

      {/* 카테고리 필터 */}
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map(c => (
          <button
            key={c.key}
            onClick={() => setCategory(c.key)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors
              ${category === c.key
                ? 'bg-[#3b82f6] text-white'
                : 'bg-[#111318] text-[#9ca3af] border border-[#252932] hover:border-[#374151] hover:text-white'
              }`}
          >
            {c.label}
          </button>
        ))}
        {loading && (
          <div className="flex items-center gap-1.5 text-xs text-[#6b7280]">
            <span className="live-indicator" />
            불러오는 중...
          </div>
        )}
      </div>

      {/* 에러 */}
      {error && (
        <div className="card border-red-900 text-red-400 text-sm">{error}</div>
      )}

      {/* 뉴스 그리드 */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="card animate-pulse h-40" />
          ))}
        </div>
      ) : (
        <>
          <p className="text-xs text-[#6b7280]">총 {news.length}건</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {news.map(item => (
              <NewsCard key={item.id} item={item} />
            ))}
          </div>
          {news.length === 0 && !error && (
            <div className="card text-center py-12 text-[#6b7280]">
              <p className="text-4xl mb-3">📭</p>
              <p>해당 카테고리의 뉴스가 없습니다.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
