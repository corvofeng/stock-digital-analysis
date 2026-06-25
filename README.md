# Stock Digital Analysis

Standalone tools for reading local stock 60-second `DAT` files, calculating digital-distribution anomaly metrics, using notebook selectors, and exporting Plotly HTML reports.

## Setup

From this directory:

```bash
uv sync --extra notebook --extra test
```

Or install into an existing environment:

```bash
uv pip install -e ".[notebook,test]"
```

## Analyze One File

```bash
uv run stock-read-dat ../../datadir/SH/60/601328.DAT --analyze
```

`--analyze` prints the notebook-style detail tables: summary scores,
Benford first-digit observed/expected/deviation rows, price tail digit
distribution, close-tail daily offsets, and daily round-price clustering.

```bash
uv run stock-read-dat ../../datadir/SH/60/601328.DAT --tail 0 --analyze --detail-rows 20
uv run stock-read-dat ../../datadir/SH/60/601328.DAT --tail 0 --analyze --detail-rows 0
uv run stock-read-dat ../../datadir/SH/60/601328.DAT --tail 0 --analyze --analysis-detail summary
```

From Python:

```python
from stock_digital_analysis import analyze_stock_dat

report = analyze_stock_dat("../../datadir/SH/60/601328.DAT")
report.to_dict()
```

## Export An HTML Report

CLI:

```bash
uv run stock-write-html ../../datadir -o reports/stock-digital-report.html
uv run stock-write-html ../../datadir -o reports/stock-digital-overview.html --overview-only
uv run stock-write-html ../../datadir -o reports/stock-digital-report.html --no-resolve-names
```

The HTML report includes a table of contents and collapsible per-symbol
sections. Stock names are resolved from Sina's quote API by default each time
you generate a report, so new DAT files do not require maintaining a local
mapping file. Add `--no-resolve-names` for offline-only report generation.

Python:

```python
from stock_digital_analysis import write_stock_datadir_dashboard_html

write_stock_datadir_dashboard_html(
    "reports/stock-digital-report.html",
    "../../datadir",
    include_symbol_dashboards=True,
    resolve_names=True,
)
```

The generated HTML contains the computed tables and Plotly charts, so you can reopen it later without reconnecting to the notebook kernel.

## Notebook Selector

```python
from IPython.display import display
from stock_digital_analysis.digital_distribution import create_stock_symbol_selector

selector = create_stock_symbol_selector("../../datadir", resolve_names=True)
display(selector)
```

## Tests

```bash
uv run python -m pytest
```
