from abc import ABC, abstractmethod
from datetime import datetime
from typing import List
from ..models import OHLCVBar


class OHLCVDataProvider(ABC):
    """Abstract base class for OHLCV data providers."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timespan: str = "minute",
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV bars for a ticker.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            start: Start datetime
            end: End datetime
            timespan: Time window (minute, hour, day)

        Returns:
            List of OHLCVBar objects
        """
        pass

    @abstractmethod
    def supports_extended_hours(self) -> bool:
        """Whether this provider has premarket/postmarket data."""
        pass

    @property
    @abstractmethod
    def rate_limit_delay(self) -> float:
        """Minimum seconds between requests."""
        pass

    @property
    def name(self) -> str:
        """Human-readable name of the provider."""
        return self.__class__.__name__
