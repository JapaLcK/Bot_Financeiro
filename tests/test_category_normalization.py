"""
tests/test_category_normalization.py — texto livre de categoria colapsa numa
forma só (`resolve_category_input`) nas portas que CHAMAM esse resolver — não
em todas as que gravam categoria.

A porta 9 (gasto fixo / receita recorrente) era a dívida citada aqui e foi
fechada pela issue #147: as 4 portas de escrita (`create_/update_recurring_expense`,
`create_/update_recurring_income`) passaram a chamar o resolver como porta de
CORREÇÃO (`db/categories.resolve_category_for_write` + `ensure_user_category` depois
do commit), e o cobrador (`core/services/recurring_charger._canonical_category`)
resolve com `create=False` — em job de lote `create=True` abortaria a cobrança num
erro de banco e tiraria o acento de quem não pode ter categoria custom.

Continua FORA deste invariante, de propósito: o Open Finance (issue #149) grava
`launches.categoria` por caminho próprio. Enquanto ele existir, `launches.categoria`
NÃO é sempre igual a algum `user_categories.name`, e não há teste aqui afirmando
esse invariante global — o vermelho convidaria a renormalizar na LEITURA, que é
exatamente o que o #143 desfez. Os testes são por PORTA.

Caso real que originou o arquivo: o usuário corrige um lançamento pelo WhatsApp
digitando "McDonald's" e o lançamento virava `mcdonald s` — apóstrofo virava
espaço, e a categoria `mcdonald's` que ele tinha criado na tela ganhava uma
fatia gêmea no dashboard.

ESCOPO: só a forma gravada (bug A). Fazer a correção manual ENSINAR (bug B) é
PR próprio — a especificação do dono é que override manual tem escopo restrito
e precedência controlada, e nunca sobrescreve keyword canônica globalmente.

Convenção travada aqui (não "conserte"):
  user_categories.name      → forma de exibição (minúscula, COM acento/pontuação)
  launches.categoria        → a grafia do catálogo, quando a porta resolve
                              (exceção conhecida: Open Finance, #149)
  user_category_rules.category → índice interno, NORMALIZADO (sem acento)

Duas invariantes que valem pra TODA porta e são testadas uma a uma:
  • a linha em `user_categories` só nasce DEPOIS de o UPDATE dar certo
    (alvo inexistente = 404 e catálogo intacto);
  • o valor gravado é uma grafia que JÁ existe no catálogo do usuário, ou o
    texto CRU — nunca `normalize_text(raw)`. Quem não pode ter categoria custom
    (hoje: plano INATIVO, ver `resolve_category_for_write`) não tem catálogo pra
    de-duplicar, então tirar o acento dele só CRIA a gêmea, em vez de fundi-la.
    O `resolve_category_input` cru ainda devolve `normalize_text(raw)` no passo 3
    — quem garante a invariante é a porta, que escolhe o `create`.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

import db
from adapters.whatsapp import wa_runtime
from adapters.whatsapp.wa_parse import InboundMessage
from conftest import promote_to_pro
from core.services.category_service import infer_category
from db.categories import (
    CATEGORY_NAME_MAX_LEN,
    create_user_category,
    delete_user_category,
    resolve_category_input,
    update_user_category,
)
import frontend.finance_bot_websocket_custom as dashboard


@pytest.fixture()
def wa_user_id():
    """user_id < 2 bilhões. Acima disso o `_normalize_user_id` do
    handle_incoming COMPRIME o id (hash), e o lançamento cairia num usuário
    diferente do que o wa_runtime usa nas correções. Em produção o
    `get_or_create_canonical_user` já devolve id pequeno; a fixture padrão
    `user_id` sorteia até 10 bilhões."""
    uid = int(uuid.uuid4().int % 1_900_000_000) + 1
    db.ensure_user(uid)
    return promote_to_pro(uid)


# ─── helpers ────────────────────────────────────────────────────────────────


def _novo_launch(user_id: int, *, alvo="mcdonalds", nota="mcdonalds", categoria="alimentação") -> int:
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id=user_id, tipo="despesa", valor=39.90,
        alvo=alvo, nota=nota, categoria=categoria,
    )
    return launch_id


def _categoria_do_launch(user_id: int, launch_id: int) -> str | None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from launches where user_id=%s and id=%s",
                (user_id, launch_id),
            )
            row = cur.fetchone()
    return row["categoria"] if row else None


def _ultimo_launch(user_id: int) -> dict:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, categoria, alvo, nota from launches "
                "where user_id=%s order by id desc limit 1",
                (user_id,),
            )
            return cur.fetchone()


def _nomes_custom(user_id: int) -> list[str]:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select name from user_categories where user_id=%s and is_system=false order by name",
                (user_id,),
            )
            return [r["name"] for r in (cur.fetchall() or [])]


def _todos_os_nomes(user_id: int) -> list[str]:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select name from user_categories where user_id=%s order by name",
                (user_id,),
            )
            return [r["name"] for r in (cur.fetchall() or [])]


def _fatias_do_donut(user_id: int) -> dict[str, int]:
    """O donut agrupa pela string CRUA de `launches.categoria` (`db/accounts.py`,
    `group by coalesce(nullif(categoria,''),'outros')`) — caixa e acento separam
    fatias. É a medição que prova (ou nega) a gêmea."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, count(*) as n from launches "
                " where user_id=%s group by categoria",
                (user_id,),
            )
            return {r["categoria"]: int(r["n"]) for r in cur.fetchall()}


def _regra(user_id: int, keyword: str) -> str | None:
    for kw, cat in db.list_user_category_rules(user_id):
        if kw == keyword:
            return cat
    return None


def _auth(client: TestClient, user_id: int) -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "cat@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return {dashboard.CSRF_HEADER_NAME: "test-csrf-token", "Content-Type": "application/json"}


def _wa(monkeypatch, user_id: int) -> list[str]:
    """Faz `process_message` cair sempre neste user_id e captura as respostas."""
    respostas: list[str] = []
    monkeypatch.setattr(wa_runtime, "get_or_create_canonical_user", lambda p, e: user_id)
    monkeypatch.setattr(
        wa_runtime, "attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": user_id},
    )
    monkeypatch.setattr(wa_runtime, "_seen_recent", lambda message_id: False)
    monkeypatch.setattr(wa_runtime, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wa_runtime, "_send_reply", lambda to, body: respostas.append(body))
    monkeypatch.setattr(
        wa_runtime, "_send_reply_with_optional_buttons",
        lambda to, body, user_id=None: respostas.append(body),
    )
    monkeypatch.setattr(wa_runtime, "send_interactive_buttons", lambda **kw: respostas.append(kw["body"]))
    monkeypatch.setattr(wa_runtime, "send_interactive_list", lambda **kw: respostas.append(kw["body"]))
    return respostas


def _msg(texto: str, user_id: int) -> InboundMessage:
    return InboundMessage(
        wa_id="5511999990000", text=texto, timestamp=None, attachments=[],
        raw={"id": f"wamid.{texto[:8]}.{user_id}", "type": "text"},
    )


def _botao(button_id: str, user_id: int) -> InboundMessage:
    """Toque num botão interativo (mesmo payload que a Meta manda)."""
    return InboundMessage(
        wa_id="5511999990000", text="", timestamp=None, attachments=[],
        raw={
            "id": f"wamid.btn.{button_id}.{user_id}",
            "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": "x"}},
        },
    )


_OFX_BANCO = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>BRL<BANKACCTFROM><BANKID>0001<ACCTID>{acct}<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260801<DTEND>20260831
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260803<TRNAMT>-39.90<FITID>{acct}TX1<MEMO>MCDONALDS 1234</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>100.00<DTASOF>20260831</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

_OFX_CARTAO = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<CCSTMTRS><CURDEF>BRL<CCACCTFROM><ACCTID>{acct}</CCACCTFROM>
<BANKTRANLIST><DTSTART>20260801<DTEND>20260831
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260803<TRNAMT>-39.90<FITID>{acct}CC1<MEMO>MCDONALDS 1234</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>-39.90<DTASOF>20260831</LEDGERBAL></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
"""


# ─── porta 1: WhatsApp "Outra (digitar)" ────────────────────────────────────


def test_wa_corrige_para_nome_custom_existente(pro_user_id):
    """Digitar "McDonald's" tem que reencontrar o `mcdonald's` criado na tela.

    Controle negativo: com `canonicalize_category_label` no lugar do
    `resolve_category_input`, o launch grava `mcdonald s` e este teste falha.
    """
    create_user_category(pro_user_id, "McDonald's")
    launch_id = _novo_launch(pro_user_id)

    wa_runtime._apply_recategorize(pro_user_id, launch_id, "McDonald's")

    assert _categoria_do_launch(pro_user_id, launch_id) == "mcdonald's"
    # e não criou gêmea
    assert _nomes_custom(pro_user_id) == ["mcdonald's"]


def test_categoria_nova_legitima_e_criada(pro_user_id):
    """CONTROLE POSITIVO: nome novo continua passando — e vira categoria de
    verdade (com emoji/cor/orçamento possíveis), não texto órfão."""
    launch_id = _novo_launch(pro_user_id)

    wa_runtime._apply_recategorize(pro_user_id, launch_id, "Padaria do Zé")

    assert _categoria_do_launch(pro_user_id, launch_id) == "padaria do zé"
    assert "padaria do zé" in _nomes_custom(pro_user_id)


@pytest.mark.parametrize("digitado", ["Alimentação", "alimentacao", "ALIMENTAÇÃO"])
def test_rotulo_do_sistema_intacto(pro_user_id, digitado):
    """CONTROLE POSITIVO: as três formas colapsam no rótulo do sistema e
    NENHUMA cria linha nova em user_categories."""
    launch_id = _novo_launch(pro_user_id, categoria="outros")

    wa_runtime._apply_recategorize(pro_user_id, launch_id, digitado)

    assert _categoria_do_launch(pro_user_id, launch_id) == "alimentação"
    assert _nomes_custom(pro_user_id) == []


# ─── caminho de LEITURA (passo B do infer_category) ─────────────────────────


def test_regra_custom_volta_com_acento(pro_user_id):
    """Controle negativo: com `canonicalize_category_label(cat)` no passo B, a
    inferência devolve `saude da familia` e este teste falha."""
    create_user_category(pro_user_id, "saúde da família")
    db.upsert_category_rule(pro_user_id, "familia", "saude da familia")

    res = infer_category(pro_user_id, "gastei 100 com a familia", allow_ai=False)

    assert res.reason == "user_rule"
    assert res.category == "saúde da família"


def test_regra_orfa_mantem_comportamento_de_hoje(pro_user_id):
    """CONTROLE POSITIVO do passo B: regra apontando pra categoria que NÃO
    existe em user_categories continua devolvendo o canonicalize de antes."""
    db.upsert_category_rule(pro_user_id, "cinema", "lazer")

    res = infer_category(pro_user_id, "gastei 40 no cinema", allow_ai=False)

    assert res.category == "lazer"


def test_inferencia_nao_escreve_no_banco(pro_user_id):
    """D8a: o caminho quente da inferência é READ-ONLY.

    `user_category_display_map` chegou a semear antes de ler — 15 INSERT em
    toda inferência que bate em regra do usuário, contra o que
    `list_custom_category_names` documenta a duas funções de distância.

    Controle negativo: devolver o `ensure_user_categories_seeded` pro topo do
    `user_category_display_map` faz os dois asserts falharem (o catálogo nasce
    com as 15 canônicas e o INSERT aparece na contagem).
    """
    # a regra tem que apontar pra categoria NÃO canônica: rótulo do sistema é
    # resolvido no passo 1, antes de o mapa ser consultado, e aí o teste não
    # discrimina o seed.
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")

    statements: list[str] = []
    import psycopg
    original = psycopg.Cursor.execute

    def _spy(self, query, *a, **k):
        q = " ".join((query.decode() if isinstance(query, bytes) else str(query)).lower().split())
        statements.append(q)
        return original(self, query, *a, **k)

    psycopg.Cursor.execute = _spy
    try:
        res = infer_category(pro_user_id, "gastei 39,90 no mcdonalds", allow_ai=False)
    finally:
        psycopg.Cursor.execute = original

    escritas = [q[:60] for q in statements if q.split(" ", 1)[0] in {"insert", "update", "delete"}]
    assert res.reason == "user_rule"
    # os 2 INSERT que sobram são o `ensure_user` que já existia na main
    # (users/accounts, on conflict do nothing) — nenhum toca user_categories.
    assert [q for q in escritas if "user_categories" in q] == [], escritas
    # 5, não 4: o guard de regra obsoleta (`_regra_ficou_obsoleta`) faz UMA
    # leitura a mais — `list_custom_category_names`, para saber se alguma
    # categoria custom passou a ser dona do keyword. É leitura, e o assert de
    # cima continua provando que nada escreve. O teto segue valendo como
    # tripwire: o seed que este teste nasceu para pegar levaria a 22.
    assert len(statements) <= 5, len(statements)   # main: 3 · com o seed: 22
    assert _todos_os_nomes(pro_user_id) == []


def test_display_map_desempate_nao_depende_de_id(pro_user_id):
    """D8b: com gêmeas ("cafe" e "café") o resultado não pode mudar quando a
    mais nova é apagada — antes o `order by id` fazia a resposta depender de
    quem foi criado/apagado por último."""
    velha = create_user_category(pro_user_id, "Cafe")
    nova = create_user_category(pro_user_id, "Café")

    antes = resolve_category_input(pro_user_id, "cafe")
    delete_user_category(pro_user_id, nova["id"])
    depois = resolve_category_input(pro_user_id, "cafe")

    assert antes == depois
    assert velha["name"] == "cafe"   # grafia digitada, minúscula (a "Café" é a nova)


def test_display_map_com_banco_fora_degrada_so_na_leitura(pro_user_id, monkeypatch):
    """As 3 chamadas de importação de extrato (`create=False`) degradam: mapa
    vazio, grava o normalizado — o import não dependia de `user_categories`
    antes deste PR e não pode abortar o arquivo por um enfeite de grafia.

    As portas de correção (`create=True`) NÃO degradam: ali o mapa vazio não
    acha a categoria que o usuário já tem e o nome digitado é gravado como se
    fosse outro — vira fatia gêmea no dashboard, que agrupa por
    `launches.categoria` (`db/accounts.py:601`).

    Controle negativo: tirando o `strict=create` do `resolve_category_input`,
    a última linha para de levantar.
    """
    from db import categories as db_categories

    def _boom(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(db_categories, "get_conn", _boom)

    assert db_categories.user_category_display_map(pro_user_id) == {}
    assert db_categories.resolve_category_input(pro_user_id, "Padaria do Zé") is None
    with pytest.raises(RuntimeError):
        db_categories.resolve_category_input(pro_user_id, "Padaria do Zé", create=True)


def test_falha_transitoria_nao_cria_gemea_no_catalogo(pro_user_id, monkeypatch):
    """O mesmo D7 pelo CAMINHO REAL (PATCH /launches), com estado no banco: a
    categoria já existe e a leitura do catálogo PISCA — falha uma vez e volta.

    Medido com o `try/except → {}` (mesma piscada, mesmo request):
        status 200 · launches.categoria = 'cafe da manha' · catálogo = ['café da manhã']
    Ou seja: 200 com dado sujo — o lançamento aponta pra uma string que não é
    o nome do catálogo, e o dashboard, que agrupa por `launches.categoria`
    (`db/accounts.py:601`), passa a mostrar DUAS fatias do mesmo café.
    (A linha gêmea em `user_categories` não chega a nascer porque o
    `ensure_user_category` já reencontra o mapa; o estrago é o lançamento.)

    Com a correção: 500, lançamento intacto, nada gravado.
    Controle negativo: devolvendo o `return {}` incondicional ao
    `user_category_display_map`, o status vira 200 e o último assert cai.
    """
    from db import categories as db_categories

    create_user_category(pro_user_id, "Café da Manhã")
    launch_id = _novo_launch(pro_user_id)

    original = db_categories.get_conn
    restantes = {"falhas": 1}

    def _piscada(*a, **k):
        if restantes["falhas"]:
            restantes["falhas"] -= 1
            raise RuntimeError("banco piscou")
        return original(*a, **k)

    monkeypatch.setattr(db_categories, "get_conn", _piscada)

    client = TestClient(dashboard.app, raise_server_exceptions=False)
    headers = _auth(client, pro_user_id)
    resp = client.patch(
        f"/launches/{pro_user_id}/{launch_id}",
        json={"categoria": "Cafe da Manha"}, headers=headers,
    )

    assert restantes["falhas"] == 0, "a piscada não chegou a acontecer"
    assert resp.status_code >= 500, resp.text[:200]
    assert _nomes_custom(pro_user_id) == ["café da manhã"]
    assert _categoria_do_launch(pro_user_id, launch_id) == "alimentação"


# ─── porta 2: PATCH /launches ───────────────────────────────────────────────


def test_patch_launch_inexistente_nao_cria_categoria(pro_user_id):
    """D5: a linha em user_categories só nasce DEPOIS do UPDATE. Antes, um id
    inexistente devolvia 404 e ainda assim sujava o catálogo — em loop, o
    usuário perdia a tela de categorias."""
    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)

    resp = client.patch(
        f"/launches/{pro_user_id}/999999999",
        json={"categoria": "categoria fantasma"},
        headers=headers,
    )

    assert resp.status_code == 404
    assert _nomes_custom(pro_user_id) == []


# Entradas que furam o teto quando ele é medido no NORMALIZADO em vez de no
# nome gravado. `normalize_text` tira emoji/pontuação e encolhe: o 3º caso
# normaliza pra 1 caractere e gravava 5001 no `launches.categoria` — que pelo
# WhatsApp vira resposta acima do limite de 4096 da Meta.
_NOMES_GIGANTES = [
    "A" * (CATEGORY_NAME_MAX_LEN + 1),
    "A" * 3000,
    "🐷" * 5000 + "a",
    "." * 5000 + "a",
]


@pytest.mark.parametrize("nome", _NOMES_GIGANTES)
def test_nome_de_categoria_gigante_recusado(pro_user_id, nome):
    """D6: `CATEGORY_NAME_MAX_LEN` é a fonte única — as 5 portas recusam pelo
    mesmo número, medido sobre o que VAI SER GRAVADO.

    Controle negativo: medir `normalize_text(raw)` (como na rodada 2) deixa os
    dois últimos casos passarem com 5001 caracteres no banco.
    """
    launch_id = _novo_launch(pro_user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)

    resp = client.patch(
        f"/launches/{pro_user_id}/{launch_id}",
        json={"categoria": nome},
        headers=headers,
    )

    assert resp.status_code == 400, resp.text[:200]
    assert _nomes_custom(pro_user_id) == []
    assert _categoria_do_launch(pro_user_id, launch_id) == "alimentação"


def test_nome_gigante_recusado_no_whatsapp(wa_user_id, monkeypatch):
    """Mesma entrada pela porta do WhatsApp: a resposta tem que caber no limite
    de 4096 caracteres da Meta e o lançamento não pode mudar.

    Controle POSITIVO junto: um nome normal passa pela mesma porta.
    """
    respostas = _wa(monkeypatch, wa_user_id)
    launch_id = _novo_launch(wa_user_id)

    recusa = wa_runtime._apply_recategorize(wa_user_id, launch_id, "🐷" * 5000 + "a")
    assert len(recusa) < 4096
    assert _categoria_do_launch(wa_user_id, launch_id) == "alimentação"

    ok = wa_runtime._apply_recategorize(wa_user_id, launch_id, "Lazer")
    assert len(ok) < 4096
    assert _categoria_do_launch(wa_user_id, launch_id) == "lazer"
    assert respostas == []


def test_free_nao_cria_categoria_custom(user_id):
    """D4: `POST /categories` exige plano pago (custom_categories). As portas
    de correção criavam a MESMA entidade sem gate.

    Comportamento pra quem não tem o plano: grava o texto no lançamento SEM
    ACENTO, exatamente como a `main`, e NÃO cria linha no catálogo.
    """
    launch_id = _novo_launch(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    negado = client.post(
        f"/categories/{user_id}", json={"name": "padaria do zé"}, headers=headers,
    )
    assert negado.status_code == 403, negado.text

    resp = client.patch(
        f"/launches/{user_id}/{launch_id}",
        json={"categoria": "Padaria do Zé"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert _categoria_do_launch(user_id, launch_id) == "padaria do ze"
    assert _nomes_custom(user_id) == []


def test_free_duas_grafias_colapsam_numa_so(user_id):
    """B-1, o furo que este PR TINHA: sem plano pago não nasce linha em
    `user_categories`, e sem catálogo não existe de-duplicação. Preservar a
    grafia fazia "Padaria do Zé" e "Padaria do Ze" virarem DUAS fatias no
    dashboard — onde a `main` colapsava as duas em `padaria do ze`.

    O mesmo usuário corrigindo DUAS vezes é o que os 47 testes da rodada
    anterior nunca faziam; por isso o furo passou.

    Controle negativo: `stored = _normalize_category_name(raw)` incondicional
    (sem o `_custom_categories_allowed`) devolve 'padaria do zé' != 'padaria
    do ze' e o assert de igualdade cai.
    """
    a_id = _novo_launch(user_id)
    b_id = _novo_launch(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    for launch_id, digitado in ((a_id, "Padaria do Zé"), (b_id, "Padaria do Ze")):
        r = client.patch(
            f"/launches/{user_id}/{launch_id}",
            json={"categoria": digitado}, headers=headers,
        )
        assert r.status_code == 200, r.text

    a = _categoria_do_launch(user_id, a_id)
    b = _categoria_do_launch(user_id, b_id)
    assert a == b == "padaria do ze", (a, b)
    assert _nomes_custom(user_id) == []


@pytest.mark.parametrize("porta", ["launches", "credit-transactions", "installments"])
def test_nul_byte_na_categoria_nao_derruba_a_rota(pro_user_id, porta):
    """Regressão que este PR introduziu: `_normalize_category_name` não
    filtrava caracteres de controle e o NUL chegava ao Postgres, que responde
    `psycopg.DataError` — 500 onde a `main` dava 200 (ela passava pelo
    `normalize_text`, que filtra).

    Controle negativo: sem o filtro de Cc no `_normalize_category_name`, as
    duas portas devolvem 500.
    """
    alvo_id = {
        "launches": _novo_launch,
        "credit-transactions": _compra_no_credito,
        "installments": _parcelado,
    }[porta](pro_user_id)

    client = TestClient(dashboard.app, raise_server_exceptions=False)
    headers = _auth(client, pro_user_id)
    resp = client.patch(
        f"/{porta}/{pro_user_id}/{alvo_id}",
        json={"categoria": "a\u0000b"}, headers=headers,
    )

    assert resp.status_code == 200, (resp.status_code, resp.text[:200])
    # e os demais controles (\x01-\x1f) não ficam gravados
    resp2 = client.patch(
        f"/{porta}/{pro_user_id}/{alvo_id}",
        json={"categoria": "cafe\u0001 manha"}, headers=headers,
    )
    assert resp2.status_code == 200, resp2.text[:200]
    assert all(
        ord(c) >= 0x20 for nome in _todos_os_nomes(pro_user_id) for c in nome
    ), _todos_os_nomes(pro_user_id)


def test_invisivel_nao_fica_gravado(pro_user_id):
    """Zero-width (U+200B) e override bidi (U+202E) são INVISÍVEIS e o filtro
    antigo parava em \\x7f. `str.split()` não os vê (não são whitespace), então
    eles ficavam GRAVADOS no lançamento e na linha de `user_categories` —
    "cafe" e "cafe\u200b" viram duas fatias visualmente idênticas no dashboard
    (é o bug que este PR existe pra matar, por outro eixo), e o U+202E ainda
    inverte a ordem do que a tela mostra.

    Digitado UMA vez só, de propósito: na segunda vez o `display_map` já
    reencontra o nome pelo `normalize_text` e o teste passaria sem o conserto.

    Controle negativo: com o filtro só em Cc, o gravado volta com os dois
    invisíveis e os dois asserts caem.
    """
    launch_id = _novo_launch(pro_user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)

    r = client.patch(
        f"/launches/{pro_user_id}/{launch_id}",
        json={"categoria": "\u202ecafe\u200b da manha"}, headers=headers,
    )
    assert r.status_code == 200, r.text

    gravado = _categoria_do_launch(pro_user_id, launch_id)
    assert gravado == "cafe da manha", repr(gravado)
    assert _nomes_custom(pro_user_id) == ["cafe da manha"], _nomes_custom(pro_user_id)


@pytest.mark.parametrize("pago", [False, True])
def test_gate_de_categoria_custom_com_planos_v2(user_id, monkeypatch, pago):
    """O `conftest` força PLANS_V2_ENABLED="0", mas o DEFAULT de produção é
    LIGADO — e o gate saiu do monólito pro `plan_service.plan_gate_ok` neste
    PR. Este é o único teste do PR que roda o ramo v2.

    Grátis não cria categoria custom; pago (tier >= essencial) cria — os dois
    gravam o texto no lançamento de qualquer jeito.
    """
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    if pago:
        promote_to_pro(user_id)
    launch_id = _novo_launch(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    resp = client.patch(
        f"/launches/{user_id}/{launch_id}",
        json={"categoria": "Padaria do Zé"}, headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert _categoria_do_launch(user_id, launch_id) == ("padaria do zé" if pago else "padaria do ze")
    assert _nomes_custom(user_id) == (["padaria do zé"] if pago else [])


# ─── porta 3: PATCH /credit-transactions ────────────────────────────────────


def _compra_no_credito(user_id: int, categoria="outros", nota="mcdonalds") -> int:
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    tx_id, _due, _bill = db.add_credit_purchase(user_id, card_id, 39.90, categoria, nota, date.today())
    return tx_id


def test_credit_transaction_patch_resolve_categoria(pro_user_id):
    """Porta 3 (sem teste nenhum antes): digitar "McDonald's" no dashboard tem
    que reencontrar o `mcdonald's` do catálogo.

    Controle negativo: voltando pro `canonicalize_category_label(raw) or
    raw.lower()`, a compra grava `mcdonald s` e o assert falha.
    """
    create_user_category(pro_user_id, "McDonald's")
    tx_id = _compra_no_credito(pro_user_id)

    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)
    resp = client.patch(
        f"/credit-transactions/{pro_user_id}/{tx_id}",
        json={"categoria": "McDonald's"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from credit_transactions where user_id=%s and id=%s",
                (pro_user_id, tx_id),
            )
            assert cur.fetchone()["categoria"] == "mcdonald's"


def test_installment_categoria_vazia_limpa_o_campo(pro_user_id):
    """Simetria com `nome`: vazio/espaços LIMPA a categoria (o
    `update_installment_group_meta` faz `.strip() or None`).

    Controle negativo: mandar o vazio pro `resolve_category_input` devolve None
    e a rota responde 400 — que foi a regressão da rodada 2.
    """
    group_id = _parcelado(pro_user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)

    resp = client.patch(
        f"/installments/{pro_user_id}/{group_id}",
        json={"categoria": "   "}, headers=headers,
    )

    assert resp.status_code == 200, resp.text
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select distinct categoria from credit_transactions "
                "where user_id=%s and group_id=%s::uuid",
                (pro_user_id, group_id),
            )
            assert [r["categoria"] for r in cur.fetchall()] == [None]


# ─── porta 4: tool da IA ────────────────────────────────────────────────────


def _tool_recategorize(user_id: int, launch_id: int, categoria: str) -> str:
    from core.services.ai_chat.tools.categories import _recategorize_launch_execute
    return _recategorize_launch_execute(
        user_id, {"launch_id": launch_id, "new_category": categoria}
    )


def test_ai_tool_resolve_categoria(pro_user_id):
    """Porta 4 (sem teste nenhum antes). Controle negativo: passando
    `args["new_category"]` cru pro update, grava "McDonald's" com maiúscula e
    apóstrofo fora do catálogo."""
    create_user_category(pro_user_id, "McDonald's")
    launch_id = _novo_launch(pro_user_id)

    _tool_recategorize(pro_user_id, launch_id, "McDonald's")

    assert _categoria_do_launch(pro_user_id, launch_id) == "mcdonald's"
    assert _nomes_custom(pro_user_id) == ["mcdonald's"]


def test_ai_tool_launch_inexistente_nao_cria_categoria(pro_user_id):
    """D5 na porta da IA."""
    _tool_recategorize(pro_user_id, 999999999, "fantasma ia")

    assert _nomes_custom(pro_user_id) == []


# ─── porta 5: PATCH /installments ───────────────────────────────────────────


def _parcelado(user_id: int, card_name: str = "Nubank") -> str:
    card_id = db.create_card(user_id, card_name, closing_day=10, due_day=17)
    info, _total = db.add_credit_purchase_installments(
        user_id, card_id, 120.0, "outros", "mcdonalds", date.today(), 3,
    )
    return info["group_id"]


def test_installment_patch_resolve_categoria(pro_user_id):
    create_user_category(pro_user_id, "McDonald's")
    group_id = _parcelado(pro_user_id)

    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)
    resp = client.patch(
        f"/installments/{pro_user_id}/{group_id}",
        json={"categoria": "McDonald's"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select distinct categoria from credit_transactions "
                "where user_id=%s and group_id=%s::uuid",
                (pro_user_id, group_id),
            )
            cats = [r["categoria"] for r in cur.fetchall()]
    assert cats == ["mcdonald's"]


def test_installment_inexistente_nao_cria_categoria(pro_user_id):
    """D5 na porta do parcelamento."""
    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)

    resp = client.patch(
        f"/installments/{pro_user_id}/{uuid.uuid4()}",
        json={"categoria": "fantasma parcelado"},
        headers=headers,
    )

    assert resp.status_code == 404
    assert _nomes_custom(pro_user_id) == []


# ─── porta 6: importação de extrato (CSV, OFX de conta, OFX de fatura) ──────


def test_import_extrato_nao_cria_gemea(pro_user_id):
    """A regra guarda `mcdonald s`; o extrato tem que gravar `mcdonald's`.

    A categoria de exibição é PRÉ-CRIADA de propósito: a importação resolve
    contra o catálogo, mas nunca CRIA categoria (inferência não cria efeito
    colateral). Sem a linha em user_categories, o import grava o normalizado —
    é o comportamento esperado, não um furo deste teste.

    Controle negativo: sem a conversão no write site, grava `mcdonald s` e cria
    a fatia gêmea no dashboard.
    """
    from statement_import import import_statement_bytes

    create_user_category(pro_user_id, "McDonald's")
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")

    csv_bytes = (
        "Data,Descrição,Valor\n"
        "03/08/2026,MCDONALDS 1234,\"-39,90\"\n"
        "04/08/2026,PADARIA CENTRAL,\"-12,00\"\n"
    ).encode("utf-8")

    rep = import_statement_bytes(pro_user_id, csv_bytes, "extrato.csv", "csv")
    assert rep["inserted"] == 2

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from launches where user_id=%s and nota ilike %s",
                (pro_user_id, "%MCDONALDS%"),
            )
            cats = [r["categoria"] for r in cur.fetchall()]
    assert cats == ["mcdonald's"]


def test_import_ofx_conta_nao_cria_gemea(pro_user_id):
    """Porta 6-ofx (sem teste nenhum antes): mesmo write site do CSV, arquivo
    diferente. Controle negativo: sem o `resolve_category_input` no
    `import_ofx_bytes`, grava `mcdonald s`."""
    from ofx_import import import_ofx_bytes

    create_user_category(pro_user_id, "McDonald's")
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")

    rep = import_ofx_bytes(
        pro_user_id, _OFX_BANCO.format(acct=pro_user_id % 100000).encode(), "extrato.ofx",
    )
    assert rep["inserted"] == 1

    assert _ultimo_launch(pro_user_id)["categoria"] == "mcdonald's"


def test_import_ofx_fatura_nao_cria_gemea(pro_user_id):
    """8ª porta: `ofx_credit_import._categorize` é cópia do `resolve_category`
    e grava direto em credit_transactions.categoria."""
    from ofx_credit_import import import_credit_ofx_bytes

    create_user_category(pro_user_id, "McDonald's")
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)

    rep = import_credit_ofx_bytes(
        pro_user_id, card_id, _OFX_CARTAO.format(acct=pro_user_id % 100000).encode(), "fatura.ofx",
    )
    assert rep["inserted"] == 1

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from credit_transactions where user_id=%s order by id desc limit 1",
                (pro_user_id,),
            )
            assert cur.fetchone()["categoria"] == "mcdonald's"


# ─── porta 7: POST /launches (categoria inferida) ───────────────────────────


def test_launch_novo_usa_grafia_do_catalogo(pro_user_id):
    """Porta 7 (sem teste nenhum antes): o lançamento nasce com a grafia do
    catálogo. Controle negativo: com `canonicalize_category_label(inferred)`
    de volta, nasce `mcdonald s` e abre fatia gêmea já no primeiro gasto."""
    create_user_category(pro_user_id, "McDonald's")
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")

    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)
    resp = client.post(
        f"/launches/{pro_user_id}",
        json={"tipo": "despesa", "valor": 39.9, "alvo": "mcdonalds"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    assert _ultimo_launch(pro_user_id)["categoria"] == "mcdonald's"


def test_compra_no_credito_nova_usa_grafia_do_catalogo(pro_user_id):
    """Mesma porta 7, o outro write site (compra no crédito, :5091)."""
    create_user_category(pro_user_id, "McDonald's")
    db.upsert_category_rule(pro_user_id, "mcdonalds", "mcdonald s")
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)

    client = TestClient(dashboard.app)
    headers = _auth(client, pro_user_id)
    resp = client.post(
        f"/launches/{pro_user_id}",
        json={"tipo": "credito", "valor": 39.9, "alvo": "mcdonalds", "card_id": card_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from credit_transactions where user_id=%s order by id desc limit 1",
                (pro_user_id,),
            )
            assert cur.fetchone()["categoria"] == "mcdonald's"


@pytest.mark.parametrize("reason", ["ai", "explicit"])
def test_categoria_digitada_pela_ia_usa_grafia_do_catalogo(pro_user_id, reason):
    """B-2, a string do chamado. `add_from_entities` é por onde entram a tool
    `add_launch` (reason="ai") e a hashtag `#McDonald's` (reason="explicit").

    Medido antes desta correção, usuário Pro que JÁ TEM "McDonald's":
        reason=ai       -> 'mcdonald s'   (canonicalize_category_label)
        reason=explicit -> "McDonald's"   (texto cru, com maiúscula)
    As duas abriam fatia gêmea no PRIMEIRO lançamento.

    Controle negativo: `canonicalize_category_label(categoria) or categoria` de
    volta em `core/handlers/launches.py` derruba o caso "ai"; devolver
    `categoria_final = categoria` cru derruba o "explicit".
    """
    from core.handlers.launches import add_from_entities

    create_user_category(pro_user_id, "McDonald's")
    # alvo sem regra local: senão o cross-check do ramo "ai" (que existe e tem
    # de continuar existindo) sobrepõe a categoria da IA e o teste mede outra coisa.
    add_from_entities(
        pro_user_id, tipo="despesa", valor=39.9,
        alvo="zzq comercio", nota="zzq comercio",
        categoria="McDonald's", category_reason=reason,
    )

    assert _ultimo_launch(pro_user_id)["categoria"] == "mcdonald's"


def test_cross_check_da_ia_continua_de_pe(pro_user_id):
    """CONTROLE POSITIVO do B-2: rotear o ramo "ai" pelo `infer_category` não
    pode desligar o cross-check — regra do usuário que contradiz a IA vence."""
    from core.handlers.launches import add_from_entities

    db.upsert_category_rule(pro_user_id, "zzq comercio", "moradia")
    add_from_entities(
        pro_user_id, tipo="despesa", valor=39.9,
        alvo="zzq comercio", nota="zzq comercio",
        categoria="Alimentação", category_reason="ai",
    )

    assert _ultimo_launch(pro_user_id)["categoria"] == "moradia"


# ─── conversa: handle_incoming de verdade, estado real no banco ─────────────


def _mock_gpt(monkeypatch, categoria: str) -> None:
    """Faz o passo E do infer_category devolver `categoria` sem rede."""
    import ai_router
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ai_router, "classify_category_with_gpt", lambda *a, **k: categoria)
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: True)


def test_round_trip_duas_grafias_uma_categoria(wa_user_id, monkeypatch):
    """Round trip do bug A: duas grafias do MESMO nome, corrigidas pelo mesmo
    usuário em dois lançamentos, gravam a MESMA string e deixam UMA linha no
    catálogo.

    Quatro mensagens, um usuário, estado real no banco — a segunda correção só
    reencontra a categoria porque a primeira criou a linha.

    Controle negativo: com `canonicalize_category_label(cat) or cat.lower()` de
    volta no `_apply_recategorize` (o código da `main`), grava `padaria do ze`
    nas duas e o catálogo fica vazio — os dois asserts caem.
    """
    respostas = _wa(monkeypatch, wa_user_id)
    _mock_gpt(monkeypatch, "alimentação")

    wa_runtime.process_message(_msg("gastei 39,90 no mcdonalds", wa_user_id))
    primeiro = _ultimo_launch(wa_user_id)
    assert primeiro is not None and primeiro["categoria"] == "alimentação", respostas

    wa_runtime.process_message(_msg("gastei 20 no mcdonalds", wa_user_id))
    segundo = _ultimo_launch(wa_user_id)
    assert segundo["id"] != primeiro["id"], respostas

    # "Padaria do Zé" e "Padaria do Ze" normalizam pra mesma chave
    for launch_id, digitado in ((primeiro["id"], "Padaria do Zé"), (segundo["id"], "Padaria do Ze")):
        wa_runtime.process_message(_botao(f"{wa_runtime.WA_RECAT_OTHER_PREFIX}{launch_id}", wa_user_id))
        wa_runtime.process_message(_msg(digitado, wa_user_id))

    a = _categoria_do_launch(wa_user_id, primeiro["id"])
    b = _categoria_do_launch(wa_user_id, segundo["id"])
    assert a == b == "padaria do zé", (a, b, respostas)
    assert _nomes_custom(wa_user_id) == ["padaria do zé"]


@pytest.mark.parametrize("parcelas", [1, 3])  # à vista e parcelado: dois call sites
def test_compra_no_credito_sem_nota_nao_aprende_o_cartao(wa_user_id, monkeypatch, parcelas):
    """B1, o OUTRO site (pré-existente na main): a compra no crédito criada sem
    nota nem alvo aprendia com `target_hint=alvo or card_name` e com a nota
    gerada "compra no crédito (Nubank)" — nascendo a regra `nubank → X`, que
    casa por substring e sequestra todo gasto que cite o cartão.

    Controle negativo: voltando `target_hint=alvo or card_name` (e `nota` como
    text_base), a regra reaparece e os dois asserts caem.
    """
    _mock_gpt(monkeypatch, "compras online")
    card_id = db.create_card(wa_user_id, "Nubank", closing_day=10, due_day=17)

    client = TestClient(dashboard.app)
    headers = _auth(client, wa_user_id)
    resp = client.post(
        f"/launches/{wa_user_id}",
        json={"tipo": "credito", "valor": 300, "card_id": card_id, "parcelas": parcelas},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    assert db.list_user_category_rules(wa_user_id) == []
    assert infer_category(wa_user_id, "gastei 300 no mercado com o nubank", allow_ai=False).reason != "user_rule"


def test_erro_de_banco_na_correcao_nao_come_o_turno(wa_user_id, monkeypatch):
    """D7: a pendência já foi apagada quando `_apply_recategorize` roda. Se a
    leitura do catálogo estourar, o turno morria em silêncio — o usuário
    digitava a categoria e não recebia resposta nenhuma.

    Controle negativo: tirando o try/except em volta do
    `resolve_category_input`, a exceção sobe até o handler de topo (que só
    loga) e `respostas` fica sem a mensagem de erro.
    """
    respostas = _wa(monkeypatch, wa_user_id)
    launch_id = _novo_launch(wa_user_id)

    def _boom(*a, **k):
        raise RuntimeError("banco caiu no meio")

    wa_runtime.process_message(_botao(f"{wa_runtime.WA_RECAT_OTHER_PREFIX}{launch_id}", wa_user_id))
    monkeypatch.setattr(db, "resolve_category_input", _boom)
    wa_runtime.process_message(_msg("McDonald's", wa_user_id))

    assert any("Não consegui atualizar agora" in r for r in respostas), respostas


def test_botao_outra_digitar_atropela_pendencia(wa_user_id, monkeypatch):
    """Documenta o estado ATUAL pelo CAMINHO REAL (toque no botão "Outra
    (digitar)" → `process_message`), não por um `set_pending_action` do
    próprio teste: o botão grava `recategorize_launch_text` CRU
    (`wa_runtime.py`), fora da disciplina claim/advance do CLAUDE.md §5, e
    apaga a pergunta de outro fluxo que estava de pé.

    Não é regressão deste PR; é o comportamento de hoje, escrito pra virar
    issue separada. Quando o atropelo for consertado, este teste vira o
    contrário — e é ele quem avisa.
    """
    respostas = _wa(monkeypatch, wa_user_id)
    launch_id = _novo_launch(wa_user_id)

    # pergunta de outro fluxo, de pé
    db.claim_pending_action(wa_user_id, "bill_amount_expected", {"bill_id": 41, "bill_name": "Luz"})

    # o usuário toca "Outra (digitar)" — o botão grava por cima
    wa_runtime.process_message(_botao(f"{wa_runtime.WA_RECAT_OTHER_PREFIX}{launch_id}", wa_user_id))
    assert db.get_pending_action(wa_user_id)["action_type"] == "recategorize_launch_text", respostas

    wa_runtime.process_message(_msg("McDonald's", wa_user_id))

    assert _categoria_do_launch(wa_user_id, launch_id) == "mcdonald's", respostas
    # a pergunta da conta se perdeu (estado atual, documentado)
    assert db.get_pending_action(wa_user_id) is None


# ─── porta 9: gasto fixo recorrente (issue #147) ────────────────────────────
#
# Dois lados. As 4 portas de ESCRITA resolvem na hora do cadastro/edição
# (`create=True`, porta de correção — é o usuário digitando). O COBRADOR resolve
# na hora de copiar pra `launches.categoria` (`create=False`), porque em produção
# já existem 11 de 15 recorrentes ativos com categoria fora do catálogo do dono —
# escrita consertada não reescreve o que já está gravado.


def _forca_categoria_crua(tabela: str, user_id: int, rec_id: int, categoria: str) -> None:
    """Deixa a linha do recorrente no estado LEGADO: categoria como o usuário
    digitou, sem passar pelo resolver. É o estado dos 11 recorrentes de produção
    e o único jeito de exercitar o cobrador depois que a escrita foi consertada."""
    assert tabela in ("recurring_expenses", "recurring_incomes")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update {tabela} set category=%s where user_id=%s and id=%s",
                (categoria, user_id, rec_id),
            )
        conn.commit()


def _gasto_fixo_legado(user_id: int, categoria_crua: str, **kw) -> int:
    from db.recurring import create_recurring_expense
    from utils_date import today_tz

    hoje = today_tz()
    rec = create_recurring_expense(
        user_id, "Assinatura", 21.90, "outros", hoje.day,
        kw.pop("payment_type", "account"), start_date=hoje, **kw,
    )
    _forca_categoria_crua("recurring_expenses", user_id, rec["id"], categoria_crua)
    return int(rec["id"])


def test_cobranca_usa_grafia_do_catalogo(pro_user_id):
    """NEGATIVO (despesa): o gasto fixo legado guarda "McDonald's"; o catálogo do
    dono tem "mcdonald's". O launch da cobrança tem de sair com a grafia do
    catálogo, senão o donut abre duas fatias todo mês.

    Controle negativo medido: com `category = rec["category"] or "outros"` de
    volta em `recurring_charger._charge_one`, o assert abaixo lê "McDonald's"."""
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz

    create_user_category(pro_user_id, "mcdonald's")
    _gasto_fixo_legado(pro_user_id, "McDonald's")

    charge_due_recurring_expenses_once(today_tz())

    assert _ultimo_launch(pro_user_id)["categoria"] == "mcdonald's"


def test_cobranca_no_cartao_usa_grafia_do_catalogo(pro_user_id):
    """O outro ramo de `_charge_one` (`payment_type='credit_card'`), que grava em
    `credit_transactions` em vez de `launches`. Sem ele o par fica meio coberto."""
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz

    create_user_category(pro_user_id, "mcdonald's")
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.get_or_create_open_bill(pro_user_id, card_id, today_tz())  # o cron exige bill open
    _gasto_fixo_legado(
        pro_user_id, "McDonald's", payment_type="credit_card", card_id=card_id,
    )

    charge_due_recurring_expenses_once(today_tz())

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from credit_transactions where user_id=%s order by id desc limit 1",
                (pro_user_id,),
            )
            row = cur.fetchone()
    assert row and row["categoria"] == "mcdonald's"


def test_credito_de_receita_usa_grafia_do_catalogo(pro_user_id):
    """NEGATIVO (receita): o lado que a issue #147 nem citou. `_credit_one` tinha
    a mesma linha crua. Controle negativo: `category = inc["category"] or
    "salário"` de volta e o assert lê "Salário Extra"."""
    from core.services.recurring_charger import credit_due_recurring_incomes_once
    from db.recurring_income import create_recurring_income
    from utils_date import today_tz

    hoje = today_tz()
    create_user_category(pro_user_id, "salário extra")
    inc = create_recurring_income(
        pro_user_id, "Freela", 500.0, "outros", hoje.day, start_date=hoje,
    )
    _forca_categoria_crua("recurring_incomes", pro_user_id, inc["id"], "Salário Extra")

    credit_due_recurring_incomes_once(hoje)

    assert _ultimo_launch(pro_user_id)["categoria"] == "salário extra"


def test_cobranca_de_free_com_acento_nao_perde_o_acento(user_id):
    """POSITIVO, e o mais importante do grupo: usuário SEM plano custom, gasto
    fixo com acento e SEM entrada correspondente no catálogo. O cobrador NÃO pode
    mexer no texto.

    É este caso que mata a variante `create=True` no cobrador: com ela,
    `resolve_category_input` devolve `normalize_text(raw)` pra quem não tem plano
    custom e o mês seguinte gravaria "padaria do ze" enquanto o histórico está em
    "Padaria do Zé" — o conserto CRIARIA a fatia gêmea que veio matar. Trocar o
    `create=False` por `create=True` em `_canonical_category` derruba só este."""
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz

    _gasto_fixo_legado(user_id, "Padaria do Zé")

    charge_due_recurring_expenses_once(today_tz())

    assert _ultimo_launch(user_id)["categoria"] == "Padaria do Zé"
    assert _nomes_custom(user_id) == []  # o cobrador nunca grava no catálogo


def test_cobranca_nao_inventa_categoria_no_catalogo(pro_user_id):
    """POSITIVO: mesmo com plano PRO, o cobrador é read-only sobre o catálogo.
    Categoria sem correspondência passa intacta e nenhuma linha nova nasce —
    `create=True` aqui semearia o catálogo a partir de um job em lote."""
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz

    _gasto_fixo_legado(pro_user_id, "Cafeteria da Esquina")

    charge_due_recurring_expenses_once(today_tz())

    assert _ultimo_launch(pro_user_id)["categoria"] == "Cafeteria da Esquina"
    assert _nomes_custom(pro_user_id) == []


# ─── porta 9, as 4 escritas: create/update × despesa/receita ────────────────


def test_create_recurring_expense_resolve_categoria(pro_user_id):
    """A porta que a issue citou (`db/recurring.py:212`, só `.strip()`)."""
    from db.recurring import create_recurring_expense

    create_user_category(pro_user_id, "mcdonald's")
    rec = create_recurring_expense(
        pro_user_id, "Assinatura", 21.90, "McDonald's", 10, "account",
    )
    assert rec["category"] == "mcdonald's"


def test_update_recurring_expense_resolve_categoria(pro_user_id):
    """A que a issue NÃO previu: deixar o `update_*` de fora reabre o buraco —
    o usuário edita o gasto fixo pela tela e a grafia crua volta."""
    from db.recurring import create_recurring_expense, update_recurring_expense

    create_user_category(pro_user_id, "mcdonald's")
    rec = create_recurring_expense(
        pro_user_id, "Assinatura", 21.90, "outros", 10, "account",
    )
    editado = update_recurring_expense(pro_user_id, rec["id"], category="McDonald's")
    assert editado["category"] == "mcdonald's"


def test_create_recurring_income_resolve_categoria(pro_user_id):
    from db.recurring_income import create_recurring_income

    create_user_category(pro_user_id, "salário extra")
    inc = create_recurring_income(pro_user_id, "Freela", 500.0, "Salário Extra", 10)
    assert inc["category"] == "salário extra"


def test_update_recurring_income_resolve_categoria(pro_user_id):
    from db.recurring_income import create_recurring_income, update_recurring_income

    create_user_category(pro_user_id, "salário extra")
    inc = create_recurring_income(pro_user_id, "Freela", 500.0, "outros", 10)
    editado = update_recurring_income(pro_user_id, inc["id"], category="Salário Extra")
    assert editado["category"] == "salário extra"


def test_escrita_de_recorrente_nao_cria_categoria_orfa(pro_user_id):
    """A invariante do arquivo aplicada à porta 9: a linha em `user_categories`
    nasce DEPOIS do write.

    O alvo tem de EXISTIR e a falha tem de vir DEPOIS do bloco de categoria,
    senão o teste não discrimina a ordem. `RECORRENTE_NAO_ENCONTRADO` sobe no
    topo de `update_recurring_expense`, antes do resolver — com ele o
    `ensure_user_category` é inalcançável em qualquer ordem e o teste passa até
    na `main`. `DIA_INVALIDO` é validado DEPOIS do bloco `if category is not
    None:` e antes de qualquer escrita: é o único ponto onde a ordem é
    observável por caminho alcançável.

    Controle negativo medido: mover o `ensure_user_category` pra dentro do bloco
    de categoria (antes do UPDATE) faz o assert do catálogo ler
    `['cafeteria da esquina']`."""
    from db.recurring import (
        create_recurring_expense,
        get_recurring_expense,
        update_recurring_expense,
    )

    rec = create_recurring_expense(
        pro_user_id, "Assinatura", 21.90, "outros", 10, "account",
    )
    with pytest.raises(ValueError, match="DIA_INVALIDO"):
        update_recurring_expense(
            pro_user_id, rec["id"], category="Cafeteria da Esquina", due_day=99,
        )
    assert _nomes_custom(pro_user_id) == []
    assert get_recurring_expense(pro_user_id, rec["id"])["category"] == "outros"


def test_escrita_de_recorrente_nao_cria_orfa_com_alvo_inexistente(pro_user_id):
    """POSITIVO do par: alvo que não existe continua sendo 404, catálogo intacto.
    Este caso NÃO mede a ordem (o raise vem antes do resolver) — está aqui pra
    provar que o caminho de erro anterior não regrediu."""
    from db.recurring import update_recurring_expense

    with pytest.raises(ValueError, match="RECORRENTE_NAO_ENCONTRADO"):
        update_recurring_expense(pro_user_id, 999_999_999, category="Cafeteria da Esquina")
    assert _nomes_custom(pro_user_id) == []


@pytest.mark.parametrize("porta", ["expense", "income"])
def test_escrita_de_recorrente_semeia_o_catalogo(pro_user_id, porta):
    """O outro lado da invariante: nome NOVO e legítimo ganha a linha em
    `user_categories` depois do write, como nas outras portas de correção — é o
    catálogo que de-duplica as grafias seguintes (inclusive a do cobrador, que é
    read-only). Controle negativo: tirar o `ensure_user_category` da porta e o
    catálogo sai vazio."""
    if porta == "expense":
        from db.recurring import create_recurring_expense
        create_recurring_expense(
            pro_user_id, "Assinatura", 21.90, "Cafeteria da Esquina", 10, "account",
        )
    else:
        from db.recurring_income import create_recurring_income
        create_recurring_income(pro_user_id, "Freela", 500.0, "Cafeteria da Esquina", 10)

    # minúscula COM acento/pontuação é a forma de exibição da convenção do topo
    assert _nomes_custom(pro_user_id) == ["cafeteria da esquina"]


# ─── plano INATIVO: a porta não pode fazer o que o cobrador foi proibido de fazer ─


@pytest.mark.parametrize(
    "porta", ["create_expense", "update_expense", "create_income", "update_income"]
)
def test_escrita_de_recorrente_sem_custom_nao_abre_fatia_gemea(user_id, porta):
    """O gêmeo das 4 portas para quem NÃO pode ter categoria custom.

    Hoje esse estado é o PLANO INATIVO — assinatura vencida, cancelada ou com
    pagamento falhando —, não "plano grátis" (esse produto não existe mais):
    `get_plan_tier` colapsa em "free" quando `_paid_plan_active` é falso
    (`core/services/plan_service.py:117-122`), e é esse veredito que
    `_custom_categories_allowed` lê. A fixture `user_id` (sem linha em
    `auth_accounts`) produz o MESMO veredito, por isso serve de modelo.

    Alcançável sem plano ativo: `core/handlers/pending.py:111` (a oferta "virar
    gasto fixo?" do WhatsApp) chama `create_recurring_expense` sem gate nenhum,
    com o `categoria_final` do próprio lançamento — literalmente a grafia que já
    está no histórico. E `list_due_recurring_expenses` não filtra por plano, então
    quem está com o cartão recusado segue sendo cobrado todo mês.

    Controle negativo medido: `create=True` fixo de volta em
    `resolve_category_for_write` e os 4 casos leem
    `{'Padaria do Zé': 1, 'padaria do ze': 1}` — duas fatias."""
    from core.services.recurring_charger import (
        charge_due_recurring_expenses_once,
        credit_due_recurring_incomes_once,
    )
    from db.recurring import create_recurring_expense, update_recurring_expense
    from db.recurring_income import create_recurring_income, update_recurring_income
    from utils_date import today_tz

    hoje = today_tz()
    grafia = "Padaria do Zé"
    _novo_launch(user_id, categoria=grafia)  # o histórico que já existe

    if porta == "create_expense":
        create_recurring_expense(
            user_id, "Assinatura", 21.90, grafia, hoje.day, "account", start_date=hoje,
        )
    elif porta == "update_expense":
        rec = create_recurring_expense(
            user_id, "Assinatura", 21.90, "outros", hoje.day, "account", start_date=hoje,
        )
        update_recurring_expense(user_id, rec["id"], category=grafia)
    elif porta == "create_income":
        create_recurring_income(
            user_id, "Freela", 500.0, grafia, hoje.day, start_date=hoje,
        )
    else:
        inc = create_recurring_income(
            user_id, "Freela", 500.0, "outros", hoje.day, start_date=hoje,
        )
        update_recurring_income(user_id, inc["id"], category=grafia)

    charge_due_recurring_expenses_once(hoje)
    credit_due_recurring_incomes_once(hoje)

    assert _fatias_do_donut(user_id) == {grafia: 2}
    assert _nomes_custom(user_id) == []  # sem plano ativo, catálogo intocado


def test_escrita_de_recorrente_com_custom_ainda_semeia_a_grafia(pro_user_id):
    """POSITIVO do par: quem PODE ter custom continua no `create=True` — grafia
    preservada E linha nova no catálogo. Sem este caso, um
    `resolve_category_for_write` que devolvesse sempre o texto cru (create=False
    fixo) passaria no teste acima e desligaria a de-duplicação que o #147 monta."""
    from db.recurring import create_recurring_expense

    rec = create_recurring_expense(
        pro_user_id, "Assinatura", 21.90, "Padaria do Zé", 10, "account",
    )
    assert rec["category"] == "padaria do zé"
    assert _nomes_custom(pro_user_id) == ["padaria do zé"]


# ─── o rename tem de alcançar o recorrente, senão a fatia gêmea volta ───────


def _zera_idempotencia(user_id: int) -> None:
    """Deixa o recorrente cobrável de novo, pra simular o mês seguinte."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update recurring_expenses set last_charged_ym=null where user_id=%s",
                (user_id,),
            )
            cur.execute("delete from recurring_charges where user_id=%s", (user_id,))
        conn.commit()


def test_rename_cascateia_para_o_recorrente(pro_user_id):
    """NEGATIVO (despesa): o #147 fez o recorrente nascer com a grafia do catálogo,
    e o rename precisava alcançá-lo. O cobrador é `create=False` + `or raw`: sem o
    cascade ele não acha mais o nome velho no `display_map` e grava a grafia ANTIGA
    no mês seguinte, enquanto o histórico já foi renomeado. Duas fatias no donut.

    O QUE ESTE TESTE NÃO FECHA (o recorrente aqui nasce PELA porta, então a grafia
    dele é idêntica à do catálogo): o cascade casa por `lower(category)=lower(old_name)`
    e `lower()` NÃO colapsa acento, enquanto a resolução do cobrador usa
    `normalize_text()`, que colapsa. Recorrente LEGADO com grafia crua — os que a
    issue #147 mira — pode divergir do catálogo só pelo acento e escapar do cascade.
    Medido: catálogo "cafe", recorrente legado "Café", rename para "cafeteria" →
    o recorrente fica em `'Café'` e o donut lê `{'Café': 1, 'cafeteria': 1}`.
    As duas igualdades sobre o mesmo dado ficam registradas como issue própria.

    Controle negativo medido: removendo o `update recurring_expenses` do
    cascade de `update_user_category`, este assert lê
    `{'café': 1, 'cafeteria': 1}`."""
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from db.recurring import create_recurring_expense
    from utils_date import today_tz

    hoje = today_tz()
    cat = create_user_category(pro_user_id, "cafeteria")
    create_recurring_expense(
        pro_user_id, "Assinatura", 21.90, "Cafeteria", hoje.day, "account",
        start_date=hoje,
    )
    charge_due_recurring_expenses_once(hoje)          # mês 1
    update_user_category(pro_user_id, cat["id"], new_name="café")
    _zera_idempotencia(pro_user_id)
    charge_due_recurring_expenses_once(hoje)          # mês 2

    fatias = _fatias_do_donut(pro_user_id)
    assert fatias == {"café": 2}, fatias


def test_rename_cascateia_para_a_receita_recorrente(pro_user_id):
    """NEGATIVO (receita): o mesmo buraco do outro lado — `recurring_incomes`
    também guarda o texto. Controle negativo: sem o `update recurring_incomes`
    no cascade, o crédito sai como "freela"."""
    from core.services.recurring_charger import credit_due_recurring_incomes_once
    from db.recurring_income import create_recurring_income
    from utils_date import today_tz

    hoje = today_tz()
    cat = create_user_category(pro_user_id, "freela")
    create_recurring_income(
        pro_user_id, "Freela", 500.0, "Freela", hoje.day, start_date=hoje,
    )
    update_user_category(pro_user_id, cat["id"], new_name="renda extra")
    credit_due_recurring_incomes_once(hoje)

    assert _ultimo_launch(pro_user_id)["categoria"] == "renda extra"


def test_rename_nao_toca_recorrente_de_outra_categoria(pro_user_id):
    """POSITIVO do par: o cascade é `where lower(category)=lower(old_name)` —
    recorrente de OUTRA categoria fica intocado. Sem este caso, um cascade que
    reescrevesse a coluna inteira passaria nos dois testes acima."""
    from db.recurring import create_recurring_expense, get_recurring_expense

    cat = create_user_category(pro_user_id, "cafeteria")
    create_user_category(pro_user_id, "padaria")
    outro = create_recurring_expense(
        pro_user_id, "Pão", 12.0, "Padaria", 10, "account",
    )
    update_user_category(pro_user_id, cat["id"], new_name="café")

    assert get_recurring_expense(pro_user_id, outro["id"])["category"] == "padaria"


# ─── teto do nome: a porta nova não pode furá-lo pra dentro do catálogo ─────


def test_escrita_de_recorrente_nao_semeia_nome_acima_do_teto(pro_user_id):
    """NEGATIVO: `resolve_category_input` recusa acima de CATEGORY_NAME_MAX_LEN
    (é o que faz o PATCH /launches devolver 400), mas a porta nova faz `or cat`
    e entrega o texto cru ao `ensure_user_category`. Sem o teto dentro do
    `create_user_category`, a linha longa entra no catálogo.

    Controle negativo medido: tirando o `len(norm) > CATEGORY_NAME_MAX_LEN` do
    `create_user_category`, este assert lê um nome de 208 chars."""
    from db.recurring import create_recurring_expense

    longo = "Padaria " + "z" * 200
    assert len(longo) > CATEGORY_NAME_MAX_LEN
    rec = create_recurring_expense(
        pro_user_id, "Assinatura", 10.0, longo, 10, "account",
    )
    # o recorrente guarda o texto (o `or cat` não perde o que o dono digitou)…
    assert rec["category"] == longo
    # …mas o catálogo não ganha linha acima do teto
    assert _nomes_custom(pro_user_id) == []


def test_nome_longo_no_catalogo_nao_fura_o_teto_das_outras_portas(pro_user_id):
    """A escalada do caso acima: com a linha longa no catálogo, o `display_map` a
    resolveria no passo 2 de `resolve_category_input` — ANTES do guard de tamanho do
    passo 3 — e o PATCH /launches, que devolvia 400, passaria a aceitar.

    FECHA 2 DAS 3 ENTRADAS do catálogo: `resolve_category_input` (passo 3) e
    `create_user_category`. A terceira, `ensure_user_categories_seeded`
    (`db/categories.py`), continua SEM teto — insere `lower(trim(categoria))` em SQL
    cru a partir do histórico do usuário, uma vez por conta. Medido: lançamento com
    categoria de 208 chars (teto 80) vira linha no catálogo, e
    `resolve_category_input(..., create=True)` passa a devolver os 208 em vez de
    `None`. Fica aberto de propósito (é importação de histórico já existente, não
    entrada nova) e registrado como issue própria."""
    from db.recurring import create_recurring_expense

    longo = "Padaria " + "z" * 200
    assert resolve_category_input(pro_user_id, longo, create=True) is None
    create_recurring_expense(pro_user_id, "Assinatura", 10.0, longo, 10, "account")
    assert resolve_category_input(pro_user_id, longo, create=True) is None


def test_categoria_no_teto_continua_entrando_no_catalogo(pro_user_id):
    """POSITIVO do par: o teto é `>`, não `>=`. Nome exatamente no limite
    continua sendo aceito — sem este caso, um teto de `len(norm) > 0` passaria
    nos dois negativos acima recusando tudo."""
    no_teto = "c" * CATEGORY_NAME_MAX_LEN
    create_user_category(pro_user_id, no_teto)
    assert _nomes_custom(pro_user_id) == [no_teto]


# ─── decisão registrada: falha do catálogo SOBE na escrita (strict=True) ────


def test_escrita_falha_quando_o_catalogo_cai(pro_user_id, monkeypatch):
    """PRENDE a decisão do comentário em `db/recurring.py`: `create=True` usa
    `user_category_display_map(strict=True)`, então falha transitória de leitura
    do catálogo aborta a criação do gasto fixo (500 na rota) em vez de degradar.

    Degradar daria mapa vazio → o nome digitado passaria por NOVO →
    `ensure_user_category` gravaria a gêmea, que é o defeito que a porta existe
    pra matar. Aqui nada foi escrito ainda, então a retentativa é segura."""
    import db.categories as C
    from db.recurring import create_recurring_expense

    real = C.get_conn
    est = {"quebrar": True}

    def conn_quebrada(*a, **k):
        if est["quebrar"]:
            est["quebrar"] = False
            raise RuntimeError("conexao caiu (transitorio)")
        return real(*a, **k)

    monkeypatch.setattr(C, "get_conn", conn_quebrada)
    with pytest.raises(RuntimeError):
        create_recurring_expense(
            pro_user_id, "Assinatura", 21.90, "Cafeteria da Esquina", 10, "account",
        )
    # nem recorrente, nem categoria: a falha é limpa
    assert _nomes_custom(pro_user_id) == []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from recurring_expenses where user_id=%s",
                (pro_user_id,),
            )
            assert int(cur.fetchone()["n"]) == 0


def test_cobranca_degrada_quando_o_catalogo_cai(pro_user_id, monkeypatch):
    """POSITIVO do par: o COBRADOR usa `strict=False` de propósito — o oposto da
    escrita. Falha do catálogo não pode abortar dinheiro; a cobrança acontece e
    a categoria fica com o texto do recorrente (`or raw`).

    Sem este caso, trocar o cobrador pra `strict=True` passaria no teste acima e
    quebraria a cobrança em produção."""
    import db.categories as C
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz

    create_user_category(pro_user_id, "mcdonald's")
    _gasto_fixo_legado(pro_user_id, "McDonald's")

    real = C.get_conn
    est = {"quebrar": True}

    def conn_quebrada(*a, **k):
        if est["quebrar"]:
            est["quebrar"] = False
            raise RuntimeError("conexao caiu (transitorio)")
        return real(*a, **k)

    monkeypatch.setattr(C, "get_conn", conn_quebrada)
    res = charge_due_recurring_expenses_once(today_tz())

    assert len(res) == 1, res
    assert _ultimo_launch(pro_user_id)["categoria"] == "McDonald's"
