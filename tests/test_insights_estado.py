"""PL-06 A2: "dispensado" e "útil" dos insights persistidos por usuário (`insight_estado`), pelo monólito real
(sessão e CSRF reais, Postgres). O LLM fica atrás da guarda `llm` do A1 (os insights não o chamam, D12)."""
import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro
from core import ai_patterns
from db.insight_estado import registrar
from db.privacy import build_user_export_zip
from test_insights_contrato import _conta_of, _mes_menos, _orcamento_apertando, _usuario, llm  # noqa: F401
from tests.test_api_v2_perfil import cliente

MES = f"{date.today():%Y-%m}"
MERCADO = f"budget_warning:mercado:{MES}"


def _get(uid):
    r = cliente(uid)[0].get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return r.json()


def _put(sessao_uid, path_uid=None, com_csrf=True, **corpo):
    c, h = cliente(sessao_uid, com_csrf)
    return c.put(f"/insights/{path_uid or sessao_uid}/estado", json=corpo, headers=h)


def _linhas(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from insight_estado where user_id = %s order by chave", (uid,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return rows


def _chaves(uid):
    return [i["key"] for i in _get(uid)["insights"]]


def _plus_com_mercado():
    uid = _usuario("pro")  # Plus
    _orcamento_apertando(uid)  # 90 %: warning
    assert _chaves(uid) == [MERCADO]
    return uid


# ── 1 e 2. dispensa persiste, não ressuscita, e desfaz ───────────────────────────

def test_dispensa_persiste_nao_ressuscita_e_desfaz(llm, monkeypatch):
    uid, vizinho = _plus_com_mercado(), _plus_com_mercado()
    r = _put(uid, chave=MERCADO, dispensado=True, severidade="warning")
    assert r.status_code == 200 and r.json() == {"ok": True, "chave": MERCADO, "dispensado": True, "util": False}
    g = _get(uid)
    assert (g["insights"], g["motivo"]) == ([], "dispensados")
    for _ in range(2):
        assert ai_patterns.generate_ai_insights(uid, force=True) == []
    assert len(_linhas(uid)) == 1
    monkeypatch.setattr(ai_patterns, "_PROMPT_VERSION_INSIGHTS", 99)  # kind novo: regenera
    assert _get(uid)["insights"] == [] and ai_patterns.ler_insights(uid) == []
    assert _chaves(vizinho) == [MERCADO]  # positivo: quem não dispensou vê
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{vizinho}")
    for quem, n in ((uid, 0), (vizinho, 1)):
        c = TestClient(dashboard.app)
        c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
        assert len(c.get("/api/v2/insights").json()["insights"]) == n
    # 2. desfazer (DP4): volta e as três colunas da dispensa ficam nulas
    r = _put(uid, chave=MERCADO, dispensado=False)
    assert r.status_code == 200 and r.json()["dispensado"] is False
    assert _chaves(uid) == [MERCADO]
    (linha,) = _linhas(uid)
    assert (linha["dispensado_em"], linha["dispensado_severidade"], linha["dispensado_valor"]) == (None, None, None)
    assert llm == []


# ── 3 e 4a. volta se piorar; a referência é a do último cálculo ──────────────────

def test_volta_se_piorar_e_a_referencia_e_a_do_ultimo_calculo(llm):
    uid = _plus_com_mercado()
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200
    db.add_launch_and_update_balance(uid, "despesa", 15, "Mercado X", None, categoria="mercado")  # 105 %
    (it,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert (it["key"], it["severity"]) == (MERCADO, "critical")  # mesma chave, mais grave: volta
    (linha,) = _linhas(uid)
    assert (linha["severidade"], linha["dispensado_severidade"]) == ("critical", "warning")  # 4a: 1 linha
    # re-dispensar o critical que já foi warning: a referência é a do último cálculo (T1)
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="critical").status_code == 200
    assert _get(uid)["insights"] == [] and _linhas(uid)[0]["dispensado_severidade"] == "critical"
    # positivo de não-piora: melhora para warning (105/120) continua escondida
    db.upsert_budget(uid, "mercado", 120)
    assert ai_patterns.generate_ai_insights(uid, force=True) == []
    (linha,) = _linhas(uid)
    assert (linha["severidade"], linha["dispensado_severidade"]) == ("warning", "critical")
    assert llm == []


# ── 3e. a referência é o que o usuário VIU, não o que outro cálculo gravou depois ──

def test_piora_gravada_antes_do_clique_continua_visivel(llm):
    uid = _plus_com_mercado()  # a tela mostra warning (90 %)
    db.add_launch_and_update_balance(uid, "despesa", 20, "Mercado X", None, categoria="mercado")  # 110 %
    ai_patterns.generate_ai_insights(uid, force=True)  # outra aba recalcula: a linha já está critical
    assert _linhas(uid)[0]["severidade"] == "critical"
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200  # o X do warning
    assert [(i["key"], i["severity"]) for i in _get(uid)["insights"]] == [(MERCADO, "critical")]
    assert _linhas(uid)[0]["dispensado_severidade"] == "warning"
    # positivo: dispensar o que a linha tem esconde
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="critical").status_code == 200
    assert _get(uid)["insights"] == []
    assert llm == []


def test_dispensar_grava_o_que_viu_mesmo_mais_grave_que_a_linha(llm):
    """A regra é "dispensei o que vi", sem teto da linha: quem manda `critical` com a linha em `warning` não revê o
    fato ao chegar a `critical`, porque não piorou além do visto."""
    uid = _plus_com_mercado()  # a linha está em warning
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="critical").status_code == 200
    assert _linhas(uid)[0]["dispensado_severidade"] == "critical"
    db.add_launch_and_update_balance(uid, "despesa", 20, "Mercado X", None, categoria="mercado")  # 110 %
    assert ai_patterns.generate_ai_insights(uid, force=True) == []
    assert _linhas(uid)[0]["severidade"] == "critical"
    assert llm == []


# ── 4b. dedupe do lote ───────────────────────────────────────────────────────────

def test_registrar_com_chave_repetida_grava_uma_linha_com_a_primeira():
    uid = _usuario("pro")
    item = {"key": "category_spike:x:2026-10", "type": "category_spike", "explicacao": {}}
    registrar(uid, [{**item, "severity": "critical"}, {**item, "severity": "warning"}])
    assert [(r["chave"], r["severidade"]) for r in _linhas(uid)] == [(item["key"], "critical")]


# ── 5. "útil" não mexe no feed ───────────────────────────────────────────────────

def test_util_e_so_metrica_e_convive_com_a_dispensa(llm):
    uid = _plus_com_mercado()
    _orcamento_apertando(uid, alvo="Bar", categoria="lazer")
    ai_patterns.generate_ai_insights(uid, force=True)
    antes = _get(uid)["insights"]
    assert len(antes) == 2
    r = _put(uid, chave=MERCADO, util=True)
    assert r.status_code == 200 and r.json() == {"ok": True, "chave": MERCADO, "dispensado": False, "util": True}
    assert _get(uid)["insights"] == antes  # a mesma lista, na mesma ordem
    util_em = next(r for r in _linhas(uid) if r["chave"] == MERCADO)["util_em"]
    assert util_em is not None
    _put(uid, chave=MERCADO, util=True)  # idempotente: não reescreve a data
    assert next(r for r in _linhas(uid) if r["chave"] == MERCADO)["util_em"] == util_em
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").json() == {"ok": True, "chave": MERCADO, "dispensado": True,
                                                                  "util": True}
    assert _put(uid, chave=MERCADO, util=False).json()["util"] is False
    linha = next(r for r in _linhas(uid) if r["chave"] == MERCADO)
    assert linha["util_em"] is None and linha["dispensado_em"] is not None
    assert llm == []


# ── 6 e 11. isolamento e export ──────────────────────────────────────────────────

def test_isolamento_entre_usuarios_com_a_mesma_chave(llm):
    a, b = _plus_com_mercado(), _plus_com_mercado()
    _orcamento_apertando(b, alvo="Loja", categoria="segredob")
    ai_patterns.generate_ai_insights(b, force=True)
    so_de_b = f"budget_warning:segredob:{MES}"
    assert _put(b, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200
    assert _chaves(a) == [MERCADO]  # a mesma chave textual, outro dono
    linhas_b = _linhas(b)
    r = _put(a, chave=so_de_b, dispensado=True, severidade="warning")
    assert r.status_code == 404 and r.json() == _put(a, chave="nao:existe:2026-10", dispensado=True, severidade="warning").json()
    assert _put(a, path_uid=b, chave=MERCADO, dispensado=False).status_code == 403  # sessão de A no caminho de B
    assert _linhas(b) == linhas_b
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(a))).read("dados.json"))["dados"]
    assert [x["chave"] for x in dados["insights_estado"]] == [MERCADO]
    assert "segredob" not in json.dumps(dados, default=str)
    assert llm == []


# ── 7. gate, CSRF e corpo ────────────────────────────────────────────────────────

def _conta_marcar(monkeypatch):
    import db.insight_estado as mod
    chamadas, real = [], mod.marcar
    monkeypatch.setattr(mod, "marcar", lambda *a, **k: chamadas.append(a) or real(*a, **k))
    return chamadas


def test_essencial_e_sem_csrf_nao_tocam_a_tabela(llm, monkeypatch):
    uid = _plus_com_mercado()
    linhas = _linhas(uid)
    chamadas = _conta_marcar(monkeypatch)
    assert _put(uid, com_csrf=False, chave=MERCADO, dispensado=True, severidade="warning").status_code == 403
    promote_to_pro(uid, "essencial")
    r = _put(uid, chave=MERCADO, dispensado=True, severidade="warning")
    assert r.status_code == 403 and r.json()["detail"]["error"] == "pro_required"
    assert chamadas == [] and _linhas(uid) == linhas
    promote_to_pro(uid, "pro")  # positivo: o mesmo pedido passa
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200 and len(chamadas) == 1


@pytest.mark.parametrize("corpo", [{}, {"chave": MERCADO}, {"chave": MERCADO, "dispensado": "sim"},
                                   {"chave": "x" * 161, "dispensado": True, "severidade": "warning"},
                                   {"chave": "a\x00b", "dispensado": True, "severidade": "warning"},
                                   {"chave": "", "util": True}, {"chave": MERCADO, "dispensado": True},
                                   {"chave": MERCADO, "dispensado": True, "severidade": "grave"},
                                   {"chave": MERCADO, "util": True, "extra": 1},
                                   {"chave": MERCADO, "dispensado": True, "severidade": "warning", "valor": 1e13}])
def test_corpo_invalido_e_422(llm, monkeypatch, corpo):
    uid = _plus_com_mercado()
    chamadas = _conta_marcar(monkeypatch)
    c, h = cliente(uid)
    assert c.put(f"/insights/{uid}/estado", json=corpo, headers=h).status_code == 422
    assert chamadas == []


# ── 8. fail-open sem laço ────────────────────────────────────────────────────────

def _quebra_o_banco_do_estado(monkeypatch):
    import db.insight_estado as mod

    def quebra(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(mod, "get_conn", quebra)


def test_falha_no_registro_nao_derruba_o_feed_nem_recalcula(llm, monkeypatch):
    import db as pacote
    uid = _usuario("pro")
    _orcamento_apertando(uid)
    calculos, real = [], pacote.compute_active_insights
    monkeypatch.setattr(pacote, "compute_active_insights", lambda u: calculos.append(u) or real(u))
    _quebra_o_banco_do_estado(monkeypatch)
    assert _chaves(uid) == [MERCADO] and _chaves(uid) == [MERCADO]
    assert calculos == [uid]  # o cache foi gravado: o 2º GET não recalcula
    monkeypatch.undo()
    assert _linhas(uid) == [] and _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 404


def test_falha_na_leitura_das_dispensas_mostra_sem_filtro(llm, monkeypatch):
    uid = _plus_com_mercado()
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200
    _quebra_o_banco_do_estado(monkeypatch)
    assert _chaves(uid) == [MERCADO]


# ── 9. período novo volta ────────────────────────────────────────────────────────

def test_dispensa_do_mes_anterior_nao_esconde_o_mes_corrente(llm):
    uid = _usuario("pro")
    _orcamento_apertando(uid)
    anterior = f"budget_warning:mercado:{_mes_menos(date.today(), 1):%Y-%m}"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into insight_estado (user_id, chave, tipo, severidade, dispensado_em,"
                    " dispensado_severidade) values (%s, %s, 'budget_warning', 'critical', now(), 'critical')",
                    (uid, anterior))
        conn.commit()
    assert _chaves(uid) == [MERCADO]


# ── 12. a conversa pelo runner ───────────────────────────────────────────────────

def _card_piggy(monkeypatch, uid, texto):
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, texto)
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    return bloco["abas"][0]["recursos"]["insights"]


def test_conversa_dispensado_some_do_card_e_volta_ao_estourar(monkeypatch, llm):
    uid = _plus_com_mercado()  # aquece o cache pela rota
    assert _put(uid, chave=MERCADO, dispensado=True, severidade="warning").status_code == 200
    card = _card_piggy(monkeypatch, uid, "o que você notou?")
    assert (card["insights"], card["motivo"]) == ([], "dispensados")
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "categoria": "mercado"})))
    r2, ev2, _ = executar(uid, "gastei 50 no mercado")
    assert "Despesa registrada" in r2.reply and not [e for e in ev2 if e["tipo"] == "bloco"]
    ai_patterns.generate_ai_insights(uid, force=True)
    card = _card_piggy(monkeypatch, uid, "e agora, o que você notou?")
    assert [(i["key"], i["severity"]) for i in card["insights"]] == [(MERCADO, "critical")]
    assert card["motivo"] is None and llm == []
