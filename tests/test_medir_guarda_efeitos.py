"""O `--autoteste` do `scripts/medir_guarda_efeitos_214.py` roda na suíte.

Aquele script é o que o dono roda contra produção para decidir o #214, e o
`--autoteste` é a ÚNICA prova de que ele DISCRIMINA (a guarda congelada do
`b4d0085` × a guarda viva, importada de `db/accounts.py`). Ele quebra sempre que
um predicado da guarda viva muda de veredito — e quebrou: o caso
`bill_id: "93"` esperava `recusa`, o `13fb792` passou a aceitar string de
dígitos, e o script ficou VERMELHO por dois commits sem ninguém saber, porque
nenhum teste o importava (`grep -rn "medir_guarda_efeitos" tests/` dava zero).

Este arquivo conserta a FRAGILIDADE, não o caso. Derivar o esperado da guarda
viva deixaria o autoteste tautológico (mediria o código contra ele mesmo); a
lista à mão é o valor dele. O que faltava era ela falhar junto com a suíte, e
não só no terminal do dono.

Quando o #214 for decidido e o script apagado, este arquivo vai junto.
"""
from __future__ import annotations

import importlib.util
import pathlib

_CAMINHO = (pathlib.Path(__file__).resolve().parent.parent
            / "scripts" / "medir_guarda_efeitos_214.py")


def test_autoteste_do_script_de_medicao_passa():
    spec = importlib.util.spec_from_file_location("medir_guarda_efeitos_214", _CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo._autoteste()  # `assert` interno: qualquer veredito mudado sai aqui
