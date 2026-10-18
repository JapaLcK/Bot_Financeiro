"""PR C1: a IA apaga e recategoriza pelo MESMO caminho e `pode` do Extrato (ids `l<id>`/`c<id>`).

Banco real; o runner de verdade (`_dispatch_tool`, `chat`), o modelo FALSO só na conversa pelo
`handle_incoming` (o pytest NÃO prova que o gpt-4o-mini manda o `l…` da lista: só o harness real/staging).

CONTROLES NEGATIVOS (injetados e restaurados na entrega, ver o relato):
  • fallback do inteiro para `credit_transactions.id` no `_achar` -> `test_inteiro_sem_lancamento_...`;
  • inteiro lido como `launches.id` no `_achar` -> `test_inteiro_e_o_user_seq_...` (as duas tools);
  • `validate` sem a checagem do `pode` -> `test_compra_do_banco_...` e a tabela P5-A;
  • `ao_confirmar` fora do ramo do runner -> `test_resumo_vem_do_banco`;
  • `_reler` sem a checagem do `pode` e sem `exigir_pode` -> `test_item_que_muda_...`;
  • `_reler` aceitando o inteiro -> `test_pendencia_antiga_...`;
  • `um_item` com o `user_id` do dono do id -> `test_isolamento_...`.
CONTROLES POSITIVOS: a carteira pura arma e apaga/recategoriza; a pendência canônica executa.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import db
from conftest import usuario_pagante
from core.services.ai_chat import runner
from core.services.ai_chat.tools import extrato_ia as ia
from db import lancamentos, lancamentos_leitura
from db.cards import MSG_COMPRA_DO_BANCO
from tests._fusao_of_helpers import manda, uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso, texto
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_lancamentos_pode import _carteira, _sombra
from tests.test_of_cartao_parcela_guarda import COMPRA, PARCELA, ct_id
from tests.test_of_cartao_sinal import rodar  # noqa: F401 (fixture)
from utils_date import today_tz


def arma(uid, nome, args):
    """O que o runner faz com a chamada do modelo: (JSON do histórico, resposta final ou None)."""
    historico, final = runner._dispatch_tool(uid, nome, args)
    return json.loads(historico), final


def sim(uid):
    return runner.chat(uid, "sim", monthly_limit=1000, platform="dashboard")


def pend(uid):
    return db.ai_get_pending_action(uid)


def lancar(uid, valor=50, alvo="Mercado Extra"):
    """(launch_id, user_seq)"""
    lid, seq, _ = db.add_launch_and_update_balance(uid, "despesa", valor, alvo, None)
    return lid, seq


def existe(uid, lid):
    return q("select 1 as x from launches where user_id = %s and id = %s", (uid, lid), ) is not None


def categoria(uid, lid):
    return q("select categoria from launches where user_id = %s and id = %s", (uid, lid))["categoria"]


def do_carro(uid, seq, ct):
    """Alinha o `#seq` de um lançamento com o id de uma compra: o cenário da colisão."""
    q("update launches set user_seq = %s where user_id = %s and user_seq = %s returning id", (ct, uid, seq))


# ── 1. a colisão: `c42` contra `#42` ─────────────────────────────────────────────────────────

def test_inteiro_e_o_numero_do_lancamento_nao_o_da_compra(uid_pro):
    lid, seq = lancar(uid_pro)
    card = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    tx = db.add_credit_purchase(uid_pro, card, 30, "outros", "Uber", date.today())[0]
    do_carro(uid_pro, seq, tx)  # `#tx` é o lançamento E `tx` é o id da compra

    h, final = arma(uid_pro, "delete_launch", {"launch_id": str(tx)})
    assert final is None and h["args"]["launch_id"] == f"l{lid}", h
    assert pend(uid_pro)["tool_args"]["launch_id"] == f"l{lid}"
    assert "Mercado Extra" in h["summary"]
    assert "Apagado" in sim(uid_pro)
    assert not existe(uid_pro, lid) and ct_existe(uid_pro, tx)  # o lançamento caiu, a compra ficou


def ct_existe(uid, tx):
    return q("select 1 as x from credit_transactions where user_id = %s and id = %s", (uid, tx)) is not None


def test_a_compra_pelo_c_e_recusada_e_o_inteiro_sem_lancamento_nao_cai_nela(uid_pro):
    card = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    tx = db.add_credit_purchase(uid_pro, card, 30, "outros", "Uber", date.today())[0]
    for bruto in (f"c{tx}", f"CC{tx}"):  # P-1: compra manual no cartão é só de leitura
        h, final = arma(uid_pro, "delete_launch", {"launch_id": bruto})
        assert final and "só de leitura" in final and pend(uid_pro) is None, (bruto, final)
    # o inteiro é SÓ o `#N`: nenhum lançamento com esse seq -> "não achei", e não a compra do mesmo número
    h, final = arma(uid_pro, "delete_launch", {"launch_id": str(tx)})
    assert final and "Não achei" in final and pend(uid_pro) is None, final
    assert ct_existe(uid_pro, tx)


# ── 2. o inteiro não é `l<inteiro>` ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["delete_launch", "recategorize_launch"])
def test_inteiro_e_o_user_seq_e_nao_o_id_interno(uid_pro, tool):
    a, seq_a = lancar(uid_pro, 10, "A")
    b, seq_b = lancar(uid_pro, 20, "B")
    q("update launches set user_seq = %s where user_id = %s and id = %s returning id", (a, uid_pro, b))
    assert seq_a != a  # A tem o id I e o seq S != I; B tem o seq I
    args = {"launch_id": f"#{a}", **({"new_category": "lazer"} if tool == "recategorize_launch" else {})}

    h, final = arma(uid_pro, tool, args)
    assert final is None and pend(uid_pro)["tool_args"]["launch_id"] == f"l{b}", h
    sim(uid_pro)
    if tool == "delete_launch":
        assert existe(uid_pro, a) and not existe(uid_pro, b)
    else:
        assert categoria(uid_pro, b) == "lazer" and categoria(uid_pro, a) != "lazer"


# ── 3. compra do Open Finance ────────────────────────────────────────────────────────────────

def test_compra_do_banco_nao_apaga_mas_recategoriza(uid_pro, rodar):
    rodar(uid_pro, [PARCELA])
    c = ct_id(uid_pro)

    h, final = arma(uid_pro, "delete_launch", {"launch_id": f"c{c}"})
    assert final.startswith("🐷 " + MSG_COMPRA_DO_BANCO) and "mudo a categoria" in final
    assert pend(uid_pro) is None and ct_existe(uid_pro, c)

    h, final = arma(uid_pro, "recategorize_launch", {"launch_id": f"c{c}", "new_category": "lazer"})
    assert final is None and "(vale para as 3 parcelas)" in h["summary"], h
    assert "agora é lazer" in sim(uid_pro)
    row = q("select categoria, categoria_editada from credit_transactions where user_id = %s", (uid_pro,))
    assert (row["categoria"], row["categoria_editada"]) == ("lazer", True)
    rodar(uid_pro, [PARCELA])  # o próximo sync não desfaz a escolha
    assert q("select categoria from credit_transactions where user_id = %s", (uid_pro,))["categoria"] == "lazer"


def test_carteira_pura_arma_e_apaga_com_o_saldo_revertido(uid_pro):
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    lid, _ = lancar(uid_pro)
    assert db.get_balance(uid_pro) == 950
    h, final = arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    assert final is None, h
    assert "Saldo revertido" in sim(uid_pro) and db.get_balance(uid_pro) == 1000 and not existe(uid_pro, lid)


# ── 4. a tabela P5-A: a IA segue o `pode` do Extrato ─────────────────────────────────────────

def test_tabela_p5a_a_ia_segue_o_pode_do_extrato():
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-ia-{a}"), "acc-ia", "100")
    db.add_launch_and_update_balance(a, "receita", 1000, "seed", None)
    pura, antiga = _carteira(a), _carteira(a, marcada=False)
    pendente, sombra_p = _carteira(a, valor=31), _sombra(a, 31)
    tx_banco(acc, "tx-pend", "-31", imported_launch_id=sombra_p, match_launch_id=pendente,
             reconciliation_status="pending")
    fundida = _carteira(a, valor=32)
    tx_banco(acc, "tx-fund", "-32", imported_launch_id=fundida, reconciliation_status="auto_merged")
    especie = _carteira(a, valor=33)
    q("""insert into of_cash_links (user_id, tx_key, key_durable, account_key, kind, status,
             launch_id, amount, tx_date) values (%s, 'k0', true, 'k', 'saque', 'ativo', %s, 1, %s)
         returning id""", (a, especie, date.today()))
    paga = _carteira(a, valor=35, bill_id=1, paid_amount_added=35)
    paga_fund = _carteira(a, valor=41, bill_id=2, paid_amount_added=41)
    tx_banco(acc, "tx-paga-fund", "-41", imported_launch_id=paga_fund, reconciliation_status="auto_merged")
    sombra = _sombra(a, 38)
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    manual_ct = db.add_credit_purchase(a, cartao, 39, "mercado", "manual", date.today())[0]
    of_ct = db.add_imported_credit_purchase(a, cartao, 40, "mercado", date.today(), "ct-ext-1")[0]
    db.create_pocket(a, "viagem")
    db.pocket_deposit_from_account(a, "viagem", 5, "guardando")
    guardado = int(db.list_launches(a, limit=1)[0]["id"])

    # id: (apagar, categoria) esperados pelo `pode` do Extrato (`tests/test_api_v2_lancamentos_pode.py`)
    esperado = {
        f"l{pura}": (True, True), f"l{antiga}": (False, False), f"l{pendente}": (True, True),
        f"l{sombra_p}": (False, True), f"l{fundida}": (True, True), f"l{especie}": (True, False),
        f"l{paga}": (False, True), f"l{paga_fund}": (False, True), f"l{sombra}": (False, True),
        f"c{manual_ct}": (False, False), f"c{of_ct}": (False, True),  # compra no cartão nunca apaga
        f"l{guardado}": (False, False),
    }
    for ident, (apagar, cat) in esperado.items():
        tabela, n = lancamentos.ler_id(ident)
        if tabela == "l":  # a expectativa vem do `pode` da linha, não de uma cópia dele
            with db.get_conn() as conn, conn.cursor() as cur:
                pode = lancamentos.pode_da_linha(cur, a, n)
            assert (("apagar" in pode), ("categoria" in pode)) == (apagar, cat), (ident, pode)
        for tool, args, quer in (("delete_launch", {}, apagar),
                                 ("recategorize_launch", {"new_category": "lazer"}, cat)):
            h, final = arma(a, tool, {"launch_id": ident, **args})
            if quer:
                assert final is None and pend(a)["tool_args"]["launch_id"] == ident, (ident, tool, final)
                db.ai_consume_pending_action(a, pend(a))
                runner._PENDENCIA_DO_TURNO.set(None)
            else:
                assert final and final.startswith("🐷") and pend(a) is None, (ident, tool, final)
    # a frase diz o motivo e o que dá para fazer (P-6), sem apontar outro canal
    assert "só de leitura" in arma(a, "delete_launch", {"launch_id": f"l{antiga}"})[1]
    assert "mudo a categoria" in arma(a, "delete_launch", {"launch_id": f"l{paga}"})[1]
    assert "apago ele" in arma(a, "recategorize_launch", {"launch_id": f"l{especie}", "new_category": "lazer"})[1]


# ── 5. o resumo vem do banco ─────────────────────────────────────────────────────────────────

def test_resumo_vem_do_banco_e_nao_do_que_o_modelo_falou(uid_pro):
    lid, _ = lancar(uid_pro)
    h, final = arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}", "descricao": "Outra coisa", "valor": 999})
    dia = today_tz().strftime("%d/%m")
    assert final is None
    assert h["summary"] == f'apagar "Mercado Extra", R$ 50,00, {dia}'
    assert h["args"]["_item"] == {"descricao": "Mercado Extra", "valor": 50.0, "data": today_tz().isoformat(),
                                  "parcelas": 1, "moeda": "BRL"}
    p = pend(uid_pro)
    assert p["summary"] == h["summary"] and p["tool_args"]["_item"] == h["args"]["_item"]
    # o modelo não forja `_item`: o runner tira a chave `_` do que ele manda
    runner._PENDENCIA_DO_TURNO.set(None)
    db.ai_consume_pending_action(uid_pro, p)
    h, _ = arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}", "_item": {"descricao": "forjada", "valor": 1}})
    assert "forjada" not in h["summary"]


# ── 6. o item some ou muda entre a pergunta e o "sim" ────────────────────────────────────────

def test_item_que_some_antes_do_sim_nao_apaga_outro_e_nao_mexe_no_saldo(uid_pro):
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    lid, _ = lancar(uid_pro)
    outro, _ = lancar(uid_pro, 20, "Outro")
    arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    db.delete_launch_and_rollback(uid_pro, lid)  # apagado por outro canal no meio
    saldo = db.get_balance(uid_pro)
    assert "Não achei mais" in sim(uid_pro)
    assert existe(uid_pro, outro) and db.get_balance(uid_pro) == saldo


def test_item_que_muda_antes_do_sim_nao_e_apagado(uid_pro):
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    lid, _ = lancar(uid_pro)
    arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    q("""update launches set efeitos = efeitos || '{"bill_id": 1}'::jsonb
         where user_id = %s and id = %s returning id""", (uid_pro, lid))  # virou pagamento de conta
    saldo = db.get_balance(uid_pro)
    assert "mudou desde a pergunta" in sim(uid_pro)
    assert existe(uid_pro, lid) and db.get_balance(uid_pro) == saldo


def test_categoria_que_perde_o_pode_antes_do_sim_nao_muda(uid_pro):
    lid, _ = lancar(uid_pro)
    antes = categoria(uid_pro, lid)
    arma(uid_pro, "recategorize_launch", {"launch_id": f"l{lid}", "new_category": "lazer"})
    q("update launches set origem = null where user_id = %s and id = %s returning id", (uid_pro, lid))
    assert "mudou desde a pergunta" in sim(uid_pro)
    assert categoria(uid_pro, lid) == antes


# ── 7. pendência armada antes desta versão ───────────────────────────────────────────────────

def test_pendencia_antiga_pede_de_novo_e_nao_toca_no_banco(uid_pro):
    a, seq_a = lancar(uid_pro, 10, "A")
    lancar(uid_pro, 20, "B")
    n = db.count_launches(uid_pro)
    db.ai_set_pending_action(uid_pro, "delete_launch", {"launch_id": str(seq_a)}, f"apagar o lançamento #{seq_a}")
    assert "Me pede de novo" in sim(uid_pro)
    # a antiga do recategorize carregava o id INTERNO (inteiro)
    db.ai_set_pending_action(uid_pro, "recategorize_launch", {"launch_id": a, "new_category": "lazer"}, "x")
    assert "Me pede de novo" in sim(uid_pro)
    assert db.count_launches(uid_pro) == n and categoria(uid_pro, a) != "lazer"


def test_pendencia_canonica_executa_com_item_e_sem_item_pede_de_novo(uid_pro):
    a, _ = lancar(uid_pro, 10, "A")
    args = {"launch_id": f"l{a}", "new_category": "lazer"}
    db.ai_set_pending_action(uid_pro, "recategorize_launch", args, "x")  # sem `_item`: nada a comparar
    assert "Me pede de novo" in sim(uid_pro) and categoria(uid_pro, a) != "lazer"
    item = ia._resumo_item(lancamentos_leitura.um_item(uid_pro, "l", a))  # controle positivo: com `_item` executa
    db.ai_set_pending_action(uid_pro, "recategorize_launch", {**args, "_item": item}, "x")
    assert "agora é lazer" in sim(uid_pro) and categoria(uid_pro, a) == "lazer"


# ── 8. isolamento entre usuários ─────────────────────────────────────────────────────────────

def test_isolamento_o_id_do_outro_usuario_e_nao_achei_e_nada_e_armado(uid_pro):
    outro = usuario_pagante()
    lid, seq = lancar(outro)
    card = db.create_card(outro, "Manual", closing_day=10, due_day=17)
    r = db.add_credit_purchase_installments(user_id=outro, card_id=card, valor_total=300, categoria="outros",
                                            nota="tv", purchased_at=date.today(), installments=3)
    info = r[0] if isinstance(r, tuple) else r
    pc = f"PC{info['group_id'].replace('-', '').upper()[:8]}"
    tx = info["tx_ids"][0]
    assert ia.validar_apagar(outro, {"launch_id": f"l{lid}"}) is None  # controle: o dono acha

    for tool, extra in (("delete_launch", {}), ("recategorize_launch", {"new_category": "lazer"})):
        for bruto in (f"l{lid}", f"c{tx}", f"CC{tx}", f"#{seq}", str(seq), pc, pc.lower()):
            h, final = arma(uid_pro, tool, {"launch_id": bruto, **extra})
            assert final and "Não achei" in final and pend(uid_pro) is None, (tool, bruto, final)
    assert existe(outro, lid) and categoria(outro, lid) != "lazer" and ct_existe(outro, tx)
    # o "sim" forjado com o id do outro também não alcança
    db.ai_set_pending_action(uid_pro, "delete_launch", {"launch_id": f"l{lid}"}, "x")
    assert "Não achei mais" in sim(uid_pro) and existe(outro, lid)


# ── 11. o que o usuário digita ───────────────────────────────────────────────────────────────

def test_formas_de_digitar_o_id(uid_pro):
    lid, seq = lancar(uid_pro)
    card = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    r = db.add_credit_purchase_installments(user_id=uid_pro, card_id=card, valor_total=300, categoria="outros",
                                            nota="tv", purchased_at=date.today(), installments=3)
    info = r[0] if isinstance(r, tuple) else r
    pc = f"PC{info['group_id'].replace('-', '').upper()[:8]}"
    tx0 = min(info["tx_ids"])
    certas = {f"#{seq}": ("l", lid), str(seq): ("l", lid), f" # {seq} ": ("l", lid), seq: ("l", lid),
              f"l{lid}": ("l", lid), f"L{lid}": ("l", lid), f" l{lid} ": ("l", lid),
              f"c{tx0}": ("c", tx0), f"CC{tx0}": ("c", tx0), f"cc{tx0}": ("c", tx0),
              pc: ("c", tx0), pc.lower(): ("c", tx0)}
    for bruto, esperado in certas.items():
        assert ia._achar(uid_pro, bruto) == esperado, bruto
    for bruto in ("", "#", "CC", "pc", f"l {lid}", f"l0", f"l0{lid}", "c-1", "l99999999999999999999",
                  "٤٢", f"{seq}\n9", "PCzzzzzzzz", "999999999", None, 0):
        assert ia._achar(uid_pro, bruto) is None, bruto


# ── 10. a conversa pelo `handle_incoming` ────────────────────────────────────────────────────

def _id_do(messages, alvo):
    lista = json.loads([m for m in messages if m.get("role") == "tool"][-1]["content"])["itens"]
    return next(i["id"] for i in lista if i["descricao"] == alvo)


def _pede_apagar(monkeypatch, alvo):
    openai_falso(
        monkeypatch,
        com_tools(chamada("list_recent_launches", {"limit": 10}, "c1")),
        lambda m: com_tools(chamada("delete_launch", {"launch_id": _id_do(m, alvo)}, "c2")),
        lambda m: texto("🐷 Confirma: " + json.loads([x for x in m if x.get("role") == "tool"][-1]["content"])["summary"]
                        + "? Responde *sim* ou *não*."),
    )


def test_conversa_apaga_o_certo_abandona_com_outro_assunto_e_o_sim_solto_nao_apaga(uid_pro, monkeypatch):
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    mercado, _ = lancar(uid_pro, 50, "Mercado Extra")
    padaria, _ = lancar(uid_pro, 20, "Padaria")
    saldo = db.get_balance(uid_pro)
    dia = today_tz().strftime("%d/%m")

    _pede_apagar(monkeypatch, "Mercado Extra")
    r = manda(uid_pro, "piggy apaga o mercado de 50")
    assert f'"Mercado Extra", R$ 50,00, {dia}' in r and pend(uid_pro)["tool_args"]["launch_id"] == f"l{mercado}", r

    openai_falso(monkeypatch, texto("🐷 Sim pra quê?"))
    manda(uid_pro, "gastei 30 na farmácia em dinheiro")  # outro assunto: a pendência é abandonada
    assert pend(uid_pro) is None
    manda(uid_pro, "sim")
    assert existe(uid_pro, mercado) and existe(uid_pro, padaria)

    _pede_apagar(monkeypatch, "Mercado Extra")
    manda(uid_pro, "piggy apaga o mercado")
    r = manda(uid_pro, "sim")
    assert "Apagado" in r and "Mercado Extra" in r
    assert not existe(uid_pro, mercado) and existe(uid_pro, padaria)
    assert db.get_balance(uid_pro) == saldo - 30 + 50  # +50 revertidos, -30 da farmácia


def test_apagar_e_so_do_lancamento_mesmo_que_o_pode_da_compra_um_dia_de_apagar():
    """Como a rota da v2: `c` nunca apaga. Hoje `PODE_CARTAO_SQL` nem tem `apagar`; a guarda impede que
    `delete_launch_and_rollback` (que recebe um id de `launches`) leia um id de compra."""
    assert ia._pode({"id": "c5", "pode": ["apagar", "categoria"]}, "apagar") is False
    assert ia._pode({"id": "l5", "pode": ["apagar"]}, "apagar") is True
    assert ia._pode({"id": "c5", "pode": ["categoria"]}, "categoria") is True


def test_as_listas_devolvem_o_id_que_as_escritas_leem(uid_pro):
    """Sem o `l`, o modelo mandaria o id interno cru, que as escritas leem como `#N`."""
    from core.services.ai_chat.tools.categories import _get_uncategorized_launches
    from core.services.ai_chat.tools.listas_ia import ultimos_lancamentos

    lid, seq = lancar(uid_pro)
    q("update launches set categoria = 'outros' where user_id = %s and id = %s returning id", (uid_pro, lid))
    recentes = ultimos_lancamentos(uid_pro, {})["itens"]
    sem_cat = _get_uncategorized_launches(uid_pro, {})["launches"]
    for item in (*recentes, *sem_cat):
        assert item["id"] == f"l{lid}", item
        assert ia.validar_apagar(uid_pro, {"launch_id": item["id"]}) is None
    assert recentes[0]["user_seq"] == seq


# ── 12. a classe: toda tool que entrega id às escritas o entrega na forma que `_achar` lê ────────────

@pytest.mark.parametrize("tool", ["delete_launch", "recategorize_launch"])
def test_o_id_da_fatura_arma_a_compra_e_nao_o_lancamento_de_mesmo_numero(uid_pro, tool):
    from core.services.ai_chat.tools.cards import _get_open_bill

    lid, seq = lancar(uid_pro)
    card = db.create_card(uid_pro, "Banco", closing_day=31, due_day=10)
    tx = db.add_imported_credit_purchase(uid_pro, card, 40, "mercado", date.today(), "ct-b1")[0]
    do_carro(uid_pro, seq, tx)  # `#tx` é o lançamento "Mercado Extra", e `tx` é a compra da fatura

    ids = [i["id"] for i in _get_open_bill(uid_pro, {"card_name": "Banco"})["items"]]
    assert ids == [f"c{tx}"], ids  # o cru (`tx`) viraria o `#tx`: o lançamento errado
    args = {"launch_id": ids[0], **({"new_category": "lazer"} if tool == "recategorize_launch" else {})}
    h, final = arma(uid_pro, tool, args)
    if tool == "recategorize_launch":  # a compra do banco muda de categoria; apagar é recusado (compra do banco)
        assert final is None and pend(uid_pro)["tool_args"]["launch_id"] == f"c{tx}", (h, final)
    else:
        assert final and MSG_COMPRA_DO_BANCO in final and pend(uid_pro) is None, final
    assert existe(uid_pro, lid) and ct_existe(uid_pro, tx)


def test_o_codigo_do_parcelamento_da_lista_e_o_que_as_escritas_leem(uid_pro):
    from core.services.ai_chat.tools.cards import _list_installments

    card = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    r = db.add_credit_purchase_installments(user_id=uid_pro, card_id=card, valor_total=300, categoria="outros",
                                            nota="tv", purchased_at=date.today(), installments=3)
    info = r[0] if isinstance(r, tuple) else r
    q("update credit_transactions set group_id = %s::uuid where user_id = %s and group_id = %s::uuid returning id",
      ("ab12cd34-0000-4000-8000-000000000001", uid_pro, info["group_id"]))
    tx0 = min(info["tx_ids"])

    [g] = _list_installments(uid_pro, {})["groups"]
    assert g["codigo"] == "PCAB12CD34" and "group_id" not in g, g  # o uuid cru não sai
    h, final = arma(uid_pro, "delete_launch", {"launch_id": g["codigo"]})
    assert final and "só de leitura" in final and "Não achei" not in final, final
    # as formas de o modelo escrever o grupo: a lista (com PC, em qualquer caixa) e o uuid inteiro
    for bruto in (g["codigo"], g["codigo"].lower(), "ab12cd34-0000-4000-8000-000000000001"):
        assert ia._achar(uid_pro, bruto) == ("c", tx0), bruto
    # os 8 hex SEM `PC` não armam o grupo: colidem com `c<id>`/`CC<id>` (uma compra de outro grupo)
    assert ia._achar(uid_pro, "ab12cd34") is None
    _, final = arma(uid_pro, "delete_launch", {"launch_id": "ab12cd34"})
    assert final and "Não achei" in final, final
    assert ia._achar(uid_pro, "12345678") is None  # só dígitos é o `#N`, que não existe
    assert ia._achar(usuario_pagante(), "ab12cd34-0000-4000-8000-000000000001") is None  # de outro usuário
