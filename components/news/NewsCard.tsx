import Link from 'next/link';
import { timeAgo } from '@/lib/utils';
import type { NewsItem } from '@/lib/types';

interface NewsCardProps {
  item: NewsItem;
  compact?: boolean;
}

const categoryLabel: Record<string, string> = {
  us: '🇺🇸 미국',
  kr: '🇰🇷 한국',
  cn: '🇨🇳 중국',
  global: '🌍 글로벌',
};

export default function NewsCard({ item, compact = false }: NewsCardProps) {
  if (compact) {
    return (
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-start gap-3 p-3 rounded-lg hover:bg-[#1a1d24] transition-colors group"
      >
        <div className="flex-shrink-0 mt-1 w-1.5 h-1.5 rounded-full bg-[#3b82f6] mt-2" />
        <div className="min-w-0">
          <p className="text-sm text-[#e5e7eb] group-hover:text-white line-clamp-2 leading-snug">
            {item.title}
          </p>
          <p className="text-xs text-[#6b7280] mt-1">
            {item.source} · {timeAgo(item.publishedAt)}
          </p>
        </div>
      </a>
    );
  }

  return (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      className="card block hover:border-[#374151] hover:shadow-[0_0_16px_rgba(59,130,246,0.06)] transition-all group"
    >
      <div className="flex items-center gap-2 mb-2">
        {item.category && (
          <span className="text-xs badge badge-blue">{categoryLabel[item.category] || item.category}</span>
        )}
        <span className="text-xs text-[#6b7280] ml-auto">{timeAgo(item.publishedAt)}</span>
      </div>

      <h3 className="text-sm font-semibold text-[#e5e7eb] group-hover:text-white line-clamp-2 leading-snug mb-2">
        {item.title}
      </h3>

      {item.summary && (
        <p className="text-xs text-[#6b7280] line-clamp-2 leading-relaxed mb-3">
          {item.summary}
        </p>
      )}

      <div className="flex items-center justify-between">
        <span className="text-xs text-[#9ca3af] font-medium">{item.source}</span>
        <span className="text-xs text-[#3b82f6] group-hover:text-blue-300 transition-colors">
          읽기 →
        </span>
      </div>
    </a>
  );
}
