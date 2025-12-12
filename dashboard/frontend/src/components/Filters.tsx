import type { Filters } from '../types';

interface Props {
  filters: Filters;
  onChange: (filters: Filters) => void;
}

export function FiltersPanel({ filters, onChange }: Props) {
  const updateSession = (session: keyof Filters['sessions'], value: boolean) => {
    onChange({
      ...filters,
      sessions: { ...filters.sessions, [session]: value },
    });
  };

  const update = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    onChange({ ...filters, [key]: value });
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-200">Filters</h2>

      {/* Session filters */}
      <div>
        <label className="sidebar-label">Sessions</label>
        <div className="grid grid-cols-2 gap-2">
          {(['premarket', 'market', 'postmarket', 'closed'] as const).map((s) => (
            <label key={s} className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={filters.sessions[s]}
                onChange={(e) => updateSession(s, e.target.checked)}
              />
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </label>
          ))}
        </div>
      </div>

      {/* CTB filter */}
      <div>
        <label className="sidebar-label">CTB</label>
        <select
          className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-slate-100"
          value={filters.ctb}
          onChange={(e) => update('ctb', e.target.value as Filters['ctb'])}
        >
          <option value="any">Any</option>
          <option value="high">High CTB</option>
          <option value="not_high">Not High CTB</option>
        </select>
      </div>

      {/* IO range */}
      <div>
        <label className="sidebar-label">IO% Range</label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            max={100}
            value={filters.io_min}
            onChange={(e) => update('io_min', parseFloat(e.target.value) || 0)}
          />
          <span className="text-slate-400">-</span>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.io_max}
            onChange={(e) => update('io_max', parseFloat(e.target.value) || 100)}
          />
        </div>
      </div>

      {/* Price range */}
      <div>
        <label className="sidebar-label">Price Range ($)</label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            max={1000}
            step={0.1}
            value={filters.price_min}
            onChange={(e) => update('price_min', parseFloat(e.target.value) || 0)}
          />
          <span className="text-slate-400">-</span>
          <input
            type="number"
            min={0}
            max={1000}
            step={0.1}
            value={filters.price_max}
            onChange={(e) => update('price_max', parseFloat(e.target.value) || 1000)}
          />
        </div>
      </div>

      {/* Float range */}
      <div>
        <label className="sidebar-label">Float (M)</label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            max={1000}
            value={filters.float_min}
            onChange={(e) => update('float_min', parseFloat(e.target.value) || 0)}
          />
          <span className="text-slate-400">-</span>
          <input
            type="number"
            min={0}
            max={1000}
            value={filters.float_max}
            onChange={(e) => update('float_max', parseFloat(e.target.value) || 1000)}
          />
        </div>
      </div>

      {/* Market cap range */}
      <div>
        <label className="sidebar-label">Market Cap (M)</label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            max={100000}
            value={filters.mc_min}
            onChange={(e) => update('mc_min', parseFloat(e.target.value) || 0)}
          />
          <span className="text-slate-400">-</span>
          <input
            type="number"
            min={0}
            max={100000}
            value={filters.mc_max}
            onChange={(e) => update('mc_max', parseFloat(e.target.value) || 100000)}
          />
        </div>
      </div>
    </div>
  );
}
