"""Uma cobrança ativa por usuário: reaproveitar, substituir ou recusar (§10, §9).

Separado de `tests/test_pix_checkout.py` por ASSUNTO — e a divisão foi forçada
pelo teto de 350 linhas (`tests/test_max_lines_python.py`), que é o mesmo motivo
pelo qual `db/pix_charges.py` virou dois módulos neste PR. Lá está a venda
NOVA e as recusas de configuração; aqui está o que acontece quando **já existe
uma cobrança viva** do mesmo dono, que é onde mora o furo financeiro do §10.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * troque a ordem de `_cancelar_remota` (criar antes de deletar, que é a v4 do
    plano) → `test_substituicao_deleta_no_asaas_antes_de_criar` VERMELHO na
    ordem das chamadas. É o furo: dois QRs pagáveis do mesmo dono, duas
    cobranças precificadas contra o MESMO crédito;
  * faça o `DELETE` que falha ser engolido → `test_delete_que_falha_nao_cria_nada`
    VERMELHO com duas cobranças ativas;
  * devolva 409 no caso "mesmo plano" (em vez do mesmo QR) →
    `test_mesma_cobranca_ativa_do_mesmo_plano_devolve_o_mesmo_qr` VERMELHO;
  * aceite a venda sem `confirm_cancel_stripe` → `test_stripe_ativo_sem_confirmacao_e_409`
    VERMELHO, e é o cliente pagando o Pix com o cartão cobrando o mesmo período.

POSITIVO do grupo: `test_substituicao_deleta_no_asaas_antes_de_criar` termina
com a cobrança nova em `pending` e pagável — sem ele, um checkout que só cancela
e nunca cria passaria em todos os negativos.
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 - fixtures
    _comprar,
    _linhas,
    asaas_falso,
    vendavel,
)
from core.services import pix_checkout
from core.services.pix_checkout import CheckoutIndisponivel
from db.pix_charges_saga import buscar_ativa


def test_mesma_cobranca_ativa_do_mesmo_plano_devolve_o_mesmo_qr(
        user_id, vendavel, asaas_falso):
    """DECISÃO DO DONO (2026-09-09): mesmo plano → **mesmo QR, 200**, não 409.

    É o caso "fechei a aba e voltei", e a prova de que é o MESMO é o
    `public_token` — ele é `secrets.token_urlsafe(16)` gerado uma vez na criação
    e nunca reemitido, então token igual é linha igual.

    A segunda asserção é a que impede a versão "quase certa": **nenhuma chamada
    nova ao Asaas**. Emitir outra cobrança aqui deixaria dois QRs pagáveis.
    """
    conta(user_id, "free", None)
    primeira = _comprar(user_id)
    asaas_falso["ordem"].clear()

    segunda = _comprar(user_id)
    assert segunda["public_token"] == primeira["public_token"]
    assert segunda["qr_payload"] == primeira["qr_payload"]
    assert asaas_falso["ordem"] == [], "reemitiu cobrança para o mesmo plano"
    assert len(_linhas(user_id)) == 1


def test_substituicao_deleta_no_asaas_antes_de_criar(user_id, vendavel, asaas_falso):
    """DISCRIMINA a ordem do §10 (correção nº 6).

    Plano DIFERENTE substitui — e o `DELETE` remoto vem ANTES do POST novo. A
    lista `ordem` é a asserção: com a ordem da v4 (criar e depois cancelar), o
    `delete:pay_1` apareceria DEPOIS do segundo `create`, e no intervalo os dois
    QRs seriam pagáveis.
    """
    conta(user_id, "free", None)
    _comprar(user_id, "pro_max")
    asaas_falso["ordem"].clear()

    r = _comprar(user_id, "pro")
    primeiro = f"pay_{asaas_falso['marca']}_1"
    assert asaas_falso["ordem"] == [f"delete:{primeiro}", "customer", "create", "qr"]
    linhas = _linhas(user_id)
    assert [l["status"] for l in linhas] == ["canceled", "pending"]
    assert linhas[0]["qr_payload_enc"] is None, "o QR da cancelada não foi apagado"
    assert r["plan"] == "pro"
    assert buscar_ativa(user_id)["id"] == linhas[1]["id"]


def test_delete_que_falha_nao_cria_nada(user_id, vendavel, asaas_falso):
    """DISCRIMINA. `DELETE` remoto falhando → 503 e **nenhuma** cobrança nova.

    A antiga fica em `canceling`, que é o rastro de que a varredura tem de
    repetir o cancelamento. Engolir a falha aqui é o furo financeiro do §10:
    duas cobranças ativas do mesmo dono, precificadas contra o mesmo crédito.
    """
    conta(user_id, "free", None)
    _comprar(user_id, "pro_max")
    asaas_falso["delete_falha"] = True

    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id, "pro")
    linhas = _linhas(user_id)
    assert len(linhas) == 1, "criou cobrança nova com o cancelamento remoto falhando"
    assert linhas[0]["status"] == "canceling"


# ── migração do Stripe (§9) ──────────────────────────────────────────────────

def test_stripe_ativo_sem_confirmacao_e_409(user_id, vendavel, asaas_falso, monkeypatch):
    """§9 passo 1: 409 `stripe_active` com o `current_period_end`, e nada criado.

    *Negativo: aceite a venda sem `confirm_cancel_stripe` → o cliente paga o Pix
    e o cartão continua cobrando o mesmo período.*
    """
    from datetime import datetime, timedelta, timezone

    from core.services.pix_checkout import StripeAtivo

    fim = datetime.now(timezone.utc) + timedelta(days=20)
    conta(user_id, "pro_max", None)
    monkeypatch.setattr(pix_checkout, "_stripe_vivo", lambda uid: ("sub_1", fim))
    with pytest.raises(StripeAtivo) as capturado:
        _comprar(user_id)
    assert capturado.value.current_period_end == fim
    assert _linhas(user_id) == []


def test_stripe_confirmado_guarda_a_assinatura_e_o_periodo(
        user_id, vendavel, asaas_falso, monkeypatch):
    """§9 passo 3: com `confirm_cancel_stripe`, cria só a cobrança Pix e guarda
    a assinatura + a ESTIMATIVA do período. **Nada é tocado no Stripe agora** —
    quem agenda o cancelamento é o efeito `stripe_cancel`, no PAGAMENTO.

    É esta linha que torna a migração alcançável: sem `stripe_period_end_at`
    gravado aqui, a janela do grant começaria hoje e o cliente pagaria duas
    vezes o período que o cartão já cobre.
    """
    from datetime import datetime, timedelta, timezone

    fim = datetime.now(timezone.utc) + timedelta(days=20)
    conta(user_id, "free", None)
    monkeypatch.setattr(pix_checkout, "_stripe_vivo", lambda uid: ("sub_9", fim))
    _comprar(user_id, confirm_cancel_stripe=True)
    linha = _linhas(user_id)[0]
    assert linha["stripe_subscription_id"] == "sub_9"
    assert linha["stripe_period_end_at"] == fim


