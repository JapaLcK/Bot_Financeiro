"""PR 2b-1: `/api/v2/lancamentos/editar` com `valor` (só a carteira pura, P4) e a data da
linha fundida com o banco travada em TODO canal (P3), pelo monólito real e Postgres.

Valor: troca `valor`, `efeitos.delta_conta` e o saldo da Carteira pela diferença, na mesma
transação (`db.accounts.update_launch_fields`); apagar depois devolve o saldo exato. Data: a
guarda lê `db/lancamentos.FUNDIDO_SQL`, sob o lock do usuário e da linha.

Controles por mutação (relato do PR): sem o `update accounts` → vermelho em
`test_valor_da_carteira_pura_move_o_saldo_exato`; sem o `where user_id` dele → vermelho em
`test_b_nao_edita_valor_nem_data_do_de_a`; sem o `jsonb_set` → vermelho no mesmo (o
apagar desfaz errado); sem "valor" em `pedidos` → vermelho em `test_valor_fora_da_carteira_pura`;
sem o ramo `fundido` da guarda → vermelho em `test_app_nao_edita_data_da_fundida`; `_lock_user`
depois do `pode` → vermelho em `test_valor_sob_lock`; sem o ramo `paga_conta and fundido` do
`PODE_SQL` → vermelho em `test_pagamento_de_conta_fundido_nao_troca_de_data_pelo_v2`; PATCH do
`/app` com data sem `_lock_user` → vermelho em `test_app_patch_data_sob_lock_fundida_na_espera`.
"""
from __future__ import annotations

import ast
import re
import subprocess
import threading
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

import db
from conftest import usuario_pagante
from test_category_launches_query import _cliente_logado
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_lancamentos_escrita import (FANTASMA, _corrida, codigo, cria, item, linha,
                                                    post, saldo)
from tests.test_api_v2_lancamentos_pode import _carteira, _sombra
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from utils_date import _tz, today_tz

FRASE_FUNDIDA = ("Esse lançamento está junto com uma transação do banco, e a data é a do banco. "
                 "Dá pra editar a descrição e a categoria.")


def cru(uid) -> Decimal:
    return q("select balance from accounts where user_id = %s", (uid,))["balance"]


def estado(lid) -> tuple:
    r = linha(lid)
    return r["valor"], r["efeitos"]["delta_conta"], r["criado_em"], r["posted_at"]


def editar(uid, lid, **campos):
    return post(uid, "editar", {"id": f"l{lid}", **campos})


def _ajuste(uid, alvo) -> int:
    c, h = _cliente_logado(uid)
    r = c.post(f"/account/{uid}/adjust-balance", headers=h, json={"target_balance": alvo})
    assert r.status_code == 200, r.text
    return r.json()["launch_id"]


def patch_data(uid, lid, dia: date):
    c, h = _cliente_logado(uid)
    return c.patch(f"/launches/{uid}/{lid}", headers=h, json={"criado_em": f"{dia.isoformat()}T10:00:00"})


def _fundida(uid, acc, valor=32, fonte="manual") -> int:
    if fonte == "manual":
        lid = _carteira(uid, valor=valor)
    else:  # extrato auto-fundido: `posted_at` é dele
        lid = q("""insert into launches (user_id, tipo, valor, source, external_id, efeitos, posted_at)
                   values (%s, 'despesa', %s, 'ofx', %s, %s, current_date) returning id""",
                (uid, valor, f"ofx-{valor}-{uid}", Jsonb({"delta_conta": -valor})))["id"]
    tx_banco(acc, f"tx-f-{lid}", f"-{valor}", imported_launch_id=lid, reconciliation_status="auto_merged")
    return lid


# ── valor: o caminho legítimo ────────────────────────────────────────────────

@pytest.mark.parametrize("tipo,sinal", [("saida", -1), ("entrada", 1)])
def test_valor_da_carteira_pura_move_o_saldo_exato(libera, tipo, sinal):
    (a,) = libera(usuario_pagante())
    inicial, exibido = cru(a), saldo(a)
    lid = cria(a, "12.34", tipo=tipo)
    assert editar(a, lid, valor="98.76").status_code == 200
    assert cru(a) == inicial + sinal * Decimal("98.76")
    assert saldo(a) == exibido + sinal * Decimal("98.76")
    r = linha(lid)
    assert (r["valor"], Decimal(str(r["efeitos"]["delta_conta"]))) == (Decimal("98.76"), sinal * Decimal("98.76"))
    assert item(a, lid)["valor"] == "98.76"
    assert editar(a, lid, valor="98.76").status_code == 200  # o mesmo valor: saldo não anda
    assert cru(a) == inicial + sinal * Decimal("98.76")
    assert post(a, "apagar", {"id": f"l{lid}"}).status_code == 200
    assert (cru(a), saldo(a)) == (inicial, exibido)


def test_valor_do_ajuste_interno(libera):
    a = usuario_pagante()
    lid = _ajuste(a, 300)
    libera(a)
    assert item(a, lid)["pode"] == ["categoria", "descricao", "data", "valor", "apagar"]
    assert editar(a, lid, valor="250").status_code == 200
    assert (cru(a), saldo(a), linha(lid)["is_internal_movement"]) == (250, 250, True)
    assert post(a, "apagar", {"id": f"l{lid}"}).status_code == 200
    assert cru(a) == 0


@pytest.mark.parametrize("valor", ["0.01", "999999999.99"])
def test_valor_nos_limites(libera, valor):
    (a,) = libera(usuario_pagante())
    inicial = cru(a)
    lid = cria(a, "5")
    assert editar(a, lid, valor=valor).status_code == 200
    assert (linha(lid)["valor"], cru(a)) == (Decimal(valor), inicial - Decimal(valor))


@pytest.mark.parametrize("valor", [0, "0", "0.00", "-1", "1e3", "1,50", "١٢", 10, "1000000000", "10.123"])
def test_valor_invalido_e_422_e_nada_muda(libera, valor):
    (a,) = libera(usuario_pagante())
    lid = cria(a, "5")
    antes = (estado(lid), cru(a))
    r = editar(a, lid, valor=valor)
    assert codigo(r) == (422, "validation_error"), r.text
    assert r.json()["error"]["details"][0]["loc"][:2] == ["body", "valor"]
    assert (estado(lid), cru(a)) == antes


@pytest.mark.parametrize("valor", ["-50", "0", "NaN", "1.234"])
def test_db_recusa_valor_invalido_sem_a_rota(libera, valor):
    (a,) = libera(usuario_pagante())
    lid = cria(a, "5")
    antes = (estado(lid), cru(a))
    with pytest.raises(ValueError):  # a função de db é a fronteira do dinheiro
        db.update_launch_fields(a, lid, valor=Decimal(valor), exigir_pode=True)
    assert (estado(lid), cru(a)) == antes


# ── valor: o que não pode ────────────────────────────────────────────────────

def test_valor_fora_da_carteira_pura(libera):
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-valor-{a}"), "acc-valor", "100")
    fundida = _fundida(a, acc)
    pendente = _carteira(a, valor=31)
    tx_banco(acc, "tx-pend", "-31", imported_launch_id=_sombra(a, 31), match_launch_id=pendente,
             reconciliation_status="pending")
    especie = _carteira(a, valor=33)
    q("""insert into of_cash_links (user_id, tx_key, key_durable, account_key, kind, status, launch_id,
             amount, tx_date) values (%s, 'k', true, 'k', 'saque', 'ativo', %s, 1, current_date)
         returning id""", (a, especie))
    paga = _carteira(a, valor=35, bill_id=1, paid_amount_added=35)
    antiga = _carteira(a, valor=36)
    q("update launches set origem = null where id = %s returning id", (antiga,))
    sombra = _sombra(a, 38)
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    manual_ct = db.add_credit_purchase(a, cartao, 39, "mercado", "manual", today_tz())[0]
    of_ct = db.add_imported_credit_purchase(a, cartao, -40, "mercado", today_tz(), f"ct-v-{a}")[0]
    libera(a)
    linhas = (fundida, pendente, especie, paga, antiga, sombra)
    antes = ([estado(lid) for lid in linhas], cru(a))
    for lid in linhas:
        assert codigo(editar(a, lid, valor="99")) == (409, "nao_editavel"), lid
    for ct in (manual_ct, of_ct):
        assert codigo(post(a, "editar", {"id": f"c{ct}", "valor": "99"})) == (409, "nao_editavel"), ct
    assert ([estado(lid) for lid in linhas], cru(a)) == antes
    assert {r["valor"] for r in q("select valor from credit_transactions where id in (%s, %s)",
                                  (manual_ct, of_ct), fetch=True)} == {39, 40}
    with pytest.raises(ValueError):  # fora da v2 não há regra de valor: erro de programação
        db.update_launch_fields(a, pendente, valor=Decimal("99"))
    assert ([estado(lid) for lid in linhas], cru(a)) == antes


# ── data da fundida: travada em todo canal ───────────────────────────────────

def test_app_nao_edita_data_da_fundida(libera):
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-data-{a}"), "acc-data", "100")
    manual, ofx = _fundida(a, acc), _fundida(a, acc, 37, fonte="ofx")
    livre, sombra = _carteira(a, valor=20), _sombra(a, 38)
    libera(a)
    ontem = today_tz() - timedelta(days=1)
    txs = q("select id, transaction_date from open_finance_transactions where account_id = %s",
            (acc,), fetch=True)
    antes = {lid: estado(lid) for lid in (manual, ofx, sombra)}
    for lid in (manual, ofx):
        r = patch_data(a, lid, ontem)
        assert (r.status_code, r.json().get("detail")) == (409, FRASE_FUNDIDA), (lid, r.text)
        assert codigo(editar(a, lid, data=ontem.isoformat())) == (409, "nao_editavel")
    r = patch_data(a, sombra, ontem)  # a sombra segue com a frase antiga
    assert r.status_code == 409 and "vem do banco conectado" in r.json()["detail"]
    assert {lid: estado(lid) for lid in antes} == antes
    assert q("select id, transaction_date from open_finance_transactions where account_id = %s",
             (acc,), fetch=True) == txs
    assert patch_data(a, livre, ontem).status_code == 200  # positivo: a não fundida troca de data
    assert linha(livre)["criado_em"].astimezone(_tz()).date() == ontem


def test_pagamento_de_conta_fundido_nao_troca_de_data_pelo_v2(libera):
    """Pagamento de conta fundido: o `pode` não promete 'data' (P3, o banco é dono da data em
    todo canal) e o v2 recusa com 409; a guarda de data e o `except` da rota ficam de defesa."""
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-paga-{a}"), "acc-paga", "100")
    paga = _carteira(a, valor=35, bill_id=1, paid_amount_added=35)
    tx_banco(acc, "tx-paga", "-35", imported_launch_id=paga, reconciliation_status="auto_merged")
    libera(a)
    assert item(a, paga)["pode"] == ["categoria"]
    antes = estado(paga)
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    assert codigo(editar(a, paga, data=ontem)) == (409, "nao_editavel")
    assert estado(paga) == antes


# ── isolamento ───────────────────────────────────────────────────────────────

def test_b_nao_edita_valor_nem_data_do_de_a(libera):
    a, b = usuario_pagante(), usuario_pagante()
    libera(a, b)
    lid = cria(a, "12.34")
    cria(b, "7")  # B com linha em `accounts`: o saldo dele é o que o positivo de A não pode mexer
    antes = (estado(lid), cru(a), cru(b))
    de_a, fantasma = editar(b, lid, valor="1"), post(b, "editar", {"id": f"l{FANTASMA}", "valor": "1"})
    assert (de_a.status_code, de_a.json()) == (fantasma.status_code, fantasma.json())
    assert codigo(de_a) == (404, "lancamento_nao_encontrado")
    assert patch_data(b, lid, today_tz() - timedelta(days=1)).status_code == 404
    assert (estado(lid), cru(a), cru(b)) == antes
    assert editar(a, lid, valor="1").status_code == 200  # positivo: o dono edita
    assert (cru(a), cru(b)) == (antes[1] + Decimal("11.34"), antes[2])  # só o saldo de A anda


# ── o `pode` é lido sob o lock ───────────────────────────────────────────────

@pytest.mark.parametrize("vira", ["pendente", "fundida"])
def test_valor_sob_lock(libera, vira):
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-vlock-{a}"), "acc-vlock", "100")
    libera(a)
    lid = cria(a, "31")
    if vira == "pendente":
        sql = """insert into open_finance_transactions (account_id, provider_transaction_id, description,
                     amount, transaction_date, imported_launch_id, match_launch_id, reconciliation_status)
                 values (%s, 'tx-vlock', 'Mercado', -31, current_date, %s, %s, 'pending')"""
        params = (acc, _sombra(a, 31), lid)
    else:
        sql = """insert into open_finance_transactions (account_id, provider_transaction_id, description,
                     amount, transaction_date, imported_launch_id, reconciliation_status)
                 values (%s, 'tx-vlock', 'Mercado', -31, current_date, %s, 'auto_merged')"""
        params = (acc, lid)
    antes = (estado(lid), cru(a))
    assert _corrida(a, "editar", {"id": f"l{lid}", "valor": "77"}, sql, params) == 409
    assert (estado(lid), cru(a)) == antes


def test_app_patch_data_sob_lock_fundida_na_espera(libera):
    """O sync funde a linha enquanto o PATCH de data do `/app` corre: o PATCH espera o lock do
    usuário e relê a fusão depois dele. Sem o lock, ele grava a data antes (200) e a fundida
    fica com a data do usuário. Espera o lock OU o fim do PATCH: no negativo ele não espera."""
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-alock-{a}"), "acc-alock", "100")
    libera(a)
    lid = cria(a, "31")
    antes, status = linha(lid)["criado_em"], []
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local lock_timeout = '10s'")
        cur.execute("select user_id from accounts where user_id = %s for update", (a,))
        t = threading.Thread(target=lambda: status.append(
            patch_data(a, lid, today_tz() - timedelta(days=1)).status_code))
        t.start()
        fim = time.monotonic() + 10
        while t.is_alive() and time.monotonic() < fim and not q(
                """select count(*) n from pg_stat_activity
                    where datname = current_database() and wait_event_type = 'Lock'""")["n"]:
            time.sleep(0.02)
        cur.execute("""insert into open_finance_transactions (account_id, provider_transaction_id,
                           description, amount, transaction_date, imported_launch_id, reconciliation_status)
                       values (%s, 'tx-alock', 'Mercado', -31, current_date, %s, 'auto_merged')""", (acc, lid))
        conn.commit()
    t.join(15)
    assert status == [409]
    assert linha(lid)["criado_em"] == antes


# ── portão: quem escreve `launches.valor` / `launches.criado_em` ─────────────

_UPDATE = re.compile(r"\bupdate\s+launches\b", re.I)
_COLUNA = re.compile(r'(?<![\w.])(?:"?\w+"?\.)?"?(valor|criado_em)"?\s*=(?!=)', re.I)
ESCRITORES = {
    ("db/reconciliation.py", "_apply_bank_fields"): "P3: banco na representação, original e delta preservados",
    ("db/reconciliation.py", "_restore_original"): "saída da fusão restaura original, não move saldo",
    ("db/accounts.py", "update_launch_fields"): "v2 (pode) e /app; data da fundida e do OF travada",
    ("db/open_finance.py", "sync_imported_open_finance_updates"): "sync do OF, preservando delta original da fundida",
    ("db/open_finance_cash_revisao.py", "_corrige"): "Q41: o banco corrigiu o saque/depósito automático",
}


def _proprios(fn):
    pilha = list(ast.iter_child_nodes(fn))
    while pilha:
        n = pilha.pop()
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield n
            pilha.extend(ast.iter_child_nodes(n))


def test_todo_escritor_de_valor_ou_data_esta_classificado():
    """Função de produção com `update launches` e `valor =`/`criado_em =` nas strings dela reprova
    até entrar em ESCRITORES. CLASSIFICA, não prova comportamento: quem entra aqui tem de dizer
    por que não move dinheiro em silêncio nem tira do banco a data da fundida (P3). Cego a SQL
    fora de constante literal da própria função (constante de módulo, f-string com o trecho
    interpolado), a nome de tabela ou coluna montado por variável e a arquivo ainda não
    versionado."""
    assert all(_COLUNA.search(s) for s in ('valor=%s', '"valor" = %s', 'l."valor"=%s', 'l.valor = %s',
                                            '"l"."criado_em" = %s'))
    assert not any(_COLUNA.search(s) for s in ('valor == x', 'valor_total = %s', 'criado_em_local = %s',
                                                '"valor_total" = %s', 'valor >= %s'))
    achados = set()
    arquivos = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True,
                              check=True).stdout.split()
    for path in arquivos:
        if path.startswith(("tests/", "scripts/", "harness_tests/")):
            continue
        for fn in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                textos = [n.value for n in _proprios(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                if any(_UPDATE.search(t) for t in textos) and any(_COLUNA.search(t) for t in textos):
                    achados.add((path, fn.name))
    assert achados == set(ESCRITORES), achados ^ set(ESCRITORES)
