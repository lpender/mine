import type { BacktestSummary } from '../types';

interface Props {
  summary: BacktestSummary;
}

export function SummaryStats({ summary }: Props) {
  const stats = [
    { label: 'Trades', value: summary.total_trades },
    {
      label: 'Win Rate',
      value: `${(summary.win_rate * 100).toFixed(1)}%`,
      color: summary.win_rate >= 0.5 ? 'text-green-400' : 'text-red-400',
    },
    {
      label: 'Avg Return',
      value: `${summary.avg_return.toFixed(2)}%`,
      color: summary.avg_return >= 0 ? 'text-green-400' : 'text-red-400',
    },
    {
      label: 'Profit Factor',
      value: summary.profit_factor === Infinity ? '∞' : summary.profit_factor.toFixed(2),
      color: summary.profit_factor >= 1 ? 'text-green-400' : 'text-red-400',
    },
    {
      label: 'Best Trade',
      value: `${summary.best_trade.toFixed(2)}%`,
      color: 'text-green-400',
    },
    {
      label: 'Worst Trade',
      value: `${summary.worst_trade.toFixed(2)}%`,
      color: 'text-red-400',
    },
    { label: 'No Entry', value: summary.no_entry_count },
    { label: 'No Data', value: summary.no_data_count },
  ];

  return (
    <div className="grid grid-cols-8 gap-3">
      {stats.map((stat) => (
        <div key={stat.label} className="stat-card">
          <div className={`stat-value ${stat.color || ''}`}>{stat.value}</div>
          <div className="stat-label">{stat.label}</div>
        </div>
      ))}
    </div>
  );
}
