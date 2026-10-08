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
    assert lancamento_com_certeza(uid_pro, args, frase) is certo


def test_salario_sem_regra_do_usuario_confirma_pela_categoria(uid_pro):
    assert lancamento_com_certeza(uid_pro, _a(1000, "salário", "receita"),
                                  "recebi 1000 de salário") is False
