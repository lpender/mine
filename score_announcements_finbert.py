import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _pick_device() -> str:
    """
    Return a torch device string: "cuda", "mps", or "cpu".
    Import torch lazily so this script can still show argparse help without torch installed.
    """
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_finbert(device: str):
    """
    Load ProsusAI FinBERT and return (tokenizer, model, id2label).
    """
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_name = "ProsusAI/finbert"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()

    import torch

    model = model.to(torch.device(device))
    id2label = {int(k): v for k, v in model.config.id2label.items()} if model.config.id2label else {}
    return tokenizer, model, id2label


def _score_batch(
    texts: List[str],
    tokenizer,
    model,
    id2label: Dict[int, str],
    device: str,
    max_length: int,
) -> List[Dict[str, Any]]:
    import torch

    enc = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    enc = {k: v.to(torch.device(device)) for k, v in enc.items()}

    with torch.no_grad():
        out = model(**enc)
        probs = torch.softmax(out.logits, dim=-1).detach().cpu().numpy()

    # Map labels to indices. FinBERT typically uses POSITIVE/NEGATIVE/NEUTRAL.
    # We'll normalize to lower-case: positive/negative/neutral.
    label_by_idx = {i: (id2label.get(i) or str(i)) for i in range(probs.shape[1])}
    norm_label_by_idx = {i: label_by_idx[i].lower() for i in label_by_idx}

    # Best-effort lookup for probability fields
    idx_pos = next((i for i, l in norm_label_by_idx.items() if "pos" in l), None)
    idx_neg = next((i for i, l in norm_label_by_idx.items() if "neg" in l), None)
    idx_neu = next((i for i, l in norm_label_by_idx.items() if "neu" in l), None)

    results: List[Dict[str, Any]] = []
    for row in probs:
        best_idx = int(row.argmax())
        label = norm_label_by_idx.get(best_idx, str(best_idx))
        p_pos = float(row[idx_pos]) if idx_pos is not None else None
        p_neg = float(row[idx_neg]) if idx_neg is not None else None
        p_neu = float(row[idx_neu]) if idx_neu is not None else None

        finbert_score: Optional[float] = None
        if p_pos is not None and p_neg is not None:
            finbert_score = p_pos - p_neg

        results.append(
            {
                "finbert_label": label,
                "finbert_score": finbert_score,
                "finbert_pos": p_pos,
                "finbert_neg": p_neg,
                "finbert_neu": p_neu,
            }
        )
    return results


def _chunks(items: List[Any], n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def score_announcements_json(
    announcements: List[Dict[str, Any]],
    *,
    text_field: str,
    batch_size: int,
    max_length: int,
    overwrite: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    device = _pick_device()
    tokenizer, model, id2label = _load_finbert(device=device)

    stats = {
        "total": len(announcements),
        "scored": 0,
        "skipped_missing_text": 0,
        "skipped_existing": 0,
    }

    # Pre-select indices to score (so we can batch efficiently)
    to_score: List[Tuple[int, str]] = []
    for i, ann in enumerate(announcements):
        text = (ann.get(text_field) or "").strip()
        if not text:
            stats["skipped_missing_text"] += 1
            continue
        if (not overwrite) and (ann.get("finbert_score") is not None or ann.get("finbert_label") is not None):
            stats["skipped_existing"] += 1
            continue
        to_score.append((i, text))

    if not to_score:
        return announcements, stats

    # Optional progress bar if tqdm is installed
    try:
        from tqdm import tqdm  # type: ignore
    except Exception:  # pragma: no cover
        tqdm = None  # type: ignore

    iterator = _chunks(to_score, batch_size)
    if tqdm is not None:
        iterator = tqdm(list(iterator), desc=f"FinBERT scoring ({device})", unit="batch")  # type: ignore

    for batch in iterator:
        batch_indices = [i for i, _ in batch]
        batch_texts = [t for _, t in batch]
        batch_scores = _score_batch(
            batch_texts,
            tokenizer=tokenizer,
            model=model,
            id2label=id2label,
            device=device,
            max_length=max_length,
        )
        for idx, score_obj in zip(batch_indices, batch_scores):
            announcements[idx].update(score_obj)
            stats["scored"] += 1

    return announcements, stats


def main():
    parser = argparse.ArgumentParser(description="Score announcement headlines with ProsusAI/finbert (FinBERT).")
    parser.add_argument(
        "--input",
        default="data/ohlcv/announcements.json",
        help="Path to announcements JSON (default: data/ohlcv/announcements.json)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path (default: overwrite input in-place).",
    )
    parser.add_argument(
        "--text-field",
        default="headline",
        help="Field in each JSON object to score (default: headline).",
    )
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size (default: 32).")
    parser.add_argument("--max-length", type=int, default=128, help="Tokenizer max_length (default: 128).")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Recompute FinBERT even if finbert_* fields already exist.",
    )

    args = parser.parse_args()
    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")

    with in_path.open("r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("Expected JSON file to contain a list of announcements.")

    scored, stats = score_announcements_json(
        data,
        text_field=args.text_field,
        batch_size=args.batch_size,
        max_length=args.max_length,
        overwrite=bool(args.overwrite),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(scored, f, indent=2)

    print(json.dumps(stats, indent=2))
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()

