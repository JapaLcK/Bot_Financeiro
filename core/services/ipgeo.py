"""
core/services/ipgeo.py — Geolocalizacao best-effort de IP via ipapi.co.

Usado pelo audit log para enriquecer o e-mail "novo login detectado" com a
cidade aproximada (usuario comum nao decifra IP cru).

Privacidade: envia o IP do request para um servico de terceiros (ipapi.co).
Pode ser desabilitado com IPGEO_DISABLED=1.

Falha sempre silenciosa: timeout, erro de rede ou JSON invalido devolvem
None. Login nao pode ficar lento por causa de geolocalizacao.
"""
from __future__ import annotations

import ipaddress
import os
import sys

import requests


_IPGEO_TIMEOUT_SEC = 2.5
_IPGEO_URL_TEMPLATE = "https://ipapi.co/{ip}/json/"


def _is_disabled() -> bool:
    return (os.getenv("IPGEO_DISABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_private_ip(ip: str) -> bool:
    """True se o IP não é um endereço público geolocalizável.

    Valida o formato com `ipaddress` (o valor vem de X-Forwarded-For, controlado
    pelo cliente) e bloqueia tudo que não seja global: privado, loopback,
    link-local (169.254/fe80), reservado, multicast, etc. Um valor que não é um
    IP literal válido também é bloqueado — assim nada de estranho chega ao path
    da URL do ipapi.co.
    """
    if not ip:
        return True
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return True
    return not addr.is_global


def lookup_city(ip: str | None) -> str | None:
    """
    Retorna "Cidade, UF, BR" ou similar para um IP publico, ou None.

    Best-effort: timeout curto, falha silenciosa.
    """
    if not ip or _is_disabled() or _is_private_ip(ip):
        return None
    try:
        resp = requests.get(
            _IPGEO_URL_TEMPLATE.format(ip=ip),
            timeout=_IPGEO_TIMEOUT_SEC,
            headers={"User-Agent": "PigBank-AI/1.0 (+ipgeo)"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        print(f"[ipgeo] lookup failed for {ip}: {exc}", file=sys.stderr)
        return None

    if not isinstance(data, dict) or data.get("error"):
        return None

    parts = []
    city = (data.get("city") or "").strip()
    region = (data.get("region") or data.get("region_code") or "").strip()
    country = (data.get("country_code") or data.get("country") or "").strip()
    if city:
        parts.append(city)
    if region and region != city:
        parts.append(region)
    if country:
        parts.append(country)
    return ", ".join(parts) if parts else None
