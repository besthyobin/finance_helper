import MarketIndexCard from '@/components/stocks/MarketIndexCard';
import NewsCard from '@/components/news/NewsCard';
import Link from 'next/link';
import type { MarketIndex, NewsItem } from '@/lib/types';

function getBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_BASE_URL) return process.env.NEXT_PUBLIC_BASE_URL;
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return 'http://localhost:3000';
}

async function getIndices(): Promise<MarketIndex[]> {
  try {
    const res = await fetch(`${getBaseUrl()}/api/stocks/indices`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.indices || [];
  } catch {
    return [];
  }
}

async function getNews(): Promise<NewsItem[]> {
  try {
    const res = await fetch(`${getBaseUrl()}/api/news?category=all`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.news || []).slice(0, 8);
  } catch {
    return [];
  }
}

export default async function DashboardPage() {
  const [indices, recentNews] = await Promise.all([getIndices(), getNews()]);

  const usIndices = indices.filter(i => i.country === 'US');
  const krIndices = indices.filter(i => i.country === 'KR');
  const cnIndices = indices.filter(i => i.country === 'CN');

  const quickLinks = [
    { href: '/news',        icon: '📰', label: '글로벌 뉴스', desc: '전세계 금융 뉴스' },
    { href: '/trending',    icon: '🔥', label: '이슈 종목',   desc: '지금 뜨는 주식' },
    { href: '/market/us',   icon: '🇺🇸', label: '미국 Top 100', desc: 'NASDAQ/NYSE' },
    { href: '/market/kr',   icon: '🇰🇷', label: '한국 Top 100', desc: 'KOSPI/KOSDAQ' },
    { href: '/market/cn',   icon: '🇨🇳', label: '중국 Top 100', desc: 'SSE/SZSE' },
    { href: '/hedge-funds', icon: '🏦', label: '헤지펀드',     desc: '13F 보유 현황' },
    { href: '/ipo',         icon: '🚀', label: 'IPO 캘린더',  desc: '상장 예정 종목' },
    { href: '/investors',   icon: '💼', label: '투자자 포트폴리오', desc: '버핏・달리오 등' },
  ];

  return (
    <div className="space-y-8 fade-in">
      {/* 헤더 */}
      <div>
        <h1 className="text-2xl font-bold text-white">마켓 대시보드</h1>
        <p className="text-[#9ca3af] text-sm mt-1">
          전세계 주식 시장, 뉴스, 투자자 동향을 한눈에 확인하세요
        </p>
      </div>

      {/* 미국 지수 */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-[#e5e7eb]">🇺🇸 미국 시장</h2>
          <Link href="/market/us" className="text-xs text-[#3b82f6] hover:text-blue-300">
            Top 100 보기 →
          </Link>
        </div>
        {usIndices.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {usIndices.map(idx => (
              <MarketIndexCard key={idx.symbol} {...idx} />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {['S&P 500', 'NASDAQ', 'Dow Jones'].map(n => (
              <div key={n} className="card animate-pulse">
                <div className="h-4 bg-[#1a1d24] rounded w-3/4 mb-2" />
                <div className="h-8 bg-[#1a1d24] rounded w-1/2" />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 한국 + 중국 지수 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-[#e5e7eb]">🇰🇷 한국 시장</h2>
            <Link href="/market/kr" className="text-xs text-[#3b82f6] hover:text-blue-300">
              Top 100 보기 →
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {krIndices.length > 0
              ? krIndices.map(idx => <MarketIndexCard key={idx.symbol} {...idx} />)
              : [1, 2].map(i => <div key={i} className="card animate-pulse h-24" />)
            }
          </div>
        </section>

        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-[#e5e7eb]">🇨🇳 중국 시장</h2>
            <Link href="/market/cn" className="text-xs text-[#3b82f6] hover:text-blue-300">
              Top 100 보기 →
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {cnIndices.length > 0
              ? cnIndices.map(idx => <MarketIndexCard key={idx.symbol} {...idx} />)
              : [1, 2].map(i => <div key={i} className="card animate-pulse h-24" />)
            }
          </div>
        </section>
      </div>

      {/* 빠른 메뉴 */}
      <section>
        <h2 className="text-base font-semibold text-[#e5e7eb] mb-3">빠른 이동</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {quickLinks.map(link => (
            <Link
              key={link.href}
              href={link.href}
              className="card hover:border-[#374151] hover:bg-[#1a1d24] transition-all group"
            >
              <div className="text-2xl mb-2">{link.icon}</div>
              <p className="text-sm font-semibold text-white group-hover:text-blue-400 transition-colors">
                {link.label}
              </p>
              <p className="text-xs text-[#6b7280] mt-0.5">{link.desc}</p>
            </Link>
          ))}
        </div>
      </section>

      {/* 최신 뉴스 */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-[#e5e7eb]">최신 뉴스</h2>
          <Link href="/news" className="text-xs text-[#3b82f6] hover:text-blue-300">
            전체 보기 →
          </Link>
        </div>
        {recentNews.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {recentNews.map(item => (
              <NewsCard key={item.id} item={item} />
            ))}
          </div>
        ) : (
          <div className="card text-center py-8 text-[#6b7280]">
            뉴스를 불러오는 중입니다...
          </div>
        )}
      </section>
    </div>
  );
}
