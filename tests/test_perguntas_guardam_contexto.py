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

    _conversa(uid, "tirar da caixinha carro", "carro", "retirei 100 da caixinha viagem")

    p = _saldos(uid)
    assert p["viagem"] == 200.00, "não saiu da caixinha que o usuário nomeou"
    assert p["carro"] == 300.00, "saiu da caixinha ERRADA"


def test_alvo_fora_do_catalogo_nao_sobrescreve(uid, sem_teto_de_caixinha):
    """CONTROLE do portão: "explícito" não é o mesmo que "existente". Sem o
    catálogo, `salario` sobrescreveria a caixinha certa e o usuário receberia
    "não encontrada" no lugar de um saque perfeitamente executável."""
    _duas_caixinhas(uid)

    _conversa(uid, "tirar da caixinha carro", "carro", "retirei 100 do salario")

    assert _saldos(uid)["carro"] == 200.00, "o alvo inexistente atropelou o guardado"


def test_nota_do_lancamento_nao_cita_o_alvo_velho(uid, sem_teto_de_caixinha):
    """A descrição é auditoria: extrato, suporte e a IA lendo o histórico depois.
    Gravar "tirar da caixinha carro" num lançamento que debitou `viagem`
    envenena os três."""
    _duas_caixinhas(uid)

    _conversa(uid, "tirar da caixinha carro", "carro", "retirei 100 da caixinha viagem")

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

    _conversa(uid, "esvaziar caixinha", "tira 100 da viagem")

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
    _conversa(uid, "tira 100 da viagem")

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

    _conversa(uid, "tirar da caixinha", "tira 100 da zerar divida")

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

    _conversa(uid, "tirar da caixinha viagem", "tira 100 da minha viagem")

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

    _conversa(uid, "esvaziar caixinha", "tira 100 da viagem")

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
