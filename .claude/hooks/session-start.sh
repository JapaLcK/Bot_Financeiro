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

# O identificador da sessão chega no JSON do stdin, não no ambiente — não existe
# CLAUDE_SESSION_ID exportado. Sem ler daqui, o fallback vira $$ (PID do shell),
# que MUDA a cada disparo: startup, resume e compact criariam um cluster novo
# cada um, em vez de reaproveitar o da sessão.
SID=""
if [ ! -t 0 ]; then   # só lê se houver algo canalizado; não trava em teste manual
  SID=$(python3 -c "
import sys, json
try:
    print((json.load(sys.stdin).get('session_id') or '')[:32])
except Exception:
    print('')
" 2>/dev/null)
fi
SID="${SID:-${CLAUDE_SESSION_ID:-$$}}"

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
# Enumerado por estado, não remendado caso a caso. O hook dispara mais de uma
# vez na mesma sessão (startup, resume, compact), então os três estados abaixo
# são rotina, não exceção:
#
#   PGDATA ausente          -> initdb, sorteia porta, persiste, sobe
#   PGDATA + servidor vivo   -> REUSA a porta do postmaster.pid, não sobe nada
#   PGDATA + servidor morto  -> reusa a porta persistida (ou sorteia outra se
#                               ela foi tomada), sobe
#
# A porta precisa ser persistida: sorteá-la de novo num resume faria o pg_ctl
# recusar (cluster já rodando), os testes sondarem uma porta vazia e o hook
# zerar o DATABASE_URL — quebrando a suíte num evento de ciclo de vida normal.
#
# PGPORT do ambiente NÃO é herdado de propósito: um valor global no host
# colocaria todas as sessões concorrentes na mesma porta, que é exatamente o
# que a alocação por sessão evita.
PGBIN=""
for v in 17 16 15 14; do
  [ -x "/usr/lib/postgresql/$v/bin/pg_ctl" ] && PGBIN="/usr/lib/postgresql/$v/bin" && break
done

_porta_livre() {
  python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
" 2>/dev/null || echo ""
}

DB_URL=""
if [ -n "$PGBIN" ]; then
  PGDATA="${TMPDIR:-/tmp}/pgdata-${SID}"
  PGSOCK="/tmp/pgsock-${SID}"
  PORTFILE="$PGDATA/.session_port"

  if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
    PGRUN="su postgres -c"
  else
    PGRUN="bash -c"
  fi

  # initdb só quando o cluster ainda não existe.
  if [ ! -d "$PGDATA/base" ]; then
    log "inicializando Postgres em $PGDATA ..."
    mkdir -p "$PGDATA" "$PGSOCK"
    if [ "$PGRUN" = "su postgres -c" ]; then
      chown postgres:postgres "$PGDATA" "$PGSOCK"; chmod 700 "$PGDATA"
      chmod 755 "${TMPDIR:-/tmp}" 2>/dev/null
    fi
    if ! $PGRUN "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null 2>&1; then
      # Limpa o que ficou pela metade. O initdb recusa diretório NÃO-VAZIO, então
      # qualquer resto (log parcial, arquivo de estado) transformaria uma falha
      # transitória em permanente: toda sessão seguinte tentaria de novo e seria
      # recusada até alguém apagar o diretório à mão.
      log "AVISO: initdb falhou — limpando $PGDATA para a próxima tentativa."
      rm -rf "$PGDATA" 2>/dev/null
    fi
  fi

  if [ -d "$PGDATA/base" ]; then
    VIVO=0
    if $PGRUN "$PGBIN/pg_ctl -D $PGDATA status" >/dev/null 2>&1; then
      VIVO=1
    fi

    if [ "$VIVO" = "1" ]; then
      # Já rodando (resume/compact): a porta real está na linha 4 do
      # postmaster.pid. Reusar, nunca sortear outra.
      PGPORT=$(sed -n 4p "$PGDATA/postmaster.pid" 2>/dev/null | tr -d ' ')
      log "Postgres desta sessão já estava no ar (porta $PGPORT)."
    else
      # Parado: tenta a porta persistida; se estiver ocupada, sorteia outra.
      PGPORT=$(cat "$PORTFILE" 2>/dev/null | tr -d ' ')
      if [ -n "$PGPORT" ] && python3 -c "
import socket, sys
s = socket.socket()
sys.exit(0 if s.connect_ex(('127.0.0.1', $PGPORT)) != 0 else 1)
" 2>/dev/null; then
        :  # porta persistida continua livre
      else
        PGPORT=$(_porta_livre)
      fi
      [ -z "$PGPORT" ] && PGPORT=5432
      echo "$PGPORT" > "$PORTFILE" 2>/dev/null
      [ "$PGRUN" = "su postgres -c" ] && chown postgres:postgres "$PORTFILE" 2>/dev/null

      mkdir -p "$PGSOCK"
      [ "$PGRUN" = "su postgres -c" ] && chown postgres:postgres "$PGSOCK"
      LOGF="$PGDATA/server.log"; : > "$LOGF"
      [ "$PGRUN" = "su postgres -c" ] && chown postgres:postgres "$LOGF"
      $PGRUN "$PGBIN/pg_ctl -D $PGDATA -o '-p $PGPORT -k $PGSOCK -c listen_addresses=127.0.0.1' -l $LOGF -w -t 30 start" >/dev/null 2>&1
    fi

    # ORDEM IMPORTA: identificar antes de qualquer comando que mute.
    # A porta pode ter sido tomada por outro processo entre o _porta_livre e o
    # pg_ctl. Se o createdb viesse primeiro, ele criaria um database dentro do
    # cluster de OUTRA sessão — todos aceitam conexão trust como postgres —
    # antes de a URL ser recusada. `show data_directory` é leitura pura e
    # portanto seguro de rodar contra servidor alheio.
    if $PGRUN "$PGBIN/pg_isready -h 127.0.0.1 -p $PGPORT -U postgres" >/dev/null 2>&1; then
      DDIR=$($PGRUN "$PGBIN/psql -h 127.0.0.1 -p $PGPORT -U postgres -tAc 'show data_directory'" 2>/dev/null | tr -d ' ')
      if [ "$DDIR" = "$PGDATA" ]; then
        $PGRUN "$PGBIN/createdb -h 127.0.0.1 -p $PGPORT -U postgres bot_financeiro_test" >/dev/null 2>&1
        DB_URL="postgresql://postgres:postgres@127.0.0.1:$PGPORT/bot_financeiro_test"
        log "Postgres pronto na porta $PGPORT (data_directory conferido)."
      else
        log "AVISO: a porta $PGPORT responde, mas de outro servidor ($DDIR)."
        log "       Não vou tocar nele — nem para criar o banco de teste."
      fi
    else
      log "AVISO: Postgres não respondeu na porta $PGPORT; veja $PGDATA/server.log."
    fi
  fi
else
  log "AVISO: Postgres não encontrado — os testes que usam banco não vão rodar."
fi

# ── 4. Variáveis de ambiente da sessão ───────────────────────────────────────
# A chave Fernet acompanha o CLUSTER, não o disparo do hook. Como o PGDATA
# agora é reaproveitado entre startup/resume/compact, gerar chave nova a cada
# vez deixaria ilegível todo PII escrito antes — o dado continua cifrado com a
# chave anterior e o decrypt_pii falha. Fica persistida ao lado da porta, no
# próprio PGDATA, com o mesmo ciclo de vida do banco que ela protege.
#
# É o único valor gerado aqui: JWT_SECRET e PII_HASH_PEPPER são constantes de
# desenvolvimento (e o pepper, por definição, nunca rotaciona — core/crypto.py).
PII_KEY=""
PII_KEY_FILE=""
# Só grava num cluster REALMENTE inicializado — `$PGDATA/base` é o mesmo teste
# que o bloco acima usa. Escrever com o initdb falhado deixaria o diretório
# não-vazio e envenenaria a próxima tentativa.
[ -n "${PGDATA:-}" ] && [ -d "${PGDATA:-/nao-existe}/base" ] && PII_KEY_FILE="$PGDATA/.session_pii_key"

if [ -n "$PII_KEY_FILE" ] && [ -s "$PII_KEY_FILE" ]; then
  PII_KEY=$(cat "$PII_KEY_FILE" 2>/dev/null | tr -d '\n')
fi
if [ -z "$PII_KEY" ]; then
  PII_KEY=$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())' 2>/dev/null)
  if [ -n "$PII_KEY_FILE" ] && [ -n "$PII_KEY" ]; then
    printf '%s' "$PII_KEY" > "$PII_KEY_FILE" 2>/dev/null
    chmod 600 "$PII_KEY_FILE" 2>/dev/null
    [ "$PGRUN" = "su postgres -c" ] && chown postgres:postgres "$PII_KEY_FILE" 2>/dev/null
  fi
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export PYTHONPATH="."'
    # Sem banco isolado, ZERA a variável em vez de deixar passar o valor
    # herdado do ambiente. A suíte apaga usuários, lançamentos, caixinhas e
    # investimentos (tests/conftest.py); herdar um DATABASE_URL apontando para
    # base compartilhada faria `pytest` destruir dados de verdade. Zerada, o
    # conftest recusa rodar com "Faltou DATABASE_URL" — falha segura.
    if [ -n "$DB_URL" ]; then
      echo "export DATABASE_URL=\"$DB_URL\""
    else
      echo 'export DATABASE_URL=""'
    fi
    echo 'export JWT_SECRET="dev-only-jwt-secret-32-bytes-minimum-len"'
    echo "export PII_ENCRYPTION_KEY=\"$PII_KEY\""
    echo 'export PII_HASH_PEPPER="dev-only-pepper-must-be-32-chars-long!!"'
    echo 'export PII_AUDIT_DISABLED=1'
    echo 'export RUN_BACKGROUND_TASKS=0'
    # Este ambiente monta o requirements SEM ofxparse (a wheel não constrói
    # aqui), então autoriza a suíte a pular o que depende dele. É explícito de
    # propósito: sem esta variável, ofxparse faltando continua estourando no
    # CI e na máquina do dev, como deve ser para dependência obrigatória.
    echo 'export PYTEST_ALLOW_MISSING_OPTIONAL_DEPS=1'
  } >> "$CLAUDE_ENV_FILE"
  log "variáveis de ambiente exportadas."
fi

if [ -n "$DB_URL" ]; then
  log "pronto. Rode a suíte com: python3 -m pytest -q"
else
  log "ATENÇÃO: sem banco isolado. DATABASE_URL foi zerado de propósito e a"
  log "         suíte vai recusar rodar — ela apaga linhas e não pode cair"
  log "         num banco compartilhado. Suba um Postgres antes de testar."
fi
exit 0
