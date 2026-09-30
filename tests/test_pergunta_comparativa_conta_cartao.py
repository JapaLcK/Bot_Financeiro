"""#568: pergunta comparativa dentro de "paguei a conta" ou da compra no cartão.

"paguei a luz e gastei mais em 2025 ou 2026?" pagava a conta com R$ 2.025, e
"comprei um tenis no credito e gastei mais em 2025 ou 2026?" gravava R$ 2.025 no
cartão: os dois liam o valor da mensagem inteira. Agora leem só o pedaço
legítimo, e o pedaço comparativo recebe o aviso do #570. Conversa real pelo
`handle_incoming`, banco real.

Qual conta o "paguei <nome>" quita mora em `tests/test_conta_por_nome.py`.

Limites conhecidos, não tratados aqui:
- R2-4: o aviso diz "depois de responder a pergunta acima" sempre que há uma
  pendência que não é oferta, inclusive uma antiga que não aparece na resposta.
- R2-8: o ramo BANCO do `add()` ("gastei 50 no mercado e gastei mais…?" com
  banco conectado e forma declarada banco) responde sem o aviso.
- R3-3: "paguei a lu e gastei mais…?" quita a Luz: o casamento parcial ("lu"
  dentro de "luz") conta como nomear a conta, como já contava sem a pergunta.
- #694: dois lançamentos na mesma frase sem pergunta ("paguei a luz e gastei 50
  no mercado") continuam registrando um só.
- #699: o detector não pega "será que", "tb", "!" nem "e quanto gastei em 2025?";
  nesses, o valor da pergunta ainda vira lançamento.
- O número do nome da conta deixou de virar valor (#700), mas sobram dois
  casos: com palavra no meio do nome ("paguei o ipva de 2025" para "IPVA 2025")
  o nome não é tirado do texto e o 2025 vira valor; e um número que não é do
  nome ("paguei a luz de 2025 e gastei mais…?") continua sendo o valor.
"""
from __future__ import annotations

import pytest

import core.handle_incoming as hi
import db
import db.bills as B
from conftest import usuario_pagante
from core.handlers import forma_pagamento as fp
from core.intent_classifier import contains_comparative_question, sem_perguntas_comparativas
from core.types import IncomingMessage
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_bill_amount_pending import _monta_conta_variavel
from tests.test_handle_incoming_routing import _Audio
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
from tests.test_multi_pergunta_comparativa_aviso import _ACHADO
from tests.test_multi_pergunta_comparativa_fila import _PERGUNTAS
from utils_date import today_tz

_ANO = "gastei mais em 2025 ou 2026?"
_AVISO = "parece uma pergunta"
_DEPOIS = "depois de responder a pergunta acima"  # com pergunta armada (A3)


def _valores(sql, uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid,))
        rows = cur.fetchall()
        conn.commit()
    return sorted(float(r["v"]) for r in rows)


def despesas(uid):
    return _valores("select valor v from launches where user_id=%s and tipo='despesa'", uid)


def no_cartao(uid):
    return _valores("select valor v from credit_transactions where user_id=%s", uid)


def contas_pagas(uid):
    return _valores("select paid_amount v from bill_instances where user_id=%s and status='paid'", uid)


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setattr(fp, "regra_ativa", lambda u: False)  # sem banco conectado (Q40)
    return usuario_pagante()


def luz(uid):
    return B.create_boleto(uid, "Luz", 150.0, today_tz(), category="moradia")


def nubank(uid):
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(uid, cid)
    return cid


# ── conta a pagar (roteador, ramo launches.add) ──────────────────────────────

@pytest.mark.parametrize("frase,pago,avisos", [
    (f"paguei a luz e {_ANO}", 150.0, 1),                          # C1
    ("Paguei a Luz e gastei mais que 5 mil esse mês?", 150.0, 1),  # C2
    (f"paguei a luz 120 e {_ANO}", 120.0, 1),                      # C3
    ("paguei a luz", 150.0, 0),                                    # positivo
    ("paguei a luz 120", 120.0, 0),                                # positivo
])
def test_conta_paga_pelo_pedaco_legitimo(uid, ia_fora, frase, pago, avisos):
    luz(uid)
    r = manda(uid, frase)
    assert contas_pagas(uid) == [pago] and despesas(uid) == [pago], r
    assert r.count(_AVISO) == avisos and _DEPOIS not in r, r  # nada armado: aviso simples


def test_conta_variavel_pergunta_o_valor_e_aceita_a_resposta(uid, ia_fora):  # C4
    _monta_conta_variavel(uid)
    r = manda(uid, f"paguei a luz e {_ANO}")
    assert "Quanto veio este mês?" in r and r.count(_AVISO) == 1 and despesas(uid) == [], r
    assert _DEPOIS in r, r
    r = manda(uid, "132,50")
    assert contas_pagas(uid) == [132.5] and despesas(uid) == [132.5], r


def test_com_banco_a_pergunta_da_forma_nao_leva_o_ano(ia_fora):  # C5
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    conta = luz(uid)
    r = manda(uid, f"paguei a luz e {_ANO}")
    pend = db.get_pending_action(uid)
    assert pend["action_type"] == "payment_method_choice", (r, pend)
    assert pend["payload"]["amount"] is None and r.count(_AVISO) == 1 and _DEPOIS in r, (r, pend)
    manda(uid, "pix")
    b = B.get_bill(uid, conta["id"])
    assert b["status"] == "paid" and b["paid_amount"] is None and despesas(uid) == []


def test_dois_assuntos_mercado_e_depois_a_conta(uid, ia_fora):  # C6
    luz(uid)
    manda(uid, "gastei 50 no mercado")
    manda(uid, f"paguei a luz e {_ANO}")
    assert despesas(uid) == [50.0, 150.0] and contas_pagas(uid) == [150.0]


def test_sem_conta_pendente_um_aviso_so(uid, ia_fora):  # C7
    r = manda(uid, f"paguei a luz e {_ANO}")
    assert "Faltou o valor de *luz*" in r and r.count(_AVISO) == 1 and despesas(uid) == [], r


# ── compra no crédito (origem: try_handle_natural_credit_purchase) ───────────

@pytest.mark.parametrize("frase,cartao,trecho,avisos", [
    (f"comprei um tenis no credito e {_ANO}", [], "Não achei o valor", 1),               # K1
    ("comprei um tênis de 300 no crédito, gastei mais em 2025 ou 2026?", [300.0], "", 1),  # K2
    ("gastei no cartao e gastei mais que 5 mil esse mes?", [], "Não achei o valor", 1),  # K3
    ("comprei tenis 300 no credito", [300.0], "", 0),                                    # positivo
    ("gastei 120 no cartao nubank", [120.0], "", 0),                                     # positivo
    ("comprei tenis no credito", [], "Não achei o valor", 0),                            # positivo
])
def test_compra_no_credito_pelo_pedaco_legitimo(uid, ia_fora, frase, cartao, trecho, avisos):
    nubank(uid)
    r = manda(uid, frase)
    assert no_cartao(uid) == cartao and despesas(uid) == [], r
    assert trecho in r and r.count(_AVISO) == avisos, r


def test_compra_sem_interrogacao_descricao_sem_a_pergunta(uid, ia_fora):  # K4
    nubank(uid)
    r = manda(uid, "gastei 300 no cartao nubank e gastei mais q no mes passado")
    assert no_cartao(uid) == [300.0] and r.count(_AVISO) == 1, r
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(nota,'') n from credit_transactions where user_id=%s", (uid,))
        nota = cur.fetchone()["n"]
    assert "passado" not in nota, nota


def test_dois_assuntos_mercado_e_depois_o_cartao(uid, ia_fora):  # K5
    # Tênis SEM valor: na main o 2025 da pergunta virava a compra.
    nubank(uid)
    manda(uid, "gastei 50 no mercado")
    r = manda(uid, f"comprei um tenis no credito e {_ANO}")
    assert despesas(uid) == [50.0] and no_cartao(uid) == [] and "Não achei o valor" in r, r


@pytest.mark.parametrize("frase", [
    f"comprei um tenis no credito e {_ANO}",  # K6
    "gastei mais no cartao em 2025 ou 2026?",  # tudo é pergunta: a main gravava R$ 2.025
    "gastei mais no cartao em 2025 e 2026?",   # nenhum pedaço é pergunta, a frase é (fallback)
])
def test_add_direto_nao_grava_e_avisa_uma_vez(uid, frase):
    from core.handlers import launches as h_launches
    nubank(uid)
    r = h_launches.add(uid, frase, {})
    assert no_cartao(uid) == [] and despesas(uid) == [] and r.count(_AVISO) == 1, r


@pytest.mark.parametrize("frase,buscado", [
    (f"comprei um tenis no credito e {_ANO}", []),
    (f"comprei um tenis 300 no credito e {_ANO}", [300.0]),
])
def test_com_banco_o_extrato_busca_o_valor_da_compra(monkeypatch, ia_fora, frase, buscado):  # K7
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    buscas = []
    monkeypatch.setattr(fp, "buscar_no_extrato", lambda u, t, v: buscas.append(v) or [])
    r = manda(uid, frase)
    assert buscas == buscado and r.count(_AVISO) == 1, r


# ── fatura (D3 = A) ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("fatura", [200, 3000])
@pytest.mark.parametrize("frase,avisos", [
    (f"paguei a fatura do nubank e {_ANO}", 1),  # F1
    ("paguei a fatura do nubank", 0),            # positivo
])
def test_fatura_paga_o_valor_em_aberto(uid, ia_fora, frase, avisos, fatura):
    db.add_launch_and_update_balance(uid, "receita", 5000, None, "seed")
    cid = nubank(uid)
    db.add_credit_purchase(uid, cid, fatura, "outros", "compra teste", today_tz())
    r = manda(uid, frase)
    assert despesas(uid) == [float(fatura)] and r.count(_AVISO) == avisos, r


@pytest.mark.parametrize("frase,pago,avisos", [
    # B4: com pergunta, a fatura só é paga com o cartão nomeado, como a conta.
    (f"paguei a fatura e {_ANO}", [], 1),
    (f"pagar a fatura e {_ANO}", [], 1),
    (f"paguei a fatura do nubank e {_ANO}", [3000.0], 1),  # positivo: cartão nomeado
    ("paguei a fatura", [3000.0], 0),                       # positivo: sem pergunta, o padrão
])
def test_fatura_com_pergunta_exige_o_cartao(uid, ia_fora, frase, pago, avisos):
    db.add_launch_and_update_balance(uid, "receita", 5000, None, "seed")
    cid = nubank(uid)
    db.add_credit_purchase(uid, cid, 3000, "outros", "compra teste", today_tz())
    r = manda(uid, frase)
    assert despesas(uid) == pago and r.count(_AVISO) == avisos, r


def test_fatura_com_duas_abertas_o_aviso_manda_responder_antes(uid, ia_fora):
    from datetime import timedelta
    cid = nubank(uid)
    db.add_credit_purchase(uid, cid, 200, "outros", "atual", today_tz())
    db.add_credit_purchase(uid, cid, 3000, "outros", "anterior", today_tz() - timedelta(days=40))
    r = manda(uid, f"paguei a fatura do nubank e {_ANO}")
    assert db.get_pending_action(uid)["action_type"] == "pay_bill_choice", r
    assert despesas(uid) == [] and r.count(_AVISO) == 1 and _DEPOIS in r, r


# ── D1 = A: o "cartão" só na pergunta não faz a compra ir para o cartão ──────

@pytest.mark.parametrize("frase", [
    "gastei 50 no mercado e gastei mais no cartao esse mes ou no passado?",
    "gastei 50 no mercado, gastei mais no cartao que no mes passado?",
    "gastei 50 no mercado e gastei mais no cartão em 2025 ou 2026?",
])
def test_cartao_so_na_pergunta_vira_despesa_comum(uid, ia_fora, frase):
    nubank(uid)
    r = manda(uid, frase)
    assert despesas(uid) == [50.0] and no_cartao(uid) == [] and r.count(_AVISO) == 1, r


def test_cartao_so_na_pergunta_com_banco_pergunta_a_forma(ia_fora):
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    nubank(uid)  # cartão manual: a main gravava os R$ 50 nele
    r = manda(uid, "gastei 50 no mercado e gastei mais no cartao esse mes ou no passado?")
    assert no_cartao(uid) == [] and despesas(uid) == [], r
    assert db.get_pending_action(uid)["action_type"] == "payment_method_choice", r


def test_luz_com_cartao_so_na_pergunta_paga_a_luz(uid, ia_fora):
    luz(uid)
    nubank(uid)
    r = manda(uid, "paguei a luz e gastei mais no cartão esse mês ou no passado?")
    assert contas_pagas(uid) == [150.0] and no_cartao(uid) == [] and r.count(_AVISO) == 1, r


def test_cartao_no_pedaco_legitimo_continua_no_cartao(uid, ia_fora):  # positivo
    nubank(uid)
    r = manda(uid, f"comprei tenis 300 no credito e {_ANO}")
    assert no_cartao(uid) == [300.0] and despesas(uid) == [] and r.count(_AVISO) == 1, r
    # Depois da compra fica a OFERTA "Apagar": ela não é pergunta, o aviso é o simples.
    assert db.get_pending_action(uid)["action_type"] == "delete_credit_purchase", r
    assert _DEPOIS not in r, r


@pytest.mark.parametrize("frase,valor", [
    # R5-1: "débito" no pedaço, "crédito" só na pergunta: é gasto da conta.
    ("paguei 80 no cartao de debito e gastei mais no credito esse mes?", 80.0),
    ("gastei 50 no cartao de debito e gastei mais no credito esse mes ou no passado?", 50.0),
    ("gastei 50 no cartao de debito e gastei mais no credito em 2025 ou 2026?", 50.0),
])
def test_debito_com_credito_so_na_pergunta_vira_despesa(uid, ia_fora, frase, valor):
    nubank(uid)
    r = manda(uid, frase)
    assert despesas(uid) == [valor] and no_cartao(uid) == [] and r.count(_AVISO) == 1, r


def test_debito_com_credito_so_na_pergunta_com_banco_nao_registra(ia_fora):
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    nubank(uid)
    r = manda(uid, "gastei 50 no cartao de debito e gastei mais no credito esse mes ou no passado?")
    assert "Não registrei: com banco conectado" in r and no_cartao(uid) == [] and despesas(uid) == [], r


def test_add_direto_com_cartao_so_na_pergunta_vira_despesa(uid):
    # O portão da compra no crédito lê o pedaço sem a pergunta: `add()` é chamado
    # também pela entrada rápida e pela resposta da forma, sem passar no roteador.
    from core.handlers import launches as h_launches
    nubank(uid)
    r = h_launches.add(uid, "gastei 50 no mercado e gastei mais no cartao esse mes ou no passado?", {})
    assert despesas(uid) == [50.0] and no_cartao(uid) == [] and r.count(_AVISO) == 1, r


def test_intent_de_cartao_sem_pergunta_nao_e_desviado(uid):
    # `limpo != text`: sem pergunta, o intent que chegou decide, mesmo quando as
    # regras leriam `launches.add` (a IA da `clarification` pode mandar credit.handle).
    import core.intent_router as IR
    from core.intent_classifier import classify
    assert classify("paguei 50", allow_ai=False).intent == "launches.add"
    db.add_launch_and_update_balance(uid, "receita", 5000, None, "seed")
    cid = nubank(uid)
    db.add_credit_purchase(uid, cid, 300, "outros", "compra teste", today_tz())
    r = IR._execute("credit.handle", uid, "paguei 50", {}, "whatsapp", None, None)
    assert "Pagamento registrado" in r and despesas(uid) == [50.0], r


# ── paridade do áudio (o caminho não muda: divide antes do route) ────────────
# No áudio, "paguei a luz" dentro do multi pergunta o valor em vez de quitar a
# conta ("Faltou o valor de *luz*"), igual à main. Aqui só se mede o 2025.

@pytest.mark.parametrize("fala,conta,cartao", [
    ("paguei a luz e gastei mais em 2025 ou 2026", [], []),
    ("comprei um tenis 300 no credito e gastei mais em 2025 ou 2026", [], [300.0]),
])
def test_audio_mesmo_resultado(uid, monkeypatch, fala, conta, cartao):
    luz(uid)
    nubank(uid)
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: fala)
    out = hi._handle_audio(IncomingMessage(platform="whatsapp", user_id=uid, text="",
                                           message_id="m", attachments=[_Audio()],
                                           external_id="e", raw={}), "whatsapp")
    corpo = "\n".join(o.text for o in out)
    assert contas_pagas(uid) == conta and no_cartao(uid) == cartao and despesas(uid) == [], corpo
    assert corpo.count(_AVISO) == 1, corpo


# ── forma_pagamento: o filtro do "no cartão" continua necessário ─────────────

def test_resposta_cartao_nao_busca_a_receita_comparativa(monkeypatch, ia_fora):
    # "recebi mais que 5 mil esse mes? no cartão" não é compra: a guarda do
    # crédito não o vê, e o add(forma=BANCO) buscaria R$ 5.000 no extrato.
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    buscas = []
    monkeypatch.setattr(fp, "buscar_no_extrato", lambda u, t, v: buscas.append((t, v)) or [])
    manda(uid, "gastei 50 no mercado e recebi mais que 5 mil esse mes?")
    r = manda(uid, "cartão")
    assert buscas == [("despesa", 50.0)] and r.count(_AVISO) == 1, r


# ── unitário: coerente com contains_comparative_question ────────────────────

_SEM_PERGUNTA = ["paguei a luz", "paguei a luz 120", "gastei 50 no mercado e 30 no uber",
                 "comprei tenis 300 no credito", ""]


@pytest.mark.parametrize("t", _PERGUNTAS + [t for t, _ in _ACHADO] + _SEM_PERGUNTA + [
    f"paguei a luz e {_ANO}", f"comprei um tenis no credito e {_ANO}",
    "paguei a luz. gastei mais em 2025 ou 2026?"])
def test_sem_perguntas_comparativas_concorda_com_contains(t):
    resto, puladas = sem_perguntas_comparativas(t)
    assert bool(puladas) == contains_comparative_question(t)
    if not puladas:
        assert resto == t


@pytest.mark.parametrize("t,esperado", [
    (f"paguei a luz e {_ANO}", ("paguei a luz", [_ANO])),
    (_ANO, ("", [_ANO])),
    ("gastei mais no cartao em 2025 e 2026?", ("", ["gastei mais no cartao em 2025 e 2026?"])),
    (f"gastei 30 no uber e gastei 50 no bar e {_ANO}", ("gastei 30 no uber e gastei 50 no bar", [_ANO])),
    ("paguei a luz", ("paguei a luz", [])),
])
def test_sem_perguntas_comparativas_tabela(t, esperado):
    assert sem_perguntas_comparativas(t) == esperado
