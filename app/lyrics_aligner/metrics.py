"""Alignment quality metrics (Bloco F1)."""

from __future__ import annotations

from typing import Sequence


def compute_aae(predicted: Sequence, reference: Sequence, tolerance: float = 0.0) -> float:
    """Average Absolute Error between predicted and reference cue starts (seconds)."""
    n = min(len(predicted), len(reference))
    if n == 0:
        return 0.0
    total = 0.0
    for i in range(n):
        p = predicted[i]
        r = reference[i]
        ps = p.start if hasattr(p, "start") else float(p.get("start", 0))
        rs = r.start if hasattr(r, "start") else float(r.get("start", 0))
        total += abs(ps - rs)
    return total / n


def compute_pcs(predicted: Sequence, reference: Sequence, tolerance: float = 0.5) -> float:
    """Percentage of Correct Segments (within tolerance seconds)."""
    n = min(len(predicted), len(reference))
    if n == 0:
        return 0.0
    correct = 0
    for i in range(n):
        p = predicted[i]
        r = reference[i]
        ps = p.start if hasattr(p, "start") else float(p.get("start", 0))
        rs = r.start if hasattr(r, "start") else float(r.get("start", 0))
        if abs(ps - rs) <= tolerance:
            correct += 1
    return (correct / n) * 100.0


def compute_mae(predicted: Sequence, reference: Sequence) -> float:
    """Median Absolute Error."""
    import statistics
    n = min(len(predicted), len(reference))
    if n == 0:
        return 0.0
    diffs = []
    for i in range(n):
        p = predicted[i]
        r = reference[i]
        ps = p.start if hasattr(p, "start") else float(p.get("start", 0))
        rs = r.start if hasattr(r, "start") else float(r.get("start", 0))
        diffs.append(abs(ps - rs))
    return statistics.median(diffs)


def metrics_report(predicted: Sequence, reference: Sequence, tolerance: float = 0.5) -> str:
    aae = compute_aae(predicted, reference)
    pcs = compute_pcs(predicted, reference, tolerance)
    mae = compute_mae(predicted, reference)
    return (
        f"AAE (avg abs error): {aae:.3f}s\n"
        f"MAE (median abs error): {mae:.3f}s\n"
        f"PCS (correct within {tolerance}s): {pcs:.1f}%\n"
        f"Cues: {min(len(predicted), len(reference))}"
    )
