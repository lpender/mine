import os
from typing import Optional
from .base import OHLCVDataProvider
from .polygon import PolygonProvider
from .alpaca import AlpacaProvider
from .ib import IBProvider

__all__ = [
    "OHLCVDataProvider",
    "PolygonProvider",
    "AlpacaProvider",
    "IBProvider",
    "get_provider",
]


def get_provider(backend: Optional[str] = None) -> OHLCVDataProvider:
    """
    Factory to get the configured data provider.

    Args:
        backend: Provider name ('polygon', 'alpaca', 'ib').
                 If not specified, uses DATA_BACKEND env var, defaulting to 'alpaca'.

    Returns:
        Configured OHLCVDataProvider instance

    Raises:
        ValueError: If backend is unknown
    """
    backend = backend or os.getenv("DATA_BACKEND", "alpaca")
    backend = backend.lower()

    if backend == "polygon" or backend == "massive":
        return PolygonProvider()
    elif backend == "alpaca":
        return AlpacaProvider()
    elif backend == "ib":
        return IBProvider()
    else:
        raise ValueError(f"Unknown data backend: {backend}. Valid options: polygon, alpaca, ib")
