"""Banqueiro v2: aporte vs rendimento (A), saída (B), ritmo/meta (C),
sugestão de meta (D) e resumo consolidado (F)."""
from datetime import date

import db
from db import get_conn
from core.services.pluggy_sync import normalize_pluggy_investment
from core.services.piggy_agents import _cofre_detect_for_user


def _conn(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into open_finance_connections
                   (user_id, provider, provider_item_id, status, institution_id, institution_name)
                   values (%s,'pluggy','test-bq-item','ACTIVE','2','Pluggy Bank')
                   on conflict (user_id, provider, provider_item_id) do update set status='ACTIVE'
                   returning id""",
                (user_id,),
            )
            cid = cur.fetchone()["id"]
        conn.commit()
    return cid


def _linked_pocket(user_id, cid, raw, *, name, target=None, last_bal, last_profit):
    """Cria (ou atualiza) o investimento OF e um pocket vinculado com baselines dados."""
    db.save_open_finance_investments(cid, [normalize_pluggy_investment(raw)])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from open_finance_investments where connection_id=%s and provider_investment_id=%s",
                (cid, raw["id"]),
            )
            of_id = cur.fetchone()["id"]
            cur.execute(
                """insert into pockets(user_id,name,balance,source,of_investment_id,
                       of_last_seen_balance,of_last_seen_profit,target_amount,interest_enabled)
                   values(%s,%s,%s,'open_finance',%s,%s,%s,%s,false) returning id""",
                (user_id, name, raw["balance"], of_id, last_bal, last_profit, target),
            )
            pid = cur.fetchone()["id"]
        conn.commit()
    return of_id, pid


def _run(user_id):
    agent = db.activate_agent(user_id, "cofre")
    return _cofre_detect_for_user({"agent_id": agent["id"], "user_id": user_id}, date.today())


def _last_event(user_id):
    evs = db.list_agent_events(user_id)
    return evs[0] if evs else None


def _cx(id_, name, balance, profit):
    return {"id": id_, "name": name, "type": "FIXED_INCOME", "subtype": "CDB",
            "balance": balance, "amountProfit": profit, "currencyCode": "BRL"}


# ── A: aporte separado do rendimento ─────────────────────────────────────────
def test_aporte_separado_do_rendimento(user_id):
    cid = _conn(user_id)
    # baseline 1000/50 → agora 1300/80: saldo +300, rendimento +30, aporte líquido 270
    _linked_pocket(user_id, cid, _cx("a", "Caixinha X", 1300, 80),
                   name="Caixinha X", last_bal=1000, last_profit=50)
    fired = _run(user_id)
    assert fired == 1
    ev = _last_event(user_id)
    assert ev["payload"]["tipo"] == "aporte"
    assert abs(float(ev["valor_impacto"]) - 270.0) < 0.01
    m = ev["payload"]["moves"][0]
    assert abs(m["aporte"] - 270.0) < 0.01 and abs(m["rendimento"] - 30.0) < 0.01
    assert "270,00" in ev["payload"]["mensagem"] and "30,00" in ev["payload"]["mensagem"]


def test_rendimento_puro_nao_vira_aporte(user_id):
    cid = _conn(user_id)
    # saldo sobe só por rendimento (1000→1015, profit 50→65): aporte líquido 0 → nada
    _linked_pocket(user_id, cid, _cx("b", "Caixinha Y", 1015, 65),
                   name="Caixinha Y", last_bal=1000, last_profit=50)
    assert _run(user_id) == 0
    assert _last_event(user_id) is None


# ── B: saída ─────────────────────────────────────────────────────────────────
def test_saida_detectada(user_id):
    cid = _conn(user_id)
    _linked_pocket(user_id, cid, _cx("c", "Reserva", 700, 50),
                   name="Reserva", last_bal=1000, last_profit=50)
    assert _run(user_id) == 1
    ev = _last_event(user_id)
    assert ev["payload"]["tipo"] == "saque"
    assert "tirou" in ev["payload"]["mensagem"].lower() and "300,00" in ev["payload"]["mensagem"]


# ── C: meta com ritmo ────────────────────────────────────────────────────────
def test_meta_progresso_e_ritmo(user_id):
    cid = _conn(user_id)
    _of, pid = _linked_pocket(user_id, cid, _cx("d", "Viagem", 1600, 0),
                              name="Viagem", target=2000, last_bal=1300, last_profit=0)
    agent = db.activate_agent(user_id, "cofre")
    # histórico de aporte pra o ritmo ter base
    db.record_agent_event(agent["id"], user_id, "cofre", dedupe_key="hist",
                          payload={"moves": [{"pocket_id": pid, "name": "Viagem",
                                              "aporte": 300, "rendimento": 0, "cur_bal": 1300}]},
                          valor_impacto=300)
    _cofre_detect_for_user({"agent_id": agent["id"], "user_id": user_id}, date.today())
    ev = _last_event(user_id)
    msg = ev["payload"]["mensagem"]
    assert "% da meta" in msg and "faltam" in msg.lower()
    assert "ritmo" in msg.lower()   # ritmo aparece porque há histórico


# ── D: sem meta → sugere meta (reserva usa gasto médio) ──────────────────────
def test_sem_meta_sugere_reserva(user_id):
    cid = _conn(user_id)
    # gasto médio: 3000 em 90d → ~1000/mês → reserva ideal ~6000
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into launches(user_id,tipo,valor,is_internal_movement,criado_em) "
                "values (%s,'despesa',3000,false, now() - interval '10 days')",
                (user_id,),
            )
        conn.commit()
    _linked_pocket(user_id, cid, _cx("e", "Minha Reserva", 1200, 0),
                   name="Minha Reserva", last_bal=900, last_profit=0)
    assert _run(user_id) == 1
    msg = _last_event(user_id)["payload"]["mensagem"]
    assert "meses de gasto" in msg.lower() or "reserva ideal" in msg.lower()


# ── F: resumo consolidado quando várias caixinhas se mexem ───────────────────
def test_resumo_consolidado(user_id):
    cid = _conn(user_id)
    _linked_pocket(user_id, cid, _cx("f1", "Reserva", 1300, 0),
                   name="Reserva", last_bal=1000, last_profit=0)
    _linked_pocket(user_id, cid, _cx("f2", "Viagem", 800, 0),
                   name="Viagem", target=5000, last_bal=500, last_profit=0)
    assert _run(user_id) == 1                      # 1 evento só (resumo), não 2
    ev = _last_event(user_id)
    assert ev["payload"]["tipo"] == "resumo"
    assert len(ev["payload"]["moves"]) == 2
    assert "Resumo" in ev["payload"]["mensagem"]
    assert abs(float(ev["valor_impacto"]) - 600.0) < 0.01   # 300 + 300


# ── G: o baseline de RENDIMENTO tem de acompanhar a posição ──────────────────
# Passada 2 do Tester, sem forjar nada: `bind_pocket_to_caixinha` inicializava só
# `of_last_seen_balance`, e `_unbind_pocket` limpava só ele. O rendimento da
# posição ANTERIOR ficava valendo de régua para a posição NOVA, e o Banqueiro
# anunciava um saque que nunca existiu.
#
# CONTROLE NEGATIVO (medido, ver relato): tirar o `of_last_seen_profit` do UPDATE
# do bind faz aparecer o evento "você tirou R$995,00". CONTROLE POSITIVO:
# `test_saida_detectada` e `test_aporte_separado_do_rendimento` acima continuam
# verdes — o conserto zera a régua ao trocar de posição, não desliga o detector.

def test_trocar_de_posicao_nao_inventa_saque(user_id):
    """A meta MANUAL troca de posição direto (bind A → bind B). `_unbind_pocket`
    não roda no pocket ALVO nesse caminho, então o baseline que ficar para trás é
    o da posição VELHA — e vira régua da NOVA.

    Balanços iguais (800 → 800) de propósito: o único delta possível vem do
    rendimento, então o evento que aparecer é 100% invenção do baseline errado."""
    cid = _conn(user_id)
    db.save_open_finance_investments(cid, [
        normalize_pluggy_investment(_cx("velha", "CDB Velho", 800, 5)),
        normalize_pluggy_investment(_cx("nova", "CDB Novo", 800, 1000)),
    ])
    _, pocket_id, _ = db.create_pocket(user_id, "Viagem", interest_enabled=False)

    def _of(provider):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select id from open_finance_investments where "
                            "connection_id=%s and provider_investment_id=%s", (cid, provider))
                return cur.fetchone()["id"]

    assert db.bind_pocket_to_caixinha(user_id, pocket_id, _of("velha")) is True
    assert _run(user_id) == 0, "pré-condição: vínculo novo não anuncia nada"

    assert db.bind_pocket_to_caixinha(user_id, pocket_id, _of("nova")) is True

    assert _run(user_id) == 0, "o usuário não mexeu em nada: não há evento"
    ev = _last_event(user_id)
    assert ev is None, f"evento inventado: {ev and ev['payload'].get('mensagem')}"


def test_posicao_que_some_e_volta_nao_inventa_saque(user_id):
    """O mesmo estrago pelo caminho da AUSÊNCIA: o `_unbind_pocket` limpa o
    `of_last_seen_balance` e deixava o `of_last_seen_profit` — os dois são
    baseline da MESMA posição e têm de sair juntos."""
    cid = _conn(user_id)
    db.save_open_finance_investments(cid, [
        normalize_pluggy_investment(_cx("velha", "CDB Velho", 800, 5))])
    _, pocket_id, _ = db.create_pocket(user_id, "Viagem", interest_enabled=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from open_finance_investments where "
                        "connection_id=%s and provider_investment_id='velha'", (cid,))
            assert db.bind_pocket_to_caixinha(user_id, pocket_id, cur.fetchone()["id"]) is True
        conn.commit()
    assert _run(user_id) == 0

    # some (a meta volta ao saldo próprio) e volta com rendimento MUITO maior
    db.save_open_finance_investments(cid, [], leitura_completa=True)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select of_last_seen_profit as p from pockets where id=%s", (pocket_id,))
            assert cur.fetchone()["p"] is None, "o baseline de rendimento sai com o vínculo"
    db.save_open_finance_investments(cid, [
        normalize_pluggy_investment(_cx("velha", "CDB Velho", 800, 1000))], leitura_completa=True)

    assert _run(user_id) == 0, "religou: o baseline nasce na posição atual"
    ev = _last_event(user_id)
    assert ev is None, f"evento inventado: {ev and ev['payload'].get('mensagem')}"
