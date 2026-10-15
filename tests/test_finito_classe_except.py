"""Classe: `except Exception` que envolve ESCRITA de dinheiro e devolve "tenta de novo" engole o
CheckViolation `finito_*` (determinístico: repetir nunca resolve) e o tratador central
(`handle_incoming` / runner da IA) nunca o vê. Cada site faz `if violacao_finito(e): raise`.

Controles: o grupo de comportamento foi visto VERMELHO sem os `raise` (relato do PR); o caso legítimo
(compra pequena) segue passando. O teste de AST impede a próxima porta de regredir."""
import ast
import re
from pathlib import Path

import pytest

import db
from conftest import usuario_pagante
from db.finito_checks import MSG_VALOR_WHATSAPP
from core.response_formatter import format_for_platform
from tests._patrimonio_helpers import q
from test_finito_ataque import PERTO, TETO, _diga, _linhas

CC = "select 1 from credit_transactions where user_id=%s"
WA = format_for_platform(MSG_VALOR_WHATSAPP, "whatsapp")


def _cartao(uid):
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(uid, cid)  # "parcelei ... celular" não nomeia o cartão
    return cid


# ── WhatsApp: duas portas do cartão (a fatura semeada em PERTO passa de VALOR_MAX com 1 bi legal) ─────

@pytest.mark.parametrize("frase,n", [("gastei {} no crédito nubank", 1), ("parcelei {} em 2x celular", 2)],
                         ids=["a_vista", "parcelada"])
def test_whatsapp_compra_no_credito_cuja_fatura_passa_do_teto(frase, n):
    """A fatura é semeada por SQL em PERTO; a compra de TETO_ENTRADA (legal na porta) cruza 1e13."""
    uid = usuario_pagante()
    _cartao(uid)
    # positivo: o caminho legítimo continua registrando
    assert _diga(uid, frase.format(100)) != WA
    antes = len(_linhas(CC, uid))
    assert antes == n, antes
    q("update credit_bills set total=%s where user_id=%s", (PERTO, uid))
    resposta = _diga(uid, frase.format(TETO))
    assert resposta == WA and "Tenta de novo" not in resposta, resposta
    # a compra que estourou não deixou linha solta
    assert len(_linhas(CC, uid)) == antes


def test_whatsapp_pagar_fatura_com_check_sobe_ao_tratador_central(monkeypatch):
    import core.handlers.credit as C
    uid = usuario_pagante()

    def estoura(*a, **k):
        q("update accounts set balance=1e20 where user_id=%s", (uid,))
    monkeypatch.setattr(C, "pay_bill_amount", estoura)
    with pytest.raises(Exception) as e:
        C._execute_pay_bill(uid, {"id": 1, "card_id": 1, "card_name": "x", "total": 10}, 10.0)
    from db.finito_checks import violacao_finito
    assert violacao_finito(e.value)


# ── IA: a tool deixa subir e o runner responde a frase do valor, não "tenta de novo" ──────────

def _estoura(uid):
    def f(*a, **k):
        q("update accounts set balance=1e20 where user_id=%s", (uid,))
    return f


CASOS_TOOL = [  # (tool, args, onde trocar o escritor)
    ("add_boleto", {"name": "iptu", "amount": 100, "due_date": "2030-01-15"}, "db.bills", "create_boleto"),
    ("create_investment", {"name": "cdbx", "rate": 10, "period": "daily"}, "db", "create_investment"),
    ("investment_withdraw", {"name": "cdbx", "amount": 10}, "db", "investment_withdraw_to_account"),
    ("pocket_withdraw", {"pocket_name": "viagem", "amount": 10}, "db", "pocket_withdraw_to_account"),
    ("pocket_deposit", {"pocket_name": "viagem", "amount": 10}, "db", "pocket_deposit_from_account"),
]


@pytest.mark.parametrize("tool,args,modulo,escritor", CASOS_TOOL, ids=[c[0] for c in CASOS_TOOL])
def test_tool_de_escrita_da_ia_com_check_responde_a_frase_do_valor(monkeypatch, tool, args, modulo, escritor):
    import importlib
    from core.services.ai_chat import runner
    uid = usuario_pagante()
    monkeypatch.setattr(importlib.import_module(modulo), escritor, _estoura(uid))
    msg = runner._execute_pending(uid, {"tool_name": tool, "tool_args": args})
    assert msg == MSG_VALOR_WHATSAPP, msg


def test_tool_de_escrita_com_erro_comum_continua_dizendo_tente_de_novo(monkeypatch):
    """Positivo: o guard só pega o CHECK finito; falha transitória segue sendo 'tenta de novo'."""
    from core.services.ai_chat import tools
    from core.services.ai_chat.runner import _execute_pending
    uid = usuario_pagante()

    def cai(*a, **k):
        raise RuntimeError("banco caiu")
    monkeypatch.setattr(db.bills, "create_boleto", cai)
    msg = _execute_pending(uid, {"tool_name": "add_boleto", "tool_args": CASOS_TOOL[0][1]})
    assert "Tenta de novo" in msg and msg != MSG_VALOR_WHATSAPP, msg


# ── AST: nenhum `except Exception` sobre escritor de dinheiro sem o guard ────────────────────

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ["core/handlers", "core/services/ai_chat/tools", "adapters/whatsapp"]
# o que move/grava dinheiro e pode estourar o CHECK (soma que cruza VALOR_MAX)
ESCRITOR = re.compile(r"^(add_credit_purchase\w*|add_launch\w*|pay_bill_amount|quitar|mark_bill_paid|create_boleto|"
                      r"create_investment|\w+_(from|to)_account|\w+_and_rollback|set_balance|add_from_entities)$")
# (arquivo, escritor, trecho do handler) -> motivo de NÃO levar o guard
SEM_GUARD = {
    ("core/handlers/pending.py", "delete_launch_and_rollback", "failed.append"):
        "lote: cada id falho é listado ao usuário; subir perderia o resumo dos já apagados",
    ("adapters/whatsapp/wa_runtime.py", "quitar", "WA message processing failed"):
        "catch-all de process_message; os dois `quitar` dele têm try próprio (a frase do valor) acima",
}


def _sites_sem_guard():
    achados = []
    for pasta in PASTAS:
        for f in sorted((RAIZ / pasta).rglob("*.py")):
            rel, fonte = str(f.relative_to(RAIZ)), f.read_text()
            for t in (n for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Try)):
                chamados = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                            for b in t.body for c in ast.walk(b) if isinstance(c, ast.Call)}
                escritores = {n for n in chamados if ESCRITOR.search(n)}
                for h in t.handlers:
                    tipo = ast.unparse(h.type) if h.type else "bare"
                    if not escritores or (tipo != "bare" and "Exception" not in tipo):
                        continue
                    texto = ast.get_source_segment(fonte, h)
                    sobe = any(isinstance(x, ast.Raise) for x in h.body)  # raise incondicional (o do guard fica dentro de um if)
                    liberado = [m for (a, w, tr), m in SEM_GUARD.items() if a == rel and w in escritores and tr in texto]
                    if "violacao_finito" not in texto and not sobe and not liberado:
                        achados.append(f"{rel}:{h.lineno} {sorted(escritores)}")
    return achados


def test_nenhum_except_exception_sobre_escrita_de_dinheiro_sem_o_guard_finito():
    assert _sites_sem_guard() == []


def test_o_detector_de_ast_enxerga_o_site_sem_guard(monkeypatch):
    """Controle negativo do detector: sem o guard num site real, ele aponta."""
    original = Path.read_text
    alvo = RAIZ / "core/services/ai_chat/tools/cards.py"

    def sem_guard(self, *a, **k):
        t = original(self, *a, **k)
        return t.replace("violacao_finito(e)", "False") if self == alvo else t
    monkeypatch.setattr(Path, "read_text", sem_guard)
    assert any("cards.py" in s for s in _sites_sem_guard())
