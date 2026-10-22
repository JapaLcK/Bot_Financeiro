"""#1228 — pagamento parcial NÃO libera o plano. Banco real, pelo dreno inteiro.

Cada caso grava na outbox e roda `drenar_evento` (`entregar`); só o mundo lá fora
(GA4, Meta, e-mail, alerta) é contador. Um caso passa pela rota
`/billing/asaas/webhook` + `drenar_pendentes`.

A. retenção (o conserto): 0,01 / 498,99 / metade / só o plano com cadernos → fica
   `pending`, sem grant, sem cadernos, 1 alerta, evento fechado.
B. caminho legítimo (POSITIVO do conserto): exato, int, com cadernos, crédito de
   upgrade exato e excesso continuam concedendo os 6 efeitos.
C. valor ilegível: retém + alerta e FECHA o evento (Infinity/1e308 não prendem mais).
D. dedupe do alerta por pagamento e por lado (retido / concedido); alerta sai
   ANTES do marcador; retido e depois completado (P→E, P→X) avisa o operador.
E. estados x eventos (tabela da seção 3 do plano).
F. isolamento por usuário.
G. funções puras.

CONTROLES NEGATIVOS (Python, não sed; relato do PR): `conferir_valor` →
`return True` deixa A e C vermelhos; remover o marcador deixa D vermelho; `>=` por
`>` deixa B vermelho; tolerância 1 deixa o 498,99 de A vermelho; tirar o `not exists`
do passo 1 do runbook (docs/CLAUDE.md) deixa d1 e i4 vermelhos; tirar o `charge_id` da
chave dele deixa i6 vermelho; voltar a coluna `completado_pelo_mesmo_pagamento` para a
cobrança da linha deixa i6 vermelho.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import (
    SEM_VALOR,
    efeitos,
    entregar,
    evento,
    ler,
    mundo_externo,
    nova_cobranca,
)
from core.services.pix_valor import centavos_do_valor, valor_pago_cobre_devido
from db.connection import get_conn
from db.plan_grants import list_grants

COMPRA = {"stripe_cancel", "grant", "ebook", "ga4", "capi", "email"}
EXTRAS = [
    {"price": "price_cad_a", "url": "https://drive.test/a", "nome": "Caderno A", "valor_cents": 1990},
    {"price": "price_cad_b", "url": "https://drive.test/b", "nome": "Caderno B", "valor_cents": 2990},
]


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _pix(uid):
    return [g for g in list_grants(uid) if g["source"] == "pix"]


def _cadernos(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from ebook_entregas where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def _varredura(monkeypatch, *ids):
    """`drenar_pendentes` só dos eventos DESTE teste: a outbox é global e carrega
    eventos abertos de outros testes, que somariam alertas aqui."""
    import db.webhook_outbox as outbox

    real = outbox.eventos_pendentes
    monkeypatch.setattr(outbox, "eventos_pendentes",
                        lambda limite=100: [e for e in real(limite) if e in ids])


def _retida(uid, cob, event_id, externo, *, alertas=1):
    """O desfecho do parcial, afirmado inteiro."""
    linha = ler(cob["id"])
    assert linha["status"] == "pending"
    assert linha["paid_at"] is None and linha["access_starts_at"] is None
    assert linha["access_expires_at"] is None
    assert _pix(uid) == [] and _cadernos(uid) == 0
    assert efeitos(cob["asaas_payment_id"]) == {"valor_alertado"}
    assert (externo["email"], externo["ga4"], externo["capi"]) == (0, 0, 0)
    assert len(externo["alerta"]) == alertas, externo["alerta"]
    ev = evento(event_id)
    assert ev["processed_at"] is not None and ev["attempts"] == 0 and ev["last_error"] is None


def _logs(tipo, uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select details from system_event_logs where event_type = %s"
                    " and user_id = %s order by id", (tipo, uid))
        return [r["details"] for r in cur.fetchall()]


def _concedida(uid, cob, externo):
    assert ler(cob["id"])["status"] == "paid"
    assert len(_pix(uid)) == 1
    assert efeitos(cob["asaas_payment_id"]) >= COMPRA


# ── A. retenção ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", [0.01, 498.99, 249.50, 0.0])
def test_a1_parcial_nao_libera_o_plano(user_id, externo, valor):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=valor)
    _retida(user_id, cob, ev, externo)
    assert "pagamento parcial retido" in externo["alerta"][0]


def test_a2_plano_com_cadernos_pagando_so_o_plano(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=EXTRAS)  # devido: 548,80
    ev = entregar("PAYMENT_RECEIVED", cob, valor=499.00)
    _retida(user_id, cob, ev, externo)


def test_a3_confirmed_tem_a_mesma_regra(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    _retida(user_id, cob, entregar("PAYMENT_CONFIRMED", cob, valor=0.01), externo)


def test_a4_registra_o_valor_pago_em_centavos(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select details from system_event_logs where event_type = %s"
                    " and user_id = %s", ("pix_pagamento_parcial_retido", user_id))
        d = cur.fetchone()["details"]
    assert (d["pago_cents"], d["devido_cents"], d["charge_id"]) == (1, 49900, cob["id"])


# ── B. caminho legítimo (positivo) ───────────────────────────────────────────

@pytest.mark.parametrize("valor", [499.0, 499], ids=["float", "int"])
def test_b1_valor_exato_concede(user_id, externo, valor):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=valor)
    _concedida(user_id, cob, externo)
    assert efeitos(cob["asaas_payment_id"]) == COMPRA  # sem alerta de valor
    assert externo["alerta"] == [] and evento(ev)["processed_at"] is not None


def test_b2_exato_com_cadernos_concede(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=EXTRAS)
    entregar("PAYMENT_RECEIVED", cob, valor=548.80)
    _concedida(user_id, cob, externo)
    assert _cadernos(user_id) == 2 and externo["alerta"] == []


def test_b3_credito_de_upgrade_exato_concede(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, credit_cents=10000, amount_cents=39900)
    entregar("PAYMENT_RECEIVED", cob, valor=399.00)
    _concedida(user_id, cob, externo)
    assert externo["alerta"] == []


def test_b4_pagar_o_preco_cheio_do_upgrade_e_excesso(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, credit_cents=10000, amount_cents=39900)
    entregar("PAYMENT_RECEIVED", cob, valor=499.00)
    _concedida(user_id, cob, externo)
    assert len(externo["alerta"]) == 1 and "valor maior" in externo["alerta"][0]


def test_b5_excesso_concede_e_alerta_uma_vez(user_id, externo):
    """Excesso concede os 6 efeitos e alerta UMA vez. A segunda entrega NÃO mede o
    marcador: a cobrança já está `paid` e o check de valor é pulado (a dedupe do
    alerta é medida em D1 e D3, com a cobrança em `pending`)."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=520.0)
    entregar("PAYMENT_RECEIVED", cob, valor=520.0)  # event_id novo, mesmo pagamento
    _concedida(user_id, cob, externo)
    assert efeitos(cob["asaas_payment_id"]) == COMPRA | {"valor_concedido_alertado"}
    assert len(externo["alerta"]) == 1 and "valor maior" in externo["alerta"][0]


def test_b6_excesso_deixa_rastro_duravel(user_id, externo):
    """O excesso CONCEDE o plano; se o canal do admin falhar, o log é o único rastro."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=520.0)
    assert _logs("pix_excesso_recebido", user_id) == [
        {"charge_id": cob["id"], "pago_cents": 52000, "devido_cents": 49900}]


# ── C. valor ilegível ────────────────────────────────────────────────────────

ILEGIVEIS = [
    pytest.param(SEM_VALOR, id="ausente"), pytest.param(None, id="None"),
    pytest.param(float("nan"), id="NaN"), pytest.param(float("inf"), id="inf"),
    pytest.param(float("-inf"), id="-inf"), pytest.param(1e308, id="1e308"),
    pytest.param(-5.0, id="negativo"), pytest.param("499.00", id="string"),
    pytest.param("abc", id="texto"), pytest.param([], id="lista"),
    pytest.param(True, id="bool"), pytest.param(498.995, id="3-casas"),
]


@pytest.mark.parametrize("valor", ILEGIVEIS)
def test_c1_valor_ilegivel_retem_alerta_e_fecha_o_evento(user_id, externo, valor):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=valor)
    _retida(user_id, cob, ev, externo)
    assert "valor ilegível" in externo["alerta"][0]
    assert evento(ev)["last_error"] != "OverflowError"


# ── D. dedupe ────────────────────────────────────────────────────────────────

def test_d1_tres_entregas_do_mesmo_parcial_um_alerta(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    evs = [entregar("PAYMENT_RECEIVED", cob, valor=0.01) for _ in range(3)]
    assert len(set(evs)) == 3 and all(evento(e)["processed_at"] for e in evs)
    _retida(user_id, cob, evs[-1], externo, alertas=1)
    assert len(_logs("pix_pagamento_parcial_retido", user_id)) == 3  # rastro por entrega
    assert len(_runbook(user_id)) == 1  # tarefa do operador: uma por pagamento


def test_d2_alerta_sai_antes_do_marcador(user_id, externo, monkeypatch):
    """Se o registro falha, o aviso de dinheiro retido já saiu e o evento fica
    ABERTO (retentável); a retentativa fecha. Custo aceito: no máximo 1 alerta em
    dobro (o inverso perderia o aviso)."""
    import core.services.pix_valor as pv
    from core.services.pix_sweeps import drenar_pendentes

    real = pv.registrar_efeito
    falhas = []

    def _quebra_uma_vez(*a, **kw):
        if not falhas:
            falhas.append(1)
            raise RuntimeError("morreu entre o alerta e o registro")
        return real(*a, **kw)

    monkeypatch.setattr(pv, "registrar_efeito", _quebra_uma_vez)
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    assert len(externo["alerta"]) == 1
    assert evento(ev)["processed_at"] is None and evento(ev)["attempts"] == 1
    _varredura(monkeypatch, ev)
    assert drenar_pendentes() == 1
    assert evento(ev)["processed_at"] is not None
    assert _pix(user_id) == [] and ler(cob["id"])["status"] == "pending"
    assert len(externo["alerta"]) == 2  # o duplicado aceito


def test_d3_alerta_nao_entregue_nao_marca_e_tenta_de_novo(user_id, externo, monkeypatch):
    """`notify_pix_alerta` devolve False (webhook vazio/não-2xx) sem levantar: marcar
    assim silenciava o parcial para sempre. Falha: sem marcador, log durável, e o
    próximo evento tenta de novo; sucesso: marcador e UM alerta só."""
    import core.services.admin_notify as an

    tentativas = []
    entrega = {"ok": False}
    monkeypatch.setattr(an, "notify_pix_alerta",
                        lambda m: tentativas.append(m) or entrega["ok"])
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    e1 = entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    e2 = entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    assert len(tentativas) == 2 and efeitos(cob["asaas_payment_id"]) == set()
    assert len(_logs("pix_alerta_nao_entregue", user_id)) == 2
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select distinct level from system_event_logs where event_type = %s"
                    " and user_id = %s", ("pix_alerta_nao_entregue", user_id))
        assert [r["level"] for r in cur.fetchall()] == ["error"]
    assert evento(e1)["processed_at"] and evento(e2)["processed_at"]  # nunca preso
    entrega["ok"] = True
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    assert len(tentativas) == 3, "entrega confirmada não pode repetir"
    assert efeitos(cob["asaas_payment_id"]) == {"valor_alertado"}
    assert ler(cob["id"])["status"] == "pending" and _pix(user_id) == []


DOIS_LADOS = COMPRA | {"valor_alertado", "valor_concedido_alertado"}
RETIDOS = [pytest.param(0.01, id="parcial"), pytest.param("abc", id="ilegivel")]


@pytest.mark.parametrize("primeiro", RETIDOS)
def test_d4_retido_e_depois_excesso_alerta_os_dois_lados(user_id, externo, primeiro):
    """P/I → X no mesmo `payment.id` (baixa manual desfeita e refeita): o marcador
    do retido não pode calar o excesso, e o texto diz que o retido caducou."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=primeiro)
    entregar("PAYMENT_RECEIVED", cob, valor=520.0)
    _concedida(user_id, cob, externo)
    assert efeitos(cob["asaas_payment_id"]) == DOIS_LADOS
    assert len(externo["alerta"]) == 2, externo["alerta"]
    assert "parcial retido" in externo["alerta"][0]
    assert "valor maior" in externo["alerta"][1]
    assert "tinha sido retido" in externo["alerta"][1] and "R$ 21.00" in externo["alerta"][1]
    assert len(_logs("pix_retido_completado", user_id)) == 1
    assert len(_logs("pix_excesso_recebido", user_id)) == 1


@pytest.mark.parametrize("primeiro", RETIDOS)
def test_d5_retido_e_depois_exato_avisa_para_nao_devolver(user_id, externo, primeiro):
    """P/I → E: concede o plano; sem este alerta o "devolva o valor" do retido
    continuaria valendo para quem pagou certo."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=primeiro)
    entregar("PAYMENT_RECEIVED", cob, valor=499.0)
    _concedida(user_id, cob, externo)
    assert efeitos(cob["asaas_payment_id"]) == DOIS_LADOS
    assert len(externo["alerta"]) == 2, externo["alerta"]
    assert "antes RETIDO foi completado" in externo["alerta"][1]
    assert "NÃO devolva" in externo["alerta"][1]
    # rastro durável: o alerta deste lado não é retentado depois de `paid`
    assert _logs("pix_retido_completado", user_id) == [
        {"charge_id": cob["id"], "pago_cents": 49900, "devido_cents": 49900}]


def test_d6_excesso_nao_repete_o_alerta_quando_a_transicao_e_retentada(
        user_id, externo, monkeypatch):
    """O alerta do lado que concede sai ANTES da transição; se ela falha, a
    retentativa reavalia o valor. É o `valor_concedido_alertado` que a cala."""
    from psycopg.errors import CheckViolation

    import core.services.pix_drain as drain
    from core.services.pix_sweeps import drenar_pendentes

    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    real, falhas = drain.transicionar, []

    def _quebra_uma_vez(*a, **kw):
        if not falhas:
            falhas.append(1)
            raise CheckViolation("transição falhou uma vez")
        return real(*a, **kw)

    monkeypatch.setattr(drain, "transicionar", _quebra_uma_vez)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=520.0)
    assert evento(ev)["processed_at"] is None and evento(ev)["attempts"] == 1
    assert ler(cob["id"])["status"] == "pending"
    _varredura(monkeypatch, ev)
    assert drenar_pendentes() == 1
    assert evento(ev)["processed_at"] is not None
    _concedida(user_id, cob, externo)
    assert len(externo["alerta"]) == 2, externo["alerta"]
    assert sum("valor maior" in m for m in externo["alerta"]) == 1


# ── E. estados x eventos ─────────────────────────────────────────────────────

def _pular_sem_1227(estado):
    from core.services.pix_drain import _ORIGENS_DE_PAGAMENTO
    return pytest.param(estado, marks=pytest.mark.skipif(
        estado not in _ORIGENS_DE_PAGAMENTO, reason="origem de pagamento só depois do #1227"))


@pytest.mark.parametrize("estado", [_pular_sem_1227(e) for e in (
    "draft", "creating", "pending", "canceling", "canceled", "expired")])
def test_e1_parcial_retem_em_toda_origem_de_pagamento(user_id, externo, estado):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set status = %s where id = %s", (estado, cob["id"]))
        conn.commit()
    ev = entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    linha = ler(cob["id"])
    assert linha["status"] == estado and linha["paid_at"] is None
    assert _pix(user_id) == [] and _cadernos(user_id) == 0
    assert efeitos(cob["asaas_payment_id"]) == {"valor_alertado"}
    assert len(externo["alerta"]) == 1 and evento(ev)["processed_at"] is not None


def test_e2_cobranca_ja_paga_nao_gera_alerta_falso(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob)
    ev = entregar("PAYMENT_RECEIVED", cob, valor=0.01)  # reentrega com valor torto
    _concedida(user_id, cob, externo)
    assert externo["alerta"] == [] and evento(ev)["processed_at"] is not None


@pytest.mark.parametrize("terminal", ["PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED"])
def test_e3_estados_pos_estorno_nao_geram_alerta_de_parcial(user_id, externo, terminal):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob)
    entregar(terminal, cob)
    externo["alerta"].clear()
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    assert not any("parcial retido" in m for m in externo["alerta"])
    assert ler(cob["id"])["status"] == ("refunded" if terminal == "PAYMENT_REFUNDED" else "refunded_partial")


def test_e4_orfao_segue_sem_acesso_e_sem_alerta_de_parcial(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set user_id = null where id = %s", (cob["id"],))
        conn.commit()
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    assert ler(cob["id"])["status"] == "paid_orphan" and _pix(user_id) == []
    assert not any("parcial retido" in m for m in externo["alerta"])


def test_e5_parcial_depois_partially_refunded_nao_muda_nada(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    assert ler(cob["id"])["status"] == "pending" and _pix(user_id) == []


def test_e6_parcial_depois_refunded_fica_pending_e_received_tardio_nao_concede(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    entregar("PAYMENT_REFUNDED", cob)
    assert ler(cob["id"])["status"] == "pending" and _pix(user_id) == []
    entregar("PAYMENT_RECEIVED", cob)  # integral, mas o pagamento já foi estornado
    assert _pix(user_id) == []


def test_e7_parcial_depois_overdue_expira_e_integral_tardio_concede(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    entregar("PAYMENT_OVERDUE", cob)
    assert ler(cob["id"])["status"] == "expired" and _pix(user_id) == []
    entregar("PAYMENT_RECEIVED", cob)
    _concedida(user_id, cob, externo)


def test_e8_complemento_com_payment_id_novo_e_avaliado_sozinho(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=249.50)
    novo = f"pay_{uuid.uuid4().hex[:12]}"
    entregar("PAYMENT_RECEIVED", cob, payment_id=novo, valor=249.50)  # soma 499, mas NÃO soma
    assert ler(cob["id"])["status"] == "pending" and _pix(user_id) == []
    assert len(externo["alerta"]) == 2  # um por pagamento
    entregar("PAYMENT_RECEIVED", cob, payment_id=f"pay_{uuid.uuid4().hex[:12]}", valor=499.0)
    assert ler(cob["id"])["status"] == "paid" and len(_pix(user_id)) == 1


def _sql_do_runbook():
    """O SQL do passo 3, lido de `docs/CLAUDE.md`: o teste executa o que o operador copia."""
    import pathlib
    import re

    doc = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "CLAUDE.md").read_text()
    achado = re.search(r"update pix_charges set status = 'canceled'.*?;", doc, re.S)
    assert achado, "runbook do #1228 sumiu de docs/CLAUDE.md"
    return re.sub(r"\s+", " ", achado.group(0)).replace("<id>", "%s")


def _runbook(uid):
    """Passo 1 do runbook, extraído de `docs/CLAUDE.md` e executado; só as linhas de `uid`."""
    import pathlib
    import re

    doc = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "CLAUDE.md").read_text()
    achado = re.search(r"select created_at, user_id.*?;", doc, re.S)
    assert achado, "consulta do passo 1 sumiu de docs/CLAUDE.md"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(re.sub(r"\s+", " ", achado.group(0)))
        return [r for r in cur.fetchall() if r["user_id"] == uid]


def test_i2_consulta_do_runbook_traz_quem_atender(user_id, externo):
    """Precisa dizer de QUEM é a linha e se ela ainda está retida: completado depois
    pelo MESMO pagamento = NÃO devolver."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    mine = _runbook(user_id)
    assert len(mine) == 1 and mine[0]["details"]["charge_id"] == cob["id"]
    assert mine[0]["message"] and mine[0]["status_agora"] == "pending"
    assert mine[0]["completado_pelo_mesmo_pagamento"] is False
    entregar("PAYMENT_RECEIVED", cob, valor=499.0)  # completado no mesmo pagamento
    assert [(r["status_agora"], r["completado_pelo_mesmo_pagamento"])
            for r in _runbook(user_id)] == [("paid", True)]


def test_i3_cobranca_paga_por_outro_pagamento_nao_poupa_o_retido(user_id, externo):
    """Parcial em `pay_A`, integral em `pay_C` (casado pelo `externalReference`): a
    COBRANÇA fica `paid`, mas os R$ 49,90 de `pay_A` seguem no caixa. O runbook não
    pode mandar "NÃO devolva" para eles."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=49.90)
    entregar("PAYMENT_RECEIVED", cob, payment_id=f"pay_{uuid.uuid4().hex[:12]}", valor=499.0)
    assert ler(cob["id"])["status"] == "paid"
    assert [(r["status_agora"], r["completado_pelo_mesmo_pagamento"])
            for r in _runbook(user_id)] == [("paid", False)]
    assert not any("completado" in m for m in externo["alerta"])


def test_i4_runbook_mostra_o_valor_mais_recente_do_mesmo_pagamento(user_id, externo):
    """Baixa manual refeita com outro valor no MESMO pagamento: uma linha, a mais recente."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=100.0)
    entregar("PAYMENT_RECEIVED", cob, valor=200.0)
    assert [r["details"]["pago_cents"] for r in _runbook(user_id)] == [20000]


def test_i5_dois_pagamentos_parciais_continuam_duas_linhas(user_id, externo):
    """POSITIVO do dedupe: pagamentos distintos da mesma cobrança não se fundem."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=249.50)
    entregar("PAYMENT_RECEIVED", cob, payment_id=f"pay_{uuid.uuid4().hex[:12]}", valor=249.50)
    linhas = _runbook(user_id)
    assert len(linhas) == 2 and len({r["details"]["payment_id"] for r in linhas}) == 2


def test_i6_mesmo_pagamento_em_duas_cobrancas_nao_esconde_a_completada(user_id, externo):
    """`pay_Z` parcial em A (expired) e em B, depois completa A: a linha de A não pode
    sumir atrás da de B, e a de B também diz completado (o dinheiro virou plano em A)."""
    conta(user_id, "free", None)
    a = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:  # libera a recompra para criar B
        cur.execute("update pix_charges set status = 'expired' where id = %s", (a["id"],))
        conn.commit()
    b = nova_cobranca(user_id)
    a = ler(a["id"])
    pz = f"pay_{uuid.uuid4().hex[:12]}"
    entregar("PAYMENT_RECEIVED", a, payment_id=pz, valor=100.0)
    entregar("PAYMENT_RECEIVED", b, payment_id=pz, valor=100.0)
    entregar("PAYMENT_RECEIVED", a, payment_id=pz, valor=499.0)
    linhas = sorted((r["details"]["charge_id"], r["status_agora"],
                     r["completado_pelo_mesmo_pagamento"]) for r in _runbook(user_id))
    assert linhas == sorted([(a["id"], "paid", True), (b["id"], "pending", True)])


@pytest.mark.parametrize("estado", ["pending", "canceling"])
def test_i1_runbook_fecha_a_linha_e_libera_a_recompra(user_id, externo, monkeypatch, estado):
    """`canceling` é o que sobra quando o cliente pede OUTRO plano: o Asaas recusa o
    `DELETE` de pagamento recebido e `_cancelar_remota` deixa a linha ali."""
    import secrets

    from core.services import asaas
    from core.services.pix_checkout import CheckoutIndisponivel, _cancelar_remota
    from db.pix_charges import criar_cobranca

    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob, valor=0.01)
    campos = dict(public_token=secrets.token_urlsafe(16), plan="pro_max", plan_stored="pro_max",
                  price_cents=49900, credit_cents=0, amount_cents=49900, duration_days=365)
    if estado == "canceling":
        def _recusa(_id):
            raise RuntimeError("Asaas: não é possível excluir cobrança recebida")
        monkeypatch.setattr(asaas, "deletar_pagamento", _recusa)
        with pytest.raises(CheckoutIndisponivel):
            _cancelar_remota(ler(cob["id"]))
    assert ler(cob["id"])["status"] == estado
    assert criar_cobranca(user_id, **campos) is None, "a linha retida deveria travar a recompra"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_sql_do_runbook(), (cob["id"],))
        assert cur.rowcount == 1
        conn.commit()
    assert ler(cob["id"])["status"] == "canceled"
    assert criar_cobranca(user_id, **campos), "o runbook não liberou a recompra"


@pytest.mark.parametrize("tipo,antes,depois", [
    ("PAYMENT_REFUNDED", "paid", "refunded"),
    ("PAYMENT_PARTIALLY_REFUNDED", "paid", "refunded_partial"),
    ("PAYMENT_OVERDUE", "pending", "expired"),
    ("PAYMENT_DELETED", "pending", "canceled"),
])
def test_e9_evento_sem_efeito_de_compra_ignora_o_valor(user_id, externo, tipo, antes, depois):
    """Estorno, vencimento e exclusão com `value` torto seguem como antes: o check de
    valor é só de PAGAMENTO. Sem o `if` de `EFEITOS_DE_COMPRA` no dreno, o REFUNDED
    de 0,01 seria "retido" e o acesso nunca seria revogado."""
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    if antes == "paid":
        entregar("PAYMENT_RECEIVED", cob)
    externo["alerta"].clear()
    ev = entregar(tipo, cob, valor=0.01)
    assert ler(cob["id"])["status"] == depois
    assert evento(ev)["processed_at"] is not None
    assert "valor_alertado" not in efeitos(cob["asaas_payment_id"])
    assert not any("parcial retido" in m for m in externo["alerta"])
    if tipo == "PAYMENT_REFUNDED":
        assert "revoke" in efeitos(cob["asaas_payment_id"])


# ── F. isolamento por usuário ────────────────────────────────────────────────

def test_f1_parcial_de_a_nao_toca_b(user_id, externo):
    from db import ensure_user

    b = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(b)
    conta(user_id, "free", None)
    conta(b, "free", None)
    cob_a = nova_cobranca(user_id)
    cob_b = nova_cobranca(b)
    entregar("PAYMENT_RECEIVED", cob_a, valor=0.01)
    entregar("PAYMENT_RECEIVED", cob_b)
    assert _pix(user_id) == [] and ler(cob_a["id"])["status"] == "pending"
    assert len(_pix(b)) == 1 and ler(cob_b["id"])["status"] == "paid"
    assert efeitos(cob_a["asaas_payment_id"]) == {"valor_alertado"}
    assert externo["email"] == 1 and len(externo["alerta"]) == 1
    alerta = externo["alerta"][0]
    for dado_de_b in (cob_b["asaas_payment_id"], cob_b["external_reference"], str(b)):
        assert dado_de_b not in alerta
    assert cob_a["external_reference"] not in efeitos(cob_b["asaas_payment_id"])


# ── pela rota ────────────────────────────────────────────────────────────────

def test_h1_webhook_real_e_dreno_pendentes(user_id, externo, monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard
    from core.services.pix_sweeps import drenar_pendentes

    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", "tok-1228")
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    client = TestClient(dashboard.app)

    def _post(valor):
        corpo = {"id": f"evt_{uuid.uuid4().hex[:12]}", "event": "PAYMENT_RECEIVED",
                 "payment": {"id": cob["asaas_payment_id"], "value": valor, "status": "RECEIVED",
                             "externalReference": cob["external_reference"]}}
        r = client.post("/billing/asaas/webhook", json=corpo,
                        headers={"asaas-access-token": "tok-1228"})
        assert r.status_code == 200
        return corpo["id"]

    primeiro = _post(0.01)
    segundo = _post(0.01)
    # o `background_tasks` da rota já drenou; a varredura de 60 s não acha mais nada
    _varredura(monkeypatch, primeiro, segundo)
    assert drenar_pendentes() == 0
    _retida(user_id, cob, primeiro, externo, alertas=1)
    assert evento(segundo)["processed_at"] is not None
    _post(499.0)
    _concedida(user_id, cob, externo)
    assert len(externo["alerta"]) == 2
    assert "antes RETIDO foi completado" in externo["alerta"][-1]


# ── G. funções puras ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor,esperado", [
    (499.0, 49900), (499, 49900), (499.9, 49990), (0.01, 1), (0, 0), (0.0, 0),
    (548.80, 54880), (1e15, 10**17),
    (498.995, None), (49899.5 / 100, None), (0.1 + 0.2, None), (1e308, None),
    (float("nan"), None), (float("inf"), None), (float("-inf"), None), (-0.01, None),
    (-5, None), (None, None), (True, None), (False, None), ("499.00", None),
    ("abc", None), ([], None), ({}, None), (b"1", None),
])
def test_g1_centavos_do_valor(valor, esperado):
    assert centavos_do_valor(valor) == esperado


@pytest.mark.parametrize("pago,devido,tol,esperado", [
    (49900, 49900, 0, True), (49901, 49900, 0, True), (49899, 49900, 0, False),
    (1, 49900, 0, False), (None, 49900, 0, False), (0, 49900, 0, False),
    (49899, 49900, 1, True), (49898, 49900, 1, False),
])
def test_g2_valor_pago_cobre_devido(pago, devido, tol, esperado):
    assert valor_pago_cobre_devido(pago, devido, tol) is esperado
    if tol == 0:  # o default é a tolerância ZERO do dono (D1)
        assert valor_pago_cobre_devido(pago, devido) is esperado
