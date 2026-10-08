"""WA_IA_PRIMEIRO — o `add_launch` da IA grava direto só com certeza.

Conversa pelo `handle_incoming` com o runner de verdade e o modelo falso
(`tests/_ia_falsa_helpers.py`); banco real. A tabela no fim exercita
`lancamento_com_certeza` sozinha.
"""
from __future__ import annotations

import pytest

import db
from core.handlers import launches as h_launches
from core.services.ai_chat import runner
from core.services.ai_chat.tools.launches import _PERGUNTE_A_FORMA
from core.services.wa_ia_primeiro import lancamento_com_certeza
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import (
    desliga_flag, lancamento, lancamentos, liga_flag, openai_falso, texto,
)
from tests._pendencia_credito_helpers import diga
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank


def _pend(uid):
    p = db.get_pending_action(uid)
    return p["action_type"] if p else None


def test_c1_coerente_grava_direto_com_botoes(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]
    assert _pend(uid_pro) == "recategorize_launch_offer"


@pytest.mark.parametrize("resposta,esperado", [
    ("sim", [{"tipo": "despesa", "valor": 500.0}]),
    ("não", []),
])
def test_c2_valor_divergente_confirma(uid_pro, monkeypatch, resposta, esperado):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(500))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" in r and "R$ 500,00" in r, r
    assert lancamentos(uid_pro) == []
    assert db.ai_get_pending_action(uid_pro)["tool_name"] == "add_launch"
    diga(uid_pro, resposta)
    assert lancamentos(uid_pro) == esperado


def test_c3_dois_numeros_confirma(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(16, alvo="café"))
    r = diga(uid_pro, "paguei 2 cafés de 8")
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


def test_c4_alvo_sem_regra_confirma_e_com_regra_grava(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(40, alvo="zé"), lancamento(40, alvo="zé"))
    assert "Só confirmando" in diga(uid_pro, "gastei 40 no zé")
    diga(uid_pro, "não")
    db.add_category_rule(uid_pro, "ze", "lazer")
    r = diga(uid_pro, "gastei 40 no zé")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 40.0}]


def test_c5_flag_desligada_executa_direto_como_hoje(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(500))
    diga(uid_pro, "piggy gastei 50 no mercado")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


def test_c6_flag_ligada_no_dashboard_executa_direto(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(500))
    runner.chat(uid_pro, "gastei 50 no mercado", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


def test_c7_com_of_pergunta_a_forma_ao_usuario(uid_pro, monkeypatch):
    _connect_fake_bank(uid_pro)
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "dinheiro vivo" in r and "Nada foi gravado" not in r, r
    assert _pend(uid_pro) == "payment_method_choice"
    assert lancamentos(uid_pro) == []
    r = diga(uid_pro, "dinheiro")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}], r


def test_c7_off_flag_desligada_com_of_mantem_o_texto_de_hoje(uid_pro, monkeypatch):
    _connect_fake_bank(uid_pro)
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, "piggy gastei 50 no mercado")
    # O formatador do WhatsApp tira as crases: compara até elas.
    assert _PERGUNTE_A_FORMA.split("`")[0] in r, r
    assert lancamentos(uid_pro) == []


def test_c8_desfazer_depois_de_leitura(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50), texto("🐷 Sobrou R$ 950,00."))
    diga(uid_pro, "gastei 50 no mercado")
    assert "Sobrou" in diga(uid_pro, "qto sobrou esse mes")
    launch_id = int(db.list_launches(uid_pro, limit=1)[0]["id"])
    h_launches.propose_delete(uid_pro, launch_id)   # o botão "Desfazer"
    diga(uid_pro, "sim")
    assert lancamentos(uid_pro) == []


def test_c9_receita_que_a_ia_mandou_como_despesa_confirma(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    # Com a regra, a categoria é certa: só o tipo pode pedir a confirmação.
    db.add_category_rule(uid_pro, "salario", "salário")
    openai_falso(monkeypatch, lancamento(1000, alvo="salário", tipo="despesa"))
    r = diga(uid_pro, "recebi 1000 de salário")
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


def test_c9_positivo_receita_coerente_grava_direto(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    db.add_category_rule(uid_pro, "salario", "salário")
    openai_falso(monkeypatch, lancamento(1000, alvo="salário", tipo="receita"))
    r = diga(uid_pro, "recebi 1000 de salário")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "receita", "valor": 1000.0}]


def _dois_lancamentos():
    """O 1º bate com o texto (certo); o 2º a IA inventou (incerto)."""
    from tests._ia_falsa_helpers import chamada, com_tools
    return com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "a"),
        chamada("add_launch", {"tipo": "despesa", "valor": 40, "alvo": "zé"}, "b"),
    )


def test_varios_add_launch_com_um_incerto_nao_grava_nada_fora_do_ia_primeiro(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, _dois_lancamentos())
    r = diga(uid_pro, "piggy gastei 50 no mercado")   # prefixo: chat sem ia_primeiro
    assert runner._UM_POR_VEZ in r, r
    assert lancamentos(uid_pro) == []
    assert db.ai_get_pending_action(uid_pro) is None
    h = db.ai_get_recent_messages(uid_pro, limit=20)
    assert h[-1] == {"role": "assistant", "content": runner._UM_POR_VEZ}
    assert not any(m.get("tool_calls") for m in h)


def test_varios_add_launch_flag_desligada_grava_como_hoje(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _dois_lancamentos())
    diga(uid_pro, "piggy gastei 50 no mercado")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0},
                                    {"tipo": "despesa", "valor": 40.0}]


def _a(valor, alvo, tipo="despesa"):
    return {"tipo": tipo, "valor": valor, "alvo": alvo}


@pytest.mark.parametrize("frase,args,certo", [
    ("77,90 mercado", _a(77.9, "mercado"), True),
    ("R$ 1.234,56 no aluguel", _a(1234.56, "aluguel"), True),
    ("cinquenta reais no mercado", _a(50, "mercado"), True),
    ("torrei 30 no ifood", _a(30, "ifood"), True),
    ("ontem gastei 50 no mercado", _a(50, "mercado"), True),
    ("Gastei 50 no MERCADO", _a(50, "MERCADO"), True),
    ("gastei 50 no mercado às 15h", _a(50, "mercado"), False),
    ("gastei 50 no mercado", _a(500, "mercado"), False),
    ("gastei 50 no mercado", _a(50, "mercado", "receita"), False),
    ("recebi 1000 de salário", _a(1000, "salário", "receita"), True),
    ("Recebi 1000 de Salário", _a(1000, "salário", "receita"), True),
    ("recebi 1000 de salário", _a(1000, "salário"), False),
    ("1000 de salário", _a(1000, "salário", "receita"), False),
    ("ontem recebi 1000 de salário", _a(1000, "salário", "receita"), True),
    ("gastei 40 no zé", _a(40, "zé"), False),
])
def test_tabela_lancamento_com_certeza(uid_pro, frase, args, certo):
    # "salário" não tem regra local: sem a do usuário, toda receita de salário
    # confirmaria pela categoria, e a tabela não mediria o tipo.
    db.add_category_rule(uid_pro, "salario", "salário")
    if frase.startswith("ontem"):
        # A IA coerente manda o dia de ontem (calculado aqui, não na coleta).
        args = {**args, "data": _dia(-1)}
    assert lancamento_com_certeza(uid_pro, args, frase) is certo


def test_salario_sem_regra_do_usuario_confirma_pela_categoria(uid_pro):
    assert lancamento_com_certeza(uid_pro, _a(1000, "salário", "receita"),
                                  "recebi 1000 de salário") is False


# ── A última cota do mês vai na mensagem que arma a confirmação ─────────────
# O "sim"/"não" dessa pergunta não gasta cota (o runner o trata antes dela):
# sem cota sobrando, ele ainda tem de chegar à pendência.

def _na_ultima_cota(uid):
    from core.services.plan_service import ai_monthly_limit_for
    from db.ai_quota import _current_month_start
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set ai_messages_this_month=%s, ai_month_reset_at=%s "
                    "where user_id=%s",
                    (ai_monthly_limit_for(uid) - 1, _current_month_start(), uid))
        conn.commit()


def _arma_com_a_ultima_cota(uid, monkeypatch):
    from core.services.plan_service import ai_chat_allowed
    liga_flag(monkeypatch)
    _na_ultima_cota(uid)
    openai_falso(monkeypatch, lancamento(500))
    assert "Só confirmando" in diga(uid, "gastei 50 no mercado")
    assert not ai_chat_allowed(uid), "a cota devia ter acabado nesta mensagem"


@pytest.mark.parametrize("resposta,esperado", [
    ("sim", [{"tipo": "despesa", "valor": 500.0}]),
    ("não", []),
])
def test_cota_no_fim_sim_e_nao_ainda_resolvem_a_confirmacao(uid_pro, monkeypatch, resposta, esperado):
    _arma_com_a_ultima_cota(uid_pro, monkeypatch)
    diga(uid_pro, resposta)
    assert lancamentos(uid_pro) == esperado
    assert db.ai_get_pending_action(uid_pro) is None


def test_downgrade_para_free_sim_nao_executa(uid_pro, monkeypatch):
    """Sem IA no plano (não só sem cota), a confirmação antiga não executa: o
    gate de sempre descarta a pendência."""
    from core.services.plan_service import ai_chat_allowed
    from tests.conftest import promote_to_pro
    from core.services.ai_chat_commands import aviso_de_cota
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(500))
    assert "Só confirmando" in diga(uid_pro, "gastei 50 no mercado")
    # Sem IA no plano só existe no v1 (`ai_chat_allowed` = is_pro): no v2 todo
    # tier tem IA e só a cota barra (`aviso_de_cota` com texto).
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    promote_to_pro(uid_pro, plan="free")
    assert not ai_chat_allowed(uid_pro) and not aviso_de_cota(uid_pro)
    r = diga(uid_pro, "sim")
    assert lancamentos(uid_pro) == []
    assert db.ai_get_pending_action(uid_pro) is None
    assert "PigBank+" in r, r


def test_cota_no_fim_outro_texto_mantem_aviso_e_limpa(uid_pro, monkeypatch):
    _arma_com_a_ultima_cota(uid_pro, monkeypatch)
    r = diga(uid_pro, "hmm sei la")
    assert "acabaram" in r, r
    assert db.ai_get_pending_action(uid_pro) is None
    assert lancamentos(uid_pro) == []


# ── Data: a gravação usa `args["data"]`, não a data do texto ─────────────────

def _dia(delta: int = 0) -> str:
    from datetime import timedelta
    from utils_date import today_tz
    return (today_tz() + timedelta(days=delta)).isoformat()


def _dias_gravados(uid):
    from utils_date import _tz
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select criado_em from launches where user_id=%s order by id", (uid,))
        rows = cur.fetchall()
        conn.commit()
    return [r["criado_em"].astimezone(_tz()).date().isoformat() for r in rows]


@pytest.mark.parametrize("frase,data_ia", [
    ("ontem gastei 50 no mercado", None),          # a IA esqueceu a data
    ("ontem gastei 50 no mercado", "hoje"),
    ("gastei 50 no mercado dia 03/10", "2026-10-04"),
    ("gastei 50 no mercado dia 3", "2026-10-04"),  # "dia 3" sem mês: o 2º número já pega
    ("gastei 50 no mercado", -2),                  # texto sem data, IA com outro dia
    ("gastei 50 no mercado", "amanhã"),            # data que não se lê
], ids=["ontem-sem-data", "ontem-ia-hoje", "03-10-ia-04", "dia-3-ia-4", "sem-data-ia-anteontem",
        "data-ilegivel"])
def test_data_divergente_confirma(uid_pro, monkeypatch, frase, data_ia):
    liga_flag(monkeypatch)
    extra = {}
    if data_ia == "hoje":
        extra["data"] = _dia(0)
    elif isinstance(data_ia, int):
        extra["data"] = _dia(data_ia)
    elif data_ia:
        extra["data"] = data_ia
    openai_falso(monkeypatch, lancamento(50, **extra))
    r = diga(uid_pro, frase)
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


@pytest.mark.parametrize("frase,delta", [
    ("ontem gastei 50 no mercado", -1),
    ("gastei 50 no mercado", 0),
    ("gastei 50 no mercado", None),
], ids=["ontem-ia-ontem", "sem-data-ia-hoje", "sem-data-ia-sem-data"])
def test_data_coerente_grava_direto_no_dia_certo(uid_pro, monkeypatch, frase, delta):
    liga_flag(monkeypatch)
    extra = {} if delta is None else {"data": _dia(delta)}
    openai_falso(monkeypatch, lancamento(50, **extra))
    r = diga(uid_pro, frase)
    assert "Só confirmando" not in r, r
    assert _dias_gravados(uid_pro) == [_dia(delta or 0)]


@pytest.mark.parametrize("agora,frase,data_ia,certo", [
    # 22:30 em São Paulo = 01:30 UTC do dia seguinte: "hoje" é o dia 8, não o 9.
    ((2026, 10, 8, 22, 30), "gastei 50 no mercado", "2026-10-08", True),
    ((2026, 10, 8, 22, 30), "gastei 50 no mercado", "2026-10-09", False),
    ((2026, 10, 8, 22, 30), "ontem gastei 50 no mercado", "2026-10-07", True),
    ((2026, 10, 8, 22, 30), "ontem gastei 50 no mercado", "2026-10-08", False),
    # 00:10 do dia 9: "ontem" é o dia 8.
    ((2026, 10, 9, 0, 10), "ontem gastei 50 no mercado", "2026-10-08", True),
    ((2026, 10, 9, 0, 10), "ontem gastei 50 no mercado", "2026-10-07", False),
])
def test_data_perto_da_meia_noite_no_fuso_do_app(uid_pro, monkeypatch, agora, frase, data_ia, certo):
    from datetime import datetime
    import utils_date
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")
    congelado = datetime(*agora, tzinfo=utils_date._tz())
    monkeypatch.setattr(utils_date, "now_tz", lambda: congelado)
    args = {"tipo": "despesa", "valor": 50, "alvo": "mercado", "data": data_ia}
    assert lancamento_com_certeza(uid_pro, args, frase) is certo


@pytest.mark.parametrize("frase,delta", [
    ("ontem gastei 50 no mercado", -1),
    ("gastei 50 no mercado", 0),
], ids=["ontem-com-espacos", "hoje-com-espacos"])
def test_data_com_espacos_nunca_grava_noutro_dia(uid_pro, monkeypatch, frase, delta):
    """A gravação lê `data` crua: " 2026-10-07 " não é ISO e vira "agora". A
    certeza lê pelo mesmo parser, então não aprova — confirma, e o "sim" grava
    onde a gravação gravaria (nunca hoje no lugar de ontem sem perguntar)."""
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, data=f" {_dia(delta)} "))
    r = diga(uid_pro, frase)
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


# ── Hashtag: a regra local da nota não pode contradizê-la ───────────────────

def test_hashtag_contrariada_por_regra_local_confirma(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    db.add_category_rule(uid_pro, "posto", "transporte")
    openai_falso(monkeypatch, lancamento(50, alvo="posto", categoria="lazer"))
    r = diga(uid_pro, "gastei 50 no posto #lazer")
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


def test_hashtag_sem_conflito_grava_direto_na_categoria_dela(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, alvo="zé", categoria="lazer"))
    r = diga(uid_pro, "gastei 50 no zé #lazer")
    assert "Só confirmando" not in r, r
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria from launches where user_id=%s", (uid_pro,))
        cats = [x["categoria"] for x in cur.fetchall()]
        conn.commit()
    assert cats == ["lazer"]


# ── Forma de pagamento: a da IA só vale se o texto disse a mesma ────────────

@pytest.mark.parametrize("frase", ["gastei 50 no mercado", "gastei 50 no mercado no pix"])
def test_forma_dinheiro_da_ia_sem_apoio_no_texto_confirma(uid_pro, monkeypatch, frase):
    """Com OF, "dinheiro" é a única forma que grava na Carteira: inventada pela
    IA, o gasto entra em dobro quando o banco importar o mesmo."""
    _connect_fake_bank(uid_pro)
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, forma_pagamento="dinheiro"))
    r = diga(uid_pro, frase)
    assert "Só confirmando" in r and "em dinheiro" in r, r
    assert lancamentos(uid_pro) == []


def test_forma_dinheiro_dita_no_texto_grava_direto(uid_pro, monkeypatch):
    _connect_fake_bank(uid_pro)
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "gastei 50 no mercado em dinheiro")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


# ── Alvo e nota: palavras do texto ──────────────────────────────────────────

@pytest.mark.parametrize("args", [
    {"alvo": "taxi"},
    {"alvo": "uber", "nota": "uber pro aeroporto"},   # categoria certa, nota inventada
], ids=["alvo-trocado", "nota-inventada"])
def test_alvo_ou_nota_fora_do_texto_confirma(uid_pro, monkeypatch, args):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, **args))
    r = diga(uid_pro, "gastei 50 no uber")
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


def test_alvo_do_texto_grava_direto(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, alvo="uber"))
    r = diga(uid_pro, "gastei 50 no uber")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


# ── O resumo da confirmação mostra o dia que vai ser gravado ────────────────

def test_resumo_mostra_hoje_quando_a_ia_nao_manda_data(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, "ontem gastei 50 no mercado")
    assert "Só confirmando" in r and "hoje" in r and "ontem" not in r.split("registrar", 1)[1], r


def test_resumo_mostra_a_data_que_a_ia_mandou(uid_pro, monkeypatch):
    from datetime import date
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, data="2026-03-04"))
    r = diga(uid_pro, "gastei 50 no mercado dia 05/03/2026")
    assert "Só confirmando" in r and date(2026, 3, 4).strftime("%d/%m/%Y") in r, r


@pytest.mark.parametrize("args,trecho", [
    ({"alvo": "mercado", "nota": 123}, "em mercado (123)"),
    ({"alvo": 123}, "em 123"),
    ({"alvo": "mercado", "nota": ["a", "b"]}, "em mercado (['a', 'b'])"),
    ({"tipo": 7, "alvo": "mercado", "categoria": 9}, "#9"),
], ids=["nota-int", "alvo-int", "nota-lista", "tipo-e-categoria-int"])
def test_resumo_nao_levanta_com_tipos_estranhos(args, trecho):
    from core.services.ai_chat.tools.launches import _add_launch_summary
    resumo = _add_launch_summary({"tipo": "despesa", "valor": 50, **args})
    assert trecho in resumo and "R$ 50,00" in resumo, resumo


def test_nota_numerica_da_ia_confirma_em_vez_de_cair_no_roteador(uid_pro, monkeypatch):
    """Exceção no resumo, no modo ia_primeiro, virava None: a mensagem caía no
    roteador e o usuário via outra resposta, sem a pergunta."""
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, nota=123))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" in r and "(123)" in r, r
    assert lancamentos(uid_pro) == []
