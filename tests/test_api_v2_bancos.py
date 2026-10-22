"""`GET /api/v2/bancos` e `/api/app/bancos` pelo monólito real: sessão, banco real (CLAUDE.md §3).

A regra de dinheiro NÃO é nova (`db/contas_hoje.py` e `db/investido.py`, com os testes deles): aqui,
o que a rota decide — uma entrada por CONEXÃO, `null` ≠ zero, o topo fechando com os bancos e com
`/contas` e `/investido`, o isolamento, o plano sem `investments` e as duas portas.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import bancos as rota
from api.v2.bancos import Bancos
from conftest import usuario_pagante
from db import bancos, investido
from db.connection import get_conn
from test_api_app_sessao import cliente, completo
from tests._patrimonio_helpers import AGORA, conexao, conta, horas_atras, posicao, q

D = Decimal
ROTA = "/api/v2/bancos"


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    u = completo(usuario_pagante())
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    return u


def get(quem, rota_=ROTA):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return c.get(rota_)


def ok(quem, rota_=ROTA) -> dict:
    r = get(quem, rota_)
    assert r.status_code == 200, r.text
    return r.json()


def por_id(corpo) -> dict:
    return {b["conexao_id"]: b for b in corpo["bancos"]}


def cartao(cid, pid="card-1"):
    q("""insert into open_finance_accounts (connection_id, provider_account_id, name, type,
             currency, balance, raw, updated_at)
         values (%s, %s, 'Cartão', 'CREDIT', 'BRL', 900, %s, %s)""",
      (cid, pid, Jsonb({"currencyCode": "BRL", "balance": 900}), AGORA))


def sem_saldo_na_conta(cid, pid):
    """O sync grava 0 na coluna quando o banco omite o saldo; só o `raw` diz que o 0 não veio dele."""
    conta(cid, pid, 0)
    q("update open_finance_accounts set raw = raw - 'balance' where connection_id=%s", (cid,))


def _mix(u) -> dict:
    """Dois "Nubank", um Inter com saldo ausente e dólar, um banco vazio, um pausado, um cartão."""
    c = {}
    c["nu1"] = conexao(u, f"i1-{u}", banco="Nubank")
    conta(c["nu1"], "a1", "1000")
    posicao(c["nu1"], "p1", "4000", subtipo="CDB")
    cartao(c["nu1"])
    c["nu2"] = conexao(u, f"i2-{u}", banco="Nubank")
    conta(c["nu2"], "a2", "250.50")
    posicao(c["nu2"], "p2", "100.25", tipo="EQUITY", subtipo="STOCK")
    c["inter"] = conexao(u, f"i3-{u}", banco="Inter")
    conta(c["inter"], "a3", "700")
    conta(c["inter"], "a4", "50", moeda="USD", code="USD")
    posicao(c["inter"], "p3", None, tipo="MUTUAL_FUND")
    c["xp"] = conexao(u, f"i4-{u}", banco="XP")
    c["itau"] = conexao(u, f"i5-{u}", status="PAUSED", banco="Itaú")
    conta(c["itau"], "a5", "900")
    posicao(c["itau"], "p5", "9000")
    return c


# ── (a) uma entrada por CONEXÃO ───────────────────────────────────────────────

def test_duas_conexoes_com_o_mesmo_nome_sao_duas_entradas_com_valores_proprios(uid):
    c = _mix(uid)
    corpo = ok(uid)
    assert Bancos.model_validate(corpo).model_dump(mode="json") == corpo
    b = por_id(corpo)
    assert len(corpo["bancos"]) == 4  # o `xp`, sem conta, cartão nem posição, não é banco (B)
    assert (D(b[c["nu1"]]["saldo_conta"]), D(b[c["nu1"]]["investido"]), D(b[c["nu1"]]["total"])) == (
        D("1000"), D("4000"), D("5000"))
    assert (D(b[c["nu2"]]["saldo_conta"]), D(b[c["nu2"]]["investido"]), D(b[c["nu2"]]["total"])) == (
        D("250.50"), D("100.25"), D("350.75"))
    assert D(corpo["total"]) == D("5000") + D("350.75") + D("700")  # Nubank 1 + Nubank 2 + Inter (só o saldo)


def test_contagens_por_conexao_sem_duplicata_de_reconexao(uid):
    c = _mix(uid)
    b = por_id(ok(uid))
    assert c["xp"] not in b  # sem nenhuma linha no espelho não há o que mostrar (a conexão (0,0,0) sumiu)
    assert [(b[c[k]]["contas"], b[c[k]]["cartoes"], b[c[k]]["investimentos"])
            for k in ("nu1", "nu2", "inter", "itau")] == [(1, 1, 1), (1, 0, 1), (2, 0, 1), (1, 0, 1)]
    # Reconexão: a MESMA identidade do provedor numa conexão mais nova conta só na nova.
    novo = conexao(uid, f"i6-{uid}", banco="Nubank")
    conta(novo, "a1", "1000")
    posicao(novo, "p1", "4000")
    cartao(novo)
    b = por_id(ok(uid))
    # A velha migrou tudo para a nova e ficou sem linha: sai de `bancos` (antes: entrada fantasma (0,0,0) null).
    assert c["nu1"] not in b and (b[novo]["contas"], b[novo]["cartoes"], b[novo]["investimentos"]) == (1, 1, 1)
    assert D(b[novo]["investido"]) == D("4000")


# ── (b) null ≠ zero ───────────────────────────────────────────────────────────

def test_conexao_viva_sem_linha_no_espelho_e_null_e_nao_zero(uid):
    c = _mix(uid)
    assert c["xp"] not in por_id(ok(uid))  # recém-conectada, sem dado: não vem em `bancos` (o app mostra "—")
    conta(c["xp"], "a6", "300")  # só saldo: o total é o saldo, e o investido continua desconhecido
    xp = por_id(ok(uid))[c["xp"]]
    assert xp["investido"] is None and D(xp["total"]) == D("300") and D(xp["saldo_conta"]) == D("300")


def test_zero_conhecido_resgatou_tudo_e_r0_nao_null(uid):
    """Positivo do `null`: a posição resgatada de conexão viva é R$ 0 conhecido."""
    c = conexao(uid, f"i-{uid}", banco="Nubank")
    posicao(c, "p1", "500", status="TOTAL_WITHDRAWAL")
    b = por_id(ok(uid))[c]
    assert D(b["investido"]) == 0 and b["total"] is not None and b["investimentos"] == 1


def test_conexao_pausada_e_null_com_o_motivo(uid):
    c = _mix(uid)
    itau = por_id(ok(uid))[c["itau"]]
    assert (itau["saldo_conta"], itau["investido"], itau["total"]) == (None, None, None)
    assert itau["motivos"] == ["conexao_pausada"] and (itau["contas"], itau["investimentos"]) == (1, 1)


def test_so_pausada_e_so_viva_sem_espelho_caem_no_ramo_sem_nenhum_banco_lido(uid):
    """O `calcular` devolve cedo quando nenhuma conexão viva tem posição no espelho: `por_conexao` vale ali também."""
    pausada = conexao(uid, f"p-{uid}", status="PAUSED", banco="Itaú")
    conta(pausada, "a1", "900")
    posicao(pausada, "p1", "9000")
    vazia = conexao(uid, f"v-{uid}", banco="XP")  # sem linha: fora de `bancos`
    corpo = ok(uid)
    b = por_id(corpo)
    assert vazia not in b
    assert (b[pausada]["investido"], b[pausada]["total"], b[pausada]["motivos"]) == (None, None, ["conexao_pausada"])
    assert corpo["total"] is None and corpo["motivos"] == ["conexao_pausada"]


def test_outra_moeda_fica_fora_com_o_motivo_e_nao_anula_o_resto(uid):
    c = _mix(uid)
    corpo = ok(uid)
    inter = por_id(corpo)[c["inter"]]
    assert D(inter["saldo_conta"]) == D("700") and "outra_moeda" in inter["motivos"]  # o USD (50) não entra
    so_usd = conexao(uid, f"usd-{uid}", banco="Wise")
    conta(so_usd, "a7", "50", moeda="USD", code="USD")
    w = por_id(ok(uid))[so_usd]
    assert w["saldo_conta"] is None and w["total"] is None and w["motivos"] == ["outra_moeda"] and w["contas"] == 1


def test_conexao_so_com_cartao_aparece_viva_e_pausada(uid):
    viva = conexao(uid, f"v-{uid}", banco="Nubank")
    cartao(viva, "card-v")
    pausada = conexao(uid, f"p-{uid}", status="PAUSED", banco="Itaú")
    cartao(pausada, "card-p")
    b = por_id(ok(uid))
    v, p = b[viva], b[pausada]  # sem conta nem posição, mas com cartão: é banco
    assert (v["cartoes"], v["contas"], v["investimentos"]) == (1, 0, 0)
    assert (v["saldo_conta"], v["investido"], v["total"], v["motivos"]) == (None, None, None, [])
    assert (p["cartoes"], p["contas"], p["investimentos"]) == (1, 0, 0)
    assert (p["saldo_conta"], p["investido"], p["total"], p["motivos"]) == (None, None, None, ["conexao_pausada"])


def test_pausada_so_com_posicao_tem_o_motivo(uid):
    p = conexao(uid, f"p-{uid}", status="PAUSED", banco="Itaú")
    posicao(p, "p1", "9000")
    b = por_id(ok(uid))[p]
    assert (b["investido"], b["total"], b["motivos"], b["investimentos"]) == (None, None, ["conexao_pausada"], 1)


def test_posicao_em_dolar_com_conta_em_reais_leva_outra_moeda_da_posicao(uid):
    c = conexao(uid, f"i-{uid}", banco="Inter")
    conta(c, "a1", "100")  # BRL: o `outra_moeda` não pode vir da conta
    posicao(c, "p1", "50", moeda="USD", code="USD")
    b = por_id(ok(uid))[c]
    assert (D(b["saldo_conta"]), b["investido"], D(b["total"]), b["investimentos"]) == (D(100), None, D(100), 1)
    assert b["motivos"] == ["outra_moeda"]


def test_saldo_ausente_e_null_nas_duas_partes(uid):
    c = conexao(uid, f"i-{uid}", banco="Inter")
    sem_saldo_na_conta(c, "a1")
    posicao(c, "p1", None)
    b = por_id(ok(uid))[c]
    assert (b["saldo_conta"], b["investido"], b["total"]) == (None, None, None)
    assert b["motivos"] == ["saldo_ausente"]
    # Positivo: uma conta com saldo e outra sem — o número fica, com o motivo.
    conta(c, "a2", "40")
    posicao(c, "p2", "10")
    b = por_id(ok(uid))[c]
    assert (D(b["saldo_conta"]), D(b["investido"]), D(b["total"])) == (D(40), D(10), D(50))
    assert b["motivos"] == ["saldo_ausente"]


def test_nenhum_banco_o_topo_e_null_e_a_lista_vazia(uid):
    assert ok(uid) == {"total": None, "motivos": [], "bancos": []}


def test_so_aparece_a_conexao_com_alguma_linha_e_o_topo_sai_so_dos_listados(uid):
    """B: DELETED sem linhas, reconexão que migrou e viva recém-conectada não são banco, e não sujam o topo."""
    viva = conexao(uid, f"v-{uid}", banco="Nubank")
    conta(viva, "a1", "100")
    apagada = conexao(uid, f"d-{uid}", status="DELETED", banco="Velho")
    conexao(uid, f"n-{uid}", banco="Novo")
    corpo = ok(uid)
    assert [b["conexao_id"] for b in corpo["bancos"]] == [viva]
    assert D(corpo["total"]) == D("100") and "conexao_pausada" not in corpo["motivos"] and apagada not in por_id(corpo)
    # Positivo: pausada/apagada COM linha continua listada, null e com o motivo (o teste exige que ela apareça).
    pausada = conexao(uid, f"p-{uid}", status="PAUSED", banco="Itaú")
    conta(pausada, "a2", "900")
    corpo = ok(uid)
    assert pausada in por_id(corpo)
    p = por_id(corpo)[pausada]
    assert (p["saldo_conta"], p["total"], p["motivos"], p["contas"]) == (None, None, ["conexao_pausada"], 1)
    assert D(corpo["total"]) == D("100") and corpo["motivos"] == ["conexao_pausada"]


def test_ordem_dos_bancos_e_por_conexao_id_crescente(uid):
    """Criadas fora da ordem alfabética: ordenar por nome (ou inverter) reprova."""
    ids = []
    for nome in ("Nubank", "Inter", "Banco do Brasil", "Itaú"):
        c = conexao(uid, f"{nome}-{uid}", banco=nome)
        conta(c, f"a-{nome}", "10")  # identidades distintas: não é reconexão
        ids.append(c)
    assert [b["conexao_id"] for b in ok(uid)["bancos"]] == ids


def test_soma_em_decimal_e_nao_em_float(uid):
    c = conexao(uid, f"i-{uid}", banco="Nubank")
    conta(c, "a1", "0.10")
    conta(c, "a2", "0.20")
    b = por_id(ok(uid))[c]
    assert b["saldo_conta"] == "0.30" and b["total"] == "0.30"  # float daria 0.30000000000000004


def test_sincronizada_ha_49h_e_banco_desatualizado_e_ha_47h_nao(uid):
    """O limite é 48 h (`BANCO_VELHO`): 49 h/47 h (a margem de 1 h cobre o relógio entre `AGORA` e a rota)."""
    velho = conexao(uid, f"v-{uid}", banco="Velho", sync=horas_atras(49))
    conta(velho, "a1", "10")
    novo = conexao(uid, f"n-{uid}", banco="Novo", sync=horas_atras(47))
    conta(novo, "a2", "10")
    corpo = ok(uid)
    b = por_id(corpo)
    assert b[velho]["motivos"] == ["banco_desatualizado"] and b[novo]["motivos"] == []
    assert corpo["motivos"] == ["banco_desatualizado"] and D(b[velho]["saldo_conta"]) == D(10)  # o número fica


def test_posicao_com_tres_casas_entra_em_centavos(uid):
    c = conexao(uid, f"i-{uid}", banco="Nubank")
    posicao(c, "p1", "10.004")
    posicao(c, "p2", "10.004")
    assert D(por_id(ok(uid))[c]["investido"]) == D("20.00")  # sem o quantize: 20.008


def test_conexao_sem_sync_nao_prova_nada_investido_null_igual_ao_de_investido(uid):
    """A: o ramo cedo de `calcular` devolve `total: null`; o `investido` por conexão tem de acompanhar."""
    sem = conexao(uid, f"s-{uid}", sync=None, banco="Nubank")
    conta(sem, "a1", "10")
    posicao(sem, "p1", "4000")
    resg = conexao(uid, f"r-{uid}", sync=None, banco="Inter")
    posicao(resg, "p2", "500", status="TOTAL_WITHDRAWAL")
    inv, corpo = ok(uid, "/api/v2/investido"), ok(uid)
    assert inv["total"] is None and inv["por_banco"] == []
    b = por_id(corpo)
    assert b[sem]["investido"] is None and D(b[sem]["total"]) == D(10) and "banco_desatualizado" in b[sem]["motivos"]
    assert b[resg]["investido"] is None and "banco_desatualizado" in b[resg]["motivos"]
    assert all(x["investido"] is None for x in corpo["bancos"])  # Σ investido == calcular()["total"] (null)
    # Positivo: sincronizada, o valor aparece e fecha com `/investido`.
    ok_ = conexao(uid, f"o-{uid}", banco="XP")
    posicao(ok_, "p3", "700")
    inv, b = ok(uid, "/api/v2/investido"), por_id(ok(uid))
    # Com uma conexão sincronizada o critério é do conjunto: a sem sync entra com o valor, como no `total`.
    assert (D(b[ok_]["investido"]), D(b[sem]["investido"])) == (D(700), D(4000))
    assert sum(D(x["investido"]) for x in b.values() if x["investido"] is not None) == D(inv["total"])


def test_snapshot_escrita_commitada_entre_as_leituras_nao_aparece(uid, monkeypatch):
    """Repeatable read: o que outra conexão commita entre `contas_hoje.listar` e `investido.calcular` fica fora."""
    c = conexao(uid, f"i-{uid}", banco="Nubank")
    conta(c, "a1", "100")
    posicao(c, "p1", "1000")
    real = investido.calcular

    def com_escrita(*a, **k):
        posicao(c, "p-novo", "500")
        conta(c, "a-nova", "50")
        return real(*a, **k)

    monkeypatch.setattr(investido, "calcular", com_escrita)
    b = por_id(ok(uid))[c]
    assert (D(b["investido"]), D(b["saldo_conta"]), b["investimentos"], b["contas"]) == (D(1000), D(100), 1, 1)
    monkeypatch.setattr(investido, "calcular", real)
    b = por_id(ok(uid))[c]  # positivo: a escrita existe, e a próxima leitura a vê
    assert (D(b["investido"]), D(b["saldo_conta"])) == (D(1500), D(150))


# ── (c) invariantes ───────────────────────────────────────────────────────────

def test_topo_fecha_com_os_bancos_com_investido_e_com_contas(uid):
    _mix(uid)
    db.set_balance(uid, D("123.45"))  # a Carteira fica fora dos bancos
    corpo, contas, inv = ok(uid), ok(uid, "/api/v2/contas"), investido.ler(uid)
    assert D(corpo["total"]) == sum(D(b["total"]) for b in corpo["bancos"] if b["total"] is not None)
    assert sum(D(b["investido"]) for b in corpo["bancos"] if b["investido"] is not None) == inv["total"]
    assert (sum(D(b["saldo_conta"]) for b in corpo["bancos"] if b["saldo_conta"] is not None)
            == D(contas["total"]) - D(contas["carteira"]["saldo"]))
    # Os dois lados com a mesma soma: saldo + investido, sem a Carteira, o pausado e o dólar.
    assert D(corpo["total"]) == D(contas["total"]) - D(contas["carteira"]["saldo"]) + inv["total"]


# ── (d) isolamento ────────────────────────────────────────────────────────────

def test_a_nao_ve_as_conexoes_de_b_e_ve_as_dele(uid, monkeypatch):
    b = completo(usuario_pagante())
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{b}")
    ca = conexao(uid, f"a-{uid}", banco="Nubank")
    conta(ca, "acc-x", "100")
    posicao(ca, "inv-x", "1000")
    cartao(ca, "card-x")
    cb = conexao(b, f"b-{b}", banco="SEGREDO-B")  # a MESMA identidade do provedor, noutro usuário
    conta(cb, "acc-x", "5000")
    posicao(cb, "inv-x", "9999")
    cartao(cb, "card-x")  # idem: a conexão MAIS NOVA vence o `distinct on` se a consulta não filtrar o dono
    de_a, de_b = ok(uid), ok(b)
    assert [x["conexao_id"] for x in de_a["bancos"]] == [ca] and D(de_a["total"]) == D("1100")  # positivo: A vê o dele
    assert [x["conexao_id"] for x in de_b["bancos"]] == [cb] and D(de_b["total"]) == D("14999")
    assert (de_a["bancos"][0]["cartoes"], de_b["bancos"][0]["cartoes"]) == (1, 1)


def test_ordem_dos_motivos_e_a_do_contrato_e_nao_a_alfabetica(uid):
    """Lista EXATA com 2+ motivos: `sorted` trocaria `saldo_ausente`/`outra_moeda`."""
    c = _mix(uid)
    corpo = ok(uid)
    assert por_id(corpo)[c["inter"]]["motivos"] == ["saldo_ausente", "outra_moeda"]
    assert corpo["motivos"] == ["saldo_ausente", "outra_moeda", "conexao_pausada"]


def test_motivos_de_investido_cabem_no_literal_da_rota():
    """Motivo novo em `investido` sem entrar em `contas_hoje.MOTIVOS_CONTA` viraria 500 no Literal do Pydantic."""
    assert set(investido.MOTIVOS_BANCO) <= set(bancos.MOTIVOS)


# ── (e) plano sem `investments` ───────────────────────────────────────────────

def test_plano_sem_investimentos_e_null_com_o_motivo_e_o_saldo_continua(uid, monkeypatch):
    c = _mix(uid)
    monkeypatch.setattr(rota, "plan_gate_ok", lambda u, f: f != "investments")
    monkeypatch.setattr(investido, "calcular", lambda *a, **k: pytest.fail("leu o investido sem o recurso"))
    corpo = ok(uid)
    nu = por_id(corpo)[c["nu1"]]
    assert nu["investido"] is None and nu["investimentos"] is None and "plano_sem_investimentos" in nu["motivos"]
    assert D(nu["saldo_conta"]) == D(nu["total"]) == D("1000")
    assert "plano_sem_investimentos" in corpo["motivos"]
    assert D(corpo["total"]) == D("1000") + D("250.50") + D("700")
    # Conexão sem conta (só cartão): aparece, e o motivo do plano vem dela (o estado dela se perde: limite declarado).
    so_cartao = conexao(uid, f"c-{uid}", banco="Santander")
    cartao(so_cartao, "card-s")
    sc = por_id(ok(uid))[so_cartao]
    assert (sc["cartoes"], sc["contas"], sc["investimentos"], sc["investido"]) == (1, 0, None, None)
    assert sc["motivos"] == ["plano_sem_investimentos"]


# ── (f) as duas portas ────────────────────────────────────────────────────────

def test_app_sem_onboarding_e_403_e_com_onboarding_devolve_o_mesmo_corpo(uid):
    _mix(uid)
    sem = usuario_pagante()
    r = cliente(sem).get("/api/app/bancos")
    assert r.status_code == 403 and r.json()["error"]["code"] == "open_finance_onboarding_required"
    r = cliente(uid).get("/api/app/bancos")
    assert r.status_code == 200 and r.json() == ok(uid)


def test_v2_fora_da_chave_do_painel_e_404_e_o_app_responde(uid, monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "1")
    r = get(uid)
    assert r.status_code == 404 and r.json()["error"]["code"] == "dashboard_v2_disabled"
    assert cliente(uid).get("/api/app/bancos").status_code == 200


# ── (g) o que já existia sai igual ────────────────────────────────────────────

LEGADO = json.loads((Path(__file__).parent / "fixtures" / "bancos_legado.json").read_text(encoding="utf-8"))


def test_investido_e_contas_saem_iguais_ao_de_antes_do_por_conexao(uid):
    """`LEGADO` foi gerado ANTES da mudança (mesma fixture `_mix`), sem nenhum campo novo."""
    from core.handlers.investido import carteira
    from core.services.ai_chat.tools.investments import _get_investment_summary

    _mix(uid)
    inv = ok(uid, "/api/v2/investido")
    assert inv == LEGADO["investido"] and set(inv) == {"total", "por_tipo", "por_banco", "motivos"}
    contas = ok(uid, "/api/v2/contas")
    sem_volateis = [{k: v for k, v in x.items() if k not in ("id", "sincronizado_em", "logo_proprio", "logo_url")} for x in contas["contas"]]  # logos: campo novo, tests/test_api_v2_contas_logo.py
    assert sem_volateis == LEGADO["contas"] and all("conexao_id" not in x for x in contas["contas"])
    assert {k: v for k, v in contas.items() if k != "contas"} == LEGADO["contas_topo"]
    assert _get_investment_summary(uid, {}) == LEGADO["tool"]
    assert carteira(uid).rsplit("\n\n", 1)[0] == LEGADO["whatsapp"]


def test_calcular_sem_o_parametro_nao_ganha_chave_e_com_ele_so_acrescenta(uid):
    _mix(uid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        puro = investido.calcular(cur, uid)
        com = investido.calcular(cur, uid, por_conexao=True)
        zero = investido.calcular(cur, uid, com_zero=True)
        ambos = investido.calcular(cur, uid, com_zero=True, por_conexao=True)
        conn.rollback()
    assert "por_conexao" not in puro and "por_conexao" not in zero
    assert {k: v for k, v in com.items() if k != "por_conexao"} == puro
    assert {k: v for k, v in ambos.items() if k != "por_conexao"} == zero


def test_calcular_com_por_conexao_e_com_zero_juntos_devolve_as_duas_chaves(uid):
    """Os dois ramos de `calcular`: o normal (`_mix`) e o cedo (só conexão pausada, sem nenhum banco lido)."""
    _mix(uid)
    pausado = completo(usuario_pagante())
    p = conexao(pausado, f"p-{pausado}", status="PAUSED", banco="Itaú")
    posicao(p, "p1", "9000")
    for quem, esperado in ((uid, "normal"), (pausado, "cedo")):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read, read only")
            so_cx = investido.calcular(cur, quem, por_conexao=True)
            ambos = investido.calcular(cur, quem, por_conexao=True, com_zero=True)
            conn.rollback()
        assert ambos["por_conexao"] == so_cx["por_conexao"] and ambos["por_conexao"], esperado
        assert "por_banco_com_zero" in ambos, esperado
    assert ambos["por_conexao"][p]["motivos"] == ["conexao_pausada"]


# ── fixture do app (contrato) ─────────────────────────────────────────────────

APP_BANCOS = json.loads((Path(__file__).parent / "fixtures" / "app_bancos.json").read_text(encoding="utf-8"))


def test_fixture_do_app_e_exatamente_o_que_o_pydantic_devolve():
    b = Bancos.model_validate(APP_BANCOS)
    assert json.loads(b.model_dump_json()) == APP_BANCOS
    assert set(APP_BANCOS) == set(Bancos.model_fields)
    assert all(set(x) == set(rota.Banco.model_fields) for x in APP_BANCOS["bancos"])
