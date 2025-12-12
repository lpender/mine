import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()

from src.features import classify_headline, compute_premarket_features
from src.massive_client import MassiveClient


def main():
    parser = argparse.ArgumentParser(
        description="Enrich announcements with (a) headline financing flags and (b) premarket gap/volume features."
    )
    parser.add_argument(
        "--input",
        default="data/ohlcv/announcements.json",
        help="Announcements JSON path (default: data/ohlcv/announcements.json)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path (default: overwrite input in-place).",
    )
    parser.add_argument(
        "--context-cache-dir",
        default="data/ohlcv_context",
        help="Cache dir for context OHLCV fetches (default: data/ohlcv_context).",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.5,
        help="Minimum seconds between Massive API requests (default: 0.5).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing headline/context fields if present.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="For debugging: only process first N announcements (default: 0 means all).",
    )
    parser.add_argument(
        "--only-cached",
        action="store_true",
        help="Only enrich announcements that already have cached 120m after-announcement OHLCV in data/ohlcv.",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=120,
        help="Window minutes used for cached after-announcement OHLCV check (default: 120).",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")

    with in_path.open("r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("Expected a JSON list of announcements.")

    client = MassiveClient(cache_dir=args.context_cache_dir, rate_limit_delay=args.rate_limit_delay)
    helper_client = MassiveClient(cache_dir="data/ohlcv", rate_limit_delay=args.rate_limit_delay)

    stats = {
        "total": len(data),
        "processed": 0,
        "headline_scored": 0,
        "context_scored": 0,
        "context_missing": 0,
        "context_errors": 0,
    }

    # Memoize expensive context computations: (ticker, session_date) -> features dict
    context_cache: Dict[Tuple[str, str], Dict[str, object]] = {}

    items = data[: args.limit] if args.limit and args.limit > 0 else data

    for ann in items:
        stats["processed"] += 1

        # Optional: only enrich announcements that already have after-announcement OHLCV cached
        if args.only_cached:
            try:
                ts0 = datetime.fromisoformat(ann["timestamp"])
                start0 = helper_client.get_effective_start_time(ts0)
                end0 = start0 + timedelta(minutes=int(args.window_minutes))
                cached = helper_client._load_from_cache(ann["ticker"], start0, end0)
                if cached is None:
                    continue
            except Exception:
                continue

        headline = ann.get("headline") or ""

        # Headline financing flags
        if args.overwrite or ann.get("headline_is_financing") is None:
            flags = classify_headline(headline)
            ann["headline_is_financing"] = bool(flags.is_financing)
            ann["headline_financing_type"] = flags.financing_type
            ann["headline_financing_tags"] = ",".join(flags.tags) if flags.tags else None
            stats["headline_scored"] += 1

        # Premarket context (gap/volume)
        context_fields = [
            "prev_close",
            "regular_open",
            "premarket_gap_pct",
            "premarket_volume",
            "premarket_dollar_volume",
        ]
        have_any = any(ann.get(k) is not None for k in context_fields)
        if args.overwrite or not have_any:
            try:
                ts = datetime.fromisoformat(ann["timestamp"])
                effective_start = helper_client.get_effective_start_time(ts)
                d = effective_start.date()

                cache_key = (str(ann["ticker"]), d.isoformat())
                if cache_key in context_cache:
                    feats = context_cache[cache_key]
                else:
                    feats = compute_premarket_features(client, ann["ticker"], d, use_cache=True)
                    context_cache[cache_key] = feats

                ann.update(feats)  # type: ignore[arg-type]

                if all(ann.get(k) is None for k in context_fields):
                    stats["context_missing"] += 1
                else:
                    stats["context_scored"] += 1
            except Exception:
                stats["context_missing"] += 1
                stats["context_errors"] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(data, f, indent=2)

    print(json.dumps(stats, indent=2))
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()

