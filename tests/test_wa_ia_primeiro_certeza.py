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
    # Era certo; agora incerto: sem verbo, "no" sobra e não é do alvo (forma curta estrita).
    ("R$ 1.234,56 no aluguel", _a(1234.56, "aluguel"), False),
    ("R$ 1.234,56 aluguel", _a(1234.56, "aluguel"), True),     # forma curta de verdade
    # Era certo; agora incerto: número por extenso não conta como "um número só".
    ("cinquenta reais no mercado", _a(50, "mercado"), False),
    ("mercado 80", _a(80, "mercado"), True),
    ("uber 23", _a(23, "uber"), True),
    ("paguei 32,90 no ifood", _a(32.9, "ifood"), True),
    ("comprei um mercado de 280", _a(280, "mercado"), True),   # "um" é artigo, não número
    ("caiu 200 no mercado", _a(200, "mercado", "receita"), True),
    ("pinguei 90 no mercado", _a(90, "mercado", "receita"), True),
    ("mercado 80", _a(80, "mercado", "receita"), False),       # forma curta é só despesa
    ("o mercado me devolveu 50 reais", _a(50, "mercado"), False),
    ("o mercado me devolveu 50 reais", _a(50, "mercado", "receita"), False),   # sem verbo de receita
    ("gastei cinquenta reais no mercado com dois amigos", _a(52, "mercado"), False),
    ("gastei 50 no mercado com dois amigos", _a(50, "mercado"), False),
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


# ── A hashtag atravessa a confirmação: o "sim" grava o que a pergunta mostrou ─

def _categorias(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria from launches where user_id=%s order by id", (uid,))
        rows = [x["categoria"] for x in cur.fetchall()]
        conn.commit()
    return rows


@pytest.mark.parametrize("categoria_ia", ["lazer", None], ids=["ia-ecoa", "ia-omite"])
def test_hashtag_confirmada_grava_a_categoria_da_hashtag(uid_pro, monkeypatch, categoria_ia):
    liga_flag(monkeypatch)
    db.add_category_rule(uid_pro, "posto", "transporte")
    extra = {"categoria": categoria_ia} if categoria_ia else {}
    openai_falso(monkeypatch, lancamento(50, alvo="posto", **extra))
    r = diga(uid_pro, "gastei 50 no posto #lazer")
    assert "Só confirmando" in r and "#lazer" in r, r
    diga(uid_pro, "sim")
    assert _categorias(uid_pro) == ["lazer"]


def test_marca_de_hashtag_forjada_pelo_modelo_e_ignorada(uid_pro, monkeypatch):
    """Sem hashtag no texto, a marca vinda do modelo não pode tirar a categoria
    do cross-check: a regra local (posto→transporte) vence como sempre."""
    liga_flag(monkeypatch)
    db.add_category_rule(uid_pro, "posto", "transporte")
    openai_falso(monkeypatch, lancamento(50, alvo="posto", categoria="lazer",
                                         _categoria_explicita=True))
    r = diga(uid_pro, "gastei 50 no posto")
    assert "Só confirmando" not in r, r
    assert _categorias(uid_pro) == ["transporte"]


def test_hashtag_com_caixa_resumo_mostra_o_que_grava(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    db.add_category_rule(uid_pro, "posto", "transporte")
    openai_falso(monkeypatch, lancamento(50, alvo="posto"))
    r = diga(uid_pro, "gastei 50 no posto #Lazer")
    assert "Só confirmando" in r and "#lazer" in r and "#Lazer" not in r, r
    diga(uid_pro, "sim")
    assert _categorias(uid_pro) == ["lazer"]


# ── Outra janela re-arma entre gravar e perguntar ───────────────────────────

_DA_OUTRA = {"tipo": "despesa", "valor": 77, "alvo": "padaria"}


def test_confirma_nao_aparece_se_outra_janela_sobrescreveu(uid_pro, monkeypatch):
    """O /ai/chat aberto junto re-arma logo depois desta gravação. O WhatsApp
    não pode mostrar "registrar R$ 500" sobre a linha da outra: o "sim" daqui
    executaria a dela."""
    from core.services.ai_chat import runner
    liga_flag(monkeypatch)
    original = db.ai_set_pending_action

    def corrida(user_id, name, args, summary):
        meu = original(user_id, name, args, summary)
        original(user_id, "add_launch", _DA_OUTRA, "despesa de R$ 77,00 em padaria")
        return meu

    monkeypatch.setattr(db, "ai_set_pending_action", corrida)
    openai_falso(monkeypatch, lancamento(500))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert runner._OUTRO_PEDIDO in r and "Só confirmando" not in r, r
    pend = db.ai_get_pending_action(uid_pro)
    assert (pend["tool_name"], pend["tool_args"]) == ("add_launch", _DA_OUTRA)
    monkeypatch.setattr(db, "ai_set_pending_action", original)
    diga(uid_pro, "sim")                   # é a que o usuário viu na outra janela
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 77.0}]


# ── Q40 armada pelo add_launch certo, junto de outra escrita ────────────────

_PAINEL = "gastei 50 no mercado e manda meu painel"


def _lanca_e_painel():
    from tests._ia_falsa_helpers import chamada, com_tools
    return com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "a"),
        chamada("open_dashboard", {}, "d"),
    )


def test_q40_com_outra_escrita_nada_roda(uid_pro, monkeypatch):
    from core.services.ai_chat import runner
    _connect_fake_bank(uid_pro)
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, _lanca_e_painel())
    r = diga(uid_pro, _PAINEL)
    assert runner._UM_POR_VEZ in r, r
    assert lancamentos(uid_pro) == []
    assert _pend(uid_pro) is None


def test_q40_sem_of_os_dois_rodam_como_hoje(uid_pro, monkeypatch):
    from core.services.ai_chat import runner
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, _lanca_e_painel())
    r = diga(uid_pro, _PAINEL)
    assert runner._UM_POR_VEZ not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


def test_q40_fora_do_whatsapp_com_flag_ou_flag_desligada_inalterado(uid_pro, monkeypatch):
    from core.services.ai_chat import runner
    _connect_fake_bank(uid_pro)
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _lanca_e_painel())
    r = diga(uid_pro, "piggy " + _PAINEL)
    assert runner._UM_POR_VEZ not in r and _PERGUNTE_A_FORMA.split("`")[0] in r, r
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, _lanca_e_painel())
    r = runner.chat(uid_pro, _PAINEL, monthly_limit=10, platform="dashboard")
    assert r != runner._UM_POR_VEZ and "Nada foi gravado" in r, r
    assert lancamentos(uid_pro) == []


# ── Negação e pergunta não são registro ─────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "não gastei 50 no mercado", "nunca gastei 50 no mercado", "jamais gastei 50 no mercado",
    "gastei 50 no mercado?", "NÃO gastei 50 no mercado",
], ids=["nao", "nunca", "jamais", "pergunta", "caixa"])
def test_negacao_ou_pergunta_confirma(uid_pro, monkeypatch, frase):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, frase)
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


# ── Valor e tipo por lista positiva ─────────────────────────────────────────

@pytest.mark.parametrize("frase,args", [
    ("gastei cinquenta reais no mercado com dois amigos", lancamento(52)),
    ("gastei 50 no mercado com dois amigos", lancamento(50)),   # 1 dígito + 1 extenso
    ("o mercado me devolveu 50 reais", lancamento(50)),
    ("o mercado me devolveu 50 reais", lancamento(50, tipo="receita")),
], ids=["extenso-soma-52", "digito-e-extenso", "devolveu-despesa", "devolveu-receita"])
def test_valor_ou_tipo_sem_apoio_confirma(uid_pro, monkeypatch, frase, args):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, args)
    r = diga(uid_pro, frase)
    assert "Só confirmando" in r, r
    assert lancamentos(uid_pro) == []


@pytest.mark.parametrize("frase,valor,alvo", [
    ("gastei 50 no mercado", 50, "mercado"),
    ("mercado 80", 80, "mercado"),
    ("uber 23", 23, "uber"),
], ids=["verbo", "forma-curta-mercado", "forma-curta-uber"])
def test_verbo_ou_forma_curta_grava_direto(uid_pro, monkeypatch, frase, valor, alvo):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(valor, alvo=alvo))
    r = diga(uid_pro, frase)
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": float(valor)}]


def test_nao_dentro_de_outra_palavra_nao_e_negacao(uid_pro):
    """"nao" só conta como palavra inteira: "nanao" / "banao" não disparam."""
    assert lancamento_com_certeza(uid_pro, _a(50, "mercado"), "gastei 50 no mercado banao") is True
    assert lancamento_com_certeza(uid_pro, _a(50, "mercado"), "gastei 50 no mercado nao") is False


# ── O dia mostrado é o dia gravado, mesmo com o "sim" depois da meia-noite ──

@pytest.mark.parametrize("data_ia", [None, "amanhã"], ids=["sem-data", "data-ilegivel"])
def test_sim_depois_da_meia_noite_grava_o_dia_mostrado(uid_pro, monkeypatch, data_ia):
    from datetime import datetime
    import utils_date
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")
    tz = utils_date._tz()
    relogio = {"agora": datetime(2026, 10, 8, 23, 58, tzinfo=tz)}
    monkeypatch.setattr(utils_date, "now_tz", lambda: relogio["agora"])
    liga_flag(monkeypatch)
    extra = {"data": data_ia} if data_ia else {}
    openai_falso(monkeypatch, lancamento(500, **extra))     # usuário disse 50: confirma
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" in r and "hoje" in r, r
    assert db.ai_get_pending_action(uid_pro)["tool_args"]["data"] == "2026-10-08"
    relogio["agora"] = datetime(2026, 10, 9, 0, 3, tzinfo=tz)
    diga(uid_pro, "sim")
    assert _dias_gravados(uid_pro) == ["2026-10-08"]
