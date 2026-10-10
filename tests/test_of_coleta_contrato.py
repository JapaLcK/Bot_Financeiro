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


def test_detalhes_que_mandam_atualizar_casam_com_a_regex_do_toast():
    """O toast de erro do Atualizar não repete "Tente de novo" quando o detalhe
    já manda atualizar. Quem decide é `OF_JA_MANDA_ATUALIZAR`, no settings.html
    (lida daqui: o teste não importa o HTML). Detalhe novo do backend com outra
    palavra faria o toast mandar atualizar duas vezes."""
    from core.services import pluggy_health as ph

    html = (_FRONT / "settings.html").read_text(encoding="utf-8")
    m = re.search(r"const OF_JA_MANDA_ATUALIZAR = /(.+?)/(\w*);", html)
    assert m, "OF_JA_MANDA_ATUALIZAR sumiu/foi renomeada no settings.html — a paridade ficou cega"
    regex = re.compile(m.group(1), re.IGNORECASE if "i" in m.group(2) else 0)
    # TODAS as constantes `_DETALHE_*` do módulo (texto, ou as strings de um
    # dict, lista, tupla ou conjunto): detalhe novo entra na varredura sem
    # ninguém lembrar deste teste.
    detalhes = {}
    for nome, valor in vars(ph).items():
        if not nome.startswith("_DETALHE_"):
            continue
        if isinstance(valor, str):
            detalhes[nome] = valor
        elif isinstance(valor, dict):
            detalhes.update({f"{nome}[{k!r}]": v for k, v in valor.items()})
        elif isinstance(valor, (list, tuple, set, frozenset)):
            detalhes.update({f"{nome}[{v!r}]": v for v in valor if isinstance(v, str)})
    # Os que NÃO mandam atualizar (o toast acrescenta "Tente de novo"), e por quê.
    nao_mandam = {
        # relato do "Dados parciais", não instrução
        "_DETALHE_INVESTIMENTOS_FALTANDO",
        # "Ainda não sincronizou": relato do "Atualizando…" sem sync, não instrução
        "_DETALHE_SEM_SYNC",
        # "Autorize o acesso no app do banco" (as chaves de `_DETALHE_POR_STATUS`):
        # a ação é no app do banco, não Atualizar
        *(n for n, t in detalhes.items() if t == ph._AUTORIZE_NO_APP),
    }
    assert {"_DETALHE_INVESTIMENTOS_FALTANDO", "_DETALHE_COLETA_VENCIDA", "_DETALHE_SEM_SYNC"} <= set(detalhes), \
        "a varredura dos _DETALHE_* ficou cega"
    casam = sorted(n for n, t in detalhes.items() if regex.search(t))
    assert casam == sorted(set(detalhes) - nao_mandam), (
        f"casam com OF_JA_MANDA_ATUALIZAR: {casam}; esperado: os _DETALHE_* fora de {sorted(nao_mandam)}")
