"""Q36 no painel antigo: quem tem a chave do v2 não cria investimento manual, não
aporta nele, não importa extrato nem fatura e não lança compra no cartão — em
nenhum canal (`/app`, WhatsApp, IA). Trava: `core/services/fonte_unica.py`.

Cada caminho roda duas vezes pelo canal de produção, com estado real:
com a chave (recusa com o texto do canal e nada gravado) e com a chave ligada
para OUTRO usuário (grava: controle positivo e isolamento ao mesmo tempo).
"""
from __future__ import annotations

import ast
import re
import subprocess
from decimal import Decimal

import pytest

import core.handle_incoming as hi
import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from core.services.ai_chat import runner
from core.services.ai_chat.tools import get_tool
from core.services.fonte_unica import MENSAGENS
from core.types import Attachment, IncomingMessage
from test_category_launches_query import _cliente_logado
from test_category_normalization import _OFX_BANCO, _OFX_CARTAO
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_forma_pagamento_cartao_e_conciliado import _compra_na_conexao
from tests.test_manual_launches_carteira_piggy import _importa_of_tx
from utils_date import today_tz

_CSV = ("Data,Descrição,Valor\n01/08/2026,MERCADO PAGUE MENOS,\"-350,00\"\n").encode()


@pytest.fixture(autouse=True)
def _limiter():
    """`/ofx/import` tem 5/hour num storage em memória compartilhado."""
    dashboard.limiter._storage.reset()


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    return lambda uid: monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))


@pytest.fixture
def uid():
    """Pagante sem banco conectado, com Carteira, um cartão manual (padrão) e um
    investimento manual — tudo criado ANTES de qualquer chave ligar."""
    u = usuario_pagante()
    db.set_balance(u, Decimal("5000"))
    cid = db.create_card(u, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(u, cid)
    db.create_investment_db(u, "CDB Nubank", 1.0, "cdi", initial_amount=100)
    db.set_balance(u, Decimal("5000"))
    return u


def _n(sql, uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid,))
        return cur.fetchone()["n"]


def escritas(uid):
    return tuple(_n(f"select count(*) n from {t} where user_id=%s", uid)
                 for t in ("investments", "investment_lots", "launches", "credit_transactions"))


def _cartao(uid):
    return db.get_default_card_id(uid)


# ── os canais ────────────────────────────────────────────────────────────────

def _app(uid, metodo, rota, **kw):
    client, headers = _cliente_logado(uid)
    if "files" in kw:
        del headers["Content-Type"]
    r = getattr(client, metodo)(rota.format(uid=uid), headers=headers, **kw)
    return r.json().get("detail") if r.status_code == 400 else f"{r.status_code} {r.text}"


def _upload(uid, nome, corpo):
    return _app(uid, "post", "/ofx/import/{uid}", files={"file": (nome, corpo, "application/x-ofx")})


def _wa_anexo(uid, nome, corpo, tipo):
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text="", message_id="1",
        attachments=[Attachment(filename=nome, content_type=tipo, data=corpo)],
        external_id=str(uid), raw={}))
    return out[0].text


def _ia(uid, tool, args):
    """Pelo despacho do runner: `validate` e confirmação; e, se a IA pediu
    confirmação, o "sim" (`_execute_pending`)."""
    _hist, terminal = runner._dispatch_tool(uid, tool, args)
    if terminal is not None:
        return terminal
    return runner._execute_pending(uid, {"tool_name": tool, "tool_args": args})


def _ia_sem_validate(uid, tool, args):
    """A trava do `db` sozinha, sem o `validate` da tool na frente."""
    return get_tool(tool).execute(uid, args)


def _responde_o_nome(uid, resposta):
    perguntou = (db.get_pending_action(uid) or {}).get("action_type") == "installment_pending"
    return manda(uid, "Geladeira") if perguntou else resposta


CAMINHOS = {
    # /app
    "app_criar_investimento": ("investimento", lambda u: _app(
        u, "post", "/investments/{uid}", json={"name": "Tesouro Selic", "rate": 1.0, "period": "cdi"})),
    "app_criar_investimento_com_aporte": ("investimento", lambda u: _app(
        u, "post", "/investments/{uid}",
        json={"name": "LCI Inter", "rate": 0.9, "period": "cdi", "initial_amount": 100})),
    "app_aportar": ("investimento", lambda u: _app(
        u, "post", "/investments/{uid}/deposit", json={"name": "CDB Nubank", "amount": 100})),
    "app_ofx_extrato": ("extrato", lambda u: _upload(
        u, "extrato.ofx", _OFX_BANCO.format(acct=u % 100000).encode())),
    "app_ofx_fatura": ("cartao", lambda u: _upload(
        u, "fatura.ofx", _OFX_CARTAO.format(acct=u % 100000).encode())),
    "app_credito_a_vista": ("cartao", lambda u: _app(
        u, "post", "/launches/{uid}", json={"tipo": "credito", "valor": 50, "card_id": _cartao(u)})),
    "app_credito_parcelado": ("cartao", lambda u: _app(
        u, "post", "/launches/{uid}",
        json={"tipo": "credito", "valor": 300, "card_id": _cartao(u), "parcelas": 3})),
    # WhatsApp, pelo handle_incoming
    "wa_investi": ("investimento", lambda u: manda(u, "investi 1000 no CDB")),
    "wa_aportei": ("investimento", lambda u: manda(u, "aportei 250,50 no CDB Nubank")),
    "wa_ofx_extrato": ("extrato", lambda u: _wa_anexo(
        u, "extrato.ofx", _OFX_BANCO.format(acct=u % 100000).encode(), "application/x-ofx")),
    "wa_ofx_fatura": ("cartao", lambda u: _wa_anexo(
        u, "fatura.ofx", _OFX_CARTAO.format(acct=u % 100000).encode(), "application/x-ofx")),
    "wa_csv": ("extrato", lambda u: _wa_anexo(u, "extrato.csv", _CSV, "text/csv")),
    "wa_cartao": ("cartao", lambda u: manda(u, "gastei 80 no cartão nubank no açougue")),
    "wa_credito_compacto": ("cartao", lambda u: manda(u, "credito 120 mercado")),
    "wa_parcelar": ("cartao", lambda u: manda(u, "parcelar 300 em 3x tv no cartao nubank")),
    # sem descrição o bot pergunta o nome; com a chave a recusa vem antes da
    # pergunta (`test_fonte_unica_q36_cartao.py` prova que não pergunta)
    "wa_parcelar_sem_nome": ("cartao", lambda u: _responde_o_nome(
        u, manda(u, "parcelar 300 em 3x no cartao nubank"))),
    # IA (chat do painel e do WhatsApp usam o mesmo runner)
    "ia_criar_investimento": ("investimento", lambda u: _ia(
        u, "create_investment", {"name": "Tesouro IPCA", "rate": 6.5, "period": "yearly"})),
    "ia_aportar": ("investimento", lambda u: _ia(
        u, "investment_deposit", {"name": "CDB Nubank", "amount": 100})),
    "ia_criar_investimento_sem_validate": ("investimento", lambda u: _ia_sem_validate(
        u, "create_investment", {"name": "Tesouro IPCA", "rate": 6.5, "period": "yearly"})),
    "ia_aportar_sem_validate": ("investimento", lambda u: _ia_sem_validate(
        u, "investment_deposit", {"name": "CDB Nubank", "amount": 100})),
    "ia_cartao": ("cartao", lambda u: _ia(
        u, "add_credit_purchase", {"valor": 45, "descricao": "farmácia"})),
}


@pytest.mark.parametrize("caminho", sorted(CAMINHOS))
def test_com_a_chave_recusa_e_nao_grava(caminho, uid, chave, ia_fora):
    caso, agir = CAMINHOS[caminho]
    chave(uid)
    antes = escritas(uid)
    resposta = agir(uid)
    assert resposta.removeprefix("🐷 ") == MENSAGENS[caso], resposta
    assert escritas(uid) == antes
    # a IA não pede "confirma?" para o que vai recusar
    assert db.ai_get_pending_action(uid) is None


@pytest.mark.parametrize("caminho", sorted(CAMINHOS))
def test_chave_de_outro_usuario_nao_afeta(caminho, uid, chave, ia_fora):
    """Controle positivo e isolamento: a chave ligada para OUTRO usuário não
    trava este, e o caminho grava de verdade."""
    caso, agir = CAMINHOS[caminho]
    chave(usuario_pagante())
    antes = escritas(uid)
    resposta = agir(uid)
    assert MENSAGENS[caso] not in resposta, resposta
    assert escritas(uid) != antes, resposta


@pytest.mark.parametrize("caminho", ["app_criar_investimento", "app_ofx_extrato", "wa_aportei",
                                     "wa_cartao", "ia_aportar"])
def test_chave_falhando_nao_bloqueia(caminho, uid, monkeypatch, ia_fora):
    """Fail-open: a checagem da chave estourando libera a escrita."""
    import core.services.plan_service as ps

    def _quebra(*_a, **_k):
        raise RuntimeError("banco piscou")
    monkeypatch.setattr(ps, "dashboard_v2_enabled", _quebra)
    antes = escritas(uid)
    resposta = CAMINHOS[caminho][1](uid)
    assert escritas(uid) != antes, resposta


def test_wa_criar_investimento_com_a_chave_nao_manda_pro_painel(uid, chave, ia_fora):
    """O `criar investimento` do WhatsApp nunca gravou (manda para o painel);
    com a chave, o painel também recusa, então a resposta é a da Q36."""
    assert "dashboard" in manda(uid, "criar investimento Tesouro Selic")
    chave(uid)
    assert manda(uid, "criar investimento Tesouro Selic") == MENSAGENS["investimento"]


# ── liberados com a chave ────────────────────────────────────────────────────

def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_liberados_no_app_com_a_chave(uid, chave):
    chave(uid)
    client, h = _cliente_logado(uid)
    _ok(client.post(f"/launches/{uid}", headers=h,
                    json={"tipo": "despesa", "valor": 30, "alvo": "feira", "funding_source": "carteira"}))
    _ok(client.post(f"/account/{uid}/adjust-balance", headers=h, json={"target_balance": 4000}))
    assert float(db.get_consolidated_balance(uid)["manual"]) == 4000
    _ok(client.post(f"/pockets/{uid}", headers=h, json={"name": "Viagem"}))
    _ok(client.post(f"/pockets/{uid}/Viagem/deposit", headers=h, json={"amount": 200}))
    _ok(client.post(f"/pockets/{uid}/Viagem/withdraw", headers=h, json={"amount": 50}))
    # resgatar e apagar o manual antigo é a saída do usuário
    db.set_balance(uid, Decimal("0"))
    _ok(client.post(f"/investments/{uid}/withdraw", headers=h,
                    json={"name": "CDB Nubank", "withdraw_all": True}))
    _ok(client.delete(f"/investments/{uid}/CDB%20Nubank", headers=h))
    assert _n("select count(*) n from investments where user_id=%s", uid) == 0


def test_desfazer_apagar_investimento_restaura_com_a_chave(uid, chave):
    """Liberado de propósito (decisão do dono): o desfazer devolve o que o usuário
    já tinha, sem dinheiro novo; apagar sem querer não pode perder o dado."""
    chave(uid)
    client, h = _cliente_logado(uid)
    _ok(client.post(f"/investments/{uid}/withdraw", headers=h,
                    json={"name": "CDB Nubank", "withdraw_all": True}))
    campos = lambda: [(i["name"], i["balance"], i["rate"], i["period"])  # noqa: E731
                      for i in db.list_investments(uid, include_lots=False)]
    antes = campos()
    launch_id = _ok(client.delete(f"/investments/{uid}/CDB%20Nubank", headers=h))["launch_id"]
    assert campos() == []
    _ok(client.delete(f"/launches/{uid}/{launch_id}", headers=h))
    assert campos() == antes
    assert CAMINHOS["app_criar_investimento"][1](uid) == MENSAGENS["investimento"]


def test_saldo_inicial_liberado_com_a_chave(chave):
    u = usuario_pagante()
    chave(u)
    client, h = _cliente_logado(u)
    assert _ok(client.post(f"/account/{u}/initial-balance", headers=h, json={"amount": 300}))["balance"] == 300


def test_open_finance_importa_com_a_chave(uid, chave):
    chave(uid)
    _importa_of_tx(uid, today_tz(), "42.00", "PADARIA", f"tx-q36-{uid}")
    assert _n("select count(*) n from launches where user_id=%s and source='open_finance'", uid) == 1
    _compra_na_conexao(uid, f"item-q36-{uid}")
    assert _n("select count(*) n from credit_transactions where user_id=%s", uid) == 1


def test_ia_resgate_e_caixinha_liberados_com_a_chave(uid, chave):
    chave(uid)
    assert _ia(uid, "create_pocket", {"name": "Reserva"}) and \
        _n("select count(*) n from pockets where user_id=%s", uid) == 1
    _ia(uid, "investment_withdraw", {"name": "CDB Nubank", "withdraw_all": True})
    assert _n("select count(*) n from launches where user_id=%s and tipo='resgate_investimento'", uid) == 1


# ── duas telas cruzadas: o recusado não gravou e a Carteira reflete o dinheiro vivo ──

def test_app_recusa_investimento_e_ofx_e_carteira_reflete_dinheiro_vivo(uid, chave):
    chave(uid)
    client, h = _cliente_logado(uid)
    antes = escritas(uid)
    assert CAMINHOS["app_criar_investimento"][1](uid) == MENSAGENS["investimento"]
    assert CAMINHOS["app_ofx_extrato"][1](uid) == MENSAGENS["extrato"]
    assert escritas(uid) == antes
    _ok(client.post(f"/launches/{uid}", headers=h,
                    json={"tipo": "despesa", "valor": 30, "alvo": "feira", "funding_source": "carteira"}))
    contas = _ok(client.get("/api/v2/contas"))
    assert Decimal(contas["carteira"]["saldo"]) == Decimal("4970")
    assert escritas(uid)[2] == antes[2] + 1  # só o lançamento de dinheiro vivo


# ── a conversa, pelo handle_incoming ─────────────────────────────────────────

@pytest.mark.parametrize("pedido", ["investi 1000 no CDB", "Investi 1.000,00 no CDB do Itaú",
                                    "apliquei 500 no tesouro"])
def test_conversa_investe_recusa_e_dinheiro_vivo_grava(pedido, uid, chave, ia_fora):
    """("Investi R$ 1.000" com o "R$" não casa o classificador e vai para a IA,
    que é o caminho `ia_aportar` acima.)"""
    chave(uid)
    antes = escritas(uid)
    assert manda(uid, pedido) == MENSAGENS["investimento"]
    assert escritas(uid) == antes
    r = manda(uid, "gastei 50 em dinheiro no café")
    assert escritas(uid)[2] == antes[2] + 1, r
    assert float(db.get_consolidated_balance(uid)["manual"]) == 4950, r
    assert not ia_fora, ia_fora


# ── inventário: todo escritor de investimento/cartão/extrato está classificado ──

_SQL = re.compile(r"insert\s+into\s+(investments|investment_lots|credit_transactions)\b", re.I)
_CHAMADAS = {"_insert_investment_lot", "import_ofx_launches_bulk", "import_credit_ofx_bulk",
             "import_ofx_bytes", "import_credit_ofx_bytes", "import_statement_bytes"}
TRAVA = "chama exigir()"
ESCRITORES = {
    ("db/investments.py", "create_investment"): TRAVA,
    ("db/investments.py", "create_investment_db"): TRAVA,
    ("db/investments.py", "investment_deposit_from_account"): TRAVA,
    ("db/cards.py", "add_credit_purchase"): TRAVA,
    ("db/cards.py", "add_credit_purchase_installments"): TRAVA,
    ("core/services/ofx_service.py", "handle_ofx_import"): TRAVA,
    ("core/services/ofx_service.py", "handle_credit_ofx_import"): TRAVA,
    ("core/services/statement_service.py", "handle_statement_import"): TRAVA,
    ("db/investments.py", "_insert_investment_lot"): "lote: só pelos escritores desta tabela",
    ("db/investments.py", "_ensure_investment_lots"): "backfill de lote de investimento existente",
    ("ofx_import.py", "import_ofx_bytes"): "só pelo handle_ofx_import",
    ("ofx_credit_import.py", "import_credit_ofx_bytes"): "só pelo handle_credit_ofx_import",
    ("statement_import.py", "import_statement_bytes"): "só pelo handle_statement_import",
    ("db/cards.py", "import_credit_ofx_bulk"): "só pelo import_credit_ofx_bytes",
    ("db/cards.py", "add_imported_credit_purchase"): "Open Finance (liberado)",
    ("db/cards.py", "add_credit_refund"): "sem chamador em produção",
    ("db/accounts.py", "delete_launch_and_rollback"): (
        "liberado de propósito (dono): desfazer o apagar investimento restaura o que já existia"),
}


def _proprios(fn):
    pilha = list(ast.iter_child_nodes(fn))
    while pilha:
        n = pilha.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield n
        pilha.extend(ast.iter_child_nodes(n))


def _nome(call):
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)


def test_todo_escritor_esta_classificado_e_os_da_trava_chamam_exigir():
    """Escritor novo (INSERT nessas tabelas, ou chamada aos importadores de
    arquivo) reprova até entrar na tabela acima — com a trava ou com o motivo
    de ficar livre."""
    achados, chamam_exigir = set(), set()
    arquivos = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True,
                              check=True).stdout.split()
    for path in arquivos:
        if path.startswith(("tests/", "scripts/", "harness_tests/")):
            continue
        for fn in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in _proprios(fn):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and _SQL.search(n.value):
                    achados.add((path, fn.name))
                if isinstance(n, ast.Call) and _nome(n) in _CHAMADAS:
                    achados.add((path, fn.name))
                if isinstance(n, ast.Call) and _nome(n) == "exigir":
                    chamam_exigir.add((path, fn.name))
    assert achados == set(ESCRITORES), (achados ^ set(ESCRITORES))
    assert {k for k, v in ESCRITORES.items() if v == TRAVA} <= chamam_exigir
