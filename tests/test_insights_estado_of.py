"""PL-06 A2: a conta do Open Finance (`of_bill_increase`), sempre `warning`: a piora é o valor (DP2), e
dispensar exige o valor que o usuário viu. Pelo monólito real, com Postgres.

O valor muda pela própria cobrança (o banco corrige o lançamento). Com DUAS cobranças no mês, o detector fica com
a "última" linha de um select sem `order by` (risco 3 do plano), e aqui o plano é um index scan que devolve a mais
nova primeiro: a 2ª cobrança não muda o valor."""
from datetime import date
from decimal import Decimal

import pytest

import db
from core import ai_patterns
from test_insights_contrato import _conta_of, _mes_menos, _usuario, llm  # noqa: F401
from test_insights_estado import _get, _linhas, _put


def _of_na_tela(uid):
    hoje = date.today()
    mes = _mes_menos(hoje, 1)
    _conta_of(uid, _mes_menos(hoje, 2), 100)
    _conta_of(uid, mes, 130)
    (it,) = [i for i in _get(uid)["insights"] if i["type"] == "of_bill_increase"]  # a tela mostra 130
    return it["key"], mes


def _cobra(uid, mes, valor):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update launches set valor = %s where user_id = %s and posted_at = %s", (valor, uid, mes))
        conn.commit()


def _of(uid):
    return [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "of_bill_increase"]


def _dispensa(uid, chave, valor, **kw):
    return _put(uid, chave=chave, dispensado=True, severidade="warning", valor=valor, **kw)


def test_conta_do_of_volta_so_se_o_valor_subir(llm):
    uid = _usuario("pro")
    chave, mes = _of_na_tela(uid)
    assert _dispensa(uid, chave, 130).status_code == 200
    _cobra(uid, mes, 120)  # menor (ainda +20 %): segue fora
    assert _of(uid) == []
    _cobra(uid, mes, 160)  # maior, mesmo mês e mesma severidade: volta
    (volta,) = _of(uid)
    assert (volta["key"], volta["severity"], volta["explicacao"]["atual"]["valor"]) == (chave, "warning", 160.0)
    assert _dispensa(uid, chave, 160).status_code == 200 and _of(uid) == []  # nova referência
    assert llm == []


def test_conta_do_of_que_subiu_antes_do_clique_continua_visivel(llm):
    uid = _usuario("pro")
    chave, mes = _of_na_tela(uid)
    _cobra(uid, mes, 160)
    assert _of(uid)[0]["explicacao"]["atual"]["valor"] == 160.0  # outra aba: a linha já está em 160
    assert _dispensa(uid, chave, 130).status_code == 200
    assert [i["explicacao"]["atual"]["valor"] for i in _of(uid)] == [160.0]
    assert _dispensa(uid, chave, 160).status_code == 200 and _of(uid) == []  # positivo: o 160 visto esconde
    assert llm == []


def test_dispensar_a_conta_do_of_sem_valor_e_422_so_para_o_dono(llm):
    dono, outro = _usuario("pro"), _usuario("pro")
    chave, mes = _of_na_tela(dono)
    _cobra(dono, mes, 160)
    _of(dono)  # a linha está em 160, que o cliente (que viu 130) não mandou
    antes = _linhas(dono)
    r = _put(dono, chave=chave, dispensado=True, severidade="warning")
    assert r.status_code == 422 and _linhas(dono) == antes
    assert [i["explicacao"]["atual"]["valor"] for i in _of(dono)] == [160.0]
    # a chave de outro usuário, sem valor: o 404 de sempre, sem revelar que ela existe
    r = _put(outro, chave=chave, dispensado=True, severidade="warning")
    assert r.status_code == 404 and r.json() == _put(outro, chave="nao:existe:2026-10", dispensado=True,
                                                     severidade="warning").json()
    # positivo: desfazer e "útil" não pedem valor; outro tipo dispensa sem valor
    assert _put(dono, chave=chave, util=True).status_code == 200
    assert _put(dono, chave=chave, dispensado=False).status_code == 200
    db.upsert_budget(dono, "mercado", 100)
    db.add_launch_and_update_balance(dono, "despesa", 90, "Mercado X", None, categoria="mercado")
    ai_patterns.generate_ai_insights(dono, force=True)
    orcamento = f"budget_warning:mercado:{date.today():%Y-%m}"
    assert _put(dono, chave=orcamento, dispensado=True, severidade="warning").status_code == 200
    assert llm == []


@pytest.mark.parametrize("atual", [None, {}, {"valor": None}, {"valor": "x"}, {"valor": float("nan")},
                                   {"valor": {"a": 1}}])
def test_conta_do_of_sem_valor_legivel_nao_derruba_a_leitura(atual):
    from core.services.insights_contrato import medida, projetar
    it = {"key": "of_bill_increase:x:2026-09", "type": "of_bill_increase", "severity": "warning",
          "explicacao": {} if atual is None else {"atual": atual}}
    assert medida(it) is None
    assert projetar([it], acoes_ok=True, dispensadas={it["key"]: ("warning", Decimal("130"))}) == []
