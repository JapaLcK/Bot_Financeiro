"""A cobrança AMBÍGUA, o QR vencido e a data que a tela mostra (Codex, #330).

Terceiro arquivo do checkout, e a divisão é por assunto (o teto de 350 linhas de
`tests/test_max_lines_python.py` decidiu só a hora): `test_pix_checkout.py` tem a
venda nova e as recusas de configuração, `test_pix_checkout_substituicao.py` tem
o par reaproveitar × substituir no caminho normal, e aqui está o que acontece
quando o mundo local e o Asaas **discordam** — a linha `creating` que pode ter
cobrança pagável lá, o `pending` cujo QR já venceu, e a cobrança que só a
varredura conseguiu fechar.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde) — saídas no
relato do PR:

  * volte `_cancelar_remota` a pular o `DELETE` quando `asaas_payment_id` é nulo
    → `test_creating_ambigua_deleta_a_cobranca_remota` vermelho, e são duas
    cobranças pagáveis do mesmo dono contra o mesmo crédito;
  * faça a consulta que falha virar "não existe" →
    `test_consulta_que_falha_na_substituicao_nao_cria_nada` vermelho;
  * tire o `vencido` da condição de reuso → `test_qr_vencido_e_substituido`
    vermelho, devolvendo para sempre um código que nenhum banco aceita;
  * anexe sem QR na varredura → `test_cobranca_reconciliada_volta_com_qr`
    vermelho com 503 (`cobranca_ativa_sem_qr`);
  * devolva `linha["access_starts_at"]` cru → `test_starts_at_sai_na_resposta`
    vermelho com `None`, que é o que a tela do PR 2 recebia.

POSITIVO do grupo: `test_creating_sem_cobranca_remota_substitui_sem_delete` —
sem ele, uma versão que recusasse toda substituição de `creating` (ou que
deletasse sempre, chamando o Asaas com id nulo) passaria em todos os negativos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 - fixtures
    _comprar,
    _linhas,
    asaas_falso,
    vendavel,
)
from core.services.asaas import AsaasApiError
from core.services.pix_checkout import CheckoutIndisponivel
from db.connection import get_conn


def _emissao_que_morre_depois_do_post(uid: int, asaas_falso) -> dict:
    """Deixa uma linha em `creating` **sem** `asaas_payment_id` — o estado
    ambíguo de verdade: o POST efetivou lá e o QR (ou o attach) se perdeu aqui.

    É o cenário do §10, e não uma fabricação de teste: `_emitir` mantém a linha
    em `creating` de propósito para a varredura decidir.
    """
    asaas_falso["qr_falha"] = True
    with pytest.raises(CheckoutIndisponivel):
        _comprar(uid)
    asaas_falso["qr_falha"] = False
    linha = _linhas(uid)[-1]
    assert linha["status"] == "creating" and linha["asaas_payment_id"] is None
    return linha


def _envelhecer(charge_id: int, minutos: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set created_at = now()"
                    " - make_interval(mins => %s) where id = %s",
                    (int(minutos), int(charge_id)))
        conn.commit()


# ── a linha `creating` que pode ter cobrança pagável no Asaas ────────────────

def test_creating_ambigua_deleta_a_cobranca_remota(user_id, vendavel, asaas_falso):
    """DISCRIMINA. Coluna nula não é prova de que não há cobrança lá.

    A substituição só olhava `asaas_payment_id`: nulo, pulava o `DELETE`,
    cancelava a linha local e criava outra. A primeira continuava PAGÁVEL no
    Asaas — e o `PAYMENT_RECEIVED` aceita `canceled` como origem (§11), então o
    cliente podia pagar as duas e o §10 inteiro perdia o sentido.
    """
    conta(user_id, "free", None)
    ambigua = _emissao_que_morre_depois_do_post(user_id, asaas_falso)
    orfa = f"pay_{asaas_falso['marca']}_1"
    asaas_falso["remotas"] = [{"id": orfa, "status": "PENDING"}]
    asaas_falso["ordem"].clear()

    _comprar(user_id, "pro")

    assert f"delete:{orfa}" in asaas_falso["ordem"], asaas_falso["ordem"]
    assert asaas_falso["ordem"].index(f"delete:{orfa}") < \
        asaas_falso["ordem"].index("create"), "criou a nova antes de matar a órfã"
    linhas = _linhas(user_id)
    assert [l["status"] for l in linhas] == ["canceled", "pending"]
    assert linhas[0]["id"] == ambigua["id"]


def test_consulta_que_falha_na_substituicao_nao_cria_nada(
        user_id, vendavel, asaas_falso):
    """DISCRIMINA. "Não sei" não pode virar "não existe" num caminho que emite a
    SEGUNDA cobrança — é a mesma regra do §10.1, do outro lado.

    503, a linha fica em `canceling` (rastro para a varredura repetir o
    cancelamento) e **nenhuma** cobrança nova.
    """
    conta(user_id, "free", None)
    _emissao_que_morre_depois_do_post(user_id, asaas_falso)
    asaas_falso["remotas"] = AsaasApiError("provedor fora", status_code=503)

    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id, "pro")

    linhas = _linhas(user_id)
    assert len(linhas) == 1, "criou cobrança nova sem saber da órfã"
    assert linhas[0]["status"] == "canceling"


def test_creating_sem_cobranca_remota_substitui_sem_delete(
        user_id, vendavel, asaas_falso):
    """POSITIVO do grupo. Lista vazia é resposta: não há o que cancelar lá, e a
    venda segue.

    Sem este caso, recusar toda substituição de `creating` — ou chamar o
    `DELETE` com id nulo — passaria nos dois negativos acima.
    """
    conta(user_id, "free", None)
    _emissao_que_morre_depois_do_post(user_id, asaas_falso)
    asaas_falso["remotas"] = []
    asaas_falso["ordem"].clear()

    r = _comprar(user_id, "pro")

    assert not [p for p in asaas_falso["ordem"] if p.startswith("delete:")]
    # `plus` para um `plan_stored="pro"`: a RESPOSTA fala o público da /precos
    # desde o #350, e `pro` é o valor legado do tier Plus. O que a coluna guarda
    # continua legado — quem prende os dois lados é
    # `tests/test_vocabulario_de_plano_nas_saidas.py`.
    assert r["plan"] == "plus"
    assert [l["status"] for l in _linhas(user_id)] == ["canceled", "pending"]


# ── o QR que já venceu ──────────────────────────────────────────────────────

def test_qr_vencido_e_substituido(user_id, vendavel, asaas_falso):
    """DISCRIMINA. `pending` com `qr_expires_at` no passado não se reaproveita.

    O `PAYMENT_OVERDUE` pode atrasar ou se perder, e `pending` não volta para a
    reconciliação: devolver o mesmo código expirado deixava o cliente sem
    NENHUMA cobrança pagável, para sempre. A substituição deleta no Asaas antes
    de criar, então não há dois QRs vivos.
    """
    conta(user_id, "free", None)
    primeira = _comprar(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set qr_expires_at = now()"
                    " - interval '1 hour' where public_token = %s",
                    (primeira["public_token"],))
        conn.commit()
    asaas_falso["ordem"].clear()

    segunda = _comprar(user_id)

    assert segunda["public_token"] != primeira["public_token"]
    assert asaas_falso["ordem"] == [
        f"delete:pay_{asaas_falso['marca']}_1", "customer", "create", "qr"]
    assert [l["status"] for l in _linhas(user_id)] == ["canceled", "pending"]


def test_qr_ainda_valido_continua_sendo_o_mesmo(user_id, vendavel, asaas_falso):
    """POSITIVO do par: a decisão do dono (mesmo plano → mesmo QR) continua de
    pé enquanto o código é pagável. Sem ele, "vencido" virando "sempre" reemitia
    cobrança a cada visita da tela."""
    conta(user_id, "free", None)
    primeira = _comprar(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set qr_expires_at = now()"
                    " + interval '1 hour' where public_token = %s",
                    (primeira["public_token"],))
        conn.commit()
    asaas_falso["ordem"].clear()

    segunda = _comprar(user_id)

    assert segunda["public_token"] == primeira["public_token"]
    assert asaas_falso["ordem"] == []


# ── a cobrança que só a varredura conseguiu fechar ──────────────────────────

def test_cobranca_reconciliada_volta_com_qr(user_id, vendavel, asaas_falso):
    """DISCRIMINA, e é a conversa inteira: emissão que morre → varredura →
    cliente reabre a tela.

    A varredura anexava só o id e movia a linha para `pending`. O checkout
    seguinte do mesmo plano cai no reuso, lê a linha, não acha QR e devolve 503
    — e `pending` já não volta para a reconciliação. Cobrança impagável e
    insubstituível.
    """
    from core.services.pix_sweeps import reconciliar_saga

    conta(user_id, "free", None)
    ambigua = _emissao_que_morre_depois_do_post(user_id, asaas_falso)
    remota = f"pay_{asaas_falso['marca']}_1"
    asaas_falso["remotas"] = [{"id": remota, "status": "PENDING"}]
    _envelhecer(ambigua["id"], 30)

    assert reconciliar_saga()["anexadas"] >= 1

    r = _comprar(user_id)
    assert r["qr_payload"] == f"000201-{remota}"
    assert r["public_token"] == ambigua["public_token"]


# ── a data que a tela mostra ────────────────────────────────────────────────

def test_starts_at_sai_na_resposta(user_id, vendavel, asaas_falso):
    """DISCRIMINA. `access_starts_at` da LINHA só é escrito no pagamento (§7),
    então a resposta do checkout devolvia `None` em toda venda — inclusive na
    renovação e na migração, que são justamente as que precisam dizer "seu
    acesso começa em".

    O valor certo é o que `plano_da_cobranca` calculou: aqui, com um grant Pix
    vigente até daqui a um ano, a compra do MESMO plano emenda no fim dele.
    """
    from _pix_checkout_helpers import _marcar_paga

    conta(user_id, "free", None)
    primeira = _comprar(user_id)
    _marcar_paga(user_id, primeira["public_token"], dias=-1)
    fim_da_cobertura = datetime.now(timezone.utc) + timedelta(days=364)

    r = _comprar(user_id)

    assert r["starts_at"] is not None, "a tela recebia nulo em todo checkout"
    assert r["starts_at"] >= fim_da_cobertura - timedelta(days=2), r["starts_at"]


def test_starts_at_do_reaproveitamento_e_o_mesmo(user_id, vendavel, asaas_falso):
    """POSITIVO do par: o caminho "fechei a aba e voltei" devolve a MESMA data.

    São duas montagens da resposta (cobrança nova e cobrança reaproveitada) e
    uma função só; sem este caso, corrigir apenas a primeira passaria.

    A comparação tem folga de propósito: sem cobertura vigente o começo é
    `now()`, recalculado a cada chamada — igualdade exata mediria o relógio, não
    a correção (é o flake que já virou portão neste repositório).
    """
    conta(user_id, "free", None)
    primeira = _comprar(user_id)
    segunda = _comprar(user_id)

    assert segunda["public_token"] == primeira["public_token"]
    assert segunda["starts_at"] is not None
    assert abs((segunda["starts_at"] - primeira["starts_at"]).total_seconds()) < 60
