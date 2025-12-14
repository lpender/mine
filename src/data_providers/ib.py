import os
import time
from datetime import datetime, timedelta
from typing import List, Optional
from ..models import OHLCVBar
from .base import OHLCVDataProvider


class IBProvider(OHLCVDataProvider):
    """OHLCV data provider using Interactive Brokers via ib_insync."""

    def __init__(
        self,
        host: str = None,
        port: int = None,
        client_id: int = None,
        rate_limit_delay: float = 0.5,
        timeout_s: float = 60.0,
    ):
        self.host = host or os.getenv("IB_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("IB_PORT", "4002"))
        self.client_id = int(client_id or os.getenv("IB_CLIENT_ID", "1"))
        self._rate_limit_delay = float(os.getenv("IB_RATE_LIMIT_DELAY", rate_limit_delay))
        self.timeout_s = float(os.getenv("IB_TIMEOUT_S", timeout_s))

        self._ib = None
        self._next_allowed_time = 0.0

    @property
    def rate_limit_delay(self) -> float:
        return self._rate_limit_delay

    def supports_extended_hours(self) -> bool:
        return True

    def _sleep_for_rate_limit(self):
        now = time.time()
        if now < self._next_allowed_time:
            time.sleep(self._next_allowed_time - now)

    def _bump_next_allowed_time(self, extra_delay_s: float = 0.0):
        now = time.time()
        base = now + self._rate_limit_delay
        extra = now + max(0.0, float(extra_delay_s))
        self._next_allowed_time = max(self._next_allowed_time, base, extra)

    def _connect(self):
        """Connect to IB Gateway if not already connected."""
        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError("ib_insync is required for IB provider. Install with: pip install ib_insync")

        if self._ib is None:
            self._ib = IB()

        if not self._ib.isConnected():
            print(f"[IB] Connecting to {self.host}:{self.port} (client_id={self.client_id})...")
            self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout_s)
            print(f"[IB] Connected")

    def _disconnect(self):
        """Disconnect from IB Gateway."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()

    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timespan: str = "minute",
    ) -> List[OHLCVBar]:
        try:
            from ib_insync import Stock
        except ImportError:
            raise ImportError("ib_insync is required for IB provider. Install with: pip install ib_insync")

        # Map timespan to IB bar size
        bar_size_map = {
            "minute": "1 min",
            "hour": "1 hour",
            "day": "1 day",
        }
        bar_size = bar_size_map.get(timespan, "1 min")

        print(f"[IB] Fetching {ticker} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")

        try:
            self._sleep_for_rate_limit()
            self._connect()

            # Create contract
            contract = Stock(ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)

            # Calculate duration string
            duration_days = (end - start).days + 1
            if duration_days <= 1:
                duration_str = f"{int((end - start).total_seconds())} S"
            elif duration_days <= 30:
                duration_str = f"{duration_days} D"
            else:
                duration_str = f"{duration_days} D"

            # Request historical data
            # Use RTH=False to include extended hours
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=end.strftime("%Y%m%d %H:%M:%S"),
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=False,  # Include extended hours
                formatDate=1,
            )

            self._bump_next_allowed_time()

            if not bars:
                print(f"[IB] No data for {ticker}")
                return []

            result = []
            for bar in bars:
                # Filter to requested time range
                bar_time = bar.date
                if isinstance(bar_time, str):
                    bar_time = datetime.strptime(bar_time, "%Y%m%d  %H:%M:%S")

                if start <= bar_time <= end:
                    result.append(OHLCVBar(
                        timestamp=bar_time,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=int(bar.volume),
                        vwap=getattr(bar, "average", None),
                    ))

            return result

        except Exception as e:
            print(f"[IB] Error fetching {ticker}: {e}")
            return []

    def __del__(self):
        """Cleanup on deletion."""
        self._disconnect()
