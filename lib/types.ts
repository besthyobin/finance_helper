// ─────────────────────────────────────────────
//  공통 타입 정의
// ─────────────────────────────────────────────

export interface NewsItem {
  id: string;
  title: string;
  summary: string;
  url: string;
  source: string;
  publishedAt: string;
  imageUrl?: string;
  category?: 'us' | 'kr' | 'cn' | 'global';
  tags?: string[];
}

export interface HedgeFund {
  name: string;
  manager: string;
  aum: string;
  topHoldings: HoldingItem[];
  recentChanges: HoldingChange[];
  filingDate?: string;
}

export interface HoldingItem {
  rank: number;
  symbol: string;
  name: string;
  value: number;      // USD (millions)
  shares: number;
  portfolioPercent: number;
  change?: 'new' | 'increased' | 'decreased' | 'unchanged' | 'sold';
  changePercent?: number;
}

export interface HoldingChange {
  symbol: string;
  name: string;
  action: 'new' | 'increased' | 'decreased' | 'sold';
  sharesChange: number;
  percentChange: number;
}

export interface InvestorPortfolio {
  investor: string;
  fund: string;
  cik: string;
  totalValue: number;
  filingDate: string;
  filingPeriod: string;
  holdings: HoldingItem[];
}

export interface IpoListing {
  symbol?: string;
  companyName: string;
  exchange: string;
  country: 'US' | 'KR' | 'CN';
  ipoDate: string;
  priceRange?: string;
  shares?: number;
  estimatedMarketCap?: number;
  sector?: string;
  status: 'upcoming' | 'priced' | 'filed';
}

