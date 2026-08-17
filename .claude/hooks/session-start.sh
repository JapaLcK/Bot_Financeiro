#!/bin/bash
# SessionStart — prepara o ambiente para a suíte rodar em sessões do Claude Code na web.
#
# Sem isto, cada sessão repete o mesmo setup à mão: instalar as dependências,
# subir um Postgres e adivinhar as variáveis de ambiente. Pior, uma sessão que
# aponte para um Postgres compartilhado atropela as outras — a suíte apaga
# usuários que não são dela (ver tests/conftest.py). Aqui cada sessão ganha o
# seu próprio Postgres, no diretório da própria sessão.
set -uo pipefail

# Só no ambiente remoto: na máquina do dev o setup é o dele.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0

log() { echo "[session-start] $*"; }

# ── 1. Aviso de branch atrasada ──────────────────────────────────────────────
# Branch velha mente com confiança: o grep não acha arquivo que existe na main
# e a conclusão sai redonda e errada. Já aconteceu neste repositório.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch origin main --quiet 2>/dev/null
  atras=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
  if [ "${atras:-0}" -gt 0 ]; then
    log "AVISO: esta branch está $atras commit(s) atrás de origin/main."
    log "       Antes de afirmar que algo 'não existe', confira contra a main:"
    log "         git grep -l '<termo>' origin/main -- <caminho>"
  else
    log "branch em dia com origin/main."
  fi
fi

# ── 2. Dependências Python ───────────────────────────────────────────────────
# Alguns pacotes do requirements.txt não instalam neste ambiente e derrubariam
# o resto do `pip install` junto:
#   audioop-lts  exige Python >= 3.13
#   ofxparse     falha ao construir a wheel (setuptools/install_layout)
# Ficam de fora aqui; no CI, com Python 3.13, o requirements.txt roda inteiro.
# A ausência de ofxparse causa 9 erros de coleta; o tests/conftest.py os ignora
# automaticamente quando o pacote não está presente.
PULAR='^(audioop-lts|ofxparse)'
REQ_TMP="$(mktemp)"
grep -vE "$PULAR" requirements.txt > "$REQ_TMP"

log "instalando dependências Python..."
if python3 -m pip install -q --disable-pip-version-check -r "$REQ_TMP" 2>&1 | tail -3; then
  log "dependências instaladas."
else
  log "AVISO: parte das dependências falhou; a suíte pode não rodar."
fi
rm -f "$REQ_TMP"
python3 -m pip install -q --disable-pip-version-check pytest anyio 2>/dev/null

# ── 3. Postgres próprio desta sessão ─────────────────────────────────────────
PGBIN=""
for v in 17 16 15 14; do
  [ -x "/usr/lib/postgresql/$v/bin/pg_ctl" ] && PGBIN="/usr/lib/postgresql/$v/bin" && break
done

DB_URL=""
if [ -n "$PGBIN" ]; then
  PGDATA="${TMPDIR:-/tmp}/pgdata-${CLAUDE_SESSION_ID:-$$}"
  PGSOCK="/tmp/pgsock-${CLAUDE_SESSION_ID:-$$}"
  PGPORT="${PGPORT:-5432}"

  if [ ! -d "$PGDATA/base" ]; then
    log "inicializando Postgres em $PGDATA ..."
    mkdir -p "$PGDATA" "$PGSOCK"
    # initdb recusa rodar como root; se existir o usuário postgres, usa ele.
    if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
      chown postgres:postgres "$PGDATA" "$PGSOCK"; chmod 700 "$PGDATA"
      chmod 755 "${TMPDIR:-/tmp}" 2>/dev/null
      PGRUN="su postgres -c"
    else
      PGRUN="bash -c"
    fi
    $PGRUN "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1 \
      || log "AVISO: initdb falhou."
  else
    [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1 && PGRUN="su postgres -c" || PGRUN="bash -c"
  fi

  if [ -d "$PGDATA/base" ]; then
    LOGF="$PGDATA/server.log"; : > "$LOGF"
    [ "$PGRUN" = "su postgres -c" ] && chown postgres:postgres "$LOGF"
    $PGRUN "$PGBIN/pg_ctl -D $PGDATA -o '-p $PGPORT -k $PGSOCK -c listen_addresses=127.0.0.1' -l $LOGF -w -t 30 start" >/dev/null 2>&1
    $PGRUN "$PGBIN/createdb -h 127.0.0.1 -p $PGPORT -U postgres bot_financeiro_test" >/dev/null 2>&1
    if $PGRUN "$PGBIN/pg_isready -h 127.0.0.1 -p $PGPORT -U postgres" >/dev/null 2>&1; then
      DB_URL="postgresql://postgres:postgres@127.0.0.1:$PGPORT/bot_financeiro_test"
      log "Postgres no ar na porta $PGPORT."
    else
      log "AVISO: Postgres não subiu; veja $LOGF."
    fi
  fi
else
  log "AVISO: Postgres não encontrado — os testes que usam banco não vão rodar."
fi

# ── 4. Variáveis de ambiente da sessão ───────────────────────────────────────
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export PYTHONPATH="."'
    [ -n "$DB_URL" ] && echo "export DATABASE_URL=\"$DB_URL\""
    echo 'export JWT_SECRET="dev-only-jwt-secret-32-bytes-minimum-len"'
    echo "export PII_ENCRYPTION_KEY=\"$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())' 2>/dev/null)\""
    echo 'export PII_HASH_PEPPER="dev-only-pepper-must-be-32-chars-long!!"'
    echo 'export PII_AUDIT_DISABLED=1'
    echo 'export RUN_BACKGROUND_TASKS=0'
  } >> "$CLAUDE_ENV_FILE"
  log "variáveis de ambiente exportadas."
fi

log "pronto. Rode a suíte com: python3 -m pytest -q"
exit 0
