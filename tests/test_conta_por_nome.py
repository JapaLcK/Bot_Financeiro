"""Qual conta o "paguei <nome>" quita: `core/handlers/conta_por_nome.py` (#568).

Empate de nomes, nome dentro de outro, mesmo nome com grafia diferente e
"paguei" sem nomear a conta quando a mensagem traz pergunta comparativa. O
número do nome da conta não vira valor (#700). O mesmo nome, inclusive no
"paguei" solto (R5-3), paga o de vencimento mais antigo; no mesmo vencimento,
o criado primeiro.

Limite conhecido: com palavra no meio do nome ("paguei o ipva de 2025" para
"IPVA 2025") o nome não é tirado do texto e o 2025 ainda vira valor; e um número
que não é do nome ("paguei a luz de 2025") continua sendo valor.
Conversa real pelo `handle_incoming`, banco real. Os ajudantes e a fixture vêm
de `test_pergunta_comparativa_conta_cartao.py`, que cobre o resto da issue.
"""
from __future__ import annotations

import pytest

import db
import db.bills as B
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_pergunta_comparativa_conta_cartao import (  # noqa: F401 (fixture uid)
    _ANO, _AVISO, contas_pagas, despesas, luz, uid)
from utils_date import today_tz


def _contas(uid, *nomes_valores):
    for nome, valor in nomes_valores:
        B.create_boleto(uid, nome, valor, today_tz(), category="moradia")


def pagas(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select name from bill_instances where user_id=%s and status='paid'", (uid,))
        return sorted(r["name"] for r in cur.fetchall())


_LUZ_CASA_ESCRITORIO = [("Luz casa", 150.0), ("Luz escritorio", 90.0)]


@pytest.mark.parametrize("ordem", [1, -1])  # as duas ordens de criação
@pytest.mark.parametrize("frase,avisos", [("paguei a luz", 0), (f"paguei a luz e {_ANO}", 1)])
def test_empate_de_nomes_pergunta_qual(uid, ia_fora, frase, avisos, ordem):
    _contas(uid, *_LUZ_CASA_ESCRITORIO[::ordem])
    r = manda(uid, frase)
    assert "Qual delas você pagou?" in r and pagas(uid) == [] and despesas(uid) == [], r
    assert "Me manda *paguei Luz casa*, com o nome inteiro." in r, r  # R2-3: como responder
    assert r.count(_AVISO) == avisos, r


def test_empate_a_resposta_sugerida_quita(uid, ia_fora):  # R2-3
    _contas(uid, *_LUZ_CASA_ESCRITORIO)
    manda(uid, "paguei a luz")
    r = manda(uid, "paguei Luz casa")
    assert pagas(uid) == ["Luz casa"], r


@pytest.mark.parametrize("ordem", [1, -1])  # o resultado não depende da ordem de criação
@pytest.mark.parametrize("contas,frase,paga", [
    ([("Luz casa", 150.0), ("Luz escritorio", 90.0)], "paguei a luz casa", "Luz casa"),
    ([("Luz", 150.0), ("Luz escritorio", 90.0)], "paguei a luz", "Luz"),       # nome inteiro
    ([("Luz", 150.0), ("Luz casa", 80.0)], "paguei a luz casa", "Luz casa"),   # "Luz" dentro de "Luz casa"
    ([("Luz", 150.0), ("Luz casa", 80.0)], "paguei a luz de casa", "Luz casa"),
    ([("Internet", 100.0), ("Internet celular", 50.0)], "paguei a internet do celular",
     "Internet celular"),
    ([("Internet", 100.0), ("Internet celular", 50.0)], "paguei a internet", "Internet"),
    ([("Net", 70.0), ("Internet", 100.0)], "paguei a internet", "Internet"),  # palavra inteira
    ([("Agua", 60.0), ("Agua e esgoto", 90.0)], "paguei a agua e esgoto", "Agua e esgoto"),
    ([("Agua", 60.0), ("Agua e esgoto", 90.0)], "paguei a agua", "Agua"),
    ([("Gas", 70.0), ("Gasolina", 100.0)], "paguei a gasolina", "Gasolina"),
    ([("Gas", 70.0), ("Gasolina", 100.0)], "paguei o gas", "Gas"),
])
def test_nome_inteiro_mais_especifico_vence(uid, ia_fora, contas, frase, paga, ordem):
    _contas(uid, *contas[::ordem])
    r = manda(uid, frase)
    assert "Qual delas" not in r and pagas(uid) == [paga], r


@pytest.mark.parametrize("ordem", [1, -1])
@pytest.mark.parametrize("contas,frase", [
    # R3-2: nomes inteiros que NÃO se contêm empatam. A r3 pagava o mais longo:
    ([("Luz", 150.0), ("Casa", 1200.0)], "paguei a luz de casa"),             # a Casa, R$ 1.200
    ([("Luz", 150.0), ("Agua", 90.0)], "paguei a luz e a agua"),              # a Agua (D2: #694)
    ([("Luz", 150.0), ("Casa de praia", 900.0)], "paguei a luz da casa de praia"),
])
def test_nomes_que_nao_se_contem_perguntam(uid, ia_fora, contas, frase, ordem):
    _contas(uid, *contas[::ordem])
    r = manda(uid, frase)
    assert "Qual delas você pagou?" in r and pagas(uid) == [] and despesas(uid) == [], r


def pagas_com_valor(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select name, paid_amount from bill_instances where user_id=%s and status='paid'",
                    (uid,))
        return sorted((r["name"], float(r["paid_amount"])) for r in cur.fetchall())


@pytest.mark.parametrize("contas,frase,dica,pago", [
    # Com dígito no nome, obedecer a dica paga o valor da conta, não o número (#700).
    ([("IPVA 2026", 1200.0), ("IPVA 2025", 800.0)], "paguei o ipva", "paguei IPVA 2025",
     ("IPVA 2025", 800.0)),
    ([("Vivo Fibra 500MB", 120.0), ("Vivo movel", 60.0)], "paguei a vivo",
     "paguei Vivo Fibra 500MB", ("Vivo Fibra 500MB", 120.0)),
    ([("Condomínio Bloco 2", 800.0), ("Condomínio Bloco 3", 700.0)], "paguei o condominio",
     "paguei Condomínio Bloco 2", ("Condomínio Bloco 2", 800.0)),
])
def test_empate_com_digito_a_dica_paga_o_valor_da_conta(uid, ia_fora, contas, frase, dica, pago):
    _contas(uid, *contas)
    r = manda(uid, frase)
    assert "Qual delas você pagou?" in r and f"Me manda *{dica}*" in r and pagas(uid) == [], r
    r = manda(uid, dica)
    assert pagas_com_valor(uid) == [pago], r


@pytest.mark.parametrize("contas,frase,pago", [
    ([("IPVA 2025", 800.0), ("IPVA 2026", 1200.0)], "paguei IPVA 2025", ("IPVA 2025", 800.0)),
    ([("IPVA 2025", 800.0), ("IPVA 2026", 1200.0)], "paguei o ipva 2026", ("IPVA 2026", 1200.0)),
    ([("Condomínio 2025", 800.0)], "paguei o condominio 2025", ("Condomínio 2025", 800.0)),
    # positivos: o número fora do nome continua sendo o valor
    ([("IPVA 2025", 800.0), ("IPVA 2026", 1200.0)], "paguei o ipva 2026 1300", ("IPVA 2026", 1300.0)),
    ([("Luz", 150.0)], "paguei a luz 120", ("Luz", 120.0)),
    ([("Luz", 150.0)], "paguei 150", ("Luz", 150.0)),
])
def test_numero_do_nome_nao_vira_valor(uid, ia_fora, contas, frase, pago):  # #700
    _contas(uid, *contas)
    r = manda(uid, frase)
    assert pagas_com_valor(uid) == [pago], r


@pytest.mark.parametrize("conta,frase", [("Net", "paguei a internet"), ("Gas", "paguei a gasolina")])
def test_nome_casa_por_palavra_inteira(uid, ia_fora, conta, frase):
    # A main pagava a Net com "paguei a internet": "net" casava como trecho.
    _contas(uid, (conta, 70.0))
    r = manda(uid, frase)
    assert pagas(uid) == [], r


def test_mesma_conta_em_dois_meses_paga_a_mais_antiga(uid, ia_fora):  # positivo
    from datetime import timedelta
    B.create_boleto(uid, "Luz", 140.0, today_tz() - timedelta(days=30), category="moradia")
    luz(uid)
    r = manda(uid, "paguei a luz")
    assert contas_pagas(uid) == [140.0], r


@pytest.mark.parametrize("frase,pago,avisos", [
    (f"paguei e {_ANO}", [], 1),               # sobrou só o verbo: não quita
    (f"paguei hoje e {_ANO}", [], 1),          # R2-1: sem nomear a conta, não quita
    (f"paguei ja e {_ANO}", [], 1),
    (f"paguei essa conta e {_ANO}", [], 1),
    (f"paguei o boleto e {_ANO}", [], 1),
    ("paguei", [150.0], 0),                    # positivo: o "paguei" do lembrete continua quitando
    ("paguei essa conta", [150.0], 0),         # positivo: idem, sem pergunta
    (f"paguei a luz e {_ANO}", [150.0], 1),    # positivo: com pergunta, nomeando a conta
])
def test_paguei_sem_nomear_a_conta(uid, ia_fora, frase, pago, avisos):
    luz(uid)
    r = manda(uid, frase)
    assert contas_pagas(uid) == pago and r.count(_AVISO) == avisos, r


# ── B1: mesmo nome com grafia diferente é a mesma conta ──────────────────────

@pytest.mark.parametrize("dias", [0, 30])  # vencimentos iguais e com 30 dias de diferença
@pytest.mark.parametrize("antiga,nova,frase", [
    ("Luz", "luz", "paguei a luz"),
    ("Água", "Agua", "paguei a agua"),
    ("Agua", "Água", "paguei a água"),
])
def test_mesmo_nome_normalizado_paga_a_mais_antiga(uid, ia_fora, antiga, nova, frase, dias):
    from datetime import timedelta
    B.create_boleto(uid, antiga, 140.0, today_tz() - timedelta(days=dias), category="moradia")
    B.create_boleto(uid, nova, 150.0, today_tz(), category="moradia")
    r = manda(uid, frase)
    assert "Qual delas" not in r and len(pagas(uid)) == 1, r
    assert dias == 0 or contas_pagas(uid) == [140.0], r


@pytest.mark.parametrize("ordem", [1, -1])
def test_paguei_solto_duas_luz_paga_a_de_vencimento_mais_antigo(uid, ia_fora, ordem):  # R5-3
    from datetime import timedelta
    contas = [("Luz", 150.0, 5), ("Luz", 160.0, -3)][::ordem]
    for nome, valor, dias in contas:
        B.create_boleto(uid, nome, valor, today_tz() + timedelta(days=dias), category="moradia")
    r = manda(uid, "paguei")
    assert pagas_com_valor(uid) == [("Luz", 160.0)], r


@pytest.mark.parametrize("valores", [(150.0, 160.0), (160.0, 150.0)])
def test_mesmo_nome_e_vencimento_paga_a_criada_primeiro(uid, ia_fora, valores):  # R5-4
    _contas(uid, ("Luz", valores[0]), ("Luz", valores[1]))
    r = manda(uid, "paguei a luz")
    assert pagas_com_valor(uid) == [("Luz", valores[0])], r


def test_paguei_solto_com_o_mesmo_nome_paga_a_mais_antiga(uid, ia_fora):
    from datetime import timedelta
    B.create_boleto(uid, "Luz", 140.0, today_tz() - timedelta(days=30), category="moradia")
    B.create_boleto(uid, "luz", 150.0, today_tz(), category="moradia")
    r = manda(uid, "paguei")
    assert "Qual delas" not in r and contas_pagas(uid) == [140.0], r


# ── O2: os níveis que decidem qual conta paga ────────────────────────────────

@pytest.mark.parametrize("contas,frase,paga", [
    ([("Energia eletrica", 150.0), ("Agua", 60.0)], "paguei a energia da casa", "Energia eletrica"),
    ([("Luz escritorio", 90.0), ("Agua", 60.0)], "paguei a luz", "Luz escritorio"),
    # o alvo inteiro dentro do nome vence uma palavra solta
    ([("Luz casa nova", 150.0), ("Luz escritorio", 90.0)], "paguei a luz casa", "Luz casa nova"),
])
def test_casamento_parcial_escolhe_a_conta(uid, ia_fora, contas, frase, paga):
    _contas(uid, *contas)
    r = manda(uid, frase)
    assert pagas(uid) == [paga], r


def test_parcial_nao_filtra_nome_dentro_de_outro(uid, ia_fora):
    # "Luz sala" está dentro de "Luz sala nova", mas o usuário não disse nenhum
    # dos dois inteiro: pergunta, não escolhe o mais longo.
    _contas(uid, ("Luz sala", 150.0), ("Luz sala nova", 90.0))
    r = manda(uid, "paguei a luz")
    assert "Qual delas" in r and pagas(uid) == [], r


def test_palavra_curta_do_nome_nao_casa(uid, ia_fora):
    # O "e" de "Agua e esgoto" não casa com o "e" de "paguei a luz e o gas".
    _contas(uid, ("Agua e esgoto", 90.0))
    r = manda(uid, "paguei a luz e o gas")
    assert pagas(uid) == [], r


def test_paguei_com_valor_quita_a_unica_pendente(uid, ia_fora):
    # O número não é alvo: "paguei 150" responde ao lembrete da única conta.
    luz(uid)
    r = manda(uid, "paguei 150")
    assert contas_pagas(uid) == [150.0], r
