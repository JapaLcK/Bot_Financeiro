"""Regra da quebra da linha do patrimônio (`db.patrimonio.quebra`) e a montagem dos trechos
(`api.v2.patrimonio.trechos`): puras, sem banco. Cada linha da tabela de estados × eventos
do plano (docs/plano-etapa6-patrimonio.md) é uma base montada à mão."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from api.v2.patrimonio import trechos
from core.services.pluggy_health import connection_ui_state
from db.open_finance_state import _TERMINAL
from db.patrimonio import _FORA_DO_TOTAL, quebra


def base(conexoes=None, contas=("c1",), posicoes=("p1",), moeda=0, v=1, **fora):
    return {"v": v, "contas": list(contas), "posicoes": list(posicoes),
            "conexoes": {"1": "updated"} if conexoes is None else conexoes,
            "fora": {"moeda": moeda, "resgatada": 0, "pausada": 0, "caixinha_espelhada": 0, **fora}}


B = base()
NAO_QUEBRA = {
    "identicas": (B, base()),
    **{f"estado_{e}": (B, base({"1": e})) for e in
       ("partial", "updating", "error_recoverable", "needs_user_action", "item_missing", "no_accounts")},
    "cdb_novo": (B, base(posicoes=("p1", "p2"))),
    "resgate": (B, base(posicoes=())),
    "reconexao_mesmos_ids": (B, base({"2": "updated"})),
    "fora_resgatada": (B, base(resgatada=1)),
    "fora_pausada": (B, base(pausada=1)),
    "fora_caixinha_espelhada": (B, base(caixinha_espelhada=1)),
    "banco_sem_coleta": (base({}, contas=(), posicoes=()), base({"1": "updated"}, contas=(), posicoes=())),
    # limites declarados (docs/CLAUDE.md): não quebram, o salto entra na variação
    "limite_declarado_banco_so_investimento_sem_coleta": (base(contas=(), posicoes=()),
                                                          base(contas=(), posicoes=("p1",))),
    "limite_declarado_moeda_compensada_so_em_posicoes": (base(posicoes=("p1", "p2")),
                                                         base(posicoes=("p1", "p3"))),
}
QUEBRA = {
    "v_mudou": (B, base(v=2)),
    "banco_entra": (base({}, contas=(), posicoes=()), base({"1": "updated"})),
    "pausou": (B, base({"1": "paused"}, contas=(), posicoes=())),
    "removido": (B, base({"1": "removed"}, contas=(), posicoes=())),
    "voltou": (base({"1": "paused"}, contas=(), posicoes=()), B),
    # banco só de investimento (sem conta BRL): só o estado da conexão separa os dois lados
    "so_investimento_pausou": (base(contas=()), base({"1": "paused"}, contas=(), posicoes=())),
    "so_investimento_removido": (base(contas=()), base({"1": "removed"}, contas=(), posicoes=())),
    "so_investimento_voltou": (base({"1": "paused"}, contas=(), posicoes=()), base(contas=())),
    "conexao_some": (B, base({}, contas=(), posicoes=())),
    "conta_brl_para_usd": (B, base(contas=(), moeda=1)),
    "posicao_brl_para_usd": (B, base(posicoes=(), moeda=1)),
    "ids_novos_reconexao": (B, base({"2": "updated"}, contas=("c9",), posicoes=("p9",))),
    "conta_nova_no_mesmo_banco": (B, base(contas=("c1", "c2"))),
    "troca_de_moeda_compensando_contagem_em_contas": (base(contas=("c1", "c2")),
                                                      base(contas=("c1", "c3"))),
    "posicao_usd_entra": (B, base(moeda=1)),
    "ilegivel_none": (B, None),
    "ilegivel_none_antes": (None, B),
    "ilegivel_vazia": (B, {}),
    "ilegivel_sem_fora": (B, {k: v for k, v in B.items() if k != "fora"}),
    "ilegivel_dos_dois_lados": ({}, {}),
}


@pytest.mark.parametrize("a,b", NAO_QUEBRA.values(), ids=NAO_QUEBRA)
def test_nao_quebra(a, b):
    assert quebra(a, b) is False


@pytest.mark.parametrize("a,b", QUEBRA.values(), ids=QUEBRA)
def test_quebra(a, b):
    assert quebra(a, b) is True


# --- montagem dos trechos ---
H = date(2026, 10, 14)
D = lambda n: H - timedelta(days=n)  # noqa: E731
BQ = base(contas=("c9",))  # quebra com B


def serie(*itens):
    return [(d, b, Decimal(t)) for d, b, t in itens]


def test_so_hoje_nao_inventa_variacao():
    assert trechos(serie((H, B, "10"))) == ([{"desde": "2026-10-14", "ate": "2026-10-14"}], None)


def test_continuo_varia_do_primeiro_ponto():
    t, v = trechos(serie((D(2), B, "10.00"), (D(1), B, "12.50"), (H, B, "11.25")))
    assert t == [{"desde": "2026-10-12", "ate": "2026-10-14"}]
    assert v == {"valor": Decimal("1.25"), "desde": "2026-10-12", "ate": "2026-10-14"}


def test_zero_real_e_zero_nao_null():
    t, v = trechos(serie((D(1), B, "10"), (H, B, "10")))
    assert v["valor"] == Decimal("0.00")


def test_buraco_de_dias_nao_quebra():
    t, v = trechos(serie((D(30), B, "1"), (H, B, "3")))
    assert t == [{"desde": "2026-09-14", "ate": "2026-10-14"}] and v["valor"] == 2


def test_quebra_no_meio_fecha_trecho_e_variacao_so_do_atual():
    t, v = trechos(serie((D(3), B, "1"), (D(2), B, "2"), (D(1), BQ, "100"), (H, BQ, "105")))
    assert t == [{"desde": "2026-10-11", "ate": "2026-10-12"}, {"desde": "2026-10-13", "ate": "2026-10-14"}]
    assert v == {"valor": Decimal("5.00"), "desde": "2026-10-13", "ate": "2026-10-14"}


def test_quebra_entre_a_ultima_foto_e_hoje_deixa_trecho_atual_so_hoje():
    t, v = trechos(serie((D(1), B, "1"), (H, BQ, "100")))
    assert t == [{"desde": "2026-10-13", "ate": "2026-10-13"}, {"desde": "2026-10-14", "ate": "2026-10-14"}]
    assert v is None


def test_valor_e_a_diferenca_dos_totais_em_centavos():
    # 10.004 -> 10.00 e 11.006 -> 11.01: 1.01, não 1.002 nem 1.00
    _, v = trechos(serie((D(1), B, "10.004"), (H, B, "11.006")))
    assert v["valor"] == Decimal("1.01")


def test_fora_do_total_acompanha_o_terminal_do_open_finance():
    ui = lambda s: connection_ui_state({"status": s})["state"]  # noqa: E731
    assert all(ui(s) in _FORA_DO_TOTAL for s in _TERMINAL)
    assert not any(ui(s) in _FORA_DO_TOTAL for s in ("UPDATED", "UPDATING", "LOGIN_ERROR", "OUTDATED"))
