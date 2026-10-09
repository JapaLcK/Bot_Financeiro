"""Sinal da compra de cartão do Open Finance (PR 0) — formato da PRODUÇÃO, banco real.

Na produção o cartão vem `amount > 0` / `type: DEBIT` para a compra e `amount < 0` /
`type: CREDIT` para estorno e pagamento (doc do campo `amount` da Pluggy). O sandbox
Pluggy Bank mostrou o oposto e o código antigo foi escrito em cima dele: na produção havia
compras gravadas como estorno (contagens medidas em 2026-10-09, só contagens, sem valores). A correção dos dados é o sync derivar do espelho (`valor`,
`is_refund`, `tipo`) e tirar da fatura o pagamento que o classificador perdia.

CONTROLES NEGATIVOS (rodados na entrega, ver o relato):
  • sinal: `sinal_cartao_of` de volta a `(-v, v > 0, ...)` → grupos 1 a 4 e 6 a 7 vermelhos;
  • regra: `pagamento_no_cartao` reduzida a `is_credit_card_payment` → 0, 1, 4, 6 e 7 vermelhos;
  • só o insert corrigido, a propagação do sync velha → 4 vermelho (o sync reinverte);
  • remoção do pagamento tirada do loop (só o `continue`) → 4 vermelho;
  • guarda `valor=%s and bill_id` do update tirada → 5 vermelho.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest

import core.services.pluggy_sync as ps
import db
import db.cards as cards
import db.open_finance as of
from core.services.cashflow_snapshot import valores_fatura
from db.resumo_mes import mes_de, totais
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import conecta, q
from utils_date import today_tz

ITEM = {"status": "UPDATED", "executionStatus": "SUCCESS",
        "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z"}}}
BANCO = {"id": "acc-banco", "name": "Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "currencyCode": "BRL", "balance": "1000.00"}
CARTAO = {"id": "acc-cartao", "name": "Nubank Mastercard", "type": "CREDIT", "subtype": "CREDIT_CARD",
          "currencyCode": "BRL", "balance": "90",
          "creditData": {"balanceCloseDate": "2026-10-20", "balanceDueDate": "2026-10-28"}}


def pluggy_tx(ident, amount, category, desc, extra=None):
    """Transação de cartão como a produção manda: compra DEBIT positiva, crédito CREDIT negativo."""
    return {"id": ident, "description": desc, "amount": amount, "category": category,
            "type": "DEBIT" if amount > 0 else "CREDIT",
            "date": f"{today_tz().isoformat()}T12:00:00.000Z", **(extra or {})}


A = ("a", 120, "Shopping", "Loja do bairro")
B = ("b", -30, "Transfers", "Estorno loja")                       # estorno real
C = ("c", -500, "Credit card payment", "Pagamento recebido")      # pagamento (regra comum)
D = ("d", -400, "Transfer - Internal", "Pagamento em 05/10")      # pagamento só pela categoria


@pytest.fixture
def rodar(monkeypatch):
    """`rodar(uid, [txs])` = um `sync_pluggy_item` inteiro com a Pluggy simulada."""
    estado = {"txs": []}
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {**ITEM, "id": i})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [BANCO, CARTAO])
    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        lambda acc, k=None, **kw: estado["txs"] if acc == CARTAO["id"] else [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_recurring_payments", lambda i, k=None: [])

    def _rodar(uid, txs, item=None):
        item = item or f"item-sinal-{uid}"
        if not q("select 1 from open_finance_connections where user_id=%s and provider_item_id=%s",
                 (uid, item), True):
            conecta(uid, item)
        estado["txs"] = [pluggy_tx(*t) for t in txs]
        return ps.sync_pluggy_item(item)
    return _rodar


@pytest.fixture
def regra_velha(monkeypatch):
    """`with regra_velha(): ...` roda com a regra de antes do PR: sinal invertido e pagamento
    só pela regra comum. É como se semeia o estado que a produção tem hoje."""
    @contextmanager
    def _velha():
        def sinal(amount):
            v = Decimal(str(amount))
            return -v, v > 0, "estorno" if v > 0 else "credito"
        with monkeypatch.context() as m:
            m.setattr(cards, "sinal_cartao_of", sinal)
            m.setattr(of, "sinal_cartao_of", sinal)
            m.setattr(of, "pagamento_no_cartao", lambda a, c, d: of.is_credit_card_payment(c, d))
            yield
    return _velha


def linhas(uid):
    """{id da Pluggy: linha do cartão} das compras ainda ligadas ao espelho."""
    return {r["pid"]: r for r in q(
        "select ct.id, ct.valor, ct.is_refund, ct.tipo, o.provider_transaction_id as pid "
        "from credit_transactions ct join open_finance_transactions o on o.imported_credit_tx_id = ct.id "
        "where ct.user_id=%s", (uid,), True)}


def faturas(uid):
    return [(r["total"], r["status"]) for r in q(
        "select total, status from credit_bills where user_id=%s order by id", (uid,), True)]


def sem_vinculo(uid, pid):
    """O espelho existe e não aponta para nenhuma linha de cartão (em todas as conexões)."""
    rows = q("select o.imported_credit_tx_id as v from open_finance_transactions o "
             "join open_finance_accounts a on a.id=o.account_id join open_finance_connections c "
             "on c.id=a.connection_id where c.user_id=%s and o.provider_transaction_id=%s",
             (uid, pid), True)
    return bool(rows) and all(r["v"] is None for r in rows)


# ── 0. a regra do pagamento no cartão (tabela) ──────────────────────────────

@pytest.mark.parametrize("amount,category,desc,esperado", [
    (-500, "Transfer - Internal", "Pagamento em 05/10", True),
    (500, "Transfer - Internal", "Compra", False),      # débito nunca é pagamento pela categoria
    (-200, "Transfer - Cash", "Algo", True),
    (-100, "Same person transfer - PIX", "Pix", True),
    (-500, "Credit card payment", "Fatura", True),      # a regra comum continua valendo
    (-30, "Transfers", "Estorno loja", False),          # estorno de loja não é pagamento
    (-80, "Shopping", "Devolução", False),
    (-50, "Cashback", "Resgate de pontos", False),      # por isso não reusa o classificador do BANK
])
def test_pagamento_no_cartao_tabela(amount, category, desc, esperado):
    assert of.pagamento_no_cartao(Decimal(amount), category, desc) is esperado


# ── 1. a conversa: sync real, depois o WhatsApp ─────────────────────────────

def test_conversa_compra_estorno_e_pagamento(uid_pro, ia_fora, rodar):
    rodar(uid_pro, [A, B, C, D])

    # compra é compra, estorno é estorno, os dois pagamentos não entram
    ls = linhas(uid_pro)
    assert set(ls) == {"a", "b"}
    assert (ls["a"]["valor"], ls["a"]["is_refund"], ls["a"]["tipo"]) == (120, False, "credito")
    assert (ls["b"]["valor"], ls["b"]["is_refund"], ls["b"]["tipo"]) == (-30, True, "estorno")
    assert sem_vinculo(uid_pro, "c") and sem_vinculo(uid_pro, "d")
    assert faturas(uid_pro) == [(90, "open")]
    (card,) = [c["id"] for c in db.list_cards(uid_pro)]
    assert cards.get_card_credit_usage(uid_pro, card) == 90

    # o Saiu do mês da fatura: a compra entra; estorno e pagamentos não
    fim = q("select period_end from credit_bills where user_id=%s", (uid_pro,), True)[0]["period_end"]
    with db.get_conn() as conn, conn.cursor() as cur:
        t = totais(cur, uid_pro, *mes_de(fim))
        (fatura,) = cards.ler_faturas(cur, uid_pro)
    assert (t["saiu"], t["n_cartao"]) == (120, 1)
    assert valores_fatura(fatura)[2:] == (Decimal(90), False)   # restante 90, sem "a conferir"

    # a conversa: um gasto manual depois e o saldo, com o estado real do banco
    manda(uid_pro, "gastei 50 no mercado")
    manda(uid_pro, "dinheiro")
    saldo = manda(uid_pro, "saldo")
    assert "R$ 90,00" in saldo, saldo


def test_controle_positivo_estorno_real_abate(uid_pro, rodar):
    """Um conserto por `abs()`, ou uma regra que pegasse todo crédito do cartão, falha aqui."""
    rodar(uid_pro, [A, B])
    assert faturas(uid_pro) == [(90, "open")]
    assert linhas(uid_pro)["b"]["is_refund"] is True


# ── 2. parcela ───────────────────────────────────────────────────────────────

def test_parcela_positiva_e_compra(uid_pro, rodar):
    meta = {"creditCardMetadata": {"installmentNumber": 1, "totalInstallments": 3, "totalAmount": 300}}
    rodar(uid_pro, [("p1", 100, "Shopping", "Loja parcelada", meta)])
    (ct,) = q("select valor, is_refund, installment_no, installments_total, group_id "
              "from credit_transactions where user_id=%s", (uid_pro,), True)
    assert (ct["valor"], ct["is_refund"], ct["installment_no"], ct["installments_total"]) == (100, False, 1, 3)
    assert cards.get_installment_group_summaries(uid_pro, [ct["group_id"]])


# ── 3. correção vinda da Pluggy ─────────────────────────────────────────────

def test_correcao_de_valor_vinda_da_pluggy(uid_pro, rodar):
    rodar(uid_pro, [A])
    rodar(uid_pro, [("a", 150, "Shopping", "Loja do bairro")])
    assert (linhas(uid_pro)["a"]["valor"], faturas(uid_pro)) == (150, [(150, "open")])
    assert db.sync_imported_open_finance_updates(uid_pro)["credit_updated"] == 0   # o 3º sync é no-op


# ── 4. o dado legado (o deploy) ─────────────────────────────────────────────

def test_deploy_corrige_o_legado_e_remove_o_pagamento(uid_pro, rodar, regra_velha):
    with regra_velha():
        rodar(uid_pro, [A, B, D])
    # o estado da produção hoje: A estorno, B compra, D (pagamento) compra
    assert linhas(uid_pro)["a"]["valor"] == -120 and set(linhas(uid_pro)) == {"a", "b", "d"}
    assert faturas(uid_pro) == [(310, "open")]

    rodar(uid_pro, [A, B, D])                       # o código novo entra no sync seguinte

    ls = linhas(uid_pro)
    assert set(ls) == {"a", "b"}                    # D apagado
    assert (ls["a"]["valor"], ls["a"]["is_refund"], ls["a"]["tipo"]) == (120, False, "credito")
    assert (ls["b"]["valor"], ls["b"]["is_refund"], ls["b"]["tipo"]) == (-30, True, "estorno")
    assert sem_vinculo(uid_pro, "d")
    assert faturas(uid_pro) == [(90, "open")]
    # idempotente: nada a corrigir, e o import não traz o D de volta
    assert db.sync_imported_open_finance_updates(uid_pro)["credit_updated"] == 0
    assert db.import_open_finance_credit(uid_pro)["inserted"] == 0
    assert faturas(uid_pro) == [(90, "open")]


# ── 5. guarda: a linha muda ou some entre o select e o update ───────────────

@pytest.mark.parametrize("o_que", ["desfeita", "editada"])
def test_guarda_linha_mexida_no_meio_do_sync(uid_pro, rodar, regra_velha, monkeypatch, o_que):
    with regra_velha():
        rodar(uid_pro, [A])
    ct = linhas(uid_pro)["a"]["id"]
    depois_do_outro = []
    real = of.categoria_pigbank

    def mexe_no_meio(categoria):
        if not depois_do_outro:                      # 1ª chamada, dentro do loop de correção
            if o_que == "desfeita":
                cards.remove_single_credit_transaction(uid_pro, ct)  # some (desfazer do OF é recusado)
            else:                                    # edição do valor, com a fatura acompanhando
                q("update credit_transactions set valor=-100 where id=%s", (ct,))
                q("update credit_bills set total=-100 where user_id=%s", (uid_pro,))
            depois_do_outro.append(faturas(uid_pro))
        return real(categoria)

    monkeypatch.setattr(of, "categoria_pigbank", mexe_no_meio)
    assert of._sync_imported_credit_updates(uid_pro, None) == 0
    assert depois_do_outro and faturas(uid_pro) == depois_do_outro[0]   # o sync não somou a diferença


# ── 6. isolamento entre usuários ────────────────────────────────────────────

def test_sync_de_um_usuario_nao_toca_o_outro(uid_pro, rodar, regra_velha):
    a, b = uid_pro, uid_pro + 1
    db.ensure_user(b)
    with regra_velha():
        rodar(a, [A, D])
        rodar(b, [A, D])                             # mesmo id na Pluggy, mesmo valor
    antes_b = (linhas(b), faturas(b))

    rodar(a, [A, D])                                 # corrige só A
    assert set(linhas(a)) == {"a"} and faturas(a) == [(120, "open")]
    assert (linhas(b), faturas(b)) == antes_b and set(linhas(b)) == {"a", "d"}

    rodar(b, [A, D])                                 # e agora só B
    assert set(linhas(b)) == {"a"} and faturas(b) == [(120, "open")]


# ── 7. reconexão: duas conexões, o mesmo cartão ─────────────────────────────

def _reconecta_com_legado(uid, rodar, regra_velha):
    with regra_velha():
        rodar(uid, [A, D], item=f"velha-{uid}")
    assert faturas(uid) == [(280, "open")]           # -120 + 400
    rodar(uid, [A, D], item=f"nova-{uid}")           # reconexão do mesmo cartão


def test_reconexao_com_legado_corrige_a_compra_uma_vez(uid_pro, rodar, regra_velha):
    _reconecta_com_legado(uid_pro, rodar, regra_velha)
    assert linhas(uid_pro)["a"]["valor"] == 120      # A: uma linha só, corrigida uma vez
    assert q("select count(*) as n from credit_transactions where user_id=%s and valor=120",
             (uid_pro,), True)[0]["n"] == 1
    assert faturas(uid_pro) == [(120, "open")]       # o total: a diferença não entra duas vezes


def test_reconexao_com_legado_remove_o_pagamento(uid_pro, rodar, regra_velha):
    """D legado fica ligado só ao espelho da conexão VELHA, que o `LATEST_TRANSACTION_SQL` não
    devolve (a nova tem o mesmo tx; o import da nova pula D e nunca o liga). Controle negativo:
    sem o 2º select (`pagamentos_no_cartao_legados`) a fatura fica em +400 e este teste vermelho."""
    _reconecta_com_legado(uid_pro, rodar, regra_velha)
    assert faturas(uid_pro) == [(120, "open")] and sem_vinculo(uid_pro, "d")
    rodar(uid_pro, [A, D], item=f"velha-{uid_pro}")  # e a conexão velha sincronizando não o traz de volta
    assert faturas(uid_pro) == [(120, "open")] and sem_vinculo(uid_pro, "d")
