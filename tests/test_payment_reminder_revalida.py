"""
tests/test_payment_reminder_revalida.py — o lembrete de pagamento × o estado que
MUDOU depois da query.

`db.dunning.list_payment_reminder_candidates` devolve UM snapshot e o `for row
in rows` não tem `LIMIT`. Quem pagasse depois da query — em especial enquanto as
linhas ANTERIORES do lote são processadas — recebia "a cobrança continua
pendente" e a dedupe registrava o envio como sucesso. E-mail errado para cliente
PAGANTE, que é a categoria que este caminho existe para consertar (célula nº 28
de `docs/dunning_estados_eventos.md`).

O conserto é `db.dunning.lembrete_ainda_vale`, chamado como ÚLTIMA coisa
antes do envio. Duas coisas que ele deliberadamente NÃO é, e cada uma tem teste
aqui:

  • não é `get_auth_user` — aquele tem cache de 10 s
    (`db_support._auth_user_cache`) e pode mentir exatamente nesta corrida;
  • não é claim atômico com gravação antecipada da dedupe — o `_fire_email` do
    webhook grava a chave DEPOIS de o envio confirmar, de propósito, e um claim
    que gravasse antes reintroduziria o ciclo mudo da rodada 1.

Arquivo próprio porque `test_payment_reminder.py` está a poucas linhas do teto
de 350 de `tests/test_max_lines_python.py`; os helpers vêm por IMPORT dele —
uma fonte só (§0.7). Mesmo arranjo do `test_payment_reminder_janela.py`.

CONTROLE NEGATIVO DECLARADO — em `core/services/payment_reminder.py`, troque o
`continue` do `if atual is None:` por `atual = _linha_do_funil` (ou seja: em vez
de pular, siga com o valor do snapshot):
    VERMELHO: test_pagou_durante_o_lote_nao_recebe_lembrete
              test_status_saiu_da_lista_durante_o_lote_nao_recebe_lembrete
              test_engagement_opt_out_ligado_durante_o_lote_nao_manda_email
              (`_consentimento.py`) — seguir com o snapshot também pula o
              segundo termo de `lembrete_ainda_vale`, o do consentimento de
              e-mail. Medido ao reescrever esta instrução: TRÊS vermelhos, não
              dois.

Regra dos controles: predicado citado, só os VERMELHOS nomeados, sem `N
passed` — ver `docs/controles_declarados.md`.

A instrução anterior mandava apagar um `if not await ... lembrete_ainda_vale`
que deixou de existir quando a função passou a devolver `dict | None` em vez de
`bool` — instrução que nomeia a forma do bloco morre com a refatoração.

CONTROLE POSITIVO: `test_lembrete_legitimo_continua_saindo`. Sem ele o arquivo
passaria num `lembrete_ainda_vale` que devolvesse sempre False — ou seja num
lembrete que nunca sai, que é pior que o bug.
"""
from __future__ import annotations

import pytest

from db.connection import get_conn
from test_payment_reminder import (
    _espia,
    _inadimplente,
    _limpar_eventos,
    _tick,
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Mesmo ambiente do arquivo irmão: flag ligada (ela nasce DESLIGADA em
    produção), sem template de WhatsApp, e `system_event_logs` garantida."""
    from _billing_grants_helpers import garantir_system_event_logs
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    garantir_system_event_logs()


def _mexer_depois_da_query(monkeypatch, sql: str, uid: int) -> list:
    """Injeta a mudança de estado no ponto EXATO onde ela cabe em produção: o
    funil já respondeu, e o `for row in rows` ainda não chegou nesta linha.

    O envelope chama o funil REAL e devolve as linhas REAIS — o snapshot fica
    igual ao de produção, e o que muda é só o banco por baixo dele. Devolve a
    lista de chamadas para o teste provar que a injeção aconteceu (sem isso, um
    funil que devolvesse vazio faria o teste passar medindo nada).
    """
    from db import dunning as db_dunning
    real = db_dunning.list_payment_reminder_candidates
    chamadas: list = []

    def _envelope(grace_days):
        rows = real(grace_days)
        chamadas.append(len(rows))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uid,))
            conn.commit()
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(uid)
        return rows

    monkeypatch.setattr(db_dunning, "list_payment_reminder_candidates", _envelope)
    return chamadas


def _eventos_de_dedupe(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from system_event_logs"
                " where event_type = 'payment_reminder_sent' and user_id = %s",
                (uid,))
            return cur.fetchone()["n"]


def test_pagou_durante_o_lote_nao_recebe_lembrete(user_id, monkeypatch):
    """O `invoice.paid` cai depois da query: o relógio zera e o lembrete morre.

    É a corrida do apontamento, com o estado REAL no banco — não um funil
    fabricado. E a asserção é DUPLA de propósito: nada de e-mail **e** nada de
    chave de dedupe. Gravar a dedupe sem enviar é o que calaria o lembrete
    legítimo do tick seguinte, quando a conta ainda estiver em atraso.
    """
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    chamadas = _mexer_depois_da_query(
        monkeypatch,
        "update auth_accounts set past_due_since = null where user_id = %s",
        user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1, \
        "o funil não devolveu a conta — a injeção mediria nada"
    assert enviados == [], "lembrete saiu para quem pagou durante o lote"
    assert _eventos_de_dedupe(user_id) == 0, \
        "dedupe gravada sem envio: o tick seguinte ficaria mudo"


def test_status_saiu_da_lista_durante_o_lote_nao_recebe_lembrete(user_id, monkeypatch):
    """O outro lado do MESMO predicado: o status sai de
    `PAST_DUE_PAYMENT_STATUSES` durante o lote.

    Em produção esse caminho é o `set_payment_status_impl`, que zera o relógio
    no mesmo UPDATE — aqui o status é trocado SEM tocar no relógio, de propósito,
    para que a revalidação seja medida pelos DOIS termos e não só pelo relógio.
    Um predicado que checasse só `past_due_since is not null` passaria no teste
    anterior e reprovaria neste.
    """
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    chamadas = _mexer_depois_da_query(
        monkeypatch,
        "update auth_accounts set last_payment_status = 'active' where user_id = %s",
        user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1
    assert enviados == [], "lembrete saiu com o status já fora da lista"
    assert _eventos_de_dedupe(user_id) == 0


def test_lembrete_legitimo_continua_saindo(user_id, monkeypatch):
    """POSITIVO: sem mudança de estado no meio do lote, o lembrete sai e a
    dedupe é gravada. Sem este caso, uma revalidação que recusasse TUDO passaria
    nos dois testes acima."""
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    # Envelope que NÃO mexe em nada: o mesmo caminho, a mesma quantidade de
    # chamadas, só sem a corrida. É o que separa "a revalidação discrimina" de
    # "a revalidação bloqueia".
    chamadas = _mexer_depois_da_query(monkeypatch, "select %s", user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1
    assert enviados != [], "o lembrete legítimo deixou de sair"
    assert _eventos_de_dedupe(user_id) == 1


# ──────────────────────────────────────────────────────────────────────────────
# O ENDEREÇO também vinha do snapshot — o terceiro e último valor da classe.
#
# Até a rodada 10, `_decifrar_lote` decifrava o e-mail de todos os candidatos no
# começo do tick e o envio usava aquele valor. Quem trocasse de e-mail durante o
# lote recebia no ANTIGO. A rodada 8 registrou isso e NÃO consertou, com a razão
# "não é consentimento, o endereço era da mesma pessoa" — razão que o
# apontamento seguinte derrubou: endereço que a pessoa REMOVEU da conta pode não
# ser mais dela (e-mail de trabalho de um emprego que ela deixou), e aí mandar
# "sua cobrança está pendente" é divulgar situação de pagamento a TERCEIRO, não
# entregar tarde.
#
# O conserto veio de graça: `lembrete_ainda_vale` já lia a linha, então passou a
# devolver `email`/`email_enc` frescos e a decriptação foi para o ponto do envio.
#
# CONTROLE NEGATIVO — em `core/services/payment_reminder.py`, decifre a partir
# de `_linha_do_funil` (o snapshot) em vez de `atual` (a leitura fresca):
#     VERMELHO: test_email_trocado_durante_o_lote_vai_para_o_novo
#               test_audit_de_pii_so_registra_quem_foi_contatado (`_lote.py`) —
#               a mesma injeção reprova os dois, porque decifrar do snapshot
#               também volta a decifrar candidato que não recebe.
# Regra dos controles: predicado citado, só os VERMELHOS nomeados, sem
# `N passed` — ver `docs/controles_declarados.md`.

# CONTROLE POSITIVO: test_lembrete_legitimo_continua_saindo (acima) e a segunda
# asserção deste teste — o endereço novo REALMENTE recebe. Sem ela, uma
# implementação que não mandasse para ninguém passaria.
# ──────────────────────────────────────────────────────────────────────────────

def test_email_trocado_durante_o_lote_vai_para_o_novo(user_id, monkeypatch):
    """O e-mail da conta muda depois do snapshot: o lembrete tem de ir para o
    endereço NOVO, e nada pode ir para o removido.

    As duas asserções são necessárias e não são a mesma: "não foi para o antigo"
    é a divulgação evitada; "foi para o novo" é a entrega preservada.
    """
    from core.services import email_service

    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    antigo = f"dun-{user_id}@t.local"
    novo = f"novo-{user_id}@t.local"

    destinos: list = []
    monkeypatch.setattr(email_service, "send_payment_reminder_email",
                        lambda to, dash="": destinos.append(to) or True)

    # `email_enc = null` de propósito: o caminho legado (coluna em claro) e o
    # cifrado convergem no mesmo `_resolver_email`, e este teste é sobre a
    # FRESCURA do endereço, não sobre cripto — o caminho cifrado tem teste
    # próprio em `test_payment_reminder_lote.py`.
    chamadas = _mexer_depois_da_query(
        monkeypatch,
        "update auth_accounts"
        "   set email = 'novo-' || user_id || '@t.local', email_enc = null"
        " where user_id = %s",
        user_id)

    _tick()

    assert chamadas and chamadas[0] >= 1, \
        "o funil não devolveu a conta — o teste mediria nada"
    assert antigo not in destinos, \
        "lembrete foi para o endereço que a pessoa removeu da conta"
    assert novo in destinos, "o endereço novo não recebeu o lembrete"
