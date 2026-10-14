"""O ritmo das metas (`db/metas_ritmo.py`, `GET /api/v2/metas`): quanto a fonte cresce por mês, nas
fotos por posição do Open Finance. Postgres real, pelo monólito, relógio congelado (`hoje`).

Controles por mutação (relato do PR; cada um deixa o grupo vermelho e a versão certa verde):
janela de 90 dias (`RITMO_DIAS_JANELA` = 365) → `test_janela_*`; piso de 7 dias (`RITMO_DIAS_MINIMOS` = 0)
→ `test_piso_*`; exigir todas as conexões em D0 (`len(conexoes)` → 1) → `test_serie_quebrada_*`;
`collection_confirmed` fora das duas SQL → `test_coleta_nao_confirmada_*`; conexão pausada dentro da
composição (`if True` em `conexoes_por_banco`) → `test_pausada_*`; `left join` → `join` →
`test_posicao_sem_espelho_*`; quantizar sem `q_posicao` → `test_mesma_regra_*`; tirar
`i.connection_id = s.connection_id` do join → `test_espelho_de_outro_usuario_*`; tirar `c.user_id` →
`test_valor_em_de_conexao_alheia_*`; tirar `ritmo <= 0` de `chega_em` → `test_chegada_*`;
`c.id desc` → `asc` no `ganha` e tirar o recorte `g.connection_id = any` → `test_vencedora_*`; janela com
`+ 1` dia → `test_janela_91_*`; `<` → `<=` em `observed_on` → `test_sem_foto_no_periodo_*`; `bool_and` fora
de `_DIA_BASE_SQL` → `test_dia_misto_*`; `collection_confirmed` só de `_FOTOS_SQL` → `test_valor_em_*`;
`c.user_id` fora do `ganha` → `test_espelho_mais_novo_de_outro_usuario_*`; `c.id = any` fora do "pertence"
→ `test_foto_de_pid_sem_espelho_*`; `filter` fora do `count` de `_DIA_BASE_SQL` → `test_dia_so_com_a_foto_*`;
`ROUND_HALF_UP` → `HALF_EVEN` → `test_empate_*`; `try/except` do ritmo → `test_ritmo_fora_de_28_digitos_*`;
atalho do `percentual` → `test_percentual_com_saldo_enorme_*`; o "pertence" no `bool_and` → `test_dia_com_foto_*`;
`historico_incompleto` → `historico_curto` (ou sem `_JANELA_SQL` na existência) → `test_historico_incompleto_*`;
existência = "qualquer foto" (sem o `having` do dia comum) → `test_conexao_nova_*`, `test_identidade_que_migrou_*`,
`test_foto_so_de_outro_usuario_*`; `and s.collection_confirmed` na existência → `test_so_ha_fotos_nao_confirmadas_*`;
`* 30` → `* 31` em `chega_em` → `test_chegada_mes_de_30_dias_*`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from db import investido, metas as dbmetas, metas_ritmo
from db.connection import get_conn
from tests._patrimonio_helpers import AGORA, posicao, q
from tests.test_api_v2_metas import (RESERVA, banco, cria, conta_do_banco, dois, fonte_conta, ler, libera,  # noqa: F401
                                     meta, uid)
from utils_date import today_tz


@pytest.fixture
def hoje(monkeypatch):
    h = today_tz()
    monkeypatch.setattr(dbmetas, "_hoje", lambda: h)
    return h


def foto(cid, pid, dia, saldo, confirmada=True, status=None):
    q("""insert into open_finance_investment_snapshots (connection_id, provider_investment_id,
             observed_on, observed_at, collection_confirmed, balance, status)
         values (%s, %s, %s, %s, %s, %s, %s)""", (cid, pid, dia, AGORA, confirmada, saldo, status))


def ritmo(quem, mid) -> dict:
    return meta(quem, mid)["ritmo"]


def simples(uid, hoje, dias, antes="1000", agora="1300"):
    cid = banco(uid)
    posicao(cid, "inv-1", agora)
    foto(cid, "inv-1", hoje - timedelta(days=dias), antes)
    return cid


# ── janela e piso ───────────────────────────────────────────────────────────

def test_parcial_com_30_dias_tem_numero_e_avisa(uid, hoje):
    simples(uid, hoje, 30)
    m = meta(uid, cria(uid))
    assert m["ritmo"] == {"valor": "300.00", "dias_de_historico": 30, "parcial": True, "motivos": []}


def test_janela_corta_em_90_dias_e_deixa_de_ser_parcial(uid, hoje):
    cid = simples(uid, hoje, 90, antes="1000")
    foto(cid, "inv-1", hoje - timedelta(days=95), "500")  # fora da janela: não pode ser a base
    r = ritmo(uid, cria(uid))
    assert r == {"valor": "100.00", "dias_de_historico": 90, "parcial": False, "motivos": []}


def test_piso_com_3_dias_e_null_e_com_7_vira_numero(uid, hoje):
    cid = simples(uid, hoje, 3)
    mid = cria(uid)
    r = ritmo(uid, mid)
    assert (r["valor"], r["dias_de_historico"], r["parcial"], r["motivos"]) == (None, 3, False, ["historico_curto"])
    foto(cid, "inv-1", hoje - timedelta(days=7), "1000")
    # a foto de 3 dias fica; D0 é a MAIS ANTIGA da janela, 7 dias
    q("delete from open_finance_investment_snapshots where observed_on = %s", (hoje - timedelta(days=3),))
    r = ritmo(uid, mid)
    assert (r["valor"], r["dias_de_historico"]) == ("1285.71", 7)


def test_sem_foto_no_periodo_ou_so_a_de_hoje_e_null(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "1300")
    mid = cria(uid)
    assert ritmo(uid, mid) == {"valor": None, "dias_de_historico": None, "parcial": False,
                               "motivos": ["historico_curto"]}
    foto(cid, "inv-1", hoje, "1200")  # hoje não é base: sem dia de base, não dias = 0
    assert ritmo(uid, mid) == {"valor": None, "dias_de_historico": None, "parcial": False,
                               "motivos": ["historico_curto"]}


def test_historico_incompleto_quando_ha_foto_mas_nenhum_dia_completo_e_confirmado(uid, hoje):
    a, b = banco(uid, "Nubank"), banco(uid, "Inter")
    posicao(a, "a-1", "1000")
    posicao(b, "b-1", "310")
    for dia in (80, 40, 10):  # 70 dias de fotos; as do Inter nunca confirmam (INVESTMENTS parado na Pluggy)
        foto(a, "a-1", hoje - timedelta(days=dia), "900")
        foto(b, "b-1", hoje - timedelta(days=dia), "300", confirmada=False)
    m = meta(uid, cria(uid))
    assert D(m["atual"]) == 1310
    assert m["ritmo"] == {"valor": None, "dias_de_historico": None, "parcial": False,
                          "motivos": ["historico_incompleto"]}
    # positivo: o mesmo caso sem a conexão que nunca confirma é número; o banco que não tem foto
    # nenhuma na janela (só uma de 91 dias) segue `historico_curto`: falta tempo, não completude
    nubank = ritmo(uid, cria(uid, nome="n", fonte={"tipo": "banco", "banco": "Nubank"}))
    assert (nubank["valor"], nubank["dias_de_historico"], nubank["motivos"]) == ("37.50", 80, [])
    c = banco(uid, "C6")
    posicao(c, "c-1", "50")
    foto(c, "c-1", hoje - timedelta(days=91), "40")
    r = ritmo(uid, cria(uid, nome="c6", fonte={"tipo": "banco", "banco": "C6"}))
    assert (r["valor"], r["motivos"]) == (None, ["historico_curto"])


def _nubank_e_inter(uid, hoje):
    a, b = banco(uid, "Nubank"), banco(uid, "Inter")
    posicao(a, "a-1", "1000")
    posicao(b, "b-1", "310")
    for dia in (80, 40, 10):
        foto(a, "a-1", hoje - timedelta(days=dia), "900")
    return a, b


def _curto(uid):
    r = ritmo(uid, cria(uid))
    return (r["valor"], r["dias_de_historico"], r["motivos"])


def test_conexao_nova_sem_foto_e_historico_curto_e_nao_incompleto(uid, hoje):
    _, b = _nubank_e_inter(uid, hoje)  # Inter recém-conectado: sem foto na janela, o tempo resolve
    mid = cria(uid)
    assert ritmo(uid, mid)["motivos"] == ["historico_curto"]
    foto(b, "b-1", hoje, "310")  # só a de hoje (fora da janela): o dia da conexão não vira "incompleto"
    assert ritmo(uid, mid)["motivos"] == ["historico_curto"]
    for dia in (80, 40, 10):  # positivo: o Inter confirma nos mesmos dias, a série é número
        foto(b, "b-1", hoje - timedelta(days=dia), "300")
    r = ritmo(uid, mid)
    assert (r["valor"], r["dias_de_historico"], r["motivos"]) == ("41.25", 80, [])  # (100 + 10) ÷ 80 × 30


def test_conexao_nova_com_foto_nao_confirmada_so_em_dia_que_o_nubank_nao_tem_e_historico_curto(uid, hoje):
    _, b = _nubank_e_inter(uid, hoje)
    foto(b, "b-1", hoje - timedelta(days=2), "310", confirmada=False)  # sem dia comum com o Nubank
    assert _curto(uid) == (None, None, ["historico_curto"])


def test_identidade_que_migrou_para_conexao_nova_sem_foto_e_historico_curto(uid, hoje):
    a, c = banco(uid), banco(uid)
    posicao(a, "p1", "500")
    posicao(a, "p2", "300")
    posicao(c, "p1", "900")  # p1 migrou de a para c; a segue na fonte pela p2
    for p in ("p1", "p2"):
        foto(a, p, hoje - timedelta(days=30), "400")  # c ainda não tem foto nenhuma
    assert _curto(uid) == (None, None, ["historico_curto"])


def test_so_ha_fotos_nao_confirmadas_em_dia_comum_e_historico_incompleto(uid, hoje):
    cid = banco(uid)
    posicao(cid, "p1", "1300")
    for dia in (40, 10):
        foto(cid, "p1", hoje - timedelta(days=dia), "1000", confirmada=False)
    assert _curto(uid) == (None, None, ["historico_incompleto"])


def test_foto_so_de_outro_usuario_na_janela_e_historico_curto(dois, hoje):
    a, b = dois
    ca, cb = banco(a), banco(b)
    posicao(ca, "inv-1", "1300")
    posicao(cb, "inv-1", "50")
    foto(cb, "inv-1", hoje - timedelta(days=30), "40")  # mesmo pid, outro dono
    assert _curto(a) == (None, None, ["historico_curto"])
    assert ritmo(b, cria(b))["valor"] is not None  # positivo: a foto vale para o dono


def test_fonte_conta_nao_tem_historico(uid, hoje):
    conta_do_banco(uid, "acc-1", "300")
    m = meta(uid, cria(uid, fonte=fonte_conta(uid)))
    assert m["ritmo"] == {"valor": None, "dias_de_historico": None, "parcial": False,
                          "motivos": ["sem_historico_da_fonte"]}


# ── série quebrada ──────────────────────────────────────────────────────────

def test_serie_quebrada_conexao_nova_recomeca_a_serie(uid, hoje):
    a, b = banco(uid, "Nubank"), banco(uid, "Inter")
    posicao(a, "a-1", "1000")
    posicao(b, "b-1", "500")
    foto(a, "a-1", hoje - timedelta(days=60), "800")
    foto(a, "a-1", hoje - timedelta(days=10), "850")
    foto(b, "b-1", hoje - timedelta(days=10), "400")
    tudo = ritmo(uid, cria(uid))
    # D0 = a primeira foto de B: (1500 - 1250) / 10 * 30; sem a regra o saldo inteiro de B virava ritmo
    assert (tudo["valor"], tudo["dias_de_historico"]) == ("750.00", 10)
    nubank = ritmo(uid, cria(uid, nome="n", fonte={"tipo": "banco", "banco": "Nubank"}))
    assert (nubank["valor"], nubank["dias_de_historico"]) == ("100.00", 60)  # B não compõe a fonte Nubank


def test_posicao_nova_na_mesma_conexao_e_crescimento_e_nao_quebra(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "500")
    posicao(cid, "inv-2", "300")  # o aporte virou posição nova, sem foto em D0
    foto(cid, "inv-1", hoje - timedelta(days=20), "500")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("450.00", 20)


def test_conexao_apagada_leva_as_fotos_e_a_serie_recomeca(uid, hoje):
    a, b = banco(uid), banco(uid)
    posicao(a, "a-1", "1000")
    posicao(b, "b-1", "500")
    for c, p in ((a, "a-1"), (b, "b-1")):
        foto(c, p, hoje - timedelta(days=40), "400")
    mid = cria(uid)
    assert ritmo(uid, mid)["dias_de_historico"] == 40
    q("delete from open_finance_connections where id = %s", (b,))
    foto(a, "a-1", hoje - timedelta(days=15), "900")
    assert (ritmo(uid, mid)["dias_de_historico"]) == 40  # só A compõe: a foto de 40 dias de A segue valendo


# ── filtros ─────────────────────────────────────────────────────────────────

def test_coleta_nao_confirmada_em_d0_e_ignorada(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "1300")
    foto(cid, "inv-1", hoje - timedelta(days=40), "100", confirmada=False)
    foto(cid, "inv-1", hoje - timedelta(days=20), "1000")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("450.00", 20)


def test_pausada_fica_fora_das_duas_pontas(uid, hoje):
    a, p = banco(uid), banco(uid, status="PAUSED")
    posicao(a, "a-1", "1300")
    posicao(p, "p-1", "9000")
    foto(a, "a-1", hoje - timedelta(days=20), "1000")
    foto(p, "p-1", hoje - timedelta(days=50), "9000")
    m = meta(uid, cria(uid))
    assert (m["ritmo"]["valor"], m["ritmo"]["dias_de_historico"]) == ("450.00", 20)
    assert "conexao_pausada" in m["ritmo"]["motivos"] and D(m["atual"]) == 1300  # os motivos da fonte acompanham


def test_posicao_sem_espelho_entra_como_real_com_moeda_presumida(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "1000")
    foto(cid, "inv-1", hoje - timedelta(days=30), "800")
    foto(cid, "inv-gone", hoje - timedelta(days=30), "200")  # resgatada e podada do espelho
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"], r["motivos"]) == ("0.00", 30, ["moeda_presumida"])


def test_saldo_nao_finito_na_foto_nao_vira_zero_com_cara_de_exato(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "1300")
    foto(cid, "inv-1", hoje - timedelta(days=30), D("NaN"))
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"], r["motivos"]) == (None, 30, ["saldo_ausente"])
    foto(cid, "inv-2", hoje - timedelta(days=30), "200")  # com outra posição boa há número, e a dúvida
    r = ritmo(uid, cria(uid, nome="x"))
    assert (r["valor"], r["motivos"]) == ("1100.00", ["saldo_ausente", "moeda_presumida"])  # inv-2 sem espelho


# ── mesma regra do /investido ───────────────────────────────────────────────

def test_mesma_regra_soma_historica_igual_ao_investido_no_total_e_por_banco(uid, hoje):
    dia = hoje - timedelta(days=5)
    a, b = banco(uid, "Nubank"), banco(uid, "Inter")
    for cid, pid, saldo, kw in ((a, "a-1", "100.006", {}), (a, "a-2", "50.004", {}),
                                (a, "a-usd", "700", {"moeda": "USD", "code": "USD"}),
                                (a, "a-zero", "90", {"status": "TOTAL_WITHDRAWAL"}),
                                (b, "b-1", "33.335", {}), (b, "b-2", "0.014", {})):
        posicao(cid, pid, saldo, **kw)
        foto(cid, pid, dia, saldo, status=kw.get("status"))
    with get_conn() as conn, conn.cursor() as cur:
        inv = investido.calcular(cur, uid, com_zero=True)
        total, _ = metas_ritmo.valor_em(cur, uid, {a, b}, dia)
        por_banco = {k: metas_ritmo.valor_em(cur, uid, c, dia)[0] for k, c in inv["conexoes_por_banco"].items()}
        conn.rollback()
    assert total == inv["total"] and total != 0
    assert por_banco == {p["banco"]: p["valor"] for p in inv["por_banco"]}


def test_valor_em_de_conexao_alheia_e_zero(dois, hoje):
    a, b = dois
    cb = banco(b)
    foto(cb, "b-1", hoje - timedelta(days=5), "777")
    with get_conn() as conn, conn.cursor() as cur:
        assert metas_ritmo.valor_em(cur, a, {cb}, hoje - timedelta(days=5)) == (D("0.00"), set())
        assert metas_ritmo.valor_em(cur, b, {cb}, hoje - timedelta(days=5))[0] == D("777.00")  # positivo
        conn.rollback()


def test_espelho_de_outro_usuario_com_o_mesmo_provider_id_nao_muda_a_moeda(dois, hoje):
    a, b = dois
    cb = banco(b)
    posicao(cb, "inv-1", "50", moeda="USD", code="USD")  # o MESMO provider_investment_id, em dólar
    ca = banco(a)
    posicao(ca, "inv-1", "1300")
    foto(ca, "inv-1", hoje - timedelta(days=30), "1000")
    foto(cb, "inv-1", hoje - timedelta(days=30), "40")
    r = ritmo(a, cria(a))
    assert (r["valor"], r["dias_de_historico"]) == ("300.00", 30)
    assert ritmo(b, cria(b))["valor"] is None  # o dólar de B segue fora do real dele


# ── chegada ─────────────────────────────────────────────────────────────────

def test_chegada_no_ritmo_de_hoje_e_null_sem_ritmo_positivo(uid, monkeypatch):
    hoje = date(2026, 1, 1)  # data fixa: o resultado não depende do dia em que a suíte roda
    monkeypatch.setattr(dbmetas, "_hoje", lambda: hoje)
    cid = banco(uid)
    posicao(cid, "inv-1", "1300")
    foto(cid, "inv-1", hoje - timedelta(days=30), "1000")  # ritmo 300/mês
    m = meta(uid, cria(uid, alvo="2000"))
    assert m["chega_em"] == (hoje + timedelta(days=70)).strftime("%Y-%m")  # 700 ÷ 300 × 30 dias
    assert meta(uid, cria(uid, nome="batida", alvo="1000"))["chega_em"] is None  # falta 0
    q("delete from open_finance_investment_snapshots where connection_id = %s", (cid,))
    foto(cid, "inv-1", hoje - timedelta(days=30), "1600")  # ritmo -300/mês
    m = meta(uid, m["id"])
    assert (m["ritmo"]["valor"], m["chega_em"]) == ("-300.00", None)
    q("delete from open_finance_investment_snapshots where connection_id = %s", (cid,))
    foto(cid, "inv-1", hoje - timedelta(days=30), "1300")  # ritmo 0
    assert meta(uid, m["id"])["chega_em"] is None


def test_chegada_reserva_sem_custo_e_sem_alvo_e_ritmo_infimo_nao_estoura(uid, hoje):
    cid = banco(uid)
    posicao(cid, "inv-1", "1300")
    foto(cid, "inv-1", hoje - timedelta(days=30), "1000")
    m = meta(uid, cria(uid, **RESERVA))
    assert m["alvo"] is None and m["ritmo"]["valor"] == "300.00" and m["chega_em"] is None
    assert metas_ritmo.chega_em(D("1000000000000"), D("0.01"), hoje) is None  # depois do ano 9999


# ── a mesma composição nas duas pontas, mesmo com a identidade repetida entre conexões ──────────────

def _plano(uid, hoje, a, p1="500"):
    posicao(a, "p1", p1)
    posicao(a, "p2", "300")
    for dia in (30, 10):  # saldo plano: o ritmo certo é 0
        foto(a, "p1", hoje - timedelta(days=dia), p1)
        foto(a, "p2", hoje - timedelta(days=dia), "300")


def test_vencedora_pausada_com_a_mesma_identidade_nao_conta_no_passado(uid, hoje):
    a = banco(uid)
    _plano(uid, hoje, a)
    posicao(banco(uid, status="PAUSED"), "p1", "900")  # mais nova: hoje p1 sai do total (vence a pausada)
    m = meta(uid, cria(uid))
    assert D(m["atual"]) == 300 and m["ritmo"]["valor"] == "0.00", m  # sem o recorte: -500.00


def test_vencedora_de_outro_banco_com_a_mesma_identidade_nao_conta_no_passado(uid, hoje):
    a = banco(uid, "Nubank")
    _plano(uid, hoje, a)
    posicao(banco(uid, "Inter"), "p1", "900")  # p1 hoje é do Inter
    m = meta(uid, cria(uid, fonte={"tipo": "banco", "banco": "Nubank"}))
    assert D(m["atual"]) == 300 and m["ritmo"]["valor"] == "0.00", m


def test_vencedora_dentro_da_fonte_conta_uma_vez_e_a_mais_nova_ganha(uid, hoje):
    a, c = banco(uid), banco(uid)  # as duas vivas; c é mais nova e vence p1 hoje; a só perde (não compõe a fonte)
    posicao(a, "p1", "500")
    posicao(c, "p1", "900")
    foto(a, "p1", hoje - timedelta(days=30), "500")
    foto(c, "p1", hoje - timedelta(days=30), "800")
    m = meta(uid, cria(uid))
    # a foto de a pertence à fonte (a vencedora é c): duas fotos de p1 em D0, vale a da conexão mais nova
    assert D(m["atual"]) == 900 and m["ritmo"]["valor"] == "100.00", m  # 900 - 800; nem 900 - 500 nem 900 - 1300


def test_perdedora_que_tambem_compoe_a_fonte_nao_soma_a_foto_dela_de_novo(uid, hoje):
    a, c = banco(uid), banco(uid)
    posicao(a, "p1", "500")  # perde p1 para c, mas p2 a mantém na fonte
    posicao(a, "p2", "100")
    posicao(c, "p1", "900")
    for cid, pid, saldo in ((a, "p1", "500"), (a, "p2", "100"), (c, "p1", "800")):
        foto(cid, pid, hoje - timedelta(days=30), saldo)
    m = meta(uid, cria(uid))
    assert D(m["atual"]) == 1000 and m["ritmo"]["valor"] == "100.00", m  # 1000 - (800 + 100)


def test_conexao_que_so_perde_identidades_empresta_a_foto_do_passado(uid, hoje):
    # p1 mudou de conexão (a → c). Hoje só c compõe a fonte; em D0 p1 estava em a e p2 já estava em c.
    a, c = banco(uid), banco(uid)
    posicao(a, "p1", "500")
    posicao(c, "p1", "900")
    posicao(c, "p2", "100")
    foto(a, "p1", hoje - timedelta(days=30), "400")
    foto(c, "p2", hoje - timedelta(days=30), "100")
    foto(a, "p1", hoje - timedelta(days=5), "450")
    m = meta(uid, cria(uid))
    assert (m["ritmo"]["valor"], m["ritmo"]["dias_de_historico"]) == ("500.00", 30), m  # 1000 - (400 + 100)


def test_dia_so_com_a_foto_emprestada_nao_serve_de_base_sem_foto_da_conexao_da_fonte(uid, hoje):
    a, c = banco(uid), banco(uid)
    posicao(a, "p1", "500")
    posicao(c, "p1", "900")
    posicao(c, "p2", "100")
    foto(a, "p1", hoje - timedelta(days=30), "400")  # c não tem foto nenhuma neste dia
    foto(c, "p2", hoje - timedelta(days=10), "100")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("2700.00", 10), r  # D0 = 1ª foto de c: (1000 - 100) ÷ 10 × 30


def test_espelho_mais_novo_de_outro_usuario_com_o_mesmo_pid_nao_tira_a_foto_de_a(dois, hoje):
    a, b = dois
    ca = banco(a)
    posicao(ca, "inv-1", "1300")
    foto(ca, "inv-1", hoje - timedelta(days=30), "1000")
    posicao(banco(b), "inv-1", "50")  # connection_id MAIOR que o de a
    r = ritmo(a, cria(a))
    assert (r["valor"], r["dias_de_historico"]) == ("300.00", 30), r


def test_foto_de_pid_sem_espelho_so_conta_em_conexao_da_fonte(uid, hoje):
    a, o = banco(uid, "Nubank"), banco(uid, "Inter")
    posicao(a, "p1", "1300")
    posicao(o, "q1", "10")
    foto(a, "p1", hoje - timedelta(days=30), "1000")
    foto(o, "q1", hoje - timedelta(days=30), "10")
    foto(banco(uid, status="PAUSED"), "gone", hoje - timedelta(days=30), "777")
    foto(o, "gone", hoje - timedelta(days=30), "888")
    assert ritmo(uid, cria(uid, fonte={"tipo": "banco", "banco": "Nubank"}))["valor"] == "300.00"
    tudo = ritmo(uid, cria(uid, nome="tudo"))  # em "tudo" a de Inter conta (sem espelho, BRL presumido); a pausada não
    assert (tudo["valor"], tudo["motivos"]) == ("-588.00", ["moeda_presumida"]), tudo  # 1310 - (1000 + 10 + 888)


# ── valor ruim ou dia ruim na foto não derruba nem infla ────────────────────────────────────────────

def test_ritmo_que_arredonda_para_zero_negativo_sai_0_00(uid, hoje):
    simples(uid, hoje, 90, antes="1000.01", agora="1000.00")
    assert ritmo(uid, cria(uid))["valor"] == "0.00"  # -0.0033/mês: era "-0.00"


def test_saldo_gigante_na_foto_e_ilegivel_e_nao_derruba_as_metas(uid, hoje):
    cid = simples(uid, hoje, 30)
    foto(cid, "inv-2", hoje - timedelta(days=30), "1" + "0" * 40)
    mid = cria(uid)
    r = ritmo(uid, mid)  # sem o tratamento: 500 (decimal.InvalidOperation no quantize)
    assert (r["valor"], r["dias_de_historico"], r["motivos"]) == ("300.00", 30, ["saldo_ausente", "moeda_presumida"])
    q("delete from open_finance_investment_snapshots where provider_investment_id = 'inv-1'")
    assert ritmo(uid, mid)["valor"] is None  # só a foto ilegível: soma 0 com saldo_ausente não é R$ 0


def test_dia_misto_na_conexao_nao_serve_de_base(uid, hoje):
    cid = banco(uid)
    posicao(cid, "p1", "1000")
    posicao(cid, "p2", "1000")
    foto(cid, "p1", hoje - timedelta(days=30), "1000")
    foto(cid, "p2", hoje - timedelta(days=30), "1000", confirmada=False)
    for p in ("p1", "p2"):
        foto(cid, p, hoje - timedelta(days=29), "1000")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("0.00", 29)  # a base é o dia inteiro confirmado; era 1000.00


def test_valor_em_ignora_foto_nao_confirmada(uid, hoje):
    cid = banco(uid)
    posicao(cid, "p1", "1000")
    dia = hoje - timedelta(days=5)
    foto(cid, "p1", dia, "700")
    foto(cid, "p2", dia, "300", confirmada=False)
    with get_conn() as conn, conn.cursor() as cur:
        assert metas_ritmo.valor_em(cur, uid, {cid}, dia)[0] == D("700.00")
        conn.rollback()


def test_janela_91_dias_fica_fora_e_90_entra(uid, hoje):
    cid = simples(uid, hoje, 90, antes="1000")
    foto(cid, "inv-1", hoje - timedelta(days=91), "1")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"], r["parcial"]) == ("100.00", 90, False)


def test_dia_com_foto_nao_confirmada_de_quem_nao_pertence_a_fonte_serve_de_base(uid, hoje):
    a = banco(uid)
    posicao(a, "p1", "1300")
    posicao(banco(uid, status="PAUSED"), "p9", "50")  # p9 hoje é da pausada: fora da fonte
    foto(a, "p1", hoje - timedelta(days=40), "1000")
    foto(a, "p9", hoje - timedelta(days=40), "50", confirmada=False)
    foto(banco(uid, status="PAUSED"), "gone", hoje - timedelta(days=40), "9", confirmada=False)
    foto(a, "p1", hoje - timedelta(days=10), "1200")
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("225.00", 40), r  # 1300 - 1000 em 40 dias, não 300.00 em 10


def test_dia_com_foto_nao_confirmada_de_pid_da_fonte_nao_serve_de_base(uid, hoje):
    a = banco(uid)
    posicao(a, "p1", "1300")
    posicao(a, "p2", "50")
    for dia in (40, 10):
        foto(a, "p1", hoje - timedelta(days=dia), "1000")
        foto(a, "p2", hoje - timedelta(days=dia), "50", confirmada=(dia != 40))
    r = ritmo(uid, cria(uid))
    assert (r["valor"], r["dias_de_historico"]) == ("900.00", 10), r  # 1350 - 1050; o dia misto de 40 dias daria 262.50


def test_empate_arredonda_para_cima(uid, hoje):
    simples(uid, hoje, 20, antes="1000.00", agora="1000.03")  # 0.03 ÷ 20 × 30 = 0.045
    assert ritmo(uid, cria(uid))["valor"] == "0.05"


def test_ritmo_fora_de_28_digitos_nao_derruba_a_listagem(uid, hoje):
    grande = "5" + "0" * 25  # < 1e26: a posição e a foto passam pelo `q_posicao`, o ritmo não cabe no quantize
    cid = simples(uid, hoje, 7, antes=grande, agora="1300")
    m = meta(uid, cria(uid, alvo="2000"))
    assert (m["ritmo"]["valor"], m["ritmo"]["dias_de_historico"], m["ritmo"]["motivos"]) == (None, 7, ["saldo_ausente"])
    assert (m["ritmo"]["parcial"], m["chega_em"]) == (False, None)
    q("delete from open_finance_investment_snapshots where connection_id = %s", (cid,))
    q("update open_finance_investments set balance = %s where connection_id = %s", (grande, cid))
    foto(cid, "inv-1", hoje - timedelta(days=7), "1")
    m = meta(uid, m["id"])
    assert (m["ritmo"]["valor"], m["ritmo"]["motivos"], m["chega_em"]) == (None, ["saldo_ausente"], None)


def test_percentual_com_saldo_enorme_e_alvo_infimo_nao_derruba_a_listagem(uid, hoje):
    posicao(banco(uid), "inv-1", "5" + "0" * 25)
    assert meta(uid, cria(uid, alvo="0.01"))["percentual"] == 100  # 5e27 ÷ 0.01 passa de 28 dígitos


def test_banco_que_perdeu_todas_as_identidades_tem_ritmo_null_com_motivo(uid, hoje):
    c, d = banco(uid, "Nubank"), banco(uid, "Inter")
    posicao(c, "p1", "500")
    posicao(d, "p1", "900")  # mais nova: p1 hoje é do Inter, o Nubank não compõe nada
    posicao(d, "p2", "300")
    foto(c, "p1", hoje - timedelta(days=30), "500")
    mid = cria(uid, fonte={"tipo": "banco", "banco": "Nubank"})
    r = ritmo(uid, mid)
    assert (r["valor"], r["dias_de_historico"], r["parcial"], r["motivos"]) == (None, None, False, ["historico_incompleto"]), r
    simples(uid, hoje, 30)  # positivo: o mesmo banco com conexão e posição volta a dar número
    assert ritmo(uid, mid)["valor"] == "300.00"


def test_com_zero_sem_nenhuma_leitura_traz_conexoes_por_banco_vazio(uid):
    with get_conn() as conn, conn.cursor() as cur:
        inv = investido.calcular(cur, uid, com_zero=True)
        sem = investido.calcular(cur, uid)
        conn.rollback()
    assert inv["conexoes_por_banco"] == {} and "conexoes_por_banco" not in sem


def test_chegada_mes_de_30_dias_e_nao_de_31():
    # falta 300 no ritmo de 300/mês = 30 dias: 2026-01-31 ("2026-01"); com 31 dias/mês cairia em "2026-02"
    assert metas_ritmo.chega_em(D("300"), D("300"), date(2026, 1, 1)) == "2026-01"


def test_chegada_com_divisao_nao_exata_arredonda_para_cima():
    # 1001 ÷ 300 × 30 = 100,1 dias: o teto dá 101 (2026-05-01) e o piso 100 (2026-04-30)
    assert metas_ritmo.chega_em(D("1001"), D("300"), date(2026, 1, 20)) == "2026-05"
