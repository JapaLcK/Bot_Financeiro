"""P7 a: a receita da Previsão vem SÓ do extrato (`receitas_do_extrato` + `ler_creditos`).

A regra pura roda em tabela; o resto passa pelo `ler` com banco real (semeadura em
`_apoio_assinaturas`). Aqui estão as exclusões, o isolamento, a contagem dupla com a
recorrência de entrada da Pluggy e o documento do pagador, que nunca sai."""
import re
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

import db
from _apoio_assinaturas import HOJE, conta, mensais, rp, semeia, tx
from conftest import usuario_pagante
from core.services import previsao_v2
from core.services.ai_chat.tools.bills import _check_cashflow
from core.services.previsao_recorrencias import MINIMO, receitas_do_extrato
from db.recurring_income import create_recurring_income
from tests.test_previsao_recorrencias_of import codigos, do_banco, ler_em, q
from tests.test_previsao_recorrencias_of_convivencia import saldo_90

MESES = [date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5)]
CPF = "12345678901"
DOC = "55566677788"  # o documento do pagador dos créditos de teste: sem ele, nada é detectado


def L(dia, valor="1000", desc="EMPRESA X", *, category=None, doc=DOC, eu=None, nome=None, conta_=1):
    return {"description": desc, "amount": D(valor), "transaction_date": dia, "category": category,
            "account_id": conta_, "pagador_doc": doc, "recebedor_doc": eu, "pagador_nome": nome}


def entradas(s, nome=None):
    return [e for e in do_banco(s, nome) if e.direcao == "entrada"]


# ── Regra pura ───────────────────────────────────────────────────────────────

def test_minimo_e_a_regra_frouxa():
    assert MINIMO == 2


def test_dois_creditos_mensais_detectam_com_valor_do_ultimo_e_dia_da_cadeia():
    (c,) = receitas_do_extrato([L(MESES[1], "1000"), L(MESES[2], "1100", nome="EMPRESA X LTDA")])
    assert (c["direcao"], c["valor"], c["dia"], c["ultima"], c["cartao"], c["grupos"], c["nome"]) == (
        "entrada", D("1100"), 5, MESES[2], False, [{1}], "EMPRESA X LTDA")
    assert c["id"].startswith("extrato#") and len(c["id"]) == len("extrato#") + 16


@pytest.mark.parametrize("linhas", [
    [L(MESES[2])],                                                  # 1 ocorrência
    [L(date(2026, 9, 1)), L(date(2026, 9, 21))],                    # intervalo de 20 dias
    [L(date(2026, 8, 1)), L(date(2026, 9, 10))],                    # 40 dias
    [L(MESES[0], "100"), L(MESES[1], "100"), L(MESES[2], "125")],   # +25% da mediana
    [L(MESES[0]), L(MESES[1]), L(MESES[2]), L(date(2026, 9, 20))],  # 13º/bônus depois do último: limite declarado
], ids=["uma", "20_dias", "40_dias", "mais_25pct", "extra_no_fim"])
def test_nao_detecta(linhas):
    assert receitas_do_extrato(linhas) == []


def test_vinte_por_cento_da_mediana_ainda_vale():
    assert receitas_do_extrato([L(MESES[0], "100"), L(MESES[1], "100"), L(MESES[2], "120")])


def test_formato_medido_chaves_soltas_e_pix_de_familia_irregular_dao_zero():
    soltas = [L(date(2026, 9, 1 + i % 28), str(10 + i), desc=f"LOJA {chr(65 + i)} ZETA", doc=None) for i in range(20)]
    familia = [L(d, v, desc="PIX RECEBIDO MAE", doc="98765432100")
               for d, v in ((date(2026, 7, 3), "50"), (date(2026, 7, 19), "200"),
                            (date(2026, 8, 30), "80"), (date(2026, 9, 9), "50"))]
    assert receitas_do_extrato(soltas + familia) == []


def test_mesmo_documento_com_descricoes_diferentes_junta():
    (c,) = receitas_do_extrato([L(MESES[1], desc="PIX A", doc=CPF), L(MESES[2], desc="TED B", doc=CPF)])
    assert c["ultima"] == MESES[2]


def test_mesma_descricao_com_documentos_diferentes_nao_junta():
    assert receitas_do_extrato([L(MESES[1], doc="11111111111"), L(MESES[2], doc="22222222222")]) == []


def test_sem_documento_do_pagador_fica_fora_mesmo_com_a_mesma_descricao():
    """Muito crédito não tem documento e tem a mesma descrição genérica ("Pix recebido"): agrupar por
    ela une pessoas diferentes numa receita que não existe."""
    assert receitas_do_extrato([L(MESES[1], "80", desc="Pix recebido", doc=None),
                                L(MESES[2], "100", desc="Pix recebido", doc=None)]) == []
    assert receitas_do_extrato([L(MESES[1], "80", desc="Pix recebido"), L(MESES[2], "100", desc="Pix recebido")])


def test_credito_duplicado_conta_uma_vez():
    """Pendente e efetivado com ids diferentes, ou duas conexões do mesmo banco: mesma chave, data e valor."""
    linhas = [L(d) for d in MESES]
    (c,) = receitas_do_extrato(linhas + [L(MESES[2]), L(MESES[1])])
    assert c["ultima"] == MESES[2] and c["valor"] == D("1000")  # não somou a duplicata


def test_creditos_do_mesmo_dia_somam_e_cada_parcela_guarda_as_contas_das_copias():
    """Salário + vale do mesmo CNPJ no mesmo dia: sem a soma o intervalo 0 mata a cadeia. Cópia idêntica
    em outra conta entra no grupo da parcela, não vira outra parcela (não dobra)."""
    for ordem in (1, -1):
        linhas = []
        for d in MESES:
            linhas += [L(d, "5000", conta_=1), L(d, "800", desc="VALE", conta_=2)][::ordem]
        (c,) = receitas_do_extrato(linhas + [L(MESES[2], "5000", conta_=3)])
        assert c["valor"] == D("5800") and sorted(map(sorted, c["grupos"])) == [[1, 3], [2]]


def test_credito_extra_no_mesmo_dia_so_no_ultimo_mes_soma():
    (c,) = receitas_do_extrato([L(MESES[0]), L(MESES[1]), L(MESES[2]), L(MESES[2], "150", desc="EXTRA")])
    assert c["valor"] == D("1150") and c["ultima"] == MESES[2]
    assert receitas_do_extrato([L(MESES[0]), L(MESES[1]), L(MESES[2]), L(MESES[2], "500")]) == []  # soma > 20%


@pytest.mark.parametrize("dias,detecta", [(24, False), (25, True), (35, True), (36, False)])
def test_borda_do_intervalo(dias, detecta):
    ini = date(2026, 7, 1)
    assert bool(receitas_do_extrato([L(ini), L(ini + timedelta(days=dias))])) is detecta


@pytest.mark.parametrize("doc", ["0", "000.000.000-00", "00000000000000", "***.456.789-**", "12345678", "123456789012"],
                         ids=["zero", "cpf_zerado", "cnpj_zerado", "mascarado", "8_digitos", "12_digitos"])
def test_documento_invalido_nao_vira_chave(doc):
    """Pessoas diferentes com o mesmo placeholder viravam uma receita; 11/14 dígitos reais seguem valendo."""
    assert receitas_do_extrato([L(MESES[1], doc=doc, desc="PIX JOAO"), L(MESES[2], doc=doc, desc="PIX MARIA")]) == []
    assert receitas_do_extrato([L(MESES[1], doc="123.456.789-01"), L(MESES[2], doc="12345678901")])
    assert receitas_do_extrato([L(MESES[1], doc="12.345.678/0001-95"), L(MESES[2], doc="12345678000195")])


@pytest.mark.parametrize("desc", ["PIX RECEBIDO INTERESTADUAL TRANSPORTES LTDA", "Pix recebido de Interestelar Producoes"])
def test_rendimento_casa_palavra_inteira_nao_substring(desc):
    assert receitas_do_extrato([L(d, desc=desc) for d in MESES])


def test_dia_e_o_da_cadeia_nao_o_do_ultimo_credito():
    (c,) = receitas_do_extrato([L(date(2026, 7, 5)), L(date(2026, 8, 5)), L(date(2026, 9, 8))])
    assert c["dia"] == 5 and c["ultima"].day == 8


@pytest.mark.parametrize("extra", [
    {"category": "Same person transfer"}, {"category": "Fixed income"},
    {"desc": "RESGATE CAIXINHA"}, {"desc": "Pagamento recebido"},
    {"category": "Interests"}, {"category": "Proceeds interests and dividends"}, {"desc": "Rendimento"},
    {"desc": "Dividendos ACME"},
    {"doc": CPF, "eu": CPF},                                  # Pix para si, vindo de outro banco
    {"doc": "123.456.789-01", "eu": "12345678901"},           # a mesma pessoa com máscara
], ids=["same_person", "renda_fixa", "caixinha", "pagamento_recebido", "juros", "dividendos", "rendimento", "dividendo_na_descricao",
         "pagador_igual_recebedor", "com_mascara"])
def test_exclusoes(extra):
    linhas = [L(d, **extra) for d in MESES]
    assert receitas_do_extrato(linhas) == []
    assert receitas_do_extrato([L(d, **{**extra, "category": None, "desc": "EMPRESA X", "doc": DOC, "eu": None})
                                for d in MESES])  # controle positivo: sem a marca, detecta


def test_o_documento_nao_vai_para_o_id_nem_para_o_nome():
    (c,) = receitas_do_extrato([L(d, doc=CPF, nome="FULANO") for d in MESES])
    assert CPF not in str(c) and c["nome"] == "FULANO"


# ── Pelo `ler`, com banco real ───────────────────────────────────────────────

def credito_mensal(uid, prefixo="sal", valor="5000", desc="EMPRESA X", pagador=DOC, **kw):
    txs = mensais(prefixo, [valor] * 2, desc=desc, pagador=pagador, **kw)
    semeia(uid, [conta(f"acc-{prefixo}", txs)], [])
    return txs


def test_detecta_pelo_ler_com_dois_creditos(user_id):
    db.set_balance(user_id, D(1000))
    credito_mensal(user_id)
    s = ler_em(user_id)
    es = entradas(s)
    assert [e.data for e in es] == [date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5)]
    assert all(e.incluida and e.fonte == "recorrencia_banco" and e.origem_id.startswith("extrato#") for e in es)
    assert "receita_nao_garantida" in codigos(es[0])
    assert saldo_90(s) == D("16000")


@pytest.mark.parametrize("ruim", ["NaN", "Infinity"])
def test_credito_nao_finito_no_espelho_nao_derruba_a_previsao_e_a_valida_continua(user_id, ruim):
    """`amount` é `numeric` e guarda NaN/Infinity, que no Postgres são `> 0`: sem o filtro finito do SQL,
    `receitas_do_extrato` levanta `InvalidOperation` e a Previsão inteira dá 500. O não finito não vira receita."""
    ok = mensais("ok", ["5000"] * 2, desc="EMPRESA X", pagador=DOC)
    mau = mensais("mau", ["1000", ruim], desc="OUTRA Y", pagador="99988877766")
    semeia(user_id, [conta("acc-ok", ok), conta("acc-mau", mau)], [])
    assert {e.valor for e in entradas(ler_em(user_id))} == {D("5000")}


def test_contagem_dupla_recorrencia_de_entrada_da_pluggy_nao_e_lida(user_id):
    db.set_balance(user_id, D(1000))
    txs = mensais("sal", ["5000"] * 3, desc="EMPRESA X", pagador=DOC)
    semeia(user_id, [conta("acc-sal", txs)], [rp("EMPRESA X", 5000, txs)])
    s = ler_em(user_id)
    assert len(entradas(s)) == 3 and saldo_90(s) == D("16000")  # 1 por ciclo; 2 se a Pluggy voltar


def test_recorrencia_de_entrada_sem_creditos_mensais_nao_gera_receita(user_id):
    txs = [tx("so-1", "5000", date(2026, 9, 5), desc="EMPRESA X", pagador=DOC)]
    semeia(user_id, [conta("acc-so", txs)], [rp("EMPRESA X", 5000, txs)])
    assert entradas(ler_em(user_id)) == []


def test_autotransferencia_entre_contas_do_usuario_fica_fora(user_id):
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"], desc="PIX ENVIADO") for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred), conta("acc-b", deb)], [])
    assert entradas(ler_em(user_id)) == []


def test_debito_de_outro_usuario_nao_derruba_o_credito():
    a, b = usuario_pagante(), usuario_pagante()
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    semeia(a, [conta("acc-a", cred)], [])
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"], desc="PIX ENVIADO") for i, t in enumerate(cred)]
    semeia(b, [conta("acc-b", deb)], [])
    assert len(entradas(ler_em(a))) == 3


def test_debito_na_mesma_conta_nao_e_autotransferencia(user_id):
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"], desc="PIX ENVIADO") for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred + deb)], [])
    assert len(entradas(ler_em(user_id))) == 3


def test_credito_em_cartao_nao_e_receita(user_id):
    est = mensais("est", ["15"] * 3, desc="ESTORNO LOJA", pagador=DOC)
    semeia(user_id, [conta("acc-est", est, tipo="CREDIT")], [])
    assert entradas(ler_em(user_id)) == []


def test_isolamento_usuario_a_nao_ve_a_receita_de_b_com_o_mesmo_id_de_transacao():
    a, b = usuario_pagante(), usuario_pagante()
    credito_mensal(b)  # sal-0, sal-1 em B
    assert len(entradas(ler_em(b))) == 3
    semeia(a, [conta("acc-a", [tx("sal-0", "9", date(2026, 9, 5), desc="OUTRA COISA", pagador=DOC)])], [])
    assert entradas(ler_em(a)) == []
    assert "EMPRESA" not in str(previsao_v2.apresentar(ler_em(a), a, 90, (30, 60, 90), True))


def test_receita_manual_casada_conta_so_a_manual(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_income(user_id, "Empresa X", 5000, "salario", 5, start_date=HOJE)
    credito_mensal(user_id)
    s = ler_em(user_id)
    assert all(not e.incluida and "recorrencia_banco_igual_a_fixo_manual" in codigos(e) for e in entradas(s))
    assert saldo_90(s) == D("16000")


def test_receita_manual_nao_casada_nao_soma_a_detectada(user_id):
    db.set_balance(user_id, D(1000))
    create_recurring_income(user_id, "Freela", 300, "salario", 20, start_date=HOJE)
    credito_mensal(user_id)
    s = ler_em(user_id)
    assert all(not e.incluida and "recorrencia_banco_pode_repetir_manual" in codigos(e) for e in entradas(s))
    assert saldo_90(s) == D("1900")  # 1000 + 3 × 300


def test_o_documento_do_pagador_nunca_sai(user_id):
    db.set_balance(user_id, D(1000))
    txs = mensais("sal", ["5000"] * 2, desc="PIX RECEBIDO", pagador="123.456.789-01", recebedor="999.888.777-66",
                  nome_pagador="EMPRESA X LTDA")
    semeia(user_id, [conta("acc-sal", txs)], [])
    s = ler_em(user_id)
    assert len(entradas(s)) == 3 and entradas(s)[0].nome == "EMPRESA X LTDA"
    saidas = [str([e.detalhe() for e in s.ocorrencias]), str(s.motivos),
              str(previsao_v2.apresentar(s, user_id, 90, (30, 60, 90), True)),
              str(_check_cashflow(user_id, {"days": 90}))]
    for texto in saidas:
        assert not re.search(r"123[.]?456[.]?789[-]?01|99988877766", texto), texto[:200]
    assert any("extrato#" in t for t in saidas[:1])


def test_credito_sem_documento_do_pagador_nao_gera_receita_pelo_ler(user_id):
    txs = [tx("a", "80", date(2026, 8, 12), desc="Pix recebido"), tx("b", "100", date(2026, 9, 10), desc="Pix recebido")]
    semeia(user_id, [conta("acc", txs)], [])
    assert entradas(ler_em(user_id)) == []


def test_rendimento_mensal_nao_e_receita_pelo_ler(user_id):
    txs = mensais("r", ["35.12", "36.01", "37.40"], desc="Rendimento", category="Interests", pagador=DOC)
    semeia(user_id, [conta("acc", txs)], [])
    assert entradas(ler_em(user_id)) == []


def test_conta_em_dolar_nao_gera_receita(user_id):
    c = conta("acc-usd", mensais("usd", ["5000"] * 3, desc="EMPRESA X", pagador=DOC)) | {"currency": "USD"}
    semeia(user_id, [c], [])
    assert entradas(ler_em(user_id)) == []


@pytest.mark.parametrize("dias,esperado", [(0, 0), (1, 0), (2, 3), (5, 3)], ids=["mesmo_dia", "um_dia", "dois_dias", "cinco_dias"])
def test_autotransferencia_so_vale_em_um_dia_de_distancia(user_id, dias, esperado):
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"] + timedelta(days=dias), desc="PIX ENVIADO")
           for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred), conta("acc-b", deb)], [])
    assert len(entradas(ler_em(user_id))) == esperado


def test_debito_em_conta_em_dolar_nao_e_autotransferencia(user_id):
    """Débito de US$ 1000 não é o repasse de um salário de R$ 1000: só conta BRL entra no `not exists`
    (o controle positivo é `test_autotransferencia_entre_contas_do_usuario_fica_fora`)."""
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"], desc="PIX ENVIADO") for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred), conta("acc-b", deb) | {"currency": "USD"}], [])
    assert len(entradas(ler_em(user_id))) == 3


@pytest.mark.parametrize("fora,maior", [("a", "a"), ("a", "b"), ("b", "a"), ("b", "b"), (None, "a")],
                         ids=["maior_fora", "menor_fora_a", "menor_fora_b", "maior_fora_b", "as_duas_na_base"])
def test_credito_somado_de_duas_contas_so_entra_com_todas_na_base(user_id, fora, maior):
    """Salário + vale do mesmo pagador no mesmo dia em DUAS contas: o valor
    previsto é a soma. Com uma delas fora da base, projetar a soma seria contar dinheiro de conta excluída."""
    db.set_balance(user_id, D(1000))
    va, vb = ("5000", "800") if maior == "a" else ("800", "5000")
    a = mensais("a", [va] * 3, desc="SALARIO", pagador=DOC)
    b = mensais("b", [vb] * 3, desc="VALE", pagador=DOC)
    semeia(user_id, [conta("acc-a", a, saldo=None if fora == "a" else "0"),
                     conta("acc-b", b, saldo=None if fora == "b" else "0")], [])
    es = entradas(ler_em(user_id))
    assert len(es) == 3 and {e.valor for e in es} == {D("5800")}
    assert all(("recorrencia_banco_conta_fora_da_base" in codigos(e)) is (fora is not None) for e in es)
    assert all(e.incluida is (fora is None) for e in es)


@pytest.mark.parametrize("fora,entra", [("a", True), ("b", True), (None, True), ("ambas", False)])
@pytest.mark.parametrize("ids", [("m", "n"), ("n", "m")], ids=["a_primeiro", "b_primeiro"])
def test_credito_duplicado_em_duas_contas_entra_se_uma_esta_na_base(user_id, fora, entra, ids):
    """Mesmo valor, pagador e dia em A e B é a MESMA parcela: conta uma vez (não dobra) e basta uma das
    contas na base (a que está tem o saldo), qualquer que seja a linha que a deduplicação guarda."""
    db.set_balance(user_id, D(1000))
    a = mensais(ids[0], ["1000"] * 3, desc="SALARIO", pagador=DOC)
    b = mensais(ids[1], ["1000"] * 3, desc="SALARIO", pagador=DOC)
    semeia(user_id, [conta("acc-a", a, saldo=None if fora in ("a", "ambas") else "0"),
                     conta("acc-b", b, saldo=None if fora in ("b", "ambas") else "0")], [])
    es = entradas(ler_em(user_id))
    assert len(es) == 3 and {e.valor for e in es} == {D("1000")}
    assert all(("recorrencia_banco_conta_fora_da_base" in codigos(e)) is (not entra) for e in es)
    assert all(e.incluida is entra for e in es)


def test_reconexao_do_mesmo_banco_nao_deixa_a_receita_fora(user_id):
    """`CONTAS_BANCO_SQL` põe na base só a conta da conexão mais nova de cada `provider_account_id`: o
    crédito das duas conexões vivas vem com as duas contas, e a velha nunca está na base."""
    db.set_balance(user_id, D(1000))
    for _ in range(2):
        semeia(user_id, [conta("acc-a", mensais("s", ["1000"] * 3, desc="SALARIO", pagador=DOC))], [])
    es = entradas(ler_em(user_id))
    assert len(es) == 3 and {e.valor for e in es} == {D("1000")} and all(e.incluida for e in es)


def test_reconexao_com_salario_e_vale_no_mesmo_dia_soma_sem_dobrar(user_id):
    db.set_balance(user_id, D(1000))
    for _ in range(2):
        semeia(user_id, [conta("acc-a", mensais("a", ["5000"] * 3, desc="SALARIO", pagador=DOC)),
                         conta("acc-b", mensais("b", ["800"] * 3, desc="VALE", pagador=DOC))], [])
    es = entradas(ler_em(user_id))
    assert len(es) == 3 and {e.valor for e in es} == {D("5800")} and all(e.incluida for e in es)


def test_debito_em_cartao_de_outra_conta_nao_e_autotransferencia(user_id):
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", "-1000", t["transaction_date"], desc="COMPRA") for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred), conta("acc-cc", deb, tipo="CREDIT")], [])
    assert len(entradas(ler_em(user_id))) == 3


def test_conexao_pausada_some_e_a_viva_do_mesmo_usuario_fica(user_id):
    pausada = semeia(user_id, [conta("acc-p", mensais("p", ["700"] * 3, desc="EMPRESA P", pagador="11111111111",
                                                       nome_pagador="EMPRESA P"))], [])
    semeia(user_id, [conta("acc-v", mensais("v", ["900"] * 3, desc="EMPRESA V", pagador="22222222222",
                                             nome_pagador="EMPRESA V"))], [])
    q("update open_finance_connections set status='PAUSED' where id=%s and user_id=%s", (pausada, user_id))
    assert {e.nome for e in entradas(ler_em(user_id))} == {"EMPRESA V"}


@pytest.mark.parametrize("delta,esperado", [("0.05", 0), ("-0.05", 0), ("0.06", 3), ("-0.06", 3)],
                         ids=["mais_5c", "menos_5c", "mais_6c", "menos_6c"])
def test_autotransferencia_tolera_cinco_centavos_para_os_dois_lados(user_id, delta, esperado):
    cred = mensais("in", ["1000"] * 3, desc="PIX RECEBIDO JOAO", pagador=DOC)
    deb = [tx(f"out-{i}", D("-1000") - D(delta), t["transaction_date"], desc="PIX ENVIADO") for i, t in enumerate(cred)]
    semeia(user_id, [conta("acc-a", cred), conta("acc-b", deb)], [])
    assert len(entradas(ler_em(user_id))) == esperado


@pytest.mark.parametrize("primeiro,esperado", [(date(2026, 4, 3), 0), (date(2026, 4, 5), 3)], ids=["181_dias_fora", "179_dias_dentro"])
def test_janela_do_extrato(user_id, primeiro, esperado):
    cred = [tx("in-0", "1000", primeiro, desc="PIX", pagador=DOC),
            tx("in-1", "1000", primeiro + timedelta(days=30), desc="PIX", pagador=DOC)]
    semeia(user_id, [conta("acc-a", cred)], [])
    assert len(entradas(ler_em(user_id))) == esperado


@pytest.mark.parametrize("debito,esperado", [(date(2026, 4, 3), 0), (None, 3)], ids=["debito_na_borda", "sem_debito"])
def test_debito_um_dia_antes_da_janela_ainda_tira_o_credito_da_borda(user_id, debito, esperado):
    """A janela é HOJE - 180 = 2026-04-04: o 1º crédito está na borda e o débito dele, um dia antes dela."""
    cred = [tx("in-0", "1000", date(2026, 4, 4), desc="PIX", pagador=DOC),
            tx("in-1", "1000", date(2026, 5, 4), desc="PIX", pagador=DOC)]
    deb = [tx("out", "-1000", debito, desc="PIX ENVIADO")] if debito else []
    semeia(user_id, [conta("acc-a", cred), conta("acc-b", deb)], [])
    assert len(entradas(ler_em(user_id))) == esperado
