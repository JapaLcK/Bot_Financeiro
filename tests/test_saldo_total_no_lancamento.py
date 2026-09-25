"""A linha de saldo da resposta de lançamento fala o mesmo número que o /saldo.

O DEFEITO: com banco conectado, "Gastei 1 real com a barbara" respondia
"🏦 Saldo: R$ -1,00" — a Carteira, não o consolidado que o dashboard e o /saldo
mostram. Decisão do dono: com banco conectado E o gate ligado, a linha vira
"💰 Saldo total: {consolidado}"; sem banco ou com o gate desligado, ela continua
EXATAMENTE a de hoje.

O assert compara as DUAS superfícies na mesma sessão (CLAUDE.md §0.7), nunca um
literal escrito à mão: o número da resposta de lançamento tem de ser o mesmo que
`core.handlers.balance.check` imprime.

Controle negativo (CLAUDE.md §3): reverter a linha do handler para
`new_balance` deixa os dois primeiros casos vermelhos — medido, não presumido.
"""
from __future__ import annotations

import re

import db
from core.handlers import balance as balance_handler
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, sincroniza, tx, uid_pro,
)

_LINHA_DE_HOJE = re.compile(r"^🏦 Saldo: (R\$ .+)$", re.M)
_LINHA_NOVA = re.compile(r"^💰 Saldo total: (R\$ .+)$", re.M)


def _saldo_total_do_comando(uid: int) -> str:
    """O número que o /saldo imprime, extraído da resposta dele."""
    m = re.search(r"\*Saldo total\*: (R\$ .+)$", balance_handler.check(uid), re.M)
    assert m, "o /saldo não está no recorte consolidado neste cenário"
    return m.group(1)


# ── com banco conectado e gate ligado: o número do /saldo ───────────────────

def test_com_banco_a_resposta_traz_o_mesmo_numero_do_saldo(uid_pro, ia_fora):
    conecta_banco(uid_pro, "114.88")

    resp = manda(uid_pro, "Gastei 1 real com a barbara")

    m = _LINHA_NOVA.search(resp)
    assert m, resp
    assert m.group(1) == _saldo_total_do_comando(uid_pro)
    assert _LINHA_DE_HOJE.search(resp) is None, resp


def test_o_numero_vale_DEPOIS_do_lancamento_na_mesma_mensagem(uid_pro, ia_fora):
    """A releitura é o ponto: `new_balance` sai obsoleto na linha em que é
    impresso. O lançamento manual fica na Carteira (👛) e o total consolidado
    (💰) é o do /saldo. A tx OF pré-importada vira PENDÊNCIA com o manual
    (`propose_manual_reconciliation`), sem fundir: a Carteira não muda até o
    usuário confirmar."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "113.88",
                            [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, conexao)

    resp = manda(uid_pro, "Gastei 1 real com a barbara")

    m = _LINHA_NOVA.search(resp)
    assert m, resp
    assert m.group(1) == _saldo_total_do_comando(uid_pro)
    assert "👛 Saldo (Carteira Piggy): R$ -1,00" in resp, resp
    assert consolidado(uid_pro) == (112.88, -1.0)


# ── POSITIVOS: sem banco, ou com o gate desligado, nada muda ───────────────

def test_sem_banco_conectado_a_linha_continua_a_de_hoje(uid_pro, ia_fora):
    resp = manda(uid_pro, "gastei 50 no mercado")

    assert _LINHA_DE_HOJE.search(resp), resp
    assert _LINHA_NOVA.search(resp) is None, resp
    assert "🏦 Saldo: R$ -50,00" in resp, resp


def test_com_banco_mas_gate_desligado_a_carteira_piggy_e_nomeada(
        uid_pro, ia_fora, monkeypatch):
    """Freio de emergência puxado e user fora da allowlist: o GATE congela só a
    linha 💰 Saldo total. A linha da Carteira segue nomeada 👛 Saldo (Carteira
    Piggy) com banco conectado (decisão "lançamentos manuais exclusivos para
    dinheiro") — número relido."""
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_EMAILS", "ninguem@test.local")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_USER_IDS", "")
    conecta_banco(uid_pro, "114.88")

    resp = manda(uid_pro, "gastei 50 no mercado")

    assert "👛 Saldo (Carteira Piggy): R$ -50,00" in resp, resp
    assert _LINHA_NOVA.search(resp) is None
    assert _LINHA_DE_HOJE.search(resp) is None, resp


def test_gate_desligado_sem_fusao_a_carteira_e_a_lancada(uid_pro, ia_fora, monkeypatch):
    """O gate congela o FORMATO, não autoriza número defasado. Com a tx
    pré-importada, o lançamento manual vira pendência com ela (sem fundir), fica
    na Carteira e ela é relida na resposta — `new_balance` obsoleto era o
    sintoma do relato original."""
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_EMAILS", "ninguem@test.local")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_USER_IDS", "")
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "113.88",
                            [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, conexao)

    resp = manda(uid_pro, "Gastei 1 real com a barbara")

    # linha da Carteira nomeada, número relido (sem fusão: -1,00)
    assert "👛 Saldo (Carteira Piggy): R$ -1,00" in resp, resp
    assert _LINHA_NOVA.search(resp) is None
    assert consolidado(uid_pro) == (112.88, -1.0)
