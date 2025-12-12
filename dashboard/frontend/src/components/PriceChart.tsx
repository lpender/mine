import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import type { IChartApi, CandlestickData, Time, HistogramData } from 'lightweight-charts';
import type { OHLCVBar } from '../types';

const API_BASE = 'http://localhost:8000';

async function fetchOHLCV(ticker: string, timestamp: string): Promise<OHLCVBar[]> {
  const res = await fetch(`${API_BASE}/api/ohlcv/${ticker}/${encodeURIComponent(timestamp)}`);
  if (!res.ok) throw new Error('Failed to fetch OHLCV');
  return res.json();
}

interface Props {
  ticker: string;
  timestamp: string;
  entryPrice: number | null;
  exitPrice: number | null;
  takeProfitPct: number;
  stopLossPct: number;
}

export function PriceChart({
  ticker,
  timestamp,
  entryPrice,
  exitPrice,
  takeProfitPct,
  stopLossPct,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const { data: bars, isLoading, error } = useQuery({
    queryKey: ['ohlcv', ticker, timestamp],
    queryFn: () => fetchOHLCV(ticker, timestamp),
  });

  useEffect(() => {
    if (!containerRef.current || !bars || bars.length === 0) return;

    // Cleanup previous chart
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#1e293b' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#334155' },
        horzLines: { color: '#334155' },
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    chartRef.current = chart;

    // Candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    const candleData: CandlestickData<Time>[] = bars.map((bar) => ({
      time: (new Date(bar.timestamp).getTime() / 1000) as Time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));

    candleSeries.setData(candleData);

    // Volume series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#3b82f6',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const volumeData: HistogramData<Time>[] = bars.map((bar) => ({
      time: (new Date(bar.timestamp).getTime() / 1000) as Time,
      value: bar.volume,
      color: bar.close >= bar.open ? '#22c55e40' : '#ef444440',
    }));

    volumeSeries.setData(volumeData);

    // Price lines
    if (entryPrice !== null) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: '#3b82f6',
        lineWidth: 2,
        lineStyle: 2, // Dashed
        axisLabelVisible: true,
        title: 'Entry',
      });

      // Take profit line
      const tpPrice = entryPrice * (1 + takeProfitPct / 100);
      candleSeries.createPriceLine({
        price: tpPrice,
        color: '#22c55e',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'TP',
      });

      // Stop loss line
      const slPrice = entryPrice * (1 - stopLossPct / 100);
      candleSeries.createPriceLine({
        price: slPrice,
        color: '#ef4444',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'SL',
      });
    }

    // Fit content
    chart.timeScale().fitContent();

    // Handle resize
    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [bars, entryPrice, exitPrice, takeProfitPct, stopLossPct]);

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-slate-400">
        Loading chart...
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-red-400">
        Failed to load chart data
      </div>
    );
  }

  if (!bars || bars.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-slate-400">
        No chart data available
      </div>
    );
  }

  return (
    <div className="h-full">
      <div className="text-sm text-slate-300 mb-2">
        {ticker} - {new Date(timestamp).toLocaleString()}
        {entryPrice !== null && (
          <span className="ml-4">
            Entry: ${entryPrice.toFixed(2)}
            {exitPrice !== null && (
              <span className={exitPrice > entryPrice ? 'text-green-400' : 'text-red-400'}>
                {' -> '}${exitPrice.toFixed(2)} ({((exitPrice - entryPrice) / entryPrice * 100).toFixed(2)}%)
              </span>
            )}
          </span>
        )}
      </div>
      <div ref={containerRef} className="h-[calc(100%-2rem)]" />
    </div>
  );
}
