import { useState } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table';
import type { SortingState } from '@tanstack/react-table';
import type { BacktestResult } from '../types';

const columnHelper = createColumnHelper<BacktestResult>();

function formatNumber(val: number | null, decimals = 2): string {
  if (val === null || val === undefined) return '-';
  return val.toFixed(decimals);
}

function formatMillions(val: number | null): string {
  if (val === null || val === undefined) return '-';
  const m = val / 1_000_000;
  if (m >= 1000) return `${(m / 1000).toFixed(1)}B`;
  if (m >= 1) return `${m.toFixed(1)}M`;
  return `${(val / 1000).toFixed(0)}K`;
}

function formatTime(timestamp: string): string {
  const d = new Date(timestamp);
  return d.toLocaleString('en-US', {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

const columns = [
  columnHelper.accessor('ticker', {
    header: 'Ticker',
    cell: (info) => <span className="font-medium">{info.getValue()}</span>,
  }),
  columnHelper.accessor('channel', {
    header: 'Channel',
    cell: (info) => info.getValue() || '-',
  }),
  columnHelper.accessor('headline', {
    header: 'Headline',
    cell: (info) => {
      const val = info.getValue();
      if (!val) return '-';
      return <span title={val}>{val.slice(0, 40)}{val.length > 40 ? '...' : ''}</span>;
    },
  }),
  columnHelper.accessor('timestamp', {
    header: 'Time (EST)',
    cell: (info) => formatTime(info.getValue()),
  }),
  columnHelper.accessor('market_session', {
    header: 'Session',
    cell: (info) => {
      const val = info.getValue();
      const colors: Record<string, string> = {
        premarket: 'text-yellow-400',
        market: 'text-green-400',
        postmarket: 'text-blue-400',
        closed: 'text-slate-500',
      };
      return <span className={colors[val] || ''}>{val}</span>;
    },
  }),
  columnHelper.accessor('price_threshold', {
    header: 'Price',
    cell: (info) => `$${formatNumber(info.getValue())}`,
  }),
  columnHelper.accessor('float_shares', {
    header: 'Float',
    cell: (info) => formatMillions(info.getValue()),
  }),
  columnHelper.accessor('io_percent', {
    header: 'IO%',
    cell: (info) => {
      const val = info.getValue();
      return val !== null ? `${formatNumber(val, 1)}%` : '-';
    },
  }),
  columnHelper.accessor('market_cap', {
    header: 'MC',
    cell: (info) => formatMillions(info.getValue()),
  }),
  columnHelper.accessor('short_interest', {
    header: 'SI%',
    cell: (info) => {
      const val = info.getValue();
      return val !== null ? `${formatNumber(val, 1)}%` : '-';
    },
  }),
  columnHelper.accessor('high_ctb', {
    header: 'CTB',
    cell: (info) => info.getValue() ? 'High' : '-',
  }),
  columnHelper.accessor('country', {
    header: 'Country',
    cell: (info) => info.getValue() || '-',
  }),
  columnHelper.accessor('finbert_score', {
    header: 'FinBERT',
    cell: (info) => {
      const val = info.getValue();
      if (val === null) return '-';
      const color = val > 0 ? 'text-green-400' : val < 0 ? 'text-red-400' : '';
      return <span className={color}>{formatNumber(val, 2)}</span>;
    },
  }),
  columnHelper.accessor('return_pct', {
    header: 'Return',
    cell: (info) => {
      const val = info.getValue();
      if (val === null) return '-';
      const color = val > 0 ? 'text-green-400' : val < 0 ? 'text-red-400' : '';
      return <span className={color}>{formatNumber(val, 2)}%</span>;
    },
  }),
  columnHelper.accessor('trigger_type', {
    header: 'Status',
    cell: (info) => {
      const val = info.getValue();
      const colors: Record<string, string> = {
        take_profit: 'text-green-400',
        stop_loss: 'text-red-400',
        timeout: 'text-yellow-400',
        no_entry: 'text-slate-500',
        no_data: 'text-slate-600',
      };
      return <span className={colors[val] || ''}>{val.replace('_', ' ')}</span>;
    },
  }),
];

interface Props {
  data: BacktestResult[];
  selectedRow: BacktestResult | null;
  onSelectRow: (row: BacktestResult | null) => void;
}

export function AnnouncementsTable({ data, selectedRow, onSelectRow }: Props) {
  // Sorting state is local - survives data updates
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'timestamp', desc: true },
  ]);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <div className="table-container h-full overflow-auto">
      <table>
        <thead className="sticky top-0">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  onClick={header.column.getToggleSortingHandler()}
                  className={header.column.getIsSorted() ? 'sorted' : ''}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {{
                    asc: ' ▲',
                    desc: ' ▼',
                  }[header.column.getIsSorted() as string] ?? ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const isSelected = selectedRow?.ticker === row.original.ticker &&
                              selectedRow?.timestamp === row.original.timestamp;
            return (
              <tr
                key={row.id}
                className={isSelected ? 'selected cursor-pointer' : 'cursor-pointer'}
                onClick={() => onSelectRow(isSelected ? null : row.original)}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      {data.length === 0 && (
        <div className="text-center text-slate-500 py-8">
          No data. Click "Run Backtest" to load results.
        </div>
      )}
    </div>
  );
}
