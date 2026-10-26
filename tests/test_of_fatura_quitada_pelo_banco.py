"""Fatura do cartão Open Finance quitada pelo banco (Fase 1b, PR 2a) — banco real, Pluggy por monkeypatch.

`cobertura` / `pago_por_fatura_pig` (puras) e o sync inteiro: `quitar_pelo_banco` grava em
`credit_bills.paid_amount` o que o banco pagou (`of_card_bills`), compra a compra pelo `billId`.

CONTROLES NEGATIVOS (rodados na entrega, ver o relato):
  • chamada de `quitar_pelo_banco` tirada de pluggy_sync.py -> fatura_paga_pelo_banco_quita_e_sai_da_previsao,
    pagamento_parcial, idempotente_e_absoluto, reconexao_recalcula_pela_conta_nova_sem_dobrar e
    conversa_fatura_quitada_nao_aparece_em_aberto vermelhos;
  • `paid_amount = b.paid_amount + novo` no UPDATE -> idempotente_e_absoluto e pago_manual_antigo_e_sobrescrito vermelhos;
  • condição de fechada tirada de `cobertura` -> cobertura_fatura_que_fica_fora e
    cobertura_fechada_pelo_fechamento_e_nao_pelo_vencimento vermelhos;
  • guarda de 20 a 40 dias tirada da N+1 -> `test_n_mais_1_guarda_de_vizinha` vermelho;
  • `greatest(0, ...)` tirado do UPDATE -> fatura_so_de_estornos_nunca_fica_negativa vermelho;
  • `of_card_bills` juntado sem a conta -> isolamento_mesmo_id_de_fatura_em_dois_usuarios e
    reconexao_recalcula_pela_conta_nova_sem_dobrar vermelhos;
  • aparo do pago tirado de disconnect_open_finance_connection -> desconectar_apara_o_pago_do_cartao_que_sobrevive e
    desconectar_apara_so_ate_zero_em_fatura_de_total_zero_ou_negativo vermelhos;
  • `APARAR_PAGO_SQL` tirado de `quitar_pelo_banco` -> compra_casada_que_sumiu_nao_deixa_pago_velho,
    compra_casada_que_sumiu_com_compra_manual_apara_ate_o_total e quitar_sem_regra_ainda_apara vermelhos;
  • where do aparo reduzido a `paid_amount > greatest(total, 0)` (sem o ramo pago == total) ->
    desconectar_deixando_pago_igual_ao_total_marca_paga vermelho;
  • where do aparo ampliado para `or total > 0` (toca fatura aberta com pago menor) ->
    aparo_nao_toca_fatura_aberta_com_pago_menor vermelho;
  • fatura com total <= TOL fora de `cobertura` (em vez de parte 0) -> total_zero_entra_com_parte_zero e
    banco_zera_compra_e_fatura_paga_reabre_com_total_zero vermelhos (banco_estorna_tudo_zera_o_pago fica verde: o aparo
    também o cura);
  • checagem de sinais misturados tirada de `_soma` -> pagamentos_de_sinais_misturados vermelho;
  • o teto do total tem DUAS guardas (o `least(b.total, ...)` do UPDATE e o aparo logo depois): tirar só uma deixa tudo
    verde; tirando as duas -> estorno_sem_bill_id_o_teto vermelho;
  • filtro por par `(conta, fatura) in frescas` tirado de `quitar_pelo_banco` (todas as linhas da conta) ->
    snapshot_velho_nao_quita_compra_nova[bills], [gravacao] e fatura_velha_nao_quita_compra_nova[invalida] e [sumiu]
    vermelhos (pago 150 de 150); `salvar_faturas_do_banco` devolvendo os pares de `itens` em vez de `validas` ->
    test_of_card_bills_ingestao::test_retorno_so_traz_os_pares_das_faturas_validas vermelho;
  • `cobertura` devolvendo `{}` no empate (em vez das zeradas) -> zeradas_entram_mesmo_sem_regra_nem_fatura_de_valor e
    banco_corrige_fatura_paga_para_total_zero_zera_o_pago_mesmo_sem_regra vermelhos;
  • `card_id` tirado do where de `APARAR_PAGO_SQL` -> aparo_nao_toca_cartao_manual_do_mesmo_usuario vermelho;
  • CASE do `_GRAVA_SQL` sem o `or novo <= 0` (volta a 'open' só com `b.total > novo`) ->
    banco_zera_compra_e_fatura_paga_reabre_com_total_zero vermelho (fica 0/0 'paid' com paid_at velho);
  • `APARAR_PAGO_SQL` sem o ramo da zumbi (`status = 'paid' and least(paid_amount, greatest(total, 0)) <= 0`) ->
    aparo_reabre_fatura_zumbi_total_zero_paga vermelho; positivos: aparo_nao_toca_fatura_aberta_com_pago_menor e a 100/100
    paga que nao reabre (banco_zera_compra_e_fatura_paga_reabre_com_total_zero, 1a fatura);
  • CASE do `APARAR_PAGO_SQL` com `total > 0` no lugar de `total > 0 and paid_amount > 0` ->
    aparo_reabre_fatura_paga_com_total_positivo_e_pago_zero vermelho.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import core.services.pluggy_sync as ps
import db
import db.of_card_bills as of_card_bills
from core.services.cashflow_snapshot import carregar
from db.of_card_bills import (
    APARAR_PAGO_SQL, COMPRAS_POR_FATURA_SQL, TOL, _fracao, cobertura, pago_por_fatura_pig, quitar_pelo_banco)
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import conecta, pagamento_legado, q
from tests.test_of_card_bills_ingestao import carimbado, gravadas
from tests.test_of_cartao_sinal import BANCO, CARTAO, ITEM, pluggy_tx
from utils_date import today_tz

D = Decimal
HOJE = date(2026, 10, 1)


# ── funções puras ────────────────────────────────────────────────────────────

def F(bid, dia, total, pay=None, fecha=True, moeda="BRL", chg=None):
    """Fatura do banco como `FATURAS_DO_CARTAO_SQL` devolve. `dia` = dias desde 2026-01-01 do vencimento."""
    due = date(2026, 1, 1) + timedelta(days=dia)
    return {"account_id": 1, "provider_bill_id": bid, "due_date": due,
            "closing_date": due - timedelta(days=7) if fecha else None, "total_amount": D(total),
            "currency": moeda, "payments": pay if isinstance(pay, list) or pay is None else [{"amount": pay}],
            "charges": chg}


BASE = F("ok", 0, 100, 100)    # uma fatura exata: dá à regra "própria" o placar que a faz valer


def test_cobertura_propria():
    r = cobertura([BASE, F("cheia", 30, 80, 80), F("parcial", 60, 100, 40), F("acima", 90, 50, 70)], HOJE)
    assert r == {"ok": 1, "cheia": 1, "parcial": D("0.4"), "acima": 1}


@pytest.mark.parametrize("fatura", [
    F("a", 400, 100, 100),                                  # fechada só depois de HOJE
    F("a", 400, 100, 100, fecha=False),                     # sem fechamento, vencimento futuro
    F("a", 30, 100, 100, moeda="USD"),                      # não-BRL
    F("a", 30, 100, [{"amount": "abc"}]),                   # pagamento ilegível: nunca lido como 0
    F("a", 30, 100, [{"amount": 10}, {"amount": None}]),
    F("a", 30, 100, [{"amount": True}]),
])
def test_cobertura_fatura_que_fica_fora(fatura):
    assert "a" not in cobertura([BASE, fatura], HOJE)


def test_cobertura_total_zero_entra_com_parte_zero():
    """Fechada com total <= TOL entra com parte 0, nunca 1 (`_fracao` devolveria 1) e sem pontuar."""
    assert cobertura([BASE, F("z", 30, 0, None), F("t", 60, TOL, None), F("n", 90, -20, None)], HOJE) \
        == {"ok": 1, "z": 0, "t": 0, "n": 0}
    assert cobertura([BASE, F("z", 30, 0, None, moeda="USD"), F("a", 400, 0, None)], HOJE) == {"ok": 1}   # outras barreiras valem


def test_cobertura_pagamentos_de_sinais_misturados_ficam_fora():
    mist = [{"amount": 100}, {"amount": -100}]
    r = cobertura([BASE, F("m", 30, 100, mist), F("neg", 60, 100, [{"amount": -40}, {"amount": -60}])], HOJE)
    assert "m" not in r and r["neg"] == 1                     # todos do mesmo sinal: soma dos absolutos


def test_cobertura_fechada_pelo_fechamento_e_nao_pelo_vencimento():
    dias = lambda d: (d - date(2026, 1, 1)).days
    vence_depois = F("v", dias(HOJE + timedelta(days=3)), 60, 60)             # fechou há 4 dias, vence em 3
    fecha_hoje = {**F("h", dias(HOJE - timedelta(days=3)), 60, 60), "closing_date": HOJE}   # vencida, mas fecha hoje
    r = cobertura([BASE, fecha_hoje, vence_depois], HOJE)
    assert r["v"] == 1 and "h" not in r


def test_fracao_fronteira_da_tolerancia():
    assert _fracao(D(100) - TOL, D(100)) == 1
    assert _fracao(D(100) - TOL - D("0.01"), D(100)) == D("0.9994")


def test_cobertura_aceita_moeda_nula_e_fechada_pelo_vencimento():
    r = cobertura([BASE, F("a", 30, 60, 60, moeda=None), F("b", 60, 60, 60, fecha=False)], HOJE)
    assert r["a"] == 1 and r["b"] == 1


def test_regra_da_conta_do_dono_propria_ganha():
    """13 faturas, 10 exatas pela própria, 0 pela N+1."""
    fs = [F(f"b{i}", 30 * i, 100 + i, 100 + i if i < 10 else 0) for i in range(13)]
    r = cobertura(fs, date(2027, 6, 1))
    assert len(r) == 13 and sum(1 for v in r.values() if v == 1) == 10


def test_regra_da_conta_pagamento_deslocado_um_mes_nao_quita(capsys):
    fs = [F(f"b{i}", 30 * i, 100 * (i + 1), 100 * i) for i in range(4)]   # o pagamento de Y+1 cobre Y
    assert cobertura(fs, HOJE) == {}
    assert "[of_card_bills] conta=1 regra indefinida" in capsys.readouterr().out


def test_regra_da_conta_empate_zero_a_zero(capsys):
    assert cobertura([F("a", 0, 100), F("b", 30, 120)], HOJE) == {}
    assert "regra indefinida" in capsys.readouterr().out


def test_zeradas_entram_mesmo_sem_regra_nem_fatura_de_valor():
    """Todas as fechadas com total <= TOL (`fech` vazio) e empate com fatura de valor: as zeradas valem 0, não `{}`."""
    assert cobertura([F("z", 0, 0), F("t", 30, TOL)], HOJE) == {"z": 0, "t": 0}
    assert cobertura([F("a", 0, 100), F("b", 30, 120), F("z", 60, 0)], HOJE) == {"z": 0}   # empate: só a de valor cai


def test_n_mais_1_guarda_de_vizinha():
    """Par a 50 dias fica fora da N+1: sem a guarda, ela empata com a própria e a conta não quita."""
    fs = [F("a", 0, 100, 0), F("b", 50, 200, 100), F("c", 100, 50, 50)]
    assert cobertura(fs, HOJE) == {"a": 0, "b": D("0.5"), "c": 1}


@pytest.mark.parametrize("gap,vale", [(20, True), (40, True), (19, False), (41, False)])
def test_n_mais_1_fronteira_de_20_a_40_dias(gap, vale):
    """a->b dentro da janela dá à N+1 o empate com a própria (conta indefinida, {}); fora, a própria vale."""
    fs = [F("a", 0, 100, 0), F("b", gap, 200, 100), F("c", gap + 30, 50, 50)]
    assert (cobertura(fs, HOJE) == {}) is vale


def test_placar_desempata_pelo_segundo_elemento_a_favor_da_propria():
    fs = [F("a", 0, 100, 100), F("b", 30, 100, 150), F("c", 60, 50, 100)]    # exatas 1 x 1; cobertas 3 x 2
    assert set(cobertura(fs, HOJE)) == {"a", "b", "c"}


def test_placar_desempata_pelo_segundo_elemento_a_favor_da_n_mais_1():
    fs = [F("a", 0, 100, 0), F("b", 30, 100, 100), F("c", 60, 30, 0), F("d", 90, 100, 40)]   # exatas 1 x 1; cobertas 1 x 2
    assert cobertura(fs, HOJE) == {}


def test_n_mais_1_desconta_os_encargos_da_seguinte():
    """b pagou 130 = 100 de a + 30 de encargos: com a subtração a N+1 empata (exata) e a conta não quita."""
    fs = [F("a", 0, 100, 0), F("b", 30, 200, 130, chg=[{"amount": 30}]), F("c", 60, 50, 50)]
    assert cobertura(fs, HOJE) == {}


def test_encargos_ilegiveis_na_seguinte_nao_derrubam():
    assert cobertura([BASE, F("b", 30, 100, 100, chg="abc")], HOJE) == {"ok": 1, "b": 1}


def test_pago_por_fatura_pig():
    compras = [{"bill_id": 1, "valor": D(60), "fatura_banco": "Y"}, {"bill_id": 2, "valor": D(40), "fatura_banco": "Y"},
               {"bill_id": 2, "valor": D(-10), "fatura_banco": "Y"},            # estorno
               {"bill_id": 3, "valor": D(99), "fatura_banco": "desconhecida"},  # fora de `frac`
               {"bill_id": 4, "valor": D(5), "fatura_banco": None}]
    assert pago_por_fatura_pig(compras, {"Y": D(1)}) == {1: D(60), 2: D(30)}
    assert pago_por_fatura_pig(compras, {"Y": D("0.5")}) == {1: D(30), 2: D(15)}
    assert pago_por_fatura_pig(compras, {"Y": D("0.333")}) == {1: D("19.98"), 2: D("9.99")}


# ── sync inteiro ─────────────────────────────────────────────────────────────

def dia(n):
    return today_tz() - timedelta(days=n)


def fat(bid, total, pago=None, fecha=40):
    """Fatura da Pluggy: fechou há `fecha` dias (negativo = fecha no futuro), vence 7 dias depois."""
    return {"id": bid, "dueDate": f"{dia(fecha - 7)}T00:00:00.000Z", "billClosingDate": f"{dia(fecha)}T00:00:00.000Z",
            "totalAmount": str(total), "totalAmountCurrencyCode": "BRL",
            "payments": [{"amount": pago}] if pago else []}


def compra(ident, valor, bill, dias):
    return pluggy_tx(ident, valor, "Shopping" if valor > 0 else "Transfers",
                     "Loja " + ident if valor > 0 else "Estorno loja",
                     extra={"creditCardMetadata": {"billId": bill}, "date": f"{dia(dias)}T12:00:00.000Z"})


# Uma fatura anterior exata: com ela a regra "própria" vale e o caso do teste é lido.
P0, F0 = ("p0", 100, "bill-0", 80), fat("bill-0", 100, 100, 70)
P1 = ("p1", 120, "bill-1", 35)
FRESCAS = {(CARTAO["id"], "bill-0"), (CARTAO["id"], "bill-1")}   # o que `salvar_faturas_do_banco` devolveria


@pytest.fixture
def rodar(monkeypatch):
    """`rodar(uid, compras, faturas, item=None)` = um `sync_pluggy_item` inteiro com a Pluggy simulada."""
    estado = {"txs": [], "faturas": []}
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {**ITEM, "id": i})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [BANCO, CARTAO])
    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        lambda acc, k=None, **kw: estado["txs"] if acc == CARTAO["id"] else [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_recurring_payments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_bills", lambda conta, key=None, **kw: estado["faturas"])

    def _rodar(uid, compras, faturas, item=None):
        item = item or f"item-qb-{uid}"
        if not q("select 1 from open_finance_connections where user_id=%s and provider_item_id=%s",
                 (uid, item), True):
            conecta(uid, item)
        estado["txs"] = [compra(*c) if isinstance(c, tuple) else c for c in compras]
        estado["faturas"] = faturas
        return ps.sync_pluggy_item(item)
    return _rodar


def pig(uid):
    """[(total, paid_amount, status)] das faturas do Pig, da mais antiga para a mais nova."""
    return [(r["total"], r["paid_amount"], r["status"]) for r in q(
        "select total, paid_amount, status from credit_bills where user_id=%s order by period_start", (uid,), True)]


def na_previsao(uid):
    snap = carregar(uid, today_tz(), today_tz() + timedelta(days=60))
    return [o for o in snap.ocorrencias if o.fonte == "fatura"]


def manual_pay_alvo(uid):
    """(id do cartão, nome, id da fatura mais nova) para o pagamento manual de antes."""
    c = q("select id, name from credit_cards where user_id=%s", (uid,), True)[0]
    b = q("select id from credit_bills where user_id=%s order by period_start desc limit 1", (uid,), True)[0]["id"]
    return c["id"], c["name"], b


def test_fatura_paga_pelo_banco_quita_e_sai_da_previsao(uid_pro, rodar):
    rodar(uid_pro, [P1], [fat("bill-1", 120)])
    assert pig(uid_pro) == [(120, 0, "open")] and len(na_previsao(uid_pro)) == 1   # antes: aparece

    rodar(uid_pro, [P1], [fat("bill-1", 120, 120)])
    assert pig(uid_pro)[0][1:] == (120, "paid")
    assert q("select paid_at from credit_bills where user_id=%s", (uid_pro,), True)[0]["paid_at"] is not None
    assert na_previsao(uid_pro) == []


def test_pagamento_parcial(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 60, "open")]


def test_o_mesmo_pagamento_nao_quita_duas_faturas(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 0, "open")]


def test_idempotente_e_absoluto(uid_pro, rodar):
    for pago in (60, 60, 90):                    # o 3º sync é maior: sobrescreve, não soma
        rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, pago)])
        assert pig(uid_pro)[1][:2] == (120, pago), pig(uid_pro)


def test_fatura_aberta_com_pagamento_nao_quita_nada(uid_pro, rodar):
    """O defeito do v2: o pagamento que aparece na fatura aberta quitava as anteriores."""
    rodar(uid_pro, [("q0", 100, "bill-0", 120), ("q1", 100, "bill-1", 70), ("q2", 70, "bill-2", 20)],
          [fat("bill-0", 100, None, 110), fat("bill-1", 100, 100, 60), fat("bill-2", 70, 70, -10)])
    assert pig(uid_pro) == [(100, 0, "open"), (100, 100, "paid"), (70, 0, "open")]


def test_estorno_na_mesma_fatura_e_pagamento_que_diminui_reabre(uid_pro, rodar):
    c, e = ("c1", 100, "bill-1", 35), ("e1", -30, "bill-1", 35)
    rodar(uid_pro, [P0, c, e], [F0, fat("bill-1", 70, 70)])
    assert pig(uid_pro)[1] == (70, 70, "paid")
    rodar(uid_pro, [P0, c, e], [F0, fat("bill-1", 70, 35)])                        # o banco desfez metade do pagamento
    assert pig(uid_pro)[1] == (70, 35, "open")                                     # recalcula e reabre


def test_estorno_sem_bill_id_o_teto_do_total_do_pig_segura(uid_pro, rodar):
    """Limite declarado: o estorno sem `billId` não entra no pago (100, não 70); o `least(b.total, ...)` do UPDATE
    e o aparo que vem depois seguram (só tirando os dois este teste fica vermelho)."""
    est = pluggy_tx("e1", -30, "Transfers", "Estorno loja", extra={"date": f"{dia(35)}T12:00:00.000Z"})
    rodar(uid_pro, [P0, ("c1", 100, "bill-1", 35), est], [F0, fat("bill-1", 70, 70)])
    assert pig(uid_pro)[1] == (70, 70, "paid")


def test_paid_at_gravado_ao_quitar_mantido_se_ja_existia_e_zerado_ao_reabrir(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    antigo = q("update credit_bills set paid_at = '2026-01-01' where user_id=%s and total=120 returning paid_at",
               (uid_pro,), True)[0]["paid_at"]
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    assert q("select paid_at from credit_bills where user_id=%s and total=120", (uid_pro,), True)[0]["paid_at"] == antigo
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])                  # o banco desfez: reabre
    assert q("select paid_at from credit_bills where user_id=%s and total=120", (uid_pro,), True)[0]["paid_at"] is None


def _espelho_da_compra(uid):
    return q("select o.id, o.imported_credit_tx_id, o.raw from open_finance_transactions o"
             " join credit_transactions ct on ct.id = o.imported_credit_tx_id where ct.user_id=%s", (uid,), True)[0]


def _compras_do_cartao_of(uid):
    cid = q("select id from credit_cards where user_id=%s and open_finance_account_id is not null", (uid,), True)[0]["id"]
    return q(COMPRAS_POR_FATURA_SQL, {"card": cid, "uid": uid}, True)


def test_compras_por_fatura_pega_o_espelho_mais_novo(uid_pro, rodar):
    rodar(uid_pro, [P1], [fat("bill-1", 120)])
    e = _espelho_da_compra(uid_pro)
    assert [c["fatura_banco"] for c in _compras_do_cartao_of(uid_pro)] == ["bill-1"]
    q("insert into open_finance_transactions (account_id, provider_transaction_id, description, amount,"
      " transaction_date, raw, imported_credit_tx_id)"
      " select account_id, 'p1-v2', description, amount, transaction_date, jsonb_set(raw, '{creditCardMetadata,billId}',"
      " '\"bill-novo\"'), imported_credit_tx_id from open_finance_transactions where id=%s", (e["id"],))
    assert [c["fatura_banco"] for c in _compras_do_cartao_of(uid_pro)] == ["bill-novo"]


def test_compras_por_fatura_ignora_espelho_cuja_compra_esta_em_outro_cartao(uid_pro, rodar):
    rodar(uid_pro, [P1], [fat("bill-1", 120)])
    assert len(_compras_do_cartao_of(uid_pro)) == 1
    outro = db.create_card(uid_pro, "Outro cartao", closing_day=20, due_day=28)
    outro = outro["id"] if isinstance(outro, dict) else outro
    db.add_credit_purchase(uid_pro, outro, 10, "outros", "manual", dia(35))
    ct = _espelho_da_compra(uid_pro)["imported_credit_tx_id"]
    q("update credit_transactions set card_id=%s, bill_id=(select id from credit_bills where card_id=%s limit 1)"
      " where id=%s and user_id=%s", (outro, outro, ct, uid_pro))
    assert _compras_do_cartao_of(uid_pro) == []


def test_fatura_so_de_estornos_nunca_fica_negativa(uid_pro, rodar):
    rodar(uid_pro, [P0, ("e0", -30, "bill-1", 35)], [F0, fat("bill-1", -30)])
    assert pig(uid_pro)[1][:2] == (-30, 0)


def test_banco_estorna_tudo_zera_o_pago(uid_pro, rodar):
    """Fatura do Pig já paga (100/100) cujo banco passou a total 0: o pago volta a 0 (não fica 100 com total 0)."""
    c = ("c1", 100, "bill-1", 35)
    rodar(uid_pro, [P0, c], [F0, fat("bill-1", 100, 100)])
    assert pig(uid_pro)[1] == (100, 100, "paid")
    # estorno no MESMO dia da compra (35): o CARTAO fecha no dia 20 e, a 34 dias, o dia(35) caindo no 20 separava as duas
    # em 2 faturas (100 e -100) em vez de 1 de total 0 — vermelho em 2026-10-25, 11-24, 12-25, 2027-01-24, 02-24.
    rodar(uid_pro, [P0, c, ("e1", -100, "bill-1", 35)], [F0, fat("bill-1", 0)])
    assert pig(uid_pro)[1] == (0, 0, "open")


def test_banco_zera_compra_e_fatura_paga_reabre_com_total_zero(uid_pro, rodar, monkeypatch):
    """Convencao da casa (db/cards.py): 'paid' so com paid > 0. Compra casada + estorno igual levam o total do Pig a 0 e o
    banco a fatura a 0 (pago 0), mas a linha chega ao UPDATE ainda 'paid' com paid_at velho: tem de voltar a 'open' com
    paid_at nulo, nao ficar zumbi. O aparo sai de cena de proposito (e quem tambem cura o caso): isola o CASE do `_GRAVA_SQL`.
    Positivo: a 100/100 vizinha continua paga."""
    c = ("c1", 100, "bill-1", 35)
    rodar(uid_pro, [P0, c, ("e1", -100, "bill-1", 35)], [F0, fat("bill-1", 0)])  # 35: mesmo dia da compra, ver teste acima
    assert pig(uid_pro) == [(100, 100, "paid"), (0, 0, "open")]
    q("update credit_bills set status='paid', paid_at='2026-01-01' where user_id=%s and total=0", (uid_pro,))
    monkeypatch.setattr(of_card_bills, "APARAR_PAGO_SQL", "select %s, %s")
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), FRESCAS)
    assert pig(uid_pro) == [(100, 100, "paid"), (0, 0, "open")]
    assert q("select paid_at from credit_bills where user_id=%s and total=0", (uid_pro,), True)[0]["paid_at"] is None


def test_banco_total_zero_com_compra_casada_nao_quita(uid_pro, rodar):
    rodar(uid_pro, [P0, ("c1", 500, "bill-1", 35)], [F0, fat("bill-1", 0)])
    assert pig(uid_pro)[1] == (500, 0, "open")


def test_cura_status_open_com_pago_igual_ao_total(uid_pro, rodar):
    """Ramo 2 do WHERE do UPDATE: pago já é o total, só o status ficou para trás. O aparo (pago == total e aberta)
    cura o mesmo caso: este teste só fica vermelho sem o ramo 2 E sem esse ramo do aparo."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    q("update credit_bills set status='open', paid_at=null where user_id=%s and total=120", (uid_pro,))
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    assert pig(uid_pro)[1] == (120, 120, "paid")


def test_cura_status_paid_com_pago_menor(uid_pro, rodar):
    """Ramo 3 do WHERE do UPDATE: pago já é o do banco, só o status 'paid' ficou para trás."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    q("update credit_bills set status='paid' where user_id=%s and total=120", (uid_pro,))
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    assert pig(uid_pro)[1] == (120, 60, "open")


def test_isolamento_mesmo_id_de_fatura_em_dois_usuarios(uid_pro, rodar):
    outro = uid_pro + 1
    db.ensure_user(outro)
    rodar(uid_pro, [P1], [fat("bill-1", 120, 120, 20)])         # só este foi pago
    rodar(outro, [P1], [fat("bill-1", 120, None, 70)])          # mesmo provider_account_id e provider_bill_id
    assert pig(uid_pro) == [(120, 120, "paid")]
    assert pig(outro) == [(120, 0, "open")]


def test_reconexao_recalcula_pela_conta_nova_sem_dobrar(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)], item=f"item-a-{uid_pro}")
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 60, "open")]
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)], item=f"item-b-{uid_pro}")
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 120, "paid")]


def test_pago_manual_antigo_e_sobrescrito(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])
    cid, nome, bill = manual_pay_alvo(uid_pro)
    pagamento_legado(uid_pro, cid, nome, 50, bill)               # pagamento manual de antes
    assert pig(uid_pro)[1][1] == 50
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])                     # o banco não pagou
    assert pig(uid_pro)[1] == (120, 0, "open")
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    assert pig(uid_pro)[1] == (120, 120, "paid")


def test_desfazer_pagamento_manual_e_o_proximo_sync_devolve_o_pago_do_banco(uid_pro, rodar):
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])
    cid, nome, bill = manual_pay_alvo(uid_pro)
    launch = pagamento_legado(uid_pro, cid, nome, 50, bill)["launch_id"]
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])                # o banco grava o total
    assert pig(uid_pro)[1] == (120, 120, "paid")

    db.delete_launch_and_rollback(uid_pro, launch)                         # desconta como antes (sem guarda no cartão OF)

    assert pig(uid_pro)[1] == (120, 70, "open")
    assert q("select 1 from launches where id=%s and user_id=%s", (launch, uid_pro), True) == []   # o lançamento saiu
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])                # a escrita é absoluta: o banco manda de volta
    assert pig(uid_pro)[1] == (120, 120, "paid")


def test_desfazer_pagamento_em_fatura_sem_espelho_do_cartao_of_volta_a_zero(uid_pro, rodar):
    """Compra manual sem espelho: o banco não é dono desse pago, o desfazer tem de descontar."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])
    cid, nome, _ = manual_pay_alvo(uid_pro)
    db.add_credit_purchase(uid_pro, cid, 30, "outros", "manual", dia(200))
    antiga = q("select id from credit_bills where user_id=%s order by period_start limit 1", (uid_pro,), True)[0]["id"]
    launch = pagamento_legado(uid_pro, cid, nome, 30, antiga)["launch_id"]
    db.delete_launch_and_rollback(uid_pro, launch)
    assert pig(uid_pro)[0] == (30, 0, "open")


def test_desfazer_pagamento_em_cartao_manual_adotado_pelo_of_volta_a_zero(uid_pro, rodar):
    cid = db.create_card(uid_pro, "Nubank Mastercard", closing_day=20, due_day=28)
    cid = cid["id"] if isinstance(cid, dict) else cid
    db.add_credit_purchase(uid_pro, cid, 40, "outros", "manual", dia(300))
    bill = q("select id from credit_bills where user_id=%s", (uid_pro,), True)[0]["id"]
    launch = db.pay_bill_amount(uid_pro, cid, "Nubank Mastercard", 40, bill_id=bill)["launch_id"]
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120)])                     # o OF adota o cartão manual
    assert q("select open_finance_account_id from credit_cards where id=%s", (cid,), True)[0]["open_finance_account_id"]
    db.delete_launch_and_rollback(uid_pro, launch)
    assert q("select paid_amount, status from credit_bills where id=%s", (bill,), True)[0] == {"paid_amount": 0, "status": "open"}


def test_desconectar_apara_o_pago_do_cartao_que_sobrevive(uid_pro, rodar):
    rodar(uid_pro, [P1], [fat("bill-1", 120, 120)])
    cid = q("select id from credit_cards where user_id=%s", (uid_pro,), True)[0]["id"]
    db.add_credit_purchase(uid_pro, cid, 30, "outros", "manual", dia(35))     # na mesma fatura do Pig
    assert pig(uid_pro) == [(150, 120, "open")]
    q("update credit_bills set paid_at = null where user_id=%s", (uid_pro,))      # o aparo é quem grava o paid_at
    conexao = q("select id from open_finance_connections where user_id=%s", (uid_pro,), True)[0]["id"]

    db.disconnect_open_finance_connection(uid_pro, conexao)

    assert pig(uid_pro) == [(30, 30, "paid")]
    assert q("select paid_at from credit_bills where user_id=%s", (uid_pro,), True)[0]["paid_at"] is not None
    assert not any("valor_fatura_a_conferir" in o.motivos for o in na_previsao(uid_pro))


def test_desconectar_apara_so_ate_zero_em_fatura_de_total_zero_ou_negativo(uid_pro, rodar):
    rodar(uid_pro, [P1], [fat("bill-1", 120, 120)])
    cid = q("select id from credit_cards where user_id=%s", (uid_pro,), True)[0]["id"]
    for d in (200, 300):
        db.add_credit_purchase(uid_pro, cid, 30, "outros", "manual", dia(d))
    ids = [r["id"] for r in q("select id from credit_bills where user_id=%s order by period_start limit 2", (uid_pro,), True)]
    for bid, total in zip(ids, (-20, 0)):
        q("update credit_bills set total=%s, paid_amount=50, status='open' where id=%s and user_id=%s", (total, bid, uid_pro))
    conexao = q("select id from open_finance_connections where user_id=%s", (uid_pro,), True)[0]["id"]

    db.disconnect_open_finance_connection(uid_pro, conexao)

    assert [(r["paid_amount"], r["status"]) for r in q(
        "select paid_amount, status from credit_bills where id = any(%s) order by period_start", (ids,), True)] \
        == [(0, "open"), (0, "open")]
    assert all(r["paid_at"] is None for r in q("select paid_at from credit_bills where id = any(%s)", (ids,), True))


def _conexao(uid):
    return q("select id from open_finance_connections where user_id=%s", (uid,), True)[0]["id"]


def _webhook_apaga(uid, *ids):
    """O caminho real do `transactions/deleted`: apaga o espelho e tira a compra da fatura do Pig."""
    assert db.delete_open_finance_transactions(f"item-qb-{uid}", list(ids)) == len(ids)


def test_compra_casada_que_sumiu_nao_deixa_pago_velho(uid_pro, rodar):
    """A única compra casada da fatura some: a fatura sai de `pagos` e o pago do banco (120) ficaria sobre total 0."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 120, "paid")]
    _webhook_apaga(uid_pro, "p1")
    assert pig(uid_pro)[1][:2] == (0, 120)                         # o webhook não mexe no pago: o defeito
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), FRESCAS)
    assert pig(uid_pro)[1][:2] == (0, 0)
    assert pig(uid_pro)[0] == (100, 100, "paid")                   # a outra fatura segue como estava


def test_compra_casada_que_sumiu_com_compra_manual_apara_ate_o_total(uid_pro, rodar):
    """Mesmo efeito aceito do disconnect: a compra manual restante (30) passa a aparecer paga."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    cid = q("select id from credit_cards where user_id=%s", (uid_pro,), True)[0]["id"]
    db.add_credit_purchase(uid_pro, cid, 30, "outros", "manual", dia(35))     # na mesma fatura do Pig
    _webhook_apaga(uid_pro, "p1")
    assert pig(uid_pro)[1][:2] == (30, 120)
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), FRESCAS)
    assert pig(uid_pro)[1] == (30, 30, "paid")
    assert q("select paid_at from credit_bills where user_id=%s and total=30", (uid_pro,), True)[0]["paid_at"] is not None


def test_quitar_sem_regra_ainda_apara(uid_pro, rodar):
    """`cobertura` vazia (a conta perdeu as faturas do banco) não pula o aparo."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 120)])
    _webhook_apaga(uid_pro, "p1")
    q("delete from of_card_bills where account_id in (select id from open_finance_accounts where connection_id=%s)",
      (_conexao(uid_pro),))
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), FRESCAS)
    assert pig(uid_pro)[1][:2] == (0, 0)


def test_banco_corrige_fatura_paga_para_total_zero_zera_o_pago_mesmo_sem_regra(uid_pro, rodar):
    """Fatura única 100/100 paga; o banco a corrige para 0 e a compra do Pig segue 100: `fech` fica vazio (empate), e o
    pago tem de voltar a 0 mesmo assim."""
    c = ("p1", 100, "bill-1", 35)
    rodar(uid_pro, [c], [fat("bill-1", 100, 100)])
    assert pig(uid_pro) == [(100, 100, "paid")]
    rodar(uid_pro, [c], [fat("bill-1", 0)])
    assert pig(uid_pro) == [(100, 0, "open")]


@pytest.mark.parametrize("falha", ["bills", "gravacao"])
def test_snapshot_velho_nao_quita_compra_nova(uid_pro, rodar, monkeypatch, falha):
    """Sync 1 grava 100/100 e quita. No sync 2 o `/bills` da conta falha (ou a gravação levanta) e a compra sobe para
    150: o snapshot velho não pode virar fração 1 sobre os 150. Positivo: o sync normal quita (testes acima)."""
    rodar(uid_pro, [("p1", 100, "bill-1", 35)], [fat("bill-1", 100, 100)])
    assert pig(uid_pro) == [(100, 100, "paid")]

    def quebra(*a, **k):
        raise RuntimeError("falha")
    monkeypatch.setattr(ps, "list_pluggy_bills" if falha == "bills" else "salvar_faturas_do_banco", quebra)
    rodar(uid_pro, [("p1", 150, "bill-1", 35)], [fat("bill-1", 100, 100)])
    total, pago, _ = pig(uid_pro)[0]
    assert total == 150 and pago == 100, pig(uid_pro)


@pytest.mark.parametrize("caso", ["invalida", "sumiu"])
def test_fatura_velha_nao_quita_compra_nova(uid_pro, rodar, caso):
    """Sync 1 grava bill-0 e bill-1 100/100 e quita. No sync 2 a bill-0 vem válida, a bill-1 vem sem `dueDate`
    (invalida) ou nem vem (sumiu); a compra sobe para 150. A linha velha da bill-1 (100/100) não pode virar fração 1
    sobre os 150. Positivo: a bill-0, fresca, segue quitada (testes acima quitam)."""
    rodar(uid_pro, [P0, ("p1", 100, "bill-1", 35)], [F0, fat("bill-1", 100, 100)])
    assert pig(uid_pro) == [(100, 100, "paid"), (100, 100, "paid")]
    nova = {**fat("bill-1", 100, 100), "dueDate": None} if caso == "invalida" else None
    rodar(uid_pro, [P0, ("p1", 150, "bill-1", 35)], [F0] + ([nova] if nova else []))
    assert pig(uid_pro) == [(100, 100, "paid"), (150, 100, "open")]


def test_desconectar_deixando_pago_igual_ao_total_marca_paga(uid_pro, rodar):
    """200 de total com 100 pago: tirar as compras do banco deixa 100/100 (pago == total, não >)."""
    rodar(uid_pro, [("p1", 100, "bill-1", 35)], [fat("bill-1", 100, 100)])
    cid = q("select id from credit_cards where user_id=%s", (uid_pro,), True)[0]["id"]
    db.add_credit_purchase(uid_pro, cid, 100, "outros", "manual", dia(35))
    q("update credit_bills set paid_amount=100, status='open', paid_at=null where user_id=%s", (uid_pro,))
    assert pig(uid_pro) == [(200, 100, "open")]

    db.disconnect_open_finance_connection(uid_pro, _conexao(uid_pro))

    assert pig(uid_pro) == [(100, 100, "paid")]
    assert q("select paid_at from credit_bills where user_id=%s", (uid_pro,), True)[0]["paid_at"] is not None


def test_aparo_nao_toca_fatura_aberta_com_pago_menor(uid_pro, rodar):
    """Positivo: o aparo não recusa tudo. Pago < total e aberta fica como está (e a paga com pago == total também)."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    cid = q("select id from credit_cards where user_id=%s", (uid_pro,), True)[0]["id"]
    antes = q("select id, total, paid_amount, status, paid_at from credit_bills where user_id=%s order by id", (uid_pro,), True)
    assert [(r["total"], r["paid_amount"], r["status"]) for r in antes] == [(100, 100, "paid"), (120, 60, "open")]
    q(APARAR_PAGO_SQL, (uid_pro, cid))
    assert q("select id, total, paid_amount, status, paid_at from credit_bills where user_id=%s order by id", (uid_pro,), True) == antes


def test_aparo_reabre_fatura_zumbi_total_zero_paga(uid_pro, rodar):
    """Fatura do cartao do banco com total 0, pago 0, 'paid' e paid_at velho (sem compra casada: so o aparo a alcanca,
    por `quitar_pelo_banco`) volta a 'open' com paid_at nulo. Positivo: a 100/100 paga vizinha nao reabre."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    q("update credit_bills set total=0, paid_amount=0, status='paid', paid_at='2026-01-01'"
      " where user_id=%s and total=120", (uid_pro,))
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), set())
    assert pig(uid_pro) == [(100, 100, "paid"), (0, 0, "open")]
    assert q("select paid_at from credit_bills where user_id=%s and total=0", (uid_pro,), True)[0]["paid_at"] is None


def test_aparo_reabre_fatura_paga_com_total_positivo_e_pago_zero(uid_pro, rodar):
    """Zumbi com total > 0: 120, pago 0, 'paid' e paid_at velho. O aparo reabre ('open', paid_at nulo) em vez de re-fechar."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    q("update credit_bills set paid_amount=0, status='paid', paid_at='2026-01-01' where user_id=%s and total=120", (uid_pro,))
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), set())
    assert pig(uid_pro) == [(100, 100, "paid"), (120, 0, "open")]
    assert q("select paid_at from credit_bills where user_id=%s and total=120", (uid_pro,), True)[0]["paid_at"] is None


def test_aparo_nao_toca_cartao_manual_do_mesmo_usuario(uid_pro, rodar):
    """Escopo do aparo: fatura de cartão manual com pago > total, do mesmo usuário, fica intacta no `quitar_pelo_banco`."""
    rodar(uid_pro, [P0, P1], [F0, fat("bill-1", 120, 60)])
    manual = q("insert into credit_cards (user_id, name, closing_day, due_day) values (%s, 'Manual', 5, 15) returning id",
               (uid_pro,), True)[0]["id"]
    q("insert into credit_bills (user_id, card_id, period_start, period_end, status, total, paid_amount)"
      " values (%s, %s, '2026-01-01', '2026-01-31', 'open', 50, 80)", (uid_pro, manual))
    quitar_pelo_banco(uid_pro, _conexao(uid_pro), FRESCAS)
    assert q("select total, paid_amount, status from credit_bills where card_id=%s", (manual,), True) \
        == [{"total": 50, "paid_amount": 80, "status": "open"}]


def test_falha_da_quitacao_nao_derruba_o_sync(uid_pro, rodar, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("banco")
    monkeypatch.setattr(ps, "quitar_pelo_banco", quebra)
    r = rodar(uid_pro, [P1], [fat("bill-1", 120, 120)])
    assert r["ok"] is True and carimbado(uid_pro) is not None
    assert gravadas(uid_pro) == [("acc-cartao", "bill-1", 120)]
    assert pig(uid_pro) == [(120, 0, "open")]


def test_conversa_fatura_quitada_nao_aparece_em_aberto(uid_pro, rodar, ia_fora):
    rodar(uid_pro, [P1], [fat("bill-1", 120, 120)])
    manda(uid_pro, "gastei 50 no mercado")
    manda(uid_pro, "dinheiro")
    resposta = manda(uid_pro, "faturas")
    assert "Nenhuma fatura em aberto" in resposta, resposta
    assert not ia_fora
