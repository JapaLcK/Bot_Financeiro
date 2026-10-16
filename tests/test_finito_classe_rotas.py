"""Classe: rota HTTP com `except Exception` sobre escrita de dinheiro que vira 500 "tente de novo".
O CheckViolation `finito_*` é determinístico: repetir nunca resolve, e o handler 400 do monólito
(`valor_finito_violado_handler`) nunca o via. Cada site faz `if violacao_finito(exc): raise`.

Controles: o grupo de comportamento foi visto VERMELHO sem os `raise` (relato do PR); o caso legítimo
e a falha comum (RuntimeError) seguem como antes. O teste de AST impede a próxima rota de regredir."""
import ast
import re
from pathlib import Path

import pytest

import db
import db.cards as dbc
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from core.pg_text import MSG_VALOR_API
from tests._patrimonio_helpers import q
from tests.test_valor_nao_finito_painel import _cliente, _linhas


def _ids(uid, tipo):
    return [r["id"] for r in _linhas(f"select id from launches where user_id=%s and tipo='{tipo}' order by id", uid)]


def _sem_linha(texto):
    return "Failing row" not in texto


# ── comportamento ────────────────────────────────────────────────────────────────────────────

def test_apagar_despesa_cujo_estorno_passa_do_teto_responde_400_com_a_frase_do_valor():
    """Codex: saldo 9e12 = 9e12 - 2e12 (despesa) + 2e12 (receita); apagar a despesa devolve 2e12 -> 11e12."""
    uid = usuario_pagante()
    db.set_balance(uid, 9_000_000_000_000)
    db.add_launch_and_update_balance(uid, "despesa", 2_000_000_000_000, "x", "x")
    db.add_launch_and_update_balance(uid, "receita", 2_000_000_000_000, "x", "x")
    despesa, = _ids(uid, "despesa")
    saldo = q("select balance from accounts where user_id=%s", (uid,))["balance"]
    c, h = _cliente(uid)

    r = c.delete(f"/launches/{uid}/{despesa}", headers=h)

    assert r.status_code == 400 and r.json() == {"detail": MSG_VALOR_API}, r.text
    assert _sem_linha(r.text)
    # nada foi apagado nem movido: a transação inteira voltou atrás
    assert _ids(uid, "despesa") == [despesa]
    assert q("select balance from accounts where user_id=%s", (uid,))["balance"] == saldo


def test_apagar_despesa_pequena_segue_200():
    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "despesa", 50, "x", "x")
    despesa, = _ids(uid, "despesa")
    c, h = _cliente(uid)

    r = c.delete(f"/launches/{uid}/{despesa}", headers=h)

    assert r.status_code == 200, r.text
    assert _ids(uid, "despesa") == []


def _estoura(uid):
    def f(*a, **k):
        q("update accounts set balance=1e20 where user_id=%s", (uid,))
    return f


def _cai(*a, **k):
    raise RuntimeError("banco caiu")


def _rota_credito(uid, mp):
    return f"/credit-transactions/{uid}/1", lambda f: mp.setattr(dashboard, "undo_credit_transaction", f)


def _rota_cartao(uid, mp):
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    return f"/cards/{uid}/{cid}", lambda f: mp.setattr(db.cards, "delete_card", f)


def _rota_parcelamento(uid, mp):
    return f"/installments/{uid}/g1", lambda f: mp.setattr(db.cards, "undo_installment_group", f)


ROTAS = [_rota_credito, _rota_cartao, _rota_parcelamento]


@pytest.mark.parametrize("rota", ROTAS, ids=["compra_credito", "cartao", "parcelamento"])
def test_rotas_destrutivas_de_cartao_deixam_o_check_finito_subir(monkeypatch, rota):
    uid = usuario_pagante()
    url, troca = rota(uid, monkeypatch)
    c, h = _cliente(uid)
    # positivo: falha comum segue sendo o 500 "tente de novo" de antes
    troca(_cai)
    r = c.delete(url, headers=h)
    assert r.status_code == 500 and "Tenta de novo" in r.text, r.text
    troca(_estoura(uid))

    r = c.delete(url, headers=h)

    assert r.status_code == 400 and r.json() == {"detail": MSG_VALOR_API}, r.text
    assert _sem_linha(r.text)


# ── AST: nenhum `except Exception` de escrita nas rotas sem o guard ───────────────────────────

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ["frontend", "api"]
# escritor de dinheiro; cobre também a função passada a `asyncio.to_thread(fn, ...)` (Name/Attribute, não só Call)
ESCRITOR = re.compile(r"^(add_(credit|launch|from|boleto|investment|pocket)\w*|create_(boleto|investment\w*|pocket\w*|goal\w*|meta)|"
                      r"update_(launch|credit|investment|boleto|pocket|card_limit)\w*|set_balance|pay_\w+|mark_bill_paid|"
                      r"delete_(launch|card|boleto|pocket|investment)\w*|undo_\w+|\w+_(from|to)_account|\w+_and_rollback|"
                      r"quitar|reconcile\w*|import_\w+|merge\w*|\w*deposit\w*|\w*withdraw\w*|\w*redeem\w*|restore_\w+|anticipate_\w+)$")
# (arquivo, escritor, trecho do handler) -> motivo de NÃO levar o guard. Vazia: nenhum caso hoje.
SEM_GUARD: dict[tuple[str, str, str], str] = {}


def _sites_sem_guard():
    achados = []
    for pasta in PASTAS:
        for f in sorted((RAIZ / pasta).rglob("*.py")):
            rel, fonte = str(f.relative_to(RAIZ)), f.read_text()
            for t in (n for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Try)):
                nomes = {n.attr if isinstance(n, ast.Attribute) else n.id for b in t.body for n in ast.walk(b)
                         if isinstance(n, (ast.Attribute, ast.Name))}
                escritores = {n for n in nomes if ESCRITOR.match(n)}
                # um `except CheckViolation:` ANTES do genérico (estilo antigo do monólito) já deixa subir
                antes = ""
                for h in t.handlers:
                    tipo = ast.unparse(h.type) if h.type else "bare"
                    texto = ast.get_source_segment(fonte, h)
                    if escritores and (tipo == "bare" or "Exception" in tipo) and "violacao_finito" not in texto \
                            and "CheckViolation" not in antes and not any(isinstance(x, ast.Raise) and x.exc is None for x in h.body) \
                            and not [m for (a, w, tr), m in SEM_GUARD.items() if a == rel and w in escritores and tr in texto]:
                        achados.append(f"{rel}:{h.lineno} {sorted(escritores)}")
                    antes += tipo
    return achados


def test_nenhum_except_exception_de_escrita_nas_rotas_sem_o_guard_finito():
    assert _sites_sem_guard() == []


@pytest.mark.parametrize("alvo", ["frontend/finance_bot_websocket_custom.py", "frontend/routes/cards.py"])
def test_o_detector_de_ast_enxerga_a_rota_sem_guard(monkeypatch, alvo):
    """Controle do detector: tirando o guard de um site real (com `to_thread(fn)` ou chamada direta), ele aponta."""
    original = Path.read_text

    def sem_guard(self, *a, **k):
        t = original(self, *a, **k)
        return t.replace("if violacao_finito(exc):", "if False:") if self == RAIZ / alvo else t
    monkeypatch.setattr(Path, "read_text", sem_guard)
    assert any(s.startswith(alvo) for s in _sites_sem_guard())
