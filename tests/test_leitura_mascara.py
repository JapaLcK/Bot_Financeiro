"""PR 3d: o leitor de conta de CONDOMÍNIO não vê nome de pessoa (funcionário, condômino). Banco real.

Mundo (`_apoio_mascara.condominio`): "Pix para João da Silva CPF 123..." em `salários` (grupo pessoal), "Mensalidade apto 101
Maria" em `mensalidades` (receitas) e "Sabesp" em `água` (funcionais), em todas as pernas: conta, cartão, parcelado,
gasto fixo, receita fixa, boleto. Para CADA rota classificada "mascara" em `tests/test_leitura_guard.py`:
o JSON serializado INTEIRO do leitor não tem nenhum `PROIBIDO`; vê valor/data/categoria e o "Sabesp"; o dono vê tudo
(controle positivo); com o helper desligado a rota fica vermelha (controle negativo).
"""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.nativo.app as nativo
import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _apoio_leitura import leitor_de, url_de
from _apoio_mascara import PROIBIDO, VISIVEL, condominio
from conftest import promote_to_pro
from core.services import leitura_mascara
from test_leitura_guard import _gets, classe_da_rota

HOJE = date.today().isoformat()
SALARIOS = "salários"

# (rota como o FastAPI a escreve) -> (query, o dono vê nome?, o leitor vê "Sabesp"?)
# `dono_ve=False`: a rota não carrega nome neste mundo (sem Open Finance nem LLM) mas está coberta pelo helper/negação.
_V2 = {
    "assinaturas": ("", False, False), "contas-a-pagar": ("", True, True), "gastos": ("", True, True),
    "insights": ("", True, False), "lancamentos": ("", True, True), "orcamento-domestico": ("", True, True),
    "parcelas": ("", True, False), "previsao": ("", True, False),
    "gastos/categoria": (f"?categoria={SALARIOS}", True, False),
}
ROTAS = {
    **{f"/api/{ns}/{r}": q for ns in ("v2", "app") for r, q in _V2.items()},
    "/data/{user_id}": ("", True, True),
    "/history/{user_id}/list": ("", True, True),
    "/forecast/{user_id}": ("", True, False),
    "/recurring-bills/{user_id}/projection": ("?date=2027-01-15", False, False),
    "/recurring-expenses/{user_id}": ("", True, True),
    "/recurring-incomes/{user_id}": ("", True, False),
    "/household-budget/{user_id}/status": ("", True, True),
    "/categories/{user_id}/launches": (f"?categoria={SALARIOS}", True, False),
    "/analytics/{user_id}/kpis": ("", True, False),
    "/analytics/{user_id}/patterns": ("", False, False),
    "/insights/{user_id}/current": ("", True, False),
    "/bills/{user_id}/{bill_id}": ("", True, False),
    "/installments/{user_id}/list": ("", True, False),
}


@pytest.fixture
def mundo(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    condominio(user_id)  # ANTES da chave v2: com ela o cartão manual é recusado (fonte única do Open Finance)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    monkeypatch.setattr(nativo, "exigir_onboarding", lambda uid: None)  # o /api/app pede o onboarding do OF: não é o assunto
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from credit_bills where user_id=%s order by id limit 1", (user_id,))
        bill = cur.fetchone()["id"]
    return {"uid": user_id, "bill": bill, "leitor": leitor_de(user_id)["bearer"],
            "dono": {"Authorization": f"Bearer {sessao_de(user_id)['access']}"}, "c": TestClient(dashboard.app)}


def _url(m, rota):
    return url_de(rota.replace("{user_id}", str(m["uid"])).replace("{bill_id}", str(m["bill"]))) + ROTAS[rota][0]


def _texto(r) -> str:
    assert r.status_code == 200, (r.request.url, r.status_code, r.text[:200])
    return json.dumps(r.json(), ensure_ascii=False)  # o JSON inteiro: chave, valor, aninhado


def _vazou(texto: str) -> list[str]:
    return [p for p in PROIBIDO if p in texto]


# ── 1. enumeração: toda rota "mascara" da tabela de classificação tem caso aqui, e vice-versa ──

def test_toda_rota_mascara_classificada_tem_caso_e_vice_versa():
    classificadas = {p for p, u in _gets() if classe_da_rota(u) == "mascara"}
    assert classificadas == set(ROTAS), (classificadas ^ set(ROTAS))
    assert len(classificadas) >= 20  # a descida de rotas não quebrou


# ── 2. o leitor não vê nome em lugar nenhum; o dono vê (controle positivo) ──

@pytest.mark.parametrize("rota", sorted(ROTAS))
def test_leitor_nao_ve_nome_e_dono_ve(mundo, rota):
    m = mundo
    _, dono_ve, sabesp = ROTAS[rota]
    url = _url(m, rota)
    leitor = _texto(m["c"].get(url, headers=m["leitor"]))
    assert _vazou(leitor) == [], (rota, _vazou(leitor))
    dono = _texto(m["c"].get(url, headers=m["dono"]))
    if dono_ve:
        assert _vazou(dono), f"{rota}: o dono deveria ver o nome (a rota não prova nada sem isto)"
    if sabesp:
        assert VISIVEL in leitor and VISIVEL in dono, rota  # fora dos grupos pessoal/receitas, o nome fica


def test_leitor_ve_valor_data_e_categoria_do_lancamento_oculto(mundo):
    m = mundo
    itens = m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).json()["itens"]
    sal = next(i for i in itens if i["categoria"] == "salarios")
    mens = next(i for i in itens if i["categoria"] == "mensalidades")
    assert (sal["valor"], sal["data"], sal["tipo"]) == ("1500.0", HOJE, "saida")
    assert (mens["valor"], mens["data"], mens["tipo"]) == ("450.0", HOJE, "entrada")
    assert sal["descricao"] == sal["mensagem"] == "salarios"  # o rótulo neutro é o nome da categoria
    agua = next(i for i in itens if i["categoria"] == "agua")
    assert agua["descricao"] == "Sabesp" and agua["mensagem"] == "Sabesp conta de água"


def test_busca_por_texto_nao_confirma_nome_ao_leitor(mundo):
    m = mundo
    leitor = m["c"].get("/api/v2/lancamentos?q=Joao", headers=m["leitor"]).json()["itens"]
    sem_q = m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).json()["itens"]
    assert len(leitor) == len(sem_q) > 1  # a busca foi ignorada: acertar ou errar o nome dá a mesma resposta
    dono = m["c"].get("/api/v2/lancamentos?q=Joao", headers=m["dono"]).json()["itens"]
    assert 0 < len(dono) < len(sem_q)  # positivo: para o dono a busca segue filtrando
    for rota in ("/history/{u}/list?q=Joao", "/data/{u}?q=Joao"):
        r = m["c"].get(rota.replace("{u}", str(m["uid"])), headers=m["leitor"])
        assert _vazou(_texto(r)) == []


# ── 3. conta pessoal: nada muda para o leitor ──

def test_leitor_de_conta_pessoal_ve_tudo_como_antes(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    db.add_launch_and_update_balance(user_id, "despesa", 80.0, "João da Silva", "Pix para João da Silva CPF 123.456.789-00", "salários")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    c, leitor = TestClient(dashboard.app), leitor_de(user_id)["bearer"]
    for url in ("/api/v2/lancamentos", f"/data/{user_id}", f"/history/{user_id}/list"):
        assert "João da Silva" in _texto(c.get(url, headers=leitor)), url
    assert leitura_mascara.categorias_visiveis(user_id, _Req(True)) is None


class _Req:  # só o que o helper lê de um Request
    def __init__(self, ro): self.state = type("S", (), {"somente_leitura": ro})(); self.headers = {}; self.cookies = {}


# ── 4. WS: a conversa real no socket ──

def test_ws_do_leitor_snapshot_get_month_get_history_sem_nome(mundo):
    m = mundo
    leitor = leitor_de(m["uid"])
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    quadros = {}
    with c.websocket_connect(f"/ws/{m['uid']}") as ws:
        quadros["snapshot"] = ws.receive_json()
        ws.send_json({"type": "refresh", "q": "Joao"})
        quadros["update"] = ws.receive_json()
        hoje = date.today()
        ws.send_json({"type": "get_month", "year": hoje.year, "month": hoje.month, "q": "Maria"})
        quadros["month_data"] = ws.receive_json()
        ws.send_json({"type": "get_history", "months": 3})
        quadros["history_data"] = ws.receive_json()
    assert set(quadros) == {q["type"] for q in quadros.values()}
    for tipo, q in quadros.items():
        assert _vazou(json.dumps(q, ensure_ascii=False)) == [], tipo
    for tipo in ("snapshot", "update", "month_data"):
        linhas = quadros[tipo]["data"]["recent_launches"]
        assert {l["categoria"] for l in linhas} >= {SALARIOS, "mensalidades", "água"}, tipo
        sal = next(l for l in linhas if l["categoria"] == SALARIOS)
        assert float(sal["valor"]) == 1500.0 and sal["alvo"] == sal["nota"] == SALARIOS, tipo
        assert any(l["alvo"] == VISIVEL for l in linhas), tipo
    # a busca é ignorada: "Joao"/"Maria" não filtram nem confirmam (todas as linhas voltam)
    assert quadros["update"]["data"]["launches_pagination"]["total"] == quadros["snapshot"]["data"]["launches_pagination"]["total"]
    # positivo: o dono, no mesmo socket, vê o nome
    d = TestClient(dashboard.app)
    d.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(m["uid"])["dashboard"])
    with d.websocket_connect(f"/ws/{m['uid']}") as ws:
        assert _vazou(json.dumps(ws.receive_json(), ensure_ascii=False))


def test_alerta_do_snapshot_nao_carrega_nome_de_gasto_fixo(mundo):
    """`alerts[].name` (gasto fixo cobrado / receita creditada) não tem categoria na linha: o servidor a põe."""
    m = mundo
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from recurring_expenses where user_id=%s and name like 'Maria%%'", (m["uid"],))
        rid = cur.fetchone()["id"]
        cur.execute("select id from recurring_incomes where user_id=%s", (m["uid"],))
        iid = cur.fetchone()["id"]
        cur.execute("insert into recurring_charges (recurring_id, user_id, amount, ym) values (%s,%s,900,'2026-11')", (rid, m["uid"]))
        cur.execute("insert into recurring_income_credits (income_id, user_id, amount, ym) values (%s,%s,450,'2026-11')", (iid, m["uid"]))
        conn.commit()
    r = m["c"].get(f"/data/{m['uid']}", headers=m["leitor"])
    tipos = {a["type"] for a in r.json()["alerts"]}
    assert {"recurring_charged", "recurring_credited"} <= tipos
    assert _vazou(_texto(r)) == []
    assert _vazou(_texto(m["c"].get(f"/data/{m['uid']}", headers=m["dono"])))  # positivo


# ── 5. controle negativo: sem o helper as rotas ficam vermelhas ──

def test_controle_negativo_helper_desligado_deixa_as_rotas_vermelhas(mundo, monkeypatch):
    m = mundo
    monkeypatch.setattr(leitura_mascara, "categorias_visiveis", lambda uid, request: None)  # todo chamador passa por aqui
    vermelhas = [rota for rota, (_, dono_ve, _) in ROTAS.items()
                 if dono_ve and _vazou(_texto(m["c"].get(_url(m, rota), headers=m["leitor"])))]
    assert set(vermelhas) == {r for r, (_, dono_ve, _) in ROTAS.items() if dono_ve}, set(ROTAS) - set(vermelhas)


# ── 6. rotas negadas (sem categoria para mascarar) e o helper em si ──

@pytest.mark.parametrize("url", ["/analytics/{u}/top-merchants", "/open-finance/{u}", "/open-finance/{u}/movements",
                                 "/open-finance/{u}/reconciliations", "/open-finance/{u}/cash-transfers",
                                 "/pockets/{u}/cx/history", "/agents/{u}/feed", "/agents/{u}/xerife/esperados"])
def test_rota_negada_ao_leitor_e_aberta_ao_dono(mundo, url):
    m = mundo
    url = url.replace("{u}", str(m["uid"]))
    r = m["c"].get(url, headers=m["leitor"])
    assert r.status_code == 403 and r.json() == {"error": "somente_leitura"}
    assert m["c"].get(url, headers=m["dono"]).status_code != 403 or url.startswith("/agents")  # agentes: o 403 do dono é o beta


def test_helper_nao_altera_a_entrada_e_so_age_no_leitor(mundo):
    uid = mundo["uid"]
    dado = {"itens": [{"categoria": "Salários", "descricao": "João", "valor": 1}, {"categoria": "água", "descricao": "Sabesp"}]}
    copia = json.loads(json.dumps(dado))
    out = leitura_mascara.mascarar_para_leitor(uid, _Req(True), dado)
    assert dado == copia  # pode vir de cache
    assert [i["descricao"] for i in out["itens"]] == ["Salários", "Sabesp"] and out["itens"][0]["valor"] == 1
    assert leitura_mascara.mascarar_para_leitor(uid, _Req(False), dado) is dado  # dono: o próprio objeto
    assert leitura_mascara.mascarar_para_leitor(uid, True, dado)["itens"][0]["descricao"] == "Salários"  # flag do /ws


# ── 7. regra INVERTIDA (fail-closed): o nome só fica em grupo funcionais/manutencao/tributarias/outras ──

def _leitor_mascara(uid, dado, **kw):
    return leitura_mascara.mascarar_para_leitor(uid, _Req(True), dado, **kw)


def _lanca(uid, valor, alvo, nota, categoria):
    db.add_launch_and_update_balance(uid, "despesa", valor, alvo, nota, categoria)


def test_i1_arquivar_a_categoria_nao_desmascara_lancamento_antigo(mundo):
    m = mundo
    antes = m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).text
    assert _vazou(antes) == []
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update user_categories set is_archived = true where user_id=%s and name in ('salários','mensalidades','água')",
                    (m["uid"],))
        assert cur.rowcount == 3
        conn.commit()
    for url in ("/api/v2/lancamentos", f"/data/{m['uid']}", f"/history/{m['uid']}/list"):
        assert _vazou(_texto(m["c"].get(url, headers=m["leitor"]))) == [], url
    assert VISIVEL in m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).text  # o arquivado de grupo visível também (o mapa usa todas as categorias da conta)


_ROTAS_I2 = ("/api/v2/lancamentos", "/api/v2/gastos", "/data/{u}", "/history/{u}/list", "/api/v2/orcamento-domestico")


@pytest.mark.parametrize("categoria", ["outros", None])
def test_i2_mensagem_comum_em_outros_ou_sem_categoria_nao_vaza_nome(mundo, categoria):
    m = mundo
    _lanca(m["uid"], 800.0, "Maria faxineira", "pix 800 pra Maria faxineira CPF 123.456.789-00", categoria)
    _lanca(m["uid"], 700.0, "José porteiro", "pagamento do José porteiro", categoria)
    for rota in _ROTAS_I2:
        url = rota.replace("{u}", str(m["uid"]))
        assert _vazou(_texto(m["c"].get(url, headers=m["leitor"]))) == [], url
    # positivo: o dono vê o nome nas rotas de lançamento (sem isto o teste passaria numa rota vazia)
    assert _vazou(_texto(m["c"].get("/api/v2/lancamentos", headers=m["dono"])))


def test_categoria_desconhecida_e_com_sufixo_de_grupo(mundo):
    uid = mundo["uid"]
    dado = {"itens": [{"categoria": "inexistente", "descricao": "João"}, {"categoria": "água (funcionais)", "descricao": "Sabesp"},
                      {"categoria": "Água", "descricao": "Sabesp"}, {"categoria": None, "descricao": "João"},
                      {"categoria": "", "descricao": "João"}, {"categoria": "outros", "descricao": "João"},
                      {"categoria": "mensalidades", "descricao": "Maria"}, {"descricao": "sem chave de categoria"}]}
    assert [i["descricao"] for i in _leitor_mascara(uid, dado)["itens"]] == [
        "Oculto", "Sabesp", "Sabesp", "Sem categoria", "Sem categoria", "outros", "mensalidades", "sem chave de categoria"]


def test_m1_subdict_sem_categoria_herda_a_mascara_do_pai(mundo):
    uid = mundo["uid"]
    item = {"categoria": "salários", "alvo": "João", "cash_declaration": {"alvo": "João", "nota": "Maria", "valor": 5,
            "itens": [{"descricao": "José"}], "notes": ["CPF 123", None]},
            "filho": {"categoria": "água", "descricao": "Sabesp-do-filho"}}
    out = _leitor_mascara(uid, item)
    assert out["cash_declaration"] == {"alvo": "salários", "nota": "salários", "valor": 5,
                                       "itens": [{"descricao": "salários"}], "notes": ["salários", None]}
    assert out["filho"]["descricao"] == "salários"  # filho visível sob pai oculto: não reabre
    pai_visivel = {"categoria": "água", "descricao": "Sabesp", "filho": {"categoria": "salários", "descricao": "João"},
                   "neto": {"nota": "Sabesp"}}
    out = _leitor_mascara(uid, pai_visivel)
    assert (out["descricao"], out["filho"]["descricao"], out["neto"]["nota"]) == ("Sabesp", "salários", "Sabesp")


def test_m1_history_list_cash_declaration_da_rota_nao_vaza(mundo, monkeypatch):
    """O sub-dict sem categoria própria que a rota real devolve dentro de um item oculto."""
    m = mundo
    orig = db.list_history

    def com_decl(*a, **k):
        r = orig(*a, **k)
        for i in (i for i in r["items"] if i["categoria"] != "água"):  # sob grupo visível o nome ficaria, e é o certo
            i["cash_declaration"] = {"alvo": "João da Silva", "nota": "CPF 123.456.789-00"}
        return r
    monkeypatch.setattr(db, "list_history", com_decl)
    url = f"/history/{m['uid']}/list"
    assert "cash_declaration" in _texto(m["c"].get(url, headers=m["dono"]))  # positivo: o sub-dict chegou à resposta
    assert _vazou(_texto(m["c"].get(url, headers=m["leitor"]))) == []


# Cada campo de nome: o item oculto o perde. Tirar um nome de CAMPOS_NOME deixa o caso vermelho.
_CAMPOS = ["descricao", "description", "descricao_original", "memo", "nota", "note", "notes", "mensagem", "message", "texto",
           "text", "raw_text", "observacao", "observacoes", "obs", "titulo", "title", "label", "alvo", "target",
           "contraparte", "counterparty", "counterpart", "payee", "payer", "merchant", "estabelecimento", "favorecido",
           "beneficiario", "pagador", "recebedor", "remetente", "destinatario", "nome", "name", "nome_contraparte",
           "cpf", "cnpj", "cpf_cnpj", "documento", "document", "document_number", "chave_pix", "pix_key", "chave",
           "arquivo", "filename", "file_name", "comprovante", "comprovante_nome", "receipt_name", "display_name",
           "counterparty_name", "payee_name", "merchant_name", "description_raw", "raw_description"]


@pytest.mark.parametrize("campo", _CAMPOS)
def test_cada_campo_de_nome_e_mascarado_no_item_oculto_e_fica_no_visivel(mundo, campo):
    uid = mundo["uid"]
    out = _leitor_mascara(uid, [{"categoria": "salários", campo: "João 123.456.789-00"},
                                {"categoria": "água", campo: "Sabesp"}, {"categoria": "salários", campo: None}])
    assert out == [{"categoria": "salários", campo: "salários"}, {"categoria": "água", campo: "Sabesp"},
                   {"categoria": "salários", campo: None}]


def test_herda_da_rota_por_categoria_oculta_e_visivel(mundo):
    uid = mundo["uid"]
    assert _leitor_mascara(uid, {"launches": [{"alvo": "João"}]}, herda="salários") == {"launches": [{"alvo": "salários"}]}
    assert _leitor_mascara(uid, {"launches": [{"alvo": "Sabesp"}]}, herda="água") == {"launches": [{"alvo": "Sabesp"}]}


# ── 8. assinaturas (Open Finance), projeção e busca do histórico ──

def test_assinaturas_do_open_finance_nao_mostra_o_comerciante_ao_leitor(mundo):
    from _apoio_assinaturas import conta, mensais, rp, semeia
    m = mundo
    txs = mensais("mf", [-900.0] * 3, desc="MARIA FAXINEIRA CPF 123.456.789-00", ultima=date.today().replace(day=5))
    semeia(m["uid"], [conta("acc-mf", txs)], [rp("MARIA FAXINEIRA CPF 123.456.789-00", -900.0, txs)])
    dono = _texto(m["c"].get("/api/v2/assinaturas", headers=m["dono"]))
    assert "MARIA FAXINEIRA" in dono  # positivo: sem o nome no dono o teste não mede nada
    leitor = _texto(m["c"].get("/api/v2/assinaturas", headers=m["leitor"]))
    assert "MARIA" not in leitor.upper() and "123.456" not in leitor and "FAXINEIRA" not in leitor.upper()
    assert "900" in leitor  # o valor fica


def test_projecao_dono_ve_o_nome_e_leitor_nao(mundo, monkeypatch):
    """`cashflow.project` hoje não devolve `nome` (a rota sai limpa nos dois lados); o teste injeta o que um compromisso
    nomeado traria e mede a fiação do `neutralizar`: dono vê, leitor recebe o tipo."""
    from core.services import cashflow
    m = mundo
    fake = {"projetado": 1.0, "compromissos": [{"nome": "Maria faxineira", "fonte": "gasto_recorrente", "valor": 900.0},
                                              {"nome": "Mensalidade Maria", "fonte": "receita_recorrente", "valor": 450.0}]}
    monkeypatch.setattr(cashflow, "project", lambda *a, **k: fake)
    url = f"/recurring-bills/{m['uid']}/projection?date=2027-01-15"
    dono = _texto(m["c"].get(url, headers=m["dono"]))
    assert "Maria faxineira" in dono and "Mensalidade Maria" in dono
    leitor = m["c"].get(url, headers=m["leitor"]).json()["projection"]
    assert [c["nome"] for c in leitor["compromissos"]] == ["Gasto fixo", "Receita fixa"] and _vazou(json.dumps(leitor)) == []
    assert leitor["compromissos"][0]["valor"] == 900.0


def test_history_list_ignora_q_do_leitor_e_filtra_para_o_dono(mundo):
    m = mundo
    u = f"/history/{m['uid']}/list"
    sem_q = m["c"].get(u, headers=m["leitor"]).json()
    com_q = m["c"].get(u + "?q=Sabesp", headers=m["leitor"]).json()
    assert com_q == sem_q and len(json.dumps(sem_q)) > 200  # a busca por texto livre não filtra nem conta
    dono_q = m["c"].get(u + "?q=Sabesp", headers=m["dono"]).json()
    assert dono_q != m["c"].get(u, headers=m["dono"]).json()  # positivo: para o dono ela filtra


def test_pessoa_fisica_e_categoria_renomeada_ficam_ocultas(mundo):
    """Tester passada 2: "serviços pf"/honorários pagam pessoa; nome próprio na categoria não vira rótulo."""
    uid = mundo["uid"]
    dado = {"itens": [{"categoria": "serviços pf", "descricao": "Joaquim CPF 123"},
                      {"categoria": "honorários advocatícios", "descricao": "Dr. Joaquim"},
                      {"categoria": "Salário Joaquim", "descricao": "Joaquim"},
                      {"categoria": "serviços pj", "descricao": "Elevadores SA"}]}
    assert [i["descricao"] for i in _leitor_mascara(uid, dado)["itens"]] == [
        "serviços pf", "honorários advocatícios", "Oculto", "Elevadores SA"]


def test_categoria_renomeada_em_grupo_visivel_fecha(mundo):
    uid = mundo["uid"]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into user_categories (user_id, name, grupo) values (%s, 'salario Joaquim', 'funcionais')",
                    (uid,))
        conn.commit()
    item = _leitor_mascara(uid, {"itens": [{"categoria": "salario Joaquim", "descricao": "Joaquim"}]})["itens"][0]
    assert item["descricao"] == "Oculto"
