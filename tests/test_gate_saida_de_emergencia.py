"""
tests/test_gate_saida_de_emergencia.py — a isenção de `/settings` no corte.

Arquivo próprio porque `tests/test_gate_plan_selection.py` bateu no teto de 350
linhas (`tests/test_max_lines_python.py`) e porque o assunto é outro: lá é o
gate de ESCOLHA de plano, aqui é a porta de SAÍDA do produto (exportar dados,
excluir conta) sobrevivendo ao corte do fim do Grátis.

Os helpers (`_Req`, `_patch`) vêm por IMPORT do irmão — uma fonte só (§0.7),
mesmo padrão de `tests/_billing_grants_helpers.py`. O par ponta a ponta por
HTTP, com banco real, mora em `tests/test_corte_do_gratis_no_webhook.py`.
"""
from fastapi.responses import RedirectResponse

import core.services.plan_service as plan_service
import frontend.routes.shared as shared
from test_gate_plan_selection import _Req, _patch


# ── /settings é a SAÍDA DE EMERGÊNCIA: isenta da perna do DIREITO ────────────
#
# Decisão do dono. Medido: `grep -rln "auth/account" frontend/` acha
# `settings.html` e mais nada — a UI de exportar os dados e excluir a conta mora
# só ali. Os endpoints `/auth/*` continuam isentos por prefixo, mas sem a página
# não sobra porta para alcançá-los.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — troque o VALOR, não
# apague o bloco: em `frontend/routes/static_pages.serve_settings`, troque
# `gate_plan_selection(request, exige_direito=False)` por
# `gate_plan_selection(request)`. VERMELHO:
#   `test_settings_nao_tranca_quem_perdeu_o_direito`
# Direção: falso NEGATIVO de acesso — quem foi cortado perde a única porta para
# exportar os dados e excluir a própria conta.
#
# Positivo do PAR, e ele fica VERDE sob essa injeção (é o que o torna positivo):
#   `test_settings_ainda_barra_quem_nunca_escolheu_plano`


def test_settings_nao_tranca_quem_perdeu_o_direito(monkeypatch):
    """Cortado alcança /settings: 200 com o HTML, não 302 pra /precos."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=False)
    assert shared.gate_plan_selection(_Req(), exige_direito=False) is None


def test_settings_ainda_barra_quem_nunca_escolheu_plano(monkeypatch):
    """A perna da ESCOLHA sobrevive à isenção — ela não foi desligada junto.

    Sem este caso, `exige_direito=False` poderia ter aberto a página inteira e o
    teste de cima ficaria verde do mesmo jeito."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True, acesso=True)
    out = shared.gate_plan_selection(_Req(), exige_direito=False)
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_isencao_do_direito_nem_consulta_o_veredito(monkeypatch):
    """`exige_direito=False` não paga o SELECT do direito — a perna nem roda."""
    def _explode(uid):
        raise AssertionError("consultou o direito numa rota isenta dele")

    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    monkeypatch.setattr(plan_service, "has_app_access", _explode)
    assert shared.gate_plan_selection(_Req(), exige_direito=False) is None


def test_as_outras_paginas_continuam_exigindo_o_direito(monkeypatch):
    """A isenção é de UMA rota, não do gate. O default é `exige_direito=True`, e
    é ele que /app, /home e /onboarding usam."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=False)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)


def test_so_o_settings_e_isento_do_direito():
    """Quem é isento é UMA rota — enumerado no arquivo, não confiado à memória.

    Uma segunda rota com `exige_direito=False` abriria o corte por uma porta que
    ninguém declarou. Cita o PREDICADO (`exige_direito=False`), que é a coisa
    que a isenção É — nome local e forma de bloco envelhecem
    (`docs/controles_declarados.md`).
    """
    import re
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent
             / "frontend" / "routes" / "static_pages.py").read_text(encoding="utf-8")
    isentos = re.findall(r"async def serve_(\w+)\(request[^)]*\):(?:(?!async def).)*?"
                         r"gate_plan_selection\(request,\s*exige_direito=False\)",
                         fonte, re.S)
    assert isentos == ["settings"], isentos
