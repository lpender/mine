import type { BacktestConfig } from '../types';

interface Props {
  config: BacktestConfig;
  onChange: (config: BacktestConfig) => void;
}

export function BacktestConfigPanel({ config, onChange }: Props) {
  const update = (key: keyof BacktestConfig, value: number | boolean) => {
    onChange({ ...config, [key]: value });
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-200">Backtest Config</h2>

      <div>
        <label className="sidebar-label">Entry Trigger (%)</label>
        <div className="slider-group">
          <input
            type="range"
            min={0}
            max={20}
            step={0.5}
            value={config.entry_trigger_pct}
            onChange={(e) => update('entry_trigger_pct', parseFloat(e.target.value))}
          />
          <span className="value">{config.entry_trigger_pct}%</span>
        </div>
      </div>

      <div>
        <label className="sidebar-label">Take Profit (%)</label>
        <div className="slider-group">
          <input
            type="range"
            min={1}
            max={50}
            step={0.5}
            value={config.take_profit_pct}
            onChange={(e) => update('take_profit_pct', parseFloat(e.target.value))}
          />
          <span className="value">{config.take_profit_pct}%</span>
        </div>
      </div>

      <div>
        <label className="sidebar-label">Stop Loss (%)</label>
        <div className="slider-group">
          <input
            type="range"
            min={1}
            max={20}
            step={0.5}
            value={config.stop_loss_pct}
            onChange={(e) => update('stop_loss_pct', parseFloat(e.target.value))}
          />
          <span className="value">{config.stop_loss_pct}%</span>
        </div>
      </div>

      <div>
        <label className="sidebar-label">Window (min)</label>
        <div className="slider-group">
          <input
            type="range"
            min={15}
            max={240}
            step={15}
            value={config.window_minutes}
            onChange={(e) => update('window_minutes', parseInt(e.target.value))}
          />
          <span className="value">{config.window_minutes}</span>
        </div>
      </div>

      <div>
        <label className="sidebar-label">Volume Threshold (K)</label>
        <div className="slider-group">
          <input
            type="range"
            min={0}
            max={1000}
            step={50}
            value={config.volume_threshold / 1000}
            onChange={(e) => update('volume_threshold', parseInt(e.target.value) * 1000)}
          />
          <span className="value">{config.volume_threshold / 1000}K</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="entry_by_message_second"
          checked={config.entry_by_message_second}
          onChange={(e) => update('entry_by_message_second', e.target.checked)}
        />
        <label htmlFor="entry_by_message_second" className="text-sm text-slate-300">
          Entry by message second
        </label>
      </div>
    </div>
  );
}
