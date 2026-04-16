import { formatMarketCap } from '@/lib/utils';

interface HoldingItem {
  rank: number;
  symbol: string | null;
  name: string;
  value: number;
  shares: number;
  portfolioPercent: number;
  putCall?: string;
  change?: string;
}

interface PortfolioTableProps {
  holdings: HoldingItem[];
  totalValue: number;
}

const changeColors: Record<string, string> = {
  new:       'badge-green',
  increased: 'badge-blue',
  decreased: 'badge-red',
  sold:      'bg-gray-800 text-gray-400 border border-gray-700',
};
const changeLabels: Record<string, string> = {
  new: '신규', increased: '증가', decreased: '감소', sold: '매도',
};

export default function PortfolioTable({ holdings, totalValue }: PortfolioTableProps) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[#252932]">
      <table className="data-table">
        <thead>
          <tr>
            <th className="w-10 text-center">#</th>
            <th>종목</th>
            <th>평가액</th>
            <th>비중</th>
            <th className="hidden md:table-cell">주식 수</th>
            <th className="hidden lg:table-cell">변동</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map(h => (
            <tr key={h.rank}>
              <td className="text-center text-[#6b7280] font-mono text-sm">{h.rank}</td>
              <td>
                <div>
                  {h.symbol ? (
                    <span className="font-semibold text-white font-mono text-sm">{h.symbol}</span>
                  ) : (
                    <span className="font-semibold text-[#9ca3af] text-sm">—</span>
                  )}
                  <p className="text-xs text-[#6b7280] truncate max-w-[200px]">{h.name}</p>
                </div>
              </td>
              <td className="tabular-nums font-mono text-sm">
                ${(h.value * 1000).toLocaleString()}
                <p className="text-xs text-[#6b7280]">{formatMarketCap(h.value * 1000)}</p>
              </td>
              <td>
                <div className="flex items-center gap-2">
                  <div className="w-16 bg-[#1a1d24] rounded-full h-1.5">
                    <div
                      className="bg-[#3b82f6] h-1.5 rounded-full"
                      style={{ width: `${Math.min(h.portfolioPercent, 100)}%` }}
                    />
                  </div>
                  <span className="text-sm tabular-nums text-[#9ca3af]">
                    {h.portfolioPercent.toFixed(1)}%
                  </span>
                </div>
              </td>
              <td className="hidden md:table-cell text-[#9ca3af] text-sm tabular-nums">
                {h.shares.toLocaleString()}
              </td>
              <td className="hidden lg:table-cell">
                {h.change ? (
                  <span className={`badge text-xs ${changeColors[h.change] || ''}`}>
                    {changeLabels[h.change] || h.change}
                  </span>
                ) : (
                  <span className="text-[#374151]">—</span>
                )}
                {h.putCall && (
                  <span className="badge badge-gold text-xs ml-1">{h.putCall}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {holdings.length === 0 && (
        <div className="text-center py-12 text-[#6b7280]">
          포트폴리오 데이터를 불러오는 중입니다...
        </div>
      )}
    </div>
  );
}
