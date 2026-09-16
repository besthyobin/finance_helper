'use client';

import { useEffect, useState } from 'react';
import PortfolioTable from '@/components/investors/PortfolioTable';
import { formatMarketCap } from '@/lib/utils';

interface InvestorOption {
  key: string;
  name: string;
  fund: string;
}

interface PortfolioData {
  investor: string;
  fund: string;
  cik: string;
  filingDate: string;
  totalValue: number;
  holdingsCount: number;
  holdings: Array<{
    rank: number;
    symbol: string | null;
    name: string;
    value: number;
    shares: number;
    portfolioPercent: number;
    putCall?: string;
    change?: string;
  }>;
  availableInvestors: InvestorOption[];
  error?: string;
}

export default function InvestorsPage() {
  const [selected, setSelected] = useState('buffett');
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/investors?investor=${selected}`)
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [selected]);

  const investors = data?.availableInvestors || [
    { key: 'buffett', name: '워런 버핏',    fund: 'Berkshire Hathaway' },
    { key: 'ackman',  name: '빌 애크먼',    fund: 'Pershing Square' },
    { key: 'paulson', name: '존 폴슨',     fund: 'Paulson & Co.' },
    { key: 'simons',  name: '짐 사이먼스',  fund: 'Renaissance Technologies' },
    { key: 'dalio',   name: '레이 달리오',  fund: 'Bridgewater Associates' },
    { key: 'tepper',  name: '데이비드 테퍼', fund: 'Appaloosa Management' },
  ];

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-bold text-white">💼 투자자 포트폴리오</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          SEC EDGAR 13F 보고서 기반 — 세계적인 투자자들의 최신 보유 종목 공개 데이터
        </p>
      </div>

      {/* 투자자 선택 */}
      <div className="flex flex-wrap gap-2">
        {investors.map(inv => (
          <button
            key={inv.key}
            onClick={() => setSelected(inv.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors border
              ${selected === inv.key
                ? 'bg-[#3b82f6] text-white border-[#3b82f6]'
                : 'bg-[#111318] text-[#9ca3af] border-[#252932] hover:text-white hover:border-[#374151]'
              }`}
          >
            <span className="font-semibold">{inv.name}</span>
            <span className="text-xs block opacity-70">{inv.fund}</span>
          </button>
        ))}
      </div>

      {/* 에러 */}
      {data?.error && (
        <div className="card border-red-900 bg-red-950/20">
          <p className="text-red-400 text-sm">{data.error}</p>
        </div>
      )}

      {/* 요약 카드 */}
      {!loading && data && !data.error && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card">
            <p className="text-xs text-[#6b7280] mb-1">투자자</p>
            <p className="font-bold text-white">{data.investor}</p>
            <p className="text-xs text-[#9ca3af]">{data.fund}</p>
          </div>
          <div className="card">
            <p className="text-xs text-[#6b7280] mb-1">총 운용 자산</p>
            <p className="font-bold text-white tabular-nums">
              {formatMarketCap(data.totalValue * 1000)}
            </p>
          </div>
          <div className="card">
            <p className="text-xs text-[#6b7280] mb-1">보유 종목 수</p>
            <p className="font-bold text-white tabular-nums">{data.holdingsCount}개</p>
          </div>
          <div className="card">
            <p className="text-xs text-[#6b7280] mb-1">보고서 제출일</p>
            <p className="font-bold text-white">{data.filingDate || '—'}</p>
            <p className="text-xs text-[#6b7280]">SEC 13F-HR</p>
          </div>
        </div>
      )}

      {/* 포트폴리오 테이블 */}
      {loading ? (
        <div className="card animate-pulse h-96" />
      ) : data && !data.error ? (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#e5e7eb]">
              {data.investor} — 상위 50개 보유 종목
            </h2>
            <a
              href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${data.cik}&type=13F&dateb=&owner=include&count=10`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-[#3b82f6] hover:text-blue-300"
            >
              SEC EDGAR 원본 보기 →
            </a>
          </div>
          <PortfolioTable holdings={data.holdings} totalValue={data.totalValue} />
          <p className="text-xs text-[#6b7280]">
            * SEC EDGAR 13F-HR 공시 데이터 기반. 분기별 업데이트됩니다.
          </p>
        </>
      ) : null}
    </div>
  );
}
