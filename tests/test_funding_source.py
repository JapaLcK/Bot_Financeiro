"""Origem explícita das movimentações de dinheiro.

O bug: `accounts.balance` (a Carteira) fazia dois papéis — saldo mantido pelo Pig E
teste de "você tem dinheiro para isso?". Ao conectar um banco por Open Finance, o
produto pede para zerar a Carteira (senão o mesmo dinheiro conta 2x), e o saldo real
passa a vir do sync. A leitura foi migrada para esse mundo; a escrita não. Resultado:
app mostrando R$ 1.387,76 e o bot respondendo "Saldo insuficiente na conta" a um
aporte de R$ 800.

Aqui a origem vira explícita: `carteira` debita e exige cobertura; `bank` não toca em
`accounts.balance` e grava `delta_conta: 0`.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from psycopg.types.json import Jsonb

import db
from core.handlers import investments as h_investments
from core.handlers import pockets as h_pockets
from core.services import funding


def _connect_fake_bank(user_id: int, balance: str = "1387.76", nome: str = "Conta") -> int:
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-{user_id}-{nome}", "connector": {"id": 612, "name": "Nubank"},
         "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [{
            "provider_account_id": f"acc-{user_id}-{nome}",
            "name": nome,
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currency": "BRL",
            "balance": Decimal(balance),
            "raw": {},
            "transactions": [],
        }],
    )
    return connection["id"]


# ─── a regra de escolha ──────────────────────────────────────────────────────

def test_sem_banco_e_com_carteira_usa_a_carteira(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    r = funding.resolve(user_id, 800)
    assert r["source"]["kind"] == funding.CARTEIRA


def test_carteira_zerada_com_banco_usa_o_banco(user_id):
    """O caso do relato: Carteira R$ 0,00, banco com R$ 1.387,76, aporte de R$ 800."""
    _connect_fake_bank(user_id)
    r = funding.resolve(user_id, 800)
    assert r["source"]["kind"] == funding.BANK
    assert r["source"]["label"] == "Nubank · Conta"


def test_as_duas_cobrem_vira_pergunta(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    r = funding.resolve(user_id, 800)
    assert [f["kind"] for f in r["ask"]] == [funding.CARTEIRA, funding.BANK]


def test_uma_fonte_so_nunca_vira_pergunta(user_id):
    """Quem tem a Carteira zerada não pode levar um round-trip extra a cada aporte."""
    _connect_fake_bank(user_id)
    assert "ask" not in funding.resolve(user_id, 800)


def test_nada_cobre_devolve_insuficiente_com_as_fontes(user_id):
    _connect_fake_bank(user_id, "100.00")
    r = funding.resolve(user_id, 800)
    assert [f["label"] for f in r["insufficient"]["sources"]] == ["Carteira", "Nubank · Conta"]


def test_deterministico_nunca_devolve_insuficiente(user_id):
    """Dashboard, Discord e chat da IA indexam ["source"] direto — devolver
    `insufficient` aqui virava KeyError e 500 em vez de 400. Achado por
    tests/test_pockets_endpoints.py."""
    _connect_fake_bank(user_id, "10.00")
    r = funding.resolve_deterministic(user_id, 99999)
    assert r["source"]["kind"] == funding.CARTEIRA   # cai na Carteira; o db recusa depois


def test_deterministico_prefere_a_carteira_quando_ela_cobre(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    assert funding.resolve_deterministic(user_id, 800)["source"]["kind"] == funding.CARTEIRA


# ─── o efeito no razão ───────────────────────────────────────────────────────

def test_aporte_com_origem_banco_nao_debita_a_carteira(user_id):
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Reserva de Emergencia", 0.14, "yearly")

    msg = h_investments.deposit(
        user_id, "Investi 800 na renda fixa",
        {"investment_name": "Reserva de Emergencia", "amount": 800},
    )

    assert "✅" in msg
    assert "saindo do Nubank" in msg          # a origem fica visível
    assert float(db.get_balance(user_id)) == 0.0
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(user_id)}
    assert invs["Reserva de Emergencia"] == 800.0


def test_aporte_sem_banco_continua_debitando(user_id):
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    msg = h_investments.deposit(
        user_id, "investi 800 no cdb xp", {"investment_name": "CDB XP", "amount": 800},
    )

    assert float(db.get_balance(user_id)) == 200.0
    assert "saindo do" not in msg             # sem banco não há o que desambiguar


def test_desfazer_aporte_do_banco_nao_cria_dinheiro(user_id):
    """`delta_conta` é o que o desfazer estorna (db/accounts.py). Se ficasse -800 sem
    o débito ter acontecido, desfazer CRIARIA R$ 800 na Carteira. É a única falha
    desta mudança que erraria na direção do dinheiro do usuário."""
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    source = funding.resolve(user_id, 800)["source"]

    launch_id, _a, _i, _c = db.investment_deposit_from_account(
        user_id, "Renda Fixa", 800, "t", funding_source=funding.to_db_arg(source))

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where id=%s", (launch_id,))
        efeitos = cur.fetchone()["efeitos"]
    assert efeitos["delta_conta"] == 0
    assert efeitos["funding_source"]["kind"] == "bank"   # proveniência para a etapa 2

    db.delete_launch_and_rollback(user_id, launch_id)
    assert float(db.get_balance(user_id)) == 0.0


def test_resgate_com_banco_nao_credita_a_carteira(user_id):
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    db.investment_deposit_from_account(
        user_id, "Renda Fixa", 800, "t", funding_source={"kind": "bank"})

    msg = h_investments.withdraw(
        user_id, "resgatei 300 da renda fixa",
        {"investment_name": "Renda Fixa", "amount": 300},
    )

    assert "para o Nubank" in msg
    assert float(db.get_balance(user_id)) == 0.0


def test_caixinha_nos_dois_sentidos(user_id):
    _connect_fake_bank(user_id)
    db.create_pocket(user_id, "Viagem")

    dep = h_pockets.deposit(user_id, "coloquei 200 na caixinha viagem",
                            {"pocket_name": "Viagem", "amount": 200})
    saq = h_pockets.withdraw(user_id, "retirei 50 da caixinha viagem",
                             {"pocket_name": "Viagem", "amount": 50})

    assert "saindo do Nubank" in dep
    assert float(db.get_balance(user_id)) == 0.0
    assert "sincronizar" in saq                # aviso do sync também na saída
    pockets = {p["name"]: float(p["balance"]) for p in db.list_pockets(user_id)}
    assert pockets["Viagem"] == 150.0


# ─── a pergunta ──────────────────────────────────────────────────────────────

def test_pergunta_quando_as_duas_cobrem(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    msg = h_investments.deposit(
        user_id, "investi 800 na renda fixa",
        {"investment_name": "Renda Fixa", "amount": 800},
    )

    assert "De onde sai" in msg
    assert "1. **Carteira**" in msg and "2. **Nubank · Conta**" in msg
    pend = db.get_pending_action(user_id)
    assert pend["action_type"] == "funding_source_choice"
    # nada foi lançado antes da escolha
    assert float(db.list_investments(user_id)[0]["balance"]) == 0.0


def test_resposta_com_numero_lanca_da_origem_escolhida(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    msg = h_investments.resolve_funding_choice(
        user_id, "2", db.get_pending_action(user_id))

    assert "✅" in msg and "saindo do Nubank" in msg
    assert float(db.get_balance(user_id)) == 1000.0   # escolheu o banco: Carteira intacta
    assert db.get_pending_action(user_id) is None


def test_resposta_pela_carteira_debita(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    h_investments.resolve_funding_choice(user_id, "1", db.get_pending_action(user_id))

    assert float(db.get_balance(user_id)) == 200.0


def test_resposta_pelo_nome_tambem_vale(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    msg = h_investments.resolve_funding_choice(
        user_id, "nubank", db.get_pending_action(user_id))

    assert "saindo do Nubank" in msg


def test_texto_solto_reperunta_sem_lancar(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    msg = h_investments.resolve_funding_choice(
        user_id, "sei lá", db.get_pending_action(user_id))

    assert "Não entendi de onde sai" in msg
    assert db.get_pending_action(user_id) is not None      # pendência preservada
    assert float(db.list_investments(user_id)[0]["balance"]) == 0.0


def test_cancelar_limpa_a_pendencia(user_id):
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    msg = h_investments.resolve_funding_choice(
        user_id, "cancelar", db.get_pending_action(user_id))

    assert "cancelei" in msg.lower()
    assert db.get_pending_action(user_id) is None


def test_resolve_funding_choice_ignora_pendencia_alheia(user_id):
    assert h_investments.resolve_funding_choice(
        user_id, "1", {"action_type": "delete_launch", "payload": {}}) is None


def test_pendencia_bloqueia_o_fallback_de_ia():
    """Sem isso, a resposta "1" classifica como baixa confiança e o fallback de IA
    sequestra o turno (core/handle_incoming.py:692), perdendo a pergunta — e o
    pending de desfazer do fluxo de áudio sobrescreve a pergunta (:345).

    Importar `handle_incoming` puxa `ofxparse`, ausente neste ambiente (CLAUDE.md §6);
    no CI o pacote existe e o teste roda.
    """
    pytest.importorskip("ofxparse", reason="ausente localmente; presente no CI")
    from db import suprime_fallback_de_ia
    assert suprime_fallback_de_ia("funding_source_choice")


# ─── a mensagem que enganou ──────────────────────────────────────────────────

def test_mensagem_sem_banco_mostra_o_numero(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 50, None, "seed")
    msg = funding.msg_insuficiente(user_id, 800)
    assert "R$ 50,00" in msg and "R$ 800,00" in msg
    assert "Saldo insuficiente na conta para esse aporte." not in msg


def test_mensagem_com_banco_lista_cada_saldo(user_id):
    _connect_fake_bank(user_id, "100.00")
    msg = funding.msg_insuficiente(user_id, 800)
    assert "**Carteira**: R$ 0,00" in msg
    assert "**Nubank · Conta**: R$ 100,00" in msg
    assert "fora dos bancos conectados" in msg   # explica POR QUE a Carteira é zero


# ─── as outras superfícies ───────────────────────────────────────────────────

def test_chat_da_ia_segue_a_mesma_regra(user_id):
    from core.services.ai_chat.tools.investments import _investment_deposit_execute

    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    out = _investment_deposit_execute(user_id, {"name": "Renda Fixa", "amount": 800})

    assert "✅" in out and "saindo do Nubank" in out
    assert float(db.get_balance(user_id)) == 0.0


def test_dashboard_nao_recusa_mais_quem_tem_banco(user_id, monkeypatch):
    """A rota do dashboard tinha o bug idêntico ao do bot: devolvia
    "Saldo insuficiente na conta." para quem tem banco conectado."""
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    source = funding.resolve_deterministic(user_id, 800)["source"]
    db.investment_deposit_from_account(
        user_id, "Renda Fixa", 800, "aporte", funding_source=funding.to_db_arg(source))

    assert float(db.get_balance(user_id)) == 0.0
    assert float(db.list_investments(user_id)[0]["balance"]) == 800.0


def test_caixinha_tambem_pergunta_e_retoma(user_id):
    """A retomada da caixinha passa por outro módulo (pockets.deposita_com_origem);
    sem teste próprio, um erro de assinatura só apareceria em produção."""
    _connect_fake_bank(user_id, "1000.00")
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    db.create_pocket(user_id, "Viagem")

    pergunta = h_pockets.deposit(user_id, "coloquei 200 na caixinha viagem",
                                 {"pocket_name": "Viagem", "amount": 200})
    assert "De onde sai" in pergunta

    msg = h_investments.resolve_funding_choice(
        user_id, "2", db.get_pending_action(user_id))

    assert "✅" in msg and "saindo do Nubank" in msg
    assert float(db.get_balance(user_id)) == 500.0            # Carteira intacta
    assert float(db.list_pockets(user_id)[0]["balance"]) == 200.0


# ─── o mesmo saldo do banco não pode ser gasto duas vezes ────────────────────
#
# O saldo em `open_finance_accounts` é espelho: o Pig não escreve nele, então um
# lançamento com origem `bank` não o reduz. Sem descontar o que já foi comprometido
# desde o último sync, o MESMO saldo autorizava lançamentos infinitos — medido antes
# da correção: 3 aportes de R$ 800 aceitos contra R$ 1.387,76, total R$ 2.400.

def test_saldo_do_banco_nao_pode_ser_gasto_duas_vezes(user_id):
    _connect_fake_bank(user_id, "1387.76")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    primeiro = h_investments.deposit(
        user_id, "investi 800", {"investment_name": "Renda Fixa", "amount": 800})
    segundo = h_investments.deposit(
        user_id, "investi 800", {"investment_name": "Renda Fixa", "amount": 800})

    assert "✅" in primeiro
    assert "✅" not in segundo
    assert float(db.list_investments(user_id)[0]["balance"]) == 800.0


def test_o_que_sobra_do_banco_continua_disponivel(user_id):
    """O desconto é do comprometido, não um bloqueio da conta inteira."""
    _connect_fake_bank(user_id, "1000.00")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    h_investments.deposit(user_id, "investi 600", {"investment_name": "Renda Fixa", "amount": 600})
    fontes = funding.list_sources(user_id)
    banco = next(f for f in fontes if f["kind"] == funding.BANK)

    assert banco["espelho"] == Decimal("1000.00")
    assert banco["comprometido"] == Decimal("600")
    assert banco["balance"] == Decimal("400.00")

    ok = h_investments.deposit(user_id, "investi 400", {"investment_name": "Renda Fixa", "amount": 400})
    assert "✅" in ok


def test_sync_novo_zera_o_comprometido(user_id):
    """O corte é `criado_em > updated_at`: o que veio antes do sync já está embutido
    no saldo que o banco mandou e não pode ser descontado duas vezes."""
    _connect_fake_bank(user_id, "1000.00")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    h_investments.deposit(user_id, "investi 600", {"investment_name": "Renda Fixa", "amount": 600})
    assert funding.list_sources(user_id)[1]["comprometido"] == Decimal("600")

    # o banco sincroniza e manda o saldo já debitado
    _connect_fake_bank(user_id, "400.00")

    banco = funding.list_sources(user_id)[1]
    assert banco["comprometido"] == Decimal("0")
    assert banco["balance"] == Decimal("400.00")


def test_desfazer_devolve_a_disponibilidade(user_id):
    _connect_fake_bank(user_id, "1000.00")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    source = funding.resolve(user_id, 600)["source"]
    launch_id, *_ = db.investment_deposit_from_account(
        user_id, "Renda Fixa", 600, "t", funding_source=funding.to_db_arg(source))
    assert funding.list_sources(user_id)[1]["balance"] == Decimal("400")

    db.delete_launch_and_rollback(user_id, launch_id)
    assert funding.list_sources(user_id)[1]["balance"] == Decimal("1000.00")


def test_caixinha_tambem_compromete(user_id):
    _connect_fake_bank(user_id, "500.00")
    db.create_pocket(user_id, "Viagem")

    h_pockets.deposit(user_id, "coloquei 300 na caixinha viagem",
                      {"pocket_name": "Viagem", "amount": 300})

    assert funding.list_sources(user_id)[1]["balance"] == Decimal("200.00")


def test_mensagem_explica_o_comprometido(user_id):
    _connect_fake_bank(user_id, "1000.00")
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    h_investments.deposit(user_id, "investi 600", {"investment_name": "Renda Fixa", "amount": 600})

    msg = funding.msg_insuficiente(user_id, 800)

    assert "R$ 400,00 disponíveis" in msg
    assert "R$ 1.000,00 no banco" in msg
    assert "R$ 600,00 já lançados aqui" in msg


# ─── criar investimento já com valor inicial ────────────────────────────────

def test_criacao_com_aporte_inicial_usa_a_mesma_origem(user_id):
    """A rota de criação manda `initial_amount` — que é uma saída de dinheiro como
    outra qualquer. Sem resolver a origem ali, criar um investimento já com valor
    continuava recusado para quem tem banco conectado."""
    _connect_fake_bank(user_id, "1000.00")

    source = funding.resolve_deterministic(user_id, 800)["source"]
    db.create_investment_db(
        user_id, "CDB Novo", 0.14, "yearly", "criado",
        initial_amount=800, initial_note="aporte inicial",
        funding_source=funding.to_db_arg(source),
    )

    assert float(db.get_balance(user_id)) == 0.0          # Carteira intacta
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(user_id)}
    assert invs["CDB Novo"] == 800.0
    # e o valor entra no comprometido, como qualquer outra saída
    assert funding.list_sources(user_id)[1]["balance"] == Decimal("200.00")


# ─── corrida e isolamento ────────────────────────────────────────────────────
#
# O `funding.resolve` no serviço DECIDE e explica; quem AUTORIZA é
# `db.assert_bank_covers`, dentro da transação que grava. A diferença não é
# estética: com a checagem só no serviço, virava check-then-act. Medido com duas
# threads antes da correção: dois aportes de R$ 800 simultâneos passaram contra
# um saldo de R$ 1.000 (R$ 1.600 gravados). O caminho da Carteira nunca teve esse
# furo porque o `select ... for update` serializa.

def _aportes_simultaneos(user_id: int, n: int, valor: float) -> int:
    """Dispara n aportes ao mesmo tempo com uma barreira e devolve quantos passaram."""
    import threading

    barreira = threading.Barrier(n)
    aceitos = []

    def go():
        barreira.wait()
        r = h_investments.deposit(
            user_id, "investi", {"investment_name": "Renda Fixa", "amount": valor})
        aceitos.append("✅" in r)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sum(aceitos)


@pytest.mark.parametrize("n,valor,saldo,teto_aceitos", [
    (2, 800, "1000", 1),    # só um cabe
    (5, 300, "1000", 3),    # três cabem
    (3, 200, "1000", 3),    # todos cabem — o lock não pode recusar quem cabia
])
def test_simultaneos_nunca_estouram_o_saldo(user_id, n, valor, saldo, teto_aceitos):
    _connect_fake_bank(user_id, saldo)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    aceitos = _aportes_simultaneos(user_id, n, valor)
    investido = float(db.list_investments(user_id)[0]["balance"])

    assert aceitos == teto_aceitos
    assert investido <= float(saldo)


def test_guard_recusa_conta_de_outro_usuario(user_id):
    """`assert_bank_covers` é pública em db/ — precisa filtrar pelo dono, e não só
    confiar em quem chama (CLAUDE.md §5: isolamento por usuário é regra dura)."""
    import uuid as _uuid

    outro = int(_uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    _connect_fake_bank(outro, "5000.00")
    conta_alheia = db.list_bank_accounts(outro)[0]["id"]

    with db.get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
            db.assert_bank_covers(cur, user_id, conta_alheia, 100)


def test_guard_autoriza_a_propria_conta(user_id):
    """A guarda não pode ser tão rígida a ponto de recusar o caso legítimo."""
    _connect_fake_bank(user_id, "500.00")
    minha = db.list_bank_accounts(user_id)[0]["id"]

    with db.get_conn() as conn, conn.cursor() as cur:
        db.assert_bank_covers(cur, user_id, minha, 400)          # cabe: não levanta
        with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
            db.assert_bank_covers(cur, user_id, minha, 600)      # não cabe


# ─── #282: o saque volta para a origem do depósito ───────────────────────────
#
# O defeito, medido em produção na conta do dono (29/08 23:31–23:47): depósitos
# com `funding_source=None` tiraram 200 + 100 = R$ 300 da Carteira, e os 4 saques
# seguintes gravaram `delta_conta = 0` — R$ 300 saíram, R$ 0,00 voltaram. O
# O destino era decidido do zero, FORA da transação, e qualquer banco conectado
# vencia a Carteira, sem consultar o `funding_source` que o próprio depósito
# gravou. Hoje quem decide é `db.destination_of_lots`, dentro da transação.
#
# A métrica é o patrimônio NO PIG (Carteira + caixinhas + investimentos), SEM o
# espelho do banco: ela pega a evaporação E pegaria um conserto que criasse
# dinheiro creditando a Carteira por um depósito que saiu do banco.
#
# Os casos são todos no MESMO dia de propósito: caixinha rende CDI e o saque
# cobra IOF/IR regressivo — em dias diferentes a comparação ganharia ruído de
# rendimento em vez de medir a assimetria.

def _patrimonio(user_id: int) -> float:
    return (float(db.get_balance(user_id))
            + sum(float(p["balance"]) for p in db.list_pockets(user_id))
            + sum(float(i["balance"]) for i in db.list_investments(user_id)))


def _efeitos(user_id: int, tipo: str) -> dict:
    """`efeitos` do lançamento mais recente do tipo. As superfícies silenciosas
    (chat da IA, dashboard) não devolvem o launch_id."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where user_id=%s and tipo=%s "
                    "order by id desc limit 1", (user_id, tipo))
        return cur.fetchone()["efeitos"]


def _assert_volta_para_a_origem(user_id: int, tipo_dep: str, tipo_saq: str, net: float):
    """A origem foi CONSULTADA, não adivinhada.

    Sem comparar os dois `funding_source`, um conserto que acertasse o total por
    acaso (ex.: creditar sempre a Carteira) passaria. `delta_conta` prende o outro
    lado: +net quando o dinheiro voltou pra Carteira, 0 quando voltou pro banco.
    """
    dep = _efeitos(user_id, tipo_dep)
    saq = _efeitos(user_id, tipo_saq)
    assert saq["funding_source"] == dep["funding_source"], (
        f"saque foi para {saq['funding_source']}, depósito saiu de {dep['funding_source']}")
    esperado = 0 if dep["funding_source"] else net
    assert float(saq["delta_conta"]) == pytest.approx(esperado)


def _dashboard_client(user_id: int, email: str):
    """Cliente autenticado do dashboard — 4 testes daqui usam o mesmo preparo."""
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    return client, {dashboard.CSRF_HEADER_NAME: "t", "Content-Type": "application/json"}


def _seed_carteira_e_banco(user_id: int, carteira: float = 1000.0):
    """O cenário do relato: banco conectado E Carteira com dinheiro.

    É aqui que o defeito vivia — a Carteira cobre, então o depósito sai dela, e o
    saque devolvia pro banco assim mesmo.
    """
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", carteira, None, "seed")


# ── caixinha, nas três superfícies ───────────────────────────────────────────

def test_282_caixinha_whatsapp_volta_para_a_carteira(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)

    source = funding.resolve_deterministic(user_id, 100)["source"]
    assert source["kind"] == funding.CARTEIRA        # a Carteira cobre, ela sai na frente
    db.pocket_deposit_from_account(user_id, "viagem", 100, "dep",
                                   funding_source=funding.to_db_arg(source))

    # saque parcial (core/handlers/pockets.py, caminho do valor)
    h_pockets.withdraw(user_id, "retirei 40 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 40})
    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 40.0)

    # e o caminho do "esvaziar", que usa o mesmo `fs` num segundo chamador
    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})
    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 60.0)

    assert _patrimonio(user_id) == pytest.approx(antes)


def test_282_caixinha_chat_da_ia_volta_para_a_carteira(user_id):
    """Superfície silenciosa: não há pergunta de origem nem aviso na tela — o
    usuário não tinha como perceber o dinheiro sumindo."""
    from core.services.ai_chat.tools.pockets import (
        _pocket_deposit_execute, _pocket_withdraw_execute)

    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)

    _pocket_deposit_execute(user_id, {"pocket_name": "viagem", "amount": 100})
    _pocket_withdraw_execute(user_id, {"pocket_name": "viagem", "amount": 100})

    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)


def test_282_caixinha_dashboard_volta_para_a_carteira(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)
    client, headers = _dashboard_client(user_id, "p282@t.com")

    r = client.post(f"/pockets/{user_id}/viagem/deposit", json={"amount": 100}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/pockets/{user_id}/viagem/withdraw", json={"amount": 100}, headers=headers)
    assert r.status_code == 200, r.text

    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)


# ── investimento: a MESMA assimetria, e a issue #282 não a citava ────────────

def test_282_investimento_whatsapp_volta_para_a_carteira(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    antes = _patrimonio(user_id)

    source = funding.resolve_deterministic(user_id, 100)["source"]
    db.investment_deposit_from_account(user_id, "CDB XP", 100, "dep",
                                       funding_source=funding.to_db_arg(source))
    msg = h_investments.withdraw(user_id, "resgatei 100 do CDB XP",
                                 {"investment_name": "CDB XP", "amount": 100})

    _assert_volta_para_a_origem(user_id, "aporte_investimento", "resgate_investimento", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)
    # SENTIDO A: o dinheiro voltou para a Carteira (há banco conectado, mas ele não é
    # o destino). O texto não pode nomear banco nem prometer o sync.
    assert "Open Finance sincronizar" not in msg, msg
    assert ", para o " not in msg, msg


def test_282_investimento_chat_da_ia_volta_para_a_carteira(user_id):
    from core.services.ai_chat.tools.investments import (
        _investment_deposit_execute, _investment_withdraw_execute)

    _seed_carteira_e_banco(user_id)
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    antes = _patrimonio(user_id)

    _investment_deposit_execute(user_id, {"name": "CDB XP", "amount": 100})
    _investment_withdraw_execute(user_id, {"name": "CDB XP", "amount": 100})

    _assert_volta_para_a_origem(user_id, "aporte_investimento", "resgate_investimento", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)


def test_282_investimento_dashboard_volta_para_a_carteira(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    antes = _patrimonio(user_id)
    client, headers = _dashboard_client(user_id, "i282@t.com")

    r = client.post(f"/investments/{user_id}/deposit",
                    json={"name": "CDB XP", "amount": 100}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/investments/{user_id}/withdraw",
                    json={"name": "CDB XP", "amount": 100}, headers=headers)
    assert r.status_code == 200, r.text

    _assert_volta_para_a_origem(user_id, "aporte_investimento", "resgate_investimento", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)


# ── os três controles positivos ──────────────────────────────────────────────

def test_282_sem_open_finance_a_ida_e_volta_fecha_em_zero(user_id):
    """A diferença entre este caso e o de cima é a prova de que o teste mede a
    ASSIMETRIA, não aritmética de ida-e-volta: aqui não há banco para vencer a
    Carteira, e o total já fechava em zero antes do conserto."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)

    source = funding.resolve_deterministic(user_id, 100)["source"]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "dep",
                                   funding_source=funding.to_db_arg(source))
    h_pockets.withdraw(user_id, "retirei 100 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 100})

    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 100.0)
    assert _patrimonio(user_id) == pytest.approx(antes)


def test_282_deposito_do_banco_continua_voltando_para_o_banco(user_id):
    """Sem este controle, um conserto "devolve SEMPRE para a Carteira" passaria no
    caso de cima e inflaria o consolidado: creditaria a Carteira com dinheiro que
    o sync do banco vai devolver de novo."""
    _connect_fake_bank(user_id)                       # Carteira zerada: a origem é o banco
    db.create_pocket(user_id, "viagem")

    source = funding.resolve_deterministic(user_id, 100)["source"]
    assert source["kind"] == funding.BANK
    db.pocket_deposit_from_account(user_id, "viagem", 100, "dep",
                                   funding_source=funding.to_db_arg(source))
    h_pockets.withdraw(user_id, "retirei 100 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 100})

    _assert_volta_para_a_origem(user_id, "deposito_caixinha", "saque_caixinha", 100.0)
    assert float(db.get_balance(user_id)) == 0.0      # a Carteira NÃO foi creditada


# ─── a leitura é de ESTADO (lotes abertos), não de HISTÓRIA ───────────────────
#
# A primeira versão do conserto lia o histórico inteiro de `launches`, e aí
# "origens misturadas" virava "misturou uma vez, para sempre". A sequência abaixo
# (test_J1) reproduzia o P0 inteiro DEPOIS daquele conserto: no passo 6 a caixinha
# é 100% dinheiro da Carteira, mas o lote do banco fechou no passo 3 e a história
# continuava dizendo "misturado".

def test_J1_lote_do_banco_ja_fechado_nao_contamina_o_deposito_seguinte(user_id):
    """A sequência de 6 passos que sobrevivia ao conserto por histórico:

      1. banco conectado, Carteira zerada (o produto pede pra zerar);
      2. deposita 500 na caixinha  -> origem = banco (a Carteira não cobre);
      3. saca os 500               -> volta pro banco; o lote do banco FECHA;
      4. registra 200 em dinheiro  -> Carteira = 200;
      5. deposita 100 na caixinha  -> origem = CARTEIRA (ela cobre);
      6. saca os 100               -> tem de voltar pra CARTEIRA.

    No passo 6 o único lote aberto veio da Carteira. Pelo histórico há duas
    origens e o dinheiro ia pro banco: R$ 100 evaporavam da visão do usuário.
    """
    _connect_fake_bank(user_id)                       # Carteira = 0
    db.create_pocket(user_id, "viagem")
    client, headers = _dashboard_client(user_id, "j1@t.com")

    assert client.post(f"/pockets/{user_id}/viagem/deposit",
                       json={"amount": 500}, headers=headers).status_code == 200
    assert client.post(f"/pockets/{user_id}/viagem/withdraw",
                       json={"amount": 500}, headers=headers).status_code == 200

    db.add_launch_and_update_balance(user_id, "receita", 200, None, "dinheiro em especie")
    assert float(db.get_balance(user_id)) == 200.0
    antes = _patrimonio(user_id)

    assert client.post(f"/pockets/{user_id}/viagem/deposit",
                       json={"amount": 100}, headers=headers).status_code == 200
    assert float(db.get_balance(user_id)) == 100.0, "o depósito saiu da Carteira"
    assert client.post(f"/pockets/{user_id}/viagem/withdraw",
                       json={"amount": 100}, headers=headers).status_code == 200

    depois = _patrimonio(user_id)
    assert depois == pytest.approx(antes), (
        f"EVAPOROU R$ {antes - depois:.2f} — Carteira={float(db.get_balance(user_id)):.2f}, "
        f"saque={_efeitos(user_id, 'saque_caixinha')['funding_source']}")


def test_J1_investimento_o_mesmo_lote_ja_fechado(user_id):
    """O irmão do J1 no investimento: o `_LOTES` tem duas variantes e só uma
    estaria coberta se este caso não existisse."""
    _connect_fake_bank(user_id)                       # Carteira = 0
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")

    source = funding.resolve_deterministic(user_id, 500)["source"]
    assert source["kind"] == funding.BANK
    db.investment_deposit_from_account(user_id, "CDB XP", 500, "do banco",
                                       funding_source=funding.to_db_arg(source))
    h_investments.withdraw(user_id, "resgatei 500 do CDB XP",
                           {"investment_name": "CDB XP", "amount": 500})

    db.add_launch_and_update_balance(user_id, "receita", 200, None, "dinheiro em especie")
    antes = _patrimonio(user_id)

    source = funding.resolve_deterministic(user_id, 100)["source"]
    assert source["kind"] == funding.CARTEIRA
    db.investment_deposit_from_account(user_id, "CDB XP", 100, "da carteira",
                                       funding_source=funding.to_db_arg(source))
    h_investments.withdraw(user_id, "resgatei 100 do CDB XP",
                           {"investment_name": "CDB XP", "amount": 100})

    assert _patrimonio(user_id) == pytest.approx(antes), (
        f"resgate={_efeitos(user_id, 'resgate_investimento')['funding_source']}")


def test_lotes_abertos_de_origens_diferentes_caem_na_regra_de_sempre(user_id):
    """Comportamento ACEITO, não conserto: com DOIS lotes consumidos de origens
    diferentes o destino segue o de hoje (o primeiro banco). Dividir o resgate é
    decisão de produto ainda não tomada — ver `db.destination_of_lots`.

    Os dois bancos existem para tornar o teste insensível à ORDEM das linhas do
    `select distinct` (que não tem `order by`). Depositando do banco B, nenhuma
    das duas origens consumidas é o destino esperado: um `origens[0]` ingênuo daria
    a Carteira ou o banco B, e a regra de sempre dá o banco A (`BANK_ACCOUNTS_ORDER`
    ordena por `balance desc, id`). Sem isso o caso passava por sorte do hash.
    """
    _connect_fake_bank(user_id, "5000.00", nome="Banco A")
    _connect_fake_bank(user_id, "100.00", nome="Banco B")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_pocket(user_id, "viagem")

    bancos = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK]
    banco_a, banco_b = bancos[0], bancos[1]
    assert banco_a["label"] != banco_b["label"]

    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    db.pocket_deposit_from_account(user_id, "viagem", 50, "do banco B",
                                   funding_source=funding.to_db_arg(banco_b))

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    destino = _efeitos(user_id, "saque_caixinha")["funding_source"]
    assert destino and destino["kind"] == funding.BANK, destino
    assert destino["of_account_id"] == banco_a["of_account_id"], (
        f"caiu em {destino['label']}, não na regra de sempre ({banco_a['label']})")


# ── isolamento, tipo e lote órfão: as três guardas da query ──────────────────

def test_isolamento_por_usuario(user_id):
    """Dois usuários com uma caixinha de MESMO NOME. A origem de um não pode
    aparecer na do outro (CLAUDE.md §0: `where user_id = %s` em toda tabela).

    Os dois têm banco conectado de propósito: sem banco no B, a regra de sempre
    já devolveria a Carteira e a mutação (tirar `pl.user_id`) passaria verde.
    """
    import uuid as _uuid

    outro = int(_uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    _connect_fake_bank(outro)                          # A: Carteira zerada
    db.create_pocket(outro, "viagem")
    fonte_a = funding.resolve_deterministic(outro, 100)["source"]
    assert fonte_a["kind"] == funding.BANK
    db.pocket_deposit_from_account(outro, "viagem", 100, "do banco de A",
                                   funding_source=funding.to_db_arg(fonte_a))

    _seed_carteira_e_banco(user_id)                    # B: banco E Carteira
    db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira de B",
                                   funding_source=None)

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    saq = _efeitos(user_id, "saque_caixinha")
    assert saq["funding_source"] is None, (
        f"o lote do OUTRO usuário vazou: destino {saq['funding_source']}")
    assert float(db.get_balance(user_id)) == 1000.0


def test_tipo_separa_caixinha_de_investimento(user_id):
    """Mesmo usuário, mesmo nome nas duas entidades, origens diferentes. É o
    `_LOTES[tipo]` que separa — trocar as duas entradas do dict deixa vermelho."""
    _seed_carteira_e_banco(user_id)
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]

    db.create_pocket(user_id, "reserva")
    db.pocket_deposit_from_account(user_id, "reserva", 100, "da carteira", funding_source=None)

    db.create_investment(user_id, "reserva", 0.12, "yearly")
    db.investment_deposit_from_account(user_id, "reserva", 100, "do banco",
                                       funding_source=funding.to_db_arg(banco))

    h_pockets.withdraw(user_id, "esvaziar caixinha reserva", {"pocket_name": "reserva"})
    h_investments.withdraw(user_id, "resgatar tudo da reserva",
                           {"investment_name": "reserva", "want_all": True})

    assert _efeitos(user_id, "saque_caixinha")["funding_source"] is None, (
        "o saque da caixinha pegou a origem do INVESTIMENTO de mesmo nome")
    resg = _efeitos(user_id, "resgate_investimento")["funding_source"]
    assert resg and int(resg["of_account_id"]) == banco["of_account_id"], (
        f"o resgate pegou a origem da CAIXINHA de mesmo nome: {resg}")


# ── bordas do alvo e da origem ───────────────────────────────────────────────

@pytest.mark.parametrize("como", ["desconectar", "pausar"])
def test_banco_de_origem_desconectado_nao_credita_a_carteira(user_id, como):
    """Depósito veio do banco e o banco sumiu. A origem existe mas não está mais em
    `list_sources` — cai na regra de sempre, sem inventar Carteira.

    As DUAS formas de sumir, porque elas somem por caminhos diferentes:
    `disconnect_open_finance_connection` APAGA a linha, e a conta pausada continua
    lá — só o `connection_status not in ('PAUSED','DELETED')` de `BANK_ACCOUNTS_SQL`
    a tira. Com só a primeira, esse filtro não era medido em `_bank_account`.
    """
    conn_id = _connect_fake_bank(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "do banco",
                                   funding_source=funding.to_db_arg(banco))
    if como == "desconectar":
        db.disconnect_open_finance_connection(user_id, conn_id)
    else:
        db.pause_open_finance_connection(conn_id)

    antes = _patrimonio(user_id)
    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})
    assert _patrimonio(user_id) == pytest.approx(antes), "criou dinheiro na Carteira"
    # O `_patrimonio` sozinho não separa os dois lados aqui (o dinheiro só muda de
    # bolso DENTRO do Pig, e a soma fica igual nas duas hipóteses). Quem separa é o
    # destino gravado: mandar para um banco que não existe mais grava delta_conta 0 e
    # o dinheiro some de vez — é o recorte de `BANK_ACCOUNTS_SQL` em `_bank_account`
    # que evita isso, e sem esta linha ele não era medido.
    saq = _efeitos(user_id, "saque_caixinha")
    assert saq["funding_source"] is None, (
        f"destino num banco desconectado ({saq['funding_source']}): o dinheiro some")
    assert float(db.get_balance(user_id)) == 100.0


def test_of_account_id_como_string_no_json_ainda_casa(user_id):
    """`efeitos` é jsonb livre. Com `of_account_id` gravado como STRING o cast
    `::bigint` da versão anterior estourava; a comparação em texto casa."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(
        user_id, "viagem", 100, "do banco",
        funding_source={"kind": "bank", "of_account_id": str(banco["of_account_id"]),
                        "label": banco["label"]})

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    saq = _efeitos(user_id, "saque_caixinha")
    destino = saq["funding_source"]
    assert destino and destino["kind"] == funding.BANK, destino
    assert int(destino["of_account_id"]) == banco["of_account_id"]
    # o depósito saiu do banco, então a Carteira não foi tocada nem na ida nem na volta
    assert saq["delta_conta"] == 0, saq["delta_conta"]
    assert float(db.get_balance(user_id)) == 1000.0


def test_lot_id_nao_numerico_vira_orfao_em_vez_de_estourar(user_id):
    """O outro lado do mesmo motivo: `lot_id` fora do formato (medido: `1.9`,
    lista, string) não pode derrubar a query. Em texto ele só não casa."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update launches set efeitos = jsonb_set(efeitos, '{pocket_lot_create,lot_id}', "
            "'\"abc\"') where user_id=%s and tipo='deposito_caixinha'", (user_id,))
        conn.commit()

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    destino = _efeitos(user_id, "saque_caixinha")["funding_source"]
    assert destino and destino["kind"] == funding.BANK, (
        f"o lote virou Carteira em vez de órfão e criaria dinheiro: {destino}")


@pytest.mark.parametrize("nome", ["Férias na Bahia", "reserva de emergência", "AÇÃO 2030"])
def test_nome_acentuado_ida_e_volta_pelo_dashboard(user_id, nome):
    """`lower()` do Postgres em nome com acento, espaço e caixa alta, pela URL."""
    import urllib.parse

    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, nome)
    antes = _patrimonio(user_id)
    client, headers = _dashboard_client(user_id, "acento@t.com")
    enc = urllib.parse.quote(nome)

    assert client.post(f"/pockets/{user_id}/{enc}/deposit",
                       json={"amount": 100}, headers=headers).status_code == 200
    assert float(db.get_balance(user_id)) == 900.0
    assert client.post(f"/pockets/{user_id}/{enc}/withdraw",
                       json={"amount": 100}, headers=headers).status_code == 200
    assert _patrimonio(user_id) == pytest.approx(antes), (
        f"{nome}: saque={_efeitos(user_id, 'saque_caixinha')['funding_source']}")


# ── pela CONVERSA, com a pergunta de origem respondida ───────────────────────
#
# Armadilha medida: sem responder "De onde sai?" o depósito NÃO acontece, o saque
# falha e o `assert patrimônio == antes` fecha porque nada rodou. Os três passos
# obrigatórios em todo teste daqui: assertar a pergunta, responder, e conferir o
# SALDO DO ALVO antes de sacar.

def _manda(user_id: int, texto: str) -> str:
    from core import intent_router
    from core.intent_classifier import classify
    from core.types import IncomingMessage

    return intent_router.route(classify(texto, user_id=user_id),
                               IncomingMessage(platform="whatsapp", user_id=user_id, text=texto))


def _saldo_pocket(user_id: int, nome: str) -> float:
    return next(float(p["balance"]) for p in db.list_pockets(user_id) if p["name"] == nome)


def test_conversa_deposito_da_carteira_e_saque_total(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)

    p = _manda(user_id, "guardei 100 na caixinha viagem")
    assert "De onde sai" in p, p
    r = _manda(user_id, "1")                       # 1 = Carteira
    assert _saldo_pocket(user_id, "viagem") == 100.0, r
    assert float(db.get_balance(user_id)) == 900.0

    s = _manda(user_id, "tirei 100 da caixinha viagem")
    assert _saldo_pocket(user_id, "viagem") == 0.0, s
    assert _patrimonio(user_id) == pytest.approx(antes), s


def test_conversa_saque_com_pergunta_de_valor(user_id):
    """O nome vem na primeira mensagem, o VALOR vira pergunta. Na volta o destino
    ainda é o certo? (a resolução desceu para depois do nome em core/handlers)."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)
    assert "De onde sai" in _manda(user_id, "guardei 100 na caixinha viagem")
    _manda(user_id, "1")
    assert _saldo_pocket(user_id, "viagem") == 100.0

    p = h_pockets.withdraw(user_id, "sacar da caixinha viagem", {"pocket_name": "viagem"})
    assert "valor" in p.lower(), p
    r = _manda(user_id, "100")
    assert _saldo_pocket(user_id, "viagem") == 0.0, r
    assert _patrimonio(user_id) == pytest.approx(antes), r


def test_conversa_com_outro_assunto_no_meio(user_id):
    """Duas conversas de assuntos diferentes no mesmo usuário (CLAUDE.md §3)."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    antes = _patrimonio(user_id)
    assert "De onde sai" in _manda(user_id, "guardei 100 na caixinha viagem")
    _manda(user_id, "1")
    assert _saldo_pocket(user_id, "viagem") == 100.0
    _manda(user_id, "gastei 50 no mercado")

    s = _manda(user_id, "esvaziar caixinha viagem")
    assert _saldo_pocket(user_id, "viagem") == 0.0, s
    assert _patrimonio(user_id) == pytest.approx(antes - 50), s


def test_conversa_investimento(user_id):
    _seed_carteira_e_banco(user_id)
    db.create_investment_db(user_id, "Reserva", 1.0, "cdi", "seed")
    antes = _patrimonio(user_id)
    assert "De onde sai" in _manda(user_id, "investi 100 na reserva")
    _manda(user_id, "1")
    saldo = next(float(i["balance"]) for i in db.list_investments(user_id) if i["name"] == "Reserva")
    assert saldo == 100.0

    r = _manda(user_id, "resgatei 100 da reserva")
    assert _patrimonio(user_id) == pytest.approx(antes), r


def test_conversa_deposito_do_BANCO_e_saque(user_id):
    """Controle positivo pela conversa: escolheu o banco, tem de voltar pro banco
    — sem creditar a Carteira com dinheiro que o sync vai devolver de novo."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    assert "De onde sai" in _manda(user_id, "guardei 100 na caixinha viagem")
    _manda(user_id, "2")                            # 2 = banco
    assert _saldo_pocket(user_id, "viagem") == 100.0
    assert float(db.get_balance(user_id)) == 1000.0

    s = _manda(user_id, "tirei 100 da caixinha viagem")
    assert float(db.get_balance(user_id)) == 1000.0, s   # a Carteira NÃO foi creditada


# ── o recorte pelo VALOR: quais lotes ESTE saque consome ─────────────────────

def test_saque_parcial_consome_so_o_lote_da_carteira(user_id):
    """O resíduo da #282 que sobrava depois do destino-por-lote-aberto.

    `[carteira 100; banco 50]` e saque de 100: o FIFO consome SÓ o lote da Carteira,
    mas "todos os lotes abertos" via duas origens, mandava pro banco e os R$ 100
    evaporavam. Hoje `db.destination_of_lots` recebe os lotes que o FIFO consumiu de
    fato, então a pergunta já é sobre o prefixo — sem previsão nenhuma.
    """
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    db.pocket_deposit_from_account(user_id, "viagem", 50, "do banco",
                                   funding_source=funding.to_db_arg(banco))
    assert float(db.get_balance(user_id)) == 900.0
    antes = _patrimonio(user_id)

    h_pockets.withdraw(user_id, "retirei 100 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 100})

    # controle positivo: o caminho legítimo credita a Carteira de volta
    assert float(db.get_balance(user_id)) == 1000.0, "a Carteira não recebeu de volta"
    assert _patrimonio(user_id) == pytest.approx(antes), (
        f"EVAPOROU R$ {antes - _patrimonio(user_id):.2f}")
    assert _efeitos(user_id, "saque_caixinha")["funding_source"] is None


def test_conversa_saque_parcial_de_caixinha_com_duas_origens(user_id):
    """O mesmo pelo `handle_incoming`, escolhendo as origens na conversa."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    assert "De onde sai" in _manda(user_id, "guardei 100 na caixinha viagem")
    _manda(user_id, "1")                                     # 1 = Carteira
    assert "De onde sai" in _manda(user_id, "guardei 50 na caixinha viagem")
    _manda(user_id, "2")                                     # 2 = banco
    assert _saldo_pocket(user_id, "viagem") == 150.0
    antes = _patrimonio(user_id)

    r = _manda(user_id, "tirei 100 da caixinha viagem")
    assert _saldo_pocket(user_id, "viagem") == 50.0, r
    assert _patrimonio(user_id) == pytest.approx(antes), (
        f"EVAPOROU R$ {antes - _patrimonio(user_id):.2f} — {r}")


def test_saque_que_atravessa_as_duas_origens_mantem_a_regra_de_sempre(user_id):
    """Controle do outro lado: o recorte não pode virar "sempre Carteira".

    Sacar 120 de `[carteira 100; banco 50]` consome os DOIS lotes — aí a divisão é
    decisão de produto (#286) e o destino continua sendo o banco.
    """
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    db.pocket_deposit_from_account(user_id, "viagem", 50, "do banco",
                                   funding_source=funding.to_db_arg(banco))

    h_pockets.withdraw(user_id, "retirei 120 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 120})
    destino = _efeitos(user_id, "saque_caixinha")["funding_source"]
    assert destino and destino["kind"] == funding.BANK, destino

    # esvaziar leva o resto: mesma regra, e é o outro ramo do handler
    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})
    destino = _efeitos(user_id, "saque_caixinha")["funding_source"]
    assert destino and destino["kind"] == funding.BANK, destino


def test_tolerancia_de_esvaziar_arrasta_o_lote_seguinte(user_id):
    """`[carteira 100,00; banco 0,005]` e pedido de 100: o saque real varre os DOIS
    (`WITHDRAW_ALL_TOLERANCE`), então o destino tem de ver os dois. Sem a mesma
    tolerância dentro da transação o destino veria um lote só, viraria Carteira e
    creditaria R$ 0,005 que continuam no banco — erraria justamente no `esvaziar`."""
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    db.pocket_deposit_from_account(user_id, "viagem", Decimal("0.005"), "do banco",
                                   funding_source=funding.to_db_arg(banco))

    h_pockets.withdraw(user_id, "retirei 100 da caixinha viagem",
                       {"pocket_name": "viagem", "amount": 100})

    saq = _efeitos(user_id, "saque_caixinha")
    assert len(saq["tax_summary"]["lots"]) == 2, (
        "a tolerância não arrastou o segundo lote: sem os dois o teste não mede nada")
    assert saq["funding_source"]["kind"] == funding.BANK, saq["funding_source"]


def test_deposito_do_banco_no_meio_do_saque_nao_credita_a_carteira(user_id):
    """A janela entre decidir o destino e gravar o saque (TOCTOU).

    Enquanto a decisão morava FORA da transação, um depósito com origem no BANCO que
    commitasse nesse intervalo entrava no FIFO do saque, e o crédito da Carteira somava
    dinheiro que continua no banco: medido 1050,00 na Carteira (R$ 50 CRIADOS) contra
    900,00 sem a janela. `db.destination_of_lots` fecha isso sob o `for update`.

    Tirar a chamada de `db/pockets.py` deixa vermelho.
    """
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "da carteira", funding_source=None)
    assert float(db.get_balance(user_id)) == 900.0

    real = db.pocket_withdraw_to_account

    def intercalado(*a, **kw):
        # commita DEPOIS do destino resolvido e ANTES do saque abrir a transação
        db.pocket_deposit_from_account(user_id, "viagem", 50, "do banco",
                                       funding_source=funding.to_db_arg(banco))
        return real(*a, **kw)

    with patch.object(db, "pocket_withdraw_to_account", intercalado):
        msg = h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    # SENTIDO B da mensagem (#286): antes do saque o único lote era da Carteira, e o
    # texto lia esse estado — o usuário via a caixinha esvaziar, a Conta parada e
    # NENHUMA explicação. É a forma exata do relato da #282, do lado da mensagem.
    assert "Open Finance sincronizar" in msg, msg

    carteira = float(db.get_balance(user_id))
    assert carteira == 900.0, (
        f"Carteira={carteira:.2f}: recebeu R$ {carteira - 900:.2f} num saque que "
        f"consumiu lote do BANCO — R$ 50,00 passam a contar duas vezes")
    efeitos = _efeitos(user_id, "saque_caixinha")
    assert efeitos["delta_conta"] == 0, efeitos["delta_conta"]
    assert efeitos["funding_source"]["kind"] == funding.BANK, efeitos["funding_source"]
    # O PREÇO desta guarda, registrado em vez de escondido (decisão do dono, #286).
    # O saque atravessou as duas origens, e origens diferentes vão para o banco: os
    # R$ 100 que tinham saído da Carteira vão junto. Patrimônio no Pig 1000 -> 900, e
    # o usuário não vê o dinheiro voltar. Continua sendo a troca certa — CRIAR R$ 50
    # infla o consolidado de todos e o sync depois não bate —, mas é uma troca, não
    # um conserto. A decisão dentro da transação NÃO muda este número: os dois lotes
    # são consumidos de fato, e a divisão do resgate é que segue sem decisão.
    # Os 8 testes irmãos da #282 afirmam o patrimônio; este afirmava só a Carteira.
    assert _patrimonio(user_id) == pytest.approx(900.0)


def test_aporte_do_banco_no_meio_do_resgate_nao_credita_a_carteira(user_id):
    """O gêmeo do de cima no investimento — a guarda é uma chamada em cada
    função de saque, e sem este teste a de `db/investments.py` podia sumir sem
    uma linha vermelha (medido: mutar só ela deixava 72 verdes)."""
    _seed_carteira_e_banco(user_id)
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.investment_deposit_from_account(user_id, "CDB XP", 100, "da carteira",
                                       funding_source=None)
    assert float(db.get_balance(user_id)) == 900.0

    real = db.investment_withdraw_to_account

    def intercalado(*a, **kw):
        db.investment_deposit_from_account(user_id, "CDB XP", 50, "do banco",
                                           funding_source=funding.to_db_arg(banco))
        return real(*a, **kw)

    with patch.object(db, "investment_withdraw_to_account", intercalado):
        msg = h_investments.withdraw(user_id, "resgatar tudo do CDB XP",
                                     {"investment_name": "CDB XP", "want_all": True})

    # SENTIDO B no investimento, onde o texto é pior: o `, para o <banco>` fica DENTRO
    # da linha de sucesso, então a previsão errada não só omitia o aviso — ela afirmava
    # o destino errado no meio da confirmação.
    assert "Open Finance sincronizar" in msg, msg
    assert f", para o {banco['label']}" in msg, msg

    carteira = float(db.get_balance(user_id))
    assert carteira == 900.0, (
        f"Carteira={carteira:.2f}: recebeu R$ {carteira - 900:.2f} num saque que "
        f"consumiu lote do BANCO — R$ 50,00 passam a contar duas vezes")
    assert _efeitos(user_id, "resgate_investimento")["delta_conta"] == 0

def test_isolamento_pelo_lot_id_de_outro_usuario(user_id):
    """A outra metade do §0 nesta query: os ids de lote são globais, então um
    `lot_id` de jsonb livre pode apontar para o lote de OUTRO usuário. Só o
    `d.user_id = %s` do `left join` de `destination_of_lots` segura — mutar essa
    condição deixa vermelho; o teste de isolamento acima passa com a mutação,
    porque lá o vazamento seria pelo alvo, não pelo `lot_id`.
    """
    import uuid as _uuid

    outro = int(_uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    _seed_carteira_e_banco(outro)                       # A: depósito da Carteira
    db.create_pocket(outro, "viagem")
    db.pocket_deposit_from_account(outro, "viagem", 100, "carteira de A", funding_source=None)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos->'pocket_lot_create'->>'lot_id' as l from launches "
                    "where user_id=%s and tipo='deposito_caixinha'", (outro,))
        lote_de_a = cur.fetchone()["l"]

    _seed_carteira_e_banco(user_id)                     # B: depósito do banco
    db.create_pocket(user_id, "viagem")
    banco = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    db.pocket_deposit_from_account(user_id, "viagem", 100, "banco de B",
                                   funding_source=funding.to_db_arg(banco))
    with db.get_conn() as conn, conn.cursor() as cur:   # B aponta para o lote de A
        cur.execute("update launches set efeitos = jsonb_set(efeitos,"
                    "'{pocket_lot_create,lot_id}', %s) where user_id=%s and "
                    "tipo='deposito_caixinha'", (Jsonb(int(lote_de_a)), user_id))
        conn.commit()

    h_pockets.withdraw(outro, "esvaziar caixinha viagem", {"pocket_name": "viagem"})
    assert _efeitos(outro, "saque_caixinha")["funding_source"] is None, (
        "A foi contaminado pelo lançamento de B")

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})
    destino_b = _efeitos(user_id, "saque_caixinha")["funding_source"]
    assert destino_b and destino_b["kind"] == funding.BANK, (
        f"o lote de B devia virar órfão e cair na regra de sempre, não virar {destino_b}")


# ── o recorte por valor nas QUATRO superfícies silenciosas ───────────────────
#
# `test_saque_parcial_consome_so_o_lote_da_carteira` cobria o WhatsApp e só ele.
# Medido antes desta bateria: revertendo as quatro chamadas não-WhatsApp para a
# assinatura sem `amount`, os 73 testes ficavam VERDES enquanto a sonda mostrava
# `Carteira 1000 -> 900` em cada uma — o dinheiro sumia sem uma linha vermelha.
#
# Com a decisão dentro da transação (`db.destination_of_lots`) estes testes medem
# mais: eles passam pelo caminho que DECIDE, não pela dica.

def _lotes_carteira_e_banco(user_id: int, tipo: str, alvo: str,
                            carteira: float = 100.0, banco: float = 50.0):
    """`[carteira 100; banco 50]` no alvo, com Carteira de 1000 e banco conectado.

    O depósito vai pelo `db` de propósito: o que está sendo medido é o SAQUE de cada
    superfície. Pela superfície, o depósito escolheria a origem sozinho
    (`resolve_deterministic` prefere a Carteira sempre que ela cobre) e não daria
    lote nenhum do banco.
    """
    _seed_carteira_e_banco(user_id)
    conta = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
    dep = (db.pocket_deposit_from_account if tipo == "deposito_caixinha"
           else db.investment_deposit_from_account)
    dep(user_id, alvo, carteira, "da carteira", funding_source=None)
    dep(user_id, alvo, banco, "do banco", funding_source=funding.to_db_arg(conta))
    assert float(db.get_balance(user_id)) == 900.0


def _assert_voltou_pra_carteira(user_id: int, tipo_saq: str, antes: float):
    saq = _efeitos(user_id, tipo_saq)
    assert saq["funding_source"] is None, (
        f"o saque consumiu só o lote da Carteira e foi para {saq['funding_source']}")
    assert float(db.get_balance(user_id)) == 1000.0, "a Carteira não recebeu de volta"
    assert _patrimonio(user_id) == pytest.approx(antes), (
        f"EVAPOROU R$ {antes - _patrimonio(user_id):.2f}")


def test_saque_parcial_com_duas_origens_pelo_dashboard(user_id):
    db.create_pocket(user_id, "viagem")
    _lotes_carteira_e_banco(user_id, "deposito_caixinha", "viagem")
    antes = _patrimonio(user_id)
    client, headers = _dashboard_client(user_id, "b1p@t.com")

    r = client.post(f"/pockets/{user_id}/viagem/withdraw", json={"amount": 100}, headers=headers)
    assert r.status_code == 200, r.text
    _assert_voltou_pra_carteira(user_id, "saque_caixinha", antes)


def test_saque_parcial_com_duas_origens_pelo_chat_da_ia(user_id):
    from core.services.ai_chat.tools.pockets import _pocket_withdraw_execute

    db.create_pocket(user_id, "viagem")
    _lotes_carteira_e_banco(user_id, "deposito_caixinha", "viagem")
    antes = _patrimonio(user_id)

    _pocket_withdraw_execute(user_id, {"pocket_name": "viagem", "amount": 100})
    _assert_voltou_pra_carteira(user_id, "saque_caixinha", antes)


def test_resgate_parcial_com_duas_origens_pelo_dashboard(user_id):
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    _lotes_carteira_e_banco(user_id, "aporte_investimento", "CDB XP")
    antes = _patrimonio(user_id)
    client, headers = _dashboard_client(user_id, "b1i@t.com")

    r = client.post(f"/investments/{user_id}/withdraw",
                    json={"name": "CDB XP", "amount": 100}, headers=headers)
    assert r.status_code == 200, r.text
    _assert_voltou_pra_carteira(user_id, "resgate_investimento", antes)


def test_resgate_parcial_com_duas_origens_pelo_chat_da_ia(user_id):
    from core.services.ai_chat.tools.investments import _investment_withdraw_execute

    db.create_investment(user_id, "CDB XP", 0.12, "yearly")
    _lotes_carteira_e_banco(user_id, "aporte_investimento", "CDB XP")
    antes = _patrimonio(user_id)

    _investment_withdraw_execute(user_id, {"name": "CDB XP", "amount": 100})
    _assert_voltou_pra_carteira(user_id, "resgate_investimento", antes)


# ── C1: o accrual muda QUAIS lotes o saque consome ───────────────────────────

def _semeia_cdi(inicio, fim, pct_ao_dia: str = "0.05") -> list:
    """CDI em `market_rates` para todo dia útil de [inicio, fim]. Devolve o que inseriu.

    A tabela está VAZIA no banco de teste, então TODO o resto deste arquivo roda com
    rendimento ZERO — e rendimento zero é justamente o que esconde o C1: sem accrual,
    a previsão de fora e o consumo de dentro nunca divergem.

    Cache completo + cauda fresca fazem `_get_cdi_daily_map` devolver sem ir ao BCB
    (ver `_sgs_cache_covers`/`_sgs_tail_is_fresh`) — a saída HTTP é bloqueada na suíte.

    A tabela é GLOBAL, sem `user_id`: quem semeia limpa, senão os outros testes passam
    a render juros. E limpa só o que inseriu (`returning`), nunca a janela inteira —
    com `PYTEST_DB_ISOLATION=0` a janela pegaria linhas de CDI que já estavam lá.
    """
    from utils_date import is_br_business_day

    dias, d = [], inicio
    while d <= fim:
        if is_br_business_day(d):
            dias.append(d)
        d += timedelta(days=1)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into market_rates(code, ref_date, value) "
            "select 'CDI', d, %s from unnest(%s::date[]) d "
            "on conflict (code, ref_date) do nothing returning ref_date",
            (pct_ao_dia, dias))
        inseridos = [r["ref_date"] for r in cur.fetchall()]
        conn.commit()
    return inseridos


def _apaga_cdi(dias) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from market_rates where code='CDI' and ref_date = any(%s)",
                    (dias,))
        conn.commit()


def test_C1_o_accrual_muda_quais_lotes_o_saque_consome(user_id):
    """A #282 literal que sobrava depois do recorte por valor.

    `[carteira 1000; banco 500]` parada há 60 dias, saque de 1005:

    * a leitura de FORA vê os saldos ANTES do accrual (1000 e 500), então prevê que o
      saque atravessa os dois lotes → duas origens → regra de sempre → BANCO;
    * o saque real accrua primeiro: o lote da Carteira virou ~1021 e 1005 cabe nele,
      então consome UM lote só, da Carteira.

    Com a decisão fora, R$ 1.005 saíam da Carteira e R$ 0,00 voltavam. Com ela dentro
    (`db.destination_of_lots`), o destino é o consumo de FATO.

    `tax_summary["lots"]` é quem prende o mecanismo: 1 lote = o accrual rodou e mudou
    o prefixo; 2 lotes = o rendimento não aconteceu e o teste não mediria nada.

    É também o SENTIDO A da mensagem (#286): a Conta sobe R$ 1.000+ e o texto não
    pode dizer que o dinheiro "aparece quando o Open Finance sincronizar".
    """
    hoje = date.today()
    inicio = hoje - timedelta(days=60)
    semeados = _semeia_cdi(inicio, hoje)
    try:
        _seed_carteira_e_banco(user_id, 2000.0)
        db.create_pocket(user_id, "viagem")
        conta = [f for f in funding.list_sources(user_id) if f["kind"] == funding.BANK][0]
        db.pocket_deposit_from_account(user_id, "viagem", 1000, "da carteira", funding_source=None)
        db.pocket_deposit_from_account(user_id, "viagem", 500, "do banco",
                                       funding_source=funding.to_db_arg(conta))
        assert float(db.get_balance(user_id)) == 1000.0
        with db.get_conn() as conn, conn.cursor() as cur:   # 60 dias sem accrual
            cur.execute("update pocket_lots set opened_at=%s, last_date=%s where user_id=%s",
                        (inicio, inicio, user_id))
            cur.execute("update pockets set last_interest_date=%s where user_id=%s",
                        (inicio, user_id))
            conn.commit()

        msg = h_pockets.withdraw(user_id, "retirei 1005 da caixinha viagem",
                                 {"pocket_name": "viagem", "amount": 1005})

        saq = _efeitos(user_id, "saque_caixinha")
        assert len(saq["tax_summary"]["lots"]) == 1, (
            "o accrual não rodou: sem rendimento o saque atravessa os dois lotes e o "
            "teste fica cego ao C1")
        assert saq["funding_source"] is None, (
            f"R$ 1.005 saíram da Carteira e o saque foi para {saq['funding_source']}")
        assert float(saq["delta_conta"]) > 1000
        assert float(db.get_balance(user_id)) == pytest.approx(
            1000.0 + float(saq["delta_conta"])), "a Carteira não recebeu de volta"

        # SENTIDO A: o razão creditou a Carteira, então a mensagem não pode avisar
        # que a entrada só aparece no sync. Enquanto o texto lia a previsão, esta
        # resposta trazia o aviso com o `delta_conta` positivo logo acima dele.
        assert "Open Finance sincronizar" not in msg, msg
        assert "🏦 Conta:" in msg, msg
    finally:
        _apaga_cdi(semeados)


def test_lote_orfao_consumido_nao_credita_a_carteira(user_id):
    """O órfão pelo caminho que DECIDE — hoje o único que existe.

    Medido quando ainda havia leitura de fora: tirar o `not orfao` de
    `db.destination_of_lots` deixava os 78 verdes, porque a cobertura do órfão
    estava toda na previsão.

    Lote sem lançamento criador existe de verdade (backfill de `db/schema.py`,
    `_ensure_pocket_lots` de `db/pockets.py`). O `coalesce(..., 'carteira')` o faz
    parecer Carteira, e aí o saque creditaria R$ 100 que nunca saíram dela.
    """
    _seed_carteira_e_banco(user_id)
    db.create_pocket(user_id, "viagem")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from pockets where user_id=%s and name='viagem'", (user_id,))
        pocket_id = cur.fetchone()["id"]
        cur.execute(
            "insert into pocket_lots(user_id, pocket_id, principal_initial, "
            "principal_remaining, balance, opened_at, last_date, status) "
            "values (%s,%s,100,100,100,current_date,current_date,'open')",
            (user_id, pocket_id),
        )
        cur.execute("update pockets set balance=100 where id=%s and user_id=%s",
                    (pocket_id, user_id))
        conn.commit()

    h_pockets.withdraw(user_id, "esvaziar caixinha viagem", {"pocket_name": "viagem"})

    saq = _efeitos(user_id, "saque_caixinha")
    assert saq["funding_source"] is not None, "órfão virou Carteira e criou dinheiro"
    assert saq["delta_conta"] == 0
    assert float(db.get_balance(user_id)) == 1000.0, "a Carteira foi creditada por um órfão"
