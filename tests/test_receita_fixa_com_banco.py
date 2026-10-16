"""R2: receita fixa manual só para quem NÃO tem banco conectado.

Com banco (conexão fora de PAUSED/DELETED, a regra da Q40): o escritor, o WhatsApp e o GET
recusam; a Previsão nem lê a manual (a do extrato a substitui). Sem banco, tudo como antes.
Banco real; a manual nasce ANTES do banco (depois dele o escritor recusa).

Controles negativos (medidos, cada um desliga UM conserto e deixa estes vermelhos):
* `receitas = ler_receitas(...)` em `cashflow_snapshot.ler`: `test_previsao_por_status` (UPDATED,
  LOGIN_ERROR, OUTDATED, UPDATING), `test_desconectou_*` e o isolamento;
* as duas recusas de `db/recurring_income.py`: `test_escritor_com_banco_recusa_*`, o POST/PATCH
  com banco e o isolamento;
* `recusa` na primeira linha de `core/handlers/recurring.add`: só o (c), porque o roteador cobre os outros;
* `recusa` do `core/intent_router.route`: (a) e (d); com o handler também fora, (a)-(e) caem
  (o (b) cairia em "Qual o valor");
* o consumo da clarification do recurring.add na recusa do roteador: `test_conversa_g_*`; o aviso "🔕"
  na frente (`AVISO`): `test_conversa_g_a_recusa_consome_*` e `test_recusa_*_aviso`; o try/except do consumo:
  `test_recusa_com_consumo_que_falha_*`; o `not ignora_pendencias`: `test_ignora_pendencias_nao_consome`;
* o `except FonteUnicaOF` de `core/handlers/recurring.add`: `test_handler_corrida_*`;
* o `confidence >= 0.55` do roteador: `test_roteador_so_recusa_*` (0.55 recusa, 0.54 e 0.3 não);
* a política "consulta falhou = com banco": `test_consulta_que_falha_conta_como_banco_*`;
* o `if recusa_receita` da rota GET: os dois `test_get_*`.
Positivos: sem banco (e PAUSED/DELETED) a manual conta, cria e edita; um código que recusasse
ou ignorasse a manual de todo mundo deixa esses vermelhos."""
import json
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
from _apoio_assinaturas import HOJE, conta, mensais, semeia
from _fusao_of_helpers import manda
from conftest import em_carencia, usuario_pagante
from core.services.fonte_unica import MSG_RECEITA, FonteUnicaOF, recusa_receita
from core.types import IncomingMessage
from db.of_recurring import ler_frescor
from db.recurring_income import (count_active_recurring_incomes, create_recurring_income,
                                 delete_recurring_income, get_recurring_income,
                                 list_recurring_incomes, update_recurring_income)
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
from tests.test_previsao_recorrencias_of import confirma, ler_em
from tests.test_previsao_recorrencias_of_convivencia import saldo_90

RECUSA = MSG_RECEITA
AVISO = "🔕 Cancelei a pergunta anterior.\n\n"
DOC = "55566677788"
VIVOS = ["UPDATED", "LOGIN_ERROR", "OUTDATED", "UPDATING"]
TERMINAIS = ["PAUSED", "DELETED"]


def _status(cid: int, status: str) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update open_finance_connections set status=%s where id=%s", (status, cid))
        conn.commit()


def _com_banco(uid, status="UPDATED", valor="6500") -> int:
    """Salário no extrato (3 créditos do mesmo pagador) em conexão com o `status` dado."""
    txs = mensais("sal", [valor] * 3, ultima=date(2026, 9, 5), desc="EMPRESA X", pagador=DOC)
    cid = semeia(uid, [conta("acc-sal", txs)], [])
    confirma(uid, DOC)  # o Sim do usuário: sem ele a receita do extrato é só candidata e fica fora da conta
    _status(cid, status)
    return cid


def _manual(uid, valor=5000, dia=5):
    return create_recurring_income(uid, "Trabalho", valor, "salario", dia, start_date=HOJE)


def _receitas_do_motor(s):
    return [e for e in s.ocorrencias if e.fonte == "receita_recorrente"]


# ── Previsão: quem decide é a leitura ────────────────────────────────────────

@pytest.mark.parametrize("status,conta_a_manual", [
    (None, True), ("PAUSED", True), ("DELETED", True),
    ("UPDATED", False), ("LOGIN_ERROR", False), ("OUTDATED", False), ("UPDATING", False),
], ids=lambda v: str(v))
def test_previsao_por_status(user_id, status, conta_a_manual):
    db.set_balance(user_id, D(1000))
    _manual(user_id)  # antes do banco
    if status:
        _com_banco(user_id, status)
    s = ler_em(user_id)
    assert bool(_receitas_do_motor(s)) is conta_a_manual
    if conta_a_manual:  # 3 ciclos de 5000 em 90 dias; o extrato (6500) não é lido sem banco vivo
        assert saldo_90(s) == D("16000")
    elif status == "UPDATED":  # só o extrato: 3 × 6500, a manual não soma
        assert saldo_90(s) == D("20500")


def test_desconectou_a_manual_volta_a_contar(user_id):
    db.set_balance(user_id, D(1000))
    _manual(user_id)
    cid = _com_banco(user_id)
    assert not _receitas_do_motor(ler_em(user_id))
    _status(cid, "DELETED")
    assert saldo_90(ler_em(user_id)) == D("16000")


def test_freio_do_saldo_consolidado_a_manual_volta_a_contar(user_id):
    db.set_balance(user_id, D(1000))
    _manual(user_id)
    _com_banco(user_id)
    s = ler_em(user_id, bancos=False)  # OF_CONSOLIDATED_BALANCE_ENABLED=0: banco fora da base
    assert _receitas_do_motor(s) and saldo_90(s) == D("16000")


@pytest.mark.parametrize("status", [None, *VIVOS, *TERMINAIS, "paused", "", "deleted"], ids=str)
def test_as_duas_formas_da_regra_dizem_o_mesmo(user_id, status):
    """§0.7: o motor (`ler_frescor`), a Q40 (`has_open_finance_connections`) e a recusa."""
    if status is not None:
        _com_banco(user_id, status)
    with db.get_conn() as conn, conn.cursor() as cur:
        motor = bool(ler_frescor(cur, user_id))
    assert motor == db.has_open_finance_connections(user_id) == (recusa_receita(user_id) == RECUSA)


def test_isolamento_a_com_banco_b_sem_banco():
    a, b = usuario_pagante(), usuario_pagante()
    db.set_balance(a, D(1000)); db.set_balance(b, D(1000))
    _manual(a); _com_banco(a)
    _manual(b, valor=700)
    sa, sb = ler_em(a), ler_em(b)
    assert not _receitas_do_motor(sa) and saldo_90(sa) == D("20500")
    assert [e.valor for e in _receitas_do_motor(sb)] == [D("700")] * 3 and saldo_90(sb) == D("3100")
    assert recusa_receita(a) == RECUSA and recusa_receita(b) is None
    with pytest.raises(FonteUnicaOF):
        create_recurring_income(a, "Outra", 1, "salario", 1)
    assert create_recurring_income(b, "Outra", 1, "salario", 1)["name"] == "Outra"
    assert all(i["name"] != "Outra" for i in list_recurring_incomes(a, True))


# ── Escritor ─────────────────────────────────────────────────────────────────

def test_escritor_com_banco_recusa_create_e_update_e_deixa_a_linha_intacta(user_id):
    inc = _manual(user_id)
    antes = get_recurring_income(user_id, inc["id"])
    _com_banco(user_id)
    with pytest.raises(FonteUnicaOF) as e:
        create_recurring_income(user_id, "Nova", 100, "salario", 1)
    assert str(e.value) == RECUSA and isinstance(e.value, ValueError)
    for kw in ({"amount": 1}, {"is_active": False}, {"name": "X"}, {"is_active": True}):
        with pytest.raises(FonteUnicaOF) as e:
            update_recurring_income(user_id, inc["id"], **kw)
        assert str(e.value) == RECUSA
    assert count_active_recurring_incomes(user_id) == 1 and len(list_recurring_incomes(user_id, True)) == 1
    assert get_recurring_income(user_id, inc["id"]) == antes


def test_escritor_com_banco_o_delete_segue_liberado(user_id):
    inc = _manual(user_id)
    _com_banco(user_id)
    delete_recurring_income(user_id, inc["id"])
    assert list_recurring_incomes(user_id, True) == []


@pytest.mark.parametrize("status", [None, "PAUSED", "DELETED"], ids=str)
def test_escritor_sem_banco_vivo_cria_e_edita(user_id, status):
    inc = _manual(user_id)
    if status:
        _com_banco(user_id, status)
    assert update_recurring_income(user_id, inc["id"], amount=900)["amount"] == 900
    assert create_recurring_income(user_id, "Nova", 100, "salario", 1)["name"] == "Nova"


# ── Rotas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import frontend.finance_bot_websocket_custom as app
    monkeypatch.setattr(app, "_authorize_dashboard_access", lambda *a: None)
    monkeypatch.setattr(app.limiter, "enabled", False)
    c = TestClient(app.app, raise_server_exceptions=False)
    c.cookies.set(app.CSRF_COOKIE_NAME, "r2-csrf")
    c.headers[app.CSRF_HEADER_NAME] = "r2-csrf"
    return c


def test_get_com_banco_e_legado_gravado_vem_vazio_e_avisa(client, pro_user_id):
    _manual(pro_user_id)
    _com_banco(pro_user_id)
    r = client.get(f"/recurring-incomes/{pro_user_id}?include_inactive=true")
    assert r.status_code == 200 and r.json() == {"ok": True, "incomes": [], "banco_conectado": True}
    assert len(list_recurring_incomes(pro_user_id, True)) == 1  # o legado fica no banco


def test_get_sem_plano_com_banco_tambem_recebe_a_lista_vazia(client, user_id):
    assert client.get(f"/recurring-incomes/{user_id}").status_code == 403  # sem banco e sem plano: como antes
    _com_banco(user_id)
    r = client.get(f"/recurring-incomes/{user_id}")
    assert r.status_code == 200 and r.json()["banco_conectado"] is True


def test_get_sem_banco_traz_a_lista_e_banco_conectado_false(client, pro_user_id):
    _manual(pro_user_id)
    r = client.get(f"/recurring-incomes/{pro_user_id}")
    assert r.status_code == 200 and r.json()["banco_conectado"] is False
    assert [i["name"] for i in r.json()["incomes"]] == ["Trabalho"]


def test_post_e_patch_com_banco_dao_400_com_o_texto_e_nao_gravam(client, pro_user_id):
    inc = _manual(pro_user_id)
    _com_banco(pro_user_id)
    body = {"name": "Nova", "amount": 10, "category": "salario", "pay_day": 3}
    r = client.post(f"/recurring-incomes/{pro_user_id}", json=body)
    assert (r.status_code, r.json()["detail"]) == (400, RECUSA)
    r = client.patch(f"/recurring-incomes/{pro_user_id}/{inc['id']}", json={"is_active": False})
    assert (r.status_code, r.json()["detail"]) == (400, RECUSA)
    (so,) = list_recurring_incomes(pro_user_id, True)
    assert so["name"] == "Trabalho" and so["is_active"] is True


def test_post_patch_delete_sem_banco_seguem_como_antes_e_o_delete_vale_com_banco(client, pro_user_id):
    r = client.post(f"/recurring-incomes/{pro_user_id}",
                    json={"name": "Nova", "amount": 10, "category": "salario", "pay_day": 3})
    assert r.status_code == 200, r.text
    iid = r.json()["income"]["id"]
    assert client.patch(f"/recurring-incomes/{pro_user_id}/{iid}", json={"amount": 20}).status_code == 200
    _com_banco(pro_user_id)
    assert client.delete(f"/recurring-incomes/{pro_user_id}/{iid}").status_code == 200
    assert list_recurring_incomes(pro_user_id, True) == []


# ── Conversa pelo handle_incoming (OpenAI simulada; classificador, roteador e gate reais) ──

@pytest.fixture
def ia(monkeypatch):
    """`ia["receita"]` = o JSON que o classificador devolve para a frase do salário."""
    import openai
    cfg = {"receita": {"intent": "recurring.add", "confidence": 0.99, "needs_clarification": False,
                       "clarification_question": None,
                       "entities": {"tipo": "receita", "valor": 3000, "dia": 5, "nome": "salário"}},
           "despesa": {"intent": "recurring.add", "confidence": 0.99, "needs_clarification": False,
                       "clarification_question": None,
                       "entities": {"valor": 100, "dia": 10, "nome": "internet", "categoria": "moradia"}},
           "avulso": {"intent": "launches.add", "confidence": 0.97, "needs_clarification": False,
                      "clarification_question": None,
                      "entities": {"tipo": "despesa", "valor": 15, "alvo": "mercado", "categoria": "mercado"}}}
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-no-network")

    def complete(**kw):
        texto = str(kw["messages"][-1]["content"]).lower()  # a frase do usuário, não o prompt
        chave = ("receita" if "salario" in texto or "salário" in texto
                 else "despesa" if "internet" in texto else "avulso")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(cfg[chave])))])
    monkeypatch.setattr(openai, "OpenAI", lambda **k: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))
    return cfg


def _linhas(uid):
    return len(list_recurring_incomes(uid, True))


def _sem_pico(texto):
    return texto.removeprefix("🐷 ")


def test_conversa_a_essencial_com_banco_recebe_a_recusa_e_a_pendencia_fica(ia):
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    manda(uid, "gastei 15 no mercado")
    antes = db.get_pending_action(uid)
    assert antes and antes["action_type"] == "payment_method_choice"
    resposta = manda(uid, "recebo meu salario de 3000 todo dia 5")
    assert _sem_pico(resposta) == RECUSA, resposta
    assert _linhas(uid) == 0
    depois = db.get_pending_action(uid)
    assert (depois["action_type"], depois["payload"], depois["created_at"]) == (
        antes["action_type"], antes["payload"], antes["created_at"])


def test_conversa_b_sem_valor_recusa_em_vez_de_perguntar_o_valor(ia):
    ia["receita"]["entities"].pop("valor")
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    resposta = manda(uid, "recebo meu salario todo dia 5")
    assert _sem_pico(resposta) == RECUSA, resposta
    assert "Qual o valor" not in resposta
    p = db.get_pending_action(uid)
    assert not p or p["action_type"] != "clarification"


def test_conversa_c_plano_free_recebe_a_recusa_e_nao_o_convite_de_assinatura(ia):
    """O tier 3 (que classifica `recurring.add`) é pago, então o free só chega ao handler pela
    retomada de uma pergunta que ficou armada (a "do que é essa receita?" de quando pagava)."""
    uid = em_carencia(usuario_pagante())  # tier free com acesso (carência)
    _connect_fake_bank(uid)
    db.set_pending_action(uid, "clarification", {
        "intent": "recurring.add", "entities": {"tipo": "receita", "valor": 3000, "dia": 5},
        "question": "Do que é essa receita?", "orig_text": "recebo 3000 todo dia 5"})
    resposta = manda(uid, "trabalho")
    assert _sem_pico(resposta) == RECUSA, resposta
    assert "Essencial" not in resposta and _linhas(uid) == 0


def test_conversa_d_ia_que_pede_o_nome_recebe_a_recusa_e_nao_fica_pendencia(ia):
    ia["receita"].update(needs_clarification=True, clarification_question="Do que é essa receita?")
    ia["receita"]["entities"].pop("nome")
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    resposta = manda(uid, "recebo meu salario de 3000 todo dia 5")
    assert _sem_pico(resposta) == RECUSA, resposta
    p = db.get_pending_action(uid)
    assert not p or p["action_type"] != "clarification"


def test_conversa_e_gasto_fixo_na_mesma_conversa_com_banco_continua_criando(ia):
    from db.recurring import list_recurring_expenses
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    assert _sem_pico(manda(uid, "recebo meu salario de 3000 todo dia 5")) == RECUSA
    manda(uid, "gasto fixo de 100 de internet todo dia 10")
    assert [g["name"] for g in list_recurring_expenses(uid)] == ["Internet"]


def test_conversa_g_a_recusa_consome_a_pergunta_do_proprio_recurring_add(ia):
    """A clarification do recurring.add ("Do que é esse gasto fixo?", com o 3000 do salário) não
    pode sobrar armada: a resposta seguinte criaria um gasto fixo de R$ 3.000."""
    from db.recurring import list_recurring_expenses
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    db.set_pending_action(uid, "clarification", {
        "intent": "recurring.add", "entities": {"valor": 3000, "dia": 5},
        "question": "Do que é esse gasto fixo? (ex: aluguel, academia)", "orig_text": "gasto fixo de 3000 todo dia 5"})
    resposta = _sem_pico(manda(uid, "é meu salario"))
    assert resposta == AVISO + RECUSA, resposta  # consumiu a pergunta: o aviso vai na frente
    assert db.get_pending_action(uid) is None
    manda(uid, "gasto fixo de 100 de internet todo dia 10")  # vem da frase nova, não do 3000 velho
    assert [(g["name"], g["amount"]) for g in list_recurring_expenses(uid)] == [("Internet", 100)]


def test_conversa_g_a_clarification_de_outro_fluxo_fica_intacta(ia):
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    db.set_pending_action(uid, "clarification", {
        "intent": "launches.add", "entities": {"tipo": "despesa"},
        "question": "Qual foi o valor?", "orig_text": "gastei no mercado"})
    antes = db.get_pending_action(uid)
    assert _sem_pico(manda(uid, "recebo meu salario de 3000 todo dia 5")) == RECUSA
    depois = db.get_pending_action(uid)
    assert (depois["action_type"], depois["payload"], depois["created_at"]) == (
        antes["action_type"], antes["payload"], antes["created_at"])


@pytest.mark.parametrize("confianca,recusa", [(0.99, True), (0.55, True), (0.54, False), (0.3, False)], ids=str)
def test_roteador_so_recusa_a_receita_com_confianca_de_0_55(confianca, recusa):
    """Direto no `route` (o `handle_incoming` nem chega nele abaixo de 0.55): abaixo disso o turno
    segue para a ajuda, que não é a recusa, e a receita não é gravada de um jeito ou de outro."""
    from core.intent_classifier import IntentResult
    from core.intent_router import route
    uid = usuario_pagante("essencial")
    _connect_fake_bank(uid)
    resposta = route(IntentResult(intent="recurring.add", confidence=confianca,
                                  entities={"tipo": "receita", "valor": 3000, "dia": 5, "nome": "salário"}),
                     IncomingMessage(platform="whatsapp", user_id=uid, text="recebo meu salario de 3000 todo dia 5"))
    assert (_sem_pico(resposta) == RECUSA) is recusa, resposta
    assert _linhas(uid) == 0


def test_consulta_que_falha_conta_como_banco_e_recusa(monkeypatch):
    """Sem banco nenhum, mas com a consulta levantando: recusa (lado seguro; a Previsão já ignora a
    manual de quem tem banco). Quem não tem banco vê a recusa naquele turno."""
    uid = usuario_pagante("essencial")

    def falha(_uid):
        raise RuntimeError("banco piscou")
    monkeypatch.setattr(db, "has_open_finance_connections", falha)
    assert recusa_receita(uid) == RECUSA
    with pytest.raises(FonteUnicaOF) as e:
        create_recurring_income(uid, "Nova", 100, "salario", 1)
    assert str(e.value) == RECUSA
    monkeypatch.undo()
    assert recusa_receita(uid) is None  # controle positivo: sem a falha, quem não tem banco cria


def test_conversa_f_sem_banco_cria_a_receita_como_hoje(ia):
    uid = usuario_pagante("essencial")
    resposta = manda(uid, "recebo meu salario de 3000 todo dia 5")
    assert "Receita fixa criada" in resposta, resposta
    assert _linhas(uid) == 1


# ── Roteador direto: aviso ao descartar a pergunta, falha do consumo, ignora_pendencias ──

def _recusa_pelo_roteador(uid, **kw):
    from core.intent_classifier import IntentResult
    from core.intent_router import route
    return route(IntentResult(intent="recurring.add", confidence=0.99,
                              entities={"tipo": "receita", "valor": 3000}),
                 IncomingMessage(platform="whatsapp", user_id=uid, text="recebo salario 3000"), **kw)


def _arma_pergunta_do_recurring_add(uid):
    db.set_pending_action(uid, "clarification", {
        "intent": "recurring.add", "entities": {"valor": 1200},
        "question": "Do que é esse gasto fixo?", "orig_text": "o"})


def test_recusa_que_consome_a_pergunta_leva_o_aviso():
    uid = usuario_pagante("essencial"); _connect_fake_bank(uid)
    _arma_pergunta_do_recurring_add(uid)
    assert _recusa_pelo_roteador(uid) == AVISO + RECUSA
    assert db.get_pending_action(uid) is None


def test_recusa_sem_pergunta_armada_nao_leva_aviso():
    uid = usuario_pagante("essencial"); _connect_fake_bank(uid)
    assert _recusa_pelo_roteador(uid) == RECUSA


def test_recusa_com_consumo_que_falha_devolve_a_recusa_sem_aviso(monkeypatch):
    uid = usuario_pagante("essencial"); _connect_fake_bank(uid)
    _arma_pergunta_do_recurring_add(uid)

    def falha(*a, **k):
        raise RuntimeError("banco piscou")
    monkeypatch.setattr(db, "consume_pending_action", falha)
    assert _recusa_pelo_roteador(uid) == RECUSA  # não vira "erro interno", nem diz que cancelou
    assert db.get_pending_action(uid) is not None  # a pergunta não foi consumida


def test_ignora_pendencias_nao_consome():
    uid = usuario_pagante("essencial"); _connect_fake_bank(uid)
    _arma_pergunta_do_recurring_add(uid)
    assert _recusa_pelo_roteador(uid, ignora_pendencias=True) == RECUSA
    assert db.get_pending_action(uid) is not None


def test_handler_corrida_com_banco_que_conecta_depois_da_checagem_devolve_so_a_recusa(monkeypatch):
    """TOCTOU: a 1ª checagem (`recusa`) passa e o escritor recusa em seguida."""
    from core.handlers import recurring as h
    uid = usuario_pagante("essencial"); _connect_fake_bank(uid)
    monkeypatch.setattr(h, "recusa", lambda *a: None)
    resposta = h.add(uid, "recebo salario 3000 todo dia 5",
                     {"tipo": "receita", "valor": 3000, "dia": 5, "nome": "salário"})
    assert resposta == RECUSA and "Não consegui" not in resposta
    assert _linhas(uid) == 0
