"""Digital distribution experiments for A-share minute bars."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence


class MarketBar(Protocol):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume_lots: int
    volume_shares: int
    amount: int


BENFORD_DIGITS = tuple(range(1, 10))
TAIL_DIGITS = tuple(range(10))


def reload_digital_distribution_module():
    """Reload this module from a notebook and return the fresh module object.

    In Jupyter, prefer:

    ```python
    from stock_digital_analysis.digital_distribution import reload_digital_distribution_module
    dd = reload_digital_distribution_module()
    selector = dd.create_stock_symbol_selector("datadir")
    ```

    Calling through `dd` avoids stale functions imported earlier with
    `from stock_digital_analysis.digital_distribution import ...`.
    """
    import importlib
    import sys

    module_name = __name__
    module = sys.modules[module_name]
    return importlib.reload(module)


@dataclass(frozen=True)
class BenfordResult:
    sample_count: int
    counts: dict[int, int]
    observed_ratio: dict[int, float]
    expected_ratio: dict[int, float]
    deviation: dict[int, float]
    chi_square: float
    mad: float
    ks_statistic: float


@dataclass(frozen=True)
class TailResult:
    sample_count: int
    counts: dict[int, int]
    ratio: dict[int, float]
    tail_concentration: float
    tail_0_5_ratio: float
    round_1_ratio: float
    round_01_ratio: float
    round_005_ratio: float


@dataclass(frozen=True)
class CloseTailOffset:
    trade_date: str
    close: float
    price_at_1430: float | None
    price_at_1455: float | None
    close_return_from_1430: float | None
    close_return_from_1455: float | None


@dataclass(frozen=True)
class CloseTailResult:
    sample_count: int
    mean_return_from_1430: float | None
    mean_abs_return_from_1430: float | None
    mean_return_from_1455: float | None
    mean_abs_return_from_1455: float | None
    offsets: list[CloseTailOffset]


@dataclass(frozen=True)
class DigitalDistributionReport:
    symbol: str
    date_range: str
    sample_count: int
    benford_amount: BenfordResult
    benford_volume: BenfordResult
    price_tail: TailResult
    close_tail: CloseTailResult
    benford_amount_score: float
    benford_volume_score: float
    tail_concentration: float
    tail_0_5_ratio: float
    round_1_ratio: float
    round_01_ratio: float
    round_005_ratio: float
    close_tail_anomaly_score: float | None
    peer_z_score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def first_significant_digit(value: float | int) -> int | None:
    number = abs(float(value))
    if not math.isfinite(number) or number <= 0:
        return None
    while number < 1:
        number *= 10
    while number >= 10:
        number /= 10
    digit = int(number)
    return digit if 1 <= digit <= 9 else None


def benford_expected_distribution() -> dict[int, float]:
    return {digit: math.log10(1 + 1 / digit) for digit in BENFORD_DIGITS}


def benford_test(values: Iterable[float | int]) -> BenfordResult:
    digits = [digit for value in values if (digit := first_significant_digit(value))]
    counts = Counter(digits)
    total = len(digits)
    expected = benford_expected_distribution()
    observed = {
        digit: (counts.get(digit, 0) / total if total else 0.0)
        for digit in BENFORD_DIGITS
    }
    deviation = {digit: observed[digit] - expected[digit] for digit in BENFORD_DIGITS}
    chi_square = 0.0
    if total:
        for digit in BENFORD_DIGITS:
            expected_count = expected[digit] * total
            if expected_count:
                chi_square += (counts.get(digit, 0) - expected_count) ** 2 / expected_count
    mad = (
        sum(abs(deviation[digit]) for digit in BENFORD_DIGITS) / len(BENFORD_DIGITS)
        if total
        else 0.0
    )
    ks = _ks_statistic(observed, expected)
    return BenfordResult(
        sample_count=total,
        counts={digit: counts.get(digit, 0) for digit in BENFORD_DIGITS},
        observed_ratio=observed,
        expected_ratio=expected,
        deviation=deviation,
        chi_square=chi_square,
        mad=mad,
        ks_statistic=ks,
    )


def _ks_statistic(observed: dict[int, float], expected: dict[int, float]) -> float:
    observed_cum = 0.0
    expected_cum = 0.0
    max_gap = 0.0
    for digit in BENFORD_DIGITS:
        observed_cum += observed[digit]
        expected_cum += expected[digit]
        max_gap = max(max_gap, abs(observed_cum - expected_cum))
    return max_gap


def price_to_cents(price: float) -> int:
    decimal_price = Decimal(str(price)) * Decimal("100")
    return int(decimal_price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def price_tail_test(prices: Iterable[float]) -> TailResult:
    cents_values = [price_to_cents(price) for price in prices if price and price > 0]
    counts = Counter(cents % 10 for cents in cents_values)
    total = len(cents_values)
    ratios = {
        digit: (counts.get(digit, 0) / total if total else 0.0)
        for digit in TAIL_DIGITS
    }
    return TailResult(
        sample_count=total,
        counts={digit: counts.get(digit, 0) for digit in TAIL_DIGITS},
        ratio=ratios,
        tail_concentration=max(ratios.values()) if ratios else 0.0,
        tail_0_5_ratio=ratios[0] + ratios[5],
        round_1_ratio=_divisible_ratio(cents_values, 100),
        round_01_ratio=_divisible_ratio(cents_values, 10),
        round_005_ratio=_divisible_ratio(cents_values, 5),
    )


def _divisible_ratio(values: list[int], divisor: int) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value % divisor == 0) / len(values)


def close_tail_offsets(bars: Iterable[MarketBar]) -> CloseTailResult:
    by_day: dict[date, list[MarketBar]] = defaultdict(list)
    for bar in bars:
        bar_time = getattr(bar, "time")
        by_day[bar_time.date()].append(bar)

    offsets = []
    for trade_date, day_bars in sorted(by_day.items()):
        ordered = sorted(day_bars, key=lambda item: getattr(item, "time"))
        if not ordered:
            continue
        close_bar = ordered[-1]
        price_1430 = _first_close_at_or_after(ordered, time(14, 30))
        price_1455 = _first_close_at_or_after(ordered, time(14, 55))
        offsets.append(
            CloseTailOffset(
                trade_date=trade_date.isoformat(),
                close=float(close_bar.close),
                price_at_1430=price_1430,
                price_at_1455=price_1455,
                close_return_from_1430=_return(close_bar.close, price_1430),
                close_return_from_1455=_return(close_bar.close, price_1455),
            )
        )

    returns_1430 = [
        item.close_return_from_1430
        for item in offsets
        if item.close_return_from_1430 is not None
    ]
    returns_1455 = [
        item.close_return_from_1455
        for item in offsets
        if item.close_return_from_1455 is not None
    ]
    return CloseTailResult(
        sample_count=len(offsets),
        mean_return_from_1430=_mean(returns_1430),
        mean_abs_return_from_1430=_mean([abs(value) for value in returns_1430]),
        mean_return_from_1455=_mean(returns_1455),
        mean_abs_return_from_1455=_mean([abs(value) for value in returns_1455]),
        offsets=offsets,
    )


def _first_close_at_or_after(bars: list[MarketBar], boundary: time) -> float | None:
    for bar in bars:
        if getattr(bar, "time").time() >= boundary:
            return float(bar.close)
    return None


def _return(close: float, base: float | None) -> float | None:
    if base is None or base == 0:
        return None
    return float(close) / base - 1


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def analyze_bars(symbol: str, bars: Iterable[MarketBar]) -> DigitalDistributionReport:
    rows = sorted(list(bars), key=lambda item: getattr(item, "time"))
    amount_result = benford_test(bar.amount for bar in rows)
    volume_result = benford_test(bar.volume_shares for bar in rows)
    tail_result = price_tail_test(bar.close for bar in rows)
    close_result = close_tail_offsets(rows)
    date_range = (
        f"{rows[0].time:%Y-%m-%d %H:%M:%S} -> {rows[-1].time:%Y-%m-%d %H:%M:%S}"
        if rows
        else ""
    )
    return DigitalDistributionReport(
        symbol=symbol,
        date_range=date_range,
        sample_count=len(rows),
        benford_amount=amount_result,
        benford_volume=volume_result,
        price_tail=tail_result,
        close_tail=close_result,
        benford_amount_score=amount_result.mad,
        benford_volume_score=volume_result.mad,
        tail_concentration=tail_result.tail_concentration,
        tail_0_5_ratio=tail_result.tail_0_5_ratio,
        round_1_ratio=tail_result.round_1_ratio,
        round_01_ratio=tail_result.round_01_ratio,
        round_005_ratio=tail_result.round_005_ratio,
        close_tail_anomaly_score=close_result.mean_abs_return_from_1430,
    )


def load_stock_minute_bars(
    file_path: str | Path,
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
) -> list[MarketBar]:
    """Load stock 60-second DAT bars with the same options as read_data.py.

    This is intentionally a thin Colab-friendly wrapper around read_data.py.
    Importing happens inside the function so the metric helpers can still be
    used with arbitrary in-memory bars or DataFrames.
    """
    from stock_digital_analysis.read_data import adjust_prices, parse_boundary, read_dat, select_bars

    start_dt = parse_boundary(start) if start else None
    end_dt = parse_boundary(end, end=True) if end else None
    bars = adjust_prices(read_dat(Path(file_path)), adjust)
    return select_bars(bars, start_dt, end_dt)


def infer_stock_symbol(file_path: str | Path) -> str:
    """Infer `601328.SH` from a DAT path like `datadir/SH/60/601328.DAT`."""
    path = Path(file_path)
    code = path.stem
    exchange = path.parents[1].name if len(path.parents) >= 2 else ""
    return f"{code}.{exchange}" if exchange else code


def _split_stock_symbol(symbol: str) -> tuple[str, str | None]:
    normalized = symbol.strip().upper()
    if "." in normalized:
        code, exchange = normalized.rsplit(".", 1)
        return code, exchange
    if len(normalized) >= 3 and normalized[:2] in {"SH", "SZ"}:
        return normalized[2:], normalized[:2]
    return normalized, None


def find_stock_dat_file(data_dir: str | Path, symbol: str) -> Path:
    """Find the local local stock minute DAT file for a symbol.

    Supported symbol inputs include `601328.SH`, `SH601328`, and `601328`.
    When no exchange is supplied, the first matching `*/60/<code>.DAT` file is
    returned in sorted path order.
    """
    root = Path(data_dir)
    code, exchange = _split_stock_symbol(symbol)
    if not code:
        raise ValueError("symbol cannot be empty")

    if exchange:
        candidate = root / exchange / "60" / f"{code}.DAT"
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"No stock DAT file found for {symbol}: {candidate}")

    matches = sorted(root.glob(f"*/60/{code}.DAT"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No stock DAT file found for {symbol} under {root}")


def stock_symbol_to_quotation_code(symbol: str) -> str:
    """Convert `588000.SH` or `SH588000` to an easyquotation/Sina code."""
    code, exchange = _split_stock_symbol(symbol)
    if not exchange:
        raise ValueError(f"Cannot infer exchange from symbol: {symbol}")
    if exchange not in {"SH", "SZ"}:
        raise ValueError(f"Unsupported exchange in symbol: {symbol}")
    return f"{exchange.lower()}{code}"


def resolve_stock_names(symbols: Iterable[str]) -> dict[str, str]:
    """Resolve stock symbols to Chinese security names.

    The lookup follows the YHTrader convention first: Redis hash
    `stock_map`, keyed by easyquotation codes such as `sh601328`. Missing
    names fall back to easyquotation/Sina and are written back to Redis when
    available. Failed lookups are omitted from the returned map.
    """
    unique_symbols = sorted({symbol for symbol in symbols if symbol})
    quotation_to_symbol = {}
    for symbol in unique_symbols:
        try:
            quotation_to_symbol[stock_symbol_to_quotation_code(symbol)] = symbol
        except ValueError:
            continue
    if not quotation_to_symbol:
        return {}

    names = _resolve_stock_names_from_redis(quotation_to_symbol)
    missing_quotation_codes = [
        quotation_code
        for quotation_code, symbol in quotation_to_symbol.items()
        if symbol not in names
    ]
    if not missing_quotation_codes:
        return names

    sina_names = _resolve_stock_names_from_sina(missing_quotation_codes, quotation_to_symbol)
    if sina_names:
        names.update(sina_names)
        _write_stock_names_to_redis(
            {
                quotation_code: names[symbol]
                for quotation_code, symbol in quotation_to_symbol.items()
                if symbol in sina_names
            }
        )
    missing_quotation_codes = [
        quotation_code
        for quotation_code, symbol in quotation_to_symbol.items()
        if symbol not in names
    ]
    if not missing_quotation_codes:
        return names

    try:
        import easyquotation

        quotation = easyquotation.use("sina")
        snapshot = quotation.real(missing_quotation_codes, prefix=True)
    except Exception:
        return names

    redis_updates = {}
    for quotation_code, item in snapshot.items():
        symbol = quotation_to_symbol.get(quotation_code)
        name = item.get("name") if isinstance(item, dict) else None
        if symbol and name:
            stock_name = str(name)
            names[symbol] = stock_name
            redis_updates[quotation_code] = stock_name
    _write_stock_names_to_redis(redis_updates)
    return names


def _resolve_stock_names_from_sina(
    quotation_codes: Sequence[str],
    quotation_to_symbol: dict[str, str],
) -> dict[str, str]:
    """Resolve names through Sina's public quote endpoint."""
    codes = [code for code in quotation_codes if re.fullmatch(r"(sh|sz)\d{6}", code)]
    if not codes:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    request = urllib.request.Request(
        url,
        headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            text = response.read().decode("gb18030", errors="replace")
    except Exception:
        return {}

    names = {}
    pattern = re.compile(r'var hq_str_((?:sh|sz)\d{6})="([^",]+)')
    for quotation_code, stock_name in pattern.findall(text):
        symbol = quotation_to_symbol.get(quotation_code)
        if symbol and stock_name:
            names[symbol] = stock_name
    return names


def _stock_name_redis_client():
    try:
        import redis
    except Exception:
        return None

    redis_url = os.environ.get("REDIS_URL", "redis://192.168.1.1:6379/0")
    try:
        client = redis.StrictRedis.from_url(
            redis_url,
            encoding="utf8",
            decode_responses=True,
            socket_connect_timeout=0.3,
            socket_timeout=0.5,
        )
        client.ping()
        return client
    except Exception:
        return None


def _resolve_stock_names_from_redis(
    quotation_to_symbol: dict[str, str],
    hash_name: str = "stock_map",
) -> dict[str, str]:
    client = _stock_name_redis_client()
    if client is None:
        return {}
    names = {}
    try:
        values = client.hmget(hash_name, list(quotation_to_symbol))
    except Exception:
        return {}
    for quotation_code, stock_name in zip(quotation_to_symbol, values):
        if stock_name:
            names[quotation_to_symbol[quotation_code]] = str(stock_name)
    return names


def _write_stock_names_to_redis(
    quotation_names: dict[str, str],
    hash_name: str = "stock_map",
) -> None:
    if not quotation_names:
        return
    client = _stock_name_redis_client()
    if client is None:
        return
    try:
        client.hset(hash_name, mapping=quotation_names)
    except Exception:
        return


def attach_stock_names(
    rows: Iterable[dict[str, Any]],
    stock_names: dict[str, str] | None = None,
    resolve_names: bool = True,
) -> list[dict[str, Any]]:
    """Return scan rows with `stock_name` and `display_name` fields."""
    output = [dict(row) for row in rows]
    names = dict(stock_names or {})
    if resolve_names:
        missing_symbols = [
            row["symbol"]
            for row in output
            if row.get("symbol") and row.get("symbol") not in names
        ]
        names.update(resolve_stock_names(missing_symbols))

    for row in output:
        symbol = row.get("symbol", "")
        stock_name = names.get(symbol, "")
        row["stock_name"] = stock_name
        row["display_name"] = f"{stock_name} ({symbol})" if stock_name else symbol
    return output


def load_stock_names_file(path: str | Path) -> dict[str, str]:
    """Load a local stock-name mapping file.

    The JSON can use stock symbols (`601328.SH`, `SH601328`) or YHTrader /
    easyquotation keys (`sh601328`). Values are Chinese security names.
    """
    mapping_path = Path(path)
    if not mapping_path.exists():
        return {}
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Stock-name mapping must be a JSON object: {mapping_path}")
    names = {}
    for key, value in data.items():
        if not value:
            continue
        symbol = _stock_name_key_to_stock_symbol(str(key))
        if symbol:
            names[symbol] = str(value)
    return names


def load_default_stock_names(data_dir: str | Path) -> dict[str, str]:
    """Load local stock-name mappings from common project/data locations."""
    candidates = [
        Path(data_dir) / "stock_names.json",
        Path("stock_names.json"),
        Path("datadir") / "stock_names.json",
    ]
    names = {}
    seen_paths = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            names.update(load_stock_names_file(candidate))
        except FileNotFoundError:
            continue
    return names


def _stock_name_key_to_stock_symbol(key: str) -> str | None:
    normalized = key.strip().upper()
    if not normalized:
        return None
    if "." in normalized:
        code, exchange = _split_stock_symbol(normalized)
        return f"{code}.{exchange}" if exchange else code
    if len(normalized) >= 3 and normalized[:2] in {"SH", "SZ"}:
        return f"{normalized[2:]}.{normalized[:2]}"
    if len(normalized) >= 3 and normalized[-2:] in {"SH", "SZ"}:
        return f"{normalized[:-2]}.{normalized[-2:]}"
    return normalized


def bars_to_records(
    bars: Iterable[MarketBar],
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Convert bars to plain dictionaries suitable for pandas or JSON."""
    records = []
    for bar in bars:
        close = float(getattr(bar, "close"))
        cents = price_to_cents(close) if close > 0 else None
        records.append(
            {
                "symbol": symbol,
                "datetime": getattr(bar, "time"),
                "date": getattr(bar, "time").date(),
                "time": getattr(bar, "time").time(),
                "open": float(getattr(bar, "open", close)),
                "high": float(getattr(bar, "high", close)),
                "low": float(getattr(bar, "low", close)),
                "close": close,
                "volume_lots": int(getattr(bar, "volume_lots", 0)),
                "volume": int(getattr(bar, "volume_shares", 0)),
                "amount": int(getattr(bar, "amount", 0)),
                "tail_digit": cents % 10 if cents is not None else None,
                "is_round_1": cents % 100 == 0 if cents is not None else False,
                "is_round_01": cents % 10 == 0 if cents is not None else False,
                "is_round_005": cents % 5 == 0 if cents is not None else False,
            }
        )
    return records


def bars_to_dataframe(
    bars: Iterable[MarketBar],
    symbol: str | None = None,
):
    """Convert bars to a pandas.DataFrame.

    pandas is imported lazily so the core metric functions do not require it.
    """
    import pandas as pd

    return pd.DataFrame(bars_to_records(bars, symbol=symbol))


def report_to_summary(report: DigitalDistributionReport) -> dict[str, Any]:
    """Flatten a report to the issue #269 single-stock output shape."""
    return {
        "symbol": report.symbol,
        "date_range": report.date_range,
        "sample_count": report.sample_count,
        "benford_amount_score": report.benford_amount_score,
        "benford_amount_chi_square": report.benford_amount.chi_square,
        "benford_amount_ks": report.benford_amount.ks_statistic,
        "benford_volume_score": report.benford_volume_score,
        "benford_volume_chi_square": report.benford_volume.chi_square,
        "benford_volume_ks": report.benford_volume.ks_statistic,
        "tail_concentration": report.tail_concentration,
        "tail_0_5_ratio": report.tail_0_5_ratio,
        "round_1_ratio": report.round_1_ratio,
        "round_01_ratio": report.round_01_ratio,
        "round_005_ratio": report.round_005_ratio,
        "close_tail_anomaly_score": report.close_tail_anomaly_score,
        "close_tail_mean_return_from_1430": report.close_tail.mean_return_from_1430,
        "close_tail_mean_return_from_1455": report.close_tail.mean_return_from_1455,
        "close_tail_mean_abs_return_from_1455": report.close_tail.mean_abs_return_from_1455,
        "peer_z_score": report.peer_z_score,
    }


def report_to_summary_dataframe(report: DigitalDistributionReport):
    import pandas as pd

    return pd.DataFrame([report_to_summary(report)])


def benford_distribution_records(
    result: BenfordResult,
    metric_name: str,
) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric_name,
            "digit": digit,
            "count": result.counts[digit],
            "observed_ratio": result.observed_ratio[digit],
            "expected_ratio": result.expected_ratio[digit],
            "deviation": result.deviation[digit],
        }
        for digit in BENFORD_DIGITS
    ]


def benford_distribution_dataframe(
    result: BenfordResult,
    metric_name: str,
):
    import pandas as pd

    return pd.DataFrame(benford_distribution_records(result, metric_name))


def tail_distribution_records(result: TailResult) -> list[dict[str, Any]]:
    return [
        {
            "digit": digit,
            "count": result.counts[digit],
            "ratio": result.ratio[digit],
        }
        for digit in TAIL_DIGITS
    ]


def tail_distribution_dataframe(result: TailResult):
    import pandas as pd

    return pd.DataFrame(tail_distribution_records(result))


def close_tail_dataframe(report: DigitalDistributionReport):
    import pandas as pd

    return pd.DataFrame(asdict(item) for item in report.close_tail.offsets)


def analyze_stock_dat(
    file_path: str | Path,
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
    symbol: str | None = None,
) -> DigitalDistributionReport:
    """Load one stock DAT file and return a DigitalDistributionReport."""
    bars = load_stock_minute_bars(file_path, start=start, end=end, adjust=adjust)
    return analyze_bars(symbol or infer_stock_symbol(file_path), bars)


def analyze_stock_symbol(
    data_dir: str | Path,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
) -> DigitalDistributionReport:
    """Analyze one symbol from a local stock data directory."""
    file_path = find_stock_dat_file(data_dir, symbol)
    return analyze_stock_dat(
        file_path,
        start=start,
        end=end,
        adjust=adjust,
        symbol=infer_stock_symbol(file_path),
    )


def scan_stock_dat_files(
    file_paths: Iterable[str | Path],
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
    min_samples: int = 1,
) -> list[dict[str, Any]]:
    """Analyze multiple DAT files and return flattened rows.

    Failed files are returned as rows with an `error` field so long scans can
    continue inside notebooks.
    """
    rows = []
    for file_path in file_paths:
        path = Path(file_path)
        symbol = infer_stock_symbol(path)
        try:
            report = analyze_stock_dat(
                path,
                start=start,
                end=end,
                adjust=adjust,
                symbol=symbol,
            )
            if report.sample_count < min_samples:
                continue
            row = report_to_summary(report)
            row["file_path"] = str(path)
            rows.append(row)
        except Exception as exc:
            rows.append({"symbol": symbol, "file_path": str(path), "error": str(exc)})
    return rows


DEFAULT_RANK_METRICS = (
    "benford_amount_score",
    "benford_volume_score",
    "tail_concentration",
    "tail_0_5_ratio",
    "round_1_ratio",
    "round_01_ratio",
    "round_005_ratio",
    "close_tail_anomaly_score",
)


def rank_scan_results(
    rows: Sequence[dict[str, Any]],
    metric_cols: Sequence[str] = DEFAULT_RANK_METRICS,
    score_col: str = "overall_score",
):
    """Build a pandas ranking DataFrame with z-scores and an overall score."""
    import pandas as pd

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    z_cols = []
    for col in metric_cols:
        if col not in df:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        std = numeric.std(ddof=0)
        z_col = f"{col}_z"
        df[z_col] = (numeric - numeric.mean()) / std if std and not pd.isna(std) else 0.0
        z_cols.append(z_col)

    if z_cols:
        df[score_col] = df[z_cols].abs().mean(axis=1)
        return df.sort_values(score_col, ascending=False)
    df[score_col] = 0.0
    return df


def scan_stock_dat_dir(
    data_dir: str | Path,
    pattern: str = "S[HZ]/60/*.DAT",
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
    min_samples: int = 1,
    include_stock_names: bool = False,
    stock_names: dict[str, str] | None = None,
    resolve_names: bool = True,
):
    """Scan a local stock data directory and return a ranked DataFrame."""
    paths = sorted(Path(data_dir).glob(pattern))
    rows = scan_stock_dat_files(
        paths,
        start=start,
        end=end,
        adjust=adjust,
        min_samples=min_samples,
    )
    if include_stock_names:
        rows = attach_stock_names(rows, stock_names=stock_names, resolve_names=resolve_names)
    return rank_scan_results(rows)


def create_benford_figure(result: BenfordResult, title: str):
    import plotly.graph_objects as go

    digits = list(BENFORD_DIGITS)
    fig = go.Figure()
    fig.add_bar(
        name="observed",
        x=digits,
        y=[result.observed_ratio[digit] for digit in digits],
    )
    fig.add_bar(
        name="expected",
        x=digits,
        y=[result.expected_ratio[digit] for digit in digits],
    )
    fig.update_layout(
        title=title,
        barmode="group",
        xaxis_title="First digit",
        yaxis_title="Ratio",
    )
    return fig


def create_tail_figure(result: TailResult, title: str = "价格尾数分布"):
    import plotly.graph_objects as go

    digits = list(TAIL_DIGITS)
    fig = go.Figure()
    fig.add_bar(x=digits, y=[result.ratio[digit] for digit in digits])
    fig.update_layout(
        title=title,
        xaxis_title="last_digit(price * 100)",
        yaxis_title="Ratio",
    )
    return fig


def create_round_clustering_figure(
    bars: Iterable[MarketBar],
    title: str = "整数价位聚集度时间序列",
):
    import plotly.graph_objects as go

    df = bars_to_dataframe(bars)
    if df.empty:
        return go.Figure()
    daily_round = (
        df.groupby("date")
        .agg(
            sample_count=("close", "size"),
            round_1_ratio=("is_round_1", "mean"),
            round_01_ratio=("is_round_01", "mean"),
            round_005_ratio=("is_round_005", "mean"),
        )
        .reset_index()
    )
    fig = go.Figure()
    for col in ["round_1_ratio", "round_01_ratio", "round_005_ratio"]:
        fig.add_scatter(x=daily_round["date"], y=daily_round[col], mode="lines", name=col)
    fig.update_layout(title=title, xaxis_title="Date", yaxis_title="Ratio")
    return fig


def create_close_tail_figure(report: DigitalDistributionReport, title: str = "尾盘价格偏移"):
    import plotly.graph_objects as go

    df = close_tail_dataframe(report)
    fig = go.Figure()
    if df.empty:
        return fig
    fig.add_scatter(
        x=df["trade_date"],
        y=df["close_return_from_1430"],
        mode="lines+markers",
        name="close / 14:30 - 1",
    )
    fig.add_scatter(
        x=df["trade_date"],
        y=df["close_return_from_1455"],
        mode="lines+markers",
        name="close / 14:55 - 1",
    )
    fig.update_layout(title=title, xaxis_title="Date", yaxis_title="Return")
    return fig


def create_ranking_figure(
    ranking_df,
    score_col: str = "overall_score",
    top_n: int = 20,
    title: str = "数字分布异常综合分数 Top 20",
):
    import plotly.graph_objects as go

    top = ranking_df.sort_values(score_col, ascending=False).head(top_n)
    fig = go.Figure()
    fig.add_bar(x=top["symbol"], y=top[score_col])
    fig.update_layout(title=title, xaxis_title="Symbol", yaxis_title="Mean abs z-score")
    return fig


def create_stock_scan_dashboard(
    ranking_df,
    score_col: str = "overall_score",
    top_n: int = 20,
    title: str = "股票数字分布异常检测总览",
):
    """Create one overview figure for a datadir scan across SH/SZ symbols."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    df = ranking_df.copy()
    if df.empty:
        return go.Figure()
    label_col = "display_name" if "display_name" in df.columns else "symbol"
    top = df.sort_values(score_col, ascending=False).head(top_n)
    metric_cols = [col for col in DEFAULT_RANK_METRICS if col in df.columns]

    fig = make_subplots(
        rows=2,
        cols=1,
        specs=[[{"type": "bar"}], [{"type": "table"}]],
        row_heights=[0.55, 0.45],
        vertical_spacing=0.15,
        subplot_titles=["异常综合分数排名", "核心指标明细"],
    )
    fig.add_bar(
        x=top[label_col],
        y=top[score_col],
        name=score_col,
        row=1,
        col=1,
    )

    table_cols = [
        col
        for col in ["display_name", "stock_name", "symbol", "sample_count", score_col, *metric_cols]
        if col in top.columns
    ]
    fig.add_trace(
        go.Table(
            header={"values": table_cols},
            cells={
                "values": [
                    [_format_dashboard_value(value) for value in top[col]]
                    for col in table_cols
                ]
            },
        ),
        row=2,
        col=1,
    )
    fig.update_layout(title=title, height=780, showlegend=False)
    fig.update_xaxes(tickangle=-30, row=1, col=1)
    return fig


def create_symbol_dashboard(
    report: DigitalDistributionReport,
    bars: Iterable[MarketBar] | None = None,
    title: str | None = None,
):
    """Create one Plotly figure showing the main metrics for a single symbol."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=3,
        cols=2,
        specs=[
            [{"type": "bar"}, {"type": "bar"}],
            [{"type": "bar"}, {"type": "scatter"}],
            [{"type": "scatter"}, {"type": "table"}],
        ],
        subplot_titles=[
            "成交额首位数字",
            "成交量首位数字",
            "价格尾数分布",
            "整数价位聚集度",
            "尾盘价格偏移",
            "指标摘要",
        ],
        vertical_spacing=0.12,
    )

    digits = list(BENFORD_DIGITS)
    for result, col in ((report.benford_amount, 1), (report.benford_volume, 2)):
        fig.add_bar(
            x=digits,
            y=[result.observed_ratio[digit] for digit in digits],
            name="observed",
            showlegend=col == 1,
            row=1,
            col=col,
        )
        fig.add_bar(
            x=digits,
            y=[result.expected_ratio[digit] for digit in digits],
            name="expected",
            showlegend=col == 1,
            row=1,
            col=col,
        )

    tail_digits = list(TAIL_DIGITS)
    fig.add_bar(
        x=tail_digits,
        y=[report.price_tail.ratio[digit] for digit in tail_digits],
        name="tail digit",
        showlegend=False,
        row=2,
        col=1,
    )

    if bars is not None:
        df = bars_to_dataframe(bars)
        if not df.empty:
            daily_round = (
                df.groupby("date")
                .agg(
                    round_1_ratio=("is_round_1", "mean"),
                    round_01_ratio=("is_round_01", "mean"),
                    round_005_ratio=("is_round_005", "mean"),
                )
                .reset_index()
            )
            for metric in ["round_1_ratio", "round_01_ratio", "round_005_ratio"]:
                fig.add_scatter(
                    x=daily_round["date"],
                    y=daily_round[metric],
                    mode="lines",
                    name=metric,
                    row=2,
                    col=2,
                )

    close_df = close_tail_dataframe(report)
    if not close_df.empty:
        fig.add_scatter(
            x=close_df["trade_date"],
            y=close_df["close_return_from_1430"],
            mode="lines+markers",
            name="close / 14:30 - 1",
            row=3,
            col=1,
        )
        fig.add_scatter(
            x=close_df["trade_date"],
            y=close_df["close_return_from_1455"],
            mode="lines+markers",
            name="close / 14:55 - 1",
            row=3,
            col=1,
        )

    summary = report_to_summary(report)
    metric_names = [
        "sample_count",
        "benford_amount_score",
        "benford_volume_score",
        "tail_concentration",
        "tail_0_5_ratio",
        "round_1_ratio",
        "round_01_ratio",
        "round_005_ratio",
        "close_tail_anomaly_score",
    ]
    fig.add_trace(
        go.Table(
            header={"values": ["metric", "value"]},
            cells={
                "values": [
                    metric_names,
                    [_format_dashboard_value(summary.get(name)) for name in metric_names],
                ]
            },
        ),
        row=3,
        col=2,
    )
    fig.update_layout(
        title=title or f"{report.symbol} 数字分布异常检测指标总览",
        barmode="group",
        height=950,
    )
    return fig


def _format_dashboard_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def create_stock_symbol_dashboard(
    data_dir: str | Path,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
):
    """Load one symbol from stock DAT files and create its multi-metric dashboard."""
    file_path = find_stock_dat_file(data_dir, symbol)
    bars = load_stock_minute_bars(file_path, start=start, end=end, adjust=adjust)
    report = analyze_bars(infer_stock_symbol(file_path), bars)
    return create_symbol_dashboard(report, bars=bars)


def create_stock_symbol_selector(
    data_dir: str | Path = "datadir",
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
    min_samples: int = 1,
    stock_names: dict[str, str] | None = None,
    resolve_names: bool = True,
    max_options: int = 50,
):
    """Create a Jupyter widget for filtering and viewing one symbol at a time.

    The search box matches both Chinese security names and symbols. In a
    notebook, call `display(widget)` on the returned object.
    """
    _enable_colab_widget_manager()
    try:
        import ipywidgets as widgets
        from IPython.display import clear_output, display
    except Exception as exc:  # pragma: no cover - depends on notebook extras
        raise RuntimeError(
            "create_stock_symbol_selector requires ipywidgets and IPython. "
            "Install notebook extras or run inside Jupyter."
        ) from exc

    ranking = scan_stock_dat_dir(
        data_dir,
        start=start,
        end=end,
        adjust=adjust,
        min_samples=min_samples,
        include_stock_names=True,
        stock_names=stock_names,
        resolve_names=resolve_names,
    )
    if ranking.empty:
        return widgets.HTML("<b>No matching stock DAT files found.</b>")

    rows = ranking.sort_values("display_name" if "display_name" in ranking else "symbol")
    records = rows.to_dict("records")
    symbols_by_label = {
        _symbol_selector_label(row): str(row["symbol"])
        for row in records
        if row.get("symbol") and not row.get("error")
    }

    search = widgets.Text(
        value="",
        placeholder="输入股票名称或代码过滤，例如 交通 / 601328 / ETF",
        description="筛选",
        layout=widgets.Layout(width="520px"),
    )
    dropdown = widgets.Dropdown(
        options=_filter_symbol_options(symbols_by_label, "", max_options),
        description="股票",
        layout=widgets.Layout(width="620px"),
    )
    refresh_button = widgets.Button(
        description="查看/刷新",
        button_style="primary",
        layout=widgets.Layout(width="120px"),
    )
    summary = widgets.Output()
    chart = widgets.Output()
    status = widgets.HTML("<span style='color:#52606d'>选择股票后点击查看/刷新</span>")

    def refresh_options(change=None):
        options = _filter_symbol_options(symbols_by_label, search.value, max_options)
        dropdown.options = options
        status.value = f"<span style='color:#52606d'>匹配 {len(options)} 个结果</span>"
        if options and dropdown.value not in dict(options).values():
            dropdown.value = options[0][1]

    def render_symbol(change=None):
        symbol = dropdown.value
        if not symbol:
            return
        status.value = f"<span style='color:#52606d'>正在加载 {symbol}...</span>"
        with summary:
            clear_output(wait=True)
            report = analyze_stock_symbol(
                data_dir,
                symbol,
                start=start,
                end=end,
                adjust=adjust,
            )
            display(report_to_summary_dataframe(report))
        with chart:
            clear_output(wait=True)
            file_path = find_stock_dat_file(data_dir, symbol)
            bars = load_stock_minute_bars(
                file_path,
                start=start,
                end=end,
                adjust=adjust,
            )
            label = next(
                (label for label, value in symbols_by_label.items() if value == symbol),
                symbol,
            )
            display(
                create_symbol_dashboard(
                    analyze_bars(symbol, bars),
                    bars=bars,
                    title=f"{label} 数字分布异常检测指标总览",
                )
            )
        status.value = f"<span style='color:#52606d'>已加载 {symbol}</span>"

    search.observe(refresh_options, names="value")
    dropdown.observe(render_symbol, names="value")
    refresh_button.on_click(render_symbol)
    refresh_options()
    return widgets.VBox(
        [
            widgets.HBox([search, status]),
            widgets.HBox([dropdown, refresh_button]),
            summary,
            chart,
        ]
    )


def display_stock_symbol_selector(*args, **kwargs):
    """Create and display the stock symbol selector in Jupyter/Colab."""
    _enable_colab_widget_manager()
    from IPython.display import display

    selector = create_stock_symbol_selector(*args, **kwargs)
    display(selector)
    return selector


def _enable_colab_widget_manager() -> None:
    """Enable ipywidgets in Google Colab when the runtime provides the hook."""
    try:
        from google.colab import output

        output.enable_custom_widget_manager()
    except Exception:
        pass


def _symbol_selector_label(row: dict[str, Any]) -> str:
    display_name = str(row.get("display_name") or row.get("symbol") or "")
    overall = row.get("overall_score")
    sample_count = row.get("sample_count")
    details = []
    if overall is not None:
        details.append(f"score={_format_dashboard_value(overall)}")
    if sample_count is not None:
        details.append(f"n={_format_dashboard_value(sample_count)}")
    return f"{display_name} | {', '.join(details)}" if details else display_name


def _filter_symbol_options(
    symbols_by_label: dict[str, str],
    query: str,
    max_options: int,
) -> list[tuple[str, str]]:
    normalized_query = query.strip().lower()
    options = []
    for label, symbol in symbols_by_label.items():
        haystack = f"{label} {symbol}".lower()
        if not normalized_query or normalized_query in haystack:
            options.append((label, symbol))
    return options[:max_options]


def write_stock_datadir_dashboard_html(
    output_path: str | Path,
    data_dir: str | Path,
    start: str | None = None,
    end: str | None = None,
    adjust: str = "none",
    min_samples: int = 1,
    include_symbol_dashboards: bool = True,
    stock_names: dict[str, str] | None = None,
    resolve_names: bool = True,
) -> Path:
    """Write an HTML report for all SH/SZ DAT files in a stock data directory."""
    from html import escape

    import plotly.io as pio

    output = Path(output_path)
    merged_stock_names = load_default_stock_names(data_dir)
    if stock_names:
        merged_stock_names.update(stock_names)
    ranking = scan_stock_dat_dir(
        data_dir,
        start=start,
        end=end,
        adjust=adjust,
        min_samples=min_samples,
        include_stock_names=True,
        stock_names=merged_stock_names,
        resolve_names=resolve_names,
    )
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>股票数字分布异常检测报告</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f7f8fa;color:#1f2933;}",
        ".layout{display:grid;grid-template-columns:minmax(220px,280px) minmax(0,1fr);gap:24px;max-width:1680px;margin:0 auto;padding:24px;}",
        ".sidebar{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow:auto;background:white;border:1px solid #d9e2ec;border-radius:8px;padding:14px;}",
        ".content{min-width:0;} h1{font-size:26px;margin:0 0 8px;} h2{font-size:20px;margin:0;}",
        ".meta{color:#52606d;margin-bottom:20px;} .panel{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:16px;margin-bottom:24px;}",
        ".toc-title{font-weight:700;margin-bottom:10px;} .toc{display:flex;flex-direction:column;gap:6px;font-size:13px;}",
        ".toc a{color:#334e68;text-decoration:none;line-height:1.35;} .toc a:hover{text-decoration:underline;}",
        "details.symbol{background:white;border:1px solid #d9e2ec;border-radius:8px;margin-bottom:14px;overflow:hidden;}",
        "details.symbol>summary{cursor:pointer;list-style:none;padding:13px 16px;display:flex;justify-content:space-between;gap:16px;align-items:center;border-bottom:1px solid transparent;}",
        "details.symbol>summary::-webkit-details-marker{display:none;} details.symbol[open]>summary{border-bottom-color:#d9e2ec;}",
        ".symbol-title{font-weight:700;} .symbol-meta{color:#52606d;font-size:13px;white-space:nowrap;} .symbol-body{padding:16px;}",
        "@media (max-width: 900px){.layout{grid-template-columns:1fr;padding:14px}.sidebar{position:relative;top:auto;max-height:260px}}",
        "</style>",
        "<script>",
        "document.addEventListener('toggle',function(e){if(e.target.matches('details.symbol[open]')&&window.Plotly){setTimeout(function(){e.target.querySelectorAll('.js-plotly-plot').forEach(function(p){Plotly.Plots.resize(p);});},80);}},true);",
        "</script>",
        "</head>",
        "<body>",
        '<div class="layout">',
        '<aside class="sidebar">',
        '<div class="toc-title">目录</div>',
        '<nav class="toc">',
        '<a href="#overview">总览</a>',
    ]

    symbol_rows = []
    if include_symbol_dashboards and not ranking.empty:
        ordered = ranking.sort_values("overall_score", ascending=False)
        seen_ids: set[str] = set()
        for _, row in ordered.iterrows():
            if row.get("error"):
                continue
            symbol = str(row["symbol"])
            display_name = str(row.get("display_name") or symbol)
            section_id = _html_anchor_id(symbol, seen_ids)
            symbol_rows.append((row, symbol, display_name, section_id))
            parts.append(f'<a href="#{section_id}">{escape(display_name)}</a>')

    parts.extend(
        [
            "</nav>",
            "</aside>",
            '<main class="content">',
        "<h1>股票数字分布异常检测报告</h1>",
        f"<div class=\"meta\">data_dir: {escape(str(data_dir))}</div>",
            '<div id="overview" class="panel">',
        pio.to_html(
            create_stock_scan_dashboard(ranking),
            include_plotlyjs="cdn",
            full_html=False,
        ),
        "</div>",
        ]
    )

    if symbol_rows:
        for row, symbol, display_name, section_id in symbol_rows:
            file_path = row.get("file_path") or find_stock_dat_file(data_dir, symbol)
            bars = load_stock_minute_bars(
                file_path,
                start=start,
                end=end,
                adjust=adjust,
            )
            report = analyze_bars(symbol, bars)
            score = _format_dashboard_value(row.get("overall_score"))
            samples = _format_dashboard_value(row.get("sample_count"))
            parts.extend(
                [
                    f'<details id="{section_id}" class="symbol">',
                    "<summary>",
                    f'<span class="symbol-title">{escape(display_name)}</span>',
                    f'<span class="symbol-meta">score {escape(score)} · n {escape(samples)}</span>',
                    "</summary>",
                    '<div class="symbol-body">',
                    pio.to_html(
                        create_symbol_dashboard(
                            report,
                            bars=bars,
                            title=f"{display_name} 数字分布异常检测指标总览",
                        ),
                        include_plotlyjs=False,
                        full_html=False,
                    ),
                    "</div>",
                    "</details>",
                ]
            )

    parts.extend(["</main>", "</div>", "</body>", "</html>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts), encoding="utf-8")
    return output


def _html_anchor_id(value: str, seen: set[str]) -> str:
    base = re.sub(r"[^0-9A-Za-z_-]+", "-", value).strip("-").lower() or "symbol"
    candidate = f"symbol-{base}"
    suffix = 2
    while candidate in seen:
        candidate = f"symbol-{base}-{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate
