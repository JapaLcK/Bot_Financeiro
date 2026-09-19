#!/usr/bin/env bash
# Sobe a branch feature/landing-v2 no serviço de STAGING do Railway
# (projeto reasonable-surprise / serviço Dashboard/whatsapp / ambiente staging).
#
# Uso: bash scripts/deploy_staging.sh
#
# Deploy via CLI (`railway up`) a partir de um diretório limpo exportado com
# `git archive` — sem working tree, sem WIP não commitado. Produção não é
# tocada: o link é feito para o AMBIENTE staging do projeto.
set -euo pipefail

PROJECT="36ea138f-3cc4-4f35-b367-343cb36b7fb4"
SERVICE="fa06a616-ccc3-4e40-a2ff-e6ea8dd83266"
ENVIRONMENT="6510c410-d274-4a32-a70d-ce4dc0514c7b"
BRANCH="${1:-feature/landing-v2}"

DEST="$(mktemp -d /tmp/pigbank-staging.XXXXXX)"
trap 'rm -rf "$DEST"' EXIT

git archive "$BRANCH" | tar -x -C "$DEST"

cd "$DEST"
railway link -p "$PROJECT" -s "$SERVICE" -e "$ENVIRONMENT" >/dev/null
railway up --detach

echo "Deploy de '$BRANCH' iniciado no staging. Build:"
echo "  https://railway.com/project/$PROJECT/service/$SERVICE"
echo "URL pública: https://dashboard-staging-3369.up.railway.app"
