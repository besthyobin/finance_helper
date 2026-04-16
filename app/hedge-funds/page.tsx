'use client';

import { useEffect, useState } from 'react';
import NewsCard from '@/components/news/NewsCard';
import type { NewsItem } from '@/lib/types';

interface FundInfo {
  name: string;
  manager: string;
  cik: string;
  filingDate: string | null;
  accessionNumber: string | null;
}

interface HedgeFundData {
  funds: FundInfo[];
  news: Array<{ title: string; url: string; publishedDate: string; site: string; text?: string }>;
}

export default function HedgeFundsPage() {
  const [data, setData] = useState<HedgeFundData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/hedge-funds')
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const funds = data?.funds || [];
  const news = data?.news || [];

  // FMP 뉴스를 NewsItem 형태로 변환
  const newsItems: NewsItem[] = news.slice(0, 6).map((n, i) => ({
    id: String(i),
    title: n.title,
    summary: n.text?.slice(0, 200) || '',
    url: n.url,
    source: n.site || 'Unknown',
    publishedAt: n.publishedDate || new Date().toISOString(),
    category: 'global',
  }));

  const managerProfiles: Record<string, { emoji: string; bio: string }> = {
    'Bridgewater Associates': { emoji: '🌊', bio: '세계 최대 헤지펀드. 올웨더 포트폴리오와 글로벌 매크로 전략으로 유명' },
    'Citadel':                { emoji: '🏰', bio: '켄 그리핀이 창설한 최고 수익률 헤지펀드. 퀀트 및 마켓메이킹 분야 최강자' },
    'Renaissance Technologies':{ emoji: '🔬', bio: '짐 사이먼스가 설립한 퀀트 펀드. 메달리온 펀드로 유명한 수학적 투자' },
    'Two Sigma':              { emoji: '2️⃣', bio: '빅데이터와 AI 기반의 퀀트 헤지펀드. 기술 주도 투자 전략' },
    'Pershing Square':        { emoji: '🎯', bio: '빌 애크먼의 행동주의 헤지펀드. 대형 공개 포지션과 적극적 주주행동으로 유명' },
    'Tiger Global':           { emoji: '🐯', bio: '체이스 콜먼이 운영하는 글로벌 테크 집중 투자 펀드' },
    'Viking Global':          { emoji: '⚔️', bio: '롱숏 에쿼티 전략의 대표 헤지펀드. 안정적 장기 수익률' },
    'Third Point':            { emoji: '🎭', bio: '댄 로브의 행동주의 펀드. 서한 공개와 경영진 교체 요구로 유명' },
  };

  return (
    <div className="space-y-8 fade-in">
      <div>
        <h1 className="text-2xl font-bold text-white">🏦 주요 헤지펀드</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          세계 주요 헤지펀드의 SEC 13F 보고서 및 최신 동향
        </p>
      </div>

      {/* 헤지펀드 카드 */}
      <section>
        <h2 className="text-base font-semibold text-[#e5e7eb] mb-4">주요 펀드 현황</h2>
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="card animate-pulse h-32" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {funds.map(fund => {
              const profile = managerProfiles[fund.name];
              return (
                <div key={fund.name} className="card hover:border-[#374151] transition-all">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-start gap-3">
                      <span className="text-3xl">{profile?.emoji || '💰'}</span>
                      <div>
                        <h3 className="font-bold text-white text-sm">{fund.name}</h3>
                        <p className="text-xs text-[#9ca3af]">운용 매니저: {fund.manager}</p>
                      </div>
                    </div>
                    {fund.filingDate && (
                      <span className="badge badge-blue text-xs shrink-0">
                        13F 보고
                      </span>
                    )}
                  </div>

                  {profile?.bio && (
                    <p className="text-xs text-[#6b7280] leading-relaxed mb-3">{profile.bio}</p>
                  )}

                  <div className="flex items-center gap-3 text-xs">
                    {fund.filingDate && (
                      <span className="text-[#6b7280]">
                        최근 13F: <span className="text-[#9ca3af]">{fund.filingDate}</span>
                      </span>
                    )}
                    {fund.cik && (
                      <a
                        href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${fund.cik}&type=13F&dateb=&owner=include&count=10`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[#3b82f6] hover:text-blue-300 ml-auto"
                      >
                        SEC EDGAR →
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 헤지펀드 관련 뉴스 */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-[#e5e7eb]">헤지펀드 관련 뉴스</h2>
          {newsItems.length > 0 && (
            <span className="text-xs text-[#6b7280]">{newsItems.length}건</span>
          )}
        </div>

        {newsItems.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {newsItems.map(item => (
              <NewsCard key={item.id} item={item} />
            ))}
          </div>
        ) : !loading ? (
          <div className="card text-center py-10 text-[#6b7280]">
            <p className="mb-1">FMP API 키를 설정하면 헤지펀드 관련 뉴스를 볼 수 있습니다.</p>
            <a
              href="https://financialmodelingprep.com/developer/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-[#3b82f6] underline"
            >
              무료 API 키 발급 →
            </a>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[1, 2, 3].map(i => <div key={i} className="card animate-pulse h-32" />)}
          </div>
        )}
      </section>

      {/* 13F 설명 */}
      <div className="card bg-[#0c1a3d]/50 border-[#1d4ed8]/30">
        <h3 className="font-semibold text-blue-400 mb-2">📋 13F 보고서란?</h3>
        <p className="text-sm text-[#9ca3af] leading-relaxed">
          미국 SEC(증권거래위원회)는 1억 달러 이상을 운용하는 기관투자자에게 분기 종료 후 45일 이내에
          13F 보고서를 제출하도록 의무화하고 있습니다. 이 보고서에는 해당 분기 말 기준으로
          보유한 모든 상장 주식(롱 포지션)이 공개되며, 일반 투자자들은 SEC EDGAR를 통해
          무료로 확인할 수 있습니다.
        </p>
        <p className="text-xs text-[#6b7280] mt-2">
          * 숏 포지션 및 옵션 일부는 공개되지 않을 수 있습니다. 분기별 지연 공시입니다.
        </p>
      </div>
    </div>
  );
}
