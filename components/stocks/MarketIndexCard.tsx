import { formatChange, getChangeColor } from '@/lib/utils';

interface MarketIndexCardProps {
  symbol: string;
  name: string;
  value: number;
  change: number;
  changePercent: number;
  country: string;
  previousClose?: number;
}

export default function MarketIndexCard({
  name, value, change, changePercent, country,
}: MarketIndexCardProps) {
  const isPositive = changePercent > 0;
  const isNegative = changePercent < 0;

  const countryFlag: Record<string, string> = {
    US: '🇺🇸', KR: '🇰🇷', CN: '🇨🇳', JP: '🇯🇵',
  };

  return (
    <div className={`card relative overflow-hidden transition-all duration-200 hover:border-[#374151]
      ${isPositive ? 'hover:shadow-[0_0_20px_rgba(34,197,94,0.08)]' : ''}
      ${isNegative ? 'hover:shadow-[0_0_20px_rgba(239,68,68,0.08)]' : ''}
    `}>
      {/* 배경 그라데이션 */}
      <div className={`absolute inset-0 opacity-5 pointer-events-none
        ${isPositive ? 'bg-gradient-to-br from-green-500 to-transparent' : ''}
        ${isNegative ? 'bg-gradient-to-br from-red-500 to-transparent' : ''}
      `} />

      <div className="relative">
        <div className="flex items-start justify-between mb-2">
          <div>
            <span className="text-xs text-[#6b7280]">{countryFlag[country] || ''} {country}</span>
            <p className="text-sm font-semibold text-[#e5e7eb] mt-0.5">{name}</p>
          </div>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium
            ${isPositive ? 'bg-green-950 text-green-400 border border-green-900' : ''}
            ${isNegative ? 'bg-red-950 text-red-400 border border-red-900' : ''}
            ${!isPositive && !isNegative ? 'bg-gray-800 text-gray-400' : ''}
          `}>
            {isPositive ? '▲' : isNegative ? '▼' : '—'} {formatChange(changePercent)}
          </span>
        </div>

        <div className="tabular-nums">
          <p className="text-2xl font-bold text-white">
            {value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </p>
          <p className={`text-sm mt-0.5 ${getChangeColor(change)}`}>
            {change > 0 ? '+' : ''}{change.toFixed(2)}
          </p>
        </div>
      </div>
    </div>
  );
}
