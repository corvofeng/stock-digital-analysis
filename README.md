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
uv run stock-read-dat 601328.SH --analyze
```

`--analyze` prints the notebook-style detail tables: summary scores,
Benford first-digit observed/expected/deviation rows, price tail digit
distribution, close-tail daily offsets, and daily round-price clustering.

```bash
uv run stock-read-dat 601328.SH --tail 0 --analyze --detail-rows 20
uv run stock-read-dat 601328.SH --tail 0 --analyze --detail-rows 0
uv run stock-read-dat 601328.SH --tail 0 --analyze --analysis-detail summary
```

From Python:

```python
from stock_digital_analysis import analyze_stock_dat

report = analyze_stock_dat("datadir/SH/60/601328.DAT")
report.to_dict()
```

## Export An HTML Report

CLI:

```bash
uv run stock-write-html datadir -o reports/stock-digital-report.html
uv run stock-write-html datadir -o reports/stock-digital-overview.html --overview-only
uv run stock-write-html datadir -o reports/stock-digital-report.html --no-resolve-names
```

The HTML report includes a table of contents and collapsible per-symbol
sections. Stock names are resolved from Sina's quote API by default each time
you generate a report, so new DAT files do not require maintaining a local
mapping file. Each symbol section includes a TradingView Lightweight Charts
daily K-line view, monthly anomaly scores, and monthly metric tables so clients
can compare anomalies with price movement by month. Add `--no-resolve-names`
for offline-only report generation.

Python:

```python
from stock_digital_analysis import write_stock_datadir_dashboard_html

write_stock_datadir_dashboard_html(
    "reports/stock-digital-report.html",
    "datadir",
    include_symbol_dashboards=True,
    resolve_names=True,
)
```

The generated HTML contains the computed tables and Plotly charts, so you can reopen it later without reconnecting to the notebook kernel.

## Publish With GitHub Actions

The DAT files are intentionally kept outside Git and stored in R2/S3 under:

```bash
s3://blog/stock/datadir
```

To refresh the uploaded data from this machine:

```bash
source ~/.env.r2-blog
aws s3 sync datadir s3://blog/stock/datadir --delete
find datadir -type f -name '*.DAT' -printf '%P\n' | sort > datadir-manifest.txt
aws s3 cp datadir-manifest.txt s3://blog/stock/datadir-manifest.txt
```

The workflow in `.github/workflows/publish-report.yml` does not need GitHub
secrets. It downloads the public manifest and each DAT file from the public R2
URL:

```text
https://rawforcorvofeng.cn/stock/datadir
```

Then it runs the tests, writes the static overview to `public/index.html`, writes
one detail page per stock under `public/symbols/`, and force-pushes those static
files to the `cloudflare-pages` branch. When you add or remove DAT files, upload
the refreshed `datadir-manifest.txt` along with the DAT files.

In Cloudflare Pages, connect this repository and set the production branch to
`cloudflare-pages`. No build command is needed because the branch already
contains `index.html`.

You can also deploy directly from `main`, matching the `OptionSlides` style. The
repository includes `wrangler.jsonc` with static assets served from `./public`.
Use this Cloudflare Pages build command:

```bash
python -m pip install --upgrade pip uv && uv sync --extra test && scripts/build-public-site.sh
```

Set the output directory to:

```text
public
```

## Notebook Selector

```python
from IPython.display import display
from stock_digital_analysis.digital_distribution import create_stock_symbol_selector

selector = create_stock_symbol_selector("datadir", resolve_names=True)
display(selector)
```

## Tests

```bash
uv run python -m pytest
```
