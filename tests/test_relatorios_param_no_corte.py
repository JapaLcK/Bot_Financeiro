"""
tests/test_relatorios_param_no_corte.py — os relatórios proativos param junto
com o resto do produto no corte do Grátis (decisão do dono).

Todo laço proativo do repositório passa pelo mesmo helper
(`core.reports.reports_daily.filtrar_por_acesso`). Quais são eles NÃO fica
escrito aqui (§2) — a lista viva é `_LACOS_ESPERADOS`, lá embaixo, e o portão
que a compara com a árvore é `test_todo_laco_proativo_passa_pelo_helper`. Os
casos de comportamento cobrem **os dois canais**, um por canal — com um só, a
categoria fica meio fechada.

**Este portão já falhou DUAS vezes, e as duas custaram um laço mandando mensagem
paga a quem foi cortado.** Primeiro ele CONTAVA ocorrências de
`filtrar_por_acesso(` por arquivo, e estava invertido: laço novo sem filtro
mantinha a contagem e ficava verde — foi assim que `_bill_reminder_tick`
(template com o botão "✅ Já paguei") passou. Consertado para enumerar, ele
ainda olhava uma LISTA DE ARQUIVOS, e `core/services/open_finance_proactive.py`
— salário identificado e pedido de reconexão — não estava nela. Hoje ele varre a
árvore inteira. Se você vier acrescentar um terceiro remendo, o padrão é claro:
o buraco sempre esteve no ESCOPO da varredura, não no predicado.

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
  `test_todo_laco_proativo_passa_pelo_helper`

**(b) o LAÇO NOVO, EM ARQUIVO NOVO e com listador de OUTRO NOME** — é o caso
que as duas versões anteriores deste teste não pegavam, e por isso a injeção usa
as duas variações de uma vez. Crie `core/services/laco_novo_proativo.py`::

    from db import list_open_finance_user_ids
    from adapters.whatsapp.wa_client import send_template

    def run_laco_novo() -> None:
        for uid in list_open_finance_user_ids():
            send_template(str(uid), "oi")

VERMELHO: o MESMO nome acima, pela asserção do CONJUNTO. Medido 2026-09-11.
Histórico das duas versões anteriores, para não repetir: contando ocorrências
por arquivo, a injeção (b) dava **8 passed, 0 failed** e um laço novo COM filtro
é que ficava vermelho; enumerando só dois arquivos, ela continuava verde por o
arquivo não ser olhado.

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


# Enumerador de POPULAÇÃO. Era `^list_users$|^list_users_with_|^list_\w*_user_ids$`
# — três prefixos escolhidos por serem os que eu já tinha visto — e deixou
# passar `list_agents_pending_email`, o laço que manda relatório financeiro por
# e-mail para quem foi cortado. **É a quinta vez neste PR que o furo está no
# ESCOPO da varredura**, e sempre pelo mesmo mecanismo: enumerar as formas
# conhecidas de dizer uma coisa em vez de enumerar a categoria.
#
# Hoje é `^list_` e nada mais. Medido 2026-09-11: o alargamento custa ZERO
# ruído — passou de 7 para 10 funções, e as TRÊS novas são membros legítimos da
# categoria (uma era o defeito, duas são isenções com razão). Quem discrimina
# não é mais o nome do listador; é o segundo termo, o ENVIO.
_POPULACAO = re.compile(r"^list_")

# Enviar por qualquer canal. `send` cru é o Discord (`await user.send(...)`) e o
# prefixo `_send_` pega os wrappers locais (`_send_periodic_template`).
_ENVIO = {"send", "send_template", "send_text", "send_message"}


def _envia(chamadas: set[str]) -> bool:
    """Manda alguma coisa para fora, por qualquer canal.

    `send_*_email` entra porque foi por aí que o laço dos agentes escapou: ele
    não chama `send_template` nem `send_text` — chama `send_agent_report_email`.
    """
    return (bool(chamadas & _ENVIO)
            or any(c.startswith("_send_")
                   or (c.startswith("send_") and c.endswith("_email"))
                   for c in chamadas))


# Laços proativos que NÃO passam por `filtrar_por_acesso`, cada um com a razão
# escrita — o análogo do `_ISENTAS_COM_RAZAO` de
# `tests/test_portao_pendencias_interceptadas.py`. Sem este escape, alargar o
# critério obrigaria a gatear coisas que não devem ser gateadas, e a saída fácil
# seria apagar o portão em vez de declarar a entrada.
_ISENTOS_COM_RAZAO = {
    "core/services/trial_downsell.py::send_trial_downsell_emails":
        "É o e-mail de WIN-BACK: existe PARA falar com quem ficou sem plano no "
        "fim do teste, e filtrar por acesso o mataria inteiro. O próprio laço "
        "já filtra o oposto (`get_plan_tier(user_id) != 'free'` → pula), e a "
        "copy dele é presa por "
        "`tests/test_billing_email_downsell_nao_promete_gratis.py`.",
    "core/services/payment_reminder_wa.py::_wa_lembrete":
        "Não é laço de população: roda POR usuário já escolhido pelo funil do "
        "lembrete, e tem gate próprio e mais estrito — "
        "`db.dunning.ciclo_de_atraso_aberto`, lido fresco imediatamente antes "
        "do envio (célula 31 de `docs/dunning_estados_eventos.md`).",
}


def _lacos_proativos() -> dict[str, bool]:
    """{"arquivo::função": ela chama `filtrar_por_acesso`} — REPOSITÓRIO INTEIRO.

    Um laço proativo é uma função que ENUMERA uma população de usuários **e**
    ENVIA alguma coisa. Os dois termos são necessários: só o enumerador pegaria
    `piggy_agents.run_*_once` e `investment_scheduler`, que varrem população e
    não mandam nada (escrevem em tabela); só o envio pegaria
    `payment_reminder_wa._wa_lembrete`, que manda mas é POR usuário já
    escolhido, com gate próprio (`ciclo_de_atraso_aberto`).

    **Varre o repositório, e não uma lista de arquivos**, porque a lista de
    arquivos foi a falha anterior: o portão cobria `wa_app.py` e
    `reports_daily.py`, e `core/services/open_finance_proactive.py` — dois laços
    mandando salário e pedido de reconexão a quem foi cortado — simplesmente não
    era olhado. Antes disso o portão CONTAVA ocorrências e estava INVERTIDO
    (laço novo sem filtro mantinha a contagem e ficava verde). Terceira mordida
    da mesma categoria neste PR; o remédio do §2 é enumerar, e enumerar TUDO.

    AST e não regex porque a forma do laço varia — `_periodic_report_tick` itera
    `weekly_users | monthly_users`, e nenhum `for ... in list_users_with_*(...)`
    casaria com ele. Nomes de chamada por `Name` **e** por `Attribute`, senão
    `user.send(...)` do Discord não conta como envio.
    """
    achados: dict[str, bool] = {}
    for caminho in sorted(RAIZ.rglob("*.py")):
        partes = caminho.relative_to(RAIZ).parts
        if {".venv", "__pycache__", "node_modules", ".claude", "tests"} & set(partes):
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(arvore):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            chamadas = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                if isinstance(n.func, ast.Name):
                    chamadas.add(n.func.id)
                elif isinstance(n.func, ast.Attribute):
                    chamadas.add(n.func.attr)
            if any(_POPULACAO.match(c) for c in chamadas) and _envia(chamadas):
                rel = "/".join(caminho.relative_to(RAIZ).parts)
                achados[f"{rel}::{fn.name}"] = "filtrar_por_acesso" in chamadas
    return achados


_LACOS_ESPERADOS = {
    "adapters/whatsapp/wa_app.py::_daily_report_tick",
    "adapters/whatsapp/wa_app.py::_periodic_report_tick",
    "adapters/whatsapp/wa_app.py::_bill_reminder_tick",
    "core/reports/reports_daily.py::_daily_report_discord",
    "core/reports/reports_daily.py::_periodic_reports_discord",
    "core/services/open_finance_proactive.py::run_salary_notifications",
    "core/services/open_finance_proactive.py::run_reconnect_notifications",
    "core/services/piggy_agents.py::run_agent_emails_once",
    *_ISENTOS_COM_RAZAO,
}


def test_todo_laco_proativo_passa_pelo_helper():
    """É a CATEGORIA que tem de estar fechada, não a instância (§2).

    Duas asserções, e as duas são necessárias:

    • **cada laço filtra** — o irmão esquecido;
    • **o conjunto é o esperado** — o laço NOVO, e o ARQUIVO novo. Sem esta,
      um laço que não filtrasse podia simplesmente não ser olhado, que é a
      falha que este teste já teve duas vezes.

    Quem adicionar laço proativo legítimo tem DUAS linhas para mexer: a chamada
    do filtro e esta lista. É de propósito — a lista é a declaração de que
    alguém olhou, e ela é o único lugar do repositório onde a categoria inteira
    está escrita.
    """
    achados = _lacos_proativos()
    sem_filtro = sorted(n for n, filtra in achados.items()
                        if not filtra and n not in _ISENTOS_COM_RAZAO)
    assert not sem_filtro, (
        f"{sem_filtro} enumera(m) população de usuários e envia(m) sem passar por "
        "`filtrar_por_acesso` — manda(m) mensagem para quem foi cortado. Se o "
        "laço tiver razão para não filtrar, ela vai POR ESCRITO em "
        "`_ISENTOS_COM_RAZAO`, não por omissão.")
    assert set(achados) == _LACOS_ESPERADOS, (
        f"laços proativos {sorted(achados)}, esperado {sorted(_LACOS_ESPERADOS)} — "
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
