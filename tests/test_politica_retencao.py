"""A política de privacidade (`frontend/privacy.html`, §6) repete o prazo da
retenção de `system_event_logs` e nomeia cada exceção de `EVENTOS_PERMANENTES`
(core/services/log_retention.py). HTML estático não importa Python, então este
teste compara as duas fontes (CLAUDE.md §0.7, precedente: test_phosphor_subset.py).

Cego para: o valor de SYSTEM_EVENT_LOGS_RETENTION_DAYS em produção (só a constante)
e a VERDADE do texto em volta do trecho (isso é revisão humana).
"""
from __future__ import annotations

import re
from pathlib import Path

from core.services.log_retention import EVENTOS_PERMANENTES, RETENCAO_MINIMA_DIAS

POLITICA = Path(__file__).resolve().parent.parent / "frontend" / "privacy.html"

_PIX = "registro de pagamento Pix recebido com valor diferente do devido"
_PLUGGY = "registro de falha ao remover uma conexão de Open Finance no provedor"

# evento permanente -> trecho do §6 que o nomeia
TRECHO_DA_POLITICA = {
    "founder_email_sent": "e-mail de boas-vindas da primeira assinatura",
    "onboarding_first_launch_nudge": "dica do primeiro lançamento no WhatsApp",
    "pix_pagamento_parcial_retido": _PIX,
    "pix_excesso_recebido": _PIX,
    "pix_retido_completado": _PIX,
    "pluggy_item_delete_failed": _PLUGGY,
    "pluggy_disconnect_auth_failed": _PLUGGY,
    "account_reset_pluggy_cleanup_failed": _PLUGGY,
    "account_deletion_pluggy_cleanup_failed": _PLUGGY,
}


def _secao_6() -> str:
    """Só o §6: "90 dias" e os trechos também aparecem em outras seções."""
    html = POLITICA.read_text(encoding="utf-8")
    secao = re.search(r'<section id="privacy-6">(.*?)</section>', html, re.S)
    assert secao, 'frontend/privacy.html perdeu <section id="privacy-6">'
    return " ".join(secao.group(1).split())


def test_toda_excecao_da_retencao_esta_nomeada_na_politica():
    texto = _secao_6()
    assert set(TRECHO_DA_POLITICA) == EVENTOS_PERMANENTES, (
        "EVENTOS_PERMANENTES mudou: atualize o §6 de frontend/privacy.html e este mapa")
    faltam = {e: t for e, t in TRECHO_DA_POLITICA.items() if t not in texto}
    assert faltam == {}, f"o §6 de frontend/privacy.html não nomeia mais: {faltam}"
    assert f"apagados automaticamente {RETENCAO_MINIMA_DIAS} dias depois de gravados" in texto, (
        "RETENCAO_MINIMA_DIAS mudou: atualize o prazo no §6 de frontend/privacy.html")


def test_todo_prazo_da_retencao_na_politica_e_o_da_constante():
    """O prazo também aparece no §1 e no parágrafo do teste do Piggy (§6)."""
    texto = " ".join(POLITICA.read_text(encoding="utf-8").split())
    prazos = re.findall(r"apagad[oa]s? automaticamente (\d+) dias depois de gravad", texto)
    assert len(prazos) >= 3 and set(prazos) == {str(RETENCAO_MINIMA_DIAS)}, (
        f"prazos na política: {prazos}; atualize frontend/privacy.html para "
        f"{RETENCAO_MINIMA_DIAS} dias em todas as ocorrências")
