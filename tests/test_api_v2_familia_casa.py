"""Plano Família, PR 5: a casa (metas e orçamentos do GRUPO), Postgres real, HTTP, nos dois prefixos.

Regras de isolamento 1-8 e 13-15 do plano (9-12 em test_api_v2_familia_casa_regras.py). Mundo de test_api_v2_familia_dados:
família A (TA, MA1, MA2), família B (TB, MB1), U solto e free, U2 solto e pago.
Controles negativos (um a um, restaurado depois) estão na tabela do relato do PR; os positivos do grupo são
t5 (membro comum escreve), t7 (quem volta acessa), t15 (mesmo nome em outro grupo) e o prazo vencido inalterado.
"""
from datetime import timedelta

import pytest

from _apoio_casa import casa, erro, item, linha, meta, orc
from core.services.family_access import revoke_members
from db import family as fam
from db.connection import get_conn
from test_api_v2_familia import _ativo
from test_api_v2_familia_dados import w  # noqa: F401  (w é fixture)
from utils_date import today_tz

# (método, caminho, corpo): `{r}` vira o ref
ROTAS = (("POST", "/metas", {"nome": "x", "alvo": "10"}), ("PUT", "/metas/{r}", {"nome": "x", "alvo": "10", "versao": 1}),
         ("DELETE", "/metas/{r}", None), ("POST", "/metas/{r}/movimentos", {"tipo": "somar", "valor": "1"}),
         ("POST", "/orcamentos", {"categoria": "x", "limite": "10"}),
         ("PUT", "/orcamentos/{r}", {"categoria": "x", "limite": "10", "versao": 1}),
         ("DELETE", "/orcamentos/{r}", None), ("POST", "/orcamentos/{r}/movimentos", {"tipo": "somar", "valor": "1"}))
COM_REF = [r for r in ROTAS if "{r}" in r[1]]


def _chama(w, quem, rota, ref="1"):
    metodo, caminho, corpo = rota
    return w.chamar(quem, metodo, "/familia/casa" + caminho.replace("{r}", ref), **({"json": corpo} if corpo else {}))


def _ref_da_tabela(rota, mb, ob):
    return mb if "/metas" in rota[1] else ob


def test_t1_sem_familia_e_404_em_tudo(w):
    erro(w.chamar(w.u2, "GET", "/familia/casa"), 404, "not_found")
    for rota in ROTAS:
        erro(_chama(w, w.u2, rota), 404, "not_found")
    erro(w.chamar(w.u, "GET", "/familia/casa"), 404, "not_found")  # free e solto: a leitura não pede plano


def test_t2_ref_de_outro_grupo_e_404_e_o_item_fica_igual(w):
    mb, ob = meta(w, w.tb, "Casa de B"), orc(w, w.tb, "Luz")
    antes = linha("family_goals", mb), linha("family_budgets", ob)
    for quem in (w.ta, w.ma1):
        for rota in COM_REF:
            erro(_chama(w, quem, rota, _ref_da_tabela(rota, mb, ob)), 404, "not_found")
    assert (linha("family_goals", mb), linha("family_budgets", ob)) == antes


def test_t3_ref_inexistente_e_malformado_tem_o_mesmo_corpo_do_de_outro_grupo(w):
    mb = meta(w, w.tb, "Casa de B")
    modelo = _chama(w, w.ta, COM_REF[0], mb).json()
    apagado = meta(w, w.ta, "Efemera")
    assert w.chamar(w.ta, "DELETE", f"/familia/casa/metas/{apagado}").status_code == 204
    for ref in (apagado, "999999999999", "abc", "9" * 19, "-1", "1.5", "%20"):
        for rota in COM_REF:
            r = _chama(w, w.ta, rota, ref)
            erro(r, 404, "not_found")
            assert r.json() == modelo


def test_t4_listagem_isolada_mesmo_com_nome_igual(w):
    ma, mb = meta(w, w.ta, "Viagem"), meta(w, w.tb, "Viagem")
    oa, ob = orc(w, w.ma1, "Mercado"), orc(w, w.mb1, "Mercado")
    assert ma != mb and oa != ob
    for quem in (w.ta, w.ma1, w.ma2):
        c = casa(w, quem)
        assert [m["ref"] for m in c["metas"]] == [ma] and [o["ref"] for o in c["orcamentos"]] == [oa]
    for quem in (w.tb, w.mb1):
        c = casa(w, quem)
        assert [m["ref"] for m in c["metas"]] == [mb] and [o["ref"] for o in c["orcamentos"]] == [ob]


def test_t5_todo_membro_le_edita_e_apaga_o_titular_nao_tem_privilegio(w):
    ref = meta(w, w.ma1, "Reforma", "2000.00")
    for quem, nome in ((w.ta, "Do titular"), (w.ma2, "Do outro membro")):
        v = item(w, quem, "metas", ref)["versao"]
        r = w.chamar(quem, "PUT", f"/familia/casa/metas/{ref}", json={"nome": nome, "alvo": "2000", "prazo": None, "versao": v})
        assert r.status_code == 200 and item(w, w.ma1, "metas", ref)["nome"] == nome
        assert w.chamar(quem, "POST", f"/familia/casa/metas/{ref}/movimentos", json={"tipo": "somar", "valor": "5"}).status_code == 200
    o = orc(w, w.ta, "Luz")  # criado pelo titular: o membro comum também edita e apaga
    v = item(w, w.ma1, "orcamentos", o)["versao"]
    assert w.chamar(w.ma1, "PUT", f"/familia/casa/orcamentos/{o}", json={"categoria": "Energia", "limite": "90", "versao": v}).status_code == 200
    assert w.chamar(w.ma2, "DELETE", f"/familia/casa/orcamentos/{o}").status_code == 204
    assert w.chamar(w.ma2, "DELETE", f"/familia/casa/metas/{ref}").status_code == 204
    assert casa(w, w.ta)["metas"] == [] and casa(w, w.ta)["orcamentos"] == []


@pytest.mark.parametrize("como", ["sai", "removido"])
def test_t6_quem_saiu_perde_tudo_e_o_item_fica_sem_autor(w, como):
    ref, o = meta(w, w.ma1, "Reforma"), orc(w, w.ma1, "Luz")
    assert item(w, w.ta, "metas", ref)["autor"] == "Maria"
    if como == "sai":
        assert w.chamar(w.ma1, "POST", "/familia/sair").status_code == 204
    else:
        assert w.chamar(w.ta, "DELETE", f"/familia/membros/{_ref(w.ma1)}").status_code == 204
    erro(w.chamar(w.ma1, "GET", "/familia/casa"), 404, "not_found")
    for rota in ROTAS:
        erro(_chama(w, w.ma1, rota, ref if "/metas" in rota[1] else o), 404, "not_found")
    c = casa(w, w.ta)
    assert [m["autor"] for m in c["metas"]] == [None] and [x["autor"] for x in c["orcamentos"]] == [None]
    assert "Maria" not in w.chamar(w.ta, "GET", "/familia/casa").text


def _ref(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_members where user_id = %s and left_at is null", (uid,))
        return str(cur.fetchone()["id"])


def test_t7_quem_volta_ao_mesmo_grupo_acessa_de_novo_e_o_nome_volta(w):
    ref = meta(w, w.ma1, "Reforma")
    assert w.chamar(w.ma1, "POST", "/familia/sair").status_code == 204
    assert item(w, w.ta, "metas", ref)["autor"] is None
    fam.accept_invite(fam.create_invite(w.ta), w.ma1)
    assert _ativo(w.ma1)
    assert item(w, w.ma1, "metas", ref)["autor"] == "Maria"
    v = item(w, w.ma1, "metas", ref)["versao"]
    r = w.chamar(w.ma1, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "Reforma 2", "alvo": "10", "prazo": None, "versao": v})
    assert r.status_code == 200


def test_t7b_quem_vai_para_outro_grupo_nao_ve_o_antigo_e_nao_deixa_nome_la(w):
    ref = meta(w, w.ma1, "Reforma")
    assert w.chamar(w.ma1, "POST", "/familia/sair").status_code == 204
    fam.accept_invite(fam.create_invite(w.tb), w.ma1)
    assert casa(w, w.ma1)["metas"] == []
    erro(w.chamar(w.ma1, "DELETE", f"/familia/casa/metas/{ref}"), 404, "not_found")
    assert item(w, w.ta, "metas", ref)["autor"] is None  # no grupo antigo ele é "ex-membro", mesmo estando em B
    assert linha("family_goals", ref) is not None


def test_t8_grupo_dissolvido_da_404_para_todos_e_o_novo_nasce_vazio(w):
    ref, o = meta(w, w.ma1, "Reforma"), orc(w, w.ta, "Luz")
    d = w.chamar(w.ta, "GET", "/familia/dissolucao").json()
    assert w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": d["confirmacao"]}).status_code == 204
    for quem in (w.ta, w.ma1, w.ma2):
        erro(w.chamar(quem, "GET", "/familia/casa"), 404, "not_found")
        for rota in COM_REF:
            erro(_chama(w, quem, rota, ref if "/metas" in rota[1] else o), 404, "not_found")
    assert linha("family_goals", ref) is None and linha("family_budgets", o) is None
    fam.create_group(w.ta)
    assert casa(w, w.ta) | {"mes": 0} == {"mes": 0, "metas": [], "orcamentos": []}


def test_t13_sem_plano_ve_mas_nao_edita(w):
    ref = meta(w, w.ta, "Reforma")
    fam.accept_invite(fam.create_invite(w.ta), w.u)  # u é free: só tem plano pelo grant da família
    assert casa(w, w.u)["metas"][0]["ref"] == ref
    revoke_members(w.ta, "teste")  # grupo suspenso: u perde o grant e fica sem plano próprio
    assert casa(w, w.u)["metas"][0]["ref"] == ref  # vê
    for rota in ROTAS:
        erro(_chama(w, w.u, rota, ref if "/metas" in rota[1] else "1"), 402, "plan_selection_required")
    assert linha("family_goals", ref)["guardado_cents"] == 0 and len(casa(w, w.ta)["metas"]) == 1
    assert w.chamar(w.ma1, "POST", f"/familia/casa/metas/{ref}/movimentos", json={"tipo": "somar", "valor": "1"}).status_code == 200  # plano próprio


def test_t13b_titular_sem_plano_ve_o_que_perderia_e_nao_edita(w):
    from _billing_grants_helpers import conta
    ref = meta(w, w.ta, "Reforma")
    conta(w.ta, "free", None, "")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update plan_grants set status = 'revoked' where user_id = %s and source <> 'family'", (w.ta,))
        conn.commit()
    assert casa(w, w.ta)["metas"][0]["ref"] == ref
    erro(_chama(w, w.ta, ROTAS[0]), 402, "plan_selection_required")
    erro(_chama(w, w.ta, ROTAS[2], ref), 402, "plan_selection_required")
    assert linha("family_goals", ref) is not None


def _chaves(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _chaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from _chaves(v)


def test_t14_nenhum_dado_pessoal_entra_ou_sai(w):
    o, m = orc(w, w.ma1, "Mercado"), meta(w, w.ma1, "Reforma")  # MA1 tem despesas em "mercado" (fixture)
    c = casa(w, w.ta)
    assert c["orcamentos"][0]["gasto"] == "0.00" and c["metas"][0]["guardado"] == "0.00"
    assert set(c) == {"mes", "metas", "orcamentos"}
    assert set(c["metas"][0]) == {"ref", "nome", "alvo", "guardado", "falta", "percentual", "prazo", "autor", "versao"}
    assert set(c["orcamentos"][0]) == {"ref", "categoria", "limite", "gasto", "restante", "autor", "versao"}
    assert w.chamar(w.ma1, "POST", f"/familia/casa/orcamentos/{o}/movimentos", json={"tipo": "somar", "valor": "7.50"}).status_code == 200
    c = casa(w, w.ta)
    assert (c["orcamentos"][0]["gasto"], c["orcamentos"][0]["restante"]) == ("7.50", "492.50")  # 7,50 lançado na casa, não os 50 do lançamento dele
    assert not {"user_id", "uid", "dono", "owner", "email", "telefone", "group_id"} & set(_chaves(c))
    assert str(w.ma1) not in w.chamar(w.ta, "GET", "/familia/casa").text


def test_t15_categoria_e_do_grupo_sem_diferenciar_caixa(w):
    a, b = orc(w, w.ta, "mercado"), orc(w, w.tb, "Mercado")  # grupos diferentes: coexistem
    assert a != b
    erro(w.chamar(w.ma1, "POST", "/familia/casa/orcamentos", json={"categoria": "MERCADO", "limite": "1"}), 409, "category_taken")
    o2 = orc(w, w.ma1, "Luz")
    v = item(w, w.ma1, "orcamentos", o2)["versao"]
    erro(w.chamar(w.ma1, "PUT", f"/familia/casa/orcamentos/{o2}", json={"categoria": "Mercado", "limite": "1", "versao": v}), 409, "category_taken")
    assert len(casa(w, w.ta)["orcamentos"]) == 2 and len(casa(w, w.tb)["orcamentos"]) == 1


def test_prazo_vencido_inalterado_continua_editavel_e_novo_passado_e_422(w):
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    erro(w.chamar(w.ta, "POST", "/familia/casa/metas", json={"nome": "x", "alvo": "10", "prazo": ontem}), 422, "validation_error")
    ref = meta(w, w.ta, "Velha", prazo=(today_tz() + timedelta(days=5)).isoformat())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update family_goals set prazo = %s where id = %s", (ontem, int(ref[1:])))
        conn.commit()
    v = item(w, w.ta, "metas", ref)["versao"]
    ok = w.chamar(w.ta, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "Velha 2", "alvo": "20", "prazo": ontem, "versao": v})
    assert ok.status_code == 200 and item(w, w.ta, "metas", ref)["prazo"] == ontem
    mudou = w.chamar(w.ta, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "Velha 2", "alvo": "20", "prazo": (today_tz() - timedelta(days=2)).isoformat(), "versao": v + 1})
    erro(mudou, 422, "validation_error")


@pytest.mark.parametrize("corpo", [{"nome": " ", "alvo": "10"}, {"nome": "x", "alvo": "0"}, {"nome": "x", "alvo": "1.234"},
                                   {"nome": "x", "alvo": "١٢"}, {"nome": "x" * 61, "alvo": "10"}, {"nome": "x", "alvo": 10}])
def test_validacao_na_fronteira_e_422(w, corpo):
    erro(w.chamar(w.ta, "POST", "/familia/casa/metas", json=corpo), 422, "validation_error")
    assert casa(w, w.ta)["metas"] == []


_BAD_CAT = [" ", "\u200b", "a\nb", "x" * 81]
_BAD_VERSAO = [True, "1", 1.5]


@pytest.mark.parametrize("categoria", _BAD_CAT)
def test_categoria_invalida_e_422_no_post_e_no_put(w, categoria):
    erro(w.chamar(w.ta, "POST", "/familia/casa/orcamentos", json={"categoria": categoria, "limite": "10"}), 422, "validation_error")
    o = orc(w, w.ta, "Luz")
    erro(w.chamar(w.ta, "PUT", f"/familia/casa/orcamentos/{o}", json={"categoria": categoria, "limite": "10", "versao": 1}), 422, "validation_error")
    assert [i["categoria"] for i in casa(w, w.ta)["orcamentos"]] == ["Luz"]


@pytest.mark.parametrize("versao", _BAD_VERSAO)
def test_versao_nao_inteira_e_422_em_meta_e_orcamento(w, versao):
    ref, o = meta(w, w.ta, "Reforma"), orc(w, w.ta, "Luz")
    erro(w.chamar(w.ta, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "N", "alvo": "10", "versao": versao}), 422, "validation_error")
    erro(w.chamar(w.ta, "PUT", f"/familia/casa/orcamentos/{o}", json={"categoria": "N", "limite": "10", "versao": versao}), 422, "validation_error")
    assert item(w, w.ta, "metas", ref)["nome"] == "Reforma" and item(w, w.ta, "orcamentos", o)["categoria"] == "Luz"


def test_percentual_e_piso_e_falta_nunca_negativa(w):
    ref = meta(w, w.ta, "Reforma", "1000.00")
    mover = lambda v: w.chamar(w.ta, "POST", f"/familia/casa/metas/{ref}/movimentos", json={"tipo": "somar", "valor": v})  # noqa: E731
    assert mover("999.00").status_code == 200
    m = item(w, w.ta, "metas", ref)
    assert (m["percentual"], m["falta"]) == (99, "1.00")  # 99,9% não arredonda para 100
    assert mover("500.00").status_code == 200
    m = item(w, w.ta, "metas", ref)
    assert (m["percentual"], m["falta"]) == (149, "0.00")


@pytest.mark.parametrize("outra", ["Cafe\u0301", "Ca\u200bfé", "caf\u00e9"])
def test_categoria_quase_duplicada_e_409(w, outra):  # NFD, zero-width no meio, caixa
    orc(w, w.ta, "Café")
    erro(w.chamar(w.ma1, "POST", "/familia/casa/orcamentos", json={"categoria": outra, "limite": "1"}), 409, "category_taken")
    o2 = orc(w, w.ma1, "Luz")
    erro(w.chamar(w.ma1, "PUT", f"/familia/casa/orcamentos/{o2}", json={"categoria": outra, "limite": "1", "versao": 1}), 409, "category_taken")
    assert [i["categoria"] for i in casa(w, w.ta)["orcamentos"]] == ["Café", "Luz"]


def test_categoria_grava_normalizada(w):
    r = w.chamar(w.ta, "POST", "/familia/casa/orcamentos", json={"categoria": "Ca\u200bfe\u0301", "limite": "1"})
    assert r.status_code == 201 and [o["categoria"] for o in r.json()["orcamentos"]] == ["Café"]


def test_categoria_preserva_zwj_e_so_invisivel_e_422(w):
    fam = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    r = w.chamar(w.ta, "POST", "/familia/casa/orcamentos", json={"categoria": fam, "limite": "1"})
    assert r.status_code == 201 and [o["categoria"] for o in r.json()["orcamentos"]] == [fam]
    for vazio in ("\u200d", "\u200d \u200c"):
        assert w.chamar(w.ta, "POST", "/familia/casa/orcamentos", json={"categoria": vazio, "limite": "1"}).status_code == 422


def test_ref_leva_o_tipo_e_ref_de_meta_nao_toca_orcamento_de_mesmo_id(w):
    ref, o = meta(w, w.ta, "Reforma"), orc(w, w.ta, "Luz")
    assert ref[0] == "m" and o[0] == "o"
    with get_conn() as conn, conn.cursor() as cur:  # força o mesmo id numérico nas duas tabelas
        cur.execute("update family_budgets set id = %s where id = %s", (int(ref[1:]), int(o[1:])))
        conn.commit()
    o = "o" + ref[1:]
    modelo = _chama(w, w.ta, COM_REF[0], "zz").json()
    antes = linha("family_budgets", o)
    for rota in COM_REF:
        if "/orcamentos" in rota[1]:
            for ruim in ("m" + ref[1:], ref[1:], "x" + ref[1:], "o", "m"):
                r = _chama(w, w.ta, rota, ruim)
                erro(r, 404, "not_found")
                assert r.json() == modelo
    assert linha("family_budgets", o) == antes and linha("family_goals", ref) is not None
    for rota in COM_REF:
        if "/metas" in rota[1]:
            erro(_chama(w, w.ta, rota, o), 404, "not_found")
    assert w.chamar(w.ta, "DELETE", f"/familia/casa/orcamentos/{o}").status_code == 204


def test_resposta_da_escrita_vem_da_mesma_transacao_e_nao_de_uma_releitura(w, monkeypatch):
    from db import family_house

    def relido(uid):
        raise fam.FamilyError("not_member")  # o membro "saiu" entre o commit e a releitura
    monkeypatch.setattr(family_house, "casa", relido)
    r = w.chamar(w.ta, "POST", "/familia/casa/metas", json={"nome": "Viagem", "alvo": "10"})
    assert r.status_code == 201 and [m["nome"] for m in r.json()["metas"]] == ["Viagem"]


def test_get_nao_e_cacheavel(w):
    assert w.chamar(w.ta, "GET", "/familia/casa").headers["cache-control"] == "no-store"
