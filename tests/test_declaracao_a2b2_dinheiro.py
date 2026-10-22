"""PR-A2b-2 (ADR 0006): "foi em dinheiro", selecionar um lançamento da Carteira ou converter, pelas rotas do /app
com Postgres real. Toda asserção é o retrato inteiro (`tests/_retrato_declaracao.py`), com a Netflix (T9, 39,90)
do banco sempre presente: transação que continua existindo não pode ser esquecida na soma.

Controles (CLAUDE.md §3; formato de `docs/controles_declarados.md`; rodados numa CÓPIA, nunca no worktree):
- H3: tirar o `MARCADOR: {"convertida": ...}` do update de `foi_em_dinheiro` -> VERMELHO
  `test_h3_a_convertida_tambem_e_exclusiva`. Positivo: o mesmo teste (a 2ª declaração ainda vê a #40).
- H4/H6: `carteira_launch_id not in {...}` trocado por `False` -> VERMELHO `test_h4_c_fora_da_lista_recalculada_e_recusada`
  (todos os casos, menos `ja_marcada_por_outra` e `inexistente`, presos pelo CAS e pelo `for update`); tolerância de R$ 0,05 no `round(valor::numeric, 2) = any` e em `_money(c["valor"]) ==` -> VERMELHO
  o caso `valor_49_99`; `RECON_DATE_WINDOW + 1` na comparação -> VERMELHO o caso `dia_mais_4`.
  Positivo: `test_h4_c_na_borda_e_aceita` (50,00 a 3 dias) e `test_k1_selecionar_a_40`.
- K1 receita: `{TIPO_CANON_SQL} in ('despesa', 'receita')` trocado por `tipo in (...)` em `candidatos_carteira` -> VERMELHO
  `test_k1_selecionar_uma_receita[entrada]` (a `[receita]` segue verde); `c["tipo"] == d["tipo"]` trocado por
  `== "despesa"` -> VERMELHO as duas.
- H5: tirar `if d["matched_transaction_id"]: raise ...` -> VERMELHO `test_h5_declaracao_casada_exige_desfazer_antes`.
  Positivo: o mesmo teste converte depois do `undo`.
- H7: tirar `"foi_em_dinheiro"` de `_EFEITOS_REVERSIVEIS` (db/accounts.py) -> VERMELHO
  `test_h7_apagar_a_marcada_e_a_convertida_devolve_o_dinheiro`.
- H8: gravar o contexto sem `nota` -> VERMELHO `test_k1_selecionar_a_40`.
- H11: sem tirar o `funding_source` da convertida -> VERMELHO `test_h11_converter_saldo_negativo_receita_e_confirmada`.
- H11b: ler `d["launch_valor"]` no lugar de `gone["amount"]` -> VERMELHO `test_h11b_converte_o_declarado_nao_o_projetado`.
- H14: tirar o `if not e_fluxo(d): raise` E o guard de `efeitos` (`set(efeitos) - {...}`) -> VERMELHO
  `test_h14_aporte_recusa_a_acao`; cada um sozinho segue verde (camadas: o aporte tem `delta_pocket` em `efeitos`).
- H4 isolamento: tirar o `user_id = %s` de `candidatos_carteira`, do `for update` de C E do CAS do marcador -> VERMELHO
  `test_h4_lancamento_de_outro_usuario_e_recusado`; as duas primeiras sem o CAS seguem verdes (camadas).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

import db
from conftest import _cleanup_user, promote_to_pro
from db.accounts import get_balance
from db.carteira_qualidade import nao_confirmada
from db.connection import get_conn
from db.reconciliation import confirm_reconciliation
from tests._fusao_of_helpers import of_tx_pendente
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import BASE, carteira, decl, em, marcadores, retrato, sombra, solta, t, tid
from tests.test_api_v2_declaracao_fluxo import linha
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_bank_movements import _bank
from tests.test_declaracao_a2b_acoes import H, R, cliente, confirma, desfaz, ana
from tests.test_declaracao_a2b_guardas import _aporte
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza
from utils_date import _tz

T9 = t("T9", -39.90, 0, "NETFLIX")  # o espectador: o banco continua tendo esta transação em todo retrato
SOMBRA9 = [sombra("T9", 39.9, 0)]


def dinheiro(c, uid, lid, carteira_id=None):
    corpo = {"launch_id": lid} if carteira_id is None else {"launch_id": lid, "carteira_launch_id": carteira_id}
    return c.post(f"/open-finance/{uid}/movements/cash", json=corpo, headers=H)


def lanca(uid, valor=50, dia=1, alvo="mercado", nota="feira", categoria="alimentacao", tipo="despesa"):
    """Lançamento da Carteira (o #40 do exemplo): debita (ou credita) a Carteira na hora."""
    return db.add_launch_and_update_balance(uid, tipo, valor, alvo, nota, categoria, em(dia))[0]


def ana2(uid, com_40=True):
    """Ana: Carteira 1000; declarou "mercado" R$ 50,00 em BASE (nota "compras da semana", categoria mercado);
    o banco só trouxe a Netflix; lançou em dinheiro "mercado" R$ 50,00 em BASE+1 ("feira"): Carteira 950."""
    promote_to_pro(uid)
    q("update accounts set balance=1000 where user_id=%s returning 1", (uid,))
    cid, _ = _bank(uid)
    d = declara(uid, criado_em=em(0), nota="compras da semana")
    c40 = lanca(uid) if com_40 else None
    sincroniza(cid, uid, T9)
    return cid, d, c40, cliente(uid)


def antes_e_depois_iguais(uid, fn):
    antes, marcas = retrato(uid), marcadores(uid)
    fn()
    assert retrato(uid) == antes and marcadores(uid) == marcas


@pytest.fixture
def outro():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    yield uid
    _cleanup_user(uid)


def test_k1_selecionar_a_40(user_id):
    cid, d, c40, c = ana2(user_id)
    inicial = R([decl(0), carteira(1), *SOMBRA9], [solta("T9", -39.9, 0)],
                [("decl mercado", -50.0, None, None, False, False)], 139.9, 950.0)
    assert retrato(user_id) == inicial
    lista = c.get(f"/open-finance/{user_id}/movements").json()
    assert lista["wallet_balance"] == 950.0
    assert lista["movements"][0]["cash"] == [{"id": c40, "name": "mercado", "category": "alimentacao",
                                              "amount": 50.0, "date": (BASE + timedelta(days=1)).isoformat()}]
    numero = q("select user_seq from launches where id=%s", (d,))["user_seq"]
    declarada = q("select declared_at from bank_movement_declarations where launch_id=%s", (d,))["declared_at"]

    r = dinheiro(c, user_id, d, c40)
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": "carteira", "launch_id": c40}, r.text
    assert retrato(user_id) == R([carteira(1), *SOMBRA9], [solta("T9", -39.9, 0)], [], 89.9, 950.0)
    assert marcadores(user_id) == {"manual mercado": {"declaracao": {
        "tipo": "despesa", "valor": "50.00", "alvo": "mercado", "nota": "compras da semana", "categoria": "mercado",
        "declarada_em": declarada.astimezone(_tz()).isoformat(), "numero": numero}}}
    # a #40 mantém nota, categoria e data (DP-3, DP-5): só ganhou o contexto
    assert q("select nota, categoria from launches where id=%s", (c40,)) == {"nota": "feira", "categoria": "alimentacao"}
    historico = {i["id"]: i for i in cliente(user_id).get(f"/history/{user_id}/list?limit=50").json()["items"]}
    assert historico[c40]["cash_declaration"]["nota"] == "compras da semana" and d not in historico
    assert dinheiro(c, user_id, d, c40).status_code == 404  # retentativa: efeito único, nada muda
    assert retrato(user_id) == R([carteira(1), *SOMBRA9], [solta("T9", -39.9, 0)], [], 89.9, 950.0)


@pytest.mark.parametrize("tipo_legado", ["receita", "entrada"])
def test_k1_selecionar_uma_receita(user_id, tipo_legado):
    """Receita declarada + receita da Carteira (também o `tipo='entrada'` legado): associa e o saldo não muda."""
    promote_to_pro(user_id)
    q("update accounts set balance=1000 where user_id=%s returning 1", (user_id,))
    cid, _ = _bank(user_id)
    d = declara(user_id, 50, "receita", em(0), "freela", "projeto", "outros")
    c40 = lanca(user_id, tipo="receita", alvo="freela", nota=None, categoria="outros")
    q("update launches set tipo=%s where id=%s returning 1", (tipo_legado, c40))
    sincroniza(cid, user_id, T9)
    c = cliente(user_id)
    assert retrato(user_id) == R([decl(0, alvo="freela", tipo="receita"), carteira(1, alvo="freela", tipo=tipo_legado), *SOMBRA9],
                                 [solta("T9", -39.9, 0)], [("decl freela", 50.0, None, None, False, False)], 39.9, 1050.0)
    assert [x["id"] for x in c.get(f"/open-finance/{user_id}/movements").json()["movements"][0]["cash"]] == [c40]
    r = dinheiro(c, user_id, d, c40)
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": "carteira", "launch_id": c40}, r.text
    assert retrato(user_id) == R([carteira(1, alvo="freela", tipo=tipo_legado), *SOMBRA9], [solta("T9", -39.9, 0)], [], 39.9, 1050.0)
    m = marcadores(user_id)["manual freela"]["declaracao"]
    assert (m["tipo"], m["valor"], m["alvo"], m["nota"]) == ("receita", "50.00", "freela", "projeto")


def test_k1_converter_nenhum_destes(user_id):
    cid, d, c40, c = ana2(user_id)
    r = dinheiro(c, user_id, d)
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": "convertida", "launch_id": d, "balance": 900.0}, r.text
    assert retrato(user_id) == R([carteira(0), carteira(1), *SOMBRA9], [solta("T9", -39.9, 0)], [], 139.9, 900.0)
    assert marcadores(user_id) == {"manual mercado": {"convertida": True}}
    # mesmo #N, valor e data declarados, nota e categoria originais
    assert q("select nota, categoria, user_seq from launches where id=%s", (d,))["nota"] == "compras da semana"
    assert dinheiro(c, user_id, d).status_code == 404


def test_k1_b_linha_a_transacao_do_banco_existe_e_nao_e_a_dela(user_id):
    """Ação B': T2 (PIX MARIA, mesmo valor, outro dia) existe e a declaração foi convertida mesmo assim."""
    cid, d, _, c = ana2(user_id, com_40=False)
    sincroniza(cid, user_id, T9, t("T2", -50, 1, "PIX MARIA"))
    antes = retrato(user_id)
    assert antes == R([decl(0), sombra("T2", 50.0, 1), *SOMBRA9], [solta("T2", -50.0, 1), solta("T9", -39.9, 0)],
                      [("decl mercado", -50.0, None, None, False, False)], 139.9, 1000.0)
    assert dinheiro(c, user_id, d).status_code == 200
    assert retrato(user_id) == R([carteira(0), sombra("T2", 50.0, 1), *SOMBRA9],
                                 [solta("T2", -50.0, 1), solta("T9", -39.9, 0)], [], 139.9, 950.0)  # sem pendência (DP-2)


def test_h10_o_banco_traz_a_igual_depois_vira_pendencia_nunca_fusao(user_id):
    cid, d, c40, c = ana2(user_id)
    assert dinheiro(c, user_id, d, c40).status_code == 200
    sincroniza(cid, user_id, T9, t("T6", -50, 2, "MERCADO"))
    assert retrato(user_id) == R([carteira(1), sombra("T6", 50.0, 2), *SOMBRA9],
                                 [("T6", -50.0, 2, "sombra T6", "manual mercado", "pending"), solta("T9", -39.9, 0)],
                                 [], 139.9, 950.0)
    assert confirm_reconciliation(user_id, of_tx_pendente(user_id))["ok"]  # só o usuário funde
    r = retrato(user_id)
    assert [x for x in r["L"] if x[1] == "despesa" and x[2] == 50.0] == [("manual mercado", "despesa", 50.0, 2, "manual", "-50.0", "carteira")]
    assert r["C"] == 950.0


def test_h3_a_convertida_tambem_e_exclusiva(user_id):
    """Duas declarações iguais: a convertida (50,00 em BASE, Carteira) não pode servir de C para a outra."""
    cid, d1, c40, c = ana2(user_id)
    d2 = declara(user_id, criado_em=em(0), alvo="padaria", categoria="padaria")
    assert dinheiro(c, user_id, d1).status_code == 200
    assert [x["id"] for x in c.get(f"/open-finance/{user_id}/movements").json()["movements"][0]["cash"]] == [c40]
    antes_e_depois_iguais(user_id, lambda: dinheiro(c, user_id, d2, d1).status_code == 409 or pytest.fail("aceitou"))
    assert dinheiro(c, user_id, d2, c40).status_code == 200  # a #40 legítima segue valendo


def _ligado(uid, cid, fundir):
    """C com o banco, de verdade pelo sync: o alvo "padaria" não dá evidência à declaração "mercado", então só
    a #40 faz par com a T6 (pendente, ou fundido depois do `confirm`). O assert impede o caso vazio."""
    lid = lanca(uid, alvo="padaria")
    sincroniza(cid, uid, T9, t("T6", -50, 1, "PADARIA"))
    assert q("select match_launch_id m from open_finance_transactions where reconciliation_status='pending'")["m"] == lid
    if fundir:
        assert confirm_reconciliation(uid, of_tx_pendente(uid))["ok"]
        assert q("select count(*) n from open_finance_transactions where imported_launch_id=%s", (lid,))["n"] == 1
    return lid


def _especie(uid, cid, c, d):
    lid = lanca(uid)
    q("""insert into of_cash_links(user_id, tx_key, key_durable, account_key, kind, status, launch_id, amount, tx_date)
         values (%s, 'k1', true, 'a', 'saque', 'ativo', %s, 50, current_date) returning 1""", (uid, lid))
    return lid


def _interno(uid, cid, c, d):
    lid = lanca(uid)
    q("update launches set is_internal_movement=true where id=%s returning 1", (lid,))
    return lid


def _ja_marcada(uid, cid, c, d):
    d2 = declara(uid, criado_em=em(0), alvo="padaria", categoria="padaria")
    c40 = lanca(uid, alvo="feira")
    assert dinheiro(c, uid, d2, c40).status_code == 200
    return c40


CASOS = {
    "valor_49_99": lambda uid, cid, c, d: lanca(uid, 49.99),
    "valor_50_01": lambda uid, cid, c, d: lanca(uid, 50.01),
    "dia_mais_4": lambda uid, cid, c, d: lanca(uid, dia=4),
    "dia_menos_4": lambda uid, cid, c, d: lanca(uid, dia=-4),
    "tipo_receita": lambda uid, cid, c, d: lanca(uid, tipo="receita"),
    "interno": _interno,
    "pendente": lambda uid, cid, c, d: _ligado(uid, cid, False),
    "fundido": lambda uid, cid, c, d: _ligado(uid, cid, True),
    "especie": _especie,
    "ja_marcada_por_outra": _ja_marcada,
    "a_propria_declaracao": lambda uid, cid, c, d: d,
    "inexistente": lambda uid, cid, c, d: 10**9,
}


@pytest.mark.parametrize("caso", list(CASOS))
def test_h4_c_fora_da_lista_recalculada_e_recusada(user_id, caso):
    cid, d, c40, c = ana2(user_id, com_40=False)
    lid = CASOS[caso](user_id, cid, c, d)
    # o lançamento de outra declaração em `ja_marcada` já mexeu no retrato: o "antes" é depois dele
    antes_e_depois_iguais(user_id, lambda: dinheiro(c, user_id, d, lid).status_code == 409 or pytest.fail(caso))


def test_h4_lancamento_de_outro_usuario_e_recusado(user_id, outro):
    cid, d, _, c = ana2(user_id, com_40=False)
    alheio = lanca(outro)
    antes_a, antes_b = retrato(user_id), retrato(outro)
    assert dinheiro(c, user_id, d, alheio).status_code == 409
    assert retrato(user_id) == antes_a and retrato(outro) == antes_b and marcadores(outro) == {}


def test_h4_converter_nao_mexe_no_saldo_de_outro_usuario(user_id, outro):
    """Isolamento do `update accounts` da conversão: o saldo de B (777) fica; só o de A anda, uma vez."""
    cid, d, _, c = ana2(user_id, com_40=False)
    q("update accounts set balance=777 where user_id=%s returning 1", (outro,))
    assert dinheiro(c, user_id, d).status_code == 200
    assert (get_balance(user_id), get_balance(outro)) == (950, 777)


@pytest.mark.parametrize("dia", [3, -3])
def test_h4_c_na_borda_e_aceita(user_id, dia):
    cid, d, _, c = ana2(user_id, com_40=False)
    lid = lanca(user_id, dia=dia)
    r = dinheiro(c, user_id, d, lid)
    assert r.status_code == 200 and r.json()["launch_id"] == lid, r.text
    assert set(marcadores(user_id)) == {"manual mercado"} and q("select count(*) n from bank_movement_declarations")["n"] == 0


def test_h6_lista_alvo_parecido_primeiro_depois_dia_e_so_cinco(user_id):
    cid, d, _, c = ana2(user_id, com_40=False)
    uber = lanca(user_id, dia=0, alvo="uber")
    mercado = lanca(user_id, dia=1, alvo="mercado")
    ids = [lanca(user_id, dia=0, alvo="uber") for _ in range(5)]  # seis iguais: só cinco, ordenados por id
    cash = c.get(f"/open-finance/{user_id}/movements").json()["movements"][0]["cash"]
    assert [x["id"] for x in cash] == [mercado, uber, *ids[:3]] and len(cash) == 5


def test_h5_declaracao_casada_exige_desfazer_antes(user_id):
    cid, d, c = ana(user_id)  # T1 casou sozinha com a declaração
    c40 = lanca(user_id)
    antes_e_depois_iguais(user_id, lambda: [dinheiro(c, user_id, d).status_code == 409 or pytest.fail("converteu casada"),
                                            dinheiro(c, user_id, d, c40).status_code == 409 or pytest.fail("selecionou casada")])
    assert c.get(f"/open-finance/{user_id}/movements?launch_id={d}").json()["movements"][0]["cash"] is None
    assert desfaz(c, user_id, d, "T1").status_code == 200  # positivo: depois do undo vale
    assert dinheiro(c, user_id, d).status_code == 200
    assert retrato(user_id)["C"] == 900.0 and marcadores(user_id) == {"manual mercado": {"convertida": True}}


def test_h7_apagar_a_marcada_e_a_convertida_devolve_o_dinheiro(user_id):
    cid, d, c40, c = ana2(user_id)
    assert dinheiro(c, user_id, d, c40).status_code == 200
    r = c.delete(f"/launches/{user_id}/{c40}", headers=H)  # apagar a #40 leva o contexto e devolve V
    assert r.status_code == 200, r.text
    assert retrato(user_id) == R(SOMBRA9, [solta("T9", -39.9, 0)], [], 39.9, 1000.0) and marcadores(user_id) == {}
    d2 = declara(user_id, criado_em=em(0))
    assert dinheiro(c, user_id, d2).status_code == 200 and get_balance(user_id) == 950
    r = c.delete(f"/launches/{user_id}/{d2}", headers=H)  # apagar a convertida devolve V; a declaração não volta
    assert r.status_code == 200, r.text
    assert retrato(user_id) == R(SOMBRA9, [solta("T9", -39.9, 0)], [], 39.9, 1000.0)


def test_h11_converter_saldo_negativo_receita_e_confirmada(user_id, libera):
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 10, "pix", None, "outros", em(-1))  # Carteira 10,00
    d = declara(user_id, criado_em=em(0))
    c = cliente(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        assert nao_confirmada(cur, user_id, get_balance(user_id)) is False
    r = dinheiro(c, user_id, d)
    assert r.status_code == 200 and r.json()["balance"] == -40.0, r.text  # negativo permitido
    with get_conn() as conn, conn.cursor() as cur:
        assert nao_confirmada(cur, user_id, get_balance(user_id)) is False  # `funding_source` saiu
    libera(user_id)
    i = linha(user_id, d)
    assert i["origem"] == "carteira" and i["pode"] == ["categoria", "descricao", "data", "valor", "apagar"]
    d3 = declara(user_id, 30, "receita", em(0), "freela", categoria="outros")  # receita: soma
    assert dinheiro(c, user_id, d3).json()["balance"] == -10.0
    assert q("select efeitos->>'delta_conta' d from launches where id=%s", (d3,))["d"] == "30.0"


def test_h11b_converte_o_declarado_nao_o_projetado(user_id):
    cid, _ = _bank(user_id)
    promote_to_pro(user_id)
    q("update accounts set balance=1000 where user_id=%s returning 1", (user_id,))
    d = declara(user_id, criado_em=em(0))
    sincroniza(cid, user_id, t("T1", -52, 1, "FARMACIA"))  # sem evidência: só a escolha manual casa (até R$ 5,00)
    c = cliente(user_id)
    assert confirma(c, user_id, d, "T1").status_code == 200
    assert q("select valor from launches where id=%s", (d,))["valor"] == 52  # projetado no extrato
    q("delete from open_finance_transactions where id=%s returning 1", (tid(user_id, "T1"),))  # o banco apagou a T
    assert q("select matched_transaction_id m from bank_movement_declarations where launch_id=%s", (d,))["m"] is None
    assert dinheiro(c, user_id, d).status_code == 200
    assert (q("select valor, criado_em from launches where id=%s", (d,))["valor"], get_balance(user_id)) == (50, 950)
    assert retrato(user_id)["L"][0][3] == 0 and retrato(user_id)["C"] == 950.0  # e a data é a de BASE, não a de BASE+1


def test_h14_aporte_recusa_a_acao(user_id):
    _, lid, c = _aporte(user_id, category="Same person transfer")  # sem par: o 409 não pode ser o "já casada"
    assert q("select matched_transaction_id m from bank_movement_declarations where launch_id=%s", (lid,))["m"] is None
    antes = (q("select * from bank_movement_declarations where launch_id=%s", (lid,)), get_balance(user_id))
    assert dinheiro(c, user_id, lid).status_code == 409
    assert (q("select * from bank_movement_declarations where launch_id=%s", (lid,)), get_balance(user_id)) == antes
