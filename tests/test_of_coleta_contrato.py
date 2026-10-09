"""Paridade entre o backend e o front do acompanhamento da coleta (Onda 5, PR-E).

O front não importa Python: as listas abaixo são lidas do texto do JS/HTML, no
mesmo padrão de tests/test_of_health.py (`_bloco`), e comparadas com a fonte
oficial em core/services/pluggy_health.py (CLAUDE.md §0.7).
"""
import re
from pathlib import Path

_FRONT = Path(__file__).resolve().parent.parent / "frontend"


def test_estados_que_seguem_esperando_existem_no_backend():
    """`SEGUE_ESPERANDO` (frontend/of-status-poll.js) só pode citar estado que o
    backend emite (`_LABELS`): um nome errado ali encerra a espera do
    `still_updating` em silêncio, e a tela para de reler no meio da coleta."""
    from core.services.pluggy_health import _LABELS

    m = re.search(r"const SEGUE_ESPERANDO = \[(.*?)\];", (_FRONT / "of-status-poll.js").read_text(encoding="utf-8"))
    assert m, "SEGUE_ESPERANDO sumiu/foi renomeado no of-status-poll.js — a paridade ficou cega"
    js = re.findall(r'["\'](\w+)["\']', m.group(1))
    assert js, "SEGUE_ESPERANDO vazio"
    assert set(js) <= set(_LABELS), f"estado que o backend não emite: {sorted(set(js) - set(_LABELS))}"
