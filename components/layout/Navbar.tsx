'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

const navItems = [
  { href: '/',             label: '대시보드',        icon: '📊' },
  { href: '/news',         label: '글로벌 뉴스',     icon: '📰' },
  { href: '/hedge-funds',  label: '헤지펀드',        icon: '🏦' },
  { href: '/ipo',          label: 'IPO 캘린더',      icon: '🚀' },
  { href: '/investors',    label: '투자자 포트폴리오', icon: '💼' },
];

export default function Navbar() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 bg-[#0a0b0d]/95 backdrop-blur border-b border-[#252932]">
      <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14">
          {/* 로고 */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <span className="text-xl font-bold text-white tracking-tight">
              Finance<span className="text-[#3b82f6]">Helper</span>
            </span>
            <span className="live-indicator ml-1" title="실시간 데이터" />
          </Link>

          {/* 데스크탑 네비게이션 */}
          <nav className="hidden lg:flex items-center gap-1">
            {navItems.map(item => {
              const active = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors
                    ${active
                      ? 'bg-[#1a1d24] text-white'
                      : 'text-[#9ca3af] hover:text-white hover:bg-[#1a1d24]'
                    }`}
                >
                  <span>{item.icon}</span>
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>

          {/* 모바일 메뉴 버튼 */}
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="lg:hidden p-2 text-[#9ca3af] hover:text-white"
            aria-label="메뉴 열기"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              {menuOpen
                ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              }
            </svg>
          </button>
        </div>
      </div>

      {/* 모바일 드롭다운 */}
      {menuOpen && (
        <div className="lg:hidden bg-[#111318] border-t border-[#252932] px-4 py-3 grid grid-cols-2 gap-1">
          {navItems.map(item => {
            const active = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors
                  ${active ? 'bg-[#1a1d24] text-white' : 'text-[#9ca3af] hover:text-white hover:bg-[#1a1d24]'}`}
              >
                <span>{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      )}
    </header>
  );
}
