"""Read `results.csv` and print a comparison table grouped by phase.

Usage:
    uv run python -m tests.integration.eval.report
    uv run python -m tests.integration.eval.report --csv path/to/other.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

DEFAULT_CSV = Path(__file__).parent / "results.csv"

METRIC_COLUMNS = ("distinctness", "profile_groundedness", "jd_relevance")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def render(rows: list[dict[str, str]]) -> str:
    """Build a textual report. One block per phase; per-fixture rows + means."""
    if not rows:
        return (
            "(results.csv empty or missing — run "
            "`INTEGRATION=1 pytest tests/integration/eval -k quality`)"
        )

    by_phase: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_phase[r.get("phase", "?")].append(r)

    out: list[str] = []
    for phase in sorted(by_phase):
        phase_rows = by_phase[phase]
        out.append(f"\nphase = {phase}  ({len(phase_rows)} runs)")
        out.append("-" * 78)
        header = f"{'fixture':<22}{'round_type':<22}" + "".join(f"{m:>18}" for m in METRIC_COLUMNS)
        out.append(header)
        for r in sorted(phase_rows, key=lambda x: (x.get("fixture", ""), x.get("round_type", ""))):
            line = f"{r.get('fixture', ''):<22}{r.get('round_type', ''):<22}"
            for m in METRIC_COLUMNS:
                v = _to_float(r.get(m, ""))
                line += f"{v:>18.4f}" if v is not None else f"{'':>18}"
            out.append(line)

        # Per-metric mean across all phase rows.
        out.append("-" * 78)
        means_line = f"{'MEAN':<22}{'':<22}"
        for m in METRIC_COLUMNS:
            vals = [v for v in (_to_float(r.get(m, "")) for r in phase_rows) if v is not None]
            means_line += f"{statistics.fmean(vals):>18.4f}" if vals else f"{'':>18}"
        out.append(means_line)

    # Cross-phase delta block (only meaningful with ≥2 phases).
    if len(by_phase) >= 2:
        out.append("\nCross-phase means")
        out.append("-" * 78)
        head = f"{'phase':<22}{'':<22}" + "".join(f"{m:>18}" for m in METRIC_COLUMNS)
        out.append(head)
        for phase in sorted(by_phase):
            phase_rows = by_phase[phase]
            line = f"{phase:<22}{'':<22}"
            for m in METRIC_COLUMNS:
                vals = [v for v in (_to_float(r.get(m, "")) for r in phase_rows) if v is not None]
                line += f"{statistics.fmean(vals):>18.4f}" if vals else f"{'':>18}"
            out.append(line)

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    rows = _read_rows(args.csv)
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
