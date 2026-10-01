"""#567: o número que faz parte do NOME não vira o valor da retirada.

"saquei 50" -> "De qual caixinha ou investimento?" -> "tesouro 2029" resgatava R$ 2.029
no lugar dos R$ 50 guardados. DECIDIDO PELO DONO: na pergunta de NOME o número da
resposta só troca o valor guardado em forma explícita:
  - número + do/da/no/na/em/pra + nome do CATÁLOGO ("tira 80 do tesouro"), lido SEM o
    nome ("tesouro 2029 no nubank", N6; "a reserva 2025 do nubank", N13: não);
  - quantia depois de ", ": "80", "R$ 80", "80 reais", "na verdade 80"; decimal com
    espaço ("132, 50") só se a parte antes não é ano (N14); SEM "R$" sem vírgula;
  - com "esvaziar" guardado, sobrar dígito ou palavra de número depois de tirar o nome
    e sem quantia reconhecida ("viagem, 2 mil", "viagem 80", "80 viagem") NÃO esvazia:
    pergunta o valor (N15/N16/N17, d046-1/e1c-1). Não ensina forma nova de quantia.
  Limites aceitos: "tesouro, 2025, 50" -> 50, "Tesouro 2029 80" -> 50, "tesouro,80" ->
  50, "tesouro, 2 mil" -> 50 (valor numérico guardado, também no aporte), "tesouro
  2029 no valor de 80" -> 50, "meia"/"metade"/"sem nada" esvaziam (como na `main`),
  "tira do tesouro 2029 no nubank" -> 2.029 (#703), nome CURTO depois da preposição
  reativa a regra ("a reserva 2025 da casa" com `casa` -> 2.025).
EXTENSÃO DO CODER: resposta SÓ com dinheiro ("80") é quantia, como na `main` (N7);
unidade inteira do `h_bills._UNIDADE`; corte em ", " antes do `limpa_pontuacao_final`.
Os ramos de "tudo" (#259) não mudam. FORA (#704): "caixinha 13º".

CONTROLE NEGATIVO (2026-09-30 sobre 2344c895; cada mutação, num caso verde com o
fix). (a) `pede_nome=False`: 50 VERMELHOS. (b) `_quantia_explicita` devolvendo
None: 40 daqui e 8 de `test_perguntas_guardam_contexto.py` (entre eles `test_tudo_
guardado_mais_quantia_nova_nao_esvazia`). Regras anteriores (catálogo depois da
preposição, número sem o nome, `crua`, limpeza da cauda, alvo inteiro antes da
", ", `_ANO_RE`): linhas de N4/N6/N7/N13/N14/N15 e da tabela. `want_all`
(d046-1/e1c-1): sem o ramo, 32 (N15, tabela, N16, N17); só o teste de dígito ou só
`_extract_valor`, 2 e 1; sem `want_all`, 40; detectar na resposta e não no
texto sem o nome, 2; sem re-armar `falta=amount`, N16 e N17.
Prefixo/unidade/sinal da cauda malformada (Codex #709, 4150138423): `2344c895`
deixa 7 vermelhos (5 N14, 2 N12); "na verdade" só na válida, 6; sem o sinal
antes do "R$", "-R$ 80"; sem a unidade na malformada, "132 50 reais"; sem a
cauda malformada ir ao `valor_perigoso`, 9.

CONTROLE POSITIVO: P1 (pergunta de VALOR), P2 (nome exato com dígitos), P3 (correção
explícita). Conversa pelo `handle_incoming`, banco real, SALDO. CLASSE CEGA: sem LLM.
"""
from __future__ import annotations

import pytest

import db
from core.intent_router import _funde_a_resposta
from utils_text import limpa_pontuacao_final
from tests.test_perguntas_guardam_contexto import (  # noqa: F401  (fixtures)
    _caixinhas_com_saldo, _conversa, _pergunta_injetada, sem_teto_de_caixinha, uid,
)


def _investimento(uid: int, nome: str, valor: float = 3000.0) -> None:
    db.add_launch_and_update_balance(uid, "receita", valor, None, "seed")
    db.create_investment_db(uid, nome, rate=0.10, period="yearly", tax_profile="exempt_ir_iof")
    db.investment_deposit_from_account(uid, nome, valor, "aporte")


def _inv(uid: int, nome: str) -> float:
    return round(float(next(i["balance"] for i in db.list_investments(uid, include_lots=False)
                            if i["name"] == nome)), 2)


def _caixinha(uid: int, nome: str) -> float:
    return round(float(next(p["balance"] for p in db.list_pockets(uid) if p["name"] == nome)), 2)


def _responde(uid: int, *mensagens: str) -> list[str]:
    """Responde e, se vier o desempate do #281 (1️⃣), escolhe "era a resposta"."""
    respostas = _conversa(uid, *mensagens)
    if "1️⃣" in respostas[-1]:
        respostas += _conversa(uid, "1")
    return respostas


def _saca(uid, caixinha, investimento, resposta, saldo=3000.0, primeira="saquei 50"):
    if caixinha:
        _caixinhas_com_saldo(uid, caixinha, saldo=saldo)
    else:
        _investimento(uid, investimento, saldo)
    r = _responde(uid, primeira, resposta)
    return (_caixinha(uid, caixinha) if caixinha else _inv(uid, investimento)), r


# ── Grupo do conserto: vermelho sem ele ──────────────────────────────────────

@pytest.mark.parametrize("nome,resposta", [
    ("Tesouro", "Tesouro Prefixado 2029"),
    ("Tesouro", "tesouro selic 2029"),
    ("LCI", "LCI 90 dias"),
    ("CDB", "cdb 110% cdi"),
])
def test_n3_numero_ao_lado_do_nome_no_resgate(uid, nome, resposta):
    _investimento(uid, nome)
    _pergunta_injetada(uid, "investments.withdraw", {"amount": 50.0}, "resgata 50")
    r = _responde(uid, resposta)
    assert _inv(uid, nome) == 2950.00, r


@pytest.mark.parametrize("caixinha,investimento,resposta", [
    (None, "Tesouro", "tesouro 2029"),         # o caso da issue
    ("viagem", None, "viagem 2027"),
    (None, "Tesouro 2029", "tesouro 2029 no nubank"),
    ("viagem 2027", None, "viagem 2027 da família"),
    ("Reserva 2025", None, "reserva 2025 de emergência"),
    # N8: o "R$" do PRÓPRIO nome não é a forma "R$ + número"
    ("meta R$ 5000", None, "caixinha meta R$ 5000"),
    ("meta R$ 5000", None, "a meta R$ 5000"),
    ("R$ 5 mil", None, "caixinha R$ 5 mil"),
    # N9: depois de ", " só vale dinheiro; o ano ou o dia que descreve o nome, não
    ("viagem", None, "viagem, a de 2027"),
    (None, "Tesouro", "tesouro, o de 2029"),
    (None, "Tesouro", "tesouro, vence em 2035"),
    ("viagem", None, "viagem, dia 15"),
    # N10: "+ nome" é literal (o "no nubank" não cita o catálogo); sem "R$" sem vírgula
    (None, "Tesouro", "tesouro 2029 no nubank"),
    (None, "CDB", "cdb 2027 do inter"),
    ("viagem", None, "viagem 2027 da família"),
    ("Reserva", None, "reserva 2025 de emergência"),
    (None, "Tesouro", "Tesouro 2029 R$ 80"),
    ("Meta", None, "meta R$ 5000"),
])
def test_n6_numero_ou_ano_do_nome_nao_vira_valor(uid, caixinha, investimento, resposta):
    saldo, r = _saca(uid, caixinha, investimento, resposta)
    assert saldo == 2950.00, r


def test_n7_so_numero_a_pergunta_de_nome_nao_vira_nome(uid):
    _caixinhas_com_saldo(uid, "viagem", saldo=3000.0)
    r = _conversa(uid, "tirar da caixinha viagem", "80")
    assert "*80* não encontrada" in r[-1], r
    assert _caixinha(uid, "viagem") == 3000.00


@pytest.mark.parametrize("resposta,fim", [
    ("viagem 2027", 3050.0),                                   # o ano é do nome
    ("viagem, na verdade 80", 3080.0),                         # positivo
    ("viagem, na verdade 132 50", 3000.0), ("viagem, na verdade -80", 3000.0),
])
def test_n12_deposito_nome_com_ano_e_correcao_malformada(uid, resposta, fim):
    _caixinhas_com_saldo(uid, "viagem", saldo=3000.0)
    _pergunta_injetada(uid, "pockets.deposit", {"amount": 50.0}, "guardar 50")
    r = _responde(uid, resposta)
    assert _caixinha(uid, "viagem") == fim, r


@pytest.mark.parametrize("caixinhas,investimento,alvo,resposta", [
    (("Nubank", "Reserva 2025 do Nubank"), None, "Reserva 2025 do Nubank", "a reserva 2025 do nubank"),
    (("Nubank", "Reserva 2025 do Nubank"), None, "Reserva 2025 do Nubank", "caixinha reserva 2025 do nubank"),
    (("Inter",), "CDB 2027 do Inter", "CDB 2027 do Inter", "o cdb 2027 do inter"),
    (("Praia", "Casa 2025 da Praia"), None, "Casa 2025 da Praia", "a casa 2025 da praia"),
])
def test_n13_nome_curto_dentro_do_longo(uid, sem_teto_de_caixinha, caixinhas, investimento, alvo, resposta):
    _caixinhas_com_saldo(uid, *caixinhas, saldo=3000.0)
    if investimento:
        _investimento(uid, investimento)
    r = _responde(uid, "saquei 50", resposta)
    assert (_inv(uid, alvo) if investimento else _caixinha(uid, alvo)) == 2950.00, r


@pytest.mark.parametrize("caixinha,investimento,resposta,fim", [
    ("Viagem, 2027", None, "a viagem, 2027", 2950.0),     # o ", 2027" é do nome
    ("Viagem, 2027", None, "a viagem, 2027, 80", 2920.0),  # positivo: vírgula depois do nome
    (None, "Tesouro", "tesouro, 132, 50", 2867.5),         # decimal com espaço
    (None, "Tesouro", "tesouro, R$ 132, 50", 2867.5),
    (None, "Tesouro", "tesouro, 2029, 80", 2920.0),        # ano não é decimal (dono)
    (None, "Tesouro", "tesouro, 2025, 50", 2950.0),        # limite aceito pelo dono
    (None, "Tesouro", "tesouro, na verdade 132, 50", 2867.5),   # positivo do prefixo
    # Codex #709: "na verdade"/sinal antes do "R$" também vão ao `valor_perigoso`
    *[(None, "Tesouro", r, 3000.0) for r in (
        "tesouro, na verdade 132 50", "tesouro, na  verdade 132 50", "tesouro, na verdade -80",
        "tesouro, na verdade R$ -80", "tesouro, -R$ 80", "tesouro, na verdade 132 50 reais")],
    # N4: quantia reconhecida depois da vírgula troca o valor
    *[(None, "Tesouro", r, 2920.0) for r in (
        "tesouro 2029, 80", "tesouro 2029, R$ 80", "tesouro 2029, r$ 80",
        "tesouro, na verdade 80", "tesouro, 80 real")],
])
def test_n14_virgula_do_nome_e_decimal_com_espaco(uid, caixinha, investimento, resposta, fim):
    saldo, r = _saca(uid, caixinha, investimento, resposta)
    assert saldo == fim, r


@pytest.mark.parametrize("primeira,resposta,fim", [
    ("saquei 50", "viagem, -80", 3000.0), ("saquei 50", "viagem, 132 50", 3000.0),
    ("esvaziar caixinha", "viagem, -80", 3000.0), ("esvaziar caixinha", "viagem, R$ -80", 3000.0),
    # "-80"/"132 50" vão ao `valor_perigoso` (#709); d046-1/e1c-1: o resto pergunta
    *[("esvaziar caixinha", c, 3000.0) for c in (
        "viagem, 2 mil", "viagem, cem", "viagem, uns 80", "viagem, 1k", "viagem, dia 15",
        "viagem, a de 2027", "viagem 80", "viagem 80 reais", "viagem R$ 80", "80 viagem",
        "minha viagem 80", "viagem: 80", "viagem 2 mil", "viagem cem", "viagem e 2 mil",
        "viagem,80", "viagem (80)", "viagem 132 50", "viagem 2027")],
    ("esvaziar caixinha", "viagem, 80", 2920.0),   # positivo: quantia reconhecida
    ("esvaziar caixinha", "tira 100 da viagem", 2900.0),
    ("esvaziar caixinha", "tira 2 mil da viagem", 1000.0),
    ("esvaziar caixinha", "viagem", 0.0),          # positivo: só o nome esvazia
    ("esvaziar caixinha", "viagem, a de férias", 0.0),   # sem dígito: descrição
    ("saquei 50", "viagem 2 mil", 2950.0),         # limite aceito: valor numérico guardado
    ("saquei 50", "viagem, 2 mil", 2950.0),
])
def test_n15_sobra_numero_que_nao_e_quantia_nao_esvazia(uid, primeira, resposta, fim):
    assert _saca(uid, "viagem", None, resposta, primeira=primeira)[0] == fim


@pytest.mark.parametrize("caixinha,resposta", [("Viagem 2027", "viagem 2027"), ("Viagem, 2027", "a viagem, 2027")])
def test_n17_o_nome_inteiro_sai_e_nao_sobra_numero_esvazia(uid, caixinha, resposta):
    assert _saca(uid, caixinha, None, resposta, primeira="esvaziar caixinha")[0] == 0.00


def test_n17_investimento_esvaziar_com_ano_no_nome_pergunta(uid):
    _investimento(uid, "Tesouro")
    _pergunta_injetada(uid, "investments.withdraw", {"want_all": True}, "resgatar tudo")
    r = _responde(uid, "tesouro 2029 no nubank")
    assert _inv(uid, "Tesouro") == 3000.00 and "Qual o valor" in r[-1], r


def test_n16_a_pergunta_do_valor_fica_viva(uid):
    """Depois de "Qual o valor?" (d046-1) a resposta "80" fecha o saque."""
    _caixinhas_com_saldo(uid, "viagem", saldo=3000.0)
    r = _responde(uid, "esvaziar caixinha", "viagem, 2 mil")
    assert _caixinha(uid, "viagem") == 3000.00 and "Qual o valor" in r[-1], r
    _responde(uid, "80")
    assert _caixinha(uid, "viagem") == 2920.00


def test_n5_sem_valor_guardado_pergunta_o_valor_e_nao_move(uid):
    _caixinhas_com_saldo(uid, "viagem", saldo=3000.0)
    r = _conversa(uid, "tirar da caixinha", "viagem 2027")
    assert "Qual o valor" in r[-1], r
    assert _caixinha(uid, "viagem") == 3000.00
    pend = db.get_pending_action(uid)
    assert pend and pend["payload"]["falta"] == "amount"


# ── Controles positivos ─────────────────────────────────────────────────────

def test_p1_pergunta_de_valor_continua_lendo_o_numero(uid):
    _caixinhas_com_saldo(uid, "viagem", saldo=3000.0)
    _investimento(uid, "Tesouro")
    _responde(uid, "tirar da caixinha viagem", "viagem", "50")
    assert _caixinha(uid, "viagem") == 2950.00
    _pergunta_injetada(uid, "investments.withdraw", {"investment_name": "Tesouro"}, "resgata")
    _responde(uid, "50")
    assert _inv(uid, "Tesouro") == 2950.00


def test_p2_nome_com_digitos_do_catalogo(uid):
    _caixinhas_com_saldo(uid, "viagem 2027", saldo=3000.0)
    r = _responde(uid, "saquei 50", "viagem 2027")
    assert _caixinha(uid, "viagem 2027") == 2950.00, r


def test_p3_correcao_explicita_troca_o_valor(uid):
    _investimento(uid, "Tesouro")
    r = _responde(uid, "saquei 50", "na verdade 80 no tesouro 2029")
    assert _inv(uid, "Tesouro") == 2920.00, r


# ── Tabela pura (sem banco) ─────────────────────────────────────────────────

_CAT = ["Tesouro", "viagem", "LCI", "CDB"]


def _funde(resposta: str, catalogo: list[str]) -> tuple[dict, str | None]:
    """Como o `_resolve_clarification` chama: limpa + a resposta crua."""
    return _funde_a_resposta(
        "funds.withdraw", {"amount": 50.0, "want_all": False},
        limpa_pontuacao_final(resposta), catalogo, pede_nome=True, crua=resposta)


@pytest.mark.parametrize("resposta,amount", [
    ("tesouro 2029", 50.0),
    ("tesouro que vence em 2026", 50.0),
    ("LCI 90 dias", 50.0),
    ("cdb 110% cdi", 50.0),
    ("viagem 2027", 50.0),
    ("80", 80.0),                             # só dinheiro: não há nome
    ("80 reais", 80.0),
    ("caixinha, viagem 2027", 50.0),          # cauda que cita o catálogo é nome
    # cauda depois de ", ": uma linha por forma
    ("tesouro 2029, 80", 80.0),
    ("tesouro 2029, R$ 80", 80.0),
    ("tesouro 2029, r$ 80", 80.0),
    ("tesouro 2029, 80 reais", 80.0),
    ("tesouro, 80 real", 80.0),
    ("tesouro, na verdade 80", 80.0),
    ("tesouro, na  verdade  80", 80.0),
    ("tesouro, 132,50.", 132.5),              # a cauda da `crua` passa pelo limpa
    ("tesouro, 80!", 80.0),
    ("tesouro 2029, 80 no total", 50.0),      # "no total" não é forma nenhuma
    ("tesouro, o de 2029", 50.0),             # cauda descritiva
    ("tesouro, de 2029", 50.0),               # o `_ENCHIMENTO` de/da/do não entra
    ("viagem, dia 15", 50.0),
    # número + preposição + nome do catálogo
    ("na verdade 80 no tesouro 2029", 80.0),
    ("tira 80 do tesouro", 80.0),
    ("tira 100 da viagem", 100.0),
    ("cem da viagem", 100.0),
    ("tesouro 2029 no nubank", 50.0),         # o "no" não leva ao catálogo
    ("lci 2026 do itau", 50.0),
    ("o tesouro 2029 da xp", 50.0),
    ("tesouro 2029 em dezembro", 50.0),
    # limites aceitos pelo dono
    ("Tesouro 2029 80", 50.0),
    ("tesouro 2029 R$ 80", 50.0),
    ("tesouro,80", 50.0),
    ("tesouro, 2 mil", 50.0),
    ("tesouro 2029 no valor de 80", 50.0),
    ("tira 100 da viajem", 50.0),
])
def test_tabela_pede_nome(resposta, amount):
    ents, recusa = _funde(resposta, _CAT)
    assert (ents.get("amount"), recusa) == (amount, None)


@pytest.mark.parametrize("nome,resposta,amount", [
    ("Tesouro 2029", "tesouro 2029 no nubank", 50.0),
    ("Tesouro 2029", "tesouro 2029 do nubank, 80", 80.0),
    ("viagem 2027", "viagem 2027 da família", 50.0),
    ("meta 2028", "meta 2028 da casa", 50.0),
    ("meta R$ 5000", "a meta R$ 5000", 50.0),  # "R$" do nome
    ("Meta", "meta R$ 5000", 50.0),
    ("R$ 100 mil", "caixinha R$ 100 mil", 50.0),
    ("R$ 5000", "caixinha, R$ 5000", 50.0),
])
def test_tabela_catalogo_com_ano(nome, resposta, amount):
    ents, recusa = _funde(resposta, [nome])
    assert (ents.get("amount"), recusa) == (amount, None)


def test_tabela_tudo_mais_numero_continua_ambiguo():
    """#259: a guarda do "tudo + número" não depende de `pede_nome`."""
    ents, recusa = _funde("tira tudo menos 50 da viagem", _CAT)
    assert recusa == "quantidade_ambigua" and "amount" not in ents


@pytest.mark.parametrize("catalogo,resposta,pergunta", [
    *[("viagem", c, True) for c in (
        "viagem, 2 mil", "viagem, a de 2027", "viagem, dia 1.2.3", "viagem 80", "80 viagem",
        "viagem R$ 80", "viagem (80)", "viagem 2027", "caixinha, viagem 2027", "viagem 132 50")],
    ("viagem", "2 mil", True),                 # sem nome: sobra número, pergunta
    *[("viagem", c, False) for c in (
        "viagem", "viagem, tudo", "viagem, a grande", "viagem, a de férias", "meia viagem")],
    ("Viagem 2027", "viagem 2027", False),     # o nome inteiro sai: não sobra nada
    ("Viagem, 2027", "a viagem, 2027", False),
])
def test_tabela_esvaziar_guardado(catalogo, resposta, pergunta):
    """e1c-1: com `want_all`, sobrar número depois de tirar o nome tira o `want_all`."""
    ents, r = _funde_a_resposta("pockets.withdraw", {"want_all": True},
                                limpa_pontuacao_final(resposta), [catalogo],
                                pede_nome=True, crua=resposta)
    assert (r, bool(ents.get("want_all"))) == (
        ("quantidade_nao_reconhecida", False) if pergunta else (None, True))
