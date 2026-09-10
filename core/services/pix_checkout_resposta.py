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


def resposta(linha: dict, qr_payload: str) -> dict:
    """O contrato que a tela do PR 2 consome. Uma função para os dois caminhos
    (cobrança nova e cobrança reaproveitada) — duas montagens divergiriam.

    `starts_at` sai do `access_starts_at` do dicionário, e quem o preenche antes
    da venda ser paga é o checkout, com o valor que `plano_da_cobranca` calculou:
    a COLUNA só é escrita no pagamento (§7), então ler a linha crua aqui devolvia
    nulo em todo checkout.
    """
    from core.services.pix_brcode import qr_svg_data_url

    return {
        "public_token": linha["public_token"],
        "qr_payload": qr_payload,
        "qr_image": qr_svg_data_url(qr_payload),
        "expires_at": linha["qr_expires_at"],
        "amount_cents": int(linha["amount_cents"]),
        "credit_cents": int(linha["credit_cents"]),
        "starts_at": linha["access_starts_at"],
        "plan": linha["plan"],
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
