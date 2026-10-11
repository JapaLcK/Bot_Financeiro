"""Cartão vindo do banco (Open Finance) não se apaga (P8). Banco real.

Apagar o cartão do sync é desfeito no sync seguinte (recria o cartão e reimporta as compras) e
perde o que o usuário mexeu: `paid_amount` manual, categorias, nome e limite. A guarda mora em
`delete_card` (única porta do WhatsApp e do /app); o WhatsApp responde a frase e não arma
pendência, o /app responde 409 e o `delete-impact` avisa já (`bloqueio`) para o modal.

CONTROLES NEGATIVOS (rodados na entrega, ver o relato): tirar o `raise CartaoDoBanco` deixa
vermelhos 1, 4 e 6; tirar a checagem do `handle` deixa o 3; a do `_is_delete` (duplicate_card_name)
deixa o 5; o `except` fora do `with restore_pending_on_error` deixa a pendência viva no 4;
`bloqueio` sempre None deixa o delete-impact; a regex antiga deixa os positivos novos.
CONTROLES POSITIVOS: cartão manual apaga (2, 6, 7); as 4 frases antigas seguem funcionando.
"""
from __future__ import annotations

import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import q
from tests.test_credit_transactions_endpoints import _auth, _csrf_headers
from tests.test_of_cartao_sinal import faturas, rodar  # noqa: F401 (fixture)

NOME = "Nubank Mastercard"  # o cartão que o sync cria
COMPRA = ("a", 120, "Shopping", "Loja do bairro")
TEM_CERTEZA = "Tem certeza que deseja excluir o cartão"


def cartoes(uid):
    return q("select id, name, credit_limit, open_finance_account_id from credit_cards "
             "where user_id=%s order by id", (uid,), True)


def cartao_id(uid, nome=NOME):
    return next(c["id"] for c in cartoes(uid) if c["name"] == nome)


def pendencia(uid):
    p = db.get_pending_action(uid)
    return p and (p["action_type"], (p.get("payload") or {}).get("step"))


def do_sync(uid, rodar):
    """Cartão do banco com ajuste manual: limite, nome e `paid_amount`."""
    rodar(uid, [COMPRA])
    cid = cartao_id(uid)
    q("update credit_cards set credit_limit=5000 where id=%s", (cid,))
    q("update credit_bills set paid_amount=10 where user_id=%s", (uid,))
    return cid


def tudo(uid):
    return (cartoes(uid), faturas(uid),
            q("select paid_amount from credit_bills where user_id=%s", (uid,), True))


# ── 1, 2. a guarda no db ─────────────────────────────────────────────────────

def test_delete_card_recusa_cartao_do_banco_e_nada_muda(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    antes = tudo(uid_pro)
    with pytest.raises(db.CartaoDoBanco, match="vem do seu banco"):
        db.delete_card(uid_pro, cid)
    assert tudo(uid_pro) == antes
    rodar(uid_pro, [COMPRA])  # o sync seguinte também não tem o que reconstruir
    assert tudo(uid_pro) == antes
    assert db.MSG_CARTAO_DO_BANCO == str(db.CartaoDoBanco(db.MSG_CARTAO_DO_BANCO))


def test_controle_positivo_cartao_manual_apaga(uid_pro):
    cid = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    assert db.delete_card(uid_pro, cid) is True
    assert cartoes(uid_pro) == []


def test_delete_card_de_outro_usuario_continua_false(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    outro = uid_pro + 1
    db.ensure_user(outro)
    assert db.delete_card(outro, cid) is False
    assert len(cartoes(uid_pro)) == 1


def test_delete_card_trava_a_linha_e_relê_o_vinculo_depois_do_lock(uid_pro, rodar):
    """Corrida: o sync adota o cartão manual enquanto o delete espera. Sem o `for update` o delete
    lê o vínculo nulo e apaga. A thread tem teto de tempo: nunca trava a suíte."""
    sync_id = do_sync(uid_pro, rodar)
    conta = next(c["open_finance_account_id"] for c in cartoes(uid_pro) if c["id"] == sync_id)
    manual = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    res = {}

    def apaga():
        try:
            res["r"] = db.delete_card(uid_pro, manual)
        except Exception as e:  # noqa: BLE001
            res["e"] = e

    t = threading.Thread(target=apaga, daemon=True)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("select id from credit_cards where id=%s for update", (manual,))
        t.start()
        t.join(0.7)
        assert t.is_alive(), f"delete_card não esperou o lock da linha: {res}"
        cur.execute("update credit_cards set open_finance_account_id=null where id=%s", (sync_id,))
        cur.execute("update credit_cards set open_finance_account_id=%s where id=%s", (conta, manual))
    t.join(10)
    assert not t.is_alive()
    assert isinstance(res.get("e"), db.CartaoDoBanco), res
    assert manual in {c["id"] for c in cartoes(uid_pro)}


# ── 3. a conversa ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "apagar cartão Nubank Mastercard", "excluir cartao nubank mastercard", "remove meu cartão Nubank Mastercard",
    "Excluir o cartão Nubank Mastercard",
])
def test_whatsapp_recusa_e_nao_arma_pendencia(uid_pro, ia_fora, rodar, frase):
    do_sync(uid_pro, rodar)
    manda(uid_pro, "gastei 50 no mercado")
    resposta = manda(uid_pro, frase)
    if "nubank" in frase.lower():
        assert db.MSG_CARTAO_DO_BANCO in resposta, resposta
    assert TEM_CERTEZA not in resposta
    assert (pendencia(uid_pro) or ("",))[0] != "credit_delete_card"
    manda(uid_pro, "sim")
    assert len(cartoes(uid_pro)) == 1


# ── 4. corrida: o cartão era manual quando a pergunta foi feita ───────────────

def test_sim_depois_do_sync_adotar_o_cartao_recusa_e_consome_a_pendencia(uid_pro, ia_fora, rodar):
    db.create_card(uid_pro, NOME, closing_day=10, due_day=17)
    assert TEM_CERTEZA in manda(uid_pro, "apagar cartão Nubank Mastercard")
    rodar(uid_pro, [COMPRA])  # adota o cartão manual de mesmo nome
    assert cartoes(uid_pro)[0]["open_finance_account_id"] is not None
    assert db.MSG_CARTAO_DO_BANCO in manda(uid_pro, "sim")
    assert len(cartoes(uid_pro)) == 1
    assert pendencia(uid_pro) is None  # fora do `with`, a pendência voltaria e o "sim" repetiria
    assert TEM_CERTEZA not in manda(uid_pro, "sim")


# ── 5. nome duplicado: "excluir" o cartão que é do banco ─────────────────────

def test_criar_cartao_com_nome_do_banco_e_excluir_recusa(uid_pro, ia_fora, rodar):
    do_sync(uid_pro, rodar)
    manda(uid_pro, f"criar cartao {NOME} fecha 1 vence 8")
    assert pendencia(uid_pro) == ("credit_card_setup", "duplicate_card_name")
    resposta = manda(uid_pro, "excluir")
    assert db.MSG_CARTAO_DO_BANCO in resposta and "cancelar" in resposta, resposta
    assert pendencia(uid_pro) == ("credit_card_setup", "duplicate_card_name")
    assert len(cartoes(uid_pro)) == 1


def test_nome_duplicado_manual_adotado_antes_do_sim_recusa(uid_pro, ia_fora, rodar):
    db.create_card(uid_pro, NOME, closing_day=10, due_day=17)
    manda(uid_pro, f"criar cartao {NOME} fecha 1 vence 8")
    assert "Tem certeza" in manda(uid_pro, "excluir")
    assert pendencia(uid_pro) == ("credit_card_setup", "confirm_delete_existing_card")
    rodar(uid_pro, [COMPRA])  # o sync adota o cartão entre o "excluir" e o "sim"
    assert db.MSG_CARTAO_DO_BANCO in manda(uid_pro, "sim")
    assert len(cartoes(uid_pro)) == 1 and pendencia(uid_pro) is None


# ── 6, 7. /app ───────────────────────────────────────────────────────────────

def _client(uid):
    client = TestClient(dashboard.app)
    _auth(client, uid)
    return client, _csrf_headers(client)


def test_app_delete_409_no_cartao_do_banco_e_200_no_manual(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    manual = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    client, h = _client(uid_pro)
    resp = client.delete(f"/cards/{uid_pro}/{cid}", headers=h)
    assert resp.status_code == 409 and resp.json()["detail"] == db.MSG_CARTAO_DO_BANCO, resp.text
    assert {c["id"] for c in cartoes(uid_pro)} == {cid, manual}
    assert client.delete(f"/cards/{uid_pro}/{manual}", headers=h).status_code == 200
    assert [c["id"] for c in cartoes(uid_pro)] == [cid]


def test_delete_impact_traz_bloqueio_so_no_cartao_do_banco(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    manual = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    client, _h = _client(uid_pro)
    assert client.get(f"/cards/{uid_pro}/{cid}/delete-impact").json()["bloqueio"] == db.MSG_CARTAO_DO_BANCO
    assert client.get(f"/cards/{uid_pro}/{manual}/delete-impact").json()["bloqueio"] is None


def test_delete_impact_de_cartao_de_outro_usuario_e_404(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    outro = uid_pro + 1
    db.ensure_user(outro)
    client, _h = _client(outro)
    assert client.get(f"/cards/{outro}/{cid}/delete-impact").status_code == 404


# ── 7. desconectar o banco continua tirando o cartão (a saída que a frase indica) ──

def test_desconectar_remove_o_cartao_do_banco(uid_pro, rodar):
    do_sync(uid_pro, rodar)
    db.disconnect_open_finance_connection(uid_pro)
    assert cartoes(uid_pro) == []


def test_desconectar_com_compra_manual_solta_o_vinculo_e_o_cartao_passa_a_apagar(uid_pro, rodar):
    cid = do_sync(uid_pro, rodar)
    db.add_credit_purchase(user_id=uid_pro, card_id=cid, valor=20.0, categoria="outros",
                           nota="manual", purchased_at=date.today())
    db.disconnect_open_finance_connection(uid_pro)
    assert cartoes(uid_pro)[0]["open_finance_account_id"] is None
    assert db.delete_card(uid_pro, cid) is True


# ── regex do WhatsApp: "excluir o cartão X" e irmãos ──────────────────────────

def _cartao_nubank(uid):
    return db.create_card(uid, "Nubank", closing_day=10, due_day=17)


@pytest.mark.parametrize("frase", [
    "excluir o cartão Nubank", "excluir o cartao nubank", "remove meu cartão nubank",
    "remova o meu cartão nubank", "apaga esse cartão nubank", "exclui o cartão do nubank",
    "deleta meu cartão de crédito nubank", "excluir o cartão nubank?",
    # prefixos de fala natural e marcador/emoji no começo (armavam na main)
    "me ajuda a excluir o cartão nubank", "ok, excluir o cartão nubank",
    "eu queria excluir cartão nubank", "eu preciso excluir cartão nubank", "vc pode excluir cartão nubank",
    "você pode apagar cartão nubank", "vamos excluir cartão nubank", "pode me ajudar a excluir cartão nubank",
    "agora excluir cartão nubank", "tá, apagar cartão nubank", "então remover cartão nubank",
    "👉 excluir cartão nubank", "- excluir cartão nubank", "1. excluir cartão nubank",
    # controle positivo: as frases que já funcionavam
    "excluir cartao Nubank", "apagar cartão nubank", "remover cartão nubank", "deletar cartao nubank",
])
def test_frases_que_apagam_cartao_pedem_confirmacao(uid_pro, ia_fora, frase):
    _cartao_nubank(uid_pro)
    manda(uid_pro, "gastei 50 no mercado")
    assert TEM_CERTEZA in manda(uid_pro, frase)
    assert pendencia(uid_pro)[0] == "credit_delete_card"
    assert ia_fora == []


@pytest.mark.parametrize("frase", [
    "apagar a compra do cartão nubank", "remover o gasto no cartão nubank", "excluir a fatura do cartão nubank",
    "tirar o limite do cartão nubank", "remove a última compra do cartão nubank",
    "apagar compra no cartão nubank", "excluir parcelamento do cartão nubank",
    "remover lembrete do cartão nubank", "apagar o lembrete do meu cartão nubank",
    "tirar o cartão nubank", "excluir o cartão", "remove meu cartão", "Excluir o cartão",
])
def test_frases_parecidas_nao_apagam_cartao(uid_pro, ia_fora, frase):
    _cartao_nubank(uid_pro)
    manda(uid_pro, "gastei 50 no mercado")
    assert TEM_CERTEZA not in manda(uid_pro, frase)
    assert (pendencia(uid_pro) or ("",))[0] != "credit_delete_card"
    assert len(cartoes(uid_pro)) == 1


@pytest.mark.parametrize("frase, resposta", [
    ("não apague o cartão nubank", "ok"),
    ("o que acontece se eu excluir o cartão nubank?", "pode"),
    ("quero saber se dá pra remover o cartão nubank", "quero"),
    ("não quero excluir o cartão nubank", "quero"),
    ("nunca apague meu cartão nubank", "ok"),
    ("posso apagar o cartão nubank?", "claro"),
    ("já exclui o cartão nubank", "ok"),
    ("remove o cartão e o gasto", "ok"),
    ("excluir o cartão do banco", "ok"),
    ("não excluir cartão nubank", "ok"),
    ("jamais apagar cartão nubank", "ok"),
    ("pode não apagar o cartão nubank", "ok"),
    ("quero não excluir o cartão nubank", "quero"),
    ("preciso saber se posso apagar o cartão nubank", "ok"),
    ("gostaria de saber se dá pra excluir o cartão nubank", "ok"),
    ("por favor não apague o cartão nubank", "ok"),
    ("não quero excluir o cartão nubank", "ok"),
    ("vc pode não apagar o cartão nubank", "ok"),
    ("vamos não excluir o cartão nubank", "ok"),
    ("eu queria saber se excluo o cartão nubank", "ok"),
    ("você pode me dizer como excluir o cartão nubank", "ok"),
    ("pode me explicar como apagar o cartão nubank", "ok"),
    ("nubank: excluir cartão nubank", "ok"),
    ("ok não excluir o cartão nubank", "ok"),
    ("me ajuda a não excluir o cartão nubank", "ok"),
    ("apagar cartão nubank\nnão, pera", "ok"),
    ("excluir o cartão nubank\nmentira, não exclui não", "ok"),
])
def test_negacao_pergunta_e_hipotese_nao_armam_a_exclusao(uid_pro, ia_fora, frase, resposta):
    """Antes da âncora, "ok/pode/quero" depois destas frases apagava o cartão com faturas e compras."""
    _cartao_nubank(uid_pro)
    manda(uid_pro, "gastei 50 no mercado")
    assert TEM_CERTEZA not in manda(uid_pro, frase)
    assert (pendencia(uid_pro) or ("",))[0] != "credit_delete_card"
    manda(uid_pro, resposta)
    assert [c["name"] for c in cartoes(uid_pro)] == ["Nubank"]


@pytest.mark.parametrize("frase", ["por favor, excluir cartão nubank", "quero excluir o cartão nubank",
                                   "pode apagar meu cartão nubank"])
def test_pedido_educado_curto_ainda_pede_confirmacao(uid_pro, ia_fora, frase):
    _cartao_nubank(uid_pro)
    assert TEM_CERTEZA in manda(uid_pro, frase)


@pytest.mark.parametrize("nome, frase", [("Do Bem", "excluir cartão Do Bem"), ("Nubank", "exclui o cartão do nubank"),
                                         ("Da Vinci", "apagar o cartão da vinci")])
def test_do_da_do_comeco_do_nome_nao_se_perde(uid_pro, ia_fora, nome, frase):
    db.create_card(uid_pro, nome, closing_day=10, due_day=17)
    assert f"excluir o cartão *{nome}*" in manda(uid_pro, frase)


def test_regex_de_exclusao_e_linear_em_texto_grande():
    import time
    from core.handlers.credit import _extract_card_name_for_delete as f
    for t in ("vamos " * 10000, "vamos " * 10000 + "excluir cartão a", "👉 " * 20000 + "x",
              "excluir cartão a" + " " * 60000 + "b", "excluir cartão a\n" + "x" * 60000, "excluir cartão " + "a " * 30000, "x" * 60000, " " * 60000 + "excluir cartão a"):
        t0 = time.perf_counter()
        f(t)
        assert time.perf_counter() - t0 < 0.5
