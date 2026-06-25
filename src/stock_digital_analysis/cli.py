"""Command line entry points for stock digital analysis reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from stock_digital_analysis.digital_distribution import (
    write_stock_datadir_dashboard_html,
)


def write_html_main() -> int:
    parser = argparse.ArgumentParser(description="Write a stock DAT directory HTML report.")
    parser.add_argument("data_dir", type=Path, help="stock data directory, e.g. datadir")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("reports/stock-digital-report.html"),
        help="Output HTML path",
    )
    parser.add_argument("--start", help="Start date/time")
    parser.add_argument("--end", help="End date/time")
    parser.add_argument(
        "--adjust",
        choices=("none", "front-ratio", "back-ratio"),
        default="none",
        help="Ratio price adjustment using the factor stored in DAT files",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="Minimum selected bars required to include a symbol",
    )
    parser.add_argument(
        "--overview-only",
        action="store_true",
        help="Only include the aggregate ranking dashboard",
    )
    parser.add_argument(
        "--separate-symbol-pages",
        action="store_true",
        help="Write one symbols/<symbol>.html page per stock and link them from the overview",
    )
    name_group = parser.add_mutually_exclusive_group()
    name_group.add_argument(
        "--resolve-names",
        dest="resolve_names",
        action="store_true",
        default=True,
        help="Resolve Chinese stock names through Redis/Sina when possible (default)",
    )
    name_group.add_argument(
        "--no-resolve-names",
        dest="resolve_names",
        action="store_false",
        help="Disable network/API stock-name resolution",
    )
    args = parser.parse_args()

    output = write_stock_datadir_dashboard_html(
        args.output,
        args.data_dir,
        start=args.start,
        end=args.end,
        adjust=args.adjust,
        min_samples=args.min_samples,
        include_symbol_dashboards=not args.overview_only,
        resolve_names=args.resolve_names,
        separate_symbol_pages=args.separate_symbol_pages,
    )
    print(output.resolve())
    return 0
