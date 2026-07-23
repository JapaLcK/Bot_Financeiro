"""
core/services/pix_brcode.py — Gera o "Pix copia e cola" (BR Code) estático.

Padrão EMV®QRCPS adotado pelo Banco Central: uma sequência de campos TLV
(id de 2 dígitos + tamanho de 2 dígitos + valor), fechada por um CRC16.
É tudo calculado localmente — não existe chamada a banco nem custo.

Usado no admin pra pagar saque de afiliado: monta o código com a chave Pix do
afiliado e o valor já embutido, então o Lucas só escaneia e confirma (sem
digitar chave nem valor, que é onde mora o erro).

Referência dos campos usados:
  00 Payload Format Indicator      "01"
  26 Merchant Account Information  → 00 GUI "BR.GOV.BCB.PIX" + 01 chave
  52 Merchant Category Code        "0000"
  53 Transaction Currency          "986" (BRL)
  54 Transaction Amount            opcional, "139.30"
  58 Country Code                  "BR"
  59 Merchant Name                 nome do RECEBEDOR (máx 25)
  60 Merchant City                 cidade do recebedor (máx 15)
  62 Additional Data               → 05 Reference Label (txid)
  63 CRC16                         CRC-16/CCITT-FALSE do payload inteiro
"""
from __future__ import annotations

import re
import unicodedata

GUI_PIX = "BR.GOV.BCB.PIX"
# O nome/cidade descrevem o RECEBEDOR. Como só guardamos email + chave do
# afiliado (não o nome civil), usamos um rótulo genérico: o destino de fato é
# definido pela CHAVE, e o app do banco mostra o titular real na confirmação.
DEFAULT_RECEIVER_NAME = "AFILIADO PIGBANK"
DEFAULT_CITY = "SAO PAULO"


def _tlv(field_id: str, value: str) -> str:
    """Monta um campo: id + tamanho (2 dígitos) + valor."""
    return f"{field_id}{len(value):02d}{value}"


def _sanitize(text: str, max_len: int) -> str:
    """ASCII maiúsculo sem acento — o padrão não aceita caracteres especiais."""
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9 ]", "", ascii_only).strip().upper()
    return cleaned[:max_len] or "PAGAMENTO"


def crc16_ccitt(payload: str) -> str:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) em hex maiúsculo, 4 dígitos."""
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def build_pix_brcode(
    pix_key: str,
    amount_cents: int | None = None,
    receiver_name: str = DEFAULT_RECEIVER_NAME,
    city: str = DEFAULT_CITY,
    txid: str = "***",
) -> str:
    """
    Monta o payload do Pix copia e cola.

    - `pix_key`: chave do recebedor (CPF/CNPJ, email, telefone ou aleatória).
    - `amount_cents`: se informado, embute o valor (evita digitação). Omitido
      quando None — aí o pagador digita o valor no app.
    - `txid`: "***" significa "sem identificador", aceito no QR estático.

    Levanta ValueError se a chave estiver vazia.
    """
    key = (pix_key or "").strip()
    if not key:
        raise ValueError("chave Pix vazia — não dá pra montar o BR Code")

    merchant_account = _tlv("00", GUI_PIX) + _tlv("01", key)

    payload = (
        _tlv("00", "01")
        + _tlv("26", merchant_account)
        + _tlv("52", "0000")
        + _tlv("53", "986")
    )
    if amount_cents is not None:
        if int(amount_cents) <= 0:
            raise ValueError("valor do Pix precisa ser positivo")
        payload += _tlv("54", f"{int(amount_cents) / 100:.2f}")
    payload += (
        _tlv("58", "BR")
        + _tlv("59", _sanitize(receiver_name, 25))
        + _tlv("60", _sanitize(city, 15))
        + _tlv("62", _tlv("05", _sanitize(txid, 25) if txid != "***" else "***"))
    )

    # O CRC é calculado sobre o payload JÁ com "6304" no fim.
    payload += "6304"
    return payload + crc16_ccitt(payload)


def qr_svg_data_url(payload: str) -> str:
    """QR do BR Code como data URL (SVG). Mesmo caminho usado no QR do MFA:
    factory SVG do `qrcode`, sem PIL e sem CDN (o CSP bloqueia externo)."""
    import base64
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(
        payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2
    )
    buf = io.BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{encoded}"
