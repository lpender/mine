import { useState, useMemo } from 'react';
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query';
import type { BacktestConfig, BacktestResponse, BacktestResult, Filters } from './types';
import { defaultConfig, defaultFilters } from './types';
import { AnnouncementsTable } from './components/AnnouncementsTable';
import { BacktestConfigPanel } from './components/BacktestConfig';
import { FiltersPanel } from './components/Filters';
import { SummaryStats } from './components/SummaryStats';
import { PriceChart } from './components/PriceChart';

const queryClient = new QueryClient();

const API_BASE = 'http://localhost:8000';

async function runBacktest(config: BacktestConfig): Promise<BacktestResponse> {
  const res = await fetch(`${API_BASE}/api/backtest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error('Backtest failed');
  return res.json();
}

function Dashboard() {
  const [config, setConfig] = useState<BacktestConfig>(defaultConfig);
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [selectedRow, setSelectedRow] = useState<BacktestResult | null>(null);
  const [data, setData] = useState<BacktestResponse | null>(null);

  const mutation = useMutation({
    mutationFn: runBacktest,
    onSuccess: (response) => {
      setData(response);
    },
  });

  // Filter results based on current filters
  const filteredResults = useMemo(() => {
    if (!data?.results) return [];

    return data.results.filter((r) => {
      // Session filter
      const session = r.market_session?.toLowerCase() || 'market';
      if (!filters.sessions[session as keyof typeof filters.sessions]) return false;

      // CTB filter
      if (filters.ctb === 'high' && !r.high_ctb) return false;
      if (filters.ctb === 'not_high' && r.high_ctb) return false;

      // IO filter
      if (r.io_percent !== null) {
        if (r.io_percent < filters.io_min || r.io_percent > filters.io_max) return false;
      }

      // Price filter
      if (r.price_threshold < filters.price_min || r.price_threshold > filters.price_max) return false;

      // Float filter (in millions)
      if (r.float_shares !== null) {
        const floatM = r.float_shares / 1_000_000;
        if (floatM < filters.float_min || floatM > filters.float_max) return false;
      }

      // Market cap filter (in millions)
      if (r.market_cap !== null) {
        const mcM = r.market_cap / 1_000_000;
        if (mcM < filters.mc_min || mcM > filters.mc_max) return false;
      }

      return true;
    });
  }, [data?.results, filters]);

  const handleRunBacktest = () => {
    mutation.mutate(config);
  };

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <div className="sidebar">
        <h1 className="text-xl font-bold text-slate-100 mb-4">Backtest Dashboard</h1>

        <div className="sidebar-section">
          <BacktestConfigPanel config={config} onChange={setConfig} />
          <button
            className="btn btn-primary w-full mt-4"
            onClick={handleRunBacktest}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? 'Running...' : 'Run Backtest'}
          </button>
        </div>

        <div className="sidebar-section">
          <FiltersPanel filters={filters} onChange={setFilters} />
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
        {/* Summary stats */}
        {data?.summary && <SummaryStats summary={data.summary} />}

        {/* Table */}
        <div className="flex-1 overflow-hidden card">
          <AnnouncementsTable
            data={filteredResults}
            selectedRow={selectedRow}
            onSelectRow={setSelectedRow}
          />
        </div>

        {/* Chart */}
        {selectedRow && (
          <div className="h-80 card">
            <PriceChart
              ticker={selectedRow.ticker}
              timestamp={selectedRow.timestamp}
              entryPrice={selectedRow.entry_price}
              exitPrice={selectedRow.exit_price}
              takeProfitPct={config.take_profit_pct}
              stopLossPct={config.stop_loss_pct}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Dashboard />
    </QueryClientProvider>
  );
}

export default App;
