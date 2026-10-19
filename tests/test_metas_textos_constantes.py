"""As constantes do servidor que a tela de metas copia em metas-textos.ts (e a pílula do chat, em MetaPilula.tsx) (CLAUDE.md §0.7):
o front não importa Python, então um teste compara os literais com a fonte (db/)."""
from __future__ import annotations

import pathlib
import re

from db.metas import MESES_RESERVA
from db.metas_ritmo import MOTIVOS_RITMO, RITMO_DIAS_MINIMOS

TS = pathlib.Path(__file__).resolve().parent.parent / "webapp" / "src" / "dashboard" / "lib" / "metas-textos.ts"


def _literal(nome: str) -> int:
    m = re.search(rf"export const {nome} = (\d+);", TS.read_text(encoding="utf-8"))
    assert m, f"{nome} sumiu de metas-textos.ts"
    return int(m.group(1))


def test_dias_minimos_do_front_e_o_do_servidor():
    assert _literal("DIAS_MINIMOS") == RITMO_DIAS_MINIMOS


def test_meses_padrao_do_front_e_o_do_servidor():
    assert _literal("MESES_PADRAO") == MESES_RESERVA


def test_so_do_ritmo_da_pilula_e_o_do_servidor():
    """A pílula do chat (MetaPilula.tsx) copia `MOTIVOS_RITMO`: motivos que só o ritmo emite."""
    texto = (TS.parent.parent / "parts" / "MetaPilula.tsx").read_text(encoding="utf-8")
    m = re.search(r"^const SO_DO_RITMO = \[([^\]]*)\];", texto, re.M)
    assert m, "SO_DO_RITMO sumiu de MetaPilula.tsx"
    assert tuple(re.findall(r'"(\w+)"', m.group(1))) == MOTIVOS_RITMO
