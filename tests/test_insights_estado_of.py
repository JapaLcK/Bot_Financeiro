"""PL-06 A2: a conta do Open Finance (`of_bill_increase`), sempre `warning`: a piora é o valor (DP2), e
dispensar exige o valor que o usuário viu. Pelo monólito real, com Postgres.

O valor muda pela própria cobrança (o banco corrige o lançamento). Com DUAS cobranças no mês vale a mais recente
(#1277): o select ordena por data, `criado_em` e `id`. O controle negativo são os testes que desarrumam a ordem de
inserção (no desempate, a ordem física contra a do `id`): sem o `order by`, o grupo fica vermelho em qualquer plano
de execução, embora caso a caso dependa do plano. O `test_cobranca_mais_recente_e_a_dispensa_pelo_valor` insere em
ordem cronológica: é integração com a dispensa do A2, não controle negativo (sem o `order by` só cai se o plano usar
um índice com `criado_em desc`)."""
from datetime import date, datetime, time
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


# ── #1277: duas cobranças no mês, vale a mais recente ──────────────────────────

def _meses():
    hoje = date.today()
    return _mes_menos(hoje, 2), _mes_menos(hoje, 1)  # dia 10 de m-2 e m-1, nunca o mês corrente


def test_vale_a_cobranca_mais_recente_do_mes_atual(llm):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2, 100)
    _conta_of(uid, m1.replace(day=20), 170)  # a mais nova entra primeiro
    _conta_of(uid, m1.replace(day=5), 160)
    (f,) = db.detect_open_finance_bill_increase(uid)
    assert (f["new"], f["date"]) == (170, m1.replace(day=20))
    (it,) = _of(uid)
    assert it["key"] == f"of_bill_increase:enel luz:{m1:%Y-%m}" and "R$ 170,00" in it["message"]
    assert llm == []


def test_vale_a_cobranca_mais_recente_do_mes_base(llm):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2.replace(day=20), 100)
    _conta_of(uid, m2.replace(day=5), 300)  # a mais velha entra depois: com ela, a média seria 300 e não há alerta
    _conta_of(uid, m1, 130)
    (f,) = db.detect_open_finance_bill_increase(uid)
    assert (f["old"], f["new"], f["pct"]) == (100, 130, 30)


def test_no_mesmo_dia_desempata_pelo_criado_em(llm):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2, 100)
    _conta_of(uid, m1, 170, criado_em=datetime.combine(m1, time(18)))
    _conta_of(uid, m1, 160, criado_em=datetime.combine(m1, time(8)))
    (f,) = db.detect_open_finance_bill_increase(uid)
    assert f["new"] == 170


def test_cobranca_mais_recente_e_a_dispensa_pelo_valor(llm):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2, 100)
    _conta_of(uid, m1.replace(day=5), 160)
    (it,) = [i for i in _get(uid)["insights"] if i["type"] == "of_bill_increase"]
    assert _dispensa(uid, it["key"], 160).status_code == 200 and _of(uid) == []
    _conta_of(uid, m1.replace(day=20), 170)  # cobrança mais nova e maior: volta
    (volta,) = _of(uid)
    assert (volta["key"], volta["explicacao"]["atual"]["valor"]) == (it["key"], 170.0)
    assert _dispensa(uid, it["key"], 170).status_code == 200 and _of(uid) == []
    _conta_of(uid, m1.replace(day=25), 165)  # mais nova e menor que a dispensada: segue fora
    assert _of(uid) == []
    assert llm == []


def _meio_dia(d):  # como o `_insert_of_shadow` grava `criado_em` quando o banco manda só a data
    return datetime.combine(d, time(12))


def test_mesmo_dia_e_mesmo_criado_em_desempata_pelo_id(llm):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2, 100, criado_em=_meio_dia(m2))
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select nextval(pg_get_serial_sequence('launches', 'id')) as id from generate_series(1, 2)")
        menor, maior = sorted(r["id"] for r in cur.fetchall())
        conn.commit()
    # o id maior entra primeiro: a ordem física é o contrário da do `id`
    _conta_of(uid, m1, 170, criado_em=_meio_dia(m1), launch_id=maior)
    _conta_of(uid, m1, 160, criado_em=_meio_dia(m1), launch_id=menor)
    (f,) = db.detect_open_finance_bill_increase(uid)
    assert f["new"] == 170


@pytest.mark.parametrize("cobrancas, dia", [
    # a data do lançamento vence o `criado_em` (importação tardia do dia 5)
    (lambda m1: [(m1.replace(day=20), _meio_dia(m1.replace(day=20)), 170), (m1.replace(day=5), datetime.now(), 160)],
     20),
    # sem `posted_at` vale o `criado_em` como data, e não o fim da fila
    (lambda m1: [(m1.replace(day=20), _meio_dia(m1.replace(day=20)), 170), (None, _meio_dia(m1.replace(day=5)), 160)],
     20),
], ids=["data_antes_do_criado_em", "posted_at_nulo"])
def test_ordem_das_cobrancas_do_mes(llm, cobrancas, dia):
    uid = _usuario("pro")
    m2, m1 = _meses()
    _conta_of(uid, m2, 100, criado_em=_meio_dia(m2))
    for posted_at, criado_em, valor in cobrancas(m1):
        _conta_of(uid, posted_at, valor, criado_em=criado_em)
    (f,) = db.detect_open_finance_bill_increase(uid)
    assert (f["new"], f["date"]) == (170, m1.replace(day=dia))
