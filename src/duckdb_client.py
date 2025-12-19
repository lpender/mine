"""
DuckDB client for fast Parquet-based queries.

This provides the same interface as PostgresClient but reads from Parquet files
using DuckDB for 10-100x faster analytical queries.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

from .models import Announcement, OHLCVBar

logger = logging.getLogger(__name__)

PARQUET_DIR = Path(__file__).parent.parent / "data" / "parquet"


class DuckDBClient:
    """Fast read-only client using DuckDB to query Parquet files."""

    def __init__(self, parquet_dir: Optional[Path] = None):
        self.parquet_dir = parquet_dir or PARQUET_DIR
        self._conn = None

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """Get or create DuckDB connection."""
        if self._conn is None:
            # In-memory DuckDB - fast for analytical queries
            self._conn = duckdb.connect(":memory:")
            # Enable parallel execution
            self._conn.execute("SET threads TO 4")
        return self._conn

    def _announcements_path(self) -> Path:
        return self.parquet_dir / "announcements.parquet"

    def _ohlcv_glob(self) -> str:
        return str(self.parquet_dir / "ohlcv_1min" / "*.parquet")

    def load_announcements(self, source: Optional[str] = "backfill") -> List[Announcement]:
        """Load announcements from Parquet."""
        path = self._announcements_path()
        if not path.exists():
            logger.warning(f"Parquet file not found: {path}")
            return []

        conn = self._get_conn()

        if source:
            query = f"""
                SELECT * FROM read_parquet('{path}')
                WHERE source = '{source}'
                ORDER BY timestamp DESC
            """
        else:
            query = f"""
                SELECT * FROM read_parquet('{path}')
                ORDER BY timestamp DESC
            """

        df = conn.execute(query).df()
        return self._df_to_announcements(df)

    def get_announcement_filter_options(self, source: str = "backfill") -> dict:
        """Get distinct values for filter widgets."""
        path = self._announcements_path()
        if not path.exists():
            return {"countries": [], "authors": [], "channels": [], "directions": []}

        conn = self._get_conn()

        def distinct_nonempty(col: str) -> list:
            query = f"""
                SELECT DISTINCT {col}
                FROM read_parquet('{path}')
                WHERE source = '{source}'
                  AND {col} IS NOT NULL
                  AND TRIM({col}) != ''
                ORDER BY {col}
            """
            result = conn.execute(query).fetchall()
            return [r[0] for r in result if r[0]]

        return {
            "countries": distinct_nonempty("country"),
            "authors": distinct_nonempty("author"),
            "channels": distinct_nonempty("channel"),
            "directions": distinct_nonempty("direction"),
        }

    def load_announcements_sampled_and_filtered(
        self,
        *,
        source: str = "backfill",
        sample_pct: int = 100,
        sample_seed: int = 0,
        sample_ids: Optional[List[int]] = None,
        sessions: Optional[List[str]] = None,
        countries: Optional[List[str]] = None,
        country_blacklist: Optional[List[str]] = None,
        authors: Optional[List[str]] = None,
        channels: Optional[List[str]] = None,
        directions: Optional[List[str]] = None,
        scanner_test: bool = False,
        scanner_after_lull: bool = False,
        max_mentions: Optional[int] = None,
        exclude_financing_headlines: bool = False,
        require_headline: bool = False,
        exclude_headline: bool = False,
        float_min_m: float = 0.0,
        float_max_m: float = 1000.0,
        mc_min_m: float = 0.0,
        mc_max_m: float = 10000.0,
        prior_move_min: float = 0.0,
        prior_move_max: float = 0.0,
        nhod_filter: str = "Any",
        nsh_filter: str = "Any",
        rvol_min: float = 0.0,
        rvol_max: float = 0.0,
        exclude_financing_types: Optional[List[str]] = None,
        exclude_biotech: bool = False,
    ) -> tuple:
        """Load filtered announcements using DuckDB SQL."""
        path = self._announcements_path()
        if not path.exists():
            return (0, [])

        conn = self._get_conn()

        # Build WHERE clause
        conditions = [f"source = '{source}'"]

        if countries:
            countries_str = ", ".join(f"'{c}'" for c in countries)
            conditions.append(f"country IN ({countries_str})")

        if country_blacklist:
            blacklist_str = ", ".join(f"'{c}'" for c in country_blacklist)
            conditions.append(f"(country IS NULL OR country NOT IN ({blacklist_str}))")

        if authors:
            authors_str = ", ".join(f"'{a}'" for a in authors)
            conditions.append(f"author IN ({authors_str})")

        if channels:
            channels_str = ", ".join(f"'{c}'" for c in channels)
            conditions.append(f"channel IN ({channels_str})")

        if directions:
            directions_str = ", ".join(f"'{d}'" for d in directions)
            conditions.append(f"direction IN ({directions_str})")

        if scanner_test:
            conditions.append("scanner_test = true")

        if scanner_after_lull:
            conditions.append("scanner_after_lull = true")

        if max_mentions is not None:
            conditions.append(f"(mention_count IS NULL OR mention_count <= {max_mentions})")

        if exclude_financing_headlines:
            conditions.append("(headline_is_financing IS NULL OR headline_is_financing = false)")

        if require_headline:
            conditions.append("headline IS NOT NULL AND TRIM(headline) != ''")

        if exclude_headline:
            conditions.append("(headline IS NULL OR TRIM(headline) = '')")

        # Float filter (in millions)
        if float_min_m > 0:
            conditions.append(f"(float_shares IS NULL OR float_shares >= {float_min_m * 1_000_000})")
        if float_max_m < 1000:
            conditions.append(f"(float_shares IS NULL OR float_shares <= {float_max_m * 1_000_000})")

        # Market cap filter (in millions)
        if mc_min_m > 0:
            conditions.append(f"(market_cap IS NULL OR market_cap >= {mc_min_m * 1_000_000})")
        if mc_max_m < 10000:
            conditions.append(f"(market_cap IS NULL OR market_cap <= {mc_max_m * 1_000_000})")

        # NHOD filter
        if nhod_filter == "Yes":
            conditions.append("is_nhod = true")
        elif nhod_filter == "No":
            conditions.append("(is_nhod IS NULL OR is_nhod = false)")

        # NSH filter
        if nsh_filter == "Yes":
            conditions.append("is_nsh = true")
        elif nsh_filter == "No":
            conditions.append("(is_nsh IS NULL OR is_nsh = false)")

        # RVOL filter
        if rvol_min > 0:
            conditions.append(f"(rvol IS NULL OR rvol >= {rvol_min})")
        if rvol_max > 0:
            conditions.append(f"(rvol IS NULL OR rvol <= {rvol_max})")

        # Exclude financing types
        if exclude_financing_types:
            for ftype in exclude_financing_types:
                conditions.append(
                    f"(headline_financing_type IS NULL OR headline_financing_type != '{ftype}')"
                )

        # Exclude biotech
        if exclude_biotech:
            conditions.append(
                "(headline IS NULL OR headline NOT ILIKE '%biotech%' AND headline NOT ILIKE '%pharma%' AND headline NOT ILIKE '%clinical%' AND headline NOT ILIKE '%FDA%')"
            )

        where_clause = " AND ".join(conditions)

        # First get total count (before sampling)
        count_query = f"""
            SELECT COUNT(*) FROM read_parquet('{path}')
            WHERE {where_clause}
        """
        total_count = conn.execute(count_query).fetchone()[0]

        # Build main query with sampling
        if sample_pct < 100 and sample_ids is None:
            # Use TABLESAMPLE for random sampling
            query = f"""
                SELECT * FROM read_parquet('{path}')
                WHERE {where_clause}
                USING SAMPLE {sample_pct}% (BERNOULLI, SEED {sample_seed})
                ORDER BY timestamp DESC
            """
        elif sample_ids:
            ids_str = ", ".join(str(i) for i in sample_ids)
            query = f"""
                SELECT * FROM read_parquet('{path}')
                WHERE {where_clause} AND id IN ({ids_str})
                ORDER BY timestamp DESC
            """
        else:
            query = f"""
                SELECT * FROM read_parquet('{path}')
                WHERE {where_clause}
                ORDER BY timestamp DESC
            """

        df = conn.execute(query).df()

        # Filter by session (requires timestamp parsing)
        if sessions:
            from .models import get_market_session

            def matches_session(ts):
                return get_market_session(ts) in sessions

            df = df[df["timestamp"].apply(matches_session)]

        announcements = self._df_to_announcements(df)
        return (total_count, announcements)

    def get_ohlcv_bars_bulk(self, announcement_keys: List[tuple]) -> dict:
        """
        Get OHLCV bars for multiple announcements.

        This is the main performance bottleneck - DuckDB should be much faster
        than Postgres for this bulk read.

        Args:
            announcement_keys: List of (ticker, timestamp) tuples

        Returns:
            Dict mapping (ticker, timestamp) to list of OHLCVBar
        """
        if not announcement_keys:
            return {}

        # Pre-create result map
        result = {key: [] for key in announcement_keys}

        ohlcv_glob = self._ohlcv_glob()
        conn = self._get_conn()

        # Build VALUES clause for the keys
        # DuckDB can handle this efficiently with a semi-join
        values_list = []
        for ticker, ts in announcement_keys:
            # Handle both datetime and string timestamps
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%f")
            else:
                ts_str = str(ts)
            values_list.append(f"('{ticker}', '{ts_str}'::TIMESTAMP)")

        values_clause = ", ".join(values_list)

        query = f"""
            WITH keys AS (
                SELECT * FROM (VALUES {values_clause}) AS t(ann_ticker, ann_timestamp)
            )
            SELECT
                o.announcement_ticker,
                o.announcement_timestamp,
                o.timestamp,
                o.open,
                o.high,
                o.low,
                o.close,
                o.volume,
                o.vwap
            FROM read_parquet('{ohlcv_glob}') o
            INNER JOIN keys k
                ON o.announcement_ticker = k.ann_ticker
                AND o.announcement_timestamp = k.ann_timestamp
            ORDER BY
                o.announcement_ticker,
                o.announcement_timestamp,
                o.timestamp
        """

        try:
            rows = conn.execute(query).fetchall()

            for ann_ticker, ann_ts, ts, o, h, l, c, vol, vwap in rows:
                key = (ann_ticker, ann_ts)
                bucket = result.get(key)
                if bucket is not None:
                    bucket.append(
                        OHLCVBar(
                            timestamp=ts,
                            open=o,
                            high=h,
                            low=l,
                            close=c,
                            volume=int(vol) if vol else 0,
                            vwap=vwap,
                        )
                    )

            logger.debug(f"DuckDB loaded {len(rows)} OHLCV bars for {len(announcement_keys)} keys")

        except Exception as e:
            logger.error(f"DuckDB query failed: {e}")
            # Return empty result on error
            pass

        return result

    def _df_to_announcements(self, df: pd.DataFrame) -> List[Announcement]:
        """Convert DataFrame to list of Announcement dataclass."""
        announcements = []
        for _, row in df.iterrows():
            ann = Announcement(
                ticker=row.get("ticker", ""),
                timestamp=row.get("timestamp"),
                price_threshold=row.get("price_threshold", 0.0),
                headline=row.get("headline") or "",
                country=row.get("country") or "US",
                float_shares=row.get("float_shares"),
                io_percent=row.get("io_percent"),
                market_cap=row.get("market_cap"),
                reg_sho=bool(row.get("reg_sho")),
                high_ctb=bool(row.get("high_ctb")),
                short_interest=row.get("short_interest"),
                channel=row.get("channel"),
                author=row.get("author"),
                direction=row.get("direction"),
                headline_is_financing=row.get("headline_is_financing"),
                headline_financing_type=row.get("headline_financing_type"),
                headline_financing_tags=row.get("headline_financing_tags"),
                prev_close=row.get("prev_close"),
                regular_open=row.get("regular_open"),
                premarket_gap_pct=row.get("premarket_gap_pct"),
                premarket_volume=row.get("premarket_volume"),
                premarket_dollar_volume=row.get("premarket_dollar_volume"),
                scanner_gain_pct=row.get("scanner_gain_pct"),
                is_nhod=bool(row.get("is_nhod")),
                is_nsh=bool(row.get("is_nsh")),
                rvol=row.get("rvol"),
                mention_count=row.get("mention_count"),
                has_news=bool(row.get("has_news", True)),
                green_bars=row.get("green_bars"),
                bar_minutes=row.get("bar_minutes"),
                scanner_test=bool(row.get("scanner_test")),
                scanner_after_lull=bool(row.get("scanner_after_lull")),
                source_message=row.get("source_message"),
                source_html=row.get("source_html"),
                ohlcv_status=row.get("ohlcv_status"),
            )
            announcements.append(ann)
        return announcements


# Global instance
_duckdb_client: Optional[DuckDBClient] = None


def get_duckdb_client() -> DuckDBClient:
    """Get the global DuckDB client."""
    global _duckdb_client
    if _duckdb_client is None:
        _duckdb_client = DuckDBClient()
    return _duckdb_client
