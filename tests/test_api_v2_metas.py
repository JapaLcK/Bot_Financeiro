"""`/api/v2/metas` (`db/metas.py`) pelo monólito real: sessão, CSRF do pai, Postgres.

Meta = nome, alvo e a FONTE escolhida (tudo investido, um banco, uma conta); `atual` é o valor
de hoje da fonte: a mesma soma de `/investido` e `/contas`; a meta anula quando nada da
fonte ficou dentro do recorte. Reserva = alvo `meses` × custo
mensal das recorrências de saída do banco (cartão incluído).

Controles por mutação (relato do PR): casar a conta fora do recorte do usuário
(`CONTAS_BANCO_SQL` sem `c.user_id`) → vermelho em `test_mesmo_provider_account_id_*`; guardar
`a.id` em vez do `provider_account_id` → vermelho em `test_reconexao_*`; tirar o cartão do custo
→ vermelho em `test_custo_*`; ignorar a marca `ignorar` → vermelho em `test_custo_ignorar_*`;
tirar o índice `uq_metas_reserva` → vermelho em `test_corrida_*` e `test_merge_com_reserva_*`;
tirar `metas` de `TABELAS_QUE_AVISAM` → vermelho em `test_escrita_em_metas_avisa_o_dono`.
Os controles dos demais testes estão no relato do PR (um por conserto).
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from psycopg.types.json import Jsonb

import _apoio_assinaturas as ap
from api.v2.metas import Metas
from conftest import usuario_pagante
from db import metas as dbmetas
from db.connection import get_conn
from db.of_recurring import marcar
from db.privacy import build_user_export_zip, delete_user_data
from db.users import MergeRefused, merge_users
from tests._patrimonio_helpers import conexao, conta, horas_atras, posicao, q
from tests.test_api_v2_perfil import cliente
from db import ensure_user
from utils_date import today_tz
from utils_text import merchant_key

URL = "/api/v2/metas"
BASE = {"nome": "Viagem", "tipo": "meta", "alvo": "1000", "fonte": {"tipo": "investido"}}
RESERVA = {"nome": "Reserva", "tipo": "reserva", "alvo": None, "fonte": {"tipo": "investido"}}


@pytest.fixture
def libera(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")

    def _libera(*uids):
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(map(str, uids)))
        return uids
    return _libera


@pytest.fixture
def uid(libera):
    u = usuario_pagante()
    libera(u)
    return u


@pytest.fixture
def dois(libera):
    a, b = usuario_pagante(), usuario_pagante()
    libera(a, b)
    return a, b


def post(quem, rota, corpo, com_csrf=True):
    c, h = cliente(quem, com_csrf)
    return c.post(URL + rota, json=corpo, headers=h)


def ler(quem) -> dict:
    c, _ = cliente(quem)
    r = c.get(URL)
    assert r.status_code == 200, r.text
    assert Metas.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def get(quem, rota) -> dict:
    c, _ = cliente(quem)
    r = c.get(rota)
    assert r.status_code == 200, r.text
    return r.json()


def get_status(quem, rota) -> int:
    c, _ = cliente(quem)
    return c.get(rota).status_code


def cria(quem, **extra) -> int:
    r = post(quem, "", {**BASE, **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def meta(quem, mid) -> dict:
    return {m["id"]: m for m in ler(quem)["metas"]}[mid]


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def n_metas(quem) -> int:
    return q("select count(*) n from metas where user_id = %s", (quem,))["n"]


def banco(uid, nome="Nubank", **kw) -> int:
    return conexao(uid, f"item-{uuid.uuid4().hex[:12]}", banco=nome, **kw)


def conta_do_banco(uid, pid="acc-1", saldo="300", numero=None, nome="Nubank", **kw) -> int:
    cid = banco(uid, nome, **kw)
    aid = conta(cid, pid, saldo)
    if numero:
        q("update open_finance_accounts set raw = raw || %s where id = %s", (Jsonb({"number": numero}), aid))
    return cid


def contas_publicas(quem) -> list[dict]:
    return get(quem, "/api/v2/contas")["contas"]


def fonte_conta(quem, **filtro) -> dict:
    (c,) = [c for c in contas_publicas(quem) if all(c[k] == v for k, v in filtro.items())]
    return {"tipo": "conta", "conta": c["id"]}


# ── isolamento ──────────────────────────────────────────────────────────────

def test_b_nao_lista_nem_edita_nem_apaga_a_meta_de_a(dois):
    a, b = dois
    mid = cria(a)
    assert ler(b)["metas"] == []
    inexistente = 9_000_000_000_000_000_000
    for rota, corpo in (("/editar", {"nome": "invadida"}), ("/apagar", {})):
        de_a = post(b, rota, {"id": mid, **corpo})
        fantasma = post(b, rota, {"id": inexistente, **corpo})
        assert codigo(de_a) == codigo(fantasma) == (404, "meta_nao_encontrada")
        assert de_a.json() == fantasma.json()
    assert q("select nome from metas where id = %s", (mid,))["nome"] == "Viagem"
    assert [m["nome"] for m in ler(a)["metas"]] == ["Viagem"]


def test_b_nao_usa_a_conta_de_a_como_fonte(dois):
    a, b = dois
    conta_do_banco(a, "acc-a", "900")
    id_de_a = fonte_conta(a)["conta"]
    r = post(b, "", {**BASE, "fonte": {"tipo": "conta", "conta": id_de_a}})
    assert codigo(r) == (422, "validation_error")
    assert n_metas(b) == 0
    # o banco de A também não vale para B
    posicao(banco(a, "SEGREDO-A"), "inv-a", "500")
    r = post(b, "", {**BASE, "fonte": {"tipo": "banco", "banco": "SEGREDO-A"}})
    assert r.status_code == 422 and n_metas(b) == 0


def test_mesmo_provider_account_id_le_o_saldo_de_cada_um(dois):
    a, b = dois
    conta_do_banco(a, "acc-1", "100")
    conta_do_banco(b, "acc-1", "200")
    ma = cria(a, fonte=fonte_conta(a))
    mb = cria(b, fonte=fonte_conta(b))
    assert D(meta(a, ma)["atual"]) == 100 and D(meta(b, mb)["atual"]) == 200


# ── paridade com as telas e vazamento ───────────────────────────────────────

def test_atual_de_cada_fonte_e_o_numero_das_telas_e_nada_do_provedor_sai(uid):
    nu = banco(uid, "Nubank")
    posicao(nu, "inv-1", "1000.5")
    posicao(nu, "inv-2", "250")
    posicao(banco(uid, "XP"), "inv-3", "500")
    conta_do_banco(uid, "acc-SEGREDO", "300.25", numero="99887-1")
    inv, contas = get(uid, "/api/v2/investido"), contas_publicas(uid)
    (c,) = [c for c in contas if c["nome"] == "Conta" and c["saldo"] is not None and D(c["saldo"]) == D("300.25")]
    ids = [cria(uid, nome="tudo", fonte={"tipo": "investido"}),
           cria(uid, nome="nu", fonte={"tipo": "banco", "banco": "Nubank"}),
           cria(uid, nome="xp", fonte={"tipo": "banco", "banco": "XP"}),
           cria(uid, nome="cc", fonte={"tipo": "conta", "conta": c["id"]})]
    tudo, nu_, xp, cc = (meta(uid, i) for i in ids)
    assert tudo["atual"] == inv["total"] == "1750.50"
    por_banco = {p["banco"]: p["valor"] for p in inv["por_banco"]}
    assert nu_["atual"] == por_banco["Nubank"] == "1250.50" and xp["atual"] == por_banco["XP"] == "500.00"
    assert cc["atual"] == c["saldo"] and cc["fonte"]["conta_id"] == c["id"]
    texto = json.dumps(ler(uid)) + json.dumps(contas)
    assert "acc-SEGREDO" not in texto and "99887" not in texto
    assert set(c) == {"id", "instituicao", "nome", "saldo", "moeda", "no_total", "conexao",
                      "sincronizado_em", "motivos", "logo_proprio", "logo_url"}


# ── reconexão (grupo) ───────────────────────────────────────────────────────

def test_reconexao_com_o_mesmo_provider_account_id_segue_lendo(uid):
    conta_do_banco(uid, "acc-1", "300")
    mid = cria(uid, fonte=fonte_conta(uid))
    antes = meta(uid, mid)["fonte"]["conta_id"]
    nova = banco(uid, "Nubank")  # conexão mais nova, linha de conta nova, mesma identidade
    conta(nova, "acc-1", "777")
    m = meta(uid, mid)
    assert D(m["atual"]) == 777 and m["fonte"]["conta_id"] != antes and m["motivos"] == []


def test_reconexao_com_ids_novos_casa_pelo_numero(uid):
    cid = conta_do_banco(uid, "acc-velha", "300", numero="12345-6")
    mid = cria(uid, fonte=fonte_conta(uid))
    q("delete from open_finance_connections where id = %s", (cid,))
    assert meta(uid, mid)["motivos"] == ["fonte_indisponivel"]  # sem candidato nenhum
    conta_do_banco(uid, "acc-nova", "450", numero="12345-6")
    m = meta(uid, mid)
    assert D(m["atual"]) == 450 and m["motivos"] == []


def test_reconexao_numero_ambiguo_ou_nulo_e_fonte_indisponivel(uid):
    cid = conta_do_banco(uid, "acc-velha", "300", numero="12345-6")
    mid = cria(uid, fonte=fonte_conta(uid))
    q("delete from open_finance_connections where id = %s", (cid,))
    conta_do_banco(uid, "acc-n1", "10", numero="12345-6")
    conta_do_banco(uid, "acc-n2", "20", numero="12345-6")
    m = meta(uid, mid)
    assert m["atual"] is None and m["motivos"] == ["fonte_indisponivel"] and m["fonte"]["conta_id"] is None
    # sem número guardado não há segunda chance
    cid = conta_do_banco(uid, "acc-sem-numero", "5")
    (c5,) = [c for c in contas_publicas(uid) if c["saldo"] is not None and D(c["saldo"]) == 5]
    outra = cria(uid, nome="outra", fonte={"tipo": "conta", "conta": c5["id"]})
    q("delete from open_finance_connections where id = %s", (cid,))
    conta_do_banco(uid, "acc-outra", "5")
    assert meta(uid, outra)["motivos"] == ["fonte_indisponivel"]


# ── estados: nunca R$ 0 inventado ───────────────────────────────────────────

def _desatualiza(cid, aid):
    q("update open_finance_connections set last_sync_at = %s where id = %s", (horas_atras(72), cid))


def _pausa(cid, aid):
    q("update open_finance_connections set status = 'PAUSED' where id = %s", (cid,))


def _sem_saldo(cid, aid):
    q("update open_finance_accounts set balance = 'NaN' where id = %s", (aid,))


def _usd(cid, aid):
    q("update open_finance_accounts set currency = 'USD', raw = raw || %s where id = %s",
      (Jsonb({"currencyCode": "USD"}), aid))


def _some(cid, aid):
    q("delete from open_finance_connections where id = %s", (cid,))


@pytest.mark.parametrize("mexe,atual,motivos", [
    (_desatualiza, "300", ["banco_desatualizado"]),
    (_pausa, None, ["conexao_pausada"]),
    (_sem_saldo, None, ["saldo_ausente"]),
    (_usd, None, ["outra_moeda"]),
    (_some, None, ["fonte_indisponivel"]),
])
def test_estados_da_conta(uid, mexe, atual, motivos, sem_check_finito):
    if mexe is _sem_saldo:  # grava NaN de propósito: o banco novo o recusaria
        sem_check_finito("open_finance_accounts", "balance")
    cid = conta_do_banco(uid, "acc-1", "300")
    mid = cria(uid, fonte=fonte_conta(uid))
    aid = q("select id from open_finance_accounts where connection_id = %s", (cid,))["id"]
    assert D(meta(uid, mid)["atual"]) == 300  # controle positivo: antes de mexer
    mexe(cid, aid)
    m = meta(uid, mid)
    assert (None if m["atual"] is None else D(m["atual"])) == (None if atual is None else D(atual))
    assert m["motivos"] == motivos
    if atual is None:
        assert m["falta"] is None and m["percentual"] is None and m["por_mes"] is None


def test_banco_ausente_do_por_banco_ou_sem_saldo_e_null(uid):
    nu = banco(uid, "Nubank")
    p = posicao(nu, "inv-1", "100")
    com_banco = cria(uid, nome="nu", fonte={"tipo": "banco", "banco": "Nubank"})
    tudo = cria(uid, nome="tudo")
    assert D(meta(uid, com_banco)["atual"]) == 100
    q("update open_finance_investments set balance = null, raw = raw || %s where id = %s",
      (Jsonb({"balance": None}), p))
    for mid in (com_banco, tudo):
        assert meta(uid, mid)["atual"] is None and "saldo_ausente" in meta(uid, mid)["motivos"]
    q("delete from open_finance_investments where id = %s", (p,))
    m = meta(uid, com_banco)
    assert m["atual"] is None and "fonte_indisponivel" in m["motivos"]
    assert meta(uid, tudo)["atual"] is None  # sem posição lida não há R$ 0


# ── custo e reserva ─────────────────────────────────────────────────────────

def recorrentes(uid, itens):
    contas, rps = [], []
    for i, (desc, valor, tipo, atras) in enumerate(itens):
        txs = ap.mensais(f"r{i}", [valor] * 3, ultima=today_tz() - timedelta(days=atras), desc=desc)
        contas.append(ap.conta(f"acc-r{i}", txs, tipo=tipo))
        rps.append(ap.rp(desc, float(valor), txs))
    return ap.semeia(uid, contas, rps)


ITENS = [("NETFLIX.COM", "-39.90", "BANK", 10), ("ALUGUEL STUDIO", "-1500", "CREDIT", 12),
         ("ACADEMIA VELHA", "-100", "BANK", 60)]


def test_custo_conta_ativas_com_cartao_e_ignora_a_interrompida(uid):
    recorrentes(uid, ITENS)
    posicao(banco(uid), "inv-1", "4000")
    q("update open_finance_connections set recurring_fetched_at = now() where user_id = %s", (uid,))
    mid = cria(uid, **RESERVA)
    r = ler(uid)
    assert r["custo_mensal"] == {"valor": "1539.90", "recorrencias": 2, "motivos": []}
    m = meta(uid, mid)
    assert (m["alvo"], m["meses"], m["tipo"]) == ("9239.40", 6, "reserva")
    assert D(m["falta"]) == D("5239.40") and m["percentual"] == 43


def test_custo_ignorar_muda_o_numero_e_vale_so_para_saida(uid):
    recorrentes(uid, ITENS)
    marcar(uid, merchant_key("NETFLIX.COM"), "ignorar")
    assert ler(uid)["custo_mensal"]["valor"] == "1500.00"


def test_reserva_meses_proprios(uid):
    recorrentes(uid, ITENS[:1])
    mid = cria(uid, **RESERVA, meses=12)
    assert meta(uid, mid)["alvo"] == "478.80"


def test_reserva_sem_custo_e_alvo_null_e_o_atual_aparece(uid):
    posicao(banco(uid), "inv-1", "500")  # conexão viva, nenhuma recorrência lida
    mid = cria(uid, **RESERVA)
    r = ler(uid)
    assert r["custo_mensal"] == {"valor": None, "recorrencias": 0,
                                 "motivos": ["sem_recorrencias", "recorrencias_banco_nao_lidas"]}
    m = meta(uid, mid)
    assert (m["alvo"], m["falta"], m["percentual"]) == (None, None, None)
    assert D(m["atual"]) == 500 and "custo_desconhecido" in m["motivos"]


def test_custo_sem_banco_e_lista_velha(uid):
    assert ler(uid)["custo_mensal"] == {"valor": None, "recorrencias": 0, "motivos": ["sem_banco_conectado"]}
    cid = recorrentes(uid, ITENS[:1])
    assert ler(uid)["custo_mensal"]["motivos"] == []
    q("update open_finance_connections set recurring_fetched_at = %s where id = %s", (horas_atras(72), cid))
    c = ler(uid)["custo_mensal"]
    assert c["valor"] == "39.90" and c["motivos"] == ["recorrencias_banco_nao_lidas"]


@pytest.mark.parametrize("ruim", ["NaN", "Infinity", "-Infinity"])
def test_valor_nao_finito_no_espelho_nao_derruba_e_o_custo_vira_desconhecido(uid, ruim, sem_check_finito):
    """`open_finance_transactions.amount` é `numeric` sem restrição e guarda NaN/±Infinity: a cadeia
    suja tira o custo do ar (`None` + motivo), nunca 500 nem um custo menor com cara de exato.
    (Banco novo: o CHECK `finito_amount` recusa; a fixture o tira porque o espelho legado pode ter.)"""
    sem_check_finito("open_finance_transactions", "amount")
    hoje = today_tz()
    ok = ap.mensais("ok", ["-39.90"] * 3, ultima=hoje - timedelta(days=10), desc="NETFLIX.COM")
    mau = ap.mensais("mau", ["-10", "-10", ruim], ultima=hoje - timedelta(days=12), desc="ACADEMIA XYZ")
    ap.semeia(uid, [ap.conta("acc-ok", ok), ap.conta("acc-mau", mau)],
              [ap.rp("NETFLIX.COM", -39.9, ok), ap.rp("ACADEMIA XYZ", -10, mau)])
    mid = cria(uid, **RESERVA)
    r = ler(uid)
    assert r["custo_mensal"]["valor"] is None and r["custo_mensal"]["recorrencias"] == 0
    assert "recorrencia_valor_invalido" in r["custo_mensal"]["motivos"]
    m = meta(uid, mid)
    assert (m["alvo"], m["falta"], m["percentual"]) == (None, None, None)
    assert "custo_desconhecido" in m["motivos"]


def test_custo_de_a_nao_entra_na_reserva_de_b(dois):
    a, b = dois
    recorrentes(a, ITENS[:1])
    posicao(banco(b), "inv-1", "1")
    assert ler(b)["custo_mensal"]["valor"] is None and ler(a)["custo_mensal"]["valor"] == "39.90"


# ── prazo ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hoje,alvo,n", [
    (date(2026, 10, 11), date(2026, 10, 11), 1), (date(2026, 10, 11), date(2026, 10, 20), 1),
    (date(2026, 10, 11), date(2026, 11, 5), 1), (date(2026, 10, 11), date(2026, 12, 11), 2),
    (date(2026, 10, 11), date(2026, 12, 20), 3), (date(2026, 12, 20), date(2027, 1, 5), 1),
])
def test_meses_ate(hoje, alvo, n):
    assert dbmetas._meses_ate(hoje, alvo) == n


def test_por_mes_percentual_e_prazo_passado(uid):
    posicao(banco(uid), "inv-1", "400")
    hoje = today_tz()
    mid = cria(uid, data_alvo=hoje.isoformat())
    m = meta(uid, mid)
    assert (m["percentual"], m["falta"], m["por_mes"], m["prazo_passou"]) == (40, "600.00", "600.00", False)
    sem_data = meta(uid, cria(uid, nome="s"))
    assert sem_data["por_mes"] is None and sem_data["data_alvo"] is None
    q("update metas set data_alvo = %s where id = %s", (hoje - timedelta(days=1), mid))
    m = meta(uid, mid)
    assert m["prazo_passou"] is True and m["por_mes"] is None and m["falta"] == "600.00"
    batida = meta(uid, cria(uid, nome="b", alvo="300"))
    assert (batida["percentual"], D(batida["falta"])) == (100, 0)


# ── conversão e edição ──────────────────────────────────────────────────────

def test_converter_reserva_em_meta_com_alvo(uid):
    mid = cria(uid, **RESERVA)
    r = post(uid, "/editar", {"id": mid, "tipo": "meta", "alvo": "5000"})
    assert r.status_code == 200, r.text
    m = meta(uid, mid)
    assert (m["tipo"], m["alvo"], m["meses"]) == ("meta", "5000.00", None)


@pytest.mark.parametrize("corpo", [{"tipo": "meta"}, {"tipo": "meta", "alvo": "10", "meses": 3}])
def test_converter_reserva_sem_alvo_ou_com_meses_e_422(uid, corpo):
    mid = cria(uid, **RESERVA)
    assert codigo(post(uid, "/editar", {"id": mid, **corpo})) == (422, "validation_error")
    assert meta(uid, mid)["tipo"] == "reserva"


def test_meta_nao_vira_reserva_e_nao_ganha_meses(uid):
    mid = cria(uid)
    for corpo in ({"tipo": "reserva"}, {"meses": 6}):
        assert codigo(post(uid, "/editar", {"id": mid, **corpo})) == (422, "validation_error")
    r = post(uid, "/editar", {"id": cria(uid, **RESERVA), "alvo": "10"})
    assert r.status_code == 422


def test_editar_campos_e_limpar_a_data(uid):
    mid = cria(uid, data_alvo=(today_tz() + timedelta(days=90)).isoformat())
    assert post(uid, "/editar", {"id": mid, "nome": "Casa", "alvo": "2000.50"}).status_code == 200
    m = meta(uid, mid)
    assert (m["nome"], m["alvo"]) == ("Casa", "2000.50") and m["data_alvo"] is not None
    assert post(uid, "/editar", {"id": mid, "data_alvo": None}).status_code == 200
    assert meta(uid, mid)["data_alvo"] is None
    assert codigo(post(uid, "/editar", {"id": mid})) == (422, "validation_error")  # nada para editar
    assert post(uid, "/editar", {"id": mid, "data_alvo": "2020-01-01"}).status_code == 422


def test_editar_a_fonte(uid):
    posicao(banco(uid, "Nubank"), "inv-1", "100")
    mid = cria(uid)
    assert post(uid, "/editar", {"id": mid, "fonte": {"tipo": "banco", "banco": "Nubank"}}).status_code == 200
    m = meta(uid, mid)
    assert m["fonte"]["banco"] == "Nubank" and D(m["atual"]) == 100
    assert post(uid, "/editar", {"id": mid, "fonte": {"tipo": "banco", "banco": "Outro"}}).status_code == 422
    assert meta(uid, mid)["fonte"]["banco"] == "Nubank"
    assert post(uid, "/editar", {"id": mid, "fonte": {"tipo": "investido"}}).status_code == 200
    assert meta(uid, mid)["fonte"] == {"tipo": "investido", "rotulo": "Tudo investido",
                                       "conta_id": None, "banco": None}


def test_apagar(uid):
    mid = cria(uid)
    assert post(uid, "/apagar", {"id": mid}).json() == {"id": mid}
    assert ler(uid)["metas"] == []
    assert codigo(post(uid, "/apagar", {"id": mid})) == (404, "meta_nao_encontrada")


# ── validação ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("extra", [
    {"alvo": "0"}, {"alvo": "-1"}, {"alvo": "1.001"}, {"alvo": "abc"}, {"alvo": "1e3"}, {"alvo": ""},
    {"alvo": None}, {"nome": ""}, {"nome": "   "}, {"nome": "x" * 61},
    {"nome": "\u200b"}, {"nome": " \u200b\u200d "}, {"nome": "\x00"}, {"nome": "\n"}, {"nome": "a\nb"},
    {"nome": "a\tb"}, {"nome": "a\rb"}, {"nome": "a\x1fb"}, {"nome": "a\x7fb"}, {"data_alvo": "2020-01-01"},
    {"data_alvo": "2026-13-45"}, {"data_alvo": "amanhã"}, {"meses": 6}, {"tipo": "outro"},
    {"tipo": "reserva", "alvo": "10"}, {"tipo": "reserva", "alvo": None, "meses": 0},
    {"tipo": "reserva", "alvo": None, "meses": 25}, {"tipo": "reserva", "alvo": None, "meses": True},
    {"fonte": {"tipo": "banco"}}, {"fonte": {"tipo": "investido", "banco": "Nubank"}},
    {"fonte": {"tipo": "conta", "conta": 1}}, {"fonte": {"tipo": "conta", "conta": "1"}},
    {"fonte": {"tipo": "banco", "banco": "Inexistente"}}, {"fonte": {"tipo": "conta"}},
])
def test_pedido_invalido_e_422_e_nada_grava(uid, extra):
    posicao(banco(uid, "Nubank"), "inv-1", "1")
    r = post(uid, "", {**BASE, **extra})
    assert codigo(r) == (422, "validation_error"), r.text
    assert n_metas(uid) == 0


def test_conta_em_outra_moeda_e_422(uid):
    cid = banco(uid)
    conta(cid, "acc-usd", "50", moeda="USD", code="USD")
    (c,) = contas_publicas(uid)
    r = post(uid, "", {**BASE, "fonte": {"tipo": "conta", "conta": c["id"]}})
    assert r.status_code == 422 and n_metas(uid) == 0


def test_criacao_valida_aparece_no_get_e_nul_no_nome_nao_estoura(uid):
    mid = cria(uid, nome="Via\x00gem", data_alvo=(today_tz() + timedelta(days=30)).isoformat())
    m = meta(uid, mid)
    assert "\x00" not in m["nome"] and m["nome"].startswith("Via") and m["alvo"] == "1000.00"
    assert m["fonte"] == {"tipo": "investido", "rotulo": "Tudo investido", "conta_id": None, "banco": None}
    assert (m["atual"], m["motivos"]) == (None, ["sem_banco_conectado"])  # sem banco: nunca zero


def test_post_sem_csrf_e_403_fora_do_envelope(uid):
    mid = cria(uid)
    for rota, corpo in (("", BASE), ("/editar", {"id": mid, "nome": "x"}), ("/apagar", {"id": mid})):
        r = post(uid, rota, corpo, com_csrf=False)
        assert r.status_code == 403 and "detail" in r.json() and "error" not in r.json()
    assert n_metas(uid) == 1 and meta(uid, mid)["nome"] == "Viagem"


# ── reserva única ───────────────────────────────────────────────────────────

def test_segunda_reserva_e_409_e_apagar_libera(uid):
    mid = cria(uid, **RESERVA)
    r = post(uid, "", {**BASE, **RESERVA})
    assert codigo(r) == (409, "reserva_ja_existe") and n_metas(uid) == 1
    cria(uid, nome="meta comum")  # meta comum não conta
    post(uid, "/apagar", {"id": mid})
    assert cria(uid, **RESERVA)


@pytest.mark.parametrize("com_accounts", [True, False])
def test_corrida_de_reservas_so_uma_passa(uid, com_accounts):
    """O `_lock_user` não trava sem linha em `accounts`; quem manda é o índice único parcial."""
    if not com_accounts:
        q("delete from accounts where user_id = %s", (uid,))
    parte = threading.Barrier(6)

    def tenta(_):
        parte.wait()
        return post(uid, "", {**BASE, **RESERVA}).status_code

    with ThreadPoolExecutor(6) as ex:
        codigos = sorted(ex.map(tenta, range(6)))
    assert codigos == [200] + [409] * 5, codigos
    assert q("select count(*) n from metas where user_id = %s and tipo = 'reserva'", (uid,))["n"] == 1


def _reserva_sql(quem):
    q("insert into metas (user_id, nome, tipo, meses, fonte_tipo) values (%s, 'R', 'reserva', 6, 'investido')",
      (quem,))


def _usuario_sem_plano() -> int:
    u = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(u)
    return u


def test_merge_com_reserva_dos_dois_lados_recusa_e_nao_move_nada(uid):
    origem = _usuario_sem_plano()  # origem paga é recusada antes, por outro motivo
    _reserva_sql(origem)
    q("insert into metas (user_id, nome, tipo, alvo, fonte_tipo) values (%s, 'M', 'meta', 10, 'investido')",
      (origem,))
    _reserva_sql(uid)
    with pytest.raises(MergeRefused) as e:
        merge_users(origem, uid)
    assert e.value.motivo == "colisao"
    assert (n_metas(origem), n_metas(uid)) == (2, 1)


def test_merge_com_uma_reserva_so_move_tudo(uid):
    origem = _usuario_sem_plano()
    _reserva_sql(origem)
    q("insert into metas (user_id, nome, tipo, alvo, fonte_tipo) values (%s, 'M', 'meta', 10, 'investido')",
      (uid,))
    merge_users(origem, uid)
    assert q("select count(*) n from metas where user_id = %s and tipo = 'reserva'", (uid,))["n"] == 1
    assert n_metas(uid) == 2


# ── privacidade e SSE ───────────────────────────────────────────────────────

def test_exportacao_contem_so_as_metas_do_dono_e_a_exclusao_apaga(dois):
    a, b = dois
    cria(a, nome="de A")
    cria(b, nome="de B")
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(a))).read("dados.json"))["dados"]
    assert [m["nome"] for m in dados["metas"]] == ["de A"]
    delete_user_data(a)
    assert n_metas(a) == 0 and n_metas(b) == 1


def test_escrita_em_metas_avisa_o_dono(uid, monkeypatch):
    from tests.test_api_v2_eventos_escrita import barreira, ouvindo

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            r = await asyncio.to_thread(post, uid, "", BASE)
            assert r.status_code == 200
            await barreira(avisados)
            return avisados.count(uid)

    assert asyncio.run(cena()) >= 1


# ── banco que zerou × banco que sumiu ───────────────────────────────────────

def _meta_de_banco(uid, nome="Nubank"):
    return cria(uid, nome="nu", fonte={"tipo": "banco", "banco": nome})


@pytest.mark.parametrize("zera", [
    lambda p: q("update open_finance_investments set balance = 0, raw = raw || %s where id = %s",
                (Jsonb({"balance": 0}), p)),
    lambda p: q("update open_finance_investments set raw = raw || %s where id = %s",
                (Jsonb({"status": "TOTAL_WITHDRAWAL"}), p)),
], ids=["saldo_zero", "resgate_total"])
def test_banco_que_resgatou_tudo_mostra_zero_e_o_que_sumiu_e_indisponivel(uid, zera):
    nu = banco(uid, "Nubank")
    p = posicao(nu, "inv-1", "100")
    posicao(banco(uid, "XP"), "inv-2", "500")
    mid, tudo = _meta_de_banco(uid), cria(uid, nome="tudo")
    zera(p)
    m = meta(uid, mid)
    assert (D(m["atual"]), m["percentual"], m["falta"], m["motivos"]) == (0, 0, "1000.00", [])
    assert D(meta(uid, tudo)["atual"]) == 500
    assert post(uid, "", {**BASE, "fonte": {"tipo": "banco", "banco": "Nubank"}}).status_code == 200
    q("delete from open_finance_connections where id = %s", (nu,))  # desconectou de vez
    m = meta(uid, mid)
    assert m["atual"] is None and m["motivos"] == ["fonte_indisponivel"]


# ── edição ──────────────────────────────────────────────────────────────────

def editada_em(mid):
    return q("select atualizada_em t from metas where id = %s", (mid,))["t"]


@pytest.mark.parametrize("campo", ["alvo", "nome", "meses", "fonte", "tipo"])
def test_campo_nao_anulavel_com_null_e_422_mesmo_junto_de_outro(uid, campo):
    mid = cria(uid, **RESERVA)
    futuro = (today_tz() + timedelta(days=90)).isoformat()
    for extra in ({}, {"data_alvo": futuro}):
        assert codigo(post(uid, "/editar", {"id": mid, campo: None, **extra})) == (422, "validation_error")
    assert meta(uid, mid)["data_alvo"] is None


@pytest.mark.parametrize("corpo,tipo", [
    ({"meses": 0}, "reserva"), ({"meses": 25}, "reserva"), ({"meses": -1}, "reserva"),
    ({"alvo": "0"}, "meta"), ({"alvo": "0.00"}, "meta"), ({"alvo": "-5"}, "meta"),
    ({"meses": 6}, "meta"), ({"alvo": "10"}, "reserva"), ({"tipo": "meta", "meses": 6}, "meta"),
    ({"tipo": "reserva", "alvo": "10"}, "reserva"),
])
def test_edicao_invalida_e_422_e_nao_muda_nada(uid, corpo, tipo):
    mid = cria(uid, **(RESERVA if tipo == "reserva" else {}))
    antes = meta(uid, mid)
    assert codigo(post(uid, "/editar", {"id": mid, **corpo})) == (422, "validation_error")
    assert meta(uid, mid) == antes


def test_edicao_sem_efeito_e_200_sem_escrever_e_sem_campo_nenhum_e_422(uid):
    mid = cria(uid)
    antes = editada_em(mid)
    for corpo in ({"tipo": "meta"}, {"data_alvo": None}, {"nome": "Viagem", "alvo": "1000"},
                  {"fonte": {"tipo": "investido"}}):
        assert post(uid, "/editar", {"id": mid, **corpo}).status_code == 200, corpo
    assert editada_em(mid) == antes
    assert codigo(post(uid, "/editar", {"id": mid})) == (422, "validation_error")


def test_meta_vencida_reenvia_a_mesma_data_mas_nao_aceita_outra_no_passado(uid):
    mid = cria(uid)
    vencida = today_tz() - timedelta(days=3)
    q("update metas set data_alvo = %s where id = %s", (vencida, mid))
    r = post(uid, "/editar", {"id": mid, "nome": "Nova", "data_alvo": vencida.isoformat()})
    assert r.status_code == 200, r.text
    assert meta(uid, mid)["nome"] == "Nova"
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    assert codigo(post(uid, "/editar", {"id": mid, "data_alvo": ontem})) == (422, "validation_error")
    assert post(uid, "/editar", {"id": mid, "data_alvo": today_tz().isoformat()}).status_code == 200


def test_nome_bom_passa_e_o_ruim_na_edicao_e_422(uid):
    mid = cria(uid, nome="Casa própria 🏠")
    assert meta(uid, mid)["nome"] == "Casa própria 🏠"
    for ruim in ("\u200b", "a\nb", "\t"):
        assert codigo(post(uid, "/editar", {"id": mid, "nome": ruim})) == (422, "validation_error")
    assert meta(uid, mid)["nome"] == "Casa própria 🏠"


# ── centavos ────────────────────────────────────────────────────────────────

def test_falta_e_por_mes_saem_em_centavos_e_o_atual_segue_o_das_contas(uid):
    conta_do_banco(uid, "acc-1", "50.135")  # numeric sem escala; 1000 − 50.135 = 949.865
    fonte = fonte_conta(uid)
    mid = cria(uid, fonte=fonte, data_alvo=today_tz().isoformat())
    m = meta(uid, mid)
    assert m["atual"] == contas_publicas(uid)[0]["saldo"] == "50.135"  # paridade: /contas não quantiza
    assert (m["falta"], m["por_mes"]) == ("949.87", "949.87")  # meio centavo sobe (HALF_UP)


# ── conta: instituição + número + tipo ──────────────────────────────────────

def _muda_tipo(cid, subtipo):
    q("update open_finance_accounts set subtype = %s where connection_id = %s", (subtipo, cid))


def test_conta_reconectada_casa_so_com_mesmo_banco_numero_e_tipo(uid):
    cid = conta_do_banco(uid, "acc-velha", "300", numero="12345-6")
    _muda_tipo(cid, "CHECKING_ACCOUNT")
    mid = cria(uid, fonte=fonte_conta(uid))
    q("delete from open_finance_connections where id = %s", (cid,))
    # mesmo número e banco, OUTRO tipo (poupança): não é a mesma conta
    pop = conta_do_banco(uid, "acc-pop", "999", numero="12345-6")
    _muda_tipo(pop, "SAVINGS_ACCOUNT")
    m = meta(uid, mid)
    assert m["atual"] is None and m["motivos"] == ["fonte_indisponivel"]
    # mesmo número e tipo em OUTRO banco: também não
    outro = conta_do_banco(uid, "acc-itau", "888", numero="12345-6", nome="Itaú")
    _muda_tipo(outro, "CHECKING_ACCOUNT")
    assert meta(uid, mid)["atual"] is None
    # mesmo banco, número e tipo: casa
    certa = conta_do_banco(uid, "acc-nova", "450", numero="12345-6")
    _muda_tipo(certa, "CHECKING_ACCOUNT")
    assert D(meta(uid, mid)["atual"]) == 450


# ── mutações que sobreviviam ────────────────────────────────────────────────

def test_saldo_negativo_nao_da_percentual_negativo(uid):
    conta_do_banco(uid, "acc-1", "-50")
    m = meta(uid, cria(uid, fonte=fonte_conta(uid)))
    assert (D(m["atual"]), m["percentual"], m["falta"]) == (-50, 0, "1050.00")


@pytest.mark.parametrize("rota", ["/editar", "/apagar"])
def test_faixa_do_id(uid, rota):
    corpo = {"nome": "x"} if rota == "/editar" else {}
    assert codigo(post(uid, rota, {"id": 2**63, **corpo})) == (422, "validation_error")
    assert codigo(post(uid, rota, {"id": 2**63 - 1, **corpo})) == (404, "meta_nao_encontrada")
    for ruim in (True, 0, -1, "1"):
        assert codigo(post(uid, rota, {"id": ruim, **corpo})) == (422, "validation_error"), ruim


def test_custo_zero_e_custo_desconhecido_sem_dividir_por_zero():
    l = {"id": 1, "nome": "R", "tipo": "reserva", "meses": 6, "alvo": None, "data_alvo": None,
         "fonte_tipo": "investido", "fonte_rotulo": None, "fonte_ref": None, "fonte_banco": None}
    inv = {"total": D(500), "motivos": [], "por_banco_com_zero": []}
    m = dbmetas._meta(l, inv, None, {"valor": D(0), "recorrencias": 1, "motivos": []}, date(2026, 10, 11),
                    lambda *a: (None, None, set()))  # o ritmo tem os testes dele
    assert (m["alvo"], m["falta"], m["percentual"]) == (None, None, None)
    assert m["atual"] == 500 and "custo_desconhecido" in m["motivos"]


def test_listar_le_tudo_no_mesmo_snapshot(uid, monkeypatch):
    conta_do_banco(uid, "acc-1", "300")
    mid = cria(uid, fonte=fonte_conta(uid))
    lido = dbmetas._ler_fontes

    def escreve_antes_de_ler(*a, **k):  # outra transação confirma entre a leitura das metas e a da fonte
        q("update open_finance_accounts set balance = 999 where provider_account_id = 'acc-1'")
        return lido(*a, **k)

    monkeypatch.setattr(dbmetas, "_ler_fontes", escreve_antes_de_ler)
    assert D(meta(uid, mid)["atual"]) == 300
    monkeypatch.setattr(dbmetas, "_ler_fontes", lido)
    assert D(meta(uid, mid)["atual"]) == 999


# ── plano sem o recurso `investments` (freio PLANS_V2_ENABLED=0) ────────────

def test_sem_investments_as_fontes_do_investido_somem_e_criar_e_403(uid, monkeypatch):
    posicao(banco(uid, "Nubank"), "inv-1", "100")
    conta_do_banco(uid, "acc-1", "300")
    inv, bco, cc = (cria(uid, nome="i"), _meta_de_banco(uid), cria(uid, nome="c", fonte=fonte_conta(uid)))
    assert D(meta(uid, inv)["atual"]) == 100
    nega = lambda u, f: f != "investments"  # noqa: E731 — o gate de `/investido`, nos dois módulos
    from api.v2 import investido as rota_inv, metas as rota_metas
    monkeypatch.setattr(rota_inv, "plan_gate_ok", nega)
    monkeypatch.setattr(rota_metas, "plan_gate_ok", nega)
    monkeypatch.setattr(dbmetas.investido, "calcular", lambda *a, **k: pytest.fail("leu sem o recurso"))
    for mid in (inv, bco):
        m = meta(uid, mid)
        assert (m["atual"], m["falta"], m["percentual"], m["motivos"]) == (None, None, None,
                                                                          ["plano_sem_investimentos"])
    assert D(meta(uid, cc)["atual"]) == 300  # conta não depende do recurso
    for fonte in ({"tipo": "investido"}, {"tipo": "banco", "banco": "Nubank"}):
        r = post(uid, "", {**BASE, "fonte": fonte})
        assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required"
        assert post(uid, "/editar", {"id": cc, "fonte": fonte}).status_code == 403
    assert post(uid, "", {**BASE, "fonte": fonte_conta(uid)}).status_code == 200
    assert post(uid, "/editar", {"id": inv, "nome": "só o nome"}).status_code == 200


def test_com_o_freio_dos_planos_v2_o_essencial_nao_ve_o_investido_nem_nas_metas(libera, monkeypatch):
    """O caso real do freio: `/investido` dá 403 ao Essencial; `/metas` não pode mostrar o valor."""
    u = usuario_pagante("essencial")
    libera(u)
    posicao(banco(u, "Nubank"), "inv-1", "100")
    mid = cria(u)
    assert D(meta(u, mid)["atual"]) == 100  # v2 ligado: Essencial tem o recurso
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    assert get_status(u, "/api/v2/investido") == 403
    assert meta(u, mid)["atual"] is None and meta(u, mid)["motivos"] == ["plano_sem_investimentos"]
    assert post(u, "", BASE).status_code == 403


# ── banco: o que ficou FORA da soma acompanha o número; sem nada dentro, é null ─

def test_banco_so_em_dolar_e_null_com_outra_moeda_como_o_investido(uid):
    posicao(banco(uid, "Nubank"), "i1", "1000", moeda="USD", code="USD")
    for m in (meta(uid, _meta_de_banco(uid)), meta(uid, cria(uid, nome="tudo"))):
        assert m["atual"] is None and m["falta"] is None and m["motivos"] == ["outra_moeda"], m
    assert "outra_moeda" in get(uid, "/api/v2/investido")["motivos"]


def test_banco_com_real_e_dolar_mostra_o_real_com_o_motivo(uid):
    """Exclusão parcial não anula: o número é o de `/investido`, com `outra_moeda`."""
    nu = banco(uid, "Nubank")
    posicao(nu, "i1", "100")
    posicao(nu, "i2", "50", moeda="USD", code="USD")
    mid, tudo = _meta_de_banco(uid), cria(uid, nome="tudo")
    inv = get(uid, "/api/v2/investido")
    for m in (meta(uid, mid), meta(uid, tudo)):
        assert D(m["atual"]) == 100 == D(inv["total"]) and m["motivos"] == ["outra_moeda"], m


def test_item_pausado_do_mesmo_banco_mantem_o_zero_do_item_vivo_com_o_motivo(uid):
    """O item vivo está DENTRO do recorte (R$ 0,00 em reais); o irmão pausado só acompanha."""
    p = posicao(banco(uid, "Nubank"), "i1", "100")
    posicao(banco(uid, "Nubank", status="PAUSED"), "i2", "700")
    q("update open_finance_investments set balance = 0, raw = raw || %s where id = %s", (Jsonb({"balance": 0}), p))
    for m in (meta(uid, _meta_de_banco(uid)), meta(uid, cria(uid, nome="tudo"))):
        assert m["atual"] == "0.00" and m["motivos"] == ["conexao_pausada"], m
    assert "conexao_pausada" in get(uid, "/api/v2/investido")["motivos"]


def test_banco_so_com_item_pausado_do_mesmo_banco_e_null(uid):
    """Nenhuma posição em reais dentro: o item vivo é todo em dólar e o outro está pausado."""
    posicao(banco(uid, "Nubank"), "i1", "10", moeda="USD", code="USD")
    posicao(banco(uid, "Nubank", status="PAUSED"), "i2", "700")
    m = meta(uid, _meta_de_banco(uid))
    assert m["atual"] is None and m["motivos"] == ["outra_moeda", "conexao_pausada"]


def test_pausa_de_OUTRO_banco_nao_anula_o_banco_escolhido(uid):
    """Positivo: o motivo é do banco, não do topo; outro banco em dólar/pausado não contamina."""
    posicao(banco(uid, "Nubank"), "i1", "100")
    posicao(banco(uid, "XP", status="PAUSED"), "i2", "700")
    posicao(banco(uid, "Inter"), "i3", "9", moeda="USD", code="USD")
    m = meta(uid, _meta_de_banco(uid))
    assert D(m["atual"]) == 100 and m["motivos"] == []


def test_banco_so_real_que_resgatou_tudo_segue_zero_sem_motivo(uid):
    posicao(banco(uid, "Nubank"), "i1", "50", status="TOTAL_WITHDRAWAL")
    m = meta(uid, _meta_de_banco(uid))
    assert m["atual"] == "0.00" and m["motivos"] == []


@pytest.mark.parametrize("velho_primeiro", [True, False])
def test_dois_itens_resgatados_um_velho_avisa_nas_duas_ordens(uid, velho_primeiro):
    for velho in (velho_primeiro, not velho_primeiro):
        c = banco(uid, "Nubank", **({"sync": horas_atras(100)} if velho else {}))
        posicao(c, uuid.uuid4().hex[:8], "50", status="TOTAL_WITHDRAWAL")
    m = meta(uid, _meta_de_banco(uid))
    assert m["atual"] == "0.00" and m["motivos"] == ["banco_desatualizado"]


def test_G1_banco_sem_linha_no_espelho_nao_e_zero(uid):
    posicao(banco(uid, "Nubank"), "i1", "100")
    banco(uid, "XP")  # viva, sincronizada agora, nenhuma linha em open_finance_investments
    r = post(uid, "", {**BASE, "fonte": {"tipo": "banco", "banco": "XP"}})
    assert r.status_code == 422, r.text
    q("insert into metas (user_id,nome,tipo,alvo,fonte_tipo,fonte_ref) values (%s,'b','meta',10,'banco','XP')", (uid,))
    (m,) = [m for m in ler(uid)["metas"] if m["nome"] == "b"]
    assert m["atual"] is None and "fonte_indisponivel" in m["motivos"], m


def test_G2_conexao_nunca_sincronizada_com_linha_nao_e_zero(uid):
    posicao(banco(uid, "Nubank"), "i1", "100")
    posicao(banco(uid, "XP", sync=None), "i2", "50", status="TOTAL_WITHDRAWAL")
    q("insert into metas (user_id,nome,tipo,alvo,fonte_tipo,fonte_ref) values (%s,'b','meta',10,'banco','XP')", (uid,))
    (m,) = [m for m in ler(uid)["metas"] if m["nome"] == "b"]
    assert m["atual"] is None, m


def test_G3_banco_velho_que_resgatou_tudo_avisa_desatualizado(uid):
    posicao(banco(uid, "Nubank", sync=horas_atras(100)), "i1", "50", status="TOTAL_WITHDRAWAL")
    m = meta(uid, _meta_de_banco(uid))
    assert m["atual"] == "0.00" and m["motivos"] == ["banco_desatualizado"], m


def _tudo(uid):
    return meta(uid, cria(uid, nome="tudo"))


def test_resgatada_de_item_vivo_e_zero_conhecido_com_irmao_pausado(uid):
    """A1: o mesmo cenário com `balance = 0` já dava `0.00`; a resgatada tem de dar igual."""
    posicao(banco(uid, "Nubank"), "i1", "100", status="TOTAL_WITHDRAWAL")
    posicao(banco(uid, "Nubank", status="PAUSED"), "i2", "700")
    mid = _meta_de_banco(uid)
    for m in (meta(uid, mid), _tudo(uid)):
        assert m["atual"] == "0.00" and m["motivos"] == ["conexao_pausada"], m
    assert get(uid, "/api/v2/investido")["total"] == "0.00"


def test_resgatada_de_item_vivo_e_zero_conhecido_com_dolar_no_mesmo_banco(uid):
    nu = banco(uid, "Nubank")
    posicao(nu, "i1", "100", status="TOTAL_WITHDRAWAL")
    posicao(nu, "i2", "50", moeda="USD", code="USD")
    mid = _meta_de_banco(uid)
    for m in (meta(uid, mid), _tudo(uid)):
        assert m["atual"] == "0.00" and m["motivos"] == ["outra_moeda"], m


def test_banco_resgatado_e_outro_so_em_dolar_na_fonte_investido_e_zero_como_o_investido(uid):
    posicao(banco(uid, "Nubank"), "i1", "100", status="TOTAL_WITHDRAWAL")
    posicao(banco(uid, "XP"), "i2", "50", moeda="USD", code="USD")
    m = _tudo(uid)
    inv = get(uid, "/api/v2/investido")
    assert m["atual"] == "0.00" == inv["total"] and m["motivos"] == ["outra_moeda"], m


def test_resgatada_de_um_banco_nao_tira_o_null_do_outro_so_em_dolar(uid):
    """Positivo: só o banco que TEM a resgatada vira zero; o outro, só em dólar, segue `null`."""
    posicao(banco(uid, "Nubank"), "i1", "100", status="TOTAL_WITHDRAWAL")
    posicao(banco(uid, "XP"), "i2", "50", moeda="USD", code="USD")
    m = meta(uid, cria(uid, nome="xp", fonte={"tipo": "banco", "banco": "XP"}))
    assert m["atual"] is None and m["motivos"] == ["outra_moeda"], m


def test_banco_que_sumiu_nao_herda_motivo_de_outro_banco(uid):
    posicao(banco(uid, "XP"), "i1", "50", moeda="USD", code="USD")
    posicao(banco(uid, "Inter"), "i2", "10")
    q("insert into metas (user_id,nome,tipo,alvo,fonte_tipo,fonte_ref) values (%s,'f','meta',10,'banco','Fantasma')", (uid,))
    (m,) = [m for m in ler(uid)["metas"] if m["nome"] == "f"]
    assert m["atual"] is None and m["motivos"] == ["fonte_indisponivel"], m


def test_banco_que_sumiu_sem_nenhuma_conexao_viva_diz_sem_banco_conectado(uid):
    q("insert into metas (user_id,nome,tipo,alvo,fonte_tipo,fonte_ref) values (%s,'f','meta',10,'banco','Fantasma')", (uid,))
    (m,) = ler(uid)["metas"]
    assert m["atual"] is None and m["motivos"] == ["sem_banco_conectado", "fonte_indisponivel"], m


# ── custo mensal: o mesmo recurso de plano das recorrências (`subscriptions`) ─

def _reserva_com_recorrencias(u):
    recorrentes(u, ITENS)
    q("update open_finance_connections set recurring_fetched_at = now() where user_id = %s", (u,))
    return cria(u, **RESERVA)


def _sem_custo(u, mid):
    r = ler(u)
    assert r["custo_mensal"] == {"valor": None, "recorrencias": 0, "motivos": ["plano_sem_custo_mensal"]}
    m = meta(u, mid)
    assert (m["alvo"], m["falta"], m["percentual"]) == (None, None, None)
    assert "plano_sem_custo_mensal" in m["motivos"] and "custo_desconhecido" not in m["motivos"]  # não é falta de dado


def test_essencial_nao_ve_o_custo_mensal_nem_o_alvo_da_reserva(libera):
    u = usuario_pagante("essencial")
    libera(u)
    mid = _reserva_com_recorrencias(u)
    assert get_status(u, "/api/v2/assinaturas") == 403 and get_status(u, "/api/v2/previsao") == 403
    _sem_custo(u, mid)


@pytest.mark.parametrize("plano", ["plus", "pro"])
def test_plus_e_pro_veem_o_custo_e_o_alvo(libera, plano):
    u = usuario_pagante(plano)
    libera(u)
    mid = _reserva_com_recorrencias(u)
    assert ler(u)["custo_mensal"] == {"valor": "1539.90", "recorrencias": 2, "motivos": []}
    assert meta(u, mid)["alvo"] == "9239.40"


def test_freio_dos_planos_v2_fecha_o_custo_do_essencial_e_mantem_o_do_pro(libera, monkeypatch):
    e, p = usuario_pagante("essencial"), usuario_pagante("pro")
    libera(e, p)
    me, mp = _reserva_com_recorrencias(e), _reserva_com_recorrencias(p)
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    assert get_status(e, "/api/v2/assinaturas") == 403
    _sem_custo(e, me)
    assert ler(p)["custo_mensal"]["valor"] == "1539.90" and meta(p, mp)["alvo"] == "9239.40"


def test_sem_o_recurso_o_custo_nem_e_lido(libera, monkeypatch):
    u = usuario_pagante("essencial")
    libera(u)
    mid = _reserva_com_recorrencias(u)
    import core.services.previsao_recorrencias as pr
    monkeypatch.setattr(pr, "custo_mensal", lambda *a, **k: pytest.fail("leu o custo sem o recurso"))
    _sem_custo(u, mid)


@pytest.mark.parametrize("plano", ["essencial", "plus", "pro"])
def test_custo_em_metas_e_o_acesso_a_assinaturas_andam_juntos(libera, plano):
    """O gate do custo é `subscriptions`: se ele mudar de tier, `/assinaturas` e `/metas` mudam JUNTOS."""
    u = usuario_pagante(plano)
    libera(u)
    mid = _reserva_com_recorrencias(u)
    com_acesso = get_status(u, "/api/v2/assinaturas") == 200
    assert com_acesso == (ler(u)["custo_mensal"]["valor"] is not None) == (meta(u, mid)["alvo"] is not None)


# ── banco só de conexão pausada existe (≠ sumiu): o motivo é o dele ─────────

def test_M1_banco_so_pausado_com_outro_vivo_e_null_com_conexao_pausada(uid):
    posicao(banco(uid, "Nubank", status="PAUSED"), "i1", "700")
    posicao(banco(uid, "XP"), "i2", "50")
    mid, xp = _meta_de_banco(uid), cria(uid, nome="xp", fonte={"tipo": "banco", "banco": "XP"})
    m = meta(uid, mid)
    assert m["atual"] is None and m["falta"] is None and m["motivos"] == ["conexao_pausada"], m
    assert D(meta(uid, xp)["atual"]) == 50 and meta(uid, xp)["motivos"] == []  # positivo: o vivo não contamina
    inv = get(uid, "/api/v2/investido")
    assert "conexao_pausada" in inv["motivos"] and D(inv["total"]) == 50  # é o que o `/investido` diz


def test_M1_tudo_pausado_segue_o_investido(uid):
    posicao(banco(uid, "Nubank", status="PAUSED"), "i1", "700")
    m, tudo = meta(uid, _meta_de_banco(uid)), _tudo(uid)
    inv = get(uid, "/api/v2/investido")
    assert inv["motivos"] == ["sem_banco_conectado", "conexao_pausada"]
    assert m["atual"] is None and m["motivos"] == inv["motivos"] == tudo["motivos"], m


def test_M1_banco_sem_linha_nenhuma_segue_fonte_indisponivel_ao_lado_do_pausado(uid):
    posicao(banco(uid, "Nubank", status="PAUSED"), "i1", "700")
    posicao(banco(uid, "XP"), "i2", "50")
    q("insert into metas (user_id,nome,tipo,alvo,fonte_tipo,fonte_ref) values (%s,'f','meta',10,'banco','Fantasma')", (uid,))
    (m,) = [m for m in ler(uid)["metas"] if m["nome"] == "f"]
    assert m["atual"] is None and m["motivos"] == ["fonte_indisponivel"], m


# ── reconexão com a conta antiga numa conexão pausada/apagada ───────────────

def _reconexao(uid, velha_status, nova=True, n_novas=1):
    cid = conta_do_banco(uid, "acc-velha", "300", numero="12345-6")
    mid = cria(uid, fonte=fonte_conta(uid))
    q("update open_finance_connections set status = %s where id = %s", (velha_status, cid))
    for i in range(n_novas if nova else 0):
        conta_do_banco(uid, f"acc-nova{i}", "450", numero="12345-6")
    return mid


@pytest.mark.parametrize("status", ["DELETED", "PAUSED"])
def test_M2_conta_antiga_terminal_e_nova_viva_le_a_nova(uid, status):
    mid = _reconexao(uid, status)
    m = meta(uid, mid)
    (nova,) = [c for c in contas_publicas(uid) if c["no_total"]]
    assert D(m["atual"]) == 450 and m["motivos"] == [] and m["fonte"]["conta_id"] == nova["id"], m
    assert D(get(uid, "/api/v2/contas")["total"]) == 450  # a regra de `/contas`


def test_M2_conta_antiga_pausada_sem_nova_e_null_com_conexao_pausada(uid):
    m = meta(uid, _reconexao(uid, "PAUSED", nova=False))
    assert m["atual"] is None and m["motivos"] == ["conexao_pausada"], m


def test_M2_dois_candidatos_vivos_nao_escolhe(uid):
    m = meta(uid, _reconexao(uid, "DELETED", n_novas=2))
    assert m["atual"] is None and m["motivos"] == ["conexao_pausada"], m


def test_M2_candidato_vivo_de_outro_usuario_nunca_casa(dois):
    a, b = dois
    mid = _reconexao(a, "DELETED", nova=False)
    conta_do_banco(b, "acc-b", "999", numero="12345-6")
    m = meta(a, mid)
    assert m["atual"] is None and m["motivos"] == ["conexao_pausada"], m


def test_M2_conta_viva_pelo_id_nao_e_trocada_por_outra_do_mesmo_numero(uid):
    conta_do_banco(uid, "acc-1", "300", numero="12345-6")
    mid = cria(uid, fonte=fonte_conta(uid))
    conta_do_banco(uid, "acc-2", "450", numero="12345-6")
    assert D(meta(uid, mid)["atual"]) == 300


# ── custo 0 vale como sem custo, no topo e na reserva ───────────────────────

def test_custo_de_cadeia_so_de_cobranca_zero_e_null_com_sem_recorrencias(uid, monkeypatch):
    from core.services import previsao_recorrencias as pr

    posicao(banco(uid), "inv-1", "500")
    q("update open_finance_connections set recurring_fetched_at = now() where user_id = %s", (uid,))
    cadeia = {"valor": D(0), "ultima": today_tz()}
    monkeypatch.setattr(pr, "cadeias_do_banco", lambda *a, **k: [cadeia])
    mid = cria(uid, **RESERVA)
    assert ler(uid)["custo_mensal"] == {"valor": None, "recorrencias": 0, "motivos": ["sem_recorrencias"]}
    m = meta(uid, mid)
    assert m["alvo"] is None and "custo_desconhecido" in m["motivos"]
