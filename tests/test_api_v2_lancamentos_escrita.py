"""Escrita da v2 (PR 2a): `POST /api/v2/lancamentos/carteira`, `/editar` e `/apagar`, pelo
monólito real (sessão, CSRF do pai, Postgres).

O `pode` da linha (`db/lancamentos.pode_da_linha`, sob o lock do usuário e da linha) decide
cada campo. POSITIVO da guarda: a carteira marcada cria, edita e apaga, e `/contas` move
exatamente o valor. NEGATIVOS: sombra, linha antiga, pagamento de conta, data de importada,
cartão manual e o que outro fluxo mudou enquanto a escrita esperava o lock dão 409; id de
outro usuário dá o mesmo 404 de id inexistente.

Controles por mutação (relato do PR): rota sem `exigir_pode` → vermelho em
`test_apagar_sombra_e_409_e_nada_muda`; `pode_da_linha` antes do lock → vermelho nos dois
`test_pode_sob_lock_*`; `[0-9]` → `\\d` no valor → vermelho em `test_valor_e_data_invalidos`;
`mark_bill_paid` com a marca na criação → vermelho em `test_pagamento_de_conta_entre_os_dois_*`.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from decimal import Decimal

import pytest

import db
import db.bills as B
from conftest import usuario_pagante
from core.services import plan_service
from core.services.plan_limits import PlanLimitExceeded
from tests._patrimonio_helpers import conexao, conta, q
from tests.test_api_v2_lancamentos import ok
from tests.test_api_v2_perfil import cliente
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_api_v2_resumo_mes import ok as resumo
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank, _importa_of_tx
from utils_date import _tz, today_tz

TUDO = ["categoria", "descricao", "data", "valor", "apagar"]
URL = "/api/v2/lancamentos/"
FANTASMA = 9_000_000_000_000_000_000


def post(uid, rota, corpo, com_csrf=True):
    c, h = cliente(uid, com_csrf)
    return c.post(URL + rota, json=corpo, headers=h)


def cria(uid, valor="50", tipo="saida", descricao="feira", **extra) -> int:
    r = post(uid, "carteira", {"tipo": tipo, "valor": valor, "descricao": descricao, **extra})
    assert r.status_code == 200, r.text
    return int(r.json()["id"][1:])


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def saldo(uid) -> Decimal:
    c, _ = cliente(uid)
    return Decimal(c.get("/api/v2/contas").json()["carteira"]["saldo"])


def item(uid, lid, tabela="l", **params) -> dict:
    return {i["id"]: i for i in ok(uid, **params)["itens"]}[f"{tabela}{lid}"]


def linha(lid) -> dict | None:
    return q("select * from launches where id = %s", (lid,))


def n_launches(uid) -> int:
    return q("select count(*) n from launches where user_id = %s", (uid,))["n"]


def _carteira_antiga(uid) -> int:
    lid = db.add_launch_and_update_balance(uid, "despesa", 30, "mercado", "msg")[0]
    q("update launches set origem = null where id = %s returning id", (lid,))
    return lid


def _sombra(uid, tx_id="tx-s") -> int:
    _importa_of_tx(uid, today_tz(), "42.00", "PADARIA", f"{tx_id}-{uid}")
    return q("select id from launches where user_id = %s and source = 'open_finance'", (uid,))["id"]


# ── o caminho legítimo ───────────────────────────────────────────────────────

def test_cria_baixa_exatamente_e_apagar_devolve(libera):
    (a,) = libera(usuario_pagante())
    antes = saldo(a)
    lid = cria(a, "50")
    assert saldo(a) == antes - 50
    i = item(a, lid)
    assert (i["origem"], i["pode"], i["descricao"], i["valor"]) == ("carteira", TUDO, "feira", "50")
    assert linha(lid)["origem"] == "carteira"
    r = post(a, "apagar", {"id": f"l{lid}"})
    assert (r.status_code, r.json()) == (200, {"id": f"l{lid}"})
    assert saldo(a) == antes and linha(lid) is None


def test_entrada_com_categoria_explicita_e_data_de_ontem(libera):
    (a,) = libera(usuario_pagante())
    antes, ontem = saldo(a), today_tz() - timedelta(days=1)
    lid = cria(a, "100.5", tipo="entrada", descricao="bico", categoria="Salário", data=ontem.isoformat())
    assert saldo(a) == antes + Decimal("100.5")
    r = linha(lid)
    assert (r["tipo"], r["alvo"], r["nota"], r["categoria"]) == ("receita", "bico", "bico", "salario")
    assert r["criado_em"].astimezone(_tz()).date() == ontem


def test_editar_para_ontem_troca_o_dia_e_mantem_a_hora(libera):
    (a,) = libera(usuario_pagante())
    lid = cria(a)
    antes = linha(lid)["criado_em"].astimezone(_tz())
    ontem = antes.date() - timedelta(days=1)
    r = post(a, "editar", {"id": f"l{lid}", "data": ontem.isoformat()})
    assert r.status_code == 200, r.text
    depois = linha(lid)["criado_em"].astimezone(_tz())
    assert (depois.date(), depois.time()) == (ontem, antes.time())
    i = item(a, lid, mes=f"{ontem:%Y-%m}")
    assert (i["data"], i["hora"]) == (ontem.isoformat(), f"{antes:%H:%M}")


def test_q40_com_banco_conectado_grava_na_carteira(libera):
    a = usuario_pagante()
    _connect_fake_bank(a)
    libera(a)
    antes = saldo(a)
    lid = cria(a, "100", tipo="entrada")
    assert saldo(a) == antes + 100
    assert linha(lid)["efeitos"]["delta_conta"] == 100


def test_categoria_nova_na_edicao_entra_no_catalogo(libera):
    (a,) = libera(usuario_pagante())
    lid = cria(a)
    r = post(a, "editar", {"id": f"l{lid}", "categoria": "Viagem Só Minha", "descricao": "passagem"})
    assert r.status_code == 200, r.text
    assert (linha(lid)["categoria"], linha(lid)["alvo"]) == ("viagem só minha", "passagem")
    assert q("select 1 as x from user_categories where user_id = %s and lower(name) = 'viagem só minha'", (a,))


def test_aprender_que_falha_depois_do_commit_nao_derruba_nem_duplica(libera, monkeypatch):
    """`core/services/carteira.lancar`, usado pelas duas rotas: o lançamento já gravou."""
    from core.services import category_service
    from test_category_launches_query import _cliente_logado

    (a,) = libera(usuario_pagante())

    def _estoura(*_a, **_k):
        raise RuntimeError("aprendizado fora do ar")
    monkeypatch.setattr(category_service, "learn_from_inference", _estoura)
    cria(a)
    client, h = _cliente_logado(a)
    r = client.post(f"/launches/{a}", headers=h, json={"tipo": "despesa", "valor": 5, "alvo": "x"})
    assert r.status_code == 200, r.text
    assert n_launches(a) == 2


# ── o que não pode ───────────────────────────────────────────────────────────

def test_apagar_sombra_e_409_e_nada_muda(libera):
    a = usuario_pagante()
    sombra = _sombra(a)
    libera(a)
    tx = q("select id from open_finance_transactions where imported_launch_id = %s", (sombra,))
    assert codigo(post(a, "apagar", {"id": f"l{sombra}"})) == (409, "nao_editavel")
    assert linha(sombra) is not None
    assert q("select imported_launch_id from open_finance_transactions where id = %s",
             (tx["id"],))["imported_launch_id"] == sombra


def test_linha_antiga_nao_edita_nem_apaga(libera):
    a = usuario_pagante()
    lid = _carteira_antiga(a)
    libera(a)
    antes = saldo(a)
    assert codigo(post(a, "editar", {"id": f"l{lid}", "categoria": "lazer"})) == (409, "nao_editavel")
    assert codigo(post(a, "apagar", {"id": f"l{lid}"})) == (409, "nao_editavel")
    assert (linha(lid)["categoria"], saldo(a)) == ("outros", antes)


def test_pagamento_de_conta_pela_carteira_nao_apaga_e_edita_categoria(libera):
    a = usuario_pagante()
    conta_luz = B.create_boleto(a, "Luz", 120, today_tz(), category="moradia")
    agua = B.create_boleto(a, "Agua", 80, today_tz())  # a ligação não pode vazar para a vizinha
    B.mark_bill_paid(a, conta_luz["id"], metodo="carteira")
    lid = q("select id from launches where user_id = %s and alvo = 'conta:Luz'", (a,))["id"]
    vizinha = B.get_bill(a, agua["id"])
    assert (vizinha["launch_id"], vizinha["status"]) == (None, "pending")
    libera(a)
    assert item(a, lid)["pode"] == ["categoria", "data"]
    antes = saldo(a)
    assert codigo(post(a, "apagar", {"id": f"l{lid}"})) == (409, "nao_editavel")
    assert codigo(post(a, "editar", {"id": f"l{lid}", "descricao": "x"})) == (409, "nao_editavel")
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "lazer"}).status_code == 200
    assert (linha(lid)["categoria"], saldo(a)) == ("lazer", antes)


# Pagamento de fatura do cartão manual: interno por estrutura (`_SET_CATEGORIA`), porque as
# compras do cartão já contam. NEGATIVO: o predicado `efeitos -> 'bill_id'` fora → vermelho
# nos dois primeiros. POSITIVO: a carteira comum segue a regra da categoria.

def _paga_fatura(uid) -> int:
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    bill_id = db.add_credit_purchase(uid, cartao, 200, "mercado", "compra", today_tz())[2]
    return db.pay_bill_amount(uid, cartao, "Nubank", None, bill_id=bill_id)["launch_id"]


def test_pagamento_de_fatura_recategorizado_segue_interno_e_nao_dobra_o_gasto(libera):
    a = usuario_pagante()
    lid = _paga_fatura(a)
    libera(a)
    assert item(a, lid)["pode"] == ["categoria", "data"]
    antes = resumo(a)["saiu"]
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "lazer"}).status_code == 200
    assert (linha(lid)["categoria"], linha(lid)["is_internal_movement"]) == ("lazer", True)
    assert resumo(a)["saiu"] == antes


def test_pagamento_de_fatura_segue_interno_no_app_e_no_lote():
    a = usuario_pagante()
    lid = _paga_fatura(a)
    assert db.update_launch_fields(a, lid, categoria="lazer")
    assert linha(lid)["is_internal_movement"] is True
    assert db.update_launch_categories_bulk(a, [(lid, "mercado")]) == 1
    assert (linha(lid)["categoria"], linha(lid)["is_internal_movement"]) == ("mercado", True)


def test_carteira_comum_segue_a_regra_da_categoria(libera):
    (a,) = libera(usuario_pagante())
    lid = cria(a)
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "transferencia_interna"}).status_code == 200
    assert linha(lid)["is_internal_movement"] is True
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "lazer"}).status_code == 200
    assert linha(lid)["is_internal_movement"] is False


def test_saldo_inicial_regravado_segue_interno_e_trocado_segue_a_categoria(libera):
    """Saldo inicial nasce interno à mão (rota do /app). Regravar a MESMA categoria junto
    com outro campo não o vira receita; trocar de categoria é reclassificação explícita.
    NEGATIVO: sem o termo `categoria is not distinct from` no `_SET_CATEGORIA` → vermelho."""
    a = usuario_pagante()
    c, h = cliente(a)
    lid = c.post(f"/account/{a}/initial-balance", headers=h, json={"amount": 300}).json()["launch_id"]
    libera(a)
    antes = resumo(a)["entrou"]
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "saldo_inicial", "descricao": "x"}).status_code == 200
    assert (linha(lid)["alvo"], linha(lid)["is_internal_movement"]) == ("x", True)
    assert resumo(a)["entrou"] == antes
    assert post(a, "editar", {"id": f"l{lid}", "categoria": "salário"}).status_code == 200
    assert linha(lid)["is_internal_movement"] is False


def test_pagamento_de_conta_entre_os_dois_commits_nao_apaga(libera, monkeypatch):
    """`mark_bill_paid` commita o lançamento e só depois o liga à conta. Pausado entre os dois,
    a linha não pode parecer carteira pura: sem a marca ela é só leitura, e o apagar dá 409."""
    import db.accounts as A
    a = usuario_pagante()
    conta_luz = B.create_boleto(a, "Luz", 120, today_tz(), category="moradia")
    libera(a)
    criado, solta, erros = threading.Event(), threading.Event(), []
    original = A.add_launch_and_update_balance

    def pausa_depois_do_commit(*args, **kw):
        r = original(*args, **kw)
        criado.set()
        solta.wait(15)
        return r

    monkeypatch.setattr(A, "add_launch_and_update_balance", pausa_depois_do_commit)

    def paga():
        try:
            B.mark_bill_paid(a, conta_luz["id"], metodo="carteira")
        except Exception as e:  # noqa: BLE001 — a FK quebrada do código antigo vem por aqui
            erros.append(e)

    t = threading.Thread(target=paga)
    t.start()
    try:
        assert criado.wait(15)
        lid = q("select id from launches where user_id = %s and alvo = 'conta:Luz'", (a,))["id"]
        antes = saldo(a)
        for rota, corpo in (("apagar", {}), ("editar", {"descricao": "x"})):
            r = post(a, rota, {"id": f"l{lid}", **corpo})
            assert r.status_code == 409 and codigo(r)[1] == "nao_editavel", (rota, r.text)
        assert (linha(lid) is not None, saldo(a)) == (True, antes)
    finally:
        solta.set()
        t.join(15)
    assert not erros, erros
    assert B.get_bill(a, conta_luz["id"])["launch_id"] == lid
    assert (linha(lid)["origem"], item(a, lid)["pode"]) == ("carteira", ["categoria", "data"])


def test_conta_apagada_entre_os_dois_commits_deixa_o_lancamento_sem_marca(libera, monkeypatch):
    """A conta some na janela (apagar o recorrente leva a instância no cascade): sem a ligação
    a marca não entra, senão o lançamento órfão vira carteira pura e o PODE libera tudo."""
    import db.accounts as A
    a = usuario_pagante()
    conta_luz = B.create_boleto(a, "Luz", 120, today_tz())
    criado, solta = threading.Event(), threading.Event()
    original = A.add_launch_and_update_balance

    def pausa_depois_do_commit(*args, **kw):
        r = original(*args, **kw)
        criado.set()
        solta.wait(15)
        return r

    monkeypatch.setattr(A, "add_launch_and_update_balance", pausa_depois_do_commit)
    t = threading.Thread(target=B.mark_bill_paid, args=(a, conta_luz["id"]),
                         kwargs={"metodo": "carteira"})
    t.start()
    try:
        assert criado.wait(15)
        q("delete from bill_instances where id = %s and user_id = %s", (conta_luz["id"], a))
    finally:
        solta.set()
        t.join(15)
    lid = q("select id from launches where user_id = %s and alvo = 'conta:Luz'", (a,))["id"]
    libera(a)
    assert (linha(lid)["origem"], item(a, lid)["pode"]) == (None, [])


def test_par_pendente_com_o_banco(libera):
    a = usuario_pagante()
    _importa_of_tx(a, today_tz(), "42.00", "PADARIA", f"tx-par-{a}")
    libera(a)
    lid = cria(a, "42", descricao="PADARIA")
    i = item(a, lid)
    assert ("conciliacao_pendente" in i["motivos"], i["pode"]) == (True, ["categoria", "descricao", "apagar"])
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    assert codigo(post(a, "editar", {"id": f"l{lid}", "data": ontem})) == (409, "nao_editavel")


def test_p5_importada_edita_categoria_e_descricao(libera):
    a = usuario_pagante()
    sombra = _sombra(a)
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    of_ct = db.add_imported_credit_purchase(a, cartao, -40, "mercado", today_tz(), f"ct-{a}")[0]
    manual_ct = db.add_credit_purchase(a, cartao, 39, "mercado", "manual", today_tz())[0]
    libera(a)
    for ident in (f"l{sombra}", f"c{of_ct}"):
        r = post(a, "editar", {"id": ident, "categoria": "lazer", "descricao": "nova"})
        assert (r.status_code, r.json()) == (200, {"id": ident}), r.text
    s = linha(sombra)
    assert (s["categoria"], s["alvo"], s["categoria_editada"]) == ("lazer", "nova", True)
    ct = q("select categoria, nota, categoria_editada from credit_transactions where id = %s", (of_ct,))
    assert (ct["categoria"], ct["nota"], ct["categoria_editada"]) == ("lazer", "nova", True)
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    assert codigo(post(a, "editar", {"id": f"l{sombra}", "data": ontem})) == (409, "nao_editavel")
    assert codigo(post(a, "editar", {"id": f"c{of_ct}", "data": ontem})) == (409, "nao_editavel")
    assert codigo(post(a, "editar", {"id": f"c{manual_ct}", "categoria": "lazer"})) == (409, "nao_editavel")
    assert codigo(post(a, "apagar", {"id": f"c{of_ct}"})) == (409, "nao_editavel")


# ── isolamento, CSRF, plano ──────────────────────────────────────────────────

def test_b_nao_edita_nem_apaga_o_de_a(libera):
    a, b = usuario_pagante(), usuario_pagante()
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    ct = db.add_imported_credit_purchase(a, cartao, -40, "mercado", today_tz(), f"ct-iso-{a}")[0]
    libera(a, b)
    lid = cria(a)
    antes = (saldo(a), linha(lid)["categoria"], linha(lid)["alvo"])
    for rota, corpo in (("editar", {"categoria": "lazer"}), ("apagar", {})):
        for tabela, dono in (("l", lid), ("c", ct)):
            de_a = post(b, rota, {"id": f"{tabela}{dono}", **corpo})
            fantasma = post(b, rota, {"id": f"{tabela}{FANTASMA}", **corpo})
            assert (de_a.status_code, de_a.json()) == (fantasma.status_code, fantasma.json())
            assert de_a.status_code in (404, 409), de_a.text
            if (rota, tabela) != ("apagar", "c"):  # cartão não apaga pelo v2: 409 para qualquer id
                assert codigo(de_a) == (404, "lancamento_nao_encontrado")
    assert (saldo(a), linha(lid)["categoria"], linha(lid)["alvo"]) == antes
    assert q("select categoria from credit_transactions where id = %s", (ct,))["categoria"] == "mercado"


@pytest.mark.parametrize("rota", ["carteira", "editar", "apagar"])
def test_sem_csrf_e_403_e_nada_grava(libera, rota):
    (a,) = libera(usuario_pagante())
    lid = cria(a)
    antes = (n_launches(a), saldo(a), linha(lid)["alvo"])
    corpo = {"carteira": {"tipo": "saida", "valor": "10", "descricao": "x"},
             "editar": {"id": f"l{lid}", "descricao": "y"}, "apagar": {"id": f"l{lid}"}}[rota]
    r = post(a, rota, corpo, com_csrf=False)
    assert (r.status_code, r.json()) == (403, {"detail": "Token CSRF inválido ou ausente."})
    assert (n_launches(a), saldo(a), linha(lid)["alvo"]) == antes


def test_limite_do_plano_e_403_e_nada_grava(libera, monkeypatch):
    (a,) = libera(usuario_pagante())

    def _lotou(_uid):
        raise PlanLimitExceeded("launches_month", "lotou")
    monkeypatch.setattr(plan_service, "check_can_create_launch", _lotou)
    r = post(a, "carteira", {"tipo": "saida", "valor": "10", "descricao": "x"})
    assert codigo(r) == (403, "plan_limit")
    assert n_launches(a) == 0


# ── validação ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", ["0.01", "999999999.99"])
def test_valor_nos_limites_grava(libera, valor):
    (a,) = libera(usuario_pagante())
    assert linha(cria(a, valor))["valor"] == Decimal(valor)


@pytest.mark.parametrize("campo,valor", [
    ("valor", 0), ("valor", "0"), ("valor", "0.00"), ("valor", "-1"), ("valor", "1e3"),
    ("valor", "1,50"), ("valor", "١٢"), ("valor", 10), ("valor", "1000000000"), ("valor", "10.123"),
    ("valor", " 10"), ("data", "amanha"), ("data", "2026-02-30"), ("descricao", ""),
    ("descricao", "   "), ("descricao", "x" * 201), ("tipo", "despesa"),
])
def test_valor_e_data_invalidos(libera, campo, valor):
    (a,) = libera(usuario_pagante())
    corpo = {"tipo": "saida", "valor": "10", "descricao": "x", campo: valor}
    r = post(a, "carteira", corpo)
    assert codigo(r) == (422, "validation_error"), r.text
    assert r.json()["error"]["details"][0]["loc"][:2] == ["body", campo]
    assert n_launches(a) == 0


def test_data_fora_da_janela(libera, monkeypatch):
    (a,) = libera(usuario_pagante())
    hoje = today_tz()
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda *_: hoje - timedelta(days=30))
    for d in (hoje + timedelta(days=1), hoje - timedelta(days=31)):
        r = post(a, "carteira", {"tipo": "saida", "valor": "10", "descricao": "x", "data": d.isoformat()})
        assert (codigo(r), r.json()["error"]["details"][0]["loc"]) == ((422, "validation_error"), ["body", "data"])
    assert n_launches(a) == 0
    cria(a, data=(hoje - timedelta(days=30)).isoformat())  # positivo: o próprio corte entra


@pytest.mark.parametrize("corpo", [{"id": "l1"}, {"id": "x1", "categoria": "lazer"}, {"id": "l01", "descricao": "a"},
                                   {"id": "l9223372036854775808", "descricao": "a"}, {"id": 5, "descricao": "a"},
                                   {"id": "l1", "descricao": " "}])
def test_edicao_invalida_e_422(libera, corpo):
    (a,) = libera(usuario_pagante())
    assert codigo(post(a, "editar", corpo)) == (422, "validation_error")


# ── o `pode` é lido sob o lock: o que mudou enquanto esperava vale ───────────

def _esperando_lock(minimo: int, teto: float = 10.0) -> None:
    fim = time.monotonic() + teto
    while time.monotonic() < fim:
        n = q("""select count(*) n from pg_stat_activity
                  where datname = current_database() and wait_event_type = 'Lock'""")["n"]
        if n >= minimo:
            return
        time.sleep(0.02)
    raise AssertionError("a escrita não chegou a esperar o lock")


def _corrida(uid, rota, corpo, muda_sql, params) -> int:
    """Segura o lock do usuário (como o sync e a conciliação), dispara a escrita, espera ela
    parar no lock, muda o estado NA MESMA transação e solta com o commit. Devolve o status da
    escrita. Na mesma transação de propósito: se a escrita pegasse a linha antes do lock, o
    Postgres acusa o deadlock em vez de o teste travar."""
    status = []
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local lock_timeout = '10s'")
        cur.execute("select user_id from accounts where user_id = %s for update", (uid,))
        t = threading.Thread(target=lambda: status.append(post(uid, rota, corpo).status_code))
        t.start()
        _esperando_lock(1)
        cur.execute(muda_sql, params)
        conn.commit()
    t.join(15)
    return status[0]


def test_pode_sob_lock_editar_data_com_par_pendente_criado_na_espera(libera):
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-lock-{a}"), "acc-lock", "100")
    libera(a)
    lid = cria(a, "31")
    sombra = q("""insert into launches (user_id, tipo, valor, source, external_id, efeitos)
                  values (%s, 'despesa', 31, 'open_finance', %s, '{"delta_conta": 0}') returning id""",
               (a, f"ext-lock-{a}"))["id"]
    ontem = (today_tz() - timedelta(days=1)).isoformat()
    st = _corrida(a, "editar", {"id": f"l{lid}", "data": ontem},
                  """insert into open_finance_transactions (account_id, provider_transaction_id, description,
                         amount, transaction_date, imported_launch_id, match_launch_id, reconciliation_status)
                     values (%s, 'tx-lock', 'Mercado', -31, current_date, %s, %s, 'pending')""",
                  (acc, sombra, lid))
    assert st == 409
    assert linha(lid)["criado_em"].astimezone(_tz()).date() == today_tz()


def test_pode_sob_lock_apagar_linha_que_perdeu_a_marca_na_espera(libera):
    (a,) = libera(usuario_pagante())
    lid = cria(a)
    st = _corrida(a, "apagar", {"id": f"l{lid}"}, "update launches set origem = null where id = %s", (lid,))
    assert st == 409 and linha(lid) is not None

