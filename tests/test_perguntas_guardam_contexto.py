"""As perguntas de caixinha/investimento/saque guardam o contexto (#136, fatia A).

Antes, um handler que devolvia "Qual o valor?" como string crua não guardava nada:
a resposta seguinte era classificada do zero e um número solto virava
`launches.add` com confiança 0,95. Medido na `main` c917f1c, conta nova, SEM IA
nenhuma no caminho — é o bot determinístico sequestrando a resposta da própria
pergunta:

    criar caixinha viagem       -> caixinha criada
    guardei na caixinha viagem  -> "Qual caixinha?"
    200 reais                   -> "Em que você gastou R$ 200,00?"
    viagem                      -> despesa R$ 200 'lazer', SALDO -200,00,
                                   caixinha intacta em 0

No saque o sinal ainda invertia: quem pedia para TIRAR R$ 100 terminava com o
saldo R$ 100 MENOR.

CONTROLE NEGATIVO — em `core/handlers/pockets.py`, troque as chamadas de
`h_pending.pergunta_guardando_contexto(...)` de volta pelo `return "<a
pergunta>"` cru: `test_deposito_nao_vira_despesa_fantasma` e
`test_saque_nao_vira_despesa_fantasma` ficam VERMELHOS (a despesa fantasma
volta e o saldo vai a -200/-100). Injetado nos dois, que estavam verdes.

CONTROLE POSITIVO — `test_deposito_legitimo_ainda_funciona`: o conserto
RESTRINGE (passa a exigir pendência), então precisa provar que o caminho bom
continua fechando. Sem ele, um código que recusasse TUDO passaria nos negativos.

RODA A CONVERSA, não a função (CLAUDE.md §3): tudo entra pelo `handle_incoming`
com estado real no banco, porque a classe de bug que esta issue trata só aparece
quando a mensagem seguinte é classificada do zero.

CLASSE CEGA: o LLM está fora do caminho aqui (sem `OPENAI_API_KEY` nos testes),
então isto prova o trajeto determinístico. O ramo do `_resolve_clarification`
que este PR adiciona roda ANTES da reclassificação por IA justamente para que o
comportamento do usuário Pro seja o mesmo — mas isso não é exercitado aqui.
Quem exercita é `scripts/whatsapp_qa_vault_harness.py`, que chama a IA de
verdade, tem custo e roda sob demanda.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

import db
from core.handle_incoming import handle_incoming
from core.types import IncomingMessage

_RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _conversa(uid: int, *mensagens: str) -> list[str]:
    """Manda as mensagens em sequência e devolve as respostas concatenadas."""
    saidas = []
    for m in mensagens:
        outs = handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=m))
        saidas.append(" ".join((o.text or "") for o in (outs or [])))
    return saidas


@pytest.fixture
def uid() -> int:
    u = int(uuid.uuid4().int % 1_000_000_000) + 1   # < 2 bi: não sofre remap
    db.ensure_user(u)
    return u


def _despesas(user_id: int) -> list[dict]:
    return [l for l in (db.list_launches(user_id, limit=50) or [])
            if (l.get("tipo") or "") == "despesa"]


# ── O bug da issue, nas duas portas de caixinha ──────────────────────────────

def test_deposito_nao_vira_despesa_fantasma(uid):
    # A ORDEM É A DA ISSUE e não é decorativa: é o valor ANTES da descrição que
    # reproduz. Com "viagem" antes de "200 reais" o teste passa na `main` também
    # — foi assim que ele nasceu, tautológico, e o controle negativo o pegou.
    _conversa(uid, "criar caixinha viagem", "guardei na caixinha viagem",
              "200 reais", "viagem")

    assert _despesas(uid) == [], "a resposta à pergunta virou despesa avulsa"
    assert round(float(db.get_balance(uid)), 2) == 0.00


def test_saque_nao_vira_despesa_fantasma(uid):
    """No saque o estrago era pior: o usuário pede para TIRAR e o saldo CAI."""
    _conversa(uid, "criar caixinha viagem", "tirar da caixinha viagem",
              "100 reais", "viagem")

    assert _despesas(uid) == []
    saldo = round(float(db.get_balance(uid)), 2)
    assert saldo == 0.00, f"saldo mexeu sozinho: {saldo}"


# ── Filtro de dano: a recusa mantém a pergunta viva ─────────────────────────

@pytest.mark.parametrize("resposta,trecho", [
    ("-10",     "maior que zero"),   # sinal engolido: gravava R$ 10,00
    ("0",       "maior que zero"),   # matava a pendência e caía no fallback
    ("132 50",  "Não entendi"),      # gravava R$ 13.250,00
])
def test_valor_perigoso_recusa_e_repergunta(uid, resposta, trecho):
    respostas = _conversa(uid, "criar caixinha viagem", "guardei na caixinha viagem",
                          "viagem", resposta)

    assert trecho in respostas[-1]
    assert "Qual o valor" in respostas[-1], "a pergunta morreu na recusa"
    assert _despesas(uid) == []
    # A pendência continua de pé — é isso que deixa o usuário só repetir o número.
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "clarification"
    assert pend["payload"]["falta"] == "amount"


def test_escotilha_outro_comando_abandona(uid):
    """"saldo" no meio da pergunta é comando, não resposta."""
    respostas = _conversa(uid, "criar caixinha viagem",
                          "guardei na caixinha viagem", "saldo")

    assert "Conta Corrente" in respostas[-1]
    assert _despesas(uid) == []


# ── CONTROLE POSITIVO: o caminho legítimo continua fechando ─────────────────

def test_deposito_legitimo_ainda_funciona(uid):
    """O conserto restringe; sem este caso, recusar tudo passaria nos negativos."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="teste",
        categoria="salário", is_internal_movement=False,
    )

    respostas = _conversa(uid, "criar caixinha viagem", "guardei na caixinha viagem",
                          "viagem", "200 reais")

    assert "Depósito na caixinha" in respostas[-1], respostas[-1]
    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 200.00
    assert round(float(db.get_balance(uid)), 2) == 300.00
    assert _despesas(uid) == [], "depósito em caixinha não é despesa"


# ── Resposta em linguagem natural ao nome (Codex P2 no #184) ────────────────

@pytest.mark.parametrize("resposta", [
    "viagem",                            # nome pelado
    "caixinha viagem",                   # repete o substantivo da pergunta
    "da caixinha viagem",                # com preposição
    "coloquei 200 na caixinha viagem",   # o comando COMPLETO que a pergunta sugere
])
def test_resposta_natural_ao_nome_da_caixinha(uid, resposta):
    """A pergunta sugere "Tente: *coloquei 200 na caixinha viagem*" — recusar esse
    texto seria o bot rejeitar o que ele mesmo recomendou.

    A primeira versão deste PR tomava a resposta VERBATIM como nome e respondia
    "Caixinha *coloquei 200 na caixinha viagem* não encontrada" — regressão que o
    Codex apontou e que a medição confirmou. Na `main` o comando completo era
    reclassificado e funcionava, então era regressão de verdade, não teoria.
    """
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    respostas = _conversa(uid, "criar caixinha viagem",
                          "guardei na caixinha viagem", resposta, "200")

    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 200.00, respostas[-2:]
    assert _despesas(uid) == []


def test_saque_com_comando_completo_como_resposta(uid):
    """Mesmo caso na porta do saque, onde a entity VENCE o parser do handler
    (`pocket_name or _p`) — por isso um nome mal extraído envenena o fluxo lá,
    e não no depósito."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    _conversa(uid, "criar caixinha viagem", "coloquei 300 na caixinha viagem")
    _conversa(uid, "tirar da caixinha viagem", "retirei 100 da caixinha viagem")

    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 200.00, "o saque de 100 sobre 300 não fechou"


def test_nome_do_alvo_preserva_nome_legitimo():
    """`reserva de emergência` é nome de caixinha, não preposição + substantivo —
    e é o exemplo que a própria pergunta do saque genérico usa."""
    from core.intent_router import _nome_do_alvo

    assert _nome_do_alvo("reserva de emergencia") == "reserva de emergencia"
    # catálogo desempata quando o nome literal contém o substantivo
    assert _nome_do_alvo("minha caixinha viagem", ["minha caixinha viagem"]) == "minha caixinha viagem"
    assert _nome_do_alvo("viagem") == "viagem"
    assert _nome_do_alvo("caixinha viagem") == "viagem"
    assert _nome_do_alvo("retirei 100 da caixinha viagem") == "viagem"
    assert _nome_do_alvo("investimento cdb nubank") == "cdb nubank"


# ── Uma pergunta não desaloja OUTRA pergunta (Codex P1 no #184) ─────────────

def test_pergunta_nao_desaloja_pergunta_alheia(uid):
    """As nove perguntas usam o tipo genérico `clarification`, e o
    `claim_pending_action` trata tipo igual como "a mesma pergunta de novo"
    (`db/pending.py:355`). Sem a guarda, a pergunta de valor de um SAQUE
    substituía a de um DEPÓSITO e a resposta ia para a operação errada.
    """
    import db as _db

    _db.claim_pending_action(uid, "clarification", {
        "intent": "pockets.deposit", "entities": {"pocket_name": "viagem"},
        "question": "Qual o valor? (deposito)", "orig_text": "guardei na caixinha viagem",
        "falta": "amount",
    })

    from core.handlers import pending as h_pending
    resposta = h_pending.pergunta_guardando_contexto(
        uid, "pockets.withdraw", {"pocket_name": "viagem"},
        "Qual o valor? (saque)", "tirar da caixinha viagem", falta="amount")

    viva = _db.get_pending_action(uid)
    assert viva["payload"]["intent"] == "pockets.deposit", "a pergunta do saque atropelou a do depósito"
    assert viva["payload"]["question"] == "Qual o valor? (deposito)"
    # E o usuário é avisado, em vez de receber uma pergunta que não vai ser ouvida.
    assert "outra pergunta minha" in resposta


def test_mesma_pergunta_de_novo_pode_substituir(uid):
    """CONTROLE POSITIVO da guarda acima: refazer o MESMO comando não pode
    travar o usuário — senão a guarda vira cadeado de 10 minutos."""
    import db as _db
    from core.handlers import pending as h_pending

    for alvo in ("viagem", "carro"):
        h_pending.pergunta_guardando_contexto(
            uid, "pockets.deposit", {"pocket_name": alvo},
            f"Qual o valor? ({alvo})", f"guardei na caixinha {alvo}", falta="amount")

    viva = _db.get_pending_action(uid)
    assert viva["payload"]["entities"]["pocket_name"] == "carro", "a repetição não substituiu"


# ── want_all e nome literal (Codex P2 no #184) ──────────────────────────────

def test_esvaziar_preserva_o_marcador_de_tudo(uid):
    """`want_all` é derivado do `text` pelos handlers de saque. Passar a resposta
    curta como texto perdia "esvaziar" e o bot pedia um valor."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    _conversa(uid, "criar caixinha viagem", "coloquei 300 na caixinha viagem")
    respostas = _conversa(uid, "esvaziar caixinha", "viagem")

    assert "esvaziada" in respostas[-1], respostas[-1]
    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 0.00


def test_nome_literal_com_a_palavra_caixinha(uid):
    """Caixinha criada pelo dashboard com "caixinha" DENTRO do nome. Recortar
    sempre respondia "Caixinha *viagem* não encontrada"; quem desempata é o
    catálogo do usuário."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    respostas = _conversa(uid, "criar caixinha minha caixinha viagem",
                          "guardei na caixinha viagem", "minha caixinha viagem", "200")

    assert "Depósito na caixinha" in respostas[-1], respostas[-1]
    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("minha caixinha viagem") == 200.00


# ── As 5 portas que a conversa determinística não alcança ───────────────────
#
# `investments.deposit`, `investments.withdraw` e as duas de `funds.withdraw`
# ficavam cobertas SÓ pelo teste de `ast` lá embaixo — que prova que a porta está
# LIGADA, não que ela funciona. É a mesma armadilha do teste tautológico que o
# controle negativo pegou mais acima, num eixo diferente.
#
# Medido: nenhuma de 9 frases ("aportar", "quero aportar no cdb", "resgatar do
# cdb", "sacar da reserva"…) classifica para as intents de investimento sem o
# LLM — todas caem em `out_of_scope`. Então a 1ª mensagem é injetada com a intent
# já resolvida, que é o que o classificador entregaria numa conta Pro.
#
# A RESPOSTA — a 2ª mensagem, onde o bug desta issue vive — passa pelo
# `handle_incoming` de verdade. É o trecho que importa.

def _pergunta_injetada(uid: int, intent: str, entities: dict, texto: str) -> str:
    from core.intent_classifier import IntentResult
    from core.intent_router import route

    return route(IntentResult(intent=intent, confidence=0.95, entities=entities),
                 IncomingMessage(platform="whatsapp", user_id=uid, text=texto))


@pytest.mark.parametrize("intent,entities,texto,pergunta", [
    ("investments.deposit",  {},                            "aportar",  "Qual valor você quer aportar?"),
    ("investments.withdraw", {},                            "resgatar", "De qual investimento"),
    ("investments.withdraw", {"investment_name": "cdb tst"}, "resgatar", "Qual valor você quer resgatar?"),
    ("funds.withdraw",       {},                            "sacar",    "Qual o valor?"),
    ("funds.withdraw",       {"amount": 50},                "sacar",    "retirar de qual"),
])
def test_portas_de_investimento_e_saque_armam_pendencia(uid, intent, entities, texto, pergunta):
    """Cada uma das 5 portas restantes pergunta E guarda o contexto."""
    resposta = _pergunta_injetada(uid, intent, entities, texto)

    assert pergunta in resposta, resposta
    pend = db.get_pending_action(uid)
    assert pend is not None, "a pergunta saiu sem guardar contexto"
    assert pend["action_type"] == "clarification"
    assert pend["payload"]["intent"] == intent
    assert pend["payload"]["falta"] in ("amount", "investment_name", "target_name")


def test_aporte_a_resposta_nao_vira_despesa(uid):
    """O bug da issue na porta do APORTE: a resposta ao "Qual valor?" era
    reclassificada do zero e virava `launches.add`."""
    # A 3a mensagem NAO e decorativa: sem a pendencia, "500 reais" vira uma
    # clarification de `launches.add` pedindo a DESCRICAO, e a despesa so e
    # gravada quando ela chega. Sem este turno o teste passa na `main` — foi
    # assim que ele nasceu, e o controle negativo pegou.
    _pergunta_injetada(uid, "investments.deposit", {}, "aportar")
    _conversa(uid, "500 reais", "cdb")

    assert _despesas(uid) == [], "a resposta ao aporte virou despesa avulsa"
    assert round(float(db.get_balance(uid)), 2) == 0.00


def test_saque_generico_a_resposta_nao_vira_despesa(uid):
    """Mesma coisa na porta do saque genérico (`funds.withdraw`)."""
    _pergunta_injetada(uid, "funds.withdraw", {}, "sacar")
    _conversa(uid, "200 reais", "reserva")

    assert _despesas(uid) == []
    assert round(float(db.get_balance(uid)), 2) == 0.00


# ── 3a rodada do Codex (#184) ───────────────────────────────────────────────

def test_nome_de_alvo_com_digitos_nao_vira_valor(uid):
    """Caixinha "meta 2028": o 2028 é parte do NOME, não o valor do saque.

    Medido antes do conserto, com R$ 500 na caixinha: o bot respondia "Saldo
    insuficiente na caixinha *meta 2028*" — ou seja, tentou sacar R$ 2.028 sem
    perguntar quanto. Com saldo maior teria sacado. Codex P1.
    """
    db.add_launch_and_update_balance(
        uid, "receita", 1000.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    _conversa(uid, "criar caixinha meta 2028", "coloquei 500 na caixinha meta 2028")
    respostas = _conversa(uid, "tirar da caixinha", "meta 2028")

    assert "Qual o valor" in respostas[-1], respostas[-1]
    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("meta 2028") == 500.00, "sacou sem perguntar o valor"


def test_claim_perde_para_pergunta_que_chegou_no_meio(uid):
    """A decisão e a escrita têm de ser o MESMO compare-and-swap.

    Simula a corrida: entre a leitura da linha e a gravação, outra tarefa põe uma
    pergunta nova. Sem o CAS sobre a linha lida, o `claim_pending_action` a
    releria, veria `clarification == clarification` e sobrescreveria a pergunta
    que o usuário acabou de ver. Codex P1, 3a rodada.
    """
    import db as _db
    from core.handlers import pending as h_pending

    # A linha que a nossa pergunta VAI ler: mesma intent+falta, então passa na guarda.
    _db.claim_pending_action(uid, "clarification", {
        "intent": "pockets.deposit", "entities": {}, "question": "Qual caixinha? (velha)",
        "orig_text": "guardei", "falta": "pocket_name",
    })
    lida = _db.get_pending_action(uid)

    # ... e outra tarefa troca a linha DEPOIS dessa leitura.
    original = _db.get_pending_action

    def _le_e_depois_atropela(u):
        _db.get_pending_action = original          # só intercepta a 1a leitura
        _db.set_pending_action(u, "clarification", {
            "intent": "investments.withdraw", "entities": {},
            "question": "De qual investimento? (nova)", "orig_text": "resgatar",
            "falta": "investment_name",
        })
        return lida

    _db.get_pending_action = _le_e_depois_atropela
    try:
        resposta = h_pending.pergunta_guardando_contexto(
            uid, "pockets.deposit", {}, "Qual caixinha? (nossa)", "guardei",
            falta="pocket_name")
    finally:
        _db.get_pending_action = original

    viva = _db.get_pending_action(uid)
    assert viva["payload"]["question"] == "De qual investimento? (nova)",         "a pergunta que chegou no meio foi sobrescrita"
    assert "outra pergunta minha" in resposta


# ── As duas pontas da fonte única (§0.7) ────────────────────────────────────

_ARQUIVOS_COM_PERGUNTA = (
    "core/handlers/pockets.py",
    "core/handlers/investments.py",
    "core/intent_router.py",
)


def _chamadas_de_pergunta() -> list[tuple[str, int, str, bool]]:
    """(arquivo, linha, intent, tem_falta) de cada `pergunta_guardando_contexto`."""
    achados = []
    for rel in _ARQUIVOS_COM_PERGUNTA:
        arvore = ast.parse((_RAIZ / rel).read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func.attr if isinstance(no.func, ast.Attribute) else getattr(no.func, "id", None)
            if alvo != "pergunta_guardando_contexto":
                continue
            intent = no.args[1].value if len(no.args) > 1 and isinstance(no.args[1], ast.Constant) else None
            tem_falta = any(k.arg == "falta" for k in no.keywords)
            achados.append((rel, no.lineno, intent, tem_falta))
    return achados


def test_toda_pergunta_declara_intent_conhecida_e_falta():
    """Quem pergunta tem de estar no `_INTENTS_PERGUNTA_DE_HANDLER` e gravar `falta`.

    Sem isto, uma pergunta nova arma a pendência e o `_resolve_clarification`
    não a reconhece: a resposta cai no fallback de data e a intent é
    re-executada SEM a entidade que faltava — laço infinito de pergunta.
    """
    from core.intent_router import _INTENTS_PERGUNTA_DE_HANDLER

    chamadas = _chamadas_de_pergunta()
    assert len(chamadas) == 9, f"o inventário mudou: {chamadas}"

    for rel, linha, intent, tem_falta in chamadas:
        assert intent in _INTENTS_PERGUNTA_DE_HANDLER, f"{rel}:{linha} intent {intent!r} fora do conjunto"
        assert tem_falta, f"{rel}:{linha} não grava `falta` — o leitor não saberá o que preencher"


def test_conjunto_nao_tem_intent_orfa():
    """Linha órfã no conjunto é tão ruim quanto ausência: mente sobre a cobertura."""
    from core.intent_router import _INTENTS_PERGUNTA_DE_HANDLER

    usadas = {intent for _, _, intent, _ in _chamadas_de_pergunta()}
    assert _INTENTS_PERGUNTA_DE_HANDLER - usadas == set()
