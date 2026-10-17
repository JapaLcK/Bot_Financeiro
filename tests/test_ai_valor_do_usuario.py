"""O dinheiro que a IA grava tem que ter sido escrito pelo usuário
(`core/services/ai_chat/valor_do_usuario.py`).

Conversas com o runner e o banco de verdade; só o modelo é falso
(`tests/_ia_falsa_helpers.py`). Flag WA_IA_PRIMEIRO desligada, como em produção.

Controle negativo: com a âncora desligada (`runner.ancora_valor` devolvendo os
args), N1–N5 ficam vermelhos. Os P* (caminho legítimo) valem nos dois estados.
"""
from __future__ import annotations

import pytest

import db
from core.services.ai_chat import runner
from core.services.ai_chat._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE
from core.services.ai_chat.tools import _BY_NAME, get_tool
from core.services.ai_chat.valor_do_usuario import _centavos_lidos, ancora_valor
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import (
    chamada, com_tools, desliga_flag, lancamento, lancamentos, openai_falso,
)
from tests._pendencia_credito_helpers import diga
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank

RECUSA = "Não anotei nada: não achei esse valor no que você escreveu. Qual é o valor?"
RECUSA_PARCELAS = "🐷 Não anotei nada: em quantas parcelas foi? Ex.: 12x."


# ── (1) o que a âncora lê do texto do usuário ───────────────────────────────

@pytest.mark.parametrize("texto,tem,nao_tem", [
    ("50", {5000}, set()),
    ("50,5", {5050}, set()),
    ("R$ 1.234,56", {123456}, set()),
    ("1,2 mil", {120000}, set()),
    ("cinquenta", {5000}, set()),
    ("mil e duzentos", {120000}, set()),
    ("torrei 80 conto no bar", {8000}, set()),
    ("dia 20 paguei 300", {30000}, {2000}),
    ("12x de 50", {5000}, {1200}),
    ("gastei 50 em 2026", {5000}, {202600}),
    # máscaras: número que não é valor (e o valor legítimo ao lado segue lido)
    ("cartão final 1234", set(), {123400}),
    ("CPF 123.456.789-09", set(), {12345678909}),
    ("CNPJ 12.345.678/0001-95", set(), {1234567800, 12345678000195}),
    ("ano 2026", set(), {202600}),
    ("às 15:30", set(), {1500, 1530}),
    ("aumentou 10%", set(), {1000}),
    ("17/10/2026", set(), {1700, 1000, 202600}),
    ("17/10", set(), {1700, 1000}),
    ("ag 1234 cep 78000-000", set(), {123400, 7800000, 78000000}),
    ("17/10 paguei R$ 1.234,56", {123456}, set()),
    ("recebi 1.250.000", {125000000}, set()),
    ("café 49,90 às 15:30", {4990}, {1500}),
    ("desconto de 10% em 200", {20000}, {1000}),
    ("paguei a conta 150", {15000}, set()),
    # `conta`/`cartão`/`final` antes de dinheiro NÃO mascaram (só "cartão final 1234")
    ("paguei a conta 1200", {120000}, set()),
    ("conta 1500", {150000}, set()),
    ("na conta 1200 reais", {120000}, set()),
    ("comprei no cartão 3000 em 3x", {300000}, set()),
    ("valor final 1500", {150000}, set()),
    ("preço final 2300", {230000}, set()),
    ("conta 1500,50", {150050}, {5000}),   # a vírgula não deixa sobrar ",50" como R$ 50
    ("conta final 1234", set(), {123400}),
    # centavos: "n centavos" é n, não R$ n; "reais e centavos" segue somado
    ("gastei 50 centavos", {50}, {5000}),
    ("50 centavos no bombom", {50}, {5000}),
    ("75 centavos", {75}, {7500}),
    ("gastei 10 no uber e 50 centavos no bombom", {1000, 50}, {5000}),
    ("30 reais e 50 centavos", {3050}, {50}),
    # centavos por extenso são n centavos, não R$ n
    ("gastei cinquenta centavos", {50}, {5000}),
    ("vinte e cinco centavos", {25}, {2500, 2000, 500}),
    ("um centavo", {1}, {100}),
    ("trinta reais e cinquenta centavos", {3000, 50}, {5000}),
    # "um"/"uma" soltos são artigo, não R$ 1
    ("comprei uma tv", set(), {100}),
    ("comprei um carro", set(), {100}),
    ("paguei um real", {100}, set()),
    ("paguei uma pizza de 40", {4000}, {100}),
    # extenso lido por trecho, não a soma da mensagem
    ("gastei cinquenta no mercado e vinte no uber", {5000, 2000}, {7000}),
    ("cinquenta e cinco", {5500}, {5000, 500}),
    ("vinte e cinco", {2500}, {2000, 500}),
    ("cento e vinte", {12000}, {10000}),
    ("mil e duzentos", {120000}, set()),
    ("dois mil e trezentos e cinquenta", {235000}, set()),
    ("cento e vinte mil e quinhentos", {12050000}, set()),
    ("cinquenta e vinte", {5000, 2000}, {7000}),   # "e" só junta quando o nível desce
    # ponto + 1-2 dígitos é decimal (o ai_guard lê ponto como milhar: 49.90 virava 499000)
    ("gastei 49.90", {4990}, {499000}),
    ("gastei 49.9", {4990}, {49900, 499000}),   # 1 casa decimal também é decimal
    ("0.50", {50}, {5000}),
    ("gastei 2.5", {250}, {2500}),
    # ponto + 3 dígitos segue milhar; vírgula decimal e data não mudam
    ("1.200", {120000}, {120}),
    ("1.200,50", {120050}, set()),
    ("12.345", {1234500}, {1234}),
    ("1.000.000", {100000000}, set()),
    ("49,90", {4990}, {499000}),
    ("10.12.2026", set(), {1012, 101200}),
    # multiplicador por extenso: n×milhão/bilhão (reais → centavos); "e meio" fica de fora
    ("meu orçamento é dois milhões", {200000000}, {200, 20000}),
    ("um milhão", {100000000}, {100}),
    ("três bilhões", {300000000000}, set()),
    ("dois milhões de reais", {200000000}, set()),
    ("dois milhões e meio", {200000000}, {250000000}),
    ("duzentos mil", {20000000}, set()),
    # "<dígitos> mil|milhão|bilhão": só o valor expandido ancora, nunca o componente (R$ 2) nem o "mil" solto (R$ 1.000)
    ("gastei 2 mil", {200000}, {200, 100000}),
    ("gastei 2 MIL", {200000}, {200, 100000}),
    ("gastei 2mil", {200000}, {200}),
    ("2,5 mil", {250000}, {250, 100000}),
    ("1.5 mil", {150000}, {150, 100000}),
    ("gastei 2 milhões", {200000000}, {200}),
    ("gastei 2 milhoes", {200000000}, {200}),
    ("1 bilhão", {100000000000}, {100}),
    ("2 mil e 500", {250000, 50000}, {200, 100000}),   # o 500 solto segue ancorando (já ancorava)
    ("gastei 2", {200}, set()),
    ("gastei 2 no mercado", {200}, set()),
    ("1.200", {120000}, set()),                          # milhar, não multiplicador
    ("5k", {500}, set()),                                # limite conhecido, não muda
])
def test_centavos_lidos(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


# Extenso só ancora se a próxima palavra for dinheiro, conector ou o fim; substantivo = contagem de coisa.
@pytest.mark.parametrize("texto,tem,nao_tem", [
    # número + substantivo: contagem, não valor
    ("comprei duas TVs no Nubank", set(), {200}),
    ("dois amigos", set(), {200}),
    ("três pizzas", set(), {300}),
    ("comprei um carro", set(), {100}),
    ("comprei uma tv", set(), {100}),
    ("paguei uma pizza de 40", {4000}, {100}),
    ("gastei cinquenta reais no mercado com dois amigos", {5000}, {200}),
    # número + preposição/conector/fim/pontuação
    ("gastei cinquenta no mercado", {5000}, set()),
    ("paguei vinte na farmácia", {2000}, set()),
    ("paguei trinta pelo uber", {3000}, set()),
    ("gastei dois", {200}, set()),
    ("gastei dois, ontem", {200}, set()),
    ("gastei cinquenta e vinte", {5000, 2000}, {7000}),
    # número + dinheiro
    ("paguei um real", {100}, set()),
    ("paguei dois reais", {200}, set()),
    ("torrei oitenta conto", {8000}, set()),
    ("achei vinte pilas", {2000}, set()),
    ("gastei cinquenta centavos", {50}, {5000}),
    # reais + centavos: componentes e total, nas três grafias
    ("paguei trinta reais e cinquenta centavos", {3000, 50, 3050}, set()),
    ("trinta reais e 50 centavos", {3000, 3050}, {50}),
    ("30 reais e 50 centavos", {3000, 3050}, {50}),
    ("um real e vinte e cinco centavos", {100, 25, 125}, set()),
    ("trinta reais e cinquenta pizzas", {3000}, {3050, 50}),   # só centavos compõem o total
    ("trinta reais e cinquenta", {3000, 5000}, {3050}),
    # multiplicador: a palavra depois dele passa na mesma lista; contagem de coisa não é valor
    ("comprei dois milhões de pizzas", {200000000}, set()),   # "de" é conector (mesmo limite de "dois de ...")
    ("dois milhões pizzas", set(), {200000000}),
    ("um milhão no mercado", {100000000}, set()),
])
def test_extenso_so_ancora_antes_de_dinheiro_conector_ou_fim(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


@pytest.mark.parametrize("digitos", [33, 100, 308, 400])
def test_numero_gigante_nao_estoura_nem_anula_o_resto(digitos):  # float("9"*308) é inf; round(inf) estoura
    lixo = "9" * digitos
    _centavos_lidos(["gastei " + lixo])   # não levanta
    assert 5000 in _centavos_lidos(["gastei 50 no mercado " + lixo])   # o 50 legítimo segue lido
    assert 5000 in _centavos_lidos(["gastei " + lixo, "gastei 50"])    # e outro texto da janela também


@pytest.mark.parametrize("texto", ["foi em dinheiro", "pix", "#50", ""])
def test_texto_sem_valor_nao_le_nada(texto):
    assert _centavos_lidos([texto]) == set()


def _ancora(uid, nome, args, fala):
    tok = CURRENT_USER_MESSAGE.set(fala)
    try:
        return ancora_valor(uid, get_tool(nome), dict(args))
    finally:
        CURRENT_USER_MESSAGE.reset(tok)


def test_regra_da_ancora(uid_pro):
    assert _ancora(uid_pro, "add_launch", {"valor": 50}, "gastei 50")[1] is None
    assert _ancora(uid_pro, "add_launch", {"valor": -50}, "gastei 50")[1] is None  # abs
    assert _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")[1] == "recusa"
    for ilegivel in (float("nan"), float("inf"), "abc"):  # o execute recusa; args intactos
        assert _ancora(uid_pro, "add_launch", {"valor": ilegivel}, "gastei 50")[1] is None
    # cartão: parcelas × valor lido
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "12x de 50")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600}, "12x de 50")[1] == "recusa"
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 601, "parcelas": 12}, "12x de 50")[1] == "recusa"
    # parcelas só multiplicam se o usuário as escreveu (A1)
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 3000, "parcelas": 60}, "comprei 50 no nubank")[1] == "recusa_parcelas"
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "600 em 12x")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 300, "parcelas": 3}, "3 vezes de 100")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "12 parcelas de 50")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 650, "parcelas": 13}, "12x de 50")[1] == "recusa_parcelas"
    # campo opcional sem âncora: descartado; obrigatório: recusa
    args, acao = _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 150}, "paguei a luz")
    assert acao == "descarta" and args == {"name": "luz"}
    assert _ancora(uid_pro, "add_boleto", {"name": "x", "amount": 300}, "anota um boleto")[1] == "recusa"
    assert _ancora(uid_pro, "set_budget", {"categoria": "lazer", "budget": 300}, "orçamento de lazer")[1] == "recusa"


# ── (2) N: a conversa NÃO pode gravar valor que o usuário não escreveu ──────

# "foi em dinheiro" não é comando do roteador: o classificador de intenção (LLM) consulta o
# modelo ANTES do runner. A 1ª rodada do roteiro é dele; falha, e a mensagem cai na IA.
CLASSIFICADOR_FORA = RuntimeError("classificador fora")


def _usa_ia(cliente):
    assert len(cliente.creates) >= 1, "a IA falsa não foi consultada: o teste não mede nada"


def _historico_sem(uid, *numeros):
    """O resultado `tool` que a recusa grava no histórico não carrega o número inventado:
    senão o ai_guard o leria como evidência. Exige ao menos uma linha `tool`."""
    tools = [m["content"] or "" for m in db.ai_get_last_messages(uid, 30) if m["role"] == "tool"]
    assert tools, "a recusa não gravou resultado `tool`: o teste não mede nada"
    for n in numeros:
        assert not any(str(n) in c or str(n).replace(".", ",") in c for c in tools), tools


@pytest.mark.parametrize("valor", [50, 450.73])
def test_n1_torrei_80_conto_foi_em_dinheiro(uid_pro, monkeypatch, valor):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(valor, forma_pagamento="dinheiro"))
    diga(uid_pro, "torrei 80 conto no bar")   # o roteador não acha o valor: 0 chamadas de IA
    assert cli.creates == []
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r, r
    assert lancamentos(uid_pro) == []
    _historico_sem(uid_pro, valor)


def test_n2_valor_so_em_mensagem_do_assistente(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    db.ai_append_message(uid_pro, "user", "oi")
    db.ai_append_message(uid_pro, "assistant", "🐷 Esse valor foi gasto ou recebimento? Ex: *450,73 mercado*")
    db.ai_append_message(uid_pro, "tool", '{"status": "done", "message": "450.73"}', tool_call_id="t0", tool_name="add_launch")
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(450.73, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r, r
    assert lancamentos(uid_pro) == []


def test_n3_quatro_anteriores_nao_ancoram(uid_pro, monkeypatch):  # janela 4 aprovaria
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    for t in ("gastei 500", "oi", "bom dia", "tudo bem"):
        db.ai_append_message(uid_pro, "user", t)
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r and lancamentos(uid_pro) == [], r


def test_n3_tres_anteriores_ainda_ancoram(uid_pro, monkeypatch):
    # a atual ("foi em dinheiro") + 3 anteriores: o 500 é a 3ª anterior. Janela 2 recusaria.
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    for t in ("gastei 500", "oi", "bom dia"):
        db.ai_append_message(uid_pro, "user", t)
    openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    diga(uid_pro, "foi em dinheiro")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


def test_n3_linha_system_corta_a_janela(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    db.ai_append_message(uid_pro, "user", "gastei 500")
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)   # o roteador já gravou
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r and lancamentos(uid_pro) == [], r


@pytest.mark.parametrize("lixo", [float("inf"), float("-inf"), float("nan"), 1e999, "abc", [3], {"a": 1}])
def test_parcelas_lixo_nao_estoura(uid_pro, lixo):  # B1: OverflowError subia pelo runner
    for nome in ("add_launch", "add_credit_purchase"):
        assert _ancora(uid_pro, nome, {"valor": 50, "parcelas": lixo}, "gastei 50")[1] is None  # lido como 1 parcela
    assert _ancora(uid_pro, "add_launch", {"valor": 500, "parcelas": lixo}, "gastei 50")[1] == "recusa"


@pytest.mark.parametrize("args,fala", [
    ({"valor": 1234}, "cartão final 1234"),
    ({"valor": 2026}, "ano 2026"),
    ({"valor": 10}, "aumentou 10%"),
    ({"valor": 15}, "às 15:30"),
    ({"valor": 123456789}, "CPF 123.456.789-09"),
    ({"valor": 17}, "vence 17/10/2026"),
])
def test_numero_que_nao_e_valor_nao_ancora(uid_pro, args, fala):
    assert _ancora(uid_pro, "add_launch", args, fala)[1] == "recusa"


def test_valor_legitimo_ao_lado_de_data_e_hora_ancora(uid_pro):
    assert _ancora(uid_pro, "add_launch", {"valor": 1234.56}, "dia 17/10 às 15:30 paguei R$ 1.234,56")[1] is None


def test_log_da_recusa_e_do_descarte(uid_pro, monkeypatch):
    achados = []
    monkeypatch.setattr("core.services.ai_chat.valor_do_usuario.log_system_event_sync",
                        lambda nivel, evento, msg, **kw: achados.append((evento, kw["details"])))
    _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")
    _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 150}, "paguei a luz")
    _ancora(uid_pro, "add_credit_purchase", {"valor": 50, "parcelas": 60}, "comprei 50 no nubank")
    _ancora(uid_pro, "add_launch", {"valor": 50}, "gastei 50")  # ancorado: sem log
    tok = CURRENT_PLATFORM.set("whatsapp")
    try:
        _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")
    finally:
        CURRENT_PLATFORM.reset(tok)
    base = {"tool": "add_launch", "acao": "recusa", "motivo": "valor", "plataforma": "dashboard",
            "texto_tem_numero": True}
    assert achados == [
        ("ai_valor_sem_ancora", base),
        ("ai_valor_sem_ancora", {"tool": "mark_bill_paid", "acao": "descarta", "motivo": "valor",
                                 "plataforma": "dashboard", "texto_tem_numero": False}),
        ("ai_valor_sem_ancora", {**base, "tool": "add_credit_purchase", "motivo": "parcelas"}),
        ("ai_valor_sem_ancora", {**base, "plataforma": "whatsapp"}),
    ]


# Portão contra o próximo caso: escritor sem confirmação com argumento de dinheiro (type "number" no
# schema; contagens como parcelas/meses/ids são "integer") tem que declarar `valor_do_usuario`.
SEM_ANCORA_DE_PROPOSITO = {
    "create_meta": "ancorada por pede_criar_meta_ja/_confere (metas_conversa), não por esta guarda",
}


def test_todo_escritor_sem_confirmacao_com_dinheiro_declara_a_ancora():
    escritores = {}
    for nome, t in _BY_NAME.items():
        props = t.schema["function"]["parameters"].get("properties", {})
        dinheiro = [k for k, v in props.items() if v.get("type") == "number"]
        if t.is_write and not t.requires_confirmation and dinheiro:
            escritores[nome] = (t.valor_do_usuario, dinheiro)
    assert set(escritores) >= {"add_launch", "add_credit_purchase", "add_boleto", "mark_bill_paid", "set_budget"}
    sem = {n for n, (campo, _) in escritores.items() if campo is None}
    assert sem == set(SEM_ANCORA_DE_PROPOSITO), sem ^ set(SEM_ANCORA_DE_PROPOSITO)
    assert all(campo in dinheiro for campo, dinheiro in escritores.values() if campo)


def _credito(uid) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) n from credit_transactions where user_id=%s", (uid,))
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def _compra_cartao(valor, parcelas=None):
    args = {"valor": valor, "card_name": "Nubank", "descricao": "geladeira"}
    if parcelas:
        args["parcelas"] = parcelas
    return com_tools(chamada("add_credit_purchase", args))


def test_n4_cartao_total_derivado_sem_parcelas(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(600))   # 12 × 50 sem dizer as parcelas
    r = runner.chat(uid_pro, "geladeira 12x de 50 no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_n6_parcelas_forjadas_nao_gravam(uid_pro, monkeypatch):  # A1
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(3000, parcelas=60))
    r = runner.chat(uid_pro, "comprei 50 no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r


@pytest.mark.parametrize("lixo", [float("inf"), float("-inf"), 1e999])
def test_parcelas_lixo_grava_como_antes(uid_pro, monkeypatch, lixo):  # B1 na conversa
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, parcelas=lixo))
    runner.chat(uid_pro, "gastei 50 no mercado", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


@pytest.mark.parametrize("digitos", [33, 400])
def test_numero_gigante_na_mensagem_nao_derruba_nem_trava(uid_pro, monkeypatch, digitos):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50), lancamento(50, alvo="padaria"))
    runner.chat(uid_pro, "gastei 50 no mercado " + "9" * digitos, platform="whatsapp")   # não levanta
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]   # o 50 escrito ancora
    runner.chat(uid_pro, "gastei 50 na padaria", platform="whatsapp")                    # não ficou travada
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}] * 2


def test_mil_derrubado_pelo_modelo_nao_grava_mil_vezes_menos(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(2), lancamento(2000))
    runner.chat(uid_pro, "gastei 2 mil", platform="whatsapp")   # modelo manda 2: recusado
    assert lancamentos(uid_pro) == []
    runner.chat(uid_pro, "gastei 2 mil", platform="whatsapp")   # modelo manda 2000: grava
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 2000.0}]


def _duas(primeiro, segundo):
    return com_tools(chamada("add_launch", {"tipo": "despesa", "valor": primeiro, "alvo": "uber"}, "c1"),
                     chamada("add_launch", {"tipo": "despesa", "valor": segundo, "alvo": "uber"}, "c2"))


@pytest.mark.parametrize("ordem", [(77, 30), (30, 77)])
def test_n7_duas_chamadas_uma_sem_ancora(uid_pro, monkeypatch, ordem):  # B2
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _duas(*ordem))
    r = runner.chat(uid_pro, "gastei 30 no uber", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}]
    assert "Não anotei nada" not in r and "Não anotei um dos itens" in r and "30" in r, r


def test_n7_todas_sem_ancora_segue_terminal(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _duas(77, 88))
    r = runner.chat(uid_pro, "gastei 30 no uber", platform="whatsapp")
    assert RECUSA in r and "um dos itens" not in r and lancamentos(uid_pro) == [], r


def _conta_variavel(uid):
    from datetime import date
    import db.bills as B
    import db.recurring as R
    rec = R.create_recurring_expense(uid, "Luz", None, "conta", 10, "account",
                                     payment_mode="manual", variable_amount=True)
    B.ensure_bill_instance(rec["id"], uid, date.today(), 0)
    return B.list_bills(uid, include_paid=False)[0]


def _paga_luz(valor):
    return com_tools(chamada("mark_bill_paid", {"name": "luz", "amount": valor}))


def test_n5_conta_variavel_amount_inventado_vira_pergunta(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_variavel(uid_pro)
    cli = openai_falso(monkeypatch, _paga_luz(150))
    r = runner.chat(uid_pro, "paguei a luz", platform="whatsapp")
    _usa_ia(cli)
    assert "Quanto veio" in r, r
    assert lancamentos(uid_pro) == []
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "pending"
    assert db.get_pending_action(uid_pro)["action_type"] == "bill_amount_expected"


# ── (3) P: o caminho legítimo continua gravando ─────────────────────────────

def test_p1_gastei_50_no_mercado(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    diga(uid_pro, "piggy gastei 50 no mercado")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


def test_p2_app_com_banco_pergunta_a_forma_e_dinheiro_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    openai_falso(monkeypatch, lancamento(500), lancamento(500, forma_pagamento="dinheiro"))
    r = runner.chat(uid_pro, "gastei 500", platform="dashboard")
    assert "dinheiro vivo" in r and lancamentos(uid_pro) == [], r   # _PERGUNTE_A_FORMA: nada gravado
    runner.chat(uid_pro, "dinheiro", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


@pytest.mark.parametrize("frase,valor", [("1,2 mil de aluguel", 1200), ("gastei cinquenta no mercado", 50)])
def test_p3_multiplicador_e_extenso(uid_pro, monkeypatch, frase, valor):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(valor))
    runner.chat(uid_pro, frase, platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": float(valor)}]


def test_p6_n_centavos_grava_meio_real_e_nao_50_reais(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(0.5), lancamento(50), lancamento(0.5))
    runner.chat(uid_pro, "gastei 50 centavos", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}]
    r = runner.chat(uid_pro, "gastei 50 centavos", platform="dashboard")   # R$ 50 inventado: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}], r


def test_p12_ponto_decimal_grava_49_90_e_nao_4990(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(4990), lancamento(49.9))
    r = runner.chat(uid_pro, "gastei 49.90", platform="dashboard")   # R$ 4.990 inventado: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "gastei 49.90", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 49.9}]


def test_p13_orcamento_de_dois_milhoes_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(chamada("set_budget", {"categoria": "Lazer", "budget": 2000000})))
    runner.chat(uid_pro, "meu orçamento de lazer é dois milhões", platform="dashboard")
    assert db.get_budget(uid_pro, "Lazer")["budget"] == 2000000


def test_p8_cinquenta_centavos_por_extenso_grava_meio_real_e_nao_50(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(0.5), lancamento(50))
    runner.chat(uid_pro, "gastei cinquenta centavos", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}]
    r = runner.chat(uid_pro, "gastei cinquenta centavos", platform="dashboard")   # R$ 50 (100x): recusa
    assert RECUSA in r and lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}], r


def test_p9_artigo_um_nao_ancora_valor_1(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(1))   # "uma tv": o 1 é inventado
    r = runner.chat(uid_pro, "comprei uma tv no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_p10_duas_tvs_nao_ancora_valor_2(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(2))   # "duas tvs": o 2 é contagem, inventado como valor
    r = runner.chat(uid_pro, "comprei duas tvs no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_p11_reais_e_centavos_por_extenso_gravam_o_total(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(30.5))
    runner.chat(uid_pro, "paguei trinta reais e cinquenta centavos", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.5}]


def test_p7_dois_extensos_na_mesma_frase_gravam_os_dois_e_a_soma_nao(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "c1"),
                                        chamada("add_launch", {"tipo": "despesa", "valor": 20, "alvo": "uber"}, "c2")))
    runner.chat(uid_pro, "gastei cinquenta no mercado e vinte no uber", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}, {"tipo": "despesa", "valor": 20.0}]
    assert _ancora(uid_pro, "add_launch", {"valor": 70}, "gastei cinquenta no mercado e vinte no uber")[1] == "recusa"


def test_p4_cartao_12x_de_50_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, _compra_cartao(600, parcelas=12))
    runner.chat(uid_pro, "geladeira 12x de 50 no nubank", platform="whatsapp")
    assert _credito(uid_pro) == 12


# ── contagem de parcelas: o valor está no texto, a contagem tem que estar também ──

def _cartao_diz(uid, monkeypatch, fala, valor, parcelas=None):
    desliga_flag(monkeypatch)
    db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(valor, parcelas=parcelas))
    r = runner.chat(uid, fala, platform="whatsapp")
    _usa_ia(cli)
    return r


def test_q1_valor_no_texto_mas_parcelas_inventadas_recusa(uid_pro, monkeypatch):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50, parcelas=60)
    assert RECUSA_PARCELAS in r and RECUSA not in r and _credito(uid_pro) == 0, r
    _historico_sem(uid_pro, 60)  # o `tool` do histórico não repete a contagem inventada


def test_q1b_recusa_de_parcelas_com_outra_chamada_que_gravou_anexa_o_parcial(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "c1"),
        chamada("add_credit_purchase", {"valor": 50, "parcelas": 60, "card_name": "Nubank", "descricao": "tv"}, "c2")))
    r = runner.chat(uid_pro, "gastei 30 no uber e comprei 50 no nubank", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}] and _credito(uid_pro) == 0
    assert "Não anotei um dos itens: em quantas parcelas foi? Ex.: 12x." in r, r
    assert "não achei o valor" not in r and RECUSA_PARCELAS not in r, r


def test_q1c_valor_inventado_com_outra_chamada_que_gravou_anexa_o_parcial_de_valor(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "c1"),
        chamada("add_credit_purchase", {"valor": 77, "card_name": "Nubank", "descricao": "tv"}, "c2")))
    r = runner.chat(uid_pro, "gastei 30 no uber e comprei uma tv no nubank", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}] and _credito(uid_pro) == 0
    assert "Não anotei um dos itens: não achei o valor dele" in r and "parcelas" not in r, r


def test_q2_sem_parcelas_grava_uma(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50)
    assert _credito(uid_pro) == 1


def test_q3_12x_de_50_grava_12(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "geladeira 12x de 50 no nubank", 600, parcelas=12)
    assert _credito(uid_pro) == 12


def test_q4_valor_da_parcela_e_contagem_escritos_grava_3(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "comprei em 3x de 100 no nubank", 100, parcelas=3)
    assert _credito(uid_pro) == 3


def test_q5_contagem_diferente_da_escrita_recusa(uid_pro, monkeypatch):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 em 12x no nubank", 50, parcelas=6)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r


@pytest.mark.parametrize("lixo", [float("inf"), float("nan"), "abc"])
def test_q6_parcelas_lixo_no_cartao_nao_estoura(uid_pro, monkeypatch, lixo):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50, parcelas=lixo)
    assert RECUSA not in r  # lido como 1 pela guarda; quem recusa o lixo é o execute


@pytest.mark.parametrize("valor,parcelas,fala", [
    (600, 12, "parcelei 600 em 12"),
    (1200, 10, "tv 1200 parcelado em 10"),
    (600, 12, "comprei tv em doze vezes de 50"),
    (50, 12, "comprei tv em doze vezes de 50"),
])
def test_r1_contagem_escrita_sem_unidade_ou_por_extenso_ancora(uid_pro, valor, parcelas, fala):  # B1 do Manager
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": valor, "parcelas": parcelas}, fala)[1] is None


def test_r2_contagem_nao_escrita_continua_recusada(uid_pro):
    a = lambda args, fala: _ancora(uid_pro, "add_credit_purchase", args, fala)[1]
    assert a({"valor": 3000, "parcelas": 60}, "comprei 50 no nubank") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "parcelei 600 em 6") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "comprei tv em dez vezes de 60") == "recusa_parcelas"


def test_q7_regra_das_parcelas_no_unitario(uid_pro):
    a = lambda args, fala: _ancora(uid_pro, "add_credit_purchase", args, fala)[1]
    assert a({"valor": 50, "parcelas": 60}, "comprei 50") == "recusa_parcelas"
    assert a({"valor": 50, "parcelas": 1}, "comprei 50 à vista") is None
    assert a({"valor": 50, "parcelas": 12.0}, "comprei 50 em 12x") is None   # float é 12
    assert a({"valor": 50, "parcelas": 6}, "comprei 50 em 12x") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "12 parcelas de 50") is None
    # tool sem `parcelas` no schema ignora o campo, então não recusa por ele
    assert _ancora(uid_pro, "add_launch", {"valor": 50, "parcelas": 6}, "gastei 50")[1] is None


def test_p5_conta_variavel_com_valor_dito_paga(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_variavel(uid_pro)
    openai_falso(monkeypatch, _paga_luz(132))
    runner.chat(uid_pro, "paguei a luz, deu 132", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 132.0}]
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "paid"
