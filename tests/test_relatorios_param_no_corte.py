"""
tests/test_relatorios_param_no_corte.py — os relatórios proativos param junto
com o resto do produto no corte do Grátis (decisão do dono).

QUATRO laços em DOIS arquivos passam pelo mesmo helper
(`core.reports.reports_daily.filtrar_por_acesso`): o diário e o periódico do
Discord (`core/reports/reports_daily.py`) e os dois irmãos do WhatsApp
(`adapters/whatsapp/wa_app.py`). O teste cobre **os dois canais**, um caso por
canal — com um só, a categoria fica meio fechada (§2).

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo**: troque o corpo de `filtrar_por_acesso` por `return list(user_ids)`.
VERMELHOS:
  `test_sem_direito_sai_do_lote`
  `test_a_ordem_e_a_composicao_do_lote_sobrevivem`
Direção: falso positivo — o relatório diário continua chegando por Discord e por
WhatsApp para quem perdeu o acesso.

**`test_os_quatro_lacos_passam_pelo_helper` NÃO cai com essa injeção, e é outra
injeção**: ele conta CALL SITES, não o corpo. O vermelho dele é apagar a chamada
de um laço (por exemplo o `filtrar_por_acesso(` do `_periodic_report_tick` em
`adapters/whatsapp/wa_app.py`) — o laço novo, ou o irmão esquecido. Duas
injeções, dois vermelhos diferentes: as duas ficam declaradas, com qual é qual.

**Positivos** (VERDES sob a injeção): `test_pagante_continua_no_lote` e
`test_carencia_aberta_continua_no_lote`, nos dois canais. Sem eles o grupo
passaria num filtro que devolve lista vazia — que é pior que o bug.

**Negativo do custo de PII** — troque
`has_app_access(uid, user=get_plan_gate_state(uid))` por `has_app_access(uid)`
(a busca volta para o `get_auth_user`; nada é apagado). VERMELHO:
  `test_filtro_nao_audita_pii_de_quem_e_filtrado_fora`
Direção: reabre a célula 28-b — decriptação e trilha de auditoria de gente que
**não recebe nada**. Medido antes/depois: 2 linhas por conta cortada → 0.
"""
from __future__ import annotations

import re
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


@pytest.mark.parametrize("arquivo,esperados", [
    ("core/reports/reports_daily.py", 2),
    ("adapters/whatsapp/wa_app.py", 2),
])
def test_os_quatro_lacos_passam_pelo_helper(arquivo, esperados):
    """Quatro laços, dois arquivos — e é a CATEGORIA que tem de estar fechada,
    não a instância (§2). O caso que este teste existe para pegar é o laço novo
    (ou o irmão esquecido) que busca `list_users_with_*_report_enabled` e sai
    mandando sem passar pelo filtro.

    Conta o uso do helper, não o texto do filtro: `filtrar_por_acesso` é o
    predicado, e é ele que sobrevive a refatoração (`docs/controles_declarados.md`).
    """
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    usos = len(re.findall(r"filtrar_por_acesso\(", texto))
    # No reports_daily.py a própria definição casa o `def`, então o `(` do uso é
    # contado junto com o da assinatura — desconta-se ela.
    if arquivo == "core/reports/reports_daily.py":
        usos -= len(re.findall(r"def filtrar_por_acesso\(", texto))
    assert usos == esperados, (
        f"{arquivo}: {usos} laço(s) filtrando, esperado {esperados} — um laço de "
        "relatório sem filtro manda relatório para quem foi cortado")


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
