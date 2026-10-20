"""Estimativa do gasto variável na previsão (`core/services/previsao_variavel.py`): Postgres real.

Relógio congelado: `hoje` é argumento de `cashflow_snapshot.ler` (HOJE = 20/10/2026), então nenhum teste
depende do dia em que roda. Os lançamentos são gravados direto, no fuso do app, `n` dias antes de HOJE.
Cada conserto tem o seu controle negativo medido à mão (a linha desligada em `previsao_variavel.py`) e
reportado no PR; o controle positivo ("Mercado" continua contando) mora no mesmo teste.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest
from psycopg.types.json import Jsonb

import db
import core.services.previsao_variavel as pv
from api.v2.previsao import Previsao
from conftest import usuario_pagante
from core.services import cashflow_snapshot, previsao_v2
from core.services.cashflow import _risco
from core.services.cashflow_contract import FONTE_VARIAVEL, Snapshot
from core.services.cashflow_forecast import _trajectory
from core.services.decision_simulator import Simulacao, simular
from core.services.previsao_variavel import mediana_por_categoria, ocorrencias
from tests._patrimonio_helpers import conexao, conta, q as qp, tx_banco
from tests.test_cashflow_snapshot import q
from utils_date import _tz

HOJE = date(2026, 10, 20)
AGORA = datetime(2026, 10, 20, 3, 30, tzinfo=timezone.utc)  # 00:30 em São Paulo


def ler_em(uid, hoje=HOJE, dias=90, bancos=False):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        s = cashflow_snapshot.ler(cur, uid, hoje, hoje + timedelta(days=dias), datetime.now(timezone.utc), bancos)
        conn.rollback()
    return s


def gasto(uid, valor, atras, categoria="mercado", alvo=None, hora=12, tipo="despesa", hoje=HOJE, minuto=0, **cols):
    """Lançamento `atras` dias antes de `hoje`, às `hora`:`minuto` (fuso do app)."""
    criado = datetime.combine(hoje - timedelta(days=atras), time(hora, minuto), tzinfo=_tz())
    cols = {"user_id": uid, "tipo": tipo, "valor": valor, "criado_em": criado, "categoria": categoria,
            "alvo": alvo, "source": "manual", **cols}
    return qp(f"insert into launches ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) returning id",
              tuple(cols.values()))["id"]


def historico(uid, hoje=HOJE, dias=100):
    """Uma entrada antiga: define o início do histórico (fora das janelas, sem pesar na mediana)."""
    gasto(uid, 1, dias, categoria="salario", tipo="receita", hoje=hoje)


def tres_janelas(uid, valores=(100, 120, 900), categoria="mercado", **kw):
    for i, v in enumerate(valores):  # uma por janela: dias 10, 40 e 70 atrás
        gasto(uid, v, 10 + 30 * i, categoria=categoria, **kw)


def mensal(s):
    return {c["chave"]: c["despesa_mensal"] for c in s.estimativa_variavel["categorias"]}


# ── funções puras ────────────────────────────────────────────────────────────

def linhas(*itens):
    return [(HOJE - timedelta(days=a), c, D(v)) for a, c, v in itens]


def test_mediana_de_3_janelas_ignora_o_pico():
    r, k = mediana_por_categoria(linhas((10, "m", 100), (40, "m", 120), (70, "m", 900)), HOJE, 90)
    assert r == {"m": D("120.00")} and k == 3  # a média seria 373,33


def test_categoria_so_numa_janela_vale_zero_e_sai():
    r, _ = mediana_por_categoria(linhas((10, "a", 500), (10, "m", 10), (40, "m", 10), (70, "m", 10)), HOJE, 90)
    assert r == {"m": D("10.00")}


@pytest.mark.parametrize("atras,janela", [(1, 0), (30, 0), (31, 1), (60, 1), (61, 2), (90, 2)])
def test_bordas_de_janela(atras, janela):
    """Totais [10, 20, 30] nas janelas (mediana 20); cada borda tem de cair na janela certa, ou a mediana muda."""
    interior = [15, 45, 75]
    dias = [atras if j == janela else interior[j] for j in range(3)]
    r, _ = mediana_por_categoria(linhas(*[(d, "m", 10 * (j + 1)) for j, d in enumerate(dias)]), HOJE, 90)
    assert r == {"m": D("20.00")}


@pytest.mark.parametrize("atras", [0, 91, -1])
def test_hoje_e_alem_de_90_dias_ficam_fora(atras):
    r, _ = mediana_por_categoria(linhas((atras, "m", 1000), (15, "m", 10), (45, "m", 20), (75, "m", 30)), HOJE, 90)
    assert r == {"m": D("20.00")}


def test_duas_janelas_e_a_media_e_uma_janela_e_o_total():
    r, k = mediana_por_categoria(linhas((10, "m", 100), (40, "m", 200)), HOJE, 75)
    assert (r, k) == ({"m": D("150.00")}, 2)
    r, k = mediana_por_categoria(linhas((10, "m", 100), (40, "m", 200)), HOJE, 45)
    assert (r, k) == ({"m": D("100.00")}, 1)  # só a janela 1 conta


def test_menos_de_30_dias_leva_o_total_a_30_dias_e_menos_de_7_nao_estima():
    r, k = mediana_por_categoria(linhas((3, "m", 70)), HOJE, 10)
    assert (r, k) == ({"m": D("210.00")}, 0)  # 70 em 10 dias × 30/10
    assert mediana_por_categoria(linhas((3, "m", 70)), HOJE, 6) is None
    assert mediana_por_categoria(linhas((3, "m", 70)), HOJE, 7)[0] == {"m": D("300.00")}


def test_mensal_e_a_soma_exata_das_categorias_e_30_dias_somam_o_mensal():
    r, _ = mediana_por_categoria(linhas(*[(a, c, "33.335") for a in (10, 40, 70) for c in "abc"]), HOJE, 90)
    assert r == {c: D("33.34") for c in "abc"}
    oc = ocorrencias(HOJE, HOJE + timedelta(days=90), D("100.00"))
    assert [o.data for o in oc] == [HOJE + timedelta(days=i) for i in range(1, 91)]
    assert round(sum((o.valor for o in oc[:30]), D(0)), 2) == D("100.00")
    assert ocorrencias(HOJE, HOJE + timedelta(days=5), D(0)) == []


# ── piso, mediana e contrato, com banco real ─────────────────────────────────

def test_mediana_do_banco_e_ocorrencias_de_amanha_em_diante(user_id):
    historico(user_id)
    tres_janelas(user_id)
    gasto(user_id, 5000, 20, tipo="receita", categoria="salario")  # entrada nunca abate
    gasto(user_id, 999, 0)  # hoje fica fora: já está no saldo
    s = ler_em(user_id)
    e = s.estimativa_variavel
    assert e["despesa_mensal"] == D("120.00") and mensal(s) == {"mercado": D("120.00")}
    assert (e["dias_de_historico"], e["janelas_completas"]) == (90, 3)
    assert e["janela"] == {"inicio": "2026-07-22", "fim": "2026-10-19"}
    assert e["categorias"][0]["nome"] == "mercado"
    assert "gastos_variaveis_nao_estimados" not in {m.codigo for m in s.motivos}
    assert "Inclui estimativa dos gastos do dia a dia" in " ".join(s.premissas)
    assert s.qualidade()["cobertura"]["inclui_estimativa_variavel"] is True
    est = [o for o in s.ocorrencias if o.fonte == FONTE_VARIAVEL]
    assert [o.data for o in est] == [HOJE + timedelta(days=i) for i in range(1, 91)]
    assert est[0].tipo == "gasto_variavel" and est[0].direcao == "saida" and not est[0].motivos


@pytest.mark.parametrize("dias,existe", [(6, False), (7, True)])
def test_piso_de_7_dias(user_id, dias, existe):
    gasto(user_id, 1, dias, categoria="salario", tipo="receita")
    gasto(user_id, 70, 2)
    s = ler_em(user_id)
    assert (s.estimativa_variavel is not None) is existe
    assert ("gastos_variaveis_nao_estimados" in {m.codigo for m in s.motivos}) is not existe
    assert s.qualidade()["cobertura"]["inclui_estimativa_variavel"] is existe
    if existe:
        assert s.estimativa_variavel["despesa_mensal"] == D("300.00")  # 70 em 7 dias × 30/7
        assert s.estimativa_variavel["janelas_completas"] == 0


def test_sem_lancamento_nenhum_nao_estima(user_id):
    assert ler_em(user_id).estimativa_variavel is None


def test_fuso_23h50_de_ontem_entra_e_00h10_de_hoje_fica_fora(user_id):
    """HOJE = 20/10 00:30 em São Paulo (03:30Z). 19/10 23:50 SP já é 20/10 em UTC; 20/10 00:10 SP também.
    Janelas [300 (com o de ontem 23:50), 400, 50]: mediana 300; sem o de ontem seria 50, com o de hoje 400."""
    historico(user_id)
    gasto(user_id, 300, 1, categoria="fuso", hora=23, minuto=50)
    gasto(user_id, 400, 40, categoria="fuso")
    gasto(user_id, 50, 70, categoria="fuso")
    gasto(user_id, 7777, 0, categoria="fuso", hora=0, minuto=10)
    assert mensal(ler_em(user_id)) == {"fuso": D("300.00")}


# ── nada contado duas vezes (controle negativo e positivo no mesmo teste) ────

def fixo(uid, nome, valor, dia, **kw):
    from db.recurring import create_recurring_expense
    return create_recurring_expense(uid, nome, valor, "outros", dia, kw.pop("payment_type", "account"), **kw)


def test_fixo_manual_pago_por_fora_fica_fora_por_nome_e_por_valor_e_dia(user_id):
    historico(user_id)
    fixo(user_id, "Aluguel", 2000, 10, start_date=HOJE - timedelta(days=120))
    fixo(user_id, "Internet fibra", 99.9, 15, start_date=HOJE - timedelta(days=120))
    tres_janelas(user_id, (300, 300, 300))  # Mercado: controle positivo
    tres_janelas(user_id, (2000, 2000, 2000), categoria="moradia", alvo="Aluguel casa")  # nome
    tres_janelas(user_id, (2000, 2000, 2000), categoria="moradia2", alvo="Pix João")  # valor + dia 10
    tres_janelas(user_id, (99.9, 99.9, 99.9), categoria="internet", alvo="Pix NET")  # valor, dia ~15
    s = ler_em(user_id)
    # dia 10 das janelas: HOJE-10 = 10/10 ✓. dia da "internet": 10/10 vs 15 → perto (≤ 5): fora também
    assert mensal(s) == {"mercado": D("300.00")}


def test_so_o_nome_exclui_quando_valor_e_dia_nao_batem(user_id):
    """B2: fixo 'Aluguel' R$ 2.000; 'PIX ALUGUEL' R$ 2.150 está fora da tolerância de valor: só o nome tira."""
    historico(user_id)
    fixo(user_id, "Aluguel", 2000, 10, start_date=HOJE - timedelta(days=120))
    tres_janelas(user_id, (300, 300, 300))
    tres_janelas(user_id, (2150, 2150, 2150), categoria="moradia", alvo="PIX ALUGUEL", hora=12)
    assert mensal(ler_em(user_id)) == {"mercado": D("300.00")}


def test_fixo_manual_de_cartao_e_once_nao_tiram_nada(user_id):
    historico(user_id)
    cid = qp("insert into credit_cards (user_id, name, closing_day, due_day) values (%s,'Fixo',5,12) returning id",
             (user_id,))["id"]
    fixo(user_id, "Netflix", 39.9, 10, payment_type="credit_card", card_id=cid, start_date=HOJE - timedelta(days=120))
    fixo(user_id, "Uma vez", 500, 10, frequency="once", start_date=HOJE - timedelta(days=5))
    tres_janelas(user_id, (39.9, 39.9, 39.9), categoria="stream", alvo="Netflix")
    tres_janelas(user_id, (500, 500, 500), categoria="avulso", alvo="Uma vez")
    assert mensal(ler_em(user_id)) == {"stream": D("39.90"), "avulso": D("500.00")}


def test_pagamento_de_conta_e_legado_do_cobrador_ficam_fora(user_id):
    from db.bills import create_boleto
    from db.recurring import create_recurring_expense
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    por_efeito = [gasto(user_id, 800, 10 + 30 * i, categoria="conta", alvo=f"luz{i}") for i in range(3)]
    for lid in por_efeito:
        q("update launches set efeitos = %s where id=%s and user_id=%s", (Jsonb({"bill_id": 1}), lid, user_id))
    por_instancia = [gasto(user_id, 700, 12 + 30 * i, categoria="conta2", alvo=f"agua{i}") for i in range(3)]
    for i, lid in enumerate(por_instancia):
        b = create_boleto(user_id, f"agua{i}", 700, HOJE + timedelta(days=i + 1))
        q("update bill_instances set launch_id=%s where id=%s and user_id=%s", (lid, b["id"], user_id))
    r = create_recurring_expense(user_id, "Plano", 600, "outros", 5, "account", start_date=HOJE - timedelta(days=100))
    legado = [gasto(user_id, 600, 14 + 30 * i, categoria="plano", alvo=f"Plano {i}") for i in range(3)]
    for i, lid in enumerate(legado):
        q("insert into recurring_charges (recurring_id, user_id, launch_id, amount, ym) values (%s,%s,%s,600,%s)",
          (r["id"], user_id, lid, f"2026-0{i + 1}"))
    assert mensal(ler_em(user_id)) == {"mercado": D("300.00")}


def test_so_o_legado_do_cobrador_exclui_quando_o_fixo_nao_parece_a_linha(user_id):
    """O1: o fixo 'Plano' (R$ 600, dia 5) não casa por nome, valor nem dia com a linha de R$ 77; só o
    `recurring_charges.launch_id` a tira."""
    from db.recurring import create_recurring_expense
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    r = create_recurring_expense(user_id, "Plano", 600, "outros", 5, "account", start_date=HOJE - timedelta(days=100))
    legado = [gasto(user_id, 77, 14 + 30 * i, categoria="outro", alvo=f"Zeta {i}") for i in range(3)]
    for i, lid in enumerate(legado):
        q("insert into recurring_charges (recurring_id, user_id, launch_id, amount, ym) values (%s,%s,%s,600,%s)",
          (r["id"], user_id, lid, f"2026-0{i + 1}"))
    assert mensal(ler_em(user_id)) == {"mercado": D("300.00")}


def test_parcela_interno_e_outra_moeda_ficam_fora(user_id):
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    tres_janelas(user_id, (50, 50, 50), categoria="interno", is_internal_movement=True)
    tres_janelas(user_id, (60, 60, 60), categoria="dolar", currency="USD")
    assert mensal(ler_em(user_id)) == {"mercado": D("300.00")}


def cartao(uid, compras, nome="Nubank"):
    """`compras`: (valor, dias atrás, fim da fatura em dias a partir de HOJE, extras)."""
    cid = qp("insert into credit_cards (user_id, name, closing_day, due_day) values (%s,%s,5,12) returning id",
             (uid, nome))["id"]
    faturas, ids = {}, []
    for valor, atras, fim, ex in compras:
        if fim not in faturas:
            end = HOJE + timedelta(days=fim)
            faturas[fim] = qp("""insert into credit_bills (user_id, card_id, period_start, period_end)
                                 values (%s,%s,%s,%s) returning id""",
                              (uid, cid, end - timedelta(days=30), end))["id"]
        ex = {"categoria": "lazer", **ex}
        ids.append(qp(f"""insert into credit_transactions (bill_id, user_id, card_id, valor, purchased_at,
                          {', '.join(ex)}) values (%s,%s,%s,%s,%s,{', '.join(['%s'] * len(ex))}) returning id""",
                      (faturas[fim], uid, cid, valor, HOJE - timedelta(days=atras), *ex.values()))["id"])
    return ids


def test_cartao_conta_pela_data_da_compra_inclusive_na_fatura_aberta_e_tira_parcela_e_legado(user_id):
    from db.recurring import create_recurring_expense
    historico(user_id)
    cartao(user_id, [(200, 5, 10, {}), (200, 35, -20, {}), (200, 65, -50, {})])  # a 1ª, na fatura aberta (fim no futuro)
    cartao(user_id, [(999, a, 10, {"installments_total": 3, "installment_no": 1, "categoria": "parcela"})
                     for a in (6, 36, 66)], nome="Parcelado")
    r = create_recurring_expense(user_id, "Assina", 30, "outros", 5, "account", start_date=HOJE - timedelta(days=100))
    for i, lid in enumerate(cartao(user_id, [(30, a, 10, {"categoria": "assina"}) for a in (7, 37, 67)], nome="Outro")):
        q("insert into recurring_charges (recurring_id, user_id, credit_tx_id, amount, ym) values (%s,%s,%s,30,%s)",
          (r["id"], user_id, lid, f"2026-0{i + 6}"))
    assert mensal(ler_em(user_id)) == {"lazer": D("200.00")}


def test_recorrencia_no_cartao_fica_na_estimativa(user_id):
    """D4: a previsão não soma a recorrência de cartão por outro caminho; sair da mediana a faria sumir."""
    historico(user_id)
    cartao(user_id, [(40, 5, 10, {"nota": "Netflix", "categoria": "stream"}),
                     (40, 35, -20, {"nota": "Netflix", "categoria": "stream"}),
                     (40, 65, -50, {"nota": "Netflix", "categoria": "stream"})])
    assert mensal(ler_em(user_id)) == {"stream": D("40.00")}


def test_cadeia_do_banco_fora_exceto_a_de_cartao(user_id):
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    na_cadeia = [gasto(user_id, 40, 10 + 30 * i, categoria="stream") for i in range(3)]
    no_cartao = [gasto(user_id, 25, 11 + 30 * i, categoria="app") for i in range(3)]
    com_cadeia = [{"direcao": "saida", "cartao": False, "launches": set(na_cadeia)},
                  {"direcao": "saida", "cartao": True, "launches": set(no_cartao)},
                  {"direcao": "entrada", "cartao": False, "launches": set(no_cartao)}]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        e = pv.estimar(cur, user_id, HOJE, [], com_cadeia, set(), False)
        sem = pv.estimar(cur, user_id, HOJE, [], [], set(), False)
        conn.rollback()
    assert {c["chave"] for c in e["categorias"]} == {"mercado", "app"}
    assert {c["chave"] for c in sem["categorias"]} == {"mercado", "app", "stream"}


def test_cadeia_real_do_open_finance_devolve_os_lancamentos_importados(user_id):
    from _apoio_assinaturas import conta as conta_of, mensais, rp, semeia
    from core.services.previsao_recorrencias import cadeias_do_banco
    txs = mensais("nf", ["-39.90"] * 3, desc="NETFLIX.COM")
    semeia(user_id, [conta_of("acc-nf", txs)], [rp("NETFLIX.COM", -39.9, txs)])
    lid = gasto(user_id, 39.9, 10, categoria="stream")
    q("""update open_finance_transactions set imported_launch_id=%s where provider_transaction_id='nf-2'
         and account_id in (select a.id from open_finance_accounts a join open_finance_connections c
         on c.id=a.connection_id where c.user_id=%s)""", (lid, user_id))
    with db.get_conn() as conn, conn.cursor() as cur:
        [c] = cadeias_do_banco(cur, user_id)
        conn.rollback()
    assert c["launches"] == {lid}


def test_cadeia_projetada_pelo_caminho_real_nao_entra_na_estimativa(user_id):
    """B4: `ler(bancos=True)` entrega a cadeia ALUGUEL IMOB à estimativa, que a deixa de fora."""
    from _apoio_assinaturas import conta as conta_of, mensais, rp, semeia
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    txs = mensais("al", ["-1500"] * 3, ultima=HOJE - timedelta(days=10), desc="ALUGUEL IMOB")
    semeia(user_id, [conta_of("acc-al", txs)], [rp("ALUGUEL IMOB", -1500, txs)])
    for i, t in enumerate(txs):
        lid = gasto(user_id, 1500, (HOJE - t["transaction_date"]).days, categoria="moradia", alvo="ALUGUEL IMOB",
                    source="open_finance", posted_at=t["transaction_date"])
        q("update open_finance_transactions set imported_launch_id=%s where provider_transaction_id=%s",
          (lid, t["provider_transaction_id"]))
    s = ler_em(user_id, bancos=True)
    assert mensal(s) == {"mercado": D("300.00")}
    assert any(o.fonte != FONTE_VARIAVEL and "aluguel" in o.nome.lower() for o in s.ocorrencias)


# ── premissa pelo histórico usado ────────────────────────────────────────────

@pytest.mark.parametrize("atras,trecho", [
    (10, "gasto dos últimos 10 dias levado a um mês"),
    (45, "mediana mensal por categoria dos últimos 30 dias"),
    (100, "mediana mensal por categoria dos últimos 90 dias"),
])
def test_premissa_diz_o_historico_que_entrou(user_id, atras, trecho):
    gasto(user_id, 1, atras, categoria="salario", tipo="receita")
    for a in (5, 40, 70)[:1 if atras < 100 else 3]:
        gasto(user_id, 70, a)
    p = " ".join(ler_em(user_id).premissas)
    assert trecho in p
    assert p.count("Inclui estimativa") == 1


# ── base: bancos e contas ────────────────────────────────────────────────────

def banco(uid, status="UPDATED"):
    cid = conexao(uid, f"item-{uid}", status=status)
    aid = conta(cid, f"acc-{uid}", 0)
    ids = []
    for i in range(3):
        lid = gasto(uid, 400, 10 + 30 * i, categoria="pix", source="open_finance")
        tx_banco(aid, f"t{uid}-{i}", -400, imported_launch_id=lid)
        ids.append(lid)
    return ids


def test_lancamento_do_banco_so_com_a_conta_na_base_e_a_carteira_fica_sempre(user_id):
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    banco(user_id)
    assert mensal(ler_em(user_id, bancos=True)) == {"mercado": D("300.00"), "pix": D("400.00")}
    assert mensal(ler_em(user_id, bancos=False)) == {"mercado": D("300.00")}  # bancos fora da base


def test_conta_pausada_fica_fora(user_id):
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    banco(user_id)
    q("update open_finance_connections set status='PAUSED' where user_id=%s", (user_id,))
    assert mensal(ler_em(user_id, bancos=True)) == {"mercado": D("300.00")}


# ── isolamento ───────────────────────────────────────────────────────────────

def test_gasto_e_marcas_forjadas_de_outro_usuario_nao_entram_nem_tiram(user_id):
    from db.bills import create_boleto
    from db.recurring import create_recurring_expense
    outro = usuario_pagante()
    historico(user_id)
    tres_janelas(user_id, (300, 300, 300))
    historico(outro)
    tres_janelas(outro, (9000, 9000, 9000), categoria="so_do_outro")
    # o B aponta para as linhas do A: nem `bill_instances` nem `recurring_charges` do B tiram o gasto do A
    alvos = [r["id"] for r in qp("select id from launches where user_id=%s and categoria='mercado'", (user_id,), fetch=True)]
    r = create_recurring_expense(outro, "Forjado", 1, "outros", 5, "account", start_date=HOJE - timedelta(days=100))
    for i, alvo in enumerate(alvos):
        b = create_boleto(outro, f"Forjado{i}", 1, HOJE + timedelta(days=1))
        q("update bill_instances set launch_id=%s where id=%s and user_id=%s", (alvo, b["id"], outro))
        q("insert into recurring_charges (recurring_id, user_id, launch_id, amount, ym) values (%s,%s,%s,1,%s)",
          (r["id"], outro, alvo, f"2026-0{i + 1}"))
    assert len(alvos) == 3
    ids_cartao = cartao(user_id, [(200, a, 10, {"categoria": "lazer"}) for a in (5, 35, 65)])
    for i, tid in enumerate(ids_cartao):  # o mesmo vale para o legado do cartão
        q("insert into recurring_charges (recurring_id, user_id, credit_tx_id, amount, ym) values (%s,%s,%s,1,%s)",
          (r["id"], outro, tid, f"2026-0{i + 4}"))
    assert mensal(ler_em(user_id)) == {"mercado": D("300.00"), "lazer": D("200.00")}
    assert mensal(ler_em(outro)) == {"so_do_outro": D("9000.00")}


# ── trajetória, risco, simulador ─────────────────────────────────────────────

def snap(saldo, mensal_, ocs=()):
    s = Snapshot(HOJE, AGORA, {"saldo": D(saldo), "balance_source": "manual", "of_bank_count": 0,
                               "banks_excluded": False, "motivos": []})
    s.ocorrencias = [*ocs, *ocorrencias(HOJE, HOJE + timedelta(days=90), D(mensal_))]
    s.estimativa_variavel = {"despesa_mensal": D(mensal_)}
    return s


def test_trajetoria_degrau_diario_sem_virar_compromisso_e_pior_dia_explica():
    s = snap(5000, 3000)
    t = _trajectory(HOJE, s.base, s.ocorrencias, 90, 0)
    assert t["trajectory"][0]["saldo_projetado"] == D("4900.00")  # 5000 − 3000/30
    assert t["trajectory"][29]["saldo_projetado"] == D("2000.00")  # um mensal em 30 dias
    assert all(p["compromissos"] == [] for p in t["trajectory"]) and t["worst_day"]["causas"] == []
    assert t["worst_day"]["despesa_variavel"] == D("9000.00")  # 90 dias × 100
    assert t["worst_day"]["date"] == (HOJE + timedelta(days=90)).isoformat()


def test_sem_estimativa_o_pior_dia_nao_tem_despesa_variavel():
    s = snap(5000, 0)
    assert _trajectory(HOJE, s.base, s.ocorrencias, 90, 0)["worst_day"]["despesa_variavel"] is None


def test_risco_se_abstem_quando_so_a_estimativa_deixa_negativo():
    assert _risco(snap(100, 3000), HOJE + timedelta(days=30)) == "abster"
    # controle positivo: um fixo real não motivado que estoura o saldo continua dando risco
    from core.services.cashflow_contract import Ocorrencia
    fixo_ = Ocorrencia("gasto_recorrente", 1, "c", HOJE + timedelta(days=3), "gasto_fixo", "Aluguel", D(500), "saida")
    assert _risco(snap(100, 3000, [fixo_]), HOJE + timedelta(days=30)) == "risco"


def test_simulador_delta_vs_atual_e_igual_com_e_sem_estimativa():
    sim = Simulacao.model_validate({"cenarios": [{"nome": "x", "preco": 1000}]})
    com = simular(snap(5000, 3000), sim)
    sem = simular(snap(5000, 0), sim)
    assert com["cenarios"][0]["resumo"]["delta_vs_atual"] == sem["cenarios"][0]["resumo"]["delta_vs_atual"]
    assert com["atual"]["saldo_final_90"] == sem["atual"]["saldo_final_90"] - D("9000.00")


def test_todo_tier_com_previsao_ve_pelo_menos_90_dias_de_historico():
    """`estimar` não aplica o corte do plano (`history_earliest_date` abre outra conexão): vale porque nenhum tier a perde."""
    from core.services.plan_limits import TIER_ORDER, limits_for_tier
    from core.services.plan_service import FEATURE_MIN_TIER_V2
    for tier, ordem in TIER_ORDER.items():
        if ordem >= TIER_ORDER[FEATURE_MIN_TIER_V2["forecast"]]:
            dias = limits_for_tier(tier)["history_days"]
            assert dias is None or dias >= 90, tier


# ── contrato da API ──────────────────────────────────────────────────────────

@pytest.fixture
def pro(monkeypatch):
    u = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    return u


def test_api_devolve_a_estimativa_so_como_campo_novo_e_nunca_em_lista(pro):
    from utils_date import now_tz
    hoje = now_tz().date()
    historico(pro, hoje=hoje)
    tres_janelas(pro, (300, 300, 300), hoje=hoje)
    q("update accounts set balance=10000 where user_id=%s", (pro,))
    corpo = Previsao.model_validate(previsao_v2.ler_previsao(pro, 90, (30, 60, 90), True)).model_dump(mode="json")
    e = corpo["estimativa_variavel"]
    assert e["despesa_mensal"] == "300.00" and e["despesa_no_horizonte"] == "900.00"
    assert e["categorias"] == [{"chave": "mercado", "nome": "mercado", "despesa_mensal": "300.00"}]
    assert corpo["cobertura"]["inclui_estimativa_variavel"] is True
    assert corpo["cabe_nas_premissas"] is False
    assert all(FONTE_VARIAVEL not in str(p) for p in corpo["trajetoria"])
    assert all(g["fonte"] != FONTE_VARIAVEL for g in corpo["compromissos"])
    assert corpo["pior_dia"]["despesa_variavel"] == "900.00" and corpo["pior_dia"]["causas"] == []
    assert corpo["marcos"][0]["saldo"] == "9700.00"


def test_api_sem_historico_devolve_null_e_mantem_o_motivo(pro):
    corpo = Previsao.model_validate(previsao_v2.ler_previsao(pro, 30, (30,), False)).model_dump(mode="json")
    assert corpo["estimativa_variavel"] is None
    assert "gastos_variaveis_nao_estimados" in [m["codigo"] for m in corpo["motivos"]]
    assert corpo["cobertura"]["inclui_estimativa_variavel"] is False


def test_ai_guard_reconhece_as_chaves_despesa():
    from core.services.ai_guard import _CHAVE_MONETARIA_RE
    for chave in ("despesa_mensal", "despesa_no_horizonte", "despesa_variavel", "despesa_variavel_estimada"):
        assert _CHAVE_MONETARIA_RE.search(chave), chave


def test_rotas_legadas_trazem_os_numeros_novos_sem_listar_a_estimativa(pro_small_uid):
    from core.services.cashflow import project
    from core.services.cashflow_forecast import forecast_with_trajectory
    uid, hoje = pro_small_uid, date.today()
    historico(uid, hoje=hoje)
    tres_janelas(uid, (100, 100, 100), hoje=hoje)
    fc = forecast_with_trajectory(uid, 90)
    assert fc["horizons"]["30"]["despesa_variavel_estimada"] == 100.0
    assert all(c["fonte"] != FONTE_VARIAVEL for c in fc["compromissos"])
    assert all(c["compromissos"] == [] for c in fc["trajectory"])
    assert "Inclui a estimativa" in fc["premises"] and fc["estimativa_variavel"]["despesa_mensal"] == 100.0
    p = project(uid, hoje + timedelta(days=30))
    assert p["despesa_variavel_estimada"] == 100.0 and p["cobertura"]["inclui_estimativa_variavel"] is True
    assert p["orientacao"] == "abster"


# ── WhatsApp, pela conversa inteira ──────────────────────────────────────────

@pytest.mark.parametrize("pergunta", ["previsão de saldo de 30 dias",
                                      f"tô tranquilo até {(date.today() + timedelta(days=10)).strftime('%d/%m')}?"])
def test_whatsapp_previsao_diz_quanto_desconta_depois_de_um_gasto(pro_small_uid, pergunta):
    from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401
    uid, hoje = pro_small_uid, date.today()
    historico(uid, hoje=hoje)
    tres_janelas(uid, (100, 100, 100), hoje=hoje)
    assert "regist" in manda(uid, "gastei 50 no mercado").lower()  # de hoje: não muda a estimativa
    resposta = manda(uid, pergunta)
    assert "Saldo previsto condicional" in resposta
    assert resposta.endswith("Já desconta cerca de R$ 100,00 por mês de gastos do dia a dia (estimativa).")


def test_whatsapp_sem_historico_nao_inventa_a_linha(pro_small_uid):
    from tests._fusao_of_helpers import manda
    assert "Já desconta" not in manda(pro_small_uid, "previsão de saldo de 30 dias")


# ── desempenho ───────────────────────────────────────────────────────────────

def test_desempenho_do_ler_com_3000_linhas_em_150_dias(user_id, monkeypatch):
    q("""insert into launches (user_id, tipo, valor, criado_em, categoria, alvo, source)
         select %s, 'despesa', 10 + (g %% 50), %s::timestamptz - (g %% 150) * interval '1 day' - interval '12 hours',
                'cat' || (g %% 12), 'loja ' || g, 'manual' from generate_series(1, 3000) g""", (user_id, AGORA))
    t = _time.perf_counter(); ler_em(user_id); com = _time.perf_counter() - t
    monkeypatch.setattr(pv, "estimar", lambda *a, **k: None)
    t = _time.perf_counter(); ler_em(user_id); sem = _time.perf_counter() - t
    print(f"\nler(): sem estimativa {sem * 1000:.0f} ms, com {com * 1000:.0f} ms")
    assert com - sem < 0.5
