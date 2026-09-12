"""`UnicodeEncodeError` é `ValueError` — e por isso vazava para o cliente.

O MRO é `UnicodeEncodeError → UnicodeError → ValueError → Exception`, então
todo handler escrito como

    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

captura o erro que o psycopg levanta ao codificar um surrogate solitário e
devolve a mensagem do CODEC ao cliente. MEDIDO em `POST /pockets/{user_id}`,
com usuário autenticado comum e na `origin/main` deste PR:

    {"name":"cofre\\ud800x"}
    → 400 {"detail":"'utf-8' codec can't encode character '\\ud800' in
                     position 5: surrogates not allowed"}

Os dois controles do CLAUDE.md §3, no GRUPO:

  · negativo — troque `detalhe_seguro(exc)` de volta por `str(exc)` em
    `frontend/routes/pockets.py:80`, e `test_surrogate_no_post_nao_vaza_o_codec`
    fica VERMELHO com o texto do codec no `detail` (MEDIDO). É injeção num caso
    que estava VERDE, que é onde ela discrimina;
  · positivo — `test_erro_de_dominio_continua_com_a_mensagem_especifica` prova
    que o `ValueError` da camada `db/` (que É a mensagem do usuário) continua
    chegando inteiro. Sem ele, um conserto que trocasse TODO `str(exc)` por uma
    frase genérica passaria — e isso apaga a única instrução útil da tela, o
    que é pior que o vazamento.

O que este arquivo NÃO alcança: os outros 16 sites da mesma classe (a correção
é a MESMA linha, mas cada rota tem um caminho de autenticação e um corpo
diferentes) e o NUL (`\\x00`), que o psycopg recusa com `psycopg.DataError` —
não é `ValueError`, não é capturado por estes `except`, e continua 500.
"""
import json

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard

CODEC = "codec can't encode"
SURROGATE = "cofre\ud800x"


def _client(user_id: int) -> TestClient:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "pkt@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    client.headers[dashboard.CSRF_HEADER_NAME] = "test-csrf-token"
    client.headers["Content-Type"] = "application/json"
    return client


def _corpo(payload: dict) -> bytes:
    """`json.dumps` com `ensure_ascii` escapa o surrogate — o corpo é ASCII
    legal na rede, e o parser do FastAPI o reconstitui como surrogate solitário.
    `client.post(json=...)` não serve: o httpx codifica em UTF-8 e estoura ANTES
    de sair, com o mesmo erro que estamos medindo do outro lado."""
    return json.dumps(payload).encode("ascii")


@pytest.mark.parametrize("campo", ["name", "description"])
def test_surrogate_no_post_nao_vaza_o_codec(user_id, campo):
    """O caso medido: o cliente não pode receber codec, code point nem offset."""
    corpo = {"name": "cofre", campo: SURROGATE}
    resp = _client(user_id).post(f"/pockets/{user_id}", content=_corpo(corpo))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert CODEC not in detail, detail
    assert "\\ud800" not in detail and "surrogates not allowed" not in detail, detail
    assert detail == "Tem um caractere que não consigo salvar nesse texto. Apaga e digita de novo."


def test_surrogate_no_patch_de_meta_nao_vaza_o_codec(user_id):
    """O irmão do mesmo arquivo (`pockets.py:132`): achar um caso não é
    resolver a categoria (CLAUDE.md §2)."""
    db.create_pocket(user_id, "viagem")
    pocket_id = next(p["id"] for p in db.list_pockets(user_id) if p["name"] == "viagem")

    resp = _client(user_id).patch(f"/pockets/{user_id}/{pocket_id}/meta",
                                  content=_corpo({"description": SURROGATE}))

    assert resp.status_code == 400, resp.text
    assert CODEC not in resp.json()["detail"], resp.text


def test_erro_de_dominio_continua_com_a_mensagem_especifica(user_id):
    """POSITIVO: o `ValueError` da camada `db/` É a mensagem do usuário e não
    pode virar genérico. `STATUS_INVALIDO` vem de `db/pockets.py:248`."""
    db.create_pocket(user_id, "viagem")
    pocket_id = next(p["id"] for p in db.list_pockets(user_id) if p["name"] == "viagem")

    resp = _client(user_id).patch(f"/pockets/{user_id}/{pocket_id}/meta",
                                  content=_corpo({"status": "status-que-nao-existe"}))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "STATUS_INVALIDO", resp.text


def test_unicode_error_e_subclasse_de_value_error():
    """A premissa inteira do conserto, medida em vez de assumida."""
    with pytest.raises(ValueError) as ei:
        "cofre\ud800x".encode("utf-8")
    assert isinstance(ei.value, UnicodeEncodeError)
