"""
Smoke test ABRANGENTE do handler principal (core.handle_incoming.handle_incoming).

Exercita TODAS as grandes funcionalidades do PigBank end-to-end pelo mesmo
caminho que uma mensagem real do WhatsApp/Discord percorre: paywall → mídia →
classify → route → handlers → format. Objetivo: pegar qualquer regressão/crash
em qualquer área do produto num único arquivo.

Roda como user Pro (uid < 1bi pra is_pro enxergar dentro do handle_incoming) e
mocka a IA/mídia pra não bater em rede. A ideia é usar comandos determinísticos
de alta confiança, então a IA quase nunca deve ser chamada.
"""
from __future__ import annotations

import re
import pytest

from core.types import IncomingMessage
import core.handle_incoming as hi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def spy_ai(monkeypatch):
    """Espiona a IA — se chamada, devolve marker e registra o texto."""
    calls: list[str] = []
    import core.services.ai_chat as ai_chat_mod

    def fake_chat(user_id, text, *, monthly_limit, platform):
        calls.append(text)
        return f"[IA] {text}"

    monkeypatch.setattr(ai_chat_mod, "chat", fake_chat)
    return calls


@pytest.fixture
def pro_uid():
    """User Pro com uid pequeno (nunca normalizado por handle_incoming)."""
    import uuid as _uuid
    import db as _db
    from db.connection import get_conn

    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', 'pro')",
                (uid, f"pro-{uid}@test.local"),
            )
        conn.commit()
    return uid


def _msg(uid: int, text: str, platform: str = "whatsapp") -> IncomingMessage:
    return IncomingMessage(
        platform=platform, user_id=uid, text=text,
        message_id="1", attachments=[], external_id=str(uid), raw={},
    )


def _send(uid: int, text: str, platform: str = "whatsapp") -> str:
    out = hi.handle_incoming(_msg(uid, text, platform))
    assert out, f"handler retornou vazio para: {text!r}"
    return out[0].text


# ---------------------------------------------------------------------------
# O smoke test — um cenário por área, sequencial (estado acumula de propósito)
# ---------------------------------------------------------------------------

def test_full_product_smoke(pro_uid, spy_ai, capsys):
    uid = pro_uid
    results: list[tuple[str, str, str]] = []  # (area, input, output)

    def step(area: str, text: str, must_contain: list[str] | None = None,
             must_not_contain: list[str] | None = None, platform: str = "whatsapp"):
        resp = _send(uid, text, platform)
        results.append((area, text, resp))
        # nunca deve cair no erro interno genérico do handler
        assert "erro interno" not in resp.lower(), (
            f"[{area}] '{text}' → ERRO INTERNO:\n{resp}"
        )
        if must_contain:
            for frag in must_contain:
                assert frag.lower() in resp.lower(), (
                    f"[{area}] '{text}' → esperava conter {frag!r}, veio:\n{resp}"
                )
        if must_not_contain:
            for frag in must_not_contain:
                assert frag.lower() not in resp.lower(), (
                    f"[{area}] '{text}' → NÃO devia conter {frag!r}, veio:\n{resp}"
                )
        return resp

    # 1. Saudação
    step("greeting", "oi")

    # 2. Ajuda
    step("help", "ajuda")

    # 3. Saldo inicial
    step("balance", "saldo", must_contain=["conta corrente"])

    # 4. Lançamentos — despesa e receita
    step("launch.add", "gastei 50 no mercado", must_contain=["mercado"])
    step("launch.add", "recebi 3000 de salario")

    # 5. Listar lançamentos
    step("launch.list", "meus lançamentos", must_contain=["mercado"])

    # 6. Quanto gastei
    step("launch.spend", "quanto gastei esse mês")

    # 7. Saldo depois dos lançamentos
    step("balance", "qual meu saldo")

    # 8. Caixinhas (pockets) — usa as formas ANUNCIADAS no catálogo
    #    (cria/deposita/saca), que devem rodar deterministicamente (sem IA).
    step("pocket.create", "cria caixinha viagem", must_contain=["viagem"],
         must_not_contain=["[IA]", "qual o nome"])
    step("pocket.list", "minhas caixinhas", must_contain=["viagem"])
    step("pocket.deposit", "deposita 100 na caixinha viagem",
         must_contain=["depósito", "viagem"], must_not_contain=["[IA]"])
    step("pocket.withdraw", "saca 30 da caixinha viagem",
         must_contain=["viagem"], must_not_contain=["[IA]"])

    # 9. Investimentos
    step("invest.list", "meus investimentos")

    # 10. Categorias
    step("cat.list", "categorias")

    # 11. Relatórios
    step("report.daily", "relatório")

    # 12. Recorrentes
    step("recurring", "gasto fixo aluguel 1200 todo dia 10")

    # 13. Contas a pagar (bills)
    step("bills", "contas a pagar")

    # 14. Cartão de crédito
    step("credit", "meus cartões")

    # 15. Dashboard
    step("dashboard", "dashboard")

    # 16. CDI — forma interrogativa natural deve ser determinística (sem IA)
    step("cdi", "quanto está o cdi", must_not_contain=["[IA]"])

    # 17. Fluxo destrutivo: apagar lançamento por #seq
    #     Descobre um #seq real da listagem.
    listagem = _send(uid, "meus lançamentos")
    m = re.search(r"#(\d+)", listagem)
    if m:
        seq = m.group(1)
        step("launch.delete", f"apagar #{seq}", must_contain=["sim", "não"])
        step("confirm.yes", "sim")

    # Dump legível de tudo pro relatório
    print("\n\n===== SMOKE RESULTS =====")
    for area, text, resp in results:
        one_line = resp.replace("\n", " ⏎ ")[:220]
        print(f"[{area:14}] {text!r:45} -> {one_line}")
    print(f"\nIA chamada {len(spy_ai)}x: {spy_ai}")

    # A IA não deveria ser necessária pra nenhum comando determinístico acima.
    # Se for chamada, é sinal de que a classificação regrediu (não é crash, mas
    # anotar). Não falha o teste por isso — só reporta.


# ---------------------------------------------------------------------------
# Porta genérica da pergunta de valor (`clarification` de launches.add).
#
# "Paguei a luz" sem conta cadastrada cai aqui: `try_pay_from_text` desiste e
# `core/handlers/launches.py` grava a pendência `clarification` com a pergunta
# "🐷 Quanto foi no *luz*?". Antes deste PR essa porta não tinha validação
# nenhuma — "-10" registrava R$ 10,00, "0" matava a pendência e "saldo" virava
# "Quanto foi no *luz saldo*?".
#
# A conversa inteira, não a função: cada caso arma a pergunta pelo `_send`.
# ---------------------------------------------------------------------------

def _pergunta_de_valor(uid: int, texto: str = "paguei a luz") -> str:
    resp = _send(uid, texto)
    assert "Quanto foi" in resp, resp
    return resp


def _pendencia(uid: int):
    import db
    return db.get_pending_action(uid)


def test_clarification_comando_confiante_abandona_a_pergunta(pro_uid, spy_ai):
    """T1 — "saldo" é comando, não valor: larga a pergunta e responde o saldo."""
    uid = pro_uid
    _pergunta_de_valor(uid)

    resp = _send(uid, "saldo")

    assert "Quanto foi" not in resp, resp
    assert "Conta Corrente" in resp, resp
    assert _pendencia(uid) is None, _pendencia(uid)


def test_clarification_valor_negativo_recusado_pergunta_viva(pro_uid, spy_ai):
    """T2 — "-10" registrava R$ 10,00 (`parse_money("-10") == 10.0`)."""
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    resp = _send(uid, "-10")

    assert "maior que zero" in resp, resp
    assert "Quanto foi no *luz*?" in resp, resp
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "clarification", pend
    assert pend["payload"]["orig_text"] == "paguei a luz", pend
    assert not db.list_launches(uid, limit=5), "não pode ter registrado nada"


def test_clarification_zero_recusado_pergunta_viva(pro_uid, spy_ai):
    """T3 — "0" respondia "Não consegui identificar o valor" e MATAVA a pendência."""
    uid = pro_uid
    _pergunta_de_valor(uid)

    resp = _send(uid, "0")

    assert "maior que zero" in resp, resp
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "clarification", pend


@pytest.mark.parametrize("resposta,esperado", [
    ("132,50.", 132.5),    # antes: 13.250,00 (parse_money lê a vírgula como milhar)
    ("1.23,45", 123.45),   # ponto solto + vírgula: passa, é o que a pessoa quis dizer
])
def test_clarification_pontuacao_nao_infla_o_valor(pro_uid, spy_ai, resposta, esperado):
    """T4 (metade que PASSA) — o critério é o dano, não a boa digitação."""
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, "deveria ter registrado"
    assert abs(float(lancs[0]["valor"])) == esperado, lancs[0]


@pytest.mark.parametrize("resposta", [
    "132 50",     # antes: 13.250,00
    "1.23.456",   # antes: 123.456,00 quando o provável era 1.234,56
])
def test_clarification_valor_ambiguo_recusado_pergunta_viva(pro_uid, spy_ai, resposta):
    """T4 (metade que RECUSA) — ambíguo re-pergunta em vez de inflar."""
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    resp = _send(uid, resposta)

    assert "Não entendi o valor" in resp, resp
    assert "Quanto foi no *luz*?" in resp, resp
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "clarification", pend
    assert not db.list_launches(uid, limit=5), "não pode ter registrado nada"


@pytest.mark.parametrize("resposta,esperado", [
    ("132", 132.0),
    ("uns 132", 132.0),           # enchimento falado
    ("cinquenta", 50.0),          # por extenso: o Whisper escreve assim
    ("2000000000", 2_000_000_000.0),  # sem teto inventado
    # As formas COM UNIDADE — o buraco da rodada 1. Faltavam aqui, e por isso a
    # escotilha por confiança (uma blacklist sobre o classificador inteiro)
    # passou verde sequestrando todas elas. Com o `ABANDONA` fechado, nenhuma
    # delas pode abandonar nada: só os seis intents de comando abandonam.
    ("132 reais", 132.0),
    ("50 pila", 50.0),
    ("5 conto", 5.0),
    ("132,50 reais", 132.5),
    ("1 real", 1.0),
    ("132 reais.", 132.0),
])
def test_clarification_positivo_o_aperto_nao_recusa_tudo(pro_uid, spy_ai, resposta, esperado):
    """T5 — controle positivo do grupo: pega teto inventado e recusa-tudo.

    Assere DESCRIÇÃO e CATEGORIA, não só o valor: com a escotilha invertida,
    "50 pila" ainda registrava R$ 50,00 — mas com descrição "50 pila" e
    categoria "outros", porque a resposta virava um lançamento novo e a "luz"
    da pergunta era jogada fora. O valor sozinho não denunciava nada.
    """
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, f"{resposta!r} deveria ter registrado"
    assert abs(float(lancs[0]["valor"])) == esperado, lancs[0]
    texto = f"{lancs[0].get('alvo') or ''} {lancs[0].get('nota') or ''}".lower()
    assert "luz" in texto, f"{resposta!r} perdeu a descrição da pergunta: {lancs[0]}"
    assert lancs[0].get("categoria") == "moradia", lancs[0]


@pytest.mark.parametrize("resposta", ["mercado", "mercado 20"])
def test_clarification_descricao_completa_sem_somar_valor(pro_uid, spy_ai, resposta):
    """T6 — "gastei 50" + "mercado 20" registrava R$ 2.050,00.

    O valor já veio no `orig_text`; a resposta é a DESCRIÇÃO. O `_extract_valor`
    frouxo aceitava "mercado 20" como valor e o combinado virava "gastei
    mercado 20 50" → 2050.
    """
    uid = pro_uid
    import db
    resp = _send(uid, "gastei 50")
    assert "Quanto" in resp or "gastou" in resp.lower(), resp

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, "deveria ter registrado"
    assert abs(float(lancs[0]["valor"])) == 50.0, lancs[0]


@pytest.mark.parametrize("resposta", [
    "fatura", "investimento", "extrato",
    # `cartao` cai por OUTRO motivo, anterior a este PR e medido na `main` com o
    # working tree limpo: o combinado "gastei 50 cartao" é lido como COMPRA NO
    # CARTÃO e `core/handlers/credit.py:477` responde "não tem cartão padrão",
    # perdendo os R$ 50. Não é a escotilha (ela nem chega a rodar aqui) e o
    # conserto é do fluxo de crédito. `strict` de propósito: quando alguém
    # consertar, este xfail fica vermelho e pede a atualização.
    pytest.param("cartao", marks=pytest.mark.xfail(
        strict=True,
        reason="pré-existente: 'gastei 50 cartao' vira compra no cartão sem cartão padrão")),
])
def test_clarification_de_descricao_nao_perde_o_valor_ja_digitado(pro_uid, spy_ai, resposta):
    """T6b — pergunta de DESCRIÇÃO não tem escotilha: o valor não pode evaporar.

    "fatura", "investimento" e "extrato" classificam como comando confiante, e
    são descrições perfeitamente plausíveis de um gasto. A escotilha por
    confiança da rodada 1 largava a pergunta, os R$ 50 que o usuário já tinha
    digitado sumiam e a resposta falava de outro assunto.
    """
    uid = pro_uid
    import db
    resp = _send(uid, "gastei 50")
    assert "gastou" in resp.lower(), resp

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, f"{resposta!r} descartou o valor: nada foi registrado"
    assert abs(float(lancs[0]["valor"])) == 50.0, lancs[0]


def test_clarification_de_recorrente_nao_vira_despesa_avulsa(pro_uid, spy_ai):
    """Bloqueante 3 — a escotilha matava a `clarification` de `recurring.add`.

    `core/handlers/recurring.py:97` guarda `intent="recurring.add"` só para não
    perder o contexto: sem a pendência, o "1000 reais" seguinte é classificado
    do zero e vira despesa avulsa — está escrito lá no comentário. A escotilha
    por confiança da rodada 1 abandonava a pergunta e reintroduzia esse bug.

    A pergunta é armada pelo PRODUTOR real (`recurring.add` com entities sem
    valor), não por um payload escrito à mão — em produção quem chega em
    `recurring.add` é a IA.
    """
    uid = pro_uid
    import db
    from core.handlers import recurring as h_recurring

    pergunta = h_recurring.add(uid, "gasto fixo aluguel todo dia 10",
                               {"nome": "aluguel", "dia": 10})
    assert "Qual o valor desse recorrente" in pergunta, pergunta
    assert _pendencia(uid)["payload"]["intent"] == "recurring.add", _pendencia(uid)

    resp = _send(uid, "1000 reais")

    assert "Gasto fixo criado" in resp and "1.000,00" in resp, resp
    assert not db.list_launches(uid, limit=5), "virou despesa avulsa"


# ---------------------------------------------------------------------------
# Porta 3 (numeração em `core/intent_router.py`): a fila do `multi_launch_values`
# (`core/handlers/launches.py::resolve_multi_launch_value`).
#
# Usuário GRÁTIS de propósito: para o Pro a IA sequestra o turno antes
# (`multi_launch_values` está fora de `_RESUMABLE_PENDING_TYPES`), e a porta
# fica escondida. É esse mascaramento que fez a rodada 1 concluir, errado, que
# ela não reproduzia.
# ---------------------------------------------------------------------------

@pytest.fixture
def free_uid():
    """User do Grátis — sem linha em `auth_accounts`."""
    import uuid as _uuid
    import db as _db

    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    return uid


def _fila_de_dois(uid: int) -> str:
    resp = _send(uid, "paguei o aluguel e paguei a luz")
    assert "Faltou o valor de *aluguel*" in resp, resp
    return resp


@pytest.mark.parametrize("resposta,motivo", [
    ("-10", "gravava R$ 10,00 — parse_money('-10') == 10.0"),
    ("132 50", "gravava R$ 13.250,00"),
    ("1.23.456", "gravava R$ 123.456,00"),
    ("0,001", "gravava 0.001 e dizia 'R$ 0,00 lançado'"),
    ("0", "apagava a pendência e a luz sumia da fila"),
    ("1" * 400, "parse_money devolve inf → '⚠️ Ocorreu um erro interno'"),
])
def test_multi_launch_recusa_valor_perigoso_e_mantem_a_fila(free_uid, spy_ai, resposta, motivo):
    uid = free_uid
    import db
    _fila_de_dois(uid)

    resp = _send(uid, resposta)

    assert "erro interno" not in resp.lower(), f"{motivo}: {resp}"
    assert "Faltou o valor de *aluguel*" in resp, f"{motivo}: {resp}"
    assert not db.list_launches(uid, limit=5), f"{motivo}: registrou {db.list_launches(uid, limit=5)}"
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "multi_launch_values", pend
    fila = [i["desc"] for i in pend["payload"]["queue"]]
    assert fila == ["aluguel", "luz"], f"a fila foi mexida: {fila}"


def test_multi_launch_pontuacao_final_nao_infla(free_uid, spy_ai):
    """`132,50.` gravava R$ 13.250,00 (parse_money lê a vírgula como milhar)."""
    uid = free_uid
    import db
    _fila_de_dois(uid)

    _send(uid, "132,50.")

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == 132.5, lancs


@pytest.mark.parametrize("resposta,esperado", [
    ("132", 132.0),
    ("132 reais", 132.0),
    ("cinquenta", 50.0),            # por extenso, sem dígito
    ("paguei 132 no mercado", 132.0),  # frase: segue com o `_extract_valor`
])
def test_multi_launch_positivo_o_aperto_nao_recusa_tudo(free_uid, spy_ai, resposta, esperado):
    """Controle positivo da porta 3."""
    uid = free_uid
    import db
    _fila_de_dois(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, f"{resposta!r} deveria ter registrado"
    assert abs(float(lancs[0]["valor"])) == esperado, lancs[0]


def test_multi_launch_texto_sem_valor_ainda_abandona(free_uid, spy_ai):
    """Controle negativo do aperto: mudar de assunto continua largando a fila."""
    uid = free_uid
    _fila_de_dois(uid)

    resp = _send(uid, "saldo")

    assert "Faltou o valor" not in resp, resp
    assert _pendencia(uid) is None or _pendencia(uid)["action_type"] != "multi_launch_values"


# ---------------------------------------------------------------------------
# Rodada 3: a aceitação voltou a ser a da `main`.
#
# A rodada 2 pôs um validador de FORMA (mensagem inteira = número) como portão
# de "isto é o valor?". Medido pelo Tester na pergunta viva "🐷 Quanto foi no
# *luz*?": 13 formas que a `main` aceita falhavam aqui, em dois modos de dano —
# laço (tem dígito, o `elif` não pega, a pergunta se re-arma para sempre) e
# perda da pergunta (a escotilha dispara e "luz"/`moradia` evaporam).
#
# O portão passou a ser o `_extract_valor` — o mesmo do resto do bot — e o
# filtro de dano roda DEPOIS, sobre o texto.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta,esperado", [
    ("132 mil", 132000.0),
    ("2,5 mil", 2500.0),
    ("10 mil", 10000.0),            # saída literal do Whisper no áudio
    ("3 milhoes", 3000000.0),       # idem
    ("132 mil reais", 132000.0),
    ("132k", 132.0),                # o que a `main` extrai daqui é 132
    ("paguei 132", 132.0),
    ("gastei 132", 132.0),
    ("foi 132 mil", 132000.0),
    ("ficou em 132", 132.0),
    ("deu 132 no total", 132.0),
    ("foi uns 132 no total", 132.0),
    ("total 132", 132.0),
    ("132 no boleto", 132.0),
    # C2 — milhar separado por espaço. O filtro de dano recusava os três; a
    # `main` (e o `_extract_valor`) acerta todos.
    ("1 500", 1500.0),
    ("12 345", 12345.0),
    ("1 000 000", 1000000.0),
])
def test_clarification_aceita_tudo_que_a_main_aceita(pro_uid, spy_ai, resposta, esperado):
    """B1 — as 17 formas medidas, pela conversa inteira.

    Assere DESCRIÇÃO e CATEGORIA junto com o valor: os dois modos de dano da
    rodada 2 se distinguem por aí. No laço, nada é registrado; na perda da
    pergunta, o valor até entra, mas com descrição errada e categoria "outros".
    """
    from parsers import _extract_valor
    uid = pro_uid
    import db
    assert _extract_valor(resposta) == esperado, "premissa: a `main` aceita isto"
    _pergunta_de_valor(uid)

    resp = _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, f"{resposta!r} não registrou nada (laço): {resp}"
    assert abs(float(lancs[0]["valor"])) == esperado, lancs[0]
    texto = f"{lancs[0].get('alvo') or ''} {lancs[0].get('nota') or ''}".lower()
    assert "luz" in texto, f"{resposta!r} perdeu a descrição da pergunta: {lancs[0]}"
    assert lancs[0].get("categoria") == "moradia", lancs[0]
    assert _pendencia(uid) is None or \
        _pendencia(uid)["action_type"] != "clarification", _pendencia(uid)


# --- B2: os seis perigosos, com e sem palavra na frente, nas DUAS portas. ---

_PERIGOSOS_COM_PREFIXO = [
    (p + e, s)
    for e, s in [("-10", "maior que zero"), ("132 50", "Não entendi o valor"),
                 ("132 50 reais", "Não entendi o valor"),
                 ("1.23.456", "Não entendi o valor"),
                 ("0,001", "maior que zero"), ("1" * 400, "maior que zero")]
    for p in ("", "paguei ")
]


@pytest.mark.parametrize("resposta,recusa", _PERIGOSOS_COM_PREFIXO)
def test_clarification_dano_nao_some_com_palavra_na_frente(pro_uid, spy_ai, resposta, recusa):
    """B2, porta da `clarification`: uma palavra na frente não desarma o filtro."""
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    resp = _send(uid, resposta)

    assert "erro interno" not in resp.lower(), resp
    assert recusa in resp, resp
    assert "Quanto foi no *luz*?" in resp, resp
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "clarification", pend
    assert not db.list_launches(uid, limit=5), db.list_launches(uid, limit=5)


@pytest.mark.parametrize("resposta,recusa", _PERIGOSOS_COM_PREFIXO)
def test_multi_launch_dano_nao_some_com_palavra_na_frente(free_uid, spy_ai, resposta, recusa):
    """B2, porta do multi-lançamento: mesma tabela, mesma fonte de verdade."""
    uid = free_uid
    import db
    _fila_de_dois(uid)

    resp = _send(uid, resposta)

    assert "erro interno" not in resp.lower(), resp
    assert recusa in resp, resp
    assert "Faltou o valor de *aluguel*" in resp, resp
    assert not db.list_launches(uid, limit=5), db.list_launches(uid, limit=5)
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "multi_launch_values", pend
    assert [i["desc"] for i in pend["payload"]["queue"]] == ["aluguel", "luz"], pend


def test_clarification_de_descricao_com_numero_na_resposta(pro_uid, spy_ai):
    """Mata o `_ja_tem_o_valor`: sem ele, "mercado 20" vira R$ 20,005.

    O predicado é UMA linha e decide dois caminhos opostos. Apagá-lo faz a
    pergunta de DESCRIÇÃO parsear a resposta como valor: o combinado vira
    "gastei R$ 20,00 50" e o `parse_money` cola os dois em 20,005.
    """
    uid = pro_uid
    import db
    _send(uid, "gastei 50")

    _send(uid, "mercado 20")

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == 50.0, lancs
    texto = f"{lancs[0].get('alvo') or ''} {lancs[0].get('nota') or ''}".lower()
    assert "mercado" in texto, lancs[0]


# ---------------------------------------------------------------------------
# Rodada 4 — o passo 1 nas portas 2 (clarification) e 3 (multi_launch).
# (A numeração das quatro portas é a de `core/intent_router.py`.)
#
# O que separa "gastei 132" (paga) de "apagar 42" (abandona) não é a forma —
# as duas têm um número — é o intent. A tabela de duas colunas da porta de
# conta variável está em `tests/test_bill_amount_pending.py`; aqui ficam as
# duas portas cujo cenário mora neste arquivo.
# ---------------------------------------------------------------------------

# Comandos com valor dentro: sem o passo 1 eles REGISTRAM dinheiro.
COMANDOS_COM_NUMERO = ["quanto gastei em 132", "apagar 42"]
COMANDOS_SEM_NUMERO = ["saldo", "extrato", "resumo do mes"]
# O BLOQUEANTE: `credit.handle` 0.95, resposta legítima. Com a blacklist da
# rodada anterior, isto apagava a fila INTEIRA do multi-lançamento.
RESPOSTAS_QUE_PARECEM_COMANDO = [("132 no cartao", 132.0), ("investi 80", 80.0)]


@pytest.mark.parametrize("comando", COMANDOS_COM_NUMERO + COMANDOS_SEM_NUMERO)
def test_clarification_abandona_comando_mesmo_com_numero(pro_uid, spy_ai, comando):
    """Porta 2 — "apagar 42" lançava R$ 42,00 na *luz*."""
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    resp = _send(uid, comando)

    assert "Quanto foi no *luz*?" not in resp, resp
    assert not db.list_launches(uid, limit=5), \
        f"{comando!r} registrou dinheiro: {db.list_launches(uid, limit=5)}"
    pend = _pendencia(uid)
    assert pend is None or pend["action_type"] != "clarification", pend


@pytest.mark.parametrize("comando", COMANDOS_COM_NUMERO + COMANDOS_SEM_NUMERO)
def test_multi_launch_abandona_comando_mesmo_com_numero(free_uid, spy_ai, comando):
    """Porta 3 — mesma tabela, mesma fonte de verdade."""
    uid = free_uid
    import db
    _fila_de_dois(uid)

    resp = _send(uid, comando)

    assert "Faltou o valor" not in resp, resp
    assert not db.list_launches(uid, limit=5), \
        f"{comando!r} registrou dinheiro: {db.list_launches(uid, limit=5)}"
    pend = _pendencia(uid)
    assert pend is None or pend["action_type"] != "multi_launch_values", pend


@pytest.mark.parametrize("resposta,esperado", [
    ("132 reais", 132.0), ("50 pila", 50.0), ("10 mil", 10000.0),
])
def test_multi_launch_continua_registrando_o_valor(free_uid, spy_ai, resposta, esperado):
    """Coluna 1 da porta 3: o passo 1 não pode sequestrar a resposta certa.

    As três classificam `launches.add` 0.95, que NÃO está no `ABANDONA` — o
    conjunto é mundo fechado. Se algum intent de valor entrar lá, a fila é
    largada e o dinheiro some.
    """
    uid = free_uid
    import db
    _fila_de_dois(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == esperado, (resposta, lancs)


# ---------------------------------------------------------------------------
# C3 — número solto respondido a uma pergunta de DESCRIÇÃO.
#
# A pergunta "em que você gastou?" já tem o valor; a resposta é a descrição.
# O `parse_money` cola dois grupos de dígitos separados por espaço, então o
# combinado "gastei 50 0" virava R$ 500,00 — dinheiro inventado, e nenhum teste
# pegava. A `main` acertava por acidente (parseava a resposta como valor).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta", ["0", "20 no mercado", "mercado 20", "1"])
def test_clarification_de_descricao_nao_infla_o_valor_ja_digitado(pro_uid, spy_ai, resposta):
    """Os R$ 50 já digitados não podem crescer por causa da descrição."""
    uid = pro_uid
    import db
    assert "gastou" in _send(uid, "gastei 50").lower()

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs, f"{resposta!r} não registrou nada"
    assert abs(float(lancs[0]["valor"])) == 50.0, (resposta, lancs[0])


def test_controle_negativo_sem_o_separador_a_descricao_infla_o_valor(pro_uid, spy_ai, monkeypatch):
    """Desligar o " - " tem que ressuscitar o R$ 500,00.

    Injetado no caso verde
    `test_clarification_de_descricao_nao_infla_o_valor_ja_digitado[0]`. O
    interruptor é o `_execute`: aqui ele recebe o combinado que o separador
    deveria ter protegido, e o teste reconstrói o combinado antigo.
    """
    import core.intent_router as IR

    real = IR._execute

    def sem_separador(intent, user_id, text, *a, **k):
        return real(intent, user_id, text.replace(" - ", " "), *a, **k)

    monkeypatch.setattr(IR, "_execute", sem_separador)

    uid = pro_uid
    import db
    _send(uid, "gastei 50")
    _send(uid, "0")

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == 500.0, \
        f"sem o separador o combinado 'gastei 50 0' TINHA que virar R$ 500,00: {lancs}"


# ---------------------------------------------------------------------------
# O BLOQUEANTE da rodada anterior, nas portas 2 e 3: resposta de valor que
# classifica como comando. `classify("132 no cartao")` é `credit.handle` 0.95 e
# `classify("investi 80")` é `investments.deposit` 0.95 — nenhum dos dois está
# no `ABANDONA`, que é mundo fechado.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta,esperado", RESPOSTAS_QUE_PARECEM_COMANDO)
def test_multi_launch_nao_perde_a_fila_com_resposta_que_parece_comando(
        free_uid, spy_ai, resposta, esperado):
    """Com a blacklist, isto apagava a fila INTEIRA e não registrava nada."""
    uid = free_uid
    import db
    _fila_de_dois(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == esperado, (resposta, lancs)
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "multi_launch_values", \
        f"{resposta!r} apagou a fila: {pend}"
    assert [i["desc"] for i in pend["payload"]["queue"]] == ["luz"], pend


def test_clarification_nao_perde_a_pergunta_com_resposta_que_parece_comando(
        pro_uid, spy_ai):
    """Porta 2, com `investi 80` (`investments.deposit` 0.95).

    `132 no cartao` fica FORA desta metade por medição, não por esquecimento:
    nas duas árvores (`main` cf54ffb e este branch) o combinado
    "gastei 132 no cartao a luz" é lido como compra no cartão e responde
    "Não achei o cartão 'a luz'", sem registrar nada. É o mesmo defeito
    pré-existente do `xfail` de `cartao` em
    `test_clarification_de_descricao_nao_perde_o_valor_ja_digitado`, e o
    conserto é do fluxo de crédito. O que este PR precisa garantir aqui é que
    a pergunta não seja LARGADA pelo passo 1 — está no teste da porta 3, onde a
    fila sobrevive.
    """
    uid = pro_uid
    import db
    _pergunta_de_valor(uid)

    _send(uid, "investi 80")

    lancs = db.list_launches(uid, limit=5)
    assert lancs, "'investi 80' perdeu a pergunta e não registrou nada"
    assert abs(float(lancs[0]["valor"])) == 80.0, lancs[0]


def test_controle_negativo_credit_handle_no_conjunto_apaga_a_fila(free_uid, spy_ai, monkeypatch):
    """Pôr `credit.handle` no `ABANDONA` tem que reproduzir o bloqueante.

    Injetado no caso verde
    `test_multi_launch_nao_perde_a_fila_com_resposta_que_parece_comando`.
    """
    import core.intent_router as IR
    import db

    monkeypatch.setattr(IR, "ABANDONA", IR.ABANDONA | {"credit.handle"})

    uid = free_uid
    _fila_de_dois(uid)

    _send(uid, "132 no cartao")

    assert not db.list_launches(uid, limit=5), "com credit.handle no conjunto nada registra"
    pend = _pendencia(uid)
    assert pend is None or pend["action_type"] != "multi_launch_values", \
        f"a fila TINHA que ter sido apagada: {pend}"


def test_passo_1_da_porta_3_nao_apaga_pergunta_de_outra_tarefa(free_uid, spy_ai, monkeypatch):
    """O abandono da porta 3 é CAS, não `clear_pending_action`.

    `pending_actions` é UMA linha por usuário: entre o `route()` ler a fila e o
    handler abandoná-la, outra tarefa pode ter posto ali uma pergunta nova —
    que já apareceu na tela. `clear_pending_action` a apaga por cima e deixa o
    usuário respondendo a uma pergunta que não existe mais. A corrida é
    injetada no próprio predicado, que roda no call site logo antes.
    """
    import core.intent_router as IR
    import db

    uid = free_uid
    _fila_de_dois(uid)

    nova = {"bill_id": 99, "name": "Internet"}
    real = IR.abandona_pergunta_de_valor

    def com_corrida(texto):
        decidiu = real(texto)
        if decidiu:
            db.set_pending_action(uid, "bill_pay_amount", nova)
        return decidiu

    monkeypatch.setattr(IR, "abandona_pergunta_de_valor", com_corrida)

    _send(uid, "apagar 42")

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount", \
        f"o passo 1 apagou a pergunta mais nova: {atual}"
    assert atual.get("payload") == nova, atual


# ---------------------------------------------------------------------------
# BLOQUEANTE 1 da rodada final, na porta 3: o `limpa_pontuacao_final` ia só
# para o `_extract_valor` e o `valor_perigoso` recebia o texto CRU. Sem
# vírgula, o `agrupamento_de_milhar_ok("132.")` via `["132", ""]` — grupo vazio,
# `len 0` ∉ (1,2,3) — e recusava o que a `main` registrava. O teste de ponto
# final que já existia aqui só cobria "132,50.", COM vírgula, que é outro ramo.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta,esperado", [
    ("132.", 132.0), ("1.500.", 1500.0),
    ("foi 132.", 132.0), ("paguei 132.  ", 132.0),
])
def test_multi_launch_ponto_final_sem_virgula_registra(free_uid, spy_ai, resposta, esperado):
    uid = free_uid
    import db
    _fila_de_dois(uid)

    _send(uid, resposta)

    lancs = db.list_launches(uid, limit=5)
    assert lancs and abs(float(lancs[0]["valor"])) == esperado, (resposta, lancs)


@pytest.mark.parametrize("resposta", ["132.", "1.500.", "foi 132.", "paguei 132.  "])
def test_controle_negativo_porta_3_sem_limpar_a_pontuacao_recusa(
        free_uid, spy_ai, monkeypatch, resposta):
    """Controle: desliga a limpeza e os quatro casos verdes ficam vermelhos.

    O `import` da porta 3 é local, então o alvo é o `utils_text`.
    """
    import db
    import utils_text

    uid = free_uid
    _fila_de_dois(uid)
    monkeypatch.setattr(utils_text, "limpa_pontuacao_final", lambda s: s or "")

    resp = _send(uid, resposta)

    assert "Não entendi o valor" in resp, (resposta, resp)
    assert not db.list_launches(uid, limit=5), db.list_launches(uid, limit=5)
    pend = _pendencia(uid)
    assert pend and pend["action_type"] == "multi_launch_values", pend
