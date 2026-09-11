"""
tests/test_relatorios_param_no_corte.py — os relatórios proativos param junto
com o resto do produto no corte do Grátis (decisão do dono).

CINCO laços em DOIS arquivos passam pelo mesmo helper
(`core.reports.reports_daily.filtrar_por_acesso`): o diário e o periódico do
Discord (`core/reports/reports_daily.py`) e TRÊS do WhatsApp
(`adapters/whatsapp/wa_app.py`) — o diário, o periódico e o **lembrete de conta
a pagar**. O teste cobre **os dois canais**, um caso por canal — com um só, a
categoria fica meio fechada (§2).

O terceiro do WhatsApp entrou em 2026-09-11, e a história dele é o motivo de
este arquivo ter mudado de método: `_bill_reminder_tick` mandava template pago
com o botão "✅ Já paguei" para conta cortada, e o portão daqui ficou VERDE o
tempo todo porque contava chamadas em vez de enumerar laços (ver
`test_todo_laco_proativo_passa_pelo_helper`).

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo**: troque o corpo de `filtrar_por_acesso` por `return list(user_ids)`.
VERMELHOS:
  `test_sem_direito_sai_do_lote`
  `test_a_ordem_e_a_composicao_do_lote_sobrevivem`
Direção: falso positivo — o relatório diário continua chegando por Discord e por
WhatsApp para quem perdeu o acesso.

**`test_todo_laco_proativo_passa_pelo_helper` NÃO cai com essa injeção, e é
outra injeção**: ele lê o call site, não o corpo do helper. Ele tem DOIS
vermelhos, um por asserção, e são bugs diferentes:

**(a) o IRMÃO ESQUECIDO** — apague a chamada `filtrar_por_acesso([uid])` do
`_periodic_report_tick` em `adapters/whatsapp/wa_app.py`. VERMELHO:
  `test_todo_laco_proativo_passa_pelo_helper[adapters/whatsapp/wa_app.py-esperados1]`

**(b) o LAÇO NOVO, que é o caso que a versão anterior deste teste NÃO pegava** —
cole em `adapters/whatsapp/wa_app.py` um laço proativo novo, sem filtro::

    def _laco_novo_tick() -> None:
        for uid in list_users_with_daily_report_enabled():
            send_text(str(uid), "oi")

VERMELHO: o MESMO id acima. Medido 2026-09-11 — com o teste antigo, que contava
ocorrências de `filtrar_por_acesso(` por arquivo, esta injeção dava **8 passed,
0 failed**, e um laço novo COM filtro é que ficava vermelho. O portão estava
invertido.

**Positivos** (VERDES sob a injeção): `test_pagante_continua_no_lote` e
`test_carencia_aberta_continua_no_lote`, nos dois canais. Sem eles o grupo
passaria num filtro que devolve lista vazia — que é pior que o bug.

**Negativo do custo de PII** — troque
`has_app_access(uid, user=get_plan_gate_state(uid))` por `has_app_access(uid)`
(a busca volta para o `get_auth_user`; nada é apagado). VERMELHOS (remedido
2026-09-11; a instrução anterior nomeava só o primeiro, e são dois):
  `test_filtro_nao_audita_pii_de_quem_e_filtrado_fora`
  `test_pagante_tambem_passa_sem_auditar`
Direção: reabre a célula 28-b — decriptação e trilha de auditoria de gente que
**não recebe nada**. Medido antes/depois: 2 linhas por conta cortada → 0.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core.reports.reports_daily import filtrar_por_acesso
from db.connection import get_conn

RAIZ = Path(__file__).resolve().parent.parent
AGORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


def _conta(sufixo: str, *, plan="free", expires=None, past_due_since=None, status=None) -> int:
    import uuid
    user = db.register_auth_user(f"rel-{sufixo}-{uuid.uuid4().hex[:10]}@t.com", "senha-forte-123")
    uid = int(user["user_id"])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan=%s, plan_expires_at=%s,"
                "       past_due_since=%s, last_payment_status=%s, plan_selected_at=now()"
                " where user_id=%s",
                # `last_payment_status` é NOT NULL no schema: o "sem status" da
                # conta que nunca passou pela Stripe é string vazia, não NULL.
                (plan, expires, past_due_since, status or "", uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)
    return uid


def test_sem_direito_sai_do_lote():
    uid = _conta("corte")
    assert filtrar_por_acesso([uid]) == []


def test_pagante_continua_no_lote():
    uid = _conta("pagante", plan="pro", expires=AGORA + timedelta(days=30), status="active")
    assert filtrar_por_acesso([uid]) == [uid]


def test_carencia_aberta_continua_no_lote():
    """O relógio de inadimplência CONCEDE tempo: quem está na carência ainda
    recebe relatório. É o lado direito do OR de `tem_direito_hoje`, e sem este
    caso o filtro poderia estar lendo o status como autoridade sem ninguém ver."""
    uid = _conta("carencia", plan="pro", expires=AGORA - timedelta(days=1),
                 past_due_since=AGORA - timedelta(days=2), status="past_due")
    assert filtrar_por_acesso([uid]) == [uid]


def test_a_ordem_e_a_composicao_do_lote_sobrevivem():
    """Filtro, não reordenação: os laços de baixo iteram a lista que sai daqui."""
    fora = _conta("mix-fora")
    dentro = _conta("mix-dentro", plan="pro", expires=AGORA + timedelta(days=30))
    outro = _conta("mix-outro", plan="essencial", expires=AGORA + timedelta(days=30))
    assert filtrar_por_acesso([dentro, fora, outro]) == [dentro, outro]


def _lacos_proativos(arquivo: str) -> dict[str, bool]:
    """{nome da função: ela chama `filtrar_por_acesso`}, para toda função que
    ENUMERA uma população proativa (`list_users_with_*`).

    Enumera, não conta. A versão anterior contava ocorrências de
    `filtrar_por_acesso(` por arquivo e estava INVERTIDA: laço novo SEM filtro
    mantinha a contagem e ficava verde; laço novo COM filtro quebrava a
    contagem e ficava vermelho — o oposto exato do que a docstring prometia.
    Foi assim que `_bill_reminder_tick` (o TERCEIRO laço proativo do
    `wa_app.py`, que manda template pago para boleto vencendo) passou por este
    portão sem filtro nenhum.

    `list_users_with_*` é o predicado, e é ele que sobrevive: toda população
    proativa deste repositório sai de um enumerador com esse prefixo. AST em
    vez de regex porque a forma do laço varia — `_periodic_report_tick` itera
    `weekly_users | monthly_users`, uma união de dois conjuntos, e nenhum
    `for ... in list_users_with_*(...)` casaria com ele.
    """
    tree = ast.parse((RAIZ / arquivo).read_text(encoding="utf-8"))
    achados: dict[str, bool] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        chamadas = {n.func.id for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if any(c.startswith("list_users_with_") for c in chamadas):
            achados[fn.name] = "filtrar_por_acesso" in chamadas
    return achados


@pytest.mark.parametrize("arquivo,esperados", [
    ("core/reports/reports_daily.py", {"_daily_report_discord", "_periodic_reports_discord"}),
    ("adapters/whatsapp/wa_app.py",
     {"_daily_report_tick", "_periodic_report_tick", "_bill_reminder_tick"}),
])
def test_todo_laco_proativo_passa_pelo_helper(arquivo, esperados):
    """É a CATEGORIA que tem de estar fechada, não a instância (§2).

    Duas asserções, e as duas são necessárias:

    • **cada laço conhecido filtra** — o irmão esquecido;
    • **o conjunto de laços é o esperado** — o laço NOVO. Sem esta, um laço
      novo que não filtrasse simplesmente não seria olhado, que é a falha que
      este teste acabou de ter.

    Quem adicionar laço proativo legítimo tem DUAS linhas para mexer aqui, e é
    de propósito: a lista é a declaração de que alguém olhou.
    """
    achados = _lacos_proativos(arquivo)
    sem_filtro = sorted(n for n, filtra in achados.items() if not filtra)
    assert not sem_filtro, (
        f"{arquivo}: {sem_filtro} enumera(m) população proativa e não passa(m) por "
        "`filtrar_por_acesso` — manda(m) mensagem para quem foi cortado")
    assert set(achados) == esperados, (
        f"{arquivo}: laços proativos {sorted(achados)}, esperado {sorted(esperados)} — "
        "laço novo entra nesta lista junto com o filtro, não depois")


def _linhas_de_auditoria(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from pii_access_log where subject_user_id=%s",
                (uid,),
            )
            return int(cur.fetchone()["n"])


def test_filtro_nao_audita_pii_de_quem_e_filtrado_fora(monkeypatch):
    """Filtrar NÃO pode custar uma linha de `pii_access_log` por conta cortada.

    É o defeito que a célula 28-b de `docs/dunning_estados_eventos.md` registra
    como fechado — "o lote registrava acesso ao e-mail de gente que nunca
    recebeu nada" — e este helper é um quarto call site onde ele poderia
    renascer. `get_plan_gate_state` traz as cinco colunas do veredito sem tocar
    em PII; `get_auth_user` decifra e audita.

    O `PII_AUDIT_DISABLED=0` é obrigatório: o `conftest` desliga a auditoria por
    padrão e sem ele este teste ficaria verde medindo zero de um lado só. O
    `invalidate_auth_user_cache` também: cache quente de 10 s esconderia o
    decrypt e daria o mesmo verde por outro motivo.
    """
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")
    import db_support

    uid = _conta("auditoria")
    db_support.invalidate_auth_user_cache(uid)
    antes = _linhas_de_auditoria(uid)

    assert filtrar_por_acesso([uid]) == [], "pré-condição: a conta tem de ser cortada"
    db_support.invalidate_auth_user_cache(uid)
    filtrar_por_acesso([uid])

    depois = _linhas_de_auditoria(uid)
    assert depois == antes, (
        f"{depois - antes} linha(s) de auditoria de PII para conta que o filtro "
        "descartou e que não recebe relatório nenhum"
    )


def test_pagante_tambem_passa_sem_auditar(monkeypatch):
    """POSITIVO do par acima: quem FICA no lote também não é auditado aqui.

    Sem ele, um filtro que devolvesse lista vazia sem consultar nada passaria no
    teste de cima — zero auditoria por não fazer trabalho nenhum."""
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")
    import db_support

    uid = _conta("auditoria-pagante", plan="pro",
                 expires=AGORA + timedelta(days=30), status="active")
    db_support.invalidate_auth_user_cache(uid)
    antes = _linhas_de_auditoria(uid)

    assert filtrar_por_acesso([uid]) == [uid], "pré-condição: o pagante fica no lote"

    assert _linhas_de_auditoria(uid) == antes
