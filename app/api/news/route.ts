import { NextResponse } from 'next/server';
import type { NewsItem } from '@/lib/types';

// RSS 피드 파싱 (서버사이드)
async function parseRssFeed(url: string, source: string, category: NewsItem['category']): Promise<NewsItem[]> {
  try {
    const res = await fetch(url, {
      next: { revalidate: 300 }, // 5분 캐시
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; FinanceHelper/1.0)' },
    });
    if (!res.ok) return [];
    const xml = await res.text();

    const items: NewsItem[] = [];
    const itemRegex = /<item>([\s\S]*?)<\/item>/g;
    let match;

    while ((match = itemRegex.exec(xml)) !== null) {
      const block = match[1];
      const title = decode(extract(block, 'title'));
      const link  = extract(block, 'link') || extract(block, 'guid');
      const desc  = decode(strip(extract(block, 'description')));
      const pub   = extract(block, 'pubDate');

      if (!title || !link) continue;
      items.push({
        id: Buffer.from(link).toString('base64').slice(0, 20),
        title,
        summary: desc.slice(0, 200),
        url: link,
        source,
        publishedAt: pub ? new Date(pub).toISOString() : new Date().toISOString(),
        category,
      });
      if (items.length >= 15) break;
    }
    return items;
  } catch {
    return [];
  }
}

function extract(xml: string, tag: string): string {
  const m = xml.match(new RegExp(`<${tag}[^>]*>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?<\\/${tag}>`, 'i'));
  return m ? m[1].trim() : '';
}
function strip(html: string): string {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}
function decode(str: string): string {
  return str.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const category = searchParams.get('category') || 'global';

  const feeds: Array<{ url: string; source: string; category: NewsItem['category'] }> = [
    // 글로벌
    { url: 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114', source: 'CNBC Markets', category: 'global' },
    // 미국
    { url: 'https://finance.yahoo.com/news/rssindex', source: 'Yahoo Finance', category: 'us' },
    { url: 'https://feeds.content.dowjones.io/public/rss/mw_marketpulse', source: 'MarketWatch', category: 'us' },
    // 한국
    { url: 'https://www.hankyung.com/feed/finance', source: '한국경제', category: 'kr' },
    { url: 'https://www.yna.co.kr/rss/economy.xml', source: '연합뉴스', category: 'kr' },
    // 중국
    { url: 'https://www.scmp.com/rss/92/feed', source: 'SCMP Business', category: 'cn' },
  ];

  const selectedFeeds = category === 'all'
    ? feeds
    : feeds.filter(f => f.category === category || f.category === 'global');

  const results = await Promise.allSettled(
    selectedFeeds.map(f => parseRssFeed(f.url, f.source, f.category))
  );

  const allNews: NewsItem[] = results
    .flatMap(r => r.status === 'fulfilled' ? r.value : [])
    .sort((a, b) => new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime())
    .slice(0, 60);

  return NextResponse.json({ news: allNews, count: allNews.length });
}
