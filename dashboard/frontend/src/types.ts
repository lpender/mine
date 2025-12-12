export interface BacktestConfig {
  entry_trigger_pct: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  volume_threshold: number;
  window_minutes: number;
  entry_by_message_second: boolean;
}

export interface BacktestResult {
  ticker: string;
  timestamp: string;
  headline: string;
  channel: string;
  price_threshold: number;
  market_session: string;
  float_shares: number | null;
  io_percent: number | null;
  market_cap: number | null;
  short_interest: number | null;
  high_ctb: boolean;
  country: string | null;
  finbert_label: string | null;
  finbert_score: number | null;
  gap_pct: number | null;
  premarket_dollar_volume: number | null;
  financing_type: string | null;
  scanner_gain_pct: number | null;
  rvol: number | null;
  mention_count: number | null;
  is_nhod: boolean;
  is_nsh: boolean;
  has_news: boolean;
  // Backtest results
  entry_price: number | null;
  exit_price: number | null;
  return_pct: number | null;
  trigger_type: string;
  entry_time: string | null;
  exit_time: string | null;
}

export interface BacktestSummary {
  total_announcements: number;
  total_trades: number;
  winners: number;
  losers: number;
  win_rate: number;
  avg_return: number;
  total_return: number;
  profit_factor: number;
  best_trade: number;
  worst_trade: number;
  no_entry_count: number;
  no_data_count: number;
}

export interface BacktestResponse {
  results: BacktestResult[];
  summary: BacktestSummary;
}

export interface OHLCVBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Filters {
  sessions: {
    premarket: boolean;
    market: boolean;
    postmarket: boolean;
    closed: boolean;
  };
  ctb: 'any' | 'high' | 'not_high';
  io_min: number;
  io_max: number;
  price_min: number;
  price_max: number;
  float_min: number;
  float_max: number;
  mc_min: number;
  mc_max: number;
}

export const defaultFilters: Filters = {
  sessions: {
    premarket: true,
    market: true,
    postmarket: true,
    closed: true,
  },
  ctb: 'any',
  io_min: 0,
  io_max: 100,
  price_min: 0,
  price_max: 1000,
  float_min: 0,
  float_max: 1000,
  mc_min: 0,
  mc_max: 100000,
};

export const defaultConfig: BacktestConfig = {
  entry_trigger_pct: 5,
  take_profit_pct: 10,
  stop_loss_pct: 3,
  volume_threshold: 0,
  window_minutes: 120,
  entry_by_message_second: false,
};
