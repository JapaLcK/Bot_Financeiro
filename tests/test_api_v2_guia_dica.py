"""`POST /api/v2/guia/dica` e as `dicas` do `GET /api/v2/guia` (`api/v2/guia.py`, `db/guia.py`)
pelo monólito real: a dica de primeiro uso de cada tela. Carimbo da 1ª vez, sem tocar o guia;
só a dica que o plano dá; 422, CSRF e export LGPD. Isolamento A/B: `tests/test_api_v2_isolamento.py`.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from api.v2.guia import DICAS
from conftest import promote_to_pro
from test_api_v2_guia import cliente, ler, linha, post, saiu_em, uid  # noqa: F401 (uid é fixture)

URL = "/api/v2/guia/dica"
ID = DICAS[0]["id"]


def dica(quem, corpo=None, **params):
    c, h = cliente(quem)
    return c.post(URL, json={"dica": ID} if corpo is None else corpo, headers=h, params=params)


def vistas(g):
    return [(d["id"], d["vista"]) for d in g["dicas"]]


def test_dois_posts_mantem_o_carimbo_e_o_get_le_vista(uid):
    assert vistas(ler(uid)) == [(ID, False)]
    r = dica(uid)
    assert r.status_code == 200, r.text
    assert vistas(r.json()) == [(ID, True)]
    antes = linha(uid)["dicas"]
    assert list(antes) == [ID]
    assert dica(uid).status_code == 200
    assert linha(uid)["dicas"] == antes
    assert vistas(ler(uid)) == [(ID, True)]


def test_dica_nao_oferece_o_guia(uid):
    """A dica não é o convite: o guia segue em `oferecer`. Controle positivo: o `visto`
    do convite, depois, ainda carimba a oferta."""
    saiu_em(uid, 0)
    assert dica(uid).json()["estado"] == "oferecer"
    l = linha(uid)
    assert (l["oferecido_em"], l["feitos"], l["concluido_em"]) == (None, {}, None)
    assert ler(uid)["estado"] == "oferecer"
    assert post(uid, "visto").json()["estado"] == "em_andamento"
    assert linha(uid)["oferecido_em"] is not None


@pytest.mark.parametrize("corpo", [{"dica": "x"}, {}, {"dica": None}], ids=["desconhecida", "vazio", "null"])
def test_invalida_e_422_e_nao_grava(uid, corpo):
    r = dica(uid, corpo)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert linha(uid) is None


def test_sem_csrf_e_403(uid):
    c, _ = cliente(uid, com_csrf=False)
    r = c.post(URL, json={"dica": ID})
    assert (r.status_code, r.json()) == (403, {"detail": "Token CSRF inválido ou ausente."})
    assert linha(uid) is None


def test_essencial_nao_recebe_a_dica_de_assinaturas(uid):
    """Assinaturas é Plus ou Pro: o Essencial não tem a tela, nem a dica. Controle positivo:
    o Plus (o `uid`, plano `pro` = Plus no v2) recebe, ainda não vista."""
    assert vistas(ler(uid)) == [(ID, False)]
    promote_to_pro(uid, plan="essencial")
    assert ler(uid)["dicas"] == []
    assert dica(uid).json()["dicas"] == []  # grava (inofensivo), mas não devolve


def test_export_lgpd_leva_as_dicas(uid):
    from db.privacy import build_user_export_zip

    dica(uid)
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]
    assert [list(r["dicas"]) for r in dados["guia_do_painel"]] == [[ID]]
