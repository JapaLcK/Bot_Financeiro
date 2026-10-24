"""PL-06 A1: a chave do insight (fato × período, estado da meta, dado pessoal vira hash) e o corte em 6.

Postgres real nos casos com banco."""
from datetime import date, timedelta

import pytest

import db
from _cards_apoio import _http
from core import ai_patterns
from core.services.insights_contrato import sujeito, tem_pii
from db.pockets import update_pocket_meta
from test_insights_contrato import _usuario, llm  # noqa: F401  (llm é fixture)


def test_feed_mostra_6_os_mais_graves_primeiro_e_o_cache_guarda_todos(llm):  # noqa: F811
    uid = _usuario("pro_max")
    for i, gasto in enumerate((81, 82, 83, 84, 85, 86, 110, 120)):  # 6 amarelos e 2 vermelhos
        db.upsert_budget(uid, f"cat{i}", 100)
        db.add_launch_and_update_balance(uid, "despesa", gasto, "Loja", None, categoria=f"cat{i}")
    feed = _http(uid).get(f"/insights/{uid}/current").json()["insights"]
    assert len(feed) == 6 and [i["severity"] for i in feed][:2] == ["critical", "critical"]
    assert len(ai_patterns._get_cached(uid, ai_patterns._cache_kind("insights"), 3600)) == 8


# ── a chave separa fato de piora (D4) ───────────────────────────────────────────

def _metas(uid):
    return [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "goal_behind"]


def test_meta_atrasada_e_vencida_sao_fatos_diferentes(llm):  # noqa: F811
    uid = _usuario("pro_max")
    _, pid, _ = db.create_pocket(uid, "Viagem")
    update_pocket_meta(uid, pid, target_amount=10000, target_date=f"{date.today().year + 1}-01-01")
    (atrasada,) = _metas(uid)
    update_pocket_meta(uid, pid, target_date=(date.today() - timedelta(days=1)).isoformat())
    (vencida,) = _metas(uid)
    mes = f"{date.today():%Y-%m}"
    assert (atrasada["key"], vencida["key"]) == (f"goal_behind:{pid}:atrasada:{mes}",
                                                 f"goal_behind:{pid}:vencida:{mes}")
    assert vencida["severity"] == "critical"


def test_orcamento_que_estoura_e_o_mesmo_fato_mais_grave(llm):  # noqa: F811
    uid = _usuario("pro_max")
    db.upsert_budget(uid, "mercado", 100)
    db.add_launch_and_update_balance(uid, "despesa", 90, "Loja", None, categoria="mercado")
    (apertando,) = ai_patterns.generate_ai_insights(uid, force=True)
    db.add_launch_and_update_balance(uid, "despesa", 20, "Loja", None, categoria="mercado")
    (estourou,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert apertando["key"] == estourou["key"] == f"budget_warning:mercado:{date.today():%Y-%m}"
    assert (apertando["severity"], estourou["severity"]) == ("warning", "critical")  # a piora que a A2 compara


# ── dado pessoal no nome não vai à chave ────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "Alimentação joao@x.com", "Netflix 123.456.789-00", "Netflix 12345678900", "CPF12345678900x",
    "Fornecedor 12.345.678/0001-90", "Viagem (11) 98888-7777", "Viagem 11 98888-7777", "Tel 11 3333-4444",
    "Casa 98888-7777", "+5511988887777", "+55 11 98888-7777", "Cartão 4111 1111 1111 1111",
    "Cartão 4111111111111111", "Cartão 4111.1111.1111.1111", "CPF 123 456 789 00",
])
def test_tem_pii_reconhece_o_que_e_pessoal(texto):
    assert tem_pii(texto)


@pytest.mark.parametrize("texto", [
    "Boleto 1234567890", "Aluguel 2024", "Parcela 3/12", "Conta 1234", "Apto 101", "Reserva 1.234,56",
    "Fundo 10000000", "Casa R$ 1.500,00/mês", "Viagem 2025-2026", "Parcela 1/12 R$ 1.234,56",
    "Seguro 2025/2026", "0800 123 4567",
])
def test_tem_pii_deixa_numero_comum(texto):
    assert not tem_pii(texto)


def test_sujeito_com_dado_pessoal_vira_hash_estavel():
    a = sujeito("Alimentação joao@x.com", "alimentacao joao@x.com")
    assert "joao" not in a and a == sujeito("Alimentação joao@x.com", "alimentacao joao@x.com")
    assert sujeito("Enel joao@x.com", "enel joao x com") != "enel joao x com"  # o merchant_key já tirou o @
    assert sujeito("Alimentação", "alimentacao") == "alimentacao"  # positivo: nome comum fica legível


def test_categoria_com_email_nao_vaza_na_chave_e_aparece_para_o_usuario(llm):  # noqa: F811
    uid = _usuario("pro_max")
    db.upsert_budget(uid, "alimentação joao@x.com", 100)
    db.add_launch_and_update_balance(uid, "despesa", 90, "Loja", None, categoria="alimentação joao@x.com")
    (it,) = ai_patterns.generate_ai_insights(uid, force=True)
    (de_novo,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert "joao" not in it["key"] and it["key"] == de_novo["key"]
    assert "joao@x.com" in it["explicacao"]["referencia"]["categoria"]  # o usuário vê o próprio nome
