#!/usr/bin/env bash
set -euo pipefail

DATA_BASE_URL="${DATA_BASE_URL:-https://rawforcorvofeng.cn/stock/datadir}"
DATA_MANIFEST_URL="${DATA_MANIFEST_URL:-https://rawforcorvofeng.cn/stock/datadir-manifest.txt}"

rm -rf datadir public

if curl --fail --location --retry 3 --retry-delay 2 \
  --output datadir-manifest.remote.txt \
  "$DATA_MANIFEST_URL"; then
  mv datadir-manifest.remote.txt datadir-manifest.txt
fi

while IFS= read -r relpath; do
  [ -n "$relpath" ] || continue
  mkdir -p "datadir/$(dirname "$relpath")"
  curl --fail --location --retry 3 --retry-delay 2 \
    --output "datadir/$relpath" \
    "$DATA_BASE_URL/$relpath"
done < datadir-manifest.txt

mkdir -p public
uv run stock-write-html datadir -o public/index.html --separate-symbol-pages
