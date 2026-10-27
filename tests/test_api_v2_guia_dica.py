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
TODAS = [d["id"] for d in DICAS]
# Quem tem cada uma: metas e patrimônio, Essencial ou acima; assinaturas e previsão, o Plus; simulador, o Pro.
ESSENCIAL = ["metas.ritmo", "patrimonio.soma"]
PLUS = [i for i in TODAS if i != "simulador.corte"]


def dica(quem, corpo=None, **params):
    c, h = cliente(quem)
    return c.post(URL, json={"dica": ID} if corpo is None else corpo, headers=h, params=params)


def vistas(g):
    return [(d["id"], d["vista"]) for d in g["dicas"]]


def test_dois_posts_mantem_o_carimbo_e_o_get_le_vista(uid):
    assert vistas(ler(uid)) == [(i, False) for i in PLUS]
    r = dica(uid)
    assert r.status_code == 200, r.text
    assert dict(vistas(r.json()))[ID] is True and sum(v for _, v in vistas(r.json())) == 1
    antes = linha(uid)["dicas"]
    assert list(antes) == [ID]
    assert dica(uid).status_code == 200
    assert linha(uid)["dicas"] == antes
    assert dict(vistas(ler(uid)))[ID] is True


@pytest.mark.parametrize("id_", TODAS)
def test_cada_dica_carimba_so_a_sua_e_volta_vista(uid, id_):
    promote_to_pro(uid, plan="pro_max")  # todas à vista
    r = dica(uid, {"dica": id_})
    assert r.status_code == 200, r.text
    assert vistas(r.json()) == [(i, i == id_) for i in TODAS]
    assert list(linha(uid)["dicas"]) == [id_]


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


def test_dica_segue_o_plano_da_tela(uid):
    """Cada dica vem com o recurso da tela: o Essencial só metas e patrimônio; o Plus também
    assinaturas e previsão; o Pro (`pro_max`), o simulador. Controles positivos: cada degrau ganha a sua."""
    ids = lambda: [i for i, _ in vistas(ler(uid))]
    assert ids() == PLUS  # o `uid` é o Plus (plano `pro` no v2)
    promote_to_pro(uid, plan="essencial")
    assert ids() == ESSENCIAL
    assert [i for i, _ in vistas(dica(uid).json())] == ESSENCIAL  # grava (inofensivo), mas não devolve
    promote_to_pro(uid, plan="pro_max")
    assert ids() == TODAS


def test_export_lgpd_leva_as_dicas(uid):
    from db.privacy import build_user_export_zip

    dica(uid)
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]
    assert [list(r["dicas"]) for r in dados["guia_do_painel"]] == [[ID]]
