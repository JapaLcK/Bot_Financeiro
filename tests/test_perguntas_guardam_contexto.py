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
    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tirar da caixinha viagem", "retirei 100 da caixinha viagem", "1")

    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 200.00, "o saque de 100 sobre 300 não fechou"


def test_comando_completo_sugerido_passa_pelo_desempate(uid):
    """O comando COMPLETO que a própria pergunta sugere ("Tente: *coloquei 200
    na caixinha viagem*") é valor + alvo — célula D8 do #281 —, então ele PASSA
    pelo desempate antes de fechar.

    CUSTO DECLARADO: o bot pede um turno a mais para o texto que ele mesmo
    recomendou. Vale a pena porque a alternativa é a classe do #189 (o mesmo
    texto respondendo a uma pergunta de SAQUE movia dinheiro no sentido
    errado), e porque perguntar nunca escreve. Com o "1", o depósito fecha
    exatamente como antes."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False,
    )
    respostas = _conversa(uid, "criar caixinha viagem",
                          "guardei na caixinha viagem",
                          "coloquei 200 na caixinha viagem")

    assert "1️⃣" in respostas[-1], respostas[-1]
    respostas += _conversa(uid, "1")

    pockets = {p["name"]: float(p["balance"]) for p in (db.list_pockets(uid) or [])}
    assert pockets.get("viagem") == 200.00, respostas[-2:]
    assert _despesas(uid) == []


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


# ═══════════════════════════════════════════════════════════════════════════
# A TABELA estados × eventos do resolver (#186)
# ═══════════════════════════════════════════════════════════════════════════
#
# Por que uma tabela e não mais um caso por bug: o resolver levou QUATRO rodadas
# de revisão, cada uma consertando a transição que a anterior deixou aberta. A
# causa era `falta` acumular dois papéis — decidir o que LER da resposta e o que
# RE-PERGUNTAR. Como seletor de leitura, cada sub-ramo aprendia um pedaço
# diferente do mundo. Rebaixado a decisor de re-pergunta, sobra UMA leitura, e o
# produto estados × eventos vira soma: esta tabela.
#
# `_funde_a_resposta` é PURA (o catálogo entra por parâmetro), então o eixo dos
# eventos se testa aqui sem conversa nenhuma — e adicionar célula = adicionar
# LINHA, que é o antídoto contra a rodada 6. As conversas logo abaixo cobrem a
# integração de verdade, que a tabela sozinha não prova.

_CATALOGO = ["viagem", "carro", "meta 2028", "minha caixinha viagem",
             "reserva de emergencia", "zerar divida"]


@pytest.mark.parametrize("resposta,nome,valor,tudo,recusa", [
    ("100",                            None,           100.0, None, None),
    ("200 reais",                      None,           200.0, None, None),
    ("viagem",                         "viagem",       None,  None, None),
    ("caixinha viagem",                "viagem",       None,  None, None),
    ("da caixinha viagem",             "viagem",       None,  None, None),
    # nome literal com o substantivo dentro: o catálogo desempata
    ("minha caixinha viagem",          "minha caixinha viagem", None, None, None),
    # nome do catálogo COM DÍGITOS: é nome, não valor (Codex P1, rodada 3)
    ("meta 2028",                      "meta 2028",    None,  None, None),
    # nome legítimo que parece preposição + substantivo
    ("reserva de emergencia",          "reserva de emergencia", None, None, None),
    # comando completo: nome E valor da mesma resposta
    ("retirei 100 da caixinha viagem", "viagem",       100.0, None, None),
    # comando completo SEM o substantivo — o catálogo acha o nome dentro
    ("tira 100 da viagem",             "viagem",       100.0, None, None),
    # alvo FORA do catálogo: o portão barra, o nome guardado permanece
    ("retirei 100 do salario",         "carro",        100.0, None, None),
    # marcador de tudo: quantidade sem número
    ("esvaziar",                       None,           None,  True, None),
    ("zerar",                          None,           None,  True, None),
    # nome do catálogo que CONTÉM o marcador: é NOME, não "esvaziar" (P1-B)
    ("zerar divida",                   "zerar divida", None,  None, None),
    ("tira 100 da zerar divida",       "zerar divida", 100.0, None, None),
    # e o marcador que SOBREVIVE à remoção do nome continua valendo
    ("esvaziar a zerar divida",        "zerar divida", None,  True, None),
    # CONTROLE POSITIVO da remoção: nome sem marcador dentro segue esvaziando
    ("esvaziar a viagem",              "viagem",       None,  True, None),
    # valores perigosos: recusa
    ("-10",                            None,           None,  None, "nao_positivo"),
    ("0",                              None,           None,  None, "nao_positivo"),
    ("132 50",                         None,           None,  None, "nao_entendi"),
])
def test_tabela_de_eventos(resposta, nome, valor, tudo, recusa):
    """Um evento por linha. O estado é sempre o mesmo — alvo `carro` guardado,
    sem quantidade — porque a LEITURA da resposta não depende do estado; é o
    ponto inteiro do conserto."""
    from core.intent_router import _funde_a_resposta

    ents, motivo = _funde_a_resposta(
        "pockets.withdraw", {"pocket_name": "carro"}, resposta, _CATALOGO)

    assert motivo == recusa
    assert ents.get("pocket_name") == (nome or "carro")
    assert ents.get("amount") == valor
    assert bool(ents.get("want_all")) is bool(tudo)


@pytest.mark.parametrize("guardado,resposta,amount,tudo", [
    # A EXCLUSIVIDADE, nas duas direções. `quantity` é um valor de soma
    # (QUANTIA | TUDO | ausente), não dois campos independentes: união
    # permitiria `amount=100 AND want_all=True`, e aí quem decide é a
    # precedência do handler — que ninguém escolheu.
    ({"want_all": True}, "tira 100 da viagem", 100.0, False),   # TUDO -> quantia
    ({"want_all": True}, "tira 100 da zerar divida", 100.0, False),  # idem, nome com marcador
    ({"amount": 100.0},  "esvaziar",           None,  True),    # quantia -> TUDO
    # sem quantidade na resposta, a guardada permanece
    ({"amount": 100.0},  "viagem",             100.0, False),
    ({"want_all": True}, "viagem",             None,  True),
])
def test_quantidade_e_excludente(guardado, resposta, amount, tudo):
    from core.intent_router import _funde_a_resposta

    ents, _ = _funde_a_resposta(
        "pockets.withdraw", {"pocket_name": "carro", **guardado}, resposta, _CATALOGO)

    assert ents.get("amount") == amount
    assert bool(ents.get("want_all")) is tudo
    assert not (ents.get("amount") and ents.get("want_all")), "as duas ao mesmo tempo"


# ── As células, pela conversa real ──────────────────────────────────────────

@pytest.fixture
def sem_teto_de_caixinha(monkeypatch):
    """O plano Grátis limita a 1 caixinha e estas células precisam de duas."""
    monkeypatch.setattr(
        "core.services.plan_service.check_can_create_pocket", lambda uid: None)


def _duas_caixinhas(uid, saldo=2000.0):
    db.add_launch_and_update_balance(
        uid, "receita", saldo, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False)
    db.create_pocket(uid, "carro")
    db.create_pocket(uid, "viagem")
    _conversa(uid, "coloquei 300 na caixinha carro", "coloquei 300 na caixinha viagem")


def _saldos(uid):
    return {x["name"]: float(x["balance"]) for x in (db.list_pockets(uid) or [])}


def test_186_alvo_da_resposta_vence_o_guardado(uid, sem_teto_de_caixinha):
    """O bug da #186, confirmado em produção com dinheiro real: a resposta nomeia
    OUTRA caixinha e o bot debitava a guardada."""
    _duas_caixinhas(uid)

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tirar da caixinha carro", "carro",
              "retirei 100 da caixinha viagem", "1")

    p = _saldos(uid)
    assert p["viagem"] == 200.00, "não saiu da caixinha que o usuário nomeou"
    assert p["carro"] == 300.00, "saiu da caixinha ERRADA"


def test_alvo_fora_do_catalogo_nao_sobrescreve(uid, sem_teto_de_caixinha):
    """CONTROLE do portão: "explícito" não é o mesmo que "existente".

    O #281 mudou o CAMINHO e manteve o que este teste protege — o dinheiro do
    `carro`. `retirei 100 do salario` é valor + alvo (célula D8), então a porta
    PERGUNTA; com o "2" o comando roda sozinho e, como `salario` não existe,
    ele erra e nada se move. Medido: "Não encontrei *salario* nem em caixinhas
    nem em investimentos".

    Antes o resolver caía no alvo guardado e sacava R$ 100 do `carro` sem que
    ninguém tivesse escolhido isso. Agora quem escolhe é o usuário: o turno a
    mais deixou de ser teto e virou a pergunta de desempate."""
    _duas_caixinhas(uid)

    respostas = _conversa(uid, "tirar da caixinha carro", "carro",
                          "retirei 100 do salario", "2")

    assert "Não encontrei" in respostas[-1], respostas[-1]
    assert _saldos(uid) == {"carro": 300.00, "viagem": 300.00}, \
        "o alvo inexistente atropelou o guardado"


def test_nota_do_lancamento_nao_cita_o_alvo_velho(uid, sem_teto_de_caixinha):
    """A descrição é auditoria: extrato, suporte e a IA lendo o histórico depois.
    Gravar "tirar da caixinha carro" num lançamento que debitou `viagem`
    envenena os três."""
    _duas_caixinhas(uid)

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tirar da caixinha carro", "carro",
              "retirei 100 da caixinha viagem", "1")

    saque = next(l for l in db.list_launches(uid, limit=20)
                 if l.get("tipo") == "saque_caixinha")
    assert saque["alvo"] == "viagem"
    assert "carro" not in (saque.get("nota") or ""), "a nota cita a caixinha errada"


def test_esvaziar_respondido_a_pergunta_de_valor(uid):
    """`esvaziar` não tem número: o `_extract_valor` devolvia None e a pergunta
    voltava em laço, sem nunca esvaziar."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False)
    _conversa(uid, "criar caixinha viagem", "coloquei 300 na caixinha viagem")

    respostas = _conversa(uid, "tirar da caixinha viagem", "viagem", "esvaziar")

    assert "esvaziada" in respostas[-1], respostas[-1]
    assert _saldos(uid)["viagem"] == 0.00


def test_tudo_guardado_mais_quantia_nova_nao_esvazia(uid):
    """A célula que o dono levantou revisando o plano. Com `want_all` e `amount`
    como campos independentes (união), a caixinha era ESVAZIADA quando o usuário
    acabou de dizer R$ 100 — medido: dava 0,00."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False)
    _conversa(uid, "criar caixinha viagem", "coloquei 300 na caixinha viagem")

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "esvaziar caixinha", "tira 100 da viagem", "1")

    assert _saldos(uid)["viagem"] == 200.00, "esvaziou apesar de o usuário ter dito 100"


# ── #186, rodada da tabela estados × eventos: três células que sobraram ──────
#
# CONTROLES NEGATIVOS (cada um injetado num caso que estava VERDE depois do fix):
#   P1-A  `core/intent_router.py:1281` de volta para
#         `bool(entities.get("want_all")) or marcador_de_tudo(text)`
#         -> test_p1a_quantia_da_resposta_nao_e_religada_pelo_orig_text
#   P1-B  `core/intent_router.py:951` de volta para `marcador_de_tudo(resposta)`
#         -> test_tabela_de_eventos[tira 100 da zerar divida...] e
#            test_p1b_nome_com_marcador_nao_esvazia_pela_conversa
#   P1-C  remover o bloco `if len(dentro) > 1` de `_nome_do_alvo`
#         -> test_p1c_nome_aninhado_escolhe_o_mais_especifico e
#            test_p1c_caixinha_aninhada_pela_conversa
# Os controles POSITIVOS estão nomeados abaixo: os três consertos RESTRINGEM
# (uma flag deixa de ser religada, um marcador deixa de contar, um empate deixa
# de ser recusado), então cada um precisa provar que o caminho bom continua vivo.


def _caixinhas_com_saldo(uid: int, *nomes: str, saldo: float = 300.0) -> None:
    """Caixinhas com saldo, pelo `db`. NÃO escapa do teto do plano Grátis: o
    `db.create_pocket` chama `check_can_create_pocket` (`db/pockets.py:531`), e
    com dois nomes o teste precisa da fixture `sem_teto_de_caixinha`."""
    db.add_launch_and_update_balance(
        uid, "receita", saldo * len(nomes) + 100, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False)
    for nome in nomes:
        db.create_pocket(uid, nome)
        db.pocket_deposit_from_account(uid, nome, saldo)


# P1-A ── a porta do saque genérico religava o `want_all=False` pelo orig_text ─

def test_p1a_quantia_da_resposta_nao_e_religada_pelo_orig_text(uid):
    """A nota do lançamento é o `orig_text` ("esvaziar"), e o
    `_execute_generic_withdraw` relia esse texto com `or`: a caixinha era
    ESVAZIADA apesar de o usuário ter acabado de dizer R$ 100."""
    _caixinhas_com_saldo(uid, "viagem")

    _pergunta_injetada(uid, "funds.withdraw", {}, "esvaziar")
    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tira 100 da viagem", "1")

    assert _saldos(uid)["viagem"] == 200.00, "esvaziou apesar do R$ 100 da resposta"


def test_p1a_esvaziar_direto_ainda_esvazia(uid):
    """CONTROLE POSITIVO do P1-A: sem a chave `want_all` nas entities, o
    `else marcador_de_tudo(text)` continua vivo e o comando direto esvazia."""
    _caixinhas_com_saldo(uid, "viagem")

    _pergunta_injetada(uid, "funds.withdraw", {"target_name": "viagem"},
                       "esvaziar caixinha viagem")

    assert _saldos(uid)["viagem"] == 0.00, "o comando direto de esvaziar parou de funcionar"


# P1-B ── nome de caixinha que CONTÉM o marcador de tudo ─────────────────────

def test_p1b_nome_com_marcador_nao_esvazia_pela_conversa(uid):
    """"zerar dívida" é nome legítimo. "tira 100 da zerar divida" pedia 100 —
    o marcador estava dentro do NOME, não na quantidade."""
    _caixinhas_com_saldo(uid, "zerar divida")

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tirar da caixinha", "tira 100 da zerar divida", "1")

    assert _saldos(uid)["zerar divida"] == 200.00, "o nome da caixinha virou 'esvaziar'"


# P1-C ── dois nomes do catálogo dentro da resposta, um contido no outro ─────

def test_p1c_nome_aninhado_escolhe_o_mais_especifico():
    from core.intent_router import _nome_do_alvo

    assert _nome_do_alvo("tira 100 da minha viagem",
                         ["viagem", "minha viagem"]) == "minha viagem"


def test_p1c_nomes_disjuntos_continuam_recusados():
    """CONTROLE POSITIVO do P1-C: com nomes DISJUNTOS escolher seria adivinhar,
    e o empate continua recusado — quem responde é o handler, com
    "não encontrada"."""
    from core.intent_router import _nome_do_alvo

    assert _nome_do_alvo("tira 100 da ana e do bruno",
                         ["ana", "bruno"]) == "tira 100 da ana e do bruno"


def test_p1c_caixinha_aninhada_pela_conversa(uid, sem_teto_de_caixinha):
    """O dinheiro: o empate recusado deixava o alvo GUARDADO vencer, e o saque
    saía da caixinha errada."""
    _caixinhas_com_saldo(uid, "viagem", "minha viagem")

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "tirar da caixinha viagem", "tira 100 da minha viagem", "1")

    p = _saldos(uid)
    assert p["minha viagem"] == 200.00, "não saiu da caixinha que o usuário nomeou"
    assert p["viagem"] == 300.00, "saiu da caixinha ERRADA"


# ── Regressões do 45db911, achadas pelo Tester ───────────────────────────────
#
# CONTROLES NEGATIVOS (cada um injetado num caso que estava VERDE depois do fix):
#   P0  `utils_text.py:28`, remover o `if not word: return False`
#       -> test_p0_caixinha_so_emoji_nao_sequestra_o_saque e
#          test_p0_caixinha_so_emoji_nao_sequestra_o_deposito
#   P1  `core/intent_router.py:863`, o `if not any(...)` de volta para
#       `if all(contains_word(normalize_text(maior), normalize_text(n)) ...)`
#       -> test_p1_duas_mencoes_aninhadas_continuam_recusadas e
#          test_p1_duas_caixinhas_aninhadas_na_frase_nao_movem_dinheiro
#   P3  `core/intent_router.py:1136`, `redirecionou` de volta para
#       `bool(antes and depois and normalize_text(antes) != normalize_text(depois))`
#       -> test_p3_nota_nao_mente_quando_nao_havia_alvo_guardado
# Os POSITIVOS do P1 continuam sendo `test_p1c_nome_aninhado_escolhe_o_mais_
# especifico` e `test_p1c_nomes_disjuntos_continuam_recusados`.

_EMOJI = "\U0001f4b0"  # 💰 — nome de caixinha legítimo que normaliza para ""


def test_p0_caixinha_so_emoji_nao_sequestra_o_saque(uid, sem_teto_de_caixinha):
    """Nome que normaliza para "" virava `contains_word(texto, "")` -> `\\b\\b`,
    que casa qualquer texto: o saque saía da caixinha do emoji."""
    _caixinhas_com_saldo(uid, _EMOJI, "viagem")

    _conversa(uid, "tirar da caixinha", "tira 100 da poupanca")

    assert _saldos(uid) == {_EMOJI: 300.00, "viagem": 300.00}, \
        "o saque saiu da caixinha do emoji"


def test_p0_caixinha_so_emoji_nao_sequestra_o_deposito(uid, sem_teto_de_caixinha):
    """Mesma raiz na outra ponta: o depósito caía na caixinha do emoji."""
    _caixinhas_com_saldo(uid, _EMOJI, "viagem")

    r = _conversa(uid, "guardei na caixinha", "coloca 100 na poupanca")

    assert _saldos(uid) == {_EMOJI: 300.00, "viagem": 300.00}, \
        f"o depósito caiu na do emoji: {r[-1]!r}"


def test_p0_caixinha_so_emoji_continua_utilizavel(uid, sem_teto_de_caixinha):
    """Controle POSITIVO do par acima: a guarda RESTRINGE o casamento, então
    precisa provar que o caminho legítimo sobreviveu (CLAUDE.md §3). Sem este
    caso, um conserto que tornasse a caixinha inerte passaria — e inerte é pior
    que o bug, porque o usuário perde o acesso ao dinheiro que já guardou.

    O nome normaliza para "" e por isso nunca casa por TEXTO; quem resolve é o
    `_eh_nome_do_catalogo`, que compara igualdade, não contenção.
    """
    _caixinhas_com_saldo(uid, _EMOJI, "viagem")

    r = _conversa(uid, "guardei na caixinha", _EMOJI, "100")

    assert _saldos(uid) == {_EMOJI: 400.00, "viagem": 300.00}, \
        f"a caixinha ficou inalcançável: {r[-1]!r}"


def test_p1_duas_mencoes_aninhadas_continuam_recusadas():
    """"um nome cabe no outro" não é "uma menção só": com DUAS menções o
    desempate do aninhamento adivinhava e escolhia a maior."""
    from core.intent_router import _nome_do_alvo

    resposta = "tira 100 da viagem e 50 da viagem japao"
    assert _nome_do_alvo(resposta, ["viagem", "viagem japao"]) == resposta


def test_p1_duas_caixinhas_aninhadas_na_frase_nao_movem_dinheiro(uid, sem_teto_de_caixinha):
    """O dinheiro: o alvo adivinhado era debitado em silêncio."""
    _caixinhas_com_saldo(uid, "viagem", "viagem japao")

    _conversa(uid, "tirar da caixinha", "tira 100 da viagem e 50 da viagem japao")

    assert _saldos(uid) == {"viagem": 300.00, "viagem japao": 300.00}, \
        "moveu dinheiro num alvo adivinhado"


def test_p3_nota_nao_mente_quando_nao_havia_alvo_guardado(uid):
    """Irmã do `test_nota_do_lancamento_nao_cita_o_alvo_velho`: sem alvo
    guardado, o `redirecionou` dava False e a nota do saque de R$ 100 ficava
    "esvaziar caixinha"."""
    _caixinhas_com_saldo(uid, "viagem")

    # O "1" é o desempate do #281: valor + alvo na mesma mensagem é AMBÍGUO
    # (célula D8), e "era a resposta" devolve o turno ao resolver — que é o que
    # este teste mede.
    _conversa(uid, "esvaziar caixinha", "tira 100 da viagem", "1")

    saque = next(l for l in db.list_launches(uid, limit=20)
                 if l.get("tipo") == "saque_caixinha")
    assert _saldos(uid)["viagem"] == 200.00
    assert "esvaziar" not in (saque.get("nota") or "").lower(), \
        f"a nota diz esvaziar num saque de 100: {saque.get('nota')!r}"


# ── #185: o veto de catálogo na porta 2 ─────────────────────────────────────
#
# A pergunta de NOME já carrega o valor em `entities["amount"]`, que o
# `_ja_tem_o_valor` (porta 2) não lê — ele só conhece `valor`. Então
# `saquei 200` + `saldo` abandonava a pergunta e os R$ 200 sumiam, mesmo com
# uma caixinha chamada `saldo` para receber o saque.
#
# POR QUE NÃO A ONE-LINER DA ISSUE, e OS TRÊS TETOS do conserto: o texto mora
# UMA VEZ SÓ, no comentário do veto em `core/intent_router.py`
# (`_clarification_abandonada`, bloco `VETO DE CATÁLOGO (#185)`). Não repita
# aqui — a primeira redação errada custou correção em três lugares (§0.7).
# Quem prende cada teto:
#   teto 1 (`extrato` sem caixinha)  -> test_185_abandono_comum_continua_vivo
#   teto 2 (comando vira comando pendente, esvaziamento integral)
#                                    -> test_185_nome_do_catalogo_vence_o_comando
#                                       test_185_teto2_comando_pendente_esvazia_a_caixinha
#   teto 3 (dispara sem valor a perder)
#                                    -> test_185_teto3_veto_dispara_sem_valor_a_perder
#
# CONTROLES NEGATIVOS (medidos, cada um injetado num caso que estava VERDE):
#   remover o bloco `if falta and isinstance(intent, str) and falta ==
#   _CHAVE_DO_NOME.get(intent)` de `_clarification_abandonada`
#     -> test_185_nome_do_catalogo_vence_o_comando,
#        test_185_teto2_comando_pendente_esvazia_a_caixinha,
#        test_185_teto3_veto_dispara_sem_valor_a_perder
#   trocar o `return not _eh_nome_do_catalogo(...)` daquele bloco por
#   `return False`                -> test_185_abandono_comum_continua_vivo
#
# CLASSE CEGA: as portas de caixinha/investimento cujo payload guarda `amount`
# só são alcançáveis via LLM, que não roda nos testes. O que esta seção prova é
# a rota `funds.withdraw` (`classify("saquei 200")` -> `funds.withdraw
# {'amount': 200.0}`, determinístico).

def test_185_nome_do_catalogo_vence_o_comando(uid):
    """O caso da issue: a resposta É uma caixinha do usuário, não um comando."""
    _caixinhas_com_saldo(uid, "saldo")

    respostas = _conversa(uid, "saquei 200", "saldo")

    assert "retirar de qual" in respostas[0], respostas[0]
    assert _saldos(uid)["saldo"] == 100.00, \
        f"o saque de R$ 200 na caixinha 'saldo' não fechou: {respostas[-1]!r}"


def test_185_abandono_comum_continua_vivo(uid):
    """CONTROLE POSITIVO, e é ele que separa este conserto do proposto na issue.

    Sem caixinha chamada "saldo", `saldo` continua sendo o COMANDO de saldo. A
    one-liner da issue derruba exatamente este caso (medido: o bot passa a
    responder "Não encontrei *saldo*..."); o veto de catálogo o mantém.
    """
    respostas = _conversa(uid, "saquei 200", "saldo")

    assert "Conta Corrente" in respostas[-1], respostas[-1]
    assert _saldos(uid) == {}, "não havia caixinha nenhuma para mover"


def test_185_saque_legitimo_pelo_nome_ainda_funciona(uid):
    """CONTROLE POSITIVO 2: o caminho normal da pergunta de nome, com um nome
    que não colide com comando nenhum."""
    db.add_launch_and_update_balance(
        uid, "receita", 500.0, alvo="salário", nota="setup",
        categoria="salário", is_internal_movement=False)

    _conversa(uid, "criar caixinha viagem", "coloquei 300 na caixinha viagem",
              "saquei 100", "viagem")

    assert _saldos(uid)["viagem"] == 200.00


def test_185_teto2_comando_pendente_esvazia_a_caixinha(uid):
    """TETO 2 no tamanho medido: não é "executa o saque", é esvaziamento
    integral. `esvaziar caixinha` deixa `entities={}` e o handler lê o `tudo` do
    `orig_text`, então a resposta `saldo` — que o classificador lê como
    `balance.check` com confiança 1.0 — zera a caixinha inteira."""
    _caixinhas_com_saldo(uid, "saldo")

    respostas = _conversa(uid, "esvaziar caixinha", "saldo")

    assert "Qual caixinha" in respostas[0], respostas[0]
    assert "esvaziada" in respostas[-1], respostas[-1]
    assert _saldos(uid)["saldo"] == 0.00, \
        f"o teto 2 é maior que um saque: a caixinha inteira foi: {respostas[-1]!r}"


def test_185_teto3_veto_dispara_sem_valor_a_perder(uid):
    """TETO 3: `tirar da caixinha` grava `entities={}` (o `falta` vem antes da
    checagem de valor), então o veto segura a pergunta sem ter valor nenhum a
    proteger. Custa UM turno — a pergunta vira a de valor e nada se move."""
    _caixinhas_com_saldo(uid, "saldo")

    respostas = _conversa(uid, "tirar da caixinha", "saldo")

    assert "Qual o valor" in respostas[-1], \
        f"sem o veto aqui viria o saldo; com ele, a pergunta de valor: {respostas[-1]!r}"
    assert "Conta Corrente" not in respostas[-1], respostas[-1]
    assert _saldos(uid)["saldo"] == 300.00, "nada podia se mover neste turno"


def test_185_pergunta_de_valor_continua_abandonando(uid, sem_teto_de_caixinha):
    """O veto vale SÓ quando a pergunta pede o NOME. Com `falta == "amount"` a
    resposta `saldo` não responde nada que a pergunta pediu, e a escotilha do
    comando continua valendo — mesmo com uma caixinha chamada `saldo` no
    catálogo, que é o que faz este caso discriminar o veto."""
    _caixinhas_com_saldo(uid, "viagem", "saldo")

    respostas = _conversa(uid, "tirar da caixinha viagem", "viagem", "saldo")

    assert "Qual o valor" in respostas[1], respostas[1]
    assert "Conta Corrente" in respostas[-1], respostas[-1]
    assert _saldos(uid) == {"viagem": 300.00, "saldo": 300.00}, \
        "sacou sem que o valor fosse dito"


# ── #281: a TABELA das 16 células, pela conversa ────────────────────────────
#
# P0 de dinheiro confirmado em produção (#189, fechada com os dados). Com uma
# pergunta de handler viva, o resolver lia número e alvo da resposta e NUNCA o
# verbo: `gastei 50 no mercado` virava saque de caixinha (dinheiro no sentido
# errado, despesa não registrada) e `guardei 100 na caixinha viagem` virava
# saque (o usuário disse GUARDEI).
#
# A REGRA DO DONO, e o QUINTO SINAL que a fecha (`core/intent_router.py`,
# `_quantidade_fecha`): **a quantidade é a última coisa da mensagem?**
#   fecha  -> a mensagem é RESPOSTA         -> resolve a pergunta
#   sobrou -> a mensagem é AMBÍGUA          -> PERGUNTA, e ninguém escreve
# O que o classificador ainda decide são duas células: o intent de LEITURA
# (`ABANDONA`) e o comando SEM quantidade (`COMANDO_SEM_QUANTIDADE`).
#
# A TABELA — R resolve · A abandona · ? pergunta (não escreve). Uma célula, um
# teste, e esta é a única cópia dela no repositório (§0.7):
#
#   A0  LEITURA               saldo, extrato                       A
#   B0  NEUTRO                132, cem, tudo, ok, retirei tudo     R
#   C1  comando sem quantia   fatura, meus cartoes, criar caixinha A
#   C2/C3 idem COM quantia    fatura 132, fatura tudo              R
#   D1  verbo puro            gastei, paguei, recebi               R
#   D2  cauda sem número      gastei o resto, gastei metade        R
#   D3  cauda alvo sem número gastei no mercado                    R
#   D4a TUDO no fim           gastei tudo, gastei quase tudo       R
#   D4b TUDO com cauda        gastei tudo mesmo                    ?
#   D5  TUDO + alvo           esvaziar caixinha                    ?
#   D6  número no fim         paguei 132, saquei 100, investi 100  R
#   D7  número + cauda        paguei 132 ok / hoje / no debito     ?
#   D8  número + alvo         gastei 50 no mercado                 ?
#   D9  valor perigoso        paguei, 132 50 / gastei -10          R
#   D10 pontuação de prosa    paguei 132,50. foi isso              R
#   D11 quantidade + UNIDADE  100 reais, 30 reais e 50 centavos    R
#
# D9 e D10 são medidos em `tests/test_bill_amount_pending.py`, onde moram as
# duas guardas que os produzem (o filtro de dano e a guarda de entrega).
#
# D11 mudou no #288, e é a diferença entre a tabela e a REGRA DO DONO. A célula
# era "começa pelo valor" e resolvia por um curto-circuito
# (`_STARTS_WITH_VALUE_RE`) posto ANTES do quinto sinal — que também levava
# `50 no mercado`, `120 de luz` e `100 na caixinha viagem` junto, e essas TRÊS
# são a regra 3 do dono. O dano NÃO era o mesmo nas 6 células (NOME × VALOR ×
# 3 textos): 4 escreviam 1 linha em `launches`, e as outras DUAS — NOME ×
# `50 no mercado` e NOME × `120 de luz` — escreviam ZERO e matavam a pendência,
# com o texto engolido como nome de caixinha ("Caixinha *50 no mercado* não
# encontrada"). Remedido em 2026-09-04 nas duas árvores — `main` em `ee0524a`
# e o HEAD anterior a este conserto, `56ed7f1` —, copiando ESTE arquivo para
# dentro delas:
#   git worktree add /tmp/m288 ee0524a   # ou 56ed7f1
#   cp tests/test_perguntas_guardam_contexto.py /tmp/m288/tests/
#   cd /tmp/m288 && pytest tests/test_perguntas_guardam_contexto.py \
#       -k test_288_regra3 -q --tb=line
# Saída idêntica nas duas: `6 failed` — 4 em "escreveu em `launches`"
# (`assert 4 == 3`) e 2 em "o desempate não foi armado: None". Remeça antes de
# reusar qualquer um desses números.
# O que separa as duas regras é a UNIDADE, não a posição: depois de `100` vem
# `reais`, que é o próprio valor; depois de `50` vem um ALVO
# (`core/intent_router.py::_UNIDADE_DE_VALOR_RE`).
#
# CADA TESTE AFIRMA TRÊS COISAS, e a terceira é a que faltava por três rodadas:
# o saldo da caixinha e da conta; que não nasceu despesa avulsa; e o que
# aconteceu com a PENDÊNCIA. Um teste que só olha saldo passa igual quando a
# pergunta MORRE levando o dinheiro junto — foi assim que `gastei` escapou.
#
# CONTROLES NEGATIVOS do grupo, MEDIDOS um a um nesta árvore (os números estão
# no relato do PR #288; remedir antes de reusar):
#   1. esvaziar o `COMANDO_SEM_QUANTIDADE`
#      -> test_281_c1_comando_sem_quantidade_abandona vermelho (3 casos).
#   2. trocar o `return "pergunta"` por `"resolve"` (desligar o desempate)
#      -> test_281_quantidade_com_cauda_nao_escreve vermelho em D5/D7/D8, e os
#         três testes do desempate junto.
#   3. desligar o quinto sinal (toda quantidade vira `"pergunta"`)
#      -> test_281_quantidade_que_fecha_e_resposta vermelho em D4a e D6.
#   4. repor o `_STARTS_WITH_VALUE_RE` no topo da via (o defeito do #288)
#      -> test_288_regra3_quantidade_com_alvo_nunca_escreve vermelho nas 6.
# CONTROLE POSITIVO: test_281_resposta_compativel_nunca_vira_lancamento —
# `100 reais`, `cem`, `132,50`, `R$ 100`, `30 reais e 50 centavos` e `tudo`
# continuam fechando o saque. Sem ele o grupo passaria num código que recusa
# tudo, que é pior que o bug.
#
# CLASSE CEGA (a mesma do #185): o LLM está fora dos testes, então o
# `classify_with_context` nunca roda e os payloads de caixinha/investimento que
# carregam `amount` só são alcançáveis por ele. A rota determinística provada
# aqui é a `funds.withdraw`.
#
# TETOS, medidos, e todos escritos também no código:
#
#   1. o verbo que os tiers 1-2 não conhecem continua sequestrado — `poupei
#      100`, `mercado 50`, `uber 25`, `gasteii 50 no mercado` e `gasto fixo
#      aluguel 1200` são todos `out_of_scope 0.0`. Como B0, eles resolvem a
#      pergunta. A porta 2 roda `allow_ai=False` de propósito (ver
#      `abandona_pergunta_de_valor`).
#   2. FECHADO no #288: a unidade deixou de contar como cauda (`paguei 100
#      reais` resolve, como `paguei 2 mil` já resolvia). O vocabulário é o
#      `h_bills._UNIDADE`, mais `centavos` e `k`, que na porta 1 mudariam o
#      valor pago. Sobra o TETO NOVO: unidade que a lista não tem ("paguei 100
#      pratas") continua perguntando — custa um turno, nunca dinheiro.
#   3. `gastei tudo mesmo` PERGUNTA em vez de esvaziar. Decisão do dono, e é a
#      única linha que saiu da tabela anterior: esvaziar zera a caixinha
#      inteira, e o lado seguro de uma operação irreversível é o que não
#      escreve.


def _pergunta_de_valor(uid):
    """Deixa viva a pergunta "Qual o valor?" de um saque, com R$ 300 na
    caixinha `viagem` e R$ 100 na conta (`_caixinhas_com_saldo` credita 400 e
    deposita 300). Duas mensagens, não uma: `tirar da
    caixinha viagem` pergunta o NOME primeiro (medido)."""
    _caixinhas_com_saldo(uid, "viagem")
    respostas = _conversa(uid, "tirar da caixinha viagem", "viagem")
    assert "Qual o valor" in respostas[-1], respostas[-1]


def _nada_se_moveu(uid, resposta):
    """As duas primeiras afirmações: caixinha, conta e nenhuma despesa avulsa."""
    assert _saldos(uid)["viagem"] == 300.00, resposta
    assert round(float(db.get_balance(uid)), 2) == 100.00, resposta
    assert _despesas(uid) == [], f"virou lançamento: {resposta!r}"


@pytest.mark.parametrize("celula,resposta,saque", [
    ("B0", "132", 132.00),
    ("B0", "cem", 100.00),
    ("B0", "tudo", 300.00),
    ("B0", "retirei tudo", 300.00),
    ("C2", "fatura 132", 132.00),
    ("C3", "fatura tudo", 300.00),
    ("D4a", "gastei tudo", 300.00),
    ("D4a", "gastei quase tudo", 300.00),
    ("D6", "paguei 132", 132.00),
    ("D6", "saquei 100", 100.00),
    ("D6", "investi 100", 100.00),
])
def test_281_quantidade_que_fecha_e_resposta(uid, celula, resposta, saque):
    """B0, C2/C3, D4a e D6: a quantidade fecha a mensagem, então ela É a
    resposta — mesmo com verbo na frente (`paguei 132`), com comando na frente
    (`fatura 132`) ou com o marcador de tudo no lugar do número.

    `investi 100` e `saquei 100` uniformizam a assimetria que sobrava: eram
    comando (abandonavam, e o saque de R$ 100 se perdia quando o alvo não
    existia); agora são resposta, como `paguei 132` sempre foi."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, resposta)

    assert _saldos(uid)["viagem"] == round(300.00 - saque, 2), \
        f"{celula}: a resposta não fechou o saque: {respostas[-1]!r}"
    assert round(float(db.get_balance(uid)), 2) == round(100.00 + saque, 2)
    assert _despesas(uid) == [], f"{celula}: virou lançamento: {respostas[-1]!r}"
    assert db.get_pending_action(uid) is None, "a pergunta tinha de ser consumida"


@pytest.mark.parametrize("celula,resposta", [
    ("D1", "gastei"), ("D1", "paguei"), ("D1", "recebi"),
    ("D2", "gastei o resto"), ("D2", "gastei metade"),
    ("D3", "gastei no mercado"), ("D3", "guardei na caixinha viagem"),
])
def test_281_sem_quantidade_a_pergunta_continua_viva(uid, celula, resposta):
    """D1, D2 e D3: sem quantidade nenhuma o comando não teria como rodar — só
    faria a própria pergunta, com a palavra do usuário no lugar do alvo
    ("🐷 Quanto foi no *metade*?"), que é a classe do
    `test_ia_com_valor_nao_engorda_o_alvo_com_a_resposta`.

    A TERCEIRA afirmação é a que discrimina: sem ela este teste passa igual
    quando a pergunta morre e o saque de R$ 300 evapora."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, resposta)

    _nada_se_moveu(uid, respostas[-1])
    assert db.get_pending_action(uid) is not None, \
        f"{celula}: a pergunta morreu levando o saque: {respostas[-1]!r}"
    assert "Qual o valor" in respostas[-1], respostas[-1]
    assert "*metade*" not in respostas[-1] and "*resto*" not in respostas[-1], \
        f"{celula}: a palavra do usuário virou alvo: {respostas[-1]!r}"


@pytest.mark.parametrize("resposta,marca", [
    ("fatura", "cart"),
    ("meus cartoes", "cart"),
    # `carro`, não `viagem`: a caixinha da pergunta já existe, e "já existe"
    # não distingue "o comando rodou" de "o comando foi engolido". A fixture é
    # o teto do plano Grátis (uma caixinha), não parte do que se mede aqui.
    ("criar caixinha carro", "carro"),
])
def test_281_c1_comando_sem_quantidade_abandona(uid, sem_teto_de_caixinha,
                                                resposta, marca):
    """C1, o ÚNICO abandono novo. `fatura` sem quantidade nenhuma não pode ser
    resposta de "Qual o valor?" — e, ao contrário do verbo de lançamento, o
    comando RODA sozinho (mostra a fatura, cria a caixinha).

    Era um laço indefinido medido: a pergunta voltava ("Quanto foi no *luz*?")
    e a pendência era RE-ARMADA a cada turno, então nunca expirava.

    E ela morre AVISANDO (#288): na `main` a pergunta sobrevivia ao `fatura`,
    então descartá-la em silêncio deixaria o usuário esperando a resposta de uma
    pergunta que já não existe. O A0 (`saldo`) segue calado — lá o abandono é o
    comportamento de sempre."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, resposta)

    _nada_se_moveu(uid, respostas[-1])
    assert db.get_pending_action(uid) is None, \
        f"a pergunta sobreviveu ao comando: {respostas[-1]!r}"
    assert marca in respostas[-1].lower(), \
        f"o comando não rodou: {respostas[-1]!r}"
    assert "Cancelei a pergunta anterior" in respostas[-1], \
        f"a pergunta morreu em silêncio: {respostas[-1]!r}"


@pytest.mark.parametrize("celula,resposta", [
    ("D4b", "gastei tudo mesmo"),
    ("D5", "esvaziar caixinha"),
    ("D5", "guardei tudo na caixinha viagem"),
    ("D7", "paguei 132 ok"),
    ("D7", "paguei 132 hoje"),
    ("D7", "paguei 132 no debito"),
    ("D8", "gastei 50 no mercado"),
    ("D8", "guardei 100 na caixinha viagem"),
])
def test_281_quantidade_com_cauda_nao_escreve(uid, celula, resposta):
    """D4b, D5, D7 e D8: sobrou cauda depois da quantidade, então a mensagem
    pode ser as duas coisas — e AMBÍGUO NÃO ESCREVE, nem o valor na pergunta
    viva nem o comando novo. O turno acaba com as duas opções na tela.

    É aqui que mora a decisão do dono sobre `gastei tudo mesmo`: perguntar
    custa um turno, esvaziar por engano custa a caixinha inteira."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, resposta)

    _nada_se_moveu(uid, respostas[-1])
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "value_or_command_choice", \
        f"{celula}: o desempate não foi armado: {pend}"
    # A pergunta original não se perdeu: ela viaja DENTRO do payload e aparece
    # na opção 1 (`pending_actions` é uma linha por usuário).
    assert pend["payload"]["clarif"].get("question"), pend
    assert "Qual o valor" in respostas[-1], respostas[-1]
    assert resposta in respostas[-1], respostas[-1]


# ── O desempate: três saídas, e a quarta que desembrulha ─────────────────────
# Zero transições novas — `1` volta ao payload original e resolve com o texto
# guardado, `2` roteia o texto como comando novo, e qualquer outra coisa
# devolve a pergunta para a linha e reentra na tabela com a mensagem NOVA.

@pytest.mark.parametrize("escolha", ["1", "responder"])
def test_281_desempate_1_era_o_valor(uid, escolha):
    """`gastei 50 no mercado` + "era o valor" = o saque de R$ 50 que a pergunta
    pedia. O texto guardado volta a ser lido como resposta."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", escolha)

    assert _saldos(uid)["viagem"] == 250.00, respostas[-1]
    assert round(float(db.get_balance(uid)), 2) == 150.00, respostas[-1]
    assert _despesas(uid) == [], respostas[-1]
    assert db.get_pending_action(uid) is None, "a linha ficou presa"


@pytest.mark.parametrize("escolha", ["2", "registrar"])
def test_281_desempate_2_o_comando_explicito_vence(uid, escolha):
    """O caso do TÍTULO da issue, com os números medidos em produção — um turno
    depois, e só quando o usuário confirma.

    Antes: `📤 Caixinha *viagem*: -R$ 50,00`, conta SOBE 50 e a despesa não
    existe. Depois: despesa de 50, conta CAI 50, caixinha intacta."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", escolha)

    assert _saldos(uid)["viagem"] == 300.00, \
        f"o gasto saiu da caixinha em vez da conta: {respostas[-1]!r}"
    assert round(float(db.get_balance(uid)), 2) == 50.00, \
        "a conta tinha de CAIR 50 (100 -> 50), não subir para 150"
    assert [round(float(d["valor"]), 2) for d in _despesas(uid)] == [50.00], \
        f"o gasto sumiu do extrato: {respostas[-1]!r}"
    # O desempate saiu da linha. O que fica lá é a oferta de recategorização do
    # lançamento novo, que é do fluxo normal de `launches.add`.
    pend = db.get_pending_action(uid)
    assert (pend or {}).get("action_type") != "value_or_command_choice", pend


def test_281_desempate_2_verbo_de_deposito_nao_vira_saque(uid):
    """GUARDEI virando SAQUE — o sentido do dinheiro invertido, que é o outro
    lado do P0. Na `main` (1fff16f) `guardei 100 na caixinha viagem`
    respondendo "Qual o valor?" DEBITAVA a caixinha; aqui ele deposita, depois
    de o usuário dizer que era comando."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "guardei 100 na caixinha viagem", "2")

    assert _saldos(uid)["viagem"] == 400.00, \
        f"o usuário disse GUARDEI e a caixinha caiu: {respostas[-1]!r}"
    assert round(float(db.get_balance(uid)), 2) == 0.00, \
        "conta = 100 do setup - 100 depositados"
    assert _despesas(uid) == [], respostas[-1]


def test_281_desempate_cancela_mata_as_duas(uid):
    """O cancelamento mata o texto pendurado E a pergunta que ele desalojou.

    A palavra oferecida é *cancela*, não *cancelar*, e é medição: com QUALQUER
    pendência determinística de pé, "cancelar" nunca chega ao `route()` — o
    `handle_billing_command` (core/services/billing_commands.py:296) responde
    "🐷 Você tá no plano Free, não tem o que cancelar" porque a guarda dele olha
    o `ai_pending_actions` e não o `pending_actions`. Hole PRÉ-EXISTENTE (vale
    igual para a confirmação de delete que o comentário de lá diz proteger) e
    não consertado neste PR."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", "cancela")

    _nada_se_moveu(uid, respostas[-1])
    assert "ancelado" in respostas[-1], respostas[-1]
    assert db.get_pending_action(uid) is None, \
        f"o cancelamento deixou uma linha viva: {db.get_pending_action(uid)}"


def test_281_desempate_a_palavra_cancelar_nao_perde_nada(uid):
    """O outro lado do hole: quem digitar *cancelar* recebe a resposta do
    billing, e o desempate continua de pé — nada se move e nenhuma opção se
    perde. É o que torna o hole barato o bastante para ficar para outro PR."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", "cancelar")

    _nada_se_moveu(uid, respostas[-1])
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "value_or_command_choice", pend


def test_281_desempate_terceira_coisa_reentra_na_tabela(uid):
    """Uma TERCEIRA mensagem desembrulha o desempate e reentra na tabela com a
    mensagem nova: `132` fecha a quantidade, então é a resposta da pergunta
    original, que voltou para a linha. O texto guardado é descartado."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", "132")

    assert _saldos(uid)["viagem"] == 168.00, respostas[-1]
    assert round(float(db.get_balance(uid)), 2) == 232.00, respostas[-1]
    assert _despesas(uid) == [], respostas[-1]


def test_281_desempate_terceira_coisa_pode_abandonar(uid):
    """A mesma porta, do outro lado: `saldo` é LEITURA (A0), então a pergunta
    que acabou de voltar para a linha é abandonada como sempre foi — o
    desempate não criou estado do qual não se sai."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "gastei 50 no mercado", "saldo")

    _nada_se_moveu(uid, respostas[-1])
    assert "Conta Corrente" in respostas[-1], respostas[-1]
    assert db.get_pending_action(uid) is None, db.get_pending_action(uid)


@pytest.mark.parametrize("resposta,saque", [
    ("100 reais", 100.00),
    ("cem", 100.00),
    ("132,50", 132.50),
    ("R$ 100", 100.00),
    ("30 reais e 50 centavos", 30.50),
    ("tudo", 300.00),
])
def test_281_resposta_compativel_nunca_vira_lancamento(uid, resposta, saque):
    """CONTROLE POSITIVO, e é o que separa este conserto de um que recusa tudo.

    `100 reais`, `2 mil` e `30 reais e 50 centavos` classificam `launches.add`
    0.95 — os mesmos 0.95 de `gastei 50 no mercado`; `cem`, `132,50` e `tudo`
    caem em `out_of_scope 0.0`. Nenhum deles pode virar lançamento.

    MEDIDO, e desde o #288 é o MESMO sinal para os seis: a quantidade é a
    última coisa da mensagem. `132,50`, `R$ 100`, `cem` e `tudo` fecham no
    próprio número/marcador; `100 reais` e `30 reais e 50 centavos` fecham
    porque a UNIDADE não é cauda (`_UNIDADE_DE_VALOR_RE`). É este teste que
    impede o conserto da regra 3 de virar um código que recusa tudo."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, resposta)

    assert _saldos(uid)["viagem"] == round(300.00 - saque, 2), \
        f"a resposta não fechou o saque: {respostas[-1]!r}"
    assert round(float(db.get_balance(uid)), 2) == round(100.00 + saque, 2)
    assert _despesas(uid) == [], \
        f"a resposta à pergunta virou lançamento: {respostas[-1]!r}"


def test_288_cas_do_desempate_falha_e_ninguem_escreve(uid, monkeypatch):
    """O fallback do CAS não pode escrever o que o desenho chamou de ambíguo.

    Quando a linha já é de OUTRA tarefa, o desempate não pode ser armado. A
    saída anterior caía no roteamento normal — medido com o CAS forçado a
    False: `💸 *Despesa registrada*: R$ 50,00 ... ID: #3`, conta em 4950 e a
    pergunta original perdida. O abandono pode cair no roteamento normal (lá o
    texto é um comando que o usuário deu de propósito); isto aqui, não."""
    import core.intent_router as IR

    _pergunta_de_valor(uid)
    real = IR.db.advance_pending_action

    def perde_a_corrida(*args, **kwargs):
        if kwargs.get("new_action_type") == "value_or_command_choice":
            return False
        return real(*args, **kwargs)

    monkeypatch.setattr(IR.db, "advance_pending_action", perde_a_corrida)
    antes = len(db.list_launches(uid, limit=50) or [])

    resposta = _conversa(uid, "gastei 50 no mercado")[-1]

    assert len(db.list_launches(uid, limit=50) or []) == antes, \
        f"o ambíguo virou lançamento no caminho de exceção: {resposta!r}"
    _nada_se_moveu(uid, resposta)


def test_281_resposta_compativel_grande_demais_nao_vira_lancamento(uid):
    """`2 mil` numa caixinha de 300: a resposta é COMPATÍVEL, então o saque é
    recusado pelo saldo — e continua sem virar despesa avulsa."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "2 mil")

    assert "insuficiente" in respostas[-1].lower(), respostas[-1]
    assert _saldos(uid)["viagem"] == 300.00
    assert _despesas(uid) == [], f"virou lançamento: {respostas[-1]!r}"


@pytest.mark.parametrize("texto", ["50 no mercado", "120 de luz",
                                   "100 na caixinha viagem"])
@pytest.mark.parametrize("pergunta", ["nome", "valor"])
def test_288_regra3_quantidade_com_alvo_nunca_escreve(uid, pergunta, texto):
    """A REGRA 3 DO DONO, verbatim, nas DUAS perguntas — e o defeito do #288.

    As três strings são as que ele escreveu; as duas perguntas são as duas que
    a porta 2 protege (NOME e VALOR). Antes do conserto o
    `_STARTS_WITH_VALUE_RE` (D11) devolvia "resolve" antes de o quinto sinal ser
    consultado — a regra não existia no código —, mas o dano era de DOIS tipos:
    4 células escreviam 1 linha em `launches` e 2 (NOME × `50 no mercado` e
    NOME × `120 de luz`) escreviam ZERO e MATAVAM a pendência, engolindo o texto
    como nome de caixinha. Este teste falha nos dois tipos; o comando que remede
    as duas árvores está no comentário da tabela, acima.

    A afirmação de dinheiro é a CONTAGEM de `launches` antes/depois: zero linha
    nova. As outras duas são a pendência (a pergunta original tem de sobreviver,
    dentro do payload do desempate) e o desempate oferecido na tela."""
    _caixinhas_com_saldo(uid, "viagem")
    original = _conversa(uid, "tirar da caixinha viagem")[-1]
    if pergunta == "valor":
        original = _conversa(uid, "viagem")[-1]
        assert "Qual o valor" in original, original
    else:
        assert "Qual caixinha" in original, original
    antes = len(db.list_launches(uid, limit=50) or [])

    resposta = _conversa(uid, texto)[-1]

    assert len(db.list_launches(uid, limit=50) or []) == antes, \
        f"escreveu em `launches`: {resposta!r}"
    _nada_se_moveu(uid, resposta)
    pend = db.get_pending_action(uid)
    assert pend and pend["action_type"] == "value_or_command_choice", \
        f"o desempate não foi armado: {pend}"
    assert pend["payload"]["clarif"].get("question"), \
        f"a pergunta original se perdeu: {pend}"
    assert "1️⃣" in resposta, \
        f"nenhum desempate na tela: {resposta!r}"


def test_288_desempate_nao_embrulha_texto_com_marcacao(uid):
    """A classe que o #270 fechou, reaberta pelo `_PERGUNTA_DE_DESEMPATE`.

    O molde interpolava o texto CRU do usuário dentro de `*...*`. Medido em
    2026-09-04 com o molde antigo (mutação `"Não sei se *{texto}*"`), pela
    conversa real: `*gastei 50 no *mercado*` na tela e 11 asteriscos na
    mensagem; com `wrap_wa_markup` são 9, e o texto sai inteiro, sem embrulho —
    que é o que este teste afirma.

    TETO DECLARADO, e é a issue #276: 9 é ÍMPAR. `wrap_wa_markup` decide POR
    ARGUMENTO e o WhatsApp pareia POR MENSAGEM, então o `*` solto do usuário
    ainda casa com a marcação PRÓPRIA do molde (`*responder*`, `*registrar*`,
    `*cancela*` e os do `{pergunta}`). Fechar isso é o desenho da #276 e não
    cabe aqui; o que cabe é não ser o bot a ABRIR o par."""
    _caixinhas_com_saldo(uid, "viagem")
    _conversa(uid, "tirar da caixinha viagem", "viagem")

    resposta = _conversa(uid, "gastei 50 no *mercado")[-1]

    assert "gastei 50 no *mercado" in resposta, resposta
    assert "*gastei 50 no *mercado*" not in resposta, \
        f"o bot embrulhou texto que já tem marcação: {resposta!r}"


def test_281_c2_quantidade_grande_no_comando_nao_cria_a_caixinha(uid):
    """C2 pelo outro lado: `criar caixinha 2028` respondendo "Qual o valor?" é
    lido como VALOR (2028 fecha a mensagem), e 2028 > 300 — então o saque é
    recusado pelo saldo e a caixinha `2028` NÃO nasce. É o teto declarado do
    `_resolve_clarification`: caixinha chamada "2028" respondida a uma pergunta
    de valor é lida como valor."""
    _pergunta_de_valor(uid)

    respostas = _conversa(uid, "criar caixinha 2028")

    assert "insuficiente" in respostas[-1].lower(), respostas[-1]
    assert sorted(_saldos(uid)) == ["viagem"], \
        f"o comando rodou em vez de responder: {_saldos(uid)}"
    assert _despesas(uid) == [], respostas[-1]
