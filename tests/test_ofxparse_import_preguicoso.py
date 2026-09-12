# tests/test_ofxparse_import_preguicoso.py
"""`core.handle_incoming` não pode arrastar o `ofxparse` no import.

A cadeia era `core/handle_incoming.py` -> `core.services.ofx_service` ->
`ofx_import` -> `ofxparse`, toda ela em import de TOPO. Como
`core.handle_incoming` é importado por meio repositório (adaptadores de
WhatsApp/Discord, rotas do frontend, helpers de teste), um ambiente sem o
`ofxparse` instalado perdia a suíte inteira em volta dele — medido em
6281 testes coletados contra 7396, com 18 arquivos estourando na coleta e
276 falhas de import tardio.

O anexo OFX é caminho raro; o import dele mora dentro do bloco que o trata.

Os dois controles exigidos pelo CLAUDE.md §3 estão aqui:

  * **negativo** — `test_handle_incoming_importa_sem_ofxparse` fica VERMELHO se
    o import voltar para o topo (medido: com a linha 26 restaurada o subprocesso
    sai com `ModuleNotFoundError: import of ofxparse halted; None in sys.modules`);
  * **positivo** — `test_anexo_ofx_ainda_roteia_para_o_importador` prova que o
    caminho legítimo continua funcionando: um anexo `.ofx` pelo `handle_incoming`
    ainda chega no `handle_ofx_import`. Import preguiçoso que quebra o upload é
    pior que o problema que ele resolve.
"""

import os
import pathlib
import subprocess
import sys

import pytest

import db
from core.types import Attachment, IncomingMessage

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# `None` em sys.modules é o que o ambiente reduzido produz na prática: faz
# `import ofxparse` levantar ModuleNotFoundError e
# `importlib.util.find_spec("ofxparse")` devolver None — o mesmo que o
# `_TEM_OFXPARSE` do conftest.py consulta.
_PROGRAMA = (
    "import sys; sys.modules['ofxparse'] = None; "
    "import core.handle_incoming; print('ok')"
)


def test_handle_incoming_importa_sem_ofxparse():
    """Subprocesso limpo: nenhum módulo do repo pré-carregado pelo pytest.

    Importar no processo do teste não mediria nada — `core.handle_incoming` já
    está em `sys.modules` desde a coleta, com o `ofxparse` real junto.
    """
    saida = subprocess.run(
        [sys.executable, "-c", _PROGRAMA],
        cwd=RAIZ, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
    )
    assert saida.returncode == 0, (
        "core.handle_incoming voltou a arrastar o ofxparse no import:\n"
        + saida.stderr[-2000:]
    )


@pytest.fixture
def uid_pro_pequeno():
    """uid < 2bi e com plano ativo.

    O id pequeno é o mesmo idioma de `test_audio_multi_launch_ask_value.py`:
    `_normalize_user_id` recomprime ids > 2bi via `_internal_user_id`, então o
    assert leria um id diferente do que o handler repassa — e a fixture
    `pro_user_id` sorteia até 10bi, o que deixaria este teste intermitente.
    O plano ativo é o que passa pelo paywall do `handle_incoming`.
    """
    import uuid as _uuid
    from conftest import _cleanup_user, promote_to_pro
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    yield uid
    _cleanup_user(uid)


def test_anexo_ofx_ainda_roteia_para_o_importador(uid_pro_pequeno, monkeypatch):
    """Controle positivo: o upload de OFX pelo bot continua chegando no parser."""
    pytest.importorskip("ofxparse", reason="ausente localmente; presente no CI")

    import core.handle_incoming as hi
    import core.services.ofx_service as ofx_service
    import ofx_import

    chamadas = []
    monkeypatch.setattr(ofx_import, "detect_ofx_type", lambda data: "bank")
    monkeypatch.setattr(
        ofx_service, "handle_ofx_import",
        lambda uid, data, filename: chamadas.append((uid, data, filename)) or "importado",
    )

    msg = IncomingMessage(
        platform="whatsapp", user_id=uid_pro_pequeno, text="",
        message_id="m", external_id="e", raw={},
        attachments=[Attachment(filename="extrato.ofx",
                                content_type="application/x-ofx",
                                data=b"OFXHEADER:100")],
    )
    out = hi.handle_incoming(msg)

    assert chamadas == [(str(uid_pro_pequeno), b"OFXHEADER:100", "extrato.ofx")]
    assert out and "importado" in out[0].text
