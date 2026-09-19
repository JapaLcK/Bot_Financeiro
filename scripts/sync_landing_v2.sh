#!/usr/bin/env bash
# Re-espelha a landing v2 (build hospedada no Lovable) em frontend/landing-v2/
# e reaplica as limpezas (scripts/clean_landing_v2.py). Uso quando a página for
# republicada no Lovable e os hashes dos arquivos mudarem — nesse caso,
# atualize também a lista de nomes abaixo e as rotas/testes que os referenciam.
set -euo pipefail

BASE="https://piggy-speak-finance.lovable.app"
DEST="$(cd "$(dirname "$0")/.." && pwd)/frontend/landing-v2"

mkdir -p "$DEST/assets"

curl -sf "$BASE/" -o "$DEST/index.html"
for f in \
  styles-Rgs8_YIn.css \
  index-C7puP08d.js \
  routes-BgmvrBee.js \
  precos-BlOJhx9-.js \
  pigbank-snout.png.asset-CsmR-u6_.js \
  pigbank-ambient-Ci1cTuh4.jpg
do
  curl -sf "$BASE/assets/$f" -o "$DEST/assets/$f"
done

for u in \
  0b3f8be0-9a43-4cad-8768-452c89261459/pig_q34.png \
  382b917d-4e7a-4e8b-8228-de84dafce277/pigbank-snout.png \
  38972309-f70a-4378-b131-6f1d4e52daf6/pig_front.png \
  868f45a5-e671-42c8-81ac-3f0ec5e16c20/pig_confiante.png \
  aad908cd-8dc8-434a-b844-a2e70d71edcf/pigbank-logo.webp \
  b8a499d3-1504-4c9a-8f2e-6599b7fa15ee/pig_rindo.png \
  bfd6a4bf-2b51-4e9e-9edd-bb7b5525abd7/pig_pensativo.png
do
  mkdir -p "$DEST/l5e/$(dirname "$u")"
  curl -sf "$BASE/__l5e/assets-v1/$u" -o "$DEST/l5e/$u"
done

python3 "$(dirname "$0")/clean_landing_v2.py"
