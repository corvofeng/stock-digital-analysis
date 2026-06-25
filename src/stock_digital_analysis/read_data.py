#!/usr/bin/env python3
"""Read stock 60-second local market data files without xtquant."""

from __future__ import annotations

import argparse
import csv
import json
import struct
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

HEADER_SIZE = 8
RECORD = struct.Struct("<I7iqiffiii")
CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume_lots: int
    volume_shares: int
    amount: int
    factor: float
    previous_close: float


def read_dat(path: Path) -> list[Bar]:
    data = path.read_bytes()
    payload_size = len(data) - HEADER_SIZE
    if payload_size < 0 or payload_size % RECORD.size:
        raise ValueError(
            f"Unexpected DAT size {len(data)}: expected 8-byte header plus "
            f"{RECORD.size}-byte records"
        )

    bars = []
    for values in RECORD.iter_unpack(data[HEADER_SIZE:]):
        (
            timestamp,
            open_price,
            high_price,
            low_price,
            close_price,
            _reserved_1,
            volume_lots,
            _reserved_2,
            amount,
            _reserved_3,
            _reserved_4,
            factor,
            previous_close,
            _reserved_5,
            _flags,
        ) = values

        bars.append(
            Bar(
                time=datetime.fromtimestamp(timestamp, CHINA_TZ),
                open=open_price / 1000,
                high=high_price / 1000,
                low=low_price / 1000,
                close=close_price / 1000,
                volume_lots=volume_lots,
                volume_shares=volume_lots * 100,
                amount=amount,
                factor=factor,
                previous_close=previous_close / 1000,
            )
        )
    return bars


def parse_boundary(value: str, end: bool = False) -> datetime:
    formats = ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if end and fmt in ("%Y%m%d", "%Y-%m-%d"):
                parsed = datetime.combine(parsed.date(), time.max)
            return parsed.replace(tzinfo=CHINA_TZ)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"Invalid time {value!r}; use YYYYMMDD, YYYY-MM-DD, or YYYYMMDDhhmmss"
    )


def select_bars(
    bars: Iterable[Bar], start: datetime | None, end: datetime | None
) -> list[Bar]:
    return [
        bar
        for bar in bars
        if (start is None or bar.time >= start) and (end is None or bar.time <= end)
    ]


def adjust_prices(bars: list[Bar], mode: str) -> list[Bar]:
    """Apply ratio-based adjustment while leaving volume and amount unchanged."""
    if mode == "none" or not bars:
        return bars

    reference_factor = bars[-1].factor if mode == "front-ratio" else bars[0].factor
    if reference_factor == 0:
        raise ValueError("Cannot adjust prices: reference factor is zero")

    result = []
    for bar in bars:
        ratio = bar.factor / reference_factor
        result.append(
            replace(
                bar,
                open=bar.open * ratio,
                high=bar.high * ratio,
                low=bar.low * ratio,
                close=bar.close * ratio,
                previous_close=bar.previous_close * ratio,
            )
        )
    return result


def write_csv(path: Path, bars: Iterable[Bar]) -> None:
    rows = list(bars)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for bar in rows:
            row = asdict(bar)
            row["time"] = bar.time.strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(row)


def print_bars(bars: list[Bar]) -> None:
    print(
        f"{'time':19} {'open':>8} {'high':>8} {'low':>8} {'close':>8} "
        f"{'lots':>10} {'amount':>14}"
    )
    for bar in bars:
        print(
            f"{bar.time:%Y-%m-%d %H:%M:%S} {bar.open:8.3f} {bar.high:8.3f} "
            f"{bar.low:8.3f} {bar.close:8.3f} {bar.volume_lots:10d} "
            f"{bar.amount:14d}"
        )


def infer_symbol(path: Path) -> str:
    code = path.stem
    exchange = path.parents[1].name if len(path.parents) >= 2 else ""
    return f"{code}.{exchange}" if exchange else code


def print_analysis(
    report,
    bars: list[Bar] | None = None,
    detail: str = "full",
    detail_rows: int = 20,
) -> None:
    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.4%}"

    def number(value: float | None) -> str:
        return "-" if value is None else f"{value:.6f}"

    def money(value: float | None) -> str:
        return "-" if value is None else f"{value:.3f}"

    print("\nDigital distribution analysis")
    print(f"Symbol: {report.symbol}")
    print(f"Range: {report.date_range}")
    print(f"Samples: {report.sample_count:,}")
    print(
        "Benford amount: "
        f"MAD={report.benford_amount.mad:.6f}, "
        f"KS={report.benford_amount.ks_statistic:.6f}, "
        f"chi_square={report.benford_amount.chi_square:.3f}"
    )
    print(
        "Benford volume: "
        f"MAD={report.benford_volume.mad:.6f}, "
        f"KS={report.benford_volume.ks_statistic:.6f}, "
        f"chi_square={report.benford_volume.chi_square:.3f}"
    )
    print(
        "Price tails: "
        f"concentration={pct(report.tail_concentration)}, "
        f"0/5={pct(report.tail_0_5_ratio)}, "
        f"x.00={pct(report.round_1_ratio)}, "
        f"x.10={pct(report.round_01_ratio)}, "
        f"x.05={pct(report.round_005_ratio)}"
    )
    print(
        "Close tail: "
        f"mean 14:30={pct(report.close_tail.mean_return_from_1430)}, "
        f"mean abs 14:30={pct(report.close_tail.mean_abs_return_from_1430)}, "
        f"mean 14:55={pct(report.close_tail.mean_return_from_1455)}, "
        f"mean abs 14:55={pct(report.close_tail.mean_abs_return_from_1455)}"
    )
    if detail == "summary":
        return

    print("\nBenford first-digit distribution")
    print(
        f"{'digit':>5} {'amount_n':>10} {'amount_obs':>12} {'amount_exp':>12} "
        f"{'amount_dev':>12} {'volume_n':>10} {'volume_obs':>12} "
        f"{'volume_exp':>12} {'volume_dev':>12}"
    )
    for digit in range(1, 10):
        print(
            f"{digit:>5d} "
            f"{report.benford_amount.counts[digit]:>10,d} "
            f"{pct(report.benford_amount.observed_ratio[digit]):>12} "
            f"{pct(report.benford_amount.expected_ratio[digit]):>12} "
            f"{pct(report.benford_amount.deviation[digit]):>12} "
            f"{report.benford_volume.counts[digit]:>10,d} "
            f"{pct(report.benford_volume.observed_ratio[digit]):>12} "
            f"{pct(report.benford_volume.expected_ratio[digit]):>12} "
            f"{pct(report.benford_volume.deviation[digit]):>12}"
        )

    print("\nPrice tail digit distribution")
    print(f"{'digit':>5} {'count':>10} {'ratio':>12}")
    for digit in range(10):
        print(
            f"{digit:>5d} "
            f"{report.price_tail.counts[digit]:>10,d} "
            f"{pct(report.price_tail.ratio[digit]):>12}"
        )

    offsets = report.close_tail.offsets
    if detail == "full" and offsets:
        shown_offsets = offsets if detail_rows == 0 else offsets[-detail_rows:]
        print("\nClose-tail daily offsets")
        if detail_rows and len(offsets) > detail_rows:
            print(f"(showing last {detail_rows} of {len(offsets):,} trading days)")
        print(
            f"{'date':10} {'close':>10} {'p1430':>10} {'p1455':>10} "
            f"{'ret1430':>12} {'ret1455':>12}"
        )
        for item in shown_offsets:
            print(
                f"{item.trade_date:10} "
                f"{money(item.close):>10} "
                f"{money(item.price_at_1430):>10} "
                f"{money(item.price_at_1455):>10} "
                f"{pct(item.close_return_from_1430):>12} "
                f"{pct(item.close_return_from_1455):>12}"
            )

    if detail == "full" and bars:
        daily_round = _daily_round_clustering(bars)
        shown_daily_round = daily_round if detail_rows == 0 else daily_round[-detail_rows:]
        print("\nDaily round-price clustering")
        if detail_rows and len(daily_round) > detail_rows:
            print(f"(showing last {detail_rows} of {len(daily_round):,} trading days)")
        print(
            f"{'date':10} {'samples':>8} {'x.00':>12} {'x.10':>12} {'x.05':>12}"
        )
        for row in shown_daily_round:
            print(
                f"{row['date']:10} "
                f"{row['sample_count']:>8,d} "
                f"{pct(row['round_1_ratio']):>12} "
                f"{pct(row['round_01_ratio']):>12} "
                f"{pct(row['round_005_ratio']):>12}"
            )

    print("\nScores")
    print(f"{'metric':34} {'value':>12}")
    print(f"{'benford_amount_score':34} {number(report.benford_amount_score):>12}")
    print(f"{'benford_volume_score':34} {number(report.benford_volume_score):>12}")
    print(f"{'tail_concentration':34} {pct(report.tail_concentration):>12}")
    print(f"{'tail_0_5_ratio':34} {pct(report.tail_0_5_ratio):>12}")
    print(f"{'round_1_ratio':34} {pct(report.round_1_ratio):>12}")
    print(f"{'round_01_ratio':34} {pct(report.round_01_ratio):>12}")
    print(f"{'round_005_ratio':34} {pct(report.round_005_ratio):>12}")
    print(
        f"{'close_tail_anomaly_score':34} "
        f"{pct(report.close_tail_anomaly_score):>12}"
    )


def _daily_round_clustering(bars: Iterable[Bar]) -> list[dict[str, float | int | str]]:
    from stock_digital_analysis.digital_distribution import price_to_cents

    grouped: dict[str, dict[str, int]] = {}
    for bar in bars:
        if bar.close <= 0:
            continue
        trade_date = bar.time.date().isoformat()
        row = grouped.setdefault(
            trade_date,
            {"sample_count": 0, "round_1": 0, "round_01": 0, "round_005": 0},
        )
        cents = price_to_cents(bar.close)
        row["sample_count"] += 1
        row["round_1"] += int(cents % 100 == 0)
        row["round_01"] += int(cents % 10 == 0)
        row["round_005"] += int(cents % 5 == 0)

    rows = []
    for trade_date, row in sorted(grouped.items()):
        sample_count = row["sample_count"]
        rows.append(
            {
                "date": trade_date,
                "sample_count": sample_count,
                "round_1_ratio": row["round_1"] / sample_count if sample_count else 0.0,
                "round_01_ratio": row["round_01"] / sample_count if sample_count else 0.0,
                "round_005_ratio": row["round_005"] / sample_count if sample_count else 0.0,
            }
        )
    return rows


def main() -> int:
    default_file = Path("datadir/SH/60/601328.DAT")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=default_file)
    parser.add_argument("--start", help="Start date/time")
    parser.add_argument("--end", help="End date/time")
    parser.add_argument(
        "--tail", type=int, default=10, help="Rows to print (default: 10)"
    )
    parser.add_argument("--csv", type=Path, help="Export all selected rows to CSV")
    parser.add_argument(
        "--adjust",
        choices=("none", "front-ratio", "back-ratio"),
        default="none",
        help="Ratio price adjustment using the factor stored in the DAT file",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run digital distribution anomaly metrics on selected minute bars",
    )
    parser.add_argument(
        "--analysis-detail",
        choices=("summary", "full"),
        default="full",
        help="Amount of analysis detail to print (default: full)",
    )
    parser.add_argument(
        "--detail-rows",
        type=int,
        default=20,
        help="Daily close-tail rows to print; use 0 for all rows (default: 20)",
    )
    parser.add_argument(
        "--analysis-json",
        type=Path,
        help="Write digital distribution metrics to JSON",
    )
    parser.add_argument("--symbol", help="Symbol shown in analysis output")
    args = parser.parse_args()

    if args.tail < 0:
        parser.error("--tail cannot be negative")
    if args.detail_rows < 0:
        parser.error("--detail-rows cannot be negative")

    start = parse_boundary(args.start) if args.start else None
    end = parse_boundary(args.end, end=True) if args.end else None
    # Adjust before filtering so the reference factor is stable for every query range.
    all_bars = adjust_prices(read_dat(args.file), args.adjust)
    bars = select_bars(all_bars, start, end)
    if not bars:
        print("No matching records.")
        return 1

    print(f"File: {args.file.resolve()}")
    print(f"Adjustment: {args.adjust}")
    print(f"Records: {len(bars):,}, range: {bars[0].time} -> {bars[-1].time}")
    if args.tail:
        print_bars(bars[-args.tail :])

    if args.csv:
        write_csv(args.csv, bars)
        print(f"CSV: {args.csv.resolve()}")

    if args.analyze or args.analysis_json:
        from stock_digital_analysis.digital_distribution import analyze_bars

        report = analyze_bars(args.symbol or infer_symbol(args.file), bars)
        if args.analyze:
            print_analysis(
                report,
                bars=bars,
                detail=args.analysis_detail,
                detail_rows=args.detail_rows,
            )
        if args.analysis_json:
            args.analysis_json.parent.mkdir(parents=True, exist_ok=True)
            args.analysis_json.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Analysis JSON: {args.analysis_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
