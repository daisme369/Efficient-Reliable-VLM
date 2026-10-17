"""Exploratory VQA scoring without inference or artifact I/O."""

from __future__ import annotations

from collections import defaultdict
from cxr_research.metrics import METRIC_VERSION


def normalize_answer(text: str) -> str:
    return " ".join(text.casefold().split())


def vqa_metrics(items: list[tuple[str, str, str, bool]]) -> dict:
    """Prediction, reference, answer type and primary eligibility; never return rows."""
    result = {"metric_version": METRIC_VERSION, "clinical_correctness": "not_measured"}
    for scope in ("primary", "secondary"):
        rows = [r for r in items if scope == "secondary" or r[3]]
        groups = defaultdict(list)
        for pred, ref, kind, _ in rows:
            groups[kind].append((normalize_answer(pred), normalize_answer(ref)))
        summary = {"count": len(rows)}
        for kind, pairs in groups.items():
            score = sum(p == r for p, r in pairs) / len(pairs)
            stats = {"count": len(pairs), "normalized_match": score,
                     "empty_output_count": sum(not p for p, _ in pairs)}
            if kind == "closed":
                labels = sorted({r for _, r in pairs} | {p for p, _ in pairs})
                f1s = []
                for label in labels:
                    tp = sum(p == r == label for p, r in pairs)
                    fp = sum(p == label and r != label for p, r in pairs)
                    fn = sum(r == label and p != label for p, r in pairs)
                    f1s.append(2 * tp / (2 * tp + fp + fn))
                stats.update(closed_accuracy=score, macro_f1=sum(f1s) / len(f1s))
            summary[kind] = stats
        result[scope] = summary
    return result
