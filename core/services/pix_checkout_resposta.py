"""
core/services/pix_checkout_resposta.py — o CONTRATO que a tela do checkout lê.

Saiu de `core/services/pix_checkout.py` quando aquele arquivo bateu no teto de
350 linhas (`tests/test_max_lines_python.py`), e a divisão é por ASSUNTO, não
por tamanho: lá ficam as DECISÕES de dinheiro (flag, preço, crédito, recusa,
substituição, a ordem da saga); aqui fica o que a resposta HTTP carrega — o QR
que sai do banco, a validade que veio do provedor, e o dicionário montado.

Nenhuma função daqui decide venda, e é por isso que elas puderam sair juntas: o
checkout chama as três **no fim** de cada caminho, depois de tudo estar
resolvido.

Plano: docs/plano_pix_anual_asaas.md §10 e §13.6.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional

# Dias em que o QR fica pagável. O provedor cobra `dueDate`, não hora; o poll da
# tela tem teto próprio e muito menor. Três dias é folga para quem fecha a aba e
# volta — o caso que devolve o MESMO QR.
VENCIMENTO_DIAS = 3


def qr_da_linha(linha: dict) -> str | None:
    """Decifra o `qr_payload_enc` da cobrança — o ÚNICO caminho de releitura."""
    return decrypt_pii_optional(linha.get("qr_payload_enc"), ctx=PiiAccessContext(
        purpose="pix_qr_read", actor="system:billing_pix",
        subject_user_id=linha["user_id"], field="qr_payload"))


def agendada(starts_at) -> bool:
    """"Começa depois" — a conta que separa compra imediata de agendada.

    Mora aqui, e não copiada em cada montagem, porque quem a lê são DOIS
    contratos: a resposta do checkout (abaixo) e o poll
    (`frontend/routes/billing_pix.py`). Duas cópias divergiriam (§0.7), e a
    tela que gatilha pela presença de `starts_at` promete "começa em <hoje>"
    a quem começa ao pagar.
    """
    return bool(starts_at and starts_at > datetime.now(timezone.utc))


def resposta(linha: dict, qr_payload: str) -> dict:
    """O contrato que a tela do PR 2 consome. Uma função para os dois caminhos
    (cobrança nova e cobrança reaproveitada) — duas montagens divergiriam.

    `starts_at` sai do `access_starts_at` do dicionário, e quem o preenche antes
    da venda ser paga é o checkout, com o valor que `plano_da_cobranca` calculou:
    a COLUNA só é escrita no pagamento (§7), então ler a linha crua aqui devolvia
    nulo em todo checkout.

    `agendada` existe porque **a data sozinha não separa os dois casos**: na
    compra IMEDIATA o `access_starts_at` também vem preenchido (com `agora`),
    então "tem `starts_at`" não quer dizer "começa depois". A tela que gatilhava
    pela presença da data prometia "começa em <hoje>, assim que o plano atual
    terminar" para quem tinha acesso naquele instante e não tinha plano nenhum
    antes.
    """
    from core.services.pix_brcode import qr_svg_data_url
    from core.services.plan_service import tier_publico

    starts_at = linha["access_starts_at"]
    return {
        "public_token": linha["public_token"],
        "qr_payload": qr_payload,
        "qr_image": qr_svg_data_url(qr_payload),
        "expires_at": linha["qr_expires_at"],
        "amount_cents": int(linha["amount_cents"]),
        "credit_cents": int(linha["credit_cents"]),
        "starts_at": starts_at,
        # Mesma conta do `agendada` que `plano_da_cobranca` devolve em TODOS os
        # ramos (`inicio > agora`) — e não o campo dele, porque o
        # `_reaproveitar` chega aqui só com a data, sem o resto do snapshot.
        "agendada": agendada(starts_at),
        # PÚBLICO, e a coluna continua legada: quem pediu `plus` tem de ler
        # `plus` de volta, não o `pro` que a coluna guarda. Sai daqui como entrou
        # no corpo do POST — é o mesmo vocabulário do `/billing/subscription`,
        # que é quem a tela compara (`pixSub.plan === plano`).
        "plan": tier_publico(linha["plan"]),
    }


def expira(qr: dict):
    """`expirationDate` do provedor → datetime, ou o vencimento local. Forma
    inesperada NÃO derruba a venda: o QR está emitido e pagável, e o que se
    perde é só a precisão do teto do poll."""
    bruto = qr.get("expirationDate")
    if isinstance(bruto, str) and bruto:
        try:
            return datetime.fromisoformat(bruto.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) + timedelta(days=VENCIMENTO_DIAS)
