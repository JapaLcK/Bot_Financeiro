"""Reconciliação da saga (§10.1) — **expurgo por CONFIRMAÇÃO, nunca por relógio**.

A regra que este arquivo inteiro existe para prender:

> A idade decide QUANDO reconciliar. Só a reconciliação decide o que apagar.

Idade não é prova. Um `draft` velho pode ter ganhado id remoto num POST cuja
resposta se perdeu, e apagá-lo cria dinheiro sem linha nossa — que é o caso que
`orphan_unknown` só consegue chorar depois.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * reponha "apaga `draft` com mais de 15 min" (idade decidindo o desfecho) →
    `test_cobranca_viva_no_asaas_e_anexada` e
    `test_consulta_que_falha_nao_decide_nada` VERMELHOS. O primeiro é dinheiro
    sem linha;
  * deixe `creating` cair na regra (b) → `test_creating_nunca_e_apagada` vermelho;
  * faça `buscar_por_external_reference` devolver `[]` quando a resposta vier
    malformada (a versão que o Codex reprovou no #304) →
    `test_consulta_que_falha_nao_decide_nada` vermelho, e é o caminho exato:
    POST efetiva, resposta se perde, GET malformado, linha apagada com uma
    cobrança pagável viva.

POSITIVO do grupo: `test_draft_recente_nao_e_tocada`. Sem ele, uma varredura que
não faz nada com ninguém passaria em todos os negativos.
"""
from __future__ import annotations

import uuid

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from core.services import pix_sweeps
from core.services.asaas import AsaasApiError
from core.services.pix_sweeps import RECONCILIAR_APOS_MIN, reconciliar_saga
from db.connection import get_conn
from db.pix_charges import criar_cobranca


@pytest.fixture(autouse=True)
def _so_a_minha_linha():
    """A varredura é GLOBAL — ela não recebe `user_id`, e é o certo (a saga é da
    cobrança, não do dono). Num banco compartilhado com os outros arquivos isso
    faz a contagem por desfecho medir o lixo de quem rodou antes.

    Limpar `draft`/`creating` antes de cada caso é o que devolve sentido à
    contagem. **Não é `TRUNCATE`**: cobrança `pending`/`paid` de outro teste
    continua lá, e é ela que prova que a varredura não a toca.
    """
    garantir_system_event_logs()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from pix_charges where status in ('draft', 'creating')")
        conn.commit()


def _cobranca(uid: int, status: str, idade_min: int) -> dict:
    """Cobrança no estado e na idade pedidos. `created_at` é escrito à mão
    porque a idade é o gatilho da varredura — esperar 15 minutos num teste é
    como um teste de retenção vira teste que ninguém roda."""
    linha = criar_cobranca(uid, public_token=uuid.uuid4().hex, plan="pro_max",
                           plan_stored="pro_max", price_cents=49900,
                           credit_cents=0, amount_cents=49900, duration_days=365)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set status = %s,"
                    "       created_at = now() - make_interval(mins => %s)"
                    " where id = %s", (status, idade_min, linha["id"]))
        conn.commit()
    return linha


def _ler(charge_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_charges where id = %s", (charge_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _asaas(monkeypatch, resposta, qr="000201-reconciliado"):
    """Troca a consulta remota. `resposta` é lista, ou uma exceção a levantar.

    O `obter_qr_pix` vem junto porque o attach da varredura busca o QR: sem ele o
    teste sairia para a rede. `qr=None` faz a busca levantar, que é o cenário
    "achei a cobrança e não consegui o instrumento de pagamento".
    """
    def _consulta(ref):
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def _qr(pid):
        if qr is None:
            raise AsaasApiError("sem payload", status_code=None)
        return {"payload": f"{qr}-{pid}"}

    monkeypatch.setattr(pix_sweeps.asaas, "buscar_por_external_reference", _consulta)
    monkeypatch.setattr(pix_sweeps.asaas, "obter_qr_pix", _qr)


VELHA = RECONCILIAR_APOS_MIN + 5


# ── (a) o Asaas MOSTRA a cobrança → a saga fecha ─────────────────────────────

def test_cobranca_viva_no_asaas_e_anexada(user_id, monkeypatch):
    """DISCRIMINA. É o caso "o POST efetivou e a resposta se perdeu".

    A linha vai a `pending` com o id remoto — e **não** é apagada. Sob a regra
    por relógio ela sumiria e o cliente pagaria um QR sem linha nossa.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "creating", VELHA)
    _asaas(monkeypatch, [{"id": "pay_remoto_1", "status": "PENDING"}])

    assert reconciliar_saga()["anexadas"] == 1
    depois = _ler(linha["id"])
    assert depois["status"] == "pending"
    assert depois["asaas_payment_id"] == "pay_remoto_1"
    # O QR vem JUNTO: `pending` sem payload é cobrança impagável e
    # insubstituível — o checkout do mesmo plano cai no reuso e devolve 503,
    # e `pending` já não volta para esta varredura.
    assert depois["qr_payload_enc"] is not None


def test_attach_sem_qr_nao_move_a_linha_para_pending(user_id, monkeypatch):
    """DISCRIMINA o par do caso acima: QR que não vem **não** fecha a saga.

    Anexar só o id deixaria a cobrança em `pending` sem instrumento de pagamento
    — o beco sem saída. Levantando ali, a linha fica `creating` e a passada
    seguinte tenta de novo.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "creating", VELHA)
    _asaas(monkeypatch, [{"id": "pay_remoto_2", "status": "PENDING"}], qr=None)

    assert reconciliar_saga()["indefinidas"] == 1
    depois = _ler(linha["id"])
    assert depois["status"] == "creating"
    assert depois["asaas_payment_id"] is None


def test_cobranca_morta_no_asaas_nao_vira_pending(user_id, monkeypatch):
    """`DELETED`/`REFUNDED` lá NÃO é attach: a linha iria a `pending` com um id
    que nunca vai pagar, e o cliente ficaria olhando um QR inútil.

    Também não é "nunca existiu" — a lista veio cheia —, então a regra (b) não
    se aplica e a linha continua na base.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "creating", VELHA)
    _asaas(monkeypatch, [{"id": "pay_morto", "status": "DELETED"}])

    reconciliar_saga()
    depois = _ler(linha["id"])
    assert depois is not None and depois["status"] == "draft"
    assert depois["asaas_payment_id"] is None


# ── (b) lista VAZIA → e só então apaga ───────────────────────────────────────

def test_draft_sem_cobranca_remota_e_apagada(user_id, monkeypatch):
    """A regra (b) inteira: `asaas_payment_id is null` **e** lista vazia.

    A segunda metade está no `where` de `apagar_cobranca`, no BANCO — não é
    precondição em Python, porque entre a consulta e o delete cabe um attach.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "draft", VELHA)
    _asaas(monkeypatch, [])

    assert reconciliar_saga()["apagadas"] == 1
    assert _ler(linha["id"]) is None


def test_creating_nunca_e_apagada(user_id, monkeypatch):
    """DISCRIMINA. `creating` é o estado AMBÍGUO por definição — o POST pode ter
    efetivado. Com lista vazia ela volta a `draft`, e é a passada SEGUINTE que
    cai na regra (b), depois de uma segunda confirmação.

    Duas consultas ao Asaas antes de apagar não é zelo: é a diferença entre uma
    resposta e uma confirmação.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "creating", VELHA)
    _asaas(monkeypatch, [])

    assert reconciliar_saga()["voltaram"] == 1
    assert _ler(linha["id"])["status"] == "draft", "creating foi apagada por idade"

    assert reconciliar_saga()["apagadas"] == 1
    assert _ler(linha["id"]) is None


# ── (c) "não sei" nunca decide ───────────────────────────────────────────────

@pytest.mark.parametrize("falha", [
    AsaasApiError("provedor fora", status_code=503),
    AsaasApiError("resposta sem `data`", status_code=None),
    RuntimeError("timeout de transporte"),
])
def test_consulta_que_falha_nao_decide_nada(user_id, monkeypatch, falha):
    """DISCRIMINA, e é o caminho que o Codex reprovou no #304.

    Indisponibilidade do provedor não é evidência de inexistência, e resposta
    2xx malformada tampouco — as duas levantam `AsaasApiError`, e as duas
    proíbem exatamente a mesma ação. A linha fica **como está**; a passada
    seguinte tenta de novo.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "draft", VELHA)
    _asaas(monkeypatch, falha)

    assert reconciliar_saga()["indefinidas"] == 1
    assert _ler(linha["id"]) is not None, "apagou a linha sem saber se ela existe lá"
    assert _ler(linha["id"])["status"] == "draft"


# ── o POSITIVO: a idade decide QUANDO, e antes dela ninguém é tocado ─────────

def test_draft_recente_nao_e_tocada(user_id, monkeypatch):
    """POSITIVO. Cobrança recém-criada é o checkout EM VOO — o cliente ainda
    está com o modal aberto. Tocá-la é apagar a venda debaixo dele.

    Sem este caso, uma varredura que apaga tudo passaria em todos os negativos.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "draft", 1)
    chamou = []
    monkeypatch.setattr(pix_sweeps.asaas, "buscar_por_external_reference",
                        lambda ref: chamou.append(ref) or [])

    assert reconciliar_saga() == {"anexadas": 0, "voltaram": 0, "apagadas": 0,
                                  "indefinidas": 0}
    assert chamou == [], "consultou o Asaas por uma cobrança de 1 minuto"
    assert _ler(linha["id"]) is not None


def test_pending_nao_entra_na_varredura(user_id, monkeypatch):
    """`pending` é cobrança EMITIDA e pagável — a saga já fechou. Ela só sai
    pelo cancelamento aos 60 dias (adiado, issue #329), nunca por esta passada.

    *Negativo: acrescente `pending` ao `where` de `listar_para_reconciliar` →
    este teste fica vermelho, e é um QR vivo sendo mexido pela varredura.*
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id, "pending", VELHA)
    chamou = []
    monkeypatch.setattr(pix_sweeps.asaas, "buscar_por_external_reference",
                        lambda ref: chamou.append(ref) or [])

    reconciliar_saga()
    assert chamou == []
    assert _ler(linha["id"])["status"] == "pending"


def test_drenar_pendentes_recupera_o_que_o_background_task_perdeu(user_id, monkeypatch):
    """A outra metade do laço de 60 s: o dreno de recuperação.

    O `background_tasks` do handler só roda no processo que atendeu a
    requisição. Evento que chegou enquanto o worker morria fica na outbox, e é
    esta passada que o encontra.

    *Negativo: filtre por `processed_at is not null` → zero eventos drenados
    para sempre, e todo pagamento fica sem acesso até alguém reparar à mão.*
    """
    from db.webhook_outbox import marcar_processado, registrar_evento

    aberto = f"evt_{uuid.uuid4().hex[:12]}"
    fechado = f"evt_{uuid.uuid4().hex[:12]}"
    for eid in (aberto, fechado):
        registrar_evento(eid, "PAYMENT_RECEIVED", {"id": eid, "payment": {}}, 1)
    marcar_processado(fechado)

    drenados = []
    import core.services.pix_drain as dreno
    monkeypatch.setattr(dreno, "drenar_evento", lambda eid: drenados.append(eid))
    pix_sweeps.drenar_pendentes()
    assert aberto in drenados
    assert fechado not in drenados, "drenou evento já concluído"
