"""A correção de LEITURA some sozinha quando o espelho do banco sai do recorte.

O conserto da fusão não escreve nada: `MERGED_WALLET_DELTA_SQL` soma o
`delta_conta` do manual fundido de volta na Carteira **só enquanto** a conta do
banco está dentro de `BANK_ACCOUNTS_SQL`. É o que torna a abordagem por leitura
correta onde a por escrita falhava: pausa por trial vencido, `enforce_of_bank_limits`,
`DELETED`, `type` e `currency` NÃO passam por porta nenhuma que pudesse "desfazer"
uma supressão — e aqui não há o que desfazer.

Os casos 1–5 são verdes COM e SEM a correção, de propósito: são eles que provam a
evaporação. **Não injete o controle negativo neles** (CLAUDE.md §3: a injeção vai
onde discrimina). Quem discrimina é o `caminho_1` de
`tests/test_fusao_of_nao_conta_duas_vezes.py` e o caso 8 daqui (o snapshot do
dashboard, cujo Passo 3 a suíte inteira não cobriria de outro jeito).

Roteiro comum: banco com 114,88 → "Gastei 1 real com a barbara" → sync com a tx
de -1 e espelho 113,88 → e então o evento.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, of_tx_pendente, saldo_bruto,
    sincroniza, tx, uid_pro, ultimo_launch,
)


def _funde_um_real(uid: int) -> int:
    """Cenário do relato, já fundido (com confirmação do usuário). Devolve a
    connection_id.

    Lançamento manual nunca funde em silêncio: o importador rebaixa o casamento
    a 'ask' e a fusão só acontece com `confirm_reconciliation`. As
    pré-condições aqui são de propósito INSENSÍVEIS à correção (a fusão
    aconteceu; o banco não foi escrito): é o que deixa os casos 1–5 verdes com e
    sem ela, provando a evaporação em vez de medi-la de novo (CLAUDE.md §3).
    """
    hoje = today_tz()
    conexao = conecta_banco(uid, "114.88")
    manda(uid, "Gastei 1 real com a barbara")
    sincroniza(conexao, uid, "113.88",
               [tx(uid, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    assert saldo_bruto(uid) == Decimal("-1"), "a abordagem por LEITURA não escreve"
    return conexao


def _sql(query: str, params: tuple):
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()


# ── 1. pausa por trial vencido ─────────────────────────────────────────────

def test_1_pausa_por_trial_vencido_volta_a_contar_o_gasto(uid_pro, ia_fora, monkeypatch):
    """`enforce_of_bank_limits` com teto 0 — o caso do trial que morreu."""
    from core.services import open_finance_trial_expiry as sweep
    from core.services import plan_service

    conexao = _funde_um_real(uid_pro)

    monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
    # teto 0 SÓ para este usuário: a varredura é global e derrubaria a conexão
    # de outro teste do mesmo banco.
    monkeypatch.setattr(
        plan_service, "get_user_limits",
        lambda uid, *a, **k: {"of_banks_max": 0 if uid == uid_pro else None})
    monkeypatch.setattr(sweep, "create_pluggy_api_key", lambda: "key-fake")
    monkeypatch.setattr(sweep, "delete_pluggy_item", lambda *a, **k: True)

    rep = sweep.enforce_of_bank_limits()
    assert rep["paused"] >= 1, rep

    # o espelho saiu do recorte: o gasto volta a ser contado pela Carteira
    assert consolidado(uid_pro) == (-1.0, -1.0)


# ── 2. pausa por limite de plano COM um segundo banco vivo ─────────────────

def test_2_limite_de_plano_com_outro_banco_vivo(uid_pro, ia_fora, monkeypatch):
    """O pior caso: `of_bank_count > 0` continua verdadeiro, o gate segue ligado
    e a linha mente com cara certa. O gasto tem de voltar para a Carteira."""
    from core.services import open_finance_trial_expiry as sweep
    from core.services import plan_service

    conexao = _funde_um_real(uid_pro)

    # segundo banco, conectado DEPOIS (é o mais recente que cai... e o teto 1
    # mantém o primeiro: ordena por id e pausa do fim para o começo)
    item2 = db.save_pluggy_open_finance_item(uid_pro, {
        "id": f"item-of2-{uid_pro}", "connector": {"id": 611, "name": "Itau"},
        "status": "UPDATED",
    })
    db.save_open_finance_sync(item2["id"], [{
        "provider_account_id": f"acc-of2-{uid_pro}", "name": "Itau Conta",
        "type": "BANK", "subtype": "CHECKING_ACCOUNT", "currency": "BRL",
        "balance": Decimal("200.00"), "raw": {}, "transactions": [],
    }])

    # teto 1: cai o mais recente (o Itaú)... então forço o inverso pausando o
    # Nubank via id — o que interessa é o ESPELHO do banco fundido sair.
    monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
    monkeypatch.setattr(
        plan_service, "get_user_limits",
        lambda uid, *a, **k: {"of_banks_max": 1 if uid == uid_pro else None})
    monkeypatch.setattr(sweep, "create_pluggy_api_key", lambda: "key-fake")
    monkeypatch.setattr(sweep, "delete_pluggy_item", lambda *a, **k: True)
    sweep.enforce_of_bank_limits()

    # o Itaú (mais recente) foi pausado; o Nubank fundido continua vivo
    cb = db.get_consolidated_balance(uid_pro)
    assert int(cb["of_bank_count"]) == 1, cb
    assert consolidado(uid_pro) == (113.88, 0.0)

    # agora o Nubank também sai: com o Itaú JÁ fora, sobra a Carteira
    db.pause_open_finance_connection(conexao)
    cb = db.get_consolidated_balance(uid_pro)
    assert int(cb["of_bank_count"]) == 0, cb
    assert consolidado(uid_pro) == (-1.0, -1.0)


def test_2b_um_banco_pausado_outro_vivo_o_gate_continua_ligado(uid_pro, ia_fora):
    """O recorte é por CONTA, não por usuário: pausar o banco que fundiu com
    outro banco ainda conectado tem de devolver o gasto mesmo com o gate ligado
    (`of_bank_count` segue > 0 — a linha que mente com cara certa)."""
    conexao = _funde_um_real(uid_pro)

    item2 = db.save_pluggy_open_finance_item(uid_pro, {
        "id": f"item-of2-{uid_pro}", "connector": {"id": 611, "name": "Itau"},
        "status": "UPDATED",
    })
    db.save_open_finance_sync(item2["id"], [{
        "provider_account_id": f"acc-of2-{uid_pro}", "name": "Itau Conta",
        "type": "BANK", "subtype": "CHECKING_ACCOUNT", "currency": "BRL",
        "balance": Decimal("200.00"), "raw": {}, "transactions": [],
    }])

    db.pause_open_finance_connection(conexao)

    cb = db.get_consolidated_balance(uid_pro)
    assert int(cb["of_bank_count"]) == 1, "o Itaú continua conectado"
    assert consolidado(uid_pro) == (199.0, -1.0)


# ── 3/4/5: os predicados de BANK_ACCOUNTS_SQL, um a um ────────────────────

def test_3_status_deleted_volta_a_contar_o_gasto(uid_pro, ia_fora):
    conexao = _funde_um_real(uid_pro)
    _sql("update open_finance_connections set status='DELETED' where id=%s", (conexao,))
    assert consolidado(uid_pro) == (-1.0, -1.0)


def test_4_conta_que_deixa_de_ser_BANK_volta_a_contar_o_gasto(uid_pro, ia_fora):
    conexao = _funde_um_real(uid_pro)
    _sql("update open_finance_accounts set type='CREDIT' where connection_id=%s", (conexao,))
    assert consolidado(uid_pro) == (-1.0, -1.0)


def test_5_conta_fora_do_BRL_volta_a_contar_o_gasto(uid_pro, ia_fora):
    conexao = _funde_um_real(uid_pro)
    _sql("update open_finance_accounts set currency='USD' where connection_id=%s", (conexao,))
    assert consolidado(uid_pro) == (-1.0, -1.0)


# ── 6. sincronizar duas vezes não move o consolidado ───────────────────────

def test_6_sincronizar_duas_vezes_nao_move_o_consolidado(uid_pro, ia_fora):
    """Idempotência SEM escrita: a segunda passada relê o mesmo vínculo."""
    hoje = today_tz()
    conexao = _funde_um_real(uid_pro)

    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, conexao)
    db.import_open_finance_launches(uid_pro, conexao)

    assert consolidado(uid_pro) == (113.88, 0.0)
    assert saldo_bruto(uid_pro) == Decimal("-1")


# ── 7. cartão de crédito não funde e a correção não se aplica ─────────────

def test_7_transacao_de_cartao_nao_aciona_a_correcao(uid_pro, ia_fora):
    """`import_open_finance_launches` pula `account_type != 'BANK'` antes de
    reconciliar, e `BANK_ACCOUNTS_SQL` tem `upper(a.type)='BANK'`: a tx do
    cartão não funde, o gasto do bolso continua debitado."""
    hoje = today_tz()
    item = db.save_pluggy_open_finance_item(uid_pro, {
        "id": f"item-card-{uid_pro}", "connector": {"id": 612, "name": "Nubank"},
        "status": "UPDATED",
    })
    db.save_open_finance_sync(item["id"], [{
        "provider_account_id": f"acc-card-{uid_pro}", "name": "Nubank Cartao",
        "type": "CREDIT", "subtype": "CREDIT_CARD", "currency": "BRL",
        "balance": Decimal("0"), "raw": {},
        "transactions": [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")],
    }])

    manda(uid_pro, "Gastei 1 real com a barbara")
    db.import_open_finance_launches(uid_pro, item["id"])

    cb = db.get_consolidated_balance(uid_pro)
    assert int(cb["of_bank_count"]) == 0, "cartão não é conta BANK"
    assert consolidado(uid_pro) == (-1.0, -1.0)


# ── 8. o snapshot do dashboard, que NÃO passa por get_consolidated_balance ─

def test_8_snapshot_do_dashboard_fecha_com_o_consolidado(uid_pro, ia_fora):
    """Sem este caso o Passo 3 podia nem existir e a suíte ficava verde: o
    `get_financial_data` monta o snapshot com query própria e o lado manual vem
    de `account["balance"]` cru. É a tela do relato ("Carteira R$ -1,00")."""
    _funde_um_real(uid_pro)

    data = asyncio.run(dashboard.get_financial_data(uid_pro))
    cb = db.get_consolidated_balance(uid_pro)

    assert data["balance"] == pytest.approx(float(cb["manual"])), \
        "a Carteira do dashboard divergiu da do /saldo"
    assert data["balance"] + data["of_bank_balance"] == pytest.approx(
        float(cb["consolidated"]))
    assert data["balance"] == pytest.approx(0.0), "a tela mostrava -1,00"


# ── 9. ajustar a Carteira para 0 não pode fazer 1 real aparecer ───────────

def test_9_ajustar_para_zero_com_fusao_viva_nao_cria_dinheiro(uid_pro, ia_fora):
    """O produto pede que quem conecta banco zere a Carteira. Com a fusão viva,
    o cru vale -1: `0 - (-1)` gravava uma receita de 1 e a tela passava a
    mostrar 1,00 — o usuário "zerou" e viu dinheiro aparecer."""
    from frontend.finance_bot_websocket_custom import (
        AdjustBalancePayload, adjust_balance_route,
    )

    _funde_um_real(uid_pro)

    class _Req:  # `_authorize_dashboard_access` é monkeypatchado abaixo
        pass

    import frontend.finance_bot_websocket_custom as mod
    original = mod._authorize_dashboard_access
    mod._authorize_dashboard_access = lambda *a, **k: None
    try:
        r = asyncio.run(adjust_balance_route(
            _Req(), uid_pro, AdjustBalancePayload(target_balance=0)))
    finally:
        mod._authorize_dashboard_access = original

    assert r["delta"] == 0.0, r
    assert r["launch_id"] is None, "não podia gravar lançamento nenhum"
    assert r["balance"] == pytest.approx(0.0)
    assert consolidado(uid_pro) == (113.88, 0.0)


# ── 10. funding: a tela e a guarda do db/ concordam ───────────────────────

def test_10_funding_e_a_guarda_do_db_concordam(uid_pro, ia_fora):
    """A queixa que o `funding.py` foi escrito para consertar, renascida do
    outro lado: `list_sources` oferece a Carteira corrigida e a guarda DENTRO
    da transação lia `accounts.balance` cru, recusando o que o app autoriza."""
    from core.services import funding

    conexao = _funde_um_real(uid_pro)
    # Carteira com dinheiro de verdade + o gasto fundido: cru 9,00, exibido 10,00
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    assert saldo_bruto(uid_pro) == Decimal("9")

    db.create_pocket(uid_pro, "Viagem")
    carteira = [f for f in funding.list_sources(uid_pro) if f["kind"] == funding.CARTEIRA][0]
    assert float(carteira["balance"]) == pytest.approx(10.0)

    # valor ENTRE o cru (9) e o corrigido (10): sem o Passo 6 vira
    # INSUFFICIENT_ACCOUNT dentro da transação
    db.pocket_deposit_from_account(uid_pro, "Viagem", 9.5, "aporte")

    assert saldo_bruto(uid_pro) == Decimal("-0.5")
    assert consolidado(uid_pro) == (114.38, 0.5)


def test_10b_a_guarda_continua_recusando_o_que_nao_cabe(uid_pro, ia_fora):
    """POSITIVO do passo 6: a guarda não vira peneira. 10,01 não cabe em 10,00."""
    from core.services import funding

    _funde_um_real(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    db.create_pocket(uid_pro, "Viagem")

    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        db.pocket_deposit_from_account(uid_pro, "Viagem", 10.01, "aporte")
