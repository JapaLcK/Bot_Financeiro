from __future__ import annotations

import re


def normalize_phone_e164(phone: str | None, default_country_code: str = "55") -> str:
    raw = (phone or "").strip()
    digits = re.sub(r"\D+", "", raw)

    if digits.startswith("00"):
        digits = digits[2:]

    # Telefones locais brasileiros informados sem DDI.
    if len(digits) in (10, 11):
        digits = f"{default_country_code}{digits}"

    if len(digits) < 12 or len(digits) > 15:
        raise ValueError("Informe um número de WhatsApp válido com DDD.")

    return digits


def mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) < 8:
        return digits or "numero desconhecido"
    return f"{digits[:4]}******{digits[-4:]}"
