from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pytest

from stock_digital_analysis.digital_distribution import (
    analyze_bars,
    attach_stock_names,
    benford_expected_distribution,
    benford_test,
    bars_to_dataframe,
    create_benford_figure,
    create_stock_scan_dashboard,
    create_symbol_dashboard,
    daily_ohlc_records,
    find_stock_dat_file,
    _filter_symbol_options,
    _overview_explanation_html,
    _resolve_stock_names_from_sina,
    _symbol_explanation_html,
    _symbol_page_filename,
    _tradingview_chart_html,
    _monthly_metrics_table_html,
    monthly_metric_records,
    _symbol_selector_label,
    stock_symbol_to_quotation_code,
    rank_scan_results,
    report_to_summary,
    first_significant_digit,
    price_tail_test,
)


CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class SampleBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume_lots: int
    volume_shares: int
    amount: int


def sample_bar(
    time: datetime,
    close: float,
    volume_shares: int,
    amount: int,
) -> SampleBar:
    return SampleBar(
        time=time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume_lots=volume_shares // 100,
        volume_shares=volume_shares,
        amount=amount,
    )


def test_first_significant_digit_handles_scales_and_invalid_values():
    assert first_significant_digit(0) is None
    assert first_significant_digit(-0.045) == 4
    assert first_significant_digit(999.0) == 9
    assert first_significant_digit(10.37) == 1


def test_benford_test_returns_expected_ratios_and_statistics():
    result = benford_test([10, 11, 20, 30, 0, -405])
    expected = benford_expected_distribution()

    assert result.sample_count == 5
    assert result.counts[1] == 2
    assert result.counts[4] == 1
    assert result.expected_ratio[1] == expected[1]
    assert result.mad > 0
    assert result.ks_statistic > 0


def test_price_tail_test_calculates_tail_and_round_number_ratios():
    result = price_tail_test([10.00, 10.05, 10.10, 10.37])

    assert result.sample_count == 4
    assert result.counts[0] == 2
    assert result.counts[5] == 1
    assert result.counts[7] == 1
    assert result.tail_concentration == 0.5
    assert result.tail_0_5_ratio == 0.75
    assert result.round_1_ratio == 0.25
    assert result.round_01_ratio == 0.5
    assert result.round_005_ratio == 0.75


def test_analyze_bars_includes_close_tail_offsets_by_day():
    bars = [
        sample_bar(datetime(2026, 6, 22, 14, 29, tzinfo=CHINA_TZ), 9.9, 100, 990),
        sample_bar(datetime(2026, 6, 22, 14, 30, tzinfo=CHINA_TZ), 10.0, 100, 1000),
        sample_bar(datetime(2026, 6, 22, 14, 55, tzinfo=CHINA_TZ), 10.2, 100, 1020),
        sample_bar(datetime(2026, 6, 22, 15, 0, tzinfo=CHINA_TZ), 10.5, 100, 1050),
        sample_bar(datetime(2026, 6, 23, 14, 30, tzinfo=CHINA_TZ), 20.0, 100, 2000),
        sample_bar(datetime(2026, 6, 23, 15, 0, tzinfo=CHINA_TZ), 19.0, 100, 1900),
    ]

    report = analyze_bars("TEST.SH", bars)

    assert report.symbol == "TEST.SH"
    assert report.sample_count == 6
    assert report.close_tail.sample_count == 2
    assert report.close_tail.offsets[0].close_return_from_1430 == pytest.approx(0.05)
    assert report.close_tail.offsets[0].close_return_from_1455 == pytest.approx(
        10.5 / 10.2 - 1
    )
    assert report.close_tail.offsets[1].close_return_from_1430 == pytest.approx(-0.05)
    assert report.close_tail.mean_return_from_1430 == pytest.approx(0)
    assert report.close_tail.mean_abs_return_from_1430 == pytest.approx(0.05)


def test_colab_helpers_flatten_records_summary_and_ranking():
    bars = [
        sample_bar(datetime(2026, 6, 22, 14, 30, tzinfo=CHINA_TZ), 10.0, 100, 1000),
        sample_bar(datetime(2026, 6, 22, 15, 0, tzinfo=CHINA_TZ), 10.5, 200, 2100),
    ]

    df = bars_to_dataframe(bars, symbol="TEST.SH")
    assert list(df["tail_digit"]) == [0, 0]
    assert list(df["is_round_1"]) == [True, False]

    report = analyze_bars("TEST.SH", bars)
    summary = report_to_summary(report)
    assert summary["symbol"] == "TEST.SH"
    assert summary["sample_count"] == 2
    assert "benford_amount_score" in summary

    ranking = rank_scan_results(
        [
            summary,
            {
                **summary,
                "symbol": "OTHER.SH",
                "benford_amount_score": summary["benford_amount_score"] + 1,
            },
        ]
    )
    assert list(ranking["symbol"])[0] == "OTHER.SH"
    assert "overall_score" in ranking.columns


def test_daily_ohlc_records_aggregate_minute_bars_by_day():
    bars = [
        sample_bar(datetime(2026, 6, 22, 9, 30, tzinfo=CHINA_TZ), 10.0, 100, 1000),
        sample_bar(datetime(2026, 6, 22, 15, 0, tzinfo=CHINA_TZ), 10.5, 200, 2100),
        sample_bar(datetime(2026, 6, 23, 15, 0, tzinfo=CHINA_TZ), 9.5, 300, 2850),
    ]

    records = daily_ohlc_records(bars)

    assert records[0]["time"] == "2026-06-22"
    assert records[0]["open"] == 10.0
    assert records[0]["close"] == 10.5
    assert records[0]["volume"] == 300
    assert records[1]["time"] == "2026-06-23"


def test_monthly_metric_records_include_monthly_anomaly_score():
    bars = [
        sample_bar(datetime(2026, 5, 29, 14, 30, tzinfo=CHINA_TZ), 10.0, 100, 1000),
        sample_bar(datetime(2026, 5, 29, 15, 0, tzinfo=CHINA_TZ), 10.5, 200, 2100),
        sample_bar(datetime(2026, 6, 22, 14, 30, tzinfo=CHINA_TZ), 20.0, 500, 10000),
        sample_bar(datetime(2026, 6, 22, 15, 0, tzinfo=CHINA_TZ), 19.0, 700, 13300),
    ]

    records = monthly_metric_records("TEST.SH", bars)

    assert [row["month"] for row in records] == ["2026-05", "2026-06"]
    assert "monthly_anomaly_score" in records[0]
    assert records[0]["month_time"] == "2026-05-01"


def test_plotly_helper_returns_figure_without_showing():
    result = benford_test([10, 20, 30])
    fig = create_benford_figure(result, "test")
    assert fig.layout.title.text == "test"
    assert len(fig.data) == 2


def test_find_stock_dat_file_supports_symbol_formats(tmp_path):
    dat_file = tmp_path / "SH" / "60" / "601328.DAT"
    dat_file.parent.mkdir(parents=True)
    dat_file.write_bytes(b"")

    assert find_stock_dat_file(tmp_path, "601328.SH") == dat_file
    assert find_stock_dat_file(tmp_path, "SH601328") == dat_file
    assert find_stock_dat_file(tmp_path, "601328") == dat_file


def test_stock_symbol_to_quotation_code_supports_common_formats():
    assert stock_symbol_to_quotation_code("601328.SH") == "sh601328"
    assert stock_symbol_to_quotation_code("SZ000721") == "sz000721"


def test_attach_stock_names_adds_display_name_from_api_lookup(monkeypatch):
    rows = [{"symbol": "601328.SH", "sample_count": 10}]
    monkeypatch.setattr(
        "stock_digital_analysis.digital_distribution.resolve_stock_names",
        lambda symbols: {"601328.SH": "交通银行"},
    )

    named = attach_stock_names(rows)

    assert named[0]["stock_name"] == "交通银行"
    assert named[0]["display_name"] == "交通银行 (601328.SH)"


def test_resolve_stock_names_from_sina_quote_api(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return (
                'var hq_str_sh601328="交通银行,7.630,7.630";\n'
                'var hq_str_sz000721="西安饮食,9.380,9.420";\n'
            ).encode("gb18030")

    def fake_urlopen(request, timeout):
        assert timeout == 3
        assert request.full_url.endswith("list=sh601328,sz000721")
        return FakeResponse()

    monkeypatch.setattr(
        "stock_digital_analysis.digital_distribution.urllib.request.urlopen",
        fake_urlopen,
    )

    names = _resolve_stock_names_from_sina(
        ["sh601328", "sz000721"],
        {"sh601328": "601328.SH", "sz000721": "000721.SZ"},
    )

    assert names == {"601328.SH": "交通银行", "000721.SZ": "西安饮食"}


def test_symbol_dashboard_combines_main_metric_panels():
    bars = [
        sample_bar(datetime(2026, 6, 22, 14, 30, tzinfo=CHINA_TZ), 10.0, 100, 1000),
        sample_bar(datetime(2026, 6, 22, 14, 55, tzinfo=CHINA_TZ), 10.2, 200, 2100),
        sample_bar(datetime(2026, 6, 22, 15, 0, tzinfo=CHINA_TZ), 10.5, 300, 3150),
    ]
    report = analyze_bars("TEST.SH", bars)

    fig = create_symbol_dashboard(report, bars=bars)

    assert fig.layout.title.text == "TEST.SH 数字分布异常检测指标总览"
    assert len(fig.data) >= 8
    assert fig.data[-1].type == "table"


def test_stock_scan_dashboard_uses_display_name_and_summary_table():
    ranking = rank_scan_results(
        [
            {
                "symbol": "601328.SH",
                "stock_name": "交通银行",
                "display_name": "交通银行 (601328.SH)",
                "sample_count": 100,
                "benford_amount_score": 0.1,
                "benford_volume_score": 0.2,
                "tail_concentration": 0.3,
                "tail_0_5_ratio": 0.4,
                "round_1_ratio": 0.5,
                "round_01_ratio": 0.6,
                "round_005_ratio": 0.7,
                "close_tail_anomaly_score": 0.8,
            }
        ]
    )

    fig = create_stock_scan_dashboard(ranking)

    assert fig.layout.title.text == "股票数字分布异常检测总览"
    assert fig.data[0].x[0] == "交通银行 (601328.SH)"
    assert fig.data[-1].type == "table"


def test_dashboard_explanation_html_describes_metrics_for_clients():
    overview = _overview_explanation_html()
    symbol = _symbol_explanation_html()

    assert "统计说明" in overview
    assert "异常综合分数" in overview
    assert "核心指标明细表" in overview
    assert "Benford" in symbol
    assert "价格尾数分布" in symbol
    assert "尾盘价格偏移" in symbol


def test_tradingview_and_monthly_table_html_include_client_context():
    monthly = [
        {
            "month": "2026-06",
            "month_time": "2026-06-01",
            "sample_count": 2,
            "monthly_anomaly_score": 1.25,
            "benford_amount_score": 0.1,
            "benford_volume_score": 0.2,
            "tail_concentration": 0.3,
            "tail_0_5_ratio": 0.4,
            "round_01_ratio": 0.5,
            "close_tail_anomaly_score": 0.6,
        }
    ]
    chart_html = _tradingview_chart_html(
        "symbol-test",
        [{"time": "2026-06-22", "open": 10, "high": 11, "low": 9, "close": 10.5}],
        monthly,
    )
    table_html = _monthly_metrics_table_html(monthly)

    assert "LightweightCharts" in chart_html
    assert "日 K 线与月度异常分数" in chart_html
    assert "月度异常指标" in table_html
    assert "2026-06" in table_html


def test_symbol_page_filename_keeps_symbol_readable_and_safe():
    assert _symbol_page_filename("601328.SH") == "601328.SH.html"
    assert _symbol_page_filename("  weird symbol/SZ  ") == "weird-symbol-SZ.html"


def test_symbol_selector_helpers_filter_by_name_and_symbol():
    row = {
        "display_name": "交通银行 (601328.SH)",
        "symbol": "601328.SH",
        "sample_count": 100,
        "overall_score": 0.123456,
    }
    label = _symbol_selector_label(row)
    options = {label: row["symbol"], "绿通科技 (301322.SZ)": "301322.SZ"}

    assert "交通银行" in label
    assert _filter_symbol_options(options, "交通", 10) == [(label, "601328.SH")]
    assert _filter_symbol_options(options, "601328", 10) == [(label, "601328.SH")]
    assert _filter_symbol_options(options, "sz", 10) == [
        ("绿通科技 (301322.SZ)", "301322.SZ")
    ]
