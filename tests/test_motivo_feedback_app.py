"""§0.7: os motivos do 👎 e o teto do comentário existem em Python (`db.ai_feedback.MOTIVOS`, `TEXTO_MAX` de
`frontend/routes/ai_chat.py`), no app (`motivoFeedback.tsx`) e no /painel (`webapp/.../conversaIa.ts`). Nenhum dos dois
importa Python: este teste lê o fonte de cada um."""
import pathlib
import re

import pytest

from db import ai_feedback
from frontend.routes import ai_chat

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTES = pytest.mark.parametrize("fonte_ts", [RAIZ / "app/src/features/painel/motivoFeedback.tsx", RAIZ / "webapp/src/dashboard/lib/conversaIa.ts"], ids=["app", "painel"])


def do_app(fonte: str) -> tuple[dict[str, str], int]:
    motivos = re.search(r"export const MOTIVOS = \{(.*?)\} as const;", fonte, re.S)
    teto = re.search(r"export const TEXTO_MAX = (\d+);", fonte)
    assert motivos and teto, "o MOTIVOS/TEXTO_MAX do app ou do painel mudou de forma: ajuste este teste"
    return dict(re.findall(r'(\w+):\s*"([^"]*)"', motivos.group(1))), int(teto.group(1))


@FONTES
def test_motivos_e_teto_sao_os_do_python(fonte_ts):
    motivos, teto = do_app(fonte_ts.read_text(encoding="utf-8"))
    assert motivos == ai_feedback.MOTIVOS
    assert list(motivos) == list(ai_feedback.MOTIVOS)  # a ordem é a das pílulas
    assert teto == ai_chat.TEXTO_MAX


@FONTES
def test_controle_divergencia_fica_vermelha(fonte_ts, monkeypatch):
    fonte = fonte_ts.read_text(encoding="utf-8")
    for velha, nova in (("Número errado", "Numero errado"), ("faltou_card", "faltou_cartao"), ("= 500;", "= 501;")):
        assert velha in fonte
        motivos, teto = do_app(fonte.replace(velha, nova))
        assert (motivos, teto) != (ai_feedback.MOTIVOS, ai_chat.TEXTO_MAX), velha
    monkeypatch.setitem(ai_feedback.MOTIVOS, "novo", "Novo")  # chave nova no Python sem o app
    assert do_app(fonte)[0] != ai_feedback.MOTIVOS
