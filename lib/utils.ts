import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { formatDistanceToNow } from 'date-fns';
import { ko } from 'date-fns/locale';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 숫자를 K/M/B/T 단위로 포맷 */
export function formatNumber(num: number, decimals = 2): string {
  if (!num && num !== 0) return '—';
  if (num >= 1e12) return (num / 1e12).toFixed(decimals) + 'T';
  if (num >= 1e9)  return (num / 1e9).toFixed(decimals)  + 'B';
  if (num >= 1e6)  return (num / 1e6).toFixed(decimals)  + 'M';
  if (num >= 1e3)  return (num / 1e3).toFixed(decimals)  + 'K';
  return num.toFixed(decimals);
}

/** 시가총액을 달러 + 단위로 포맷 */
export function formatMarketCap(value: number): string {
  if (!value) return '—';
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9)  return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6)  return `$${(value / 1e6).toFixed(2)}M`;
  return `$${value.toLocaleString()}`;
}

/** 가격 포맷 (달러/원) */
export function formatPrice(price: number, currency: 'USD' | 'KRW' | 'CNY' = 'USD'): string {
  if (!price && price !== 0) return '—';
  if (currency === 'KRW') return price.toLocaleString('ko-KR') + '원';
  if (currency === 'CNY') return '¥' + price.toFixed(2);
  return '$' + price.toFixed(2);
}

/** % 변화율 포맷 (+ 부호 포함) */
export function formatChange(value: number): string {
  if (!value && value !== 0) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

/** 변화율에 따른 색상 클래스 반환 */
export function getChangeColor(value: number): string {
  if (value > 0)  return 'text-green-400';
  if (value < 0)  return 'text-red-400';
  return 'text-gray-400';
}

/** 변화율에 따른 배경 색상 바지 클래스 */
export function getChangeBadge(value: number): string {
  if (value > 0)  return 'badge-green';
  if (value < 0)  return 'badge-red';
  return 'bg-gray-800 text-gray-400';
}

/** 날짜를 상대 시간으로 (예: "3분 전") */
export function timeAgo(dateStr: string): string {
  try {
    return formatDistanceToNow(new Date(dateStr), { addSuffix: true, locale: ko });
  } catch {
    return dateStr;
  }
}

/** API URL 빌더 */
export function buildFmpUrl(path: string, params: Record<string, string> = {}): string {
  const key = process.env.FMP_API_KEY || '';
  const base = 'https://financialmodelingprep.com/api/v3';
  const query = new URLSearchParams({ ...params, apikey: key }).toString();
  return `${base}${path}?${query}`;
}

/** 국가 코드 → 이름 */
export const COUNTRY_LABELS: Record<string, string> = {
  US: '🇺🇸 미국',
  KR: '🇰🇷 한국',
  CN: '🇨🇳 중국',
};

/** 주요 시장 지수 심볼 */
export const INDEX_SYMBOLS = {
  SP500:  '^GSPC',
  NASDAQ: '^IXIC',
  DOW:    '^DJI',
  KOSPI:  '^KS11',
  KOSDAQ: '^KQ11',
  SSE:    '000001.SS',   // 상하이 종합지수
  SZSE:   '399001.SZ',   // 선전 성분지수
  HSCEI:  '^HSI',        // 홍콩 항셍
};

/** Berkshire Hathaway CIK (워런 버핏) */
export const BERKSHIRE_CIK = '0001067983';
