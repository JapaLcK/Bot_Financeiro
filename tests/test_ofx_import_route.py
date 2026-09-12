# tests/test_ofx_import_route.py
"""A rota HTTP de upload de OFX — `POST /ofx/import/{user_id}`.

Existe por causa da issue #375: a rota caiu em PRODUÇÃO com 500
(`AssertionError: The python-multipart library must be installed to use form
parsing`) e nenhum teste viu, porque toda a cobertura de OFX
(`test_category_normalization.py`, `test_statement_import.py`) chama os parsers
DIRETO. Parser verde não prova rota viva: o `await request.form()` da
`finance_bot_websocket_custom.py:6911` só existe no caminho HTTP.

Controle negativo deste arquivo NÃO é tirar a linha do `requirements.txt` — o
pytest lê o venv, não o arquivo. É desinstalar `python-multipart` do venv: os
testes que chegam no `request.form()` ficam vermelhos com o mesmo AssertionError
do traceback de produção.
"""
import pytest

import db
from conftest import promote_to_pro
from db.users import ensure_user

# Reuso (CLAUDE.md §0.1): o extrato OFX de exemplo e o TestClient com os 3
# cookies do dashboard já existem — não crio a terceira cópia de nenhum dos dois.
from test_category_normalization import _OFX_BANCO
from test_category_launches_query import _cliente_logado

import frontend.finance_bot_websocket_custom as dashboard


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """A rota tem `@limiter.limit("5/hour")` e o storage do slowapi é em memória,
    compartilhado entre testes. Com 5 testes de 1 POST cada, o último cairia em
    429 em vez de exercitar o que o teste mede. Mesmo padrão de
    `test_billing_checkout.py:34`."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    yield


def _upload(user_id: int, *, como: int | None = None, arquivo=("extrato.ofx", None, "application/x-ofx")):
    """POST multipart em /ofx/import/{user_id}, logado como `como` (default: o
    próprio dono). `arquivo=None` manda um multipart SEM o campo `file`."""
    client, headers = _cliente_logado(como if como is not None else user_id)
    del headers["Content-Type"]  # multipart: o httpx monta o boundary sozinho
    if arquivo is None:
        files = {"outro_campo": ("x.txt", b"x", "text/plain")}
    else:
        nome, corpo, ctype = arquivo
        if corpo is None:
            corpo = _OFX_BANCO.format(acct=user_id % 100000).encode()
        files = {"file": (nome, corpo, ctype)}
    return client.post(f"/ofx/import/{user_id}", files=files, headers=headers)


def _launches(user_id: int) -> list:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, tipo, valor, categoria from launches where user_id = %s", (user_id,))
            return cur.fetchall()


# ─── o defeito da #375: a rota tem de responder E importar ───────────────────

def test_ofx_bancario_importa_pela_rota(pro_user_id):
    """O teste que faltava. Sem `python-multipart` no ambiente isto é 500.

    Não basta `!= 500`: uma rota que devolvesse 400 para tudo passaria nesse
    assert. Aqui o desfecho é o lançamento gravado no Postgres.
    """
    assert _launches(pro_user_id) == []

    r = _upload(pro_user_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["type"] == "bank"

    linhas = _launches(pro_user_id)
    assert len(linhas) == 1, linhas
    # o -39.90 do <STMTTRN> vira saída de 39,90 (o sinal mora no `tipo`), e a
    # categoria só existe se o parser rodou de verdade — não é linha em branco.
    assert (linhas[0]["tipo"], float(linhas[0]["valor"]), linhas[0]["categoria"]) == \
        ("despesa", 39.90, "alimentação"), linhas[0]


# ─── controles negativos: o conserto não mexeu nos 400 que já existiam ───────

def test_sem_campo_file_continua_400(pro_user_id):
    r = _upload(pro_user_id, arquivo=None)
    assert r.status_code == 400, r.text
    assert "campo 'file' ausente" in r.json()["detail"]
    assert _launches(pro_user_id) == []


def test_extensao_errada_continua_400(pro_user_id):
    r = _upload(pro_user_id, arquivo=("extrato.pdf", b"%PDF-1.4 nao e ofx", "application/octet-stream"))
    assert r.status_code == 400, r.text
    assert "extensao .ofx" in r.json()["detail"]
    assert _launches(pro_user_id) == []


# ─── isolamento por usuário (CLAUDE.md §0, regra dura) ───────────────────────

def test_nao_importa_ofx_na_conta_de_outro(pro_user_id):
    """Vítima também é Pro de propósito: assim o 403 vem de QUEM é o dono, e não
    do `_require_pro` — que barraria os dois casos e esconderia o buraco."""
    vitima = int(pro_user_id) + 1
    ensure_user(vitima)
    promote_to_pro(vitima)

    r = _upload(vitima, como=pro_user_id)

    assert r.status_code == 403, r.text
    assert _launches(vitima) == []
    assert _launches(pro_user_id) == []  # nem cai na conta do atacante


# ─── o gate de plano continua fechado ────────────────────────────────────────

def test_usuario_sem_pro_continua_barrado(user_id):
    r = _upload(user_id)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "pro_required"
    assert _launches(user_id) == []
