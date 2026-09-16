import NewsCard from '@/components/news/NewsCard';
import Link from 'next/link';
import type { NewsItem } from '@/lib/types';

function getBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_BASE_URL) return process.env.NEXT_PUBLIC_BASE_URL;
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return 'http://localhost:3000';
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
  const recentNews = await getNews();

  const quickLinks = [
    { href: '/news',        icon: '📰', label: '글로벌 뉴스', desc: '전세계 금융 뉴스' },
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
          전세계 금융 뉴스와 투자자 동향을 한눈에 확인하세요
        </p>
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
