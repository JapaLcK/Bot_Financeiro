#!/usr/bin/env bash
# Login do app ponta a ponta, no simulador iOS, com Maestro.
#
#   ALVO=local   app/e2e/rodar.sh [--negativo] [--build]   (padrão)
#   ALVO=staging app/e2e/rodar.sh [--negativo] [--build]
#
# local: banco descartável + uvicorn em 127.0.0.1:8000, derrubados no fim.
# staging: não sobe nada; o apoio.py roda com o ambiente do staging (railway).
# --build: recompila o app do alvo (sem ele, só compila se não estiver instalado).
# --negativo: troca um insumo por fluxo; só sai 0 se os 5 ficarem vermelhos
#   NA asserção esperada (vermelho em outro passo — 429, app que não abriu — reprova).
set -euo pipefail

ALVO=${ALVO:-local}
NEGATIVO=0
BUILD=0
for a in "$@"; do
  case $a in
    --negativo) NEGATIVO=1 ;;
    --build) BUILD=1 ;;
    *) echo "uso: ALVO=local|staging $0 [--negativo] [--build]" >&2; exit 2 ;;
  esac
done

E2E=$(cd "$(dirname "$0")" && pwd)
RAIZ=$(cd "$E2E/../.." && pwd)
# O .venv mora no checkout principal; worktrees não têm o seu.
PY=$(cd "$RAIZ" && cd "$(git rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
# ponytail: um simulador só para os dois alvos — uma rodada por vez na máquina.
SIM="PigBank E2E"
export E2E_RUN=$(date +%s)
export E2E_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pigbank-e2e.XXXXXX")
export PYTHONPATH=$RAIZ
export JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}
export PATH=$JAVA_HOME/bin:$PATH
export MAESTRO_CLI_NO_ANALYTICS=1
# Simulador recém-ligado (ou máquina carregada) passa do teto padrão do driver.
export MAESTRO_DRIVER_STARTUP_TIMEOUT=${MAESTRO_DRIVER_STARTUP_TIMEOUT:-180000}

case $ALVO in
  local) APP_ENV=development; export E2E_API=http://127.0.0.1:8000 ;;
  staging) APP_ENV=staging; export E2E_API=https://staging.pigbankai.com ;;
  *) echo "ALVO inválido: $ALVO" >&2; exit 2 ;;
esac
APP_ID=com.pigbankai.mobile$([ $APP_ENV = development ] && echo .dev || echo .$APP_ENV) # app.config.ts

UVICORN=""
ROSTO=""
RESTAURAR=0  # 1 enquanto o build pode ter reescrito app/package.json e app/.gitignore
DB=pigbank_e2e_$E2E_RUN
limpar() {
  local rc=$?
  # Rodada que falhou: guarda as capturas do Maestro (o resto some — tem senhas de teste).
  if [ "$rc" != 0 ] && [ -d "$E2E_DIR/maestro" ]; then
    mv "$E2E_DIR/maestro" "${TMPDIR:-/tmp}/pigbank-e2e-falha-$E2E_RUN" && echo "capturas: ${TMPDIR:-/tmp}/pigbank-e2e-falha-$E2E_RUN" >&2
  fi
  if [ -n "$UVICORN" ]; then kill "$UVICORN" 2>/dev/null || true; wait "$UVICORN" 2>/dev/null || true; fi
  # O laço do Face ID do 05b: um TERM no meio dele sairia antes do `kill $ROSTO` lá embaixo.
  if [ -n "$ROSTO" ]; then kill "$ROSTO" 2>/dev/null || true; fi
  if [ "$ALVO" = local ]; then dropdb --if-exists --force "$DB" || true; fi
  # Sinal no meio do build: o `git checkout` do fim do compilar não chega a rodar.
  if [ "$RESTAURAR" = 1 ]; then git -C "$RAIZ/app" checkout -- package.json .gitignore || true; fi
  rm -rf "$E2E_DIR"
}
trap limpar EXIT
# Aborta mesmo se um filho tratar o sinal e sair com código. Rodado em segundo
# plano por shell não interativo, o INT chega ignorado e nada aqui o pega: use TERM.
trap 'exit 130' INT TERM

# Roda "$@" só com o básico + o arquivo env: nada do shell de quem roda (chave
# do Resend, Stripe, OpenAI…) chega ao backend local nem ao apoio.py. Dá exec:
# chame dentro de um subshell.
limpo() {
  exec env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" LANG=en_US.UTF-8 \
    E2E_DIR="$E2E_DIR" E2E_RUN="$E2E_RUN" E2E_API="$E2E_API" \
    sh -c 'set -a; . "$E2E_DIR/env"; set +a; exec "$@"' sh "$@"
}

apoio() {
  if [ "$ALVO" = local ]; then
    (limpo "$PY" "$E2E/apoio.py" "$@")
  elif [ "$1" = campo ] || [ "$1" = totp ] || [ "$1" = totp-alheio ]; then
    "$PY" "$E2E/apoio.py" "$@"  # só lê o contas.json da rodada, sem ida ao Railway
  else
    # Chaves e pepper do web do staging; o banco pela URL pública do Postgres do
    # staging (o DATABASE_URL do web é interno à rede do Railway).
    (cd "$RAIZ" && E2E_DB=$STAGING_DB railway run -e staging -s "Dashboard/whatsapp" -- \
      sh -c 'DATABASE_URL=$E2E_DB exec "$0" "$@"' "$PY" "$E2E/apoio.py" "$@")
  fi
}
if [ "$ALVO" = staging ]; then
  STAGING_DB=$(railway variables -e staging -s Postgres --kv | sed -n 's/^DATABASE_PUBLIC_URL=//p')
  [ -n "$STAGING_DB" ] || { echo "sem DATABASE_PUBLIC_URL do Postgres do staging" >&2; exit 1; }
fi

subir_local() {
  # O backend chama load_app_env(), que preenche o que falta com o .env (e o .env.dev)
  # da raiz do código mesmo depois do `env -i`: Resend, OpenAI, Sentry… de verdade.
  for f in "$RAIZ/.env" "$RAIZ/.env.dev"; do
    if [ -e "$f" ]; then
      echo "$f existe: o backend local herdaria as credenciais dele. Rode de um worktree sem .env." >&2; exit 1
    fi
  done
  if lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "a porta 8000 já está ocupada — não vou testar contra um servidor que não subi" >&2; exit 1
  fi
  createdb "$DB"
  local chave='from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
  local segredo='import secrets; print(secrets.token_urlsafe(48))'
  # Sem RESEND_API_KEY: o e-mail do "esqueci a senha" só é registrado, nunca sai.
  cat >"$E2E_DIR/env" <<EOF
DATABASE_URL=postgresql://localhost:5432/$DB
JWT_SECRET=$("$PY" -c "$segredo")
PII_ENCRYPTION_KEY=$("$PY" -c "$chave")
MFA_ENCRYPTION_KEY=$("$PY" -c "$chave")
PII_HASH_PEPPER=$("$PY" -c "$segredo")
PII_AUDIT_DISABLED=1
PLANS_V2_ENABLED=0
RUN_BACKGROUND_TASKS=0
PYTHONPATH=$RAIZ
EOF
  (cd "$RAIZ" && limpo "$PY" -m uvicorn frontend.finance_bot_websocket_custom:app --http h11 --host 127.0.0.1 --port 8000) \
    >"$E2E_DIR/uvicorn.log" 2>&1 &
  UVICORN=$!
  for _ in $(seq 120); do
    curl -s -o /dev/null "$E2E_API/" && return 0
    kill -0 "$UVICORN" 2>/dev/null || break
    sleep 1
  done
  echo "o uvicorn não respondeu:" >&2; tail -30 "$E2E_DIR/uvicorn.log" >&2; exit 1
}

preparar_simulador() {
  UDID=$(xcrun simctl list devices | grep -m1 "    $SIM (" | grep -Eo '[0-9A-F-]{36}' || true)
  if [ -z "$UDID" ]; then
    local runtime
    runtime=$(xcrun simctl list runtimes | awk '/^iOS /{r=$NF} END{print r}')
    UDID=$(xcrun simctl create "$SIM" "iPhone 17" "$runtime")
  fi
  xcrun simctl bootstatus "$UDID" -b >/dev/null
  # Um rosto cadastrado (Features > Face ID > Enrolled): a trava do 05b pede Face ID, não o código.
  xcrun simctl spawn "$UDID" notifyutil -s com.apple.BiometricKit.enrollmentChanged 1
  xcrun simctl spawn "$UDID" notifyutil -p com.apple.BiometricKit.enrollmentChanged
}

compilar() {
  cd "$RAIZ/app"
  # `expo run:ios` reescreve estes dois; com mudança sua neles, o restore abaixo a apagaria.
  if ! git diff --quiet -- package.json .gitignore; then
    echo "app/package.json ou app/.gitignore com mudança local — recuso compilar" >&2; exit 1
  fi
  local ok=0
  RESTAURAR=1
  # prebuild --clean: o bundle id (e o Info.plist) muda com o APP_ENV.
  APP_ENV=$APP_ENV EXPO_PUBLIC_API_URL=$E2E_API SENTRY_DISABLE_AUTO_UPLOAD=true LANG=en_US.UTF-8 \
    npx expo prebuild --clean --platform ios &&
    APP_ENV=$APP_ENV EXPO_PUBLIC_API_URL=$E2E_API SENTRY_DISABLE_AUTO_UPLOAD=true LANG=en_US.UTF-8 \
      npx expo run:ios --configuration Release --device "$UDID" --no-bundler || ok=$?
  git checkout -- package.json .gitignore
  RESTAURAR=0
  cd "$RAIZ"
  [ $ok = 0 ] || { echo "build falhou" >&2; exit 1; }
}

# fluxo <arquivo> <passo que o negativo tem de derrubar | -> [-e CHAVE=valor ...]
# "-" = fluxo de preparo: tem de passar nos dois modos.
RESUMO=""
FALHOU=0
fluxo() {
  local nome=$1 alvo=$2; shift 2
  local saida rc=0 t0=$SECONDS
  saida=$(maestro --device "$UDID" test --debug-output "$E2E_DIR/maestro/$nome" \
    -e APP_ID="$APP_ID" "$@" "$E2E/flows/$nome.yaml" 2>&1) || rc=$?
  echo "$saida"
  local veredito
  if [ "$NEGATIVO" = 0 ] || [ "$alvo" = - ]; then
    [ $rc = 0 ] && veredito="verde" || { veredito="VERMELHO"; FALHOU=1; }
  elif [ $rc = 0 ]; then
    veredito="VERDE — o negativo não derrubou"; FALHOU=1
  elif echo "$saida" | grep -F "$alvo" | grep -q "FAILED"; then
    veredito="vermelho no passo esperado"
  else
    veredito="VERMELHO NO PASSO ERRADO (esperado: $alvo)"; FALHOU=1
  fi
  RESUMO+=$(printf '%-16s %s (%ss)' "$nome" "$veredito" $((SECONDS - t0)))$'\n'
}

[ "$ALVO" = local ] && subir_local
apoio semear
preparar_simulador
if [ $BUILD = 1 ] || ! xcrun simctl get_app_container "$UDID" "$APP_ID" >/dev/null 2>&1; then
  compilar
  # O `expo run:ios` abre uma URL do dev client ao instalar, e o alerta "Open in …?"
  # fica por cima do app em todos os fluxos. Reiniciar o simulador o limpa.
  xcrun simctl shutdown "$UDID"
  preparar_simulador
fi

c() { apoio campo "$@"; }
OLA='"Olá, E2E"'
if [ $NEGATIVO = 0 ]; then S1=$(c 1 senha); B3=$(c 3 backup)
else S1=errada-de-proposito; B3=AAAAA-AAAAA; fi
# No negativo, um código que a conta 2 não aceita
totp2() { if [ $NEGATIVO = 0 ]; then apoio totp 2; else apoio totp-alheio 2; fi; }

fluxo 01-email "$OLA" -e EMAIL="$(c 1 email)" -e SENHA="$S1"
fluxo 02-mfa-totp "$OLA" -e EMAIL="$(c 2 email)" -e SENHA="$(c 2 senha)" -e TOTP="$(totp2)"
fluxo 03-backup "$OLA" -e EMAIL="$(c 3 email)" -e SENHA="$(c 3 senha)" -e BACKUP="$B3"

# /auth/login aceita 5 por minuto por IP (slowapi em memória + auth_rate_limits
# no banco, janela fixa a partir da 1ª tentativa). A rodada faz 7: três acima,
# quatro abaixo (senha antiga, senha nova, 05a, o "outro aparelho" do revogar).
# 61 s zeram as duas janelas antes da segunda leva.
sleep 61

fluxo 04a-pedir-reset - -e EMAIL="$(c 4 email)"
ANTIGA=$(c 4 senha)
[ $NEGATIVO = 0 ] && apoio reset 4
fluxo 04b-senha-nova '"E-mail ou senha incorretos."' -e EMAIL="$(c 4 email)" -e ANTIGA="$ANTIGA" -e NOVA="$(c 4 senha)"
fluxo 05a-entrar - -e EMAIL="$(c 5 email)" -e SENHA="$(c 5 senha)"
[ $NEGATIVO = 0 ] && apoio revogar 5
# "Rosto certo" a cada segundo enquanto o 05b roda: sem prompt aberto, o aviso se perde.
(while sleep 1; do xcrun simctl spawn "$UDID" notifyutil -p com.apple.BiometricKit_Sim.pearl.match; done) &
ROSTO=$!
fluxo 05b-revogada '"Sua sessão expirou. Entre de novo."'
kill $ROSTO

echo
echo "== ALVO=$ALVO $([ $NEGATIVO = 1 ] && echo negativo || echo positivo)"
printf '%s' "$RESUMO"
exit $FALHOU
