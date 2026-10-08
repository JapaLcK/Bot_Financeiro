"""Fase 1a: a Previsão lê as recorrências do Open Finance (`previsao_recorrencias.py`).

Banco real: a semeadura passa pelo `save_open_finance_sync` e pelo `salvar_recorrencias`
(`_apoio_assinaturas`). O relógio é congelado chamando `cashflow_snapshot.ler` com `hoje`
fixo, numa transação read only (toda leitura daqui prova também que `ler` não escreve).
A convivência com o fixo manual e a comparação antes × depois estão no arquivo irmão
`test_previsao_recorrencias_of_convivencia.py`."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

import db
from _apoio_assinaturas import HOJE, conta, mensais, netflix_no_cartao, rp, semeia, tx
from api.v2.previsao import Previsao
from conftest import usuario_pagante
from core.services import previsao_v2
from core.services.assinaturas import listar_assinaturas
from core.services.cashflow import _projection
from core.services.cashflow_forecast import _trajectory
from core.services.cashflow_snapshot import ler
from db.of_recurring import marcar
from tests.test_cashflow_snapshot import q


def ler_em(uid, hoje=HOJE, dias=90, bancos=True):
    agora = datetime.combine(hoje, time(12), tzinfo=timezone.utc)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        s = ler(cur, uid, hoje, hoje + timedelta(days=dias), agora, bancos)
        conn.rollback()
    return s


def do_banco(s, nome=None):
    return [e for e in s.ocorrencias if e.fonte == "recorrencia_banco" and (nome is None or e.nome == nome)]


def codigos(e):
    return {m.codigo for m in e.motivos}


def netflix(uid, valores=("-39.90",) * 3, ultima=date(2026, 9, 5), prefixo="nf", tipo="BANK"):
    txs = mensais(prefixo, list(valores), ultima=ultima, desc="NETFLIX.COM")
    return semeia(uid, [conta(f"acc-{prefixo}", txs, tipo=tipo)], [rp("NETFLIX.COM", float(valores[-1]), txs)])


def salario(uid, valor="5000", ultima=date(2026, 9, 5), prefixo="sal", desc="SALARIO EMPRESA X"):
    txs = mensais(prefixo, [valor] * 3, ultima=ultima, desc=desc)
    return semeia(uid, [conta(f"acc-{prefixo}", txs)], [rp(desc, float(valor), txs)])


def test_1_saida_mensal_simples_e_valor_exato_na_v2(user_id):
    netflix(user_id)
    s = ler_em(user_id)
    es = do_banco(s)
    assert [e.data for e in es] == [date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5)]
    assert {(e.valor, e.direcao, e.tipo, e.incluida, e.qualidade_valor, e.qualidade_data) for e in es} == {
        (D("39.90"), "saida", "gasto_fixo", True, "estimado", "presumida")}
    assert {e.realizacao for e in es} == {"prevista"}
    corpo = Previsao.model_validate(previsao_v2.apresentar(s, user_id, 90, (30, 60, 90), True)).model_dump(mode="json")
    (g,) = [g for g in corpo["compromissos"] if g["fonte"] == "recorrencia_banco"]
    assert g["nome"] == "NETFLIX.COM" and len(g["ocorrencias"]) == 3
    assert {(o["valor"], o["incluida_no_calculo"]) for o in g["ocorrencias"]} == {("39.90", True)}
    assert {"recorrencia_banco_estimada"} <= {m["codigo"] for m in g["ocorrencias"][0]["motivos"]}


def test_2_dia_31_cai_no_fim_do_mes_inclusive_fevereiro(user_id):
    txs = [tx("d31-0", "-80", date(2026, 7, 31), desc="ACADEMIA"), tx("d31-1", "-80", date(2026, 8, 31), desc="ACADEMIA"),
           tx("d31-2", "-80", date(2026, 9, 30), desc="ACADEMIA")]
    semeia(user_id, [conta("acc-31", txs)], [rp("ACADEMIA", -80, txs)])
    datas = [e.data for e in do_banco(ler_em(user_id, dias=520))]
    assert datas[:3] == [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)]
    assert date(2027, 2, 28) in datas and date(2028, 2, 29) in datas and len(datas) == len(set(datas)) == 17


def test_3_cobranca_adiantada_na_virada_nao_repete_o_ciclo(user_id):
    txs = [tx(f"ad-{i}", "-20", d, desc="SEGURO") for i, d in
           enumerate((date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 9, 30)))]
    semeia(user_id, [conta("acc-ad", txs)], [rp("SEGURO", -20, txs)])
    assert [e.data for e in do_banco(ler_em(user_id))] == [date(2026, 11, 1), date(2026, 12, 1)]


def test_4_ja_realizado_neste_mes(user_id):
    txs = [tx(f"jr-{i}", "-30", d, desc="ESCOLA") for i, d in
           enumerate((date(2026, 8, 10), date(2026, 9, 10), date(2026, 10, 7)))]
    semeia(user_id, [conta("acc-jr", txs)], [rp("ESCOLA", -30, txs)])
    datas = [e.data for e in do_banco(ler_em(user_id, hoje=date(2026, 10, 8)))]
    assert datas[0] == date(2026, 11, 10) and all(d.month != 10 or d.year != 2026 for d in datas)


def test_5_atrasada_saida_pesa_na_partida_e_entrada_fica_fora(user_id):
    db.set_balance(user_id, D(1000))
    netflix(user_id)
    salario(user_id)
    hoje = date(2026, 10, 8)
    s = ler_em(user_id, hoje=hoje, dias=30)
    nf, sal = do_banco(s, "NETFLIX.COM"), do_banco(s, "SALARIO EMPRESA X")
    assert nf[0].data == sal[0].data == date(2026, 10, 5)
    assert nf[0].incluida and nf[0].realizacao == "a_conferir" and "recorrencia_banco_atrasada" in codigos(nf[0])
    assert not sal[0].incluida and "recorrencia_banco_atrasada" in codigos(sal[0])
    assert nf[1].incluida and sal[1].incluida and "recorrencia_banco_atrasada" not in codigos(nf[1]) | codigos(sal[1])
    vencidos = _trajectory(hoje, s.base, s.ocorrencias, 30, D(0))["vencidos"]
    assert [(v["nome"], v["date"]) for v in vencidos] == [("NETFLIX.COM", "2026-10-05")]
    assert _projection(hoje, s.base, s.ocorrencias, hoje)["projetado"] == D("960.10")


def test_6_interrompida_fica_fora_nas_duas_direcoes(user_id):
    netflix(user_id, ultima=date(2026, 8, 10))
    salario(user_id, ultima=date(2026, 8, 10))
    es = do_banco(ler_em(user_id))
    assert len(es) == 6 and {e.data for e in es} == {date(2026, 10, 10), date(2026, 11, 10), date(2026, 12, 10)}
    assert not any(e.incluida for e in es)
    assert all("recorrencia_banco_interrompida" in codigos(e) for e in es)


def test_7_reajuste_usa_o_ultimo_valor(user_id):
    netflix(user_id, valores=("-39.90", "-39.90", "-44.90"))
    es = do_banco(ler_em(user_id))
    assert {(e.valor, e.qualidade_valor) for e in es} == {(D("44.90"), "estimado")}


def test_8_receita_entra_como_nao_garantida(user_id):
    salario(user_id)
    es = do_banco(ler_em(user_id))
    assert len(es) == 3 and all(e.incluida and e.direcao == "entrada" and e.tipo == "receita" for e in es)
    m = next(m for m in es[0].motivos if m.codigo == "receita_nao_garantida")
    assert (m.direcao_do_erro, m.efeito_quantificado) == ("so_melhora", D("5000"))


def test_9_ignorar_some_com_a_saida_e_nao_com_a_receita_homonima(user_id):
    # A saída termina antes da entrada começar: sem a direção na cadeia, as duas colariam.
    out = mensais("tj-out", ["-100"] * 3, ultima=date(2026, 6, 5), desc="TRANSF JOAO")
    inn = mensais("tj-in", ["300"] * 3, ultima=date(2026, 9, 6), desc="TRANSF JOAO")
    semeia(user_id, [conta("acc-tj", out + inn)], [rp("TRANSF JOAO", -100, out), rp("TRANSF JOAO", 300, inn)])
    assert {e.direcao for e in do_banco(ler_em(user_id))} == {"entrada", "saida"}
    marcar(user_id, "transf joao", "ignorar")
    es = do_banco(ler_em(user_id))
    assert es and {(e.direcao, e.valor, e.incluida) for e in es} == {("entrada", D("300"), True)}


def test_10_conexao_pausada_ou_apagada_fica_fora_e_viva_em_erro_entra(user_id):
    cid = netflix(user_id)
    for status, conta_ in (("PAUSED", 0), ("DELETED", 0), ("LOGIN_ERROR", 3)):
        q("update open_finance_connections set status=%s where id=%s and user_id=%s", (status, cid, user_id))
        assert len(do_banco(ler_em(user_id))) == conta_, status


def test_11_duas_conexoes_nao_fundem_homonimos(user_id):
    netflix(user_id, prefixo="nf-a")
    netflix(user_id, prefixo="nf-b")
    es = do_banco(ler_em(user_id))
    assert len(es) == 6 and len({e.origem_id for e in es}) == 2 and all(e.incluida for e in es)
    assert sum(e.assinado for e in es) == D("-239.40")


def test_12_isolamento_entre_usuarios_pelo_id_de_transacao():
    a, b = usuario_pagante(), usuario_pagante()
    netflix(b)  # nf-0..2 em B
    # A tem transações com os MESMOS ids da Pluggy e uma recorrência que aponta para eles.
    txs_a = mensais("nf", ["-999"] * 3, desc="OUTRA COISA")
    semeia(a, [conta("acc-a", [])], [rp("NETFLIX.COM", -39.9, txs_a)])
    semeia(a, [conta("acc-a2", txs_a)], [])
    for bancos in (True, False):
        assert not do_banco(ler_em(a, bancos=bancos))
    assert {e.valor for e in do_banco(ler_em(b))} == {D("39.90")}
    corpo = previsao_v2.apresentar(ler_em(a), a, 90, (30, 60, 90), True)
    assert "NETFLIX" not in str(corpo) and "recorrencia_banco" not in str(corpo)


def test_13_cartao_saida_fora_e_receita_no_cartao_descartada(user_id):
    netflix_no_cartao(user_id)
    est = mensais("est", ["15"] * 3, desc="ESTORNO LOJA")
    semeia(user_id, [conta("acc-est", est, tipo="CREDIT")], [rp("ESTORNO LOJA", 15, est)])
    es = do_banco(ler_em(user_id))
    assert len(es) == 3 and {e.nome for e in es} == {"NETFLIX.COM"}
    assert not any(e.incluida for e in es)
    assert all("incorporacao_cartao_nao_comprovada" in codigos(e) for e in es)


def test_14_pagamento_de_fatura_sem_categoria_nao_e_recorrencia(user_id):
    pf = mensais("pf", ["-1200"] * 3, desc="PAGAMENTO DE FATURA")
    semeia(user_id, [conta("acc-pf", pf)], [rp("PAGAMENTO DE FATURA", -1200, pf)])
    netflix(user_id)  # positivo: a vizinha legítima continua
    assert {e.nome for e in do_banco(ler_em(user_id))} == {"NETFLIX.COM"}
    lista = listar_assinaturas(user_id, HOJE)
    assert [x["nome"] for x in lista["servicos"] + lista["outras"]] == ["NETFLIX.COM"]


def test_19_sem_bancos_na_base_nao_le_recorrencia(user_id):
    netflix(user_id)
    salario(user_id)
    s = ler_em(user_id, bancos=False)
    assert not do_banco(s) and "recorrencias_banco_nao_lidas" not in {m.codigo for m in s.motivos}


@pytest.mark.parametrize("fetched,avisa", [(None, True), ("now() - interval '3 days'", True), ("now()", False)],
                         ids=["nunca_lida", "velha", "fresca"])
def test_20_lista_nao_lida_ou_velha_vira_motivo(user_id, fetched, avisa):
    cid = netflix(user_id)
    q(f"update open_finance_connections set recurring_fetched_at={fetched or 'null'} where id=%s and user_id=%s",
      (cid, user_id))
    s = ler_em(user_id, hoje=datetime.now(timezone.utc).date())
    assert ("recorrencias_banco_nao_lidas" in {m.codigo for m in s.motivos}) is avisa
    assert any("se repetem todo mês" in p for p in s.premissas)
