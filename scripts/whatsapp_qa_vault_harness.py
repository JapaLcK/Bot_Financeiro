#!/usr/bin/env python3
"""
Harness de QA do vault WhatsApp (piloto: 23 interações / 3 domínios).

O QUE FAZ
  - Cria um database Postgres isolado e descartável (`qa_wa_pilot_<hex>`),
    aponta DATABASE_URL pra ele, chama `db.init_db()` e SEMPRE derruba o
    banco no final (mesmo se algo quebrar no meio — try/finally).
  - Carrega OPENAI_API_KEY/OPENAI_MODEL do `.env` REAL do checkout original
    (não do worktree) sem deixar o DATABASE_URL de lá vazar por cima do
    isolado.
  - Dispara as 23 interações do piloto chamando
    `core.handle_incoming.handle_incoming()` DIRETO — o mesmo pipeline que
    uma mensagem real do WhatsApp percorre (classify → route → NLP →
    handlers) — SEM MOCKAR a IA. Ou seja: toda mensagem que cai em
    fallback de IA faz uma chamada real pra API da OpenAI, o que TEM
    CUSTO ($) e leva minutos pra rodar a suíte inteira.
  - Gera `docs/qa_whatsapp_pilot_<data>.md` com, para cada interação, os
    turnos reais (pergunta/resposta), veredito e checklist do vault.

COMO RODAR
    cd /Users/lucaskuramoti/Desktop/bot/bot_wa/.claude/worktrees/wa-qa-vault-pilot
    /Users/lucaskuramoti/Desktop/bot/bot_wa/.venv/bin/python \
        scripts/whatsapp_qa_vault_harness.py

PRÉ-REQUISITOS
  - Postgres local rodando em localhost:5432, aceitando conexão sem senha
    como usuário do SO (postgresql://localhost:5432/postgres).
  - `.venv` do checkout original (bot_wa/.venv) com psycopg, ofxparse,
    reportlab, pypdf, cryptography, python-dotenv etc.
  - `.env` real em bot_wa/.env com OPENAI_API_KEY válido.

NÃO é uma suíte pytest e não deve ser incluída na baseline de testes —
é uma ferramenta de QA exploratória, feita pra rodar sob demanda.
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
import uuid as _uuid
from datetime import datetime, timedelta

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CHECKOUT_ENV = "/Users/lucaskuramoti/Desktop/bot/bot_wa/.env"

if WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, WORKTREE_ROOT)

# ─────────────────────────────────────────────────────────────────────────
# 1) DB isolado e descartável
# ─────────────────────────────────────────────────────────────────────────
import psycopg  # noqa: E402

DB_NAME = f"qa_wa_pilot_{_uuid.uuid4().hex[:12]}"
_ADMIN_DSN = "postgresql://localhost:5432/postgres"

print(f"[harness] criando database isolado {DB_NAME} ...")
with psycopg.connect(_ADMIN_DSN, autocommit=True) as _conn:
    _conn.execute(f'create database "{DB_NAME}"')
os.environ["DATABASE_URL"] = f"postgresql://localhost:5432/{DB_NAME}"

# ─────────────────────────────────────────────────────────────────────────
# 2) Envs obrigatórias (mesmos valores/padrão de tests/conftest.py)
# ─────────────────────────────────────────────────────────────────────────
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-32-bytes")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("PII_HASH_PEPPER", "test-pepper-for-pytest-only-must-be-32-chars-long")
os.environ.setdefault("PII_AUDIT_DISABLED", "1")
os.environ.setdefault("RUN_BACKGROUND_TASKS", "0")
os.environ.setdefault("PLANS_V2_ENABLED", "0")
os.environ.pop("PAYWALL_ENABLED", None)

# ─────────────────────────────────────────────────────────────────────────
# 3) OPENAI_API_KEY do .env REAL (nunca do worktree), sem vazar DATABASE_URL
# ─────────────────────────────────────────────────────────────────────────
from dotenv import dotenv_values  # noqa: E402

_real_env = dotenv_values(REAL_CHECKOUT_ENV)
for _k in ("OPENAI_API_KEY", "OPENAI_MODEL"):
    if _real_env.get(_k):
        os.environ.setdefault(_k, _real_env[_k])

if not os.environ.get("OPENAI_API_KEY"):
    print("[harness] AVISO: OPENAI_API_KEY não encontrada — chamadas de IA vão falhar.")

# ─────────────────────────────────────────────────────────────────────────
# 3b) Medidor de chamadas OpenAI (custo + coluna comando × IA)
# ─────────────────────────────────────────────────────────────────────────
import scripts._ai_meter as meter  # noqa: E402

meter.install()

# Modelos que ESTE código usa de fato: o do .env (6 módulos leem a mesma env)
# e os dois fixos do media_service. Conferir antes vira erro de apelido aqui,
# em 1 segundo, em vez de no meio de uma cena com o banco já semeado.
_MODELOS_USADOS = [
    os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    "whisper-1",  # core/services/media_service.py:102
    "gpt-4o",     # core/services/media_service.py:187
]
MODEL_PROBLEMS = meter.check_models(_MODELOS_USADOS)
for _p in MODEL_PROBLEMS:
    print(f"[harness] MODELO: {_p}")
if not MODEL_PROBLEMS:
    print(f"[harness] modelos conferidos em /v1/models: {', '.join(_MODELOS_USADOS)}")

# ─────────────────────────────────────────────────────────────────────────
# 4) init_db()
# ─────────────────────────────────────────────────────────────────────────
from db import init_db  # noqa: E402

init_db()
print("[harness] schema inicializado.")

# ─────────────────────────────────────────────────────────────────────────
# Helpers de harness (dados pelo spec da tarefa)
# ─────────────────────────────────────────────────────────────────────────
from core.types import IncomingMessage  # noqa: E402
import core.handle_incoming as hi  # noqa: E402
import db  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# Guarda de afirmações: coleta o que as tools devolveram (a evidência que o
# modelo teve na mão) pra conferir número inventado na resposta.
# `_dispatch_tool` é o ÚNICO ponto por onde todo resultado de tool passa.
# ─────────────────────────────────────────────────────────────────────────
from core.services import ai_guard  # noqa: E402
import core.services.ai_chat.runner as _runner  # noqa: E402

# Espiona a PRÓPRIA função de produção em vez de redefinir aqui o que é
# "resposta escrita pelo modelo". A versão anterior contava entrada no
# `_run_tool_loop`, e o loop também retorna pelo `terminal_msg` — template
# determinístico de write auto-executado, que o modelo não escreveu. Aquilo
# inflava a contagem de afirmações de IA com confirmação gerada por código.
#
# `_log_unsupported_claims` é chamada exatamente no ramo em que o modelo
# escreveu o texto, e recebe as mensagens JÁ fatiadas por turno. Espionando
# ela, harness e produção não têm como divergir — que é o defeito que o Codex
# pegou na rodada anterior, quando eu tinha duas definições.
GUARD_CALLS: list[tuple[str, list[dict]]] = []
_orig_guard = _runner._log_unsupported_claims


def _guard_spy(user_id, reply, messages):
    GUARD_CALLS.append((reply, list(messages)))
    return _orig_guard(user_id, reply, messages)


_runner._log_unsupported_claims = _guard_spy


def msg(uid, text, platform="whatsapp"):
    return IncomingMessage(
        platform=platform, user_id=uid, text=text,
        message_id=str(_uuid.uuid4()), attachments=[], external_id=str(uid), raw={},
    )


def send(uid, text, platform="whatsapp") -> str:
    out = hi.handle_incoming(msg(uid, text, platform))
    return out[0].text if out else "(sem resposta — lista vazia)"


def new_free_uid() -> int:
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return uid


def new_pro_uid() -> int:
    uid = new_free_uid()
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) values (%s, %s, 'x', 'pro')",
                (uid, f"pro-{uid}@qa.local"),
            )
        conn.commit()
    return uid


def seed_card(user_id, name="Nubank", closing_day=10, due_day=17):
    card_id = db.create_card(user_id, name, closing_day=closing_day, due_day=due_day)
    db.set_default_card(user_id, card_id)
    return card_id


# ─────────────────────────────────────────────────────────────────────────
# Infraestrutura de registro / relatório
# ─────────────────────────────────────────────────────────────────────────

class Scenario:
    def __init__(self, name, domain, scenario_desc):
        self.name = name
        self.domain = domain
        self.scenario_desc = scenario_desc
        self.turns: list[tuple[str, str, list[dict], list]] = []  # (você, pigbank, chamadas, claims)
        self.checklist: list[tuple[bool | None, str]] = []
        self.veredict = "🔍"
        self.notes: list[str] = []
        self.exception = None

    def turn(self, uid, text, platform="whatsapp"):
        mark = meter.snapshot()
        gmark = len(GUARD_CALLS)
        try:
            resp = send(uid, text, platform=platform)
        except Exception as e:
            tb = traceback.format_exc()
            resp = f"(EXCEÇÃO: {e.__class__.__name__}: {e})"
            self.exception = tb
            self.veredict = "⚠️"
        # A marca é lida MESMO no caminho da exceção: chamada que já saiu foi
        # cobrada, e o turno que quebrou depois dela não é "comando".
        chamadas = meter.since(mark)
        # A guarda só faz sentido em resposta de MODELO. No caminho
        # determinístico não há tool nenhuma pra servir de evidência, e todo
        # ID viraria falso positivo — o código que imprime `#1` é o mesmo que
        # gravou a linha. Quem diz se o modelo escreveu é a produção, não eu.
        claims = []
        for resposta_do_modelo, msgs_do_turno in GUARD_CALLS[gmark:]:
            claims += ai_guard.check(resposta_do_modelo,
                                      ai_guard.tool_results(msgs_do_turno),
                                      user_text=text)
        self.turns.append((text, resp, chamadas, claims))
        return resp

    def check(self, ok, label):
        self.checklist.append((ok, label))

    def set_veredict(self, v):
        self.veredict = v

    def note(self, text):
        self.notes.append(text)


SCENARIOS: list[Scenario] = []
DISCREPANCIAS: list[str] = []


def extract_hash_id(text: str) -> str | None:
    m = re.search(r"#(\d+)", text)
    return m.group(1) if m else None


def extract_cc_code(text: str) -> str | None:
    m = re.search(r"\bCC(\d+)\b", text)
    return m.group(1) if m else None


def extract_pc_code(text: str) -> str | None:
    m = re.search(r"\bPC([0-9A-Fa-f]{8})\b", text)
    return m.group(1) if m else None


def has_all(text: str, *markers) -> bool:
    return all(m in text for m in markers)


_NOT_UNDERSTOOD_MARKERS = (
    "Não entendi exatamente",
    "Não entendi bem",
    "Não consegui identificar o valor",
    "Não entendi exatamente o que você quis fazer",
)


def looks_not_understood(text: str) -> bool:
    return any(m in text for m in _NOT_UNDERSTOOD_MARKERS)


def turn_with_retry(sc: "Scenario", uid, primary_text: str, alt_text: str, uid2=None):
    """Manda `primary_text`; se a resposta for claramente 'não entendi'/fallback
    genérico, tenta UMA variação (`alt_text`) e documenta as duas tentativas —
    política do CLAUDE.md (no máximo uma variação, nunca mais). Retorna a
    resposta que deve ser usada para checagens/continuação (a da retry, se
    houve; senão a original)."""
    r1 = sc.turn(uid, primary_text)
    if looks_not_understood(r1):
        r2 = sc.turn(uid, alt_text)
        sc.note(f"1ª tentativa \"{primary_text}\" não foi entendida; variação \"{alt_text}\" → {r2!r}")
        return r2
    return r1


def run_scenario(fn):
    """Decorator: roda o cenário, capturando exceção de nível-cenário (fora
    dos turnos individuais, ex: erro no seed) sem derrubar o harness."""
    sc = fn.__doc__  # placeholder, unused
    try:
        fn()
    except Exception:
        tb = traceback.format_exc()
        print(f"[harness] cenário {fn.__name__} quebrou fora dos turnos:\n{tb}")
    return fn


# ═══════════════════════════════════════════════════════════════════════
# DOMÍNIO: LANÇAMENTOS
# ═══════════════════════════════════════════════════════════════════════

def cena_1_registrar_despesa():
    sc = Scenario("1. Registrar despesa", "Lançamentos", "free (novo) — L1")
    SCENARIOS.append(sc)
    uid = new_free_uid()
    L1_UID[0] = uid

    r1 = sc.turn(uid, "gastei 50 no mercado")
    ok1 = ("Despesa registrada" in r1 or "💸" in r1) and "R$ 50,00" in r1 and "#" in r1
    sc.check(ok1, "\"gastei 50 no mercado\" → Despesa registrada/💸 + R$ 50,00 + #")

    r2 = sc.turn(uid, "pixei 40 pro joão")
    ok2 = "💸" in r2 and "R$ 40,00" in r2 and "#" in r2
    sc.check(ok2, "\"pixei 40 pro joão\" → 💸 + R$ 40,00 + #")

    r3 = sc.turn(uid, "paguei 80 de luz")
    ok3 = "💸" in r3 and "R$ 80,00" in r3
    sc.check(ok3, "\"paguei 80 de luz\" → 💸 + R$ 80,00 (sem desviar pra pagar boleto)")

    r4 = sc.turn(uid, "gastei 300 no ifood e 150 na farmácia")
    n_valores = len(re.findall(r"R\$\s?300,00", r4)) + len(re.findall(r"R\$\s?150,00", r4))
    ok4 = ("R$ 300,00" in r4) and ("R$ 150,00" in r4)
    sc.check(ok4, "\"gastei 300 no ifood e 150 na farmácia\" → DOIS lançamentos (300 e 150)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_2_registrar_receita():
    sc = Scenario("2. Registrar receita", "Lançamentos", "free (novo) — L2")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "recebi 2000 de salário")
    ok1 = "💰" in r1 and "R$ 2.000,00" in r1 and "#" in r1
    sc.check(ok1, "\"recebi 2000 de salário\" → 💰 + R$ 2.000,00 + #")

    r2 = turn_with_retry(sc, uid, "caiu 1500 na conta", "1500 caiu na conta")
    ok2 = "💰" in r2 and "R$ 1.500,00" in r2
    sc.check(ok2, "\"caiu 1500 na conta\" → 💰 + R$ 1.500,00")

    r3 = sc.turn(uid, "ganhei 300 de freela ontem")
    ok3 = "💰" in r3 and "R$ 300,00" in r3
    sc.check(ok3, "\"ganhei 300 de freela ontem\" → 💰 + R$ 300,00")

    r4 = sc.turn(uid, "500")
    is_receita_generica = "💰" in r4 and "R$ 500,00" in r4
    sc.check(not is_receita_generica, "\"500\" sozinho → NÃO tratado como receita direta (regressão)")
    sc.note(f"resposta real a \"500\" sozinho (após contexto de receitas): {r4!r}")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "🔍")


def cena_3_valor_primeiro_sem_verbo():
    sc = Scenario("3. Lançamento valor-primeiro sem verbo", "Lançamentos", "free (novo) — L3")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "77,90 mercado")
    ok1 = "💸" in r1 and "R$ 77,90" in r1
    sc.check(ok1, "\"77,90 mercado\" → 💸 + R$ 77,90")

    r2 = sc.turn(uid, "50 uber")
    ok2 = "💸" in r2 and "R$ 50,00" in r2
    sc.check(ok2, "\"50 uber\" → 💸 + R$ 50,00")

    r3 = sc.turn(uid, "500")
    ok3 = "EXCEÇÃO" not in r3
    sc.check(ok3, "\"500\" sozinho → não quebra (sem exceção)")
    sc.note(f"resposta real a \"500\" sozinho (sem descrição): {r3!r}")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else ("⚠️" if sc.exception else "🔍"))


def cena_4_lancamento_com_data():
    sc = Scenario("4. Lançamento com data", "Lançamentos", "free (novo) — L4")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "ontem gastei 50 no mercado")
    ok1 = "💸" in r1 and "R$ 50,00" in r1
    sc.check(ok1, "\"ontem gastei 50 no mercado\" → 💸 + R$ 50,00 + sinal de data")
    sc.note(f"sinal de data na resposta: {r1!r}")

    r2 = turn_with_retry(sc, uid, "dia 5 paguei 200 de academia", "paguei 200 de academia dia 5")
    ok2 = "💸" in r2 and "R$ 200,00" in r2
    sc.check(ok2, "\"dia 5 paguei 200 de academia\" → 💸 + R$ 200,00")

    r3 = sc.turn(uid, "03/04 comprei tênis 250")
    ok3 = "💸" in r3 and "R$ 250,00" in r3
    sc.check(ok3, "\"03/04 comprei tênis 250\" → 💸 + R$ 250,00")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_5_multi_lancamento():
    sc = Scenario("5. Multi-lançamento numa mensagem", "Lançamentos", "free (novo) — L5")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "gastei 500 no ifood e mais 800 no mercado")
    ok1 = "R$ 500,00" in r1 and "R$ 800,00" in r1
    sc.check(ok1, "\"gastei 500 no ifood e mais 800 no mercado\" → dois lançamentos (500 e 800)")

    r2 = sc.turn(uid, "paguei 50 uber, 30 café e o aluguel")
    ok2 = ("R$ 50,00" in r2 and "R$ 30,00" in r2 and
           ("aluguel" in r2.lower() and ("?" in r2 or "valor" in r2.lower())))
    sc.check(ok2, "\"paguei 50 uber, 30 café e o aluguel\" → registra uber+café e PERGUNTA valor do aluguel")

    r3 = sc.turn(uid, "1500")
    # A confirmação de sucesso mostra categoria, não a nota crua ("aluguel"
    # normalmente vira categoria "moradia") — exigir a palavra "aluguel" no
    # texto reprovava um sucesso real.
    ok3 = "R$ 1.500,00" in r3 and "Despesa registrada" in r3
    sc.check(ok3, "\"1500\" → completa lançamento de aluguel (R$ 1.500,00)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_6_valor_faltando():
    sc = Scenario("6. Lançamento com valor faltando", "Lançamentos", "free (novo) — L6 / L6b")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "gastei cinquenta")
    # Clarificação de verdade pede a descrição SEM já ter registrado o lançamento;
    # "Despesa registrada" no texto significa que ele lançou direto (categoria
    # vaga), não que perguntou — não conta como "?" de clarificação mesmo que a
    # mensagem de boas-vindas tenha um "?" solto no final.
    is_clarification = "R$ 50,00" in r1 and "Despesa registrada" not in r1 and "?" in r1
    sc.check(is_clarification, "\"gastei cinquenta\" → clarificação mencionando R$ 50,00 e pedindo descrição")
    if not is_clarification and "R$ 50,00" in r1:
        sc.note("Achado: \"gastei cinquenta\" NÃO pede clarificação — registra direto "
                 "(categoria 'outros', sem descrição), diferente do fluxo de clarificação "
                 "documentado no vault.")

    r2 = sc.turn(uid, "mercado")
    ok2 = "R$ 50,00" in r2 and "mercado" in r2.lower()
    sc.check(ok2, "\"mercado\" → completa R$ 50,00 + mercado")

    uid_b = new_free_uid()
    r3 = sc.turn(uid_b, "paguei o mercado")
    # "Não consegui identificar o valor. Tente: *gastei 50 no mercado*" também
    # contém "mercado"/"valor" — checagem antiga batia nessa mensagem de erro
    # por coincidência, sem provar que o bot perguntou de verdade.
    ok3 = ("mercado" in r3.lower() and "?" in r3
           and "não consegui identificar" not in r3.lower()
           and "Despesa registrada" not in r3)
    sc.check(ok3, "\"paguei o mercado\" (L6b) → clarificação pedindo valor, mencionando mercado")

    r4 = sc.turn(uid_b, "120")
    ok4 = "R$ 120,00" in r4 and "mercado" in r4.lower()
    sc.check(ok4, "\"120\" → completa R$ 120,00 + mercado")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_7_oferta_gasto_fixo():
    sc = Scenario("7. Oferta de gasto fixo", "Lançamentos", "free (novo) — L7")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    # Semeia lançamento do "mês passado" direto no banco (não via handle_incoming)
    sc.note("Passo de setup SEMEADO DIRETO NO BANCO (não via handle_incoming): "
            "add_launch_and_update_balance + UPDATE launches SET criado_em = mês anterior.")
    # nota="netflix" (não "netflix 44,90"): merchant_key() precisa bater com a
    # descrição que o parser da mensagem viva vai extrair ("netflix 44,90" →
    # alvo/nota "netflix", sem o valor).
    launch_id, user_seq, _saldo = db.add_launch_and_update_balance(
        uid, "despesa", 44.90, "netflix", "netflix",
    )
    prev_month = (datetime.now().replace(day=1) - timedelta(days=1))
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set criado_em = %s where id = %s",
                (prev_month.replace(hour=12, minute=0, second=0, microsecond=0), launch_id),
            )
        conn.commit()

    r1 = turn_with_retry(sc, uid, "netflix 44,90", "gastei 44,90 na netflix")
    ok1 = "R$ 44,90" in r1 and ("gasto fixo" in r1.lower() and "?" in r1)
    sc.check(ok1, "\"netflix 44,90\" → registra lançamento E pergunta oferta de gasto fixo (sim/não)")
    sc.note(f"resposta completa do lançamento+oferta: {r1!r}")

    r2 = sc.turn(uid, "sim")
    ok2 = "gasto fixo" in r2.lower() or "recorrente" in r2.lower() or "pro" in r2.lower()
    sc.check(ok2, "\"sim\" → confirma criação do recorrente (ou gate Pro-only, documentar o que sair)")
    sc.note(f"resposta a 'sim': {r2!r}")

    sc.set_veredict("✅" if (ok1 and ok2) else "🔍")


def cena_8_listar_historico():
    sc = Scenario("8. Listar lançamentos e histórico", "Lançamentos", "free — reusa uid de L1")
    SCENARIOS.append(sc)
    uid = L1_UID[0]

    r1 = sc.turn(uid, "meus gastos")
    ok1 = "🧾" in r1 and "#" in r1 and ("gasto" in r1.lower() or "receita" in r1.lower())
    sc.check(ok1, "\"meus gastos\" → 🧾 + lista com # + resumo Gastos/Receitas")

    r2 = sc.turn(uid, "gastos hoje")
    ok2 = "EXCEÇÃO" not in r2
    sc.check(ok2, "\"gastos hoje\" → resposta filtrada, sem exceção")

    r3 = sc.turn(uid, "extrato")
    ok3 = "EXCEÇÃO" not in r3
    sc.check(ok3, "\"extrato\" → mesma listagem geral")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_9_quanto_gastei():
    sc = Scenario("9. Quanto gastei — consulta de total", "Lançamentos", "free — reusa uid de L1; turno 2 usa pro (novo)")
    SCENARIOS.append(sc)
    uid = L1_UID[0]

    r1 = sc.turn(uid, "quanto gastei essa semana")
    ok1 = "💸 Você gastou" in r1 and "R$" in r1
    sc.check(ok1, "\"quanto gastei essa semana\" → 💸 Você gastou + total em R$ (não lista item a item)")

    # "qto" (gíria) só é entendida via fallback de IA — e esse fallback é
    # Pro-only (handle_incoming.py:678-680 documenta "qto sobrou" como o
    # exemplo canônico disso). Testar com Free sempre dá "não entendi" por
    # desenho do produto, não por bug — usa um Pro novo com um gasto de ontem
    # já registrado.
    pro_uid = new_pro_uid()
    sc.turn(pro_uid, "ontem gastei 90 no mercado")
    r2 = sc.turn(pro_uid, "qto gastei ontem")
    ok2 = "EXCEÇÃO" not in r2 and "R$" in r2
    sc.check(ok2, "\"qto gastei ontem\" (Pro, via fallback de IA) → total do dia anterior")
    sc.note(f"resposta a 'qto gastei ontem' (Pro): {r2!r}")
    if "não teve gastos" in r2.lower() or "nao teve gastos" in r2.lower():
        DISCREPANCIAS.append(
            "Item 9: 'qto gastei ontem' respondeu 'você não teve gastos ontem' com um gasto real "
            "seedado pra ontem (via 'ontem gastei 90 no mercado', mesmo user, turno anterior). "
            "CAUSA RAIZ (diagnosticada aqui; CORRIGIDA depois em utils_date.align_process_tz, "
            "que escreve PGTZ = fuso do app e põe a sessão de TODA conexão libpq nesse fuso): na "
            "época a sessão do Postgres não tinha timezone fixado em lugar nenhum do app. O app "
            "grava datas relativas ('ontem') usando America/Sao_Paulo (utils_date.today_tz()), "
            "mas ao ler de volta um `timestamptz`, "
            "psycopg reinterpreta o instante sob o timezone DA SESSÃO — nesta máquina, "
            "America/New_York (herdado do SO, `SHOW timezone` confirma). Medido direto: um "
            "lançamento gravado com criado_em='2026-08-19 00:00 -03:00' (correto, meia-noite em "
            "São Paulo) volta do banco como '2026-08-18 23:00 -04:00' — um dia inteiro mais cedo. "
            "Isso não é específico da IA: qualquer código que compara `criado_em.date()` (relatórios, "
            "'hoje'/'ontem', vencimento de fatura, contas a pagar) está sujeito ao mesmo erro sempre "
            "que o timezone da sessão do Postgres divergir de America/Sao_Paulo o suficiente pra "
            "cruzar meia-noite. A produção TINHA o problema: a sessão do Railway era `Etc/UTC` "
            "(medido em 27/08/2026 08:46 UTC), a pior faixa possível para o -03 de São Paulo."
        )

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "🔍")


def cena_10_apagar_por_id():
    sc = Scenario("10. Apagar lançamento por ID", "Lançamentos", "free (novo) — L10")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "gastei 50 no mercado")
    n = extract_hash_id(r1)
    sc.note(f"#N capturado: {n}")

    r2 = sc.turn(uid, f"apagar #{n}" if n else "apagar #999999")
    ok2 = "⚠️" in r2 and ("sim" in r2.lower() and "não" in r2.lower())
    sc.check(ok2, "\"apagar #N\" → ⚠️ pede confirmação sim/não, NÃO apaga ainda")

    r3 = sc.turn(uid, "sim")
    ok3 = "✅" in r3 and "apagado" in r3.lower()
    sc.check(ok3, "\"sim\" → confirma apagado + saldo atualizado (vault esperava 🗑️)")
    if "🗑️" not in r3 and ok3:
        DISCREPANCIAS.append(
            "Itens 10/11/12: a confirmação de apagar UM lançamento (pending.py, action_type "
            "delete_launch) responde com '✅ Lançamento **#N** apagado e saldo revertido.' — "
            "NÃO usa 🗑️. O emoji 🗑️ só aparece nas confirmações de apagar compra/parcelamento "
            "no cartão (core/handlers/credit.py). A nota do vault que espera 🗑️ para apagar "
            "lançamento (item 10) e para 'desfazer' (item 12) está desalinhada com o código atual."
        )

    # Guarda anti-órfã
    r4 = sc.turn(uid, "gastei 30 no cafe")
    m = extract_hash_id(r4)
    sc.note(f"#M capturado: {m}")
    sc.turn(uid, f"apagar #{m}" if m else "apagar #999998")  # arma pendência
    r6 = sc.turn(uid, "saldo")
    ok6 = "EXCEÇÃO" not in r6 and "apagar" not in r6.lower()
    sc.check(ok6, "\"saldo\" (comando claro) → resposta de saldo normal, NÃO apaga #M")
    r7 = sc.turn(uid, "meus gastos")
    ok7 = bool(m) and (f"#{m}" in r7)
    sc.check(ok7, "\"meus gastos\" depois → #M ainda aparece (guarda anti-órfã funcionou)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


L1_UID = [None]


def cena_11_apagar_varios():
    sc = Scenario("11. Apagar vários lançamentos", "Lançamentos", "free (novo) — L11")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    ra = sc.turn(uid, "gastei 50 uber")
    a = extract_hash_id(ra)
    rb = sc.turn(uid, "gastei 30 cafe")
    b = extract_hash_id(rb)
    sc.note(f"#A={a} #B={b}")

    r_del = sc.turn(uid, f"apagar #{a} e #{b}" if a and b else "apagar #999997 e #999996")
    ok_del = "⚠️" in r_del and (str(a) in r_del if a else True) and (str(b) in r_del if b else True)
    sc.check(ok_del, "\"apagar #A e #B\" → ⚠️ listando os dois, pede confirmação")

    r_sim = sc.turn(uid, "sim")
    ok_sim = "✅" in r_sim and "apagad" in r_sim.lower()
    sc.check(ok_sim, "\"sim\" → confirma os DOIS apagados (vault esperava 🗑️; ver discrepância do item 10)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_12_desfazer():
    sc = Scenario("12. Desfazer último lançamento", "Lançamentos", "free (novo) — L12")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r1 = sc.turn(uid, "gastei 50 no mercado")
    n = extract_hash_id(r1)
    sc.note(f"#N capturado: {n}")

    r2 = sc.turn(uid, "desfazer")
    pede_confirmacao = "⚠️" in r2 and "sim" in r2.lower() and "não" in r2.lower()
    sc.note(f"resposta a 'desfazer': {r2!r}")
    if pede_confirmacao:
        r3 = sc.turn(uid, "sim")
        ok2 = bool(n) and (n in r3) and ("✅" in r3 or "↩️" in r3)
        sc.note(f"resposta ao 'sim' subsequente: {r3!r}")
        DISCREPANCIAS.append(
            "Item 12: 'desfazer' arma uma pendência de confirmação (⚠️ ... sim/não) em vez de "
            "desfazer direto num turno só como ↩️ — comportamento intencional (mesma cautela que "
            "toda ação destrutiva tem neste bot); a nota do vault está incompleta, faltando o turno "
            "de confirmação."
        )
    else:
        ok2 = "↩️" in r2 and bool(n) and (n in r2)
    sc.check(ok2, "\"desfazer\" → confirma e desfaz #N (pode exigir sim/não antes, ver discrepância)")

    sc.set_veredict("✅" if ok2 else "❌")


# ═══════════════════════════════════════════════════════════════════════
# DOMÍNIO: CARTÃO DE CRÉDITO
# ═══════════════════════════════════════════════════════════════════════

def cena_13_cadastrar_cartao():
    sc = Scenario("13. Cadastrar cartão", "Cartão de Crédito",
                  "free (novo) C13a one-shot; free (novo) C13b guiado; pro (novo) C13c duplicado; C13a gate")
    SCENARIOS.append(sc)

    uid_a = new_free_uid()
    r1 = sc.turn(uid_a, "criar cartao Nubank fecha 10 vence 17")
    ok1 = "✅" in r1 and "Nubank" in r1 and ("⭐" in r1 or "principal" in r1.lower())
    sc.check(ok1, "one-shot \"criar cartao Nubank fecha 10 vence 17\" → ✅ + Nubank + indicador de principal")
    # O one-shot arma um pending credit_card_setup (pergunta de lembrete/limite).
    # Se não resolvido, a próxima mensagem de uid_a (o teste de gate, mais abaixo)
    # seria capturada por esse pending em vez de rodar o fluxo normal de criação
    # — resolve aqui pra não contaminar o teste do gate.
    sc.turn(uid_a, "não")  # lembretes: não
    sc.turn(uid_a, "não")  # limite: não → finaliza o setup, limpa o pending

    uid_b = new_free_uid()
    r2a = sc.turn(uid_b, "cadastrar cartão")
    r2b = sc.turn(uid_b, "Inter")
    r2c = sc.turn(uid_b, "25")
    r2d = sc.turn(uid_b, "2")
    r2e = sc.turn(uid_b, "não")
    r2f = sc.turn(uid_b, "3000")
    ok2 = "✅" in r2f and "Inter" in r2f
    sc.check(ok2, "fluxo guiado (nome→fechamento→vencimento→lembretes→limite) → ✅ Inter criado")
    sc.note("respostas do fluxo guiado: " + " | ".join(repr(x) for x in (r2a, r2b, r2c, r2d, r2e, r2f)))

    uid_c = new_pro_uid()
    sc.turn(uid_c, "criar cartao Nubank fecha 10 vence 17")
    r3 = sc.turn(uid_c, "criar cartao Nubank")
    sc.note(f"resposta a nome duplicado (Pro): {r3!r}")
    sc.check(None, "nome duplicado (Pro, sem diálogo completo documentado no vault) — ver nota")

    r4 = sc.turn(uid_a, "criar cartao Outro fecha 1 vence 10")
    ok4 = ("upgrade" in r4.lower() or "plano pago" in r4.lower()) and "Outro" not in r4
    sc.check(ok4, "gate Free (já tem 1 cartão) → \"criar cartao Outro...\" bloqueado / indicando limite")
    sc.note(f"resposta ao gate Free: {r4!r}")

    core_ok = ok1 and ok2 and ok4
    sc.set_veredict("✅" if core_ok else "❌")


def cena_14_comprar_credito():
    sc = Scenario("14. Registrar compra no crédito", "Cartão de Crédito", "pro (novo) — C14")
    SCENARIOS.append(sc)
    uid = new_pro_uid()
    card_id = seed_card(uid, "Nubank")
    C14_STATE["uid"] = uid
    C14_STATE["card_id"] = card_id

    r1 = sc.turn(uid, "gastei 150 no cartão Nubank")
    ok1 = "✅" in r1 and "R$ 150,00" in r1 and "CC" in r1 and (
        "Compra no Crédito Registrada" in r1 or "compra no crédito registrada" in r1.lower())
    sc.check(ok1, "\"gastei 150 no cartão Nubank\" → ✅ + Compra no Crédito Registrada + R$ 150,00 + CC<n>")
    cc1 = extract_cc_code(r1)
    sc.note(f"CC capturado (150 nubank): {cc1}")

    r2 = sc.turn(uid, "credito 60 ifood")
    ok2 = "✅" in r2 and "CC" in r2
    sc.check(ok2, "\"credito 60 ifood\" → ✅ + CC<n>")
    cc2 = extract_cc_code(r2)
    sc.note(f"CC capturado (60 ifood): {cc2}")
    C14_STATE["cc_ifood"] = cc2

    db.set_card_limit(uid, card_id, 100.0)
    r3 = sc.turn(uid, "comprei 9000 no crédito")
    ok3 = "❌" in r3 or "limite" in r3.lower()
    sc.check(ok3, "\"comprei 9000 no crédito\" com limite baixo → BLOQUEADO (❌/limite)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


C14_STATE: dict = {}


def cena_15_parcelamento():
    sc = Scenario("15. Registrar parcelamento", "Cartão de Crédito", "pro — reusa uid de C14")
    SCENARIOS.append(sc)
    uid = C14_STATE["uid"]
    db.set_card_limit(uid, C14_STATE["card_id"], 100000.0)
    sc.note("Limite do cartão Nubank resetado pra R$ 100.000,00 antes deste item — o item 14 "
             "tinha deixado um limite baixo (R$ 100,00) travado de propósito pra testar o "
             "bloqueio de compra à vista; sem o reset, este item ficava contaminado por esse "
             "estado e não testava compreensão de linguagem de verdade.")

    r1 = sc.turn(uid, "parcelar 600 em 3x no Nubank")
    ok1 = "?" in r1 and ("nome" in r1.lower() or "descri" in r1.lower())
    sc.check(ok1, "\"parcelar 600 em 3x no Nubank\" → pergunta o nome da compra")

    r2 = sc.turn(uid, "geladeira")
    ok2 = "✅" in r2 and "Parcelamento Registrado" in r2 and "3x de R$ 200,00" in r2 and "PC" in r2
    sc.check(ok2, "\"geladeira\" → ✅ Parcelamento Registrado + 3x de R$ 200,00 + PC<código>")
    pc1 = extract_pc_code(r2)
    sc.note(f"PC capturado (geladeira): {pc1}")
    C14_STATE["pc_geladeira"] = pc1

    r3 = sc.turn(uid, "parcelei 12x de 79,90 celular")
    ok3 = "✅" in r3 and "Parcelamento Registrado" in r3 and "12x de R$ 79,90" in r3 and "PC" in r3
    sc.check(ok3, "\"parcelei 12x de 79,90 celular\" direto → ✅ Parcelamento Registrado + 12x de R$ 79,90 + PC")
    pc2 = extract_pc_code(r3)
    sc.note(f"PC capturado (celular): {pc2}")
    C14_STATE["pc_celular"] = pc2

    # Indexa em vez de desempacotar: `turns` ganhou campos (chamadas, claims) e
    # o `for _, r in` daqui quebrava a cena inteira a cada campo novo.
    limit_blocked = any("excede o limite" in t[1].lower() for t in sc.turns)
    if limit_blocked and not all(x[0] for x in sc.checklist):
        sc.set_veredict("🔍")
        sc.note("Veredito 🔍 em vez de ❌: bloqueio por limite (ver aviso acima), não erro de "
                 "compreensão — precisa de re-teste com limite alto pra separar as duas causas.")
    else:
        sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_16_ver_parcelamentos():
    sc = Scenario("16. Ver parcelamentos ativos", "Cartão de Crédito", "pro — reusa uid de C14")
    SCENARIOS.append(sc)
    uid = C14_STATE["uid"]
    if not C14_STATE.get("pc_geladeira") and not C14_STATE.get("pc_celular"):
        sc.note("Nenhum parcelamento foi criado no item 15 (bloqueado pelo limite herdado "
                 "do item 14) — esta lista vazia é consequência disso, não um teste limpo "
                 "de listagem.")

    r1 = sc.turn(uid, "parcelamentos")
    # O código usa 📦, não 📆 (o vault documenta 📆) — divergência de emoji na
    # doc, não bug funcional; ver seção de discrepâncias no topo do relatório.
    ok1 = "📦" in r1 and "geladeira" in r1.lower() and "celular" in r1.lower() and "PC" in r1
    sc.check(ok1, "\"parcelamentos\" → 📦 (vault documenta 📆) + lista com geladeira e celular + códigos PC")
    if ok1:
        DISCREPANCIAS.append(
            "Item 16: 'parcelamentos' responde com 📦 (core/handlers/credit.py), não 📆 como "
            "o vault documenta. Cosmético — descrição, valores e códigos PC batem certinho."
        )

    if not ok1 and not C14_STATE.get("pc_geladeira") and not C14_STATE.get("pc_celular"):
        sc.set_veredict("🔍")
    else:
        sc.set_veredict("✅" if ok1 else "❌")


def cena_17_fatura():
    sc = Scenario("17. Ver fatura do cartão", "Cartão de Crédito", "pro — reusa uid de C14")
    SCENARIOS.append(sc)
    uid = C14_STATE["uid"]

    r1 = sc.turn(uid, "fatura Nubank")
    ok1 = "💳" in r1 and "Fatura" in r1 and "Total" in r1 and "Em aberto" in r1
    sc.check(ok1, "\"fatura Nubank\" → 💳 + Fatura + Total + Em aberto")

    r2 = sc.turn(uid, "faturas")
    ok2 = "Nubank" in r2
    sc.check(ok2, "\"faturas\" → lista de faturas abertas incluindo Nubank")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_18_limite():
    sc = Scenario("18. Ver e definir limite do cartão", "Cartão de Crédito", "pro — reusa uid de C14")
    SCENARIOS.append(sc)
    uid = C14_STATE["uid"]

    r1 = sc.turn(uid, "limite nubank")
    ok1 = "EXCEÇÃO" not in r1 and ("limite" in r1.lower())
    sc.check(ok1, "\"limite nubank\" → mostra limite/usado/disponível")

    r2 = sc.turn(uid, "definir limite Nubank 8000")
    if "❓" in r2 or "não encontrei" in r2.lower() or "nao encontrei" in r2.lower():
        r2b = sc.turn(uid, "limite nubank 8000")
        sc.note(f"1ª tentativa \"definir limite Nubank 8000\" não achou o cartão; "
                 f"variação \"limite nubank 8000\" → {r2b!r}")
        r2 = r2b
    ok2 = "✅" in r2 and "R$ 8.000,00" in r2
    sc.check(ok2, "\"definir limite Nubank 8000\" → ✅ + novo limite R$ 8.000,00")

    r3 = sc.turn(uid, "limite nubank")
    ok3 = "8.000,00" in r3 or "8000" in r3
    sc.check(ok3, "\"limite nubank\" de novo → reflete 8000")
    if not ok3:
        DISCREPANCIAS.append(
            "Item 18: 'limite nubank' não bate em NENHUM regex determinístico de "
            "credit.handle (só 'limite do <cartão>'/'quanto...limite'/etc, não "
            "'limite <nome>' solto) — cai na IA conversacional (o formato de "
            "resposta com '🐷 Seu limite do X tá assim' não é o template "
            "determinístico de credit.py). O UPDATE em si funciona e persiste "
            "(confirmado direto no banco), mas a IA respondeu de novo com o valor "
            "ANTIGO (R$ 100.000,00) depois do 'definir limite' já ter gravado "
            "R$ 8.000,00 — parece responder repetindo/parafraseando a resposta "
            "anterior da conversa em vez de rebuscar o dado fresco. Achado "
            "separado do bug de resolução de nome (esse já corrigido em "
            "db/cards.py::get_card_id_by_name) — mais profundo, na camada de "
            "IA/tool-calling, não investigado a fundo aqui."
        )

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_19_principal():
    sc = Scenario("19. Listar e definir cartão principal", "Cartão de Crédito", "pro (novo) — C19")
    SCENARIOS.append(sc)
    uid = new_pro_uid()
    seed_card(uid, "Nubank", 10, 17)
    inter_id = db.create_card(uid, "Inter", closing_day=25, due_day=2)
    C19_STATE["uid"] = uid
    C19_STATE["inter_id"] = inter_id

    r1 = sc.turn(uid, "cartoes")
    ok1 = "Nubank" in r1 and "Inter" in r1
    sc.check(ok1, "\"cartoes\" → lista os dois, marcação só no Nubank")
    sc.note(f"resposta 'cartoes': {r1!r}")

    r2 = sc.turn(uid, "padrao Inter")
    pediu_confirmacao = "sim" in r2.lower() and "não" in r2.lower() and "?" in r2
    sc.note(f"resposta a 'padrao Inter': {r2!r}")
    if pediu_confirmacao:
        r2b = sc.turn(uid, "sim")
        ok2 = "✅" in r2b and "Inter" in r2b and "principal" in r2b.lower()
        sc.note(f"resposta ao 'sim' subsequente: {r2b!r}")
    else:
        ok2 = False
        DISCREPANCIAS.append(
            "Item 19: 'padrao Inter' trocou o cartão principal DIRETO, sem perguntar sim/não — "
            "diverge do vault e da decisão do dono do produto (deveria confirmar)."
        )
    sc.check(pediu_confirmacao, "\"padrao Inter\" → pede confirmação sim/não antes de trocar (fix aplicado em core/handlers/credit.py)")
    sc.check(ok2, "\"sim\" → confirma a troca de principal")

    r3 = sc.turn(uid, "meu Nubank vence quando")
    ok3 = "17" in r3
    sc.check(ok3, "\"meu Nubank vence quando\" → dia 17")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


C19_STATE: dict = {}


def cena_20_excluir_cartao():
    sc = Scenario("20. Excluir cartão", "Cartão de Crédito", "pro — reusa uid de C19, cartão Inter")
    SCENARIOS.append(sc)
    uid = C19_STATE["uid"]

    r1 = sc.turn(uid, "excluir cartao Inter")
    ok1 = "⚠️" in r1 and "sim" in r1.lower() and "não" in r1.lower()
    sc.check(ok1, "\"excluir cartao Inter\" → ⚠️ pedindo confirmação sim/não")

    r2 = sc.turn(uid, "não")
    ok2 = "EXCEÇÃO" not in r2
    sc.check(ok2, "\"não\" → confirma cancelamento, Inter NÃO foi removido")
    sc.note(f"resposta ao 'não': {r2!r}")

    r3 = sc.turn(uid, "cartoes")
    ok3 = "Inter" in r3
    sc.check(ok3, "\"cartoes\" depois → Inter ainda aparece")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_21_apagar_compra_parcelamento():
    sc = Scenario("21. Apagar compra ou parcelamento", "Cartão de Crédito", "pro — reusa uid de C14")
    SCENARIOS.append(sc)
    uid = C14_STATE["uid"]
    cc = C14_STATE.get("cc_ifood")
    pc = C14_STATE.get("pc_geladeira")
    sc.note(f"usando CC={cc} (compra ifood) e PC={pc} (parcelamento geladeira)")

    r1 = sc.turn(uid, f"apagar CC{cc}" if cc else "apagar CC999999")
    ok1 = "🗑️" in r1
    sc.check(ok1, "\"apagar CC<código>\" → 🗑️ confirmando compra apagada")

    r2 = sc.turn(uid, f"apagar PC{pc}" if pc else "apagar PC99999999")
    ok2 = "🗑️" in r2
    sc.check(ok2, "\"apagar PC<código>\" → 🗑️ confirmando parcelamento desfeito")
    if not pc:
        sc.note("PC não capturado no item 15 (parcelamento bloqueado pelo limite herdado do "
                 "item 14) — este turno usou um código inexistente de propósito só pra "
                 "registrar a resposta de 'não achei'; não testa o apagar de verdade.")

    if not pc and not ok2:
        sc.set_veredict("🔍" if ok1 else "❌")
    else:
        sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


# ═══════════════════════════════════════════════════════════════════════
# DOMÍNIO: CONFIRMAÇÕES E PENDÊNCIAS
# ═══════════════════════════════════════════════════════════════════════

def cena_22_sim_nao():
    sc = Scenario("22. Responder sim ou não (sinônimos)", "Confirmações/Pendências",
                  "free (novo) — P22; free (novo) — P22b")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r0 = sc.turn(uid, "gastei 20 no busao")
    n = extract_hash_id(r0)
    sc.turn(uid, f"apagar #{n}" if n else "apagar #999995")
    r_pode = sc.turn(uid, "pode")
    ok_pode = "✅" in r_pode and "apagado" in r_pode.lower()
    sc.check(ok_pode, "\"pode\" (sinônimo de sim) → confirma apagamento igual a 'sim' (✅, não 🗑️ — ver item 10)")

    uid_b = new_free_uid()
    r_sim_solto = sc.turn(uid_b, "sim")
    ok_sim_solto = "EXCEÇÃO" not in r_sim_solto
    sc.check(ok_sim_solto, "\"sim\" sem pendência armada (uid totalmente novo) → não quebra")
    sc.note(f"resposta a 'sim' sem pendência: {r_sim_solto!r}")

    rm = sc.turn(uid, "gastei 30 no cafe")
    m = extract_hash_id(rm)
    sc.turn(uid, f"apagar #{m}" if m else "apagar #999994")
    r_nope = sc.turn(uid, "nope")
    ok_nope = "EXCEÇÃO" not in r_nope and "apagado" not in r_nope.lower() and "cancel" in r_nope.lower()
    sc.check(ok_nope, "\"nope\" (sinônimo de não) → cancela igual a 'não', #M continua existindo")
    r_check = sc.turn(uid, "meus gastos")
    ok_check = bool(m) and f"#{m}" in r_check
    sc.check(ok_check, "confirma que #M ainda existe após 'nope'")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


def cena_23_pendencia_abandonada():
    sc = Scenario("23. Pendência abandonada por novo comando", "Confirmações/Pendências", "free (novo) — P23")
    SCENARIOS.append(sc)
    uid = new_free_uid()

    r0 = sc.turn(uid, "gastei 40 no mercado")
    n = extract_hash_id(r0)
    sc.turn(uid, f"apagar #{n}" if n else "apagar #999993")

    r_saldo = sc.turn(uid, "saldo")
    ok_saldo = "EXCEÇÃO" not in r_saldo and "apagar" not in r_saldo.lower()
    sc.check(ok_saldo, "\"saldo\" → responde saldo normalmente (não trata como confirmação)")

    r_check = sc.turn(uid, "meus gastos")
    ok_check = bool(n) and f"#{n}" in r_check
    sc.check(ok_check, "\"meus gastos\" depois → #N NÃO foi apagado (guarda anti-órfã)")

    sc.set_veredict("✅" if all(x[0] for x in sc.checklist) else "❌")


# ═══════════════════════════════════════════════════════════════════════
# Execução sequencial e determinística
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# PARES COMANDO × IA
#
# As 23 cenas acima quase não exercitam o modelo: medido, 84 dos 90 turnos
# não fazem chamada nenhuma, e só 2 respostas foram ESCRITAS pela IA. Elas
# cobrem o mesmo caminho determinístico que o pytest já cobre de graça.
#
# Aqui a pergunta é outra: a mesma intenção, dita CURTA e dita SOLTA, chega no
# mesmo lugar? Não escrevo resultado esperado à mão — é o que tornaria isto
# caro e frágil. A asserção é que os dois lados afirmam os mesmos valores.
#
# A forma curta TENDE a ser determinística e servir de gabarito, mas não é
# garantido e o harness não assume: qual caminho cada lado tomou sai MEDIDO no
# rótulo do turno. Medido em 2026-09-02, o par "Gasto por categoria" inverteu —
# "gastos com saúde" foi pra IA e "quanto eu gastei com remedio e farmacia"
# ficou no determinístico.
#
# Dois usuários Pro com seed IDÊNTICO, um pra cada lado: mesma base de dados,
# duas formas de perguntar. Se divergirem, ou a IA alucinou ou achou um bug —
# foi assim que a cena 18 apareceu.
# ═══════════════════════════════════════════════════════════════════════

SEED_PAR = (
    "gastei 50 no mercado",
    "gastei 123,45 na farmacia",
    "recebi 2000 de salario",
    "gastei 89,90 com uber",
)

# (rótulo, forma CURTA, forma SOLTA como o usuário falaria)
PARES = [
    ("Saldo",            "saldo",             "qto sobrou pra mim"),
    ("Saldo (2)",        "saldo",             "quanto eu tenho de dinheiro agora"),
    ("Extrato",          "extrato",           "me mostra meus ultimos lancamentos"),
    ("Gasto do mês",     "quanto gastei",     "quanto eu torrei esse mes"),
    ("Gasto do mês (2)", "quanto gastei",     "somando tudo, quanto saiu da minha conta"),
    ("Registrar gasto",  "gastei 30 no ifood", "torrei trinta reais no ifood hoje"),
    ("Registrar receita", "recebi 500 freela", "caiu quinhentos de freela na conta"),
    ("Gasto por categoria", "gastos com saúde", "quanto eu gastei com remedio e farmacia"),
    ("Limite do cartão", "limite nubank",     "quanto ainda posso gastar no nubank"),
    ("Fatura",           "fatura nubank",     "quanto ta a fatura do nubank"),
    ("Listar cartões",   "cartoes",           "quais cartoes eu tenho cadastrados"),
]


def _seed_par(com_cartao: bool) -> int:
    """Usuário Pro com base idêntica dos dois lados. Semeia pelo caminho de
    comando de propósito: é determinístico e (quase) não gasta chamada."""
    uid = new_pro_uid()
    for t in SEED_PAR:
        send(uid, t)
    if com_cartao:
        seed_card(uid)
        send(uid, "comprei 200 no nubank")
    return uid


def roda_pares():
    precisa_cartao = ("nubank", "cartoes", "fatura", "cartao")
    for rotulo, cmd, ia in PARES:
        com_cartao = any(k in (cmd + " " + ia).lower() for k in precisa_cartao)
        sc = Scenario(f"PAR — {rotulo}", "comando × IA",
                      "dois users Pro com seed idêntico; caminho de cada lado é medido")
        try:
            uid_c = _seed_par(com_cartao)
            uid_i = _seed_par(com_cartao)
        except Exception:
            sc.exception = traceback.format_exc()
            sc.set_veredict("⚠️")
            SCENARIOS.append(sc)
            continue

        r_cmd = sc.turn(uid_c, cmd)
        r_ia = sc.turn(uid_i, ia)

        v_cmd = ai_guard.money_cents(r_cmd)
        v_ia = ai_guard.money_cents(r_ia)
        inventados = v_ia - v_cmd

        sem_invencao = not inventados
        trouxe_dado = bool(v_ia) or not v_cmd
        faltando = v_cmd - v_ia
        sc.check(sem_invencao, "todo valor da forma solta aparece na resposta da forma curta")
        sc.check(trouxe_dado, "a forma solta trouxe o dado numérico que a curta traz")
        # Terceiro item, e NÃO entra no veredito: exigir todos os valores
        # reprovaria resposta legítima. "limite nubank" devolve total, usado e
        # disponível; "quanto posso gastar" responder só o disponível é uma
        # resposta boa, não um defeito. Mas `trouxe_dado` sozinho passa com UM
        # valor de três, então a omissão precisa aparecer em algum lugar —
        # aqui, como `[?]` no checklist, pra quem lê o relatório julgar.
        sc.check(None if faltando else True,
                 "a forma solta trouxe TODOS os valores da curta (informativo)")

        claims_ia = sc.turns[-1][3]
        guarda_ok = all(c.supported for c in claims_ia)
        sc.check(guarda_ok, "guarda: nenhum número da IA veio de fora das tools")

        if inventados:
            sc.note("valores que só a forma solta afirma: "
                     + ", ".join(f"R$ {v/100:.2f}" for v in sorted(inventados)))
        # Máquina de estados do veredito, enumerada de uma vez (CLAUDE.md §4:
        # dois achados no mesmo subsistema = parar de remendar). Dois estados
        # davam ✅ sem terem comparado nada:
        #   - chamada de IA FALHOU nos dois lados → tudo vazio → passava;
        #   - nenhum dos lados afirmou valor → nada foi medido → passava.
        chamadas_do_par = sc.turns[-2][2] + sc.turns[-1][2]
        falhas_openai = sorted({c["erro"] for c in chamadas_do_par if c.get("erro")})
        sc.check(not falhas_openai, "nenhuma chamada à OpenAI falhou neste par")

        if faltando:
            sc.note("valores que a forma curta traz e a solta não: "
                     + ", ".join(f"R$ {v/100:.2f}" for v in sorted(faltando)))
        if v_cmd and not v_ia:
            sc.note(f"a forma curta trouxe {len(v_cmd)} valor(es) e a solta não trouxe nenhum")
        if falhas_openai:
            # Não é ❌: ❌ acusa o produto, e aqui quem quebrou foi a chamada.
            sc.set_veredict("⚠️")
            sc.note("chamada à OpenAI falhou (" + ", ".join(falhas_openai)
                     + ") — o par não comparou nada, o resultado não vale")
        elif not v_cmd and not v_ia:
            sc.set_veredict("🔍")
            sc.note("nenhum dos dois lados afirmou valor em R$ — o par não mediu nada; "
                     "ou o caso não é numérico, ou os dois falharam em responder")
        else:
            sc.set_veredict("✅" if (sem_invencao and trouxe_dado and guarda_ok) else "❌")
        SCENARIOS.append(sc)


def main():
    t0 = time.time()

    # L1_UID[0] é preenchido dentro de cena_1_registrar_despesa e reusado
    # nas cenas 8 e 9 (histórico acumulado é intencional).
    ordered = [
        cena_1_registrar_despesa,
        cena_2_registrar_receita,
        cena_3_valor_primeiro_sem_verbo,
        cena_4_lancamento_com_data,
        cena_5_multi_lancamento,
        cena_6_valor_faltando,
        cena_7_oferta_gasto_fixo,
        cena_8_listar_historico,
        cena_9_quanto_gastei,
        cena_10_apagar_por_id,
        cena_11_apagar_varios,
        cena_12_desfazer,
        cena_13_cadastrar_cartao,
        cena_14_comprar_credito,
        cena_15_parcelamento,
        cena_16_ver_parcelamentos,
        cena_17_fatura,
        cena_18_limite,
        cena_19_principal,
        cena_20_excluir_cartao,
        cena_21_apagar_compra_parcelamento,
        cena_22_sim_nao,
        cena_23_pendencia_abandonada,
    ]

    so_pares = "--pares" in sys.argv
    if so_pares:
        ordered = []

    for fn in ordered:
        print(f"[harness] rodando {fn.__name__} ...")
        try:
            fn()
        except Exception:
            tb = traceback.format_exc()
            print(f"[harness] cena {fn.__name__} quebrou fora dos turnos:\n{tb}")
            sc = Scenario(fn.__name__, "?", "?")
            sc.exception = tb
            sc.set_veredict("⚠️")
            SCENARIOS.append(sc)

    print(f"[harness] rodando {len(PARES)} pares comando × IA ...")
    roda_pares()

    elapsed = time.time() - t0
    print(f"[harness] execução concluída em {elapsed:.1f}s "
          f"({len(SCENARIOS)} cenários registrados).")

    # Ledger ANTES do relatório: o `write_report()` imprime o acumulado do mês,
    # e gravando depois o artefato sempre omitia a própria rodada — na primeira
    # do mês ele mostrava custo > 0 e acumulado 0,0000, contradizendo a si
    # mesmo. O total do console estava certo só porque roda depois.
    usd_rodada, _ = meter.cost_usd(meter.CALLS)
    meter.append_ledger(usd_rodada, "whatsapp_qa_vault_harness")

    write_report()

    counts = {"✅": 0, "❌": 0, "⚠️": 0, "🔍": 0}
    for sc in SCENARIOS:
        counts[sc.veredict] = counts.get(sc.veredict, 0) + 1
    print(f"[harness] resumo: ✅{counts['✅']} ❌{counts['❌']} ⚠️{counts['⚠️']} 🔍{counts['🔍']} "
          f"de {len(SCENARIOS)}")

    usd, unknown = meter.cost_usd(meter.CALLS)
    ia, cmd = _split_paths()
    print(f"[harness] caminho: {ia} turno(s) por IA, {cmd} por comando "
          f"({len(meter.CALLS)} chamadas OpenAI)")
    print(f"[harness] custo desta rodada: US$ {usd:.4f}")
    if unknown:
        print(f"[harness] AVISO: modelo(s) sem preço na tabela, FORA do custo: {', '.join(unknown)}")
    todas = [c for sc in SCENARIOS for _, _, _, cl in sc.turns for c in cl]
    nao_sust = [c for c in todas if not c.supported]
    print(f"[harness] guarda: {len(todas)} afirmação(ões) numérica(s) em resposta de IA, "
          f"{len(nao_sust)} NÃO sustentada(s)")
    for c in nao_sust:
        print(f"[harness]   🚨 {c.token} ({c.kind}) — {c.detail}")
    print(f"[harness] acumulado dos harnesses no mês: US$ {meter.month_total_usd():.4f}")


def _split_paths() -> tuple[int, int]:
    """(turnos que chamaram IA, turnos resolvidos por comando)."""
    ia = cmd = 0
    for sc in SCENARIOS:
        for _, _, chamadas, _ in sc.turns:
            if chamadas:
                ia += 1
            else:
                cmd += 1
    return ia, cmd


def write_report():
    # Data do dia, não fixa: com o nome cravado em 2026-08-20 toda rodada nova
    # sobrescrevia em silêncio o relatório da rodada anterior — que é o único
    # registro do que a IA respondeu naquele dia, e não se reproduz sem pagar
    # a suíte de novo.
    # Com data, hora, minuto E SEGUNDO. Só data sobrescrevia entre rodadas do
    # mesmo dia (custou a comparação que provava que o defeito da cena 18 é
    # intermitente); e só até o minuto ainda sobrescreve, porque a rodada
    # completa leva 11,6s medidos — duas dentro do mesmo minuto é rotina, não
    # exceção.
    hoje = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(WORKTREE_ROOT, "docs", f"qa_whatsapp_pilot_{hoje}.md")
    counts = {"✅": 0, "❌": 0, "⚠️": 0, "🔍": 0}
    for sc in SCENARIOS:
        counts[sc.veredict] = counts.get(sc.veredict, 0) + 1

    lines = []
    lines.append("# QA piloto — vault WhatsApp (23 interações, 3 domínios)")
    lines.append("")
    lines.append(f"Gerado em {hoje} por `scripts/whatsapp_qa_vault_harness.py`, chamando "
                  f"`core.handle_incoming.handle_incoming()` direto contra um Postgres isolado "
                  f"e descartável, sem mockar a IA (chamadas OpenAI reais).")
    lines.append("")
    lines.append(f"**Sumário:** {len(SCENARIOS)} interações — "
                  f"✅ {counts['✅']} · ❌ {counts['❌']} · ⚠️ {counts['⚠️']} · 🔍 {counts['🔍']}")
    lines.append("")
    usd, unknown = meter.cost_usd(meter.CALLS)
    ia, cmd = _split_paths()
    lines.append(f"**Caminho:** {ia} turno(s) resolvido(s) pela IA · {cmd} por comando/regex "
                  f"(sem chamada nenhuma) — {len(meter.CALLS)} chamadas OpenAI no total.")
    lines.append("")
    tin = sum(c["in"] for c in meter.CALLS)
    tcached = sum(c["cached"] for c in meter.CALLS)
    pct = (100 * tcached / tin) if tin else 0
    lines.append(f"**Custo:** US$ {usd:.4f} nesta rodada · US$ {meter.month_total_usd():.4f} "
                  f"acumulado no mês pelos harnesses. {tin} tokens de entrada, "
                  f"{tcached} cacheados ({pct:.0f}%), cobrados à metade. "
                  f"Preços da tabela de 2026-09-01, sem o whisper (cobrado por minuto) "
                  f"— reconfira antes de citar fora daqui.")
    if unknown:
        lines.append("")
        lines.append(f"⚠️ **Fora do custo:** modelo(s) sem preço na tabela: {', '.join(unknown)}.")
    if MODEL_PROBLEMS:
        lines.append("")
        lines.append("⚠️ **Modelos:** " + " · ".join(MODEL_PROBLEMS))
    lines.append("")
    todas = [c for sc in SCENARIOS for _, _, _, cl in sc.turns for c in cl]
    nao_sust = [c for c in todas if not c.supported]
    if todas:
        lines.append(f"**Guarda de afirmações:** {len(todas)} afirmação(ões) numérica(s) "
                      f"em resposta de IA · **{len(nao_sust)} não sustentada(s)** "
                      f"(número que não veio de nenhuma tool nem da mensagem do usuário).")
        for c in nao_sust:
            lines.append(f"- 🚨 `{c.token}` ({c.kind}) — {c.detail}")
        lines.append("")

    lines.append("## Discrepâncias entre vault e código")
    lines.append("")
    if DISCREPANCIAS:
        for d in DISCREPANCIAS:
            lines.append(f"- {d}")
    else:
        lines.append("(nenhuma sinalizada)")
    lines.append("")
    lines.append("---")
    lines.append("")

    for sc in SCENARIOS:
        lines.append(f"## {sc.name}")
        lines.append(f"**Domínio:** {sc.domain} · **Cenário/usuário:** {sc.scenario_desc}")
        lines.append("")
        for texto, resposta, chamadas, claims in sc.turns:
            lines.append(f"> Você: {texto}")
            lines.append(f"> PigBank: {resposta}")
            lines.append(f"> _caminho: {meter.path_label(chamadas)}_")
            if claims:
                lines.append(f"> _guarda: {ai_guard.verdict(claims)}_")
            lines.append(">")
        lines.append("")
        lines.append(f"**Veredito:** {sc.veredict}")
        lines.append("")
        lines.append("**Checklist do vault:**")
        for ok, label in sc.checklist:
            mark = "x" if ok else (" " if ok is False else "?")
            lines.append(f"- [{mark}] {label}")
        if sc.notes:
            lines.append("")
            lines.append("**Notas:**")
            for n in sc.notes:
                lines.append(f"- {n}")
        if sc.exception:
            lines.append("")
            lines.append("**Traceback resumido:**")
            lines.append("```")
            tb_lines = sc.exception.strip().splitlines()
            lines.append("\n".join(tb_lines[-15:]))
            lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))

    print(f"[harness] relatório gerado em {path}")


if __name__ == "__main__":
    try:
        main()
    finally:
        print(f"[harness] derrubando database {DB_NAME} ...")
        try:
            with psycopg.connect(_ADMIN_DSN, autocommit=True) as _conn:
                _conn.execute(f'drop database "{DB_NAME}" with (force)')
            print("[harness] database derrubado.")
        except Exception as e:
            print(f"[harness] AVISO: falha ao derrubar database {DB_NAME}: {e}")
