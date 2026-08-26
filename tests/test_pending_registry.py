"""O registro de `db/pending.py` é a única lista de "isto é pergunta?".

Antes da #136 as três respostas (cede a linha? suprime a IA? sobrevive a um
áudio?) viviam em três enumerações que ninguém importava da outra. Divergiam, e
esquecer uma era silencioso — foi o que aconteceu com `bill_amount_expected` no
#133, que precisou de um commit de correção só para entrar na segunda lista.

Este arquivo tem duas metades:

1. **a guarda** — varre o código com `ast` atrás de todo tipo que é GRAVADO e
   cobra que ele esteja na tabela. É o que impede a próxima pendência de nascer
   fora dela;
2. **as duas divergências que a unificação encontrou**, medidas ponta a ponta.

Controle negativo (medido): tirando `multi_launch_values` do `suprime_ia`, o
teste da fila falha; tirando o `oferta` de `delete_credit_purchase`, o teste do
botão falha. Os dois positivos (`undo_audio` NÃO suprime a IA; uma pergunta de
pé ainda recusa outra pergunta) passam nas duas versões.
"""
import ast
import pathlib
import uuid

import pytest

import db
from db.pending import _REGISTRO
from core.types import IncomingMessage


_ESCRITORES = {
    "set_pending_action",
    "claim_pending_action",
    "create_pending_action_if_absent",
}
_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_IGNORADOS = {".venv", ".claude", ".git", "tests", "node_modules"}

# O encanamento genérico: `claim_pending_action` repassa o `action_type` que
# RECEBEU para o `create_pending_action_if_absent`. Ali o tipo é uma variável de
# propósito e não há literal para conferir — quem introduz o tipo é o chamador,
# que a varredura pega. Único arquivo isento, e por esse motivo.
_ENCANAMENTO = "db/pending.py"


def _arg_action_type(no: ast.Call) -> ast.expr | None:
    """O `action_type` da chamada, seja posicional ou por palavra-chave.

    `db.set_pending_action(user_id=uid, action_type="x", payload={})` é uma
    chamada válida e escapava da varredura, que só olhava `no.args[1]` — o tipo
    novo ficaria fora do registro, cairia nas três colunas False e o teste que
    existe para pegar isso passaria. Apontado pelo Codex no PR #142.
    """
    if len(no.args) >= 2:
        return no.args[1]
    for kw in no.keywords:
        if kw.arg == "action_type":
            return kw.value
    return None


def _varre_gravacoes() -> tuple[dict[str, set[str]], list[str]]:
    """(tipo → arquivos que o gravam, chamadas sem literal).

    Por `ast`, não por regex: as chamadas são multilinha e um `grep` de uma
    linha só perdia 4 dos tipos.
    """
    achados: dict[str, set[str]] = {}
    dinamicas: list[str] = []
    for py in _RAIZ.rglob("*.py"):
        rel = str(py.relative_to(_RAIZ))
        if _IGNORADOS & set(py.relative_to(_RAIZ).parts):
            continue
        try:
            arvore = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:                                   # pragma: no cover
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            fn = no.func
            nome = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if nome not in _ESCRITORES:
                continue
            alvo = _arg_action_type(no)
            if isinstance(alvo, ast.Constant) and isinstance(alvo.value, str):
                achados.setdefault(alvo.value, set()).add(rel)
            elif rel != _ENCANAMENTO:
                dinamicas.append(f"{rel}:{no.lineno} ({nome})")
    return achados, dinamicas


def _tipos_gravados_pelo_codigo() -> dict[str, set[str]]:
    return _varre_gravacoes()[0]


def test_todo_tipo_gravado_esta_no_registro():
    gravados, dinamicas = _varre_gravacoes()
    assert gravados, "a varredura não achou nenhuma gravação — o walk quebrou"
    assert not dinamicas, (
        "gravação cujo `action_type` não é literal: a varredura não consegue "
        "conferir contra o registro, e um tipo novo passaria em silêncio. Use "
        f"um literal, ou trate o caso aqui de propósito: {dinamicas}"
    )
    faltando = {t: sorted(f) for t, f in gravados.items() if t not in _REGISTRO}
    assert not faltando, (
        "tipo gravado fora do registro de db/pending.py — as três colunas "
        f"cairiam em False sem ninguém decidir: {faltando}"
    )


def test_registro_nao_tem_tipo_morto():
    """O contrário: entrada na tabela que nada grava é lixo que confunde."""
    gravados = set(_tipos_gravados_pelo_codigo())
    # `undo_audio` é gravado pelo handle_incoming e `clarification` pelos
    # handlers; se algum dia um tipo sumir do código, a linha tem que sumir junto.
    orfaos = sorted(set(_REGISTRO) - gravados)
    assert not orfaos, f"tipos no registro que nenhum código grava: {orfaos}"


# ── as duas divergências que a unificação encontrou ─────────────────────────

def _uid_de_whatsapp() -> int:
    """Abaixo de 2 bilhões de propósito.

    O `_normalize_user_id` do `handle_incoming` COMPRIME qualquer id acima disso
    para outro id interno — gravar a pendência no id da fixture `user_id` (que
    sorteia até 10 bilhões) e mandar a mensagem por ele mediria dois usuários
    diferentes, e o teste ficaria verde ou vermelho por motivo nenhum. Mesmo
    formato do `tests/test_bill_amount_pending.py`.
    """
    return int(uuid.uuid4().int % 1_000_000_000)


def _diga(uid, texto):
    import core.handle_incoming as HI

    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="x", attachments=[], external_id="e", raw={})
    assert HI._normalize_user_id(msg) == uid, "o uid do teste seria comprimido"
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


@pytest.fixture
def ia_espia(monkeypatch):
    """Mocka a IA e devolve a lista de mensagens que chegaram nela."""
    import core.services.ai_chat as AC

    vistas: list[str] = []
    monkeypatch.setattr(AC, "chat", lambda uid, text, **kw: vistas.append(text) or "[IA]")
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda u: True)
    return vistas


def test_pergunta_da_fila_nao_e_sequestrada_pela_ia(ia_espia):
    """"quanto foi *aluguel*?" → "1200" tem de chegar ao route(), não à IA.

    `multi_launch_values` estava fora do `suprime_ia`. A resposta é um número
    solto, que classifica como `out_of_scope`, e o fallback de IA roda ANTES do
    `route()` — então a IA do usuário Pro recebia "1200" e o item da fila ficava
    sem valor. É a #132 sobrevivendo para quem paga.
    """
    user_id = _uid_de_whatsapp()
    db.set_pending_action(user_id, "multi_launch_values",
                          {"queue": [{"desc": "aluguel", "tipo": "despesa"}],
                           "platform": "whatsapp"})

    _diga(user_id, "1200")

    assert ia_espia == [], f"a IA sequestrou a resposta da fila: {ia_espia}"
    lancamentos = db.list_launches(user_id, limit=5)
    assert len(lancamentos) == 1, f"o item da fila não foi registrado: {lancamentos}"
    assert float(lancamentos[0]["valor"]) == 1200.0


def test_oferta_do_botao_apagar_nao_bloqueia_uma_pergunta(user_id):
    """`delete_credit_purchase` é consumida no mesmo turno pelo
    `_send_reply_with_optional_buttons` — logo é oferta. Fora da coluna
    `oferta`, uma que ficasse de pé (botão não tocado) travava QUALQUER
    pergunta nova por 10 minutos."""
    db.set_pending_action(user_id, "delete_credit_purchase", {"tx_id": 1})

    assert db.claim_pending_action(
        user_id, "bill_amount_expected", {"bill_id": 7, "bill_name": "Luz"}) is True
    atual = db.get_pending_action(user_id)
    assert atual["action_type"] == "bill_amount_expected"


# ── controles positivos ────────────────────────────────────────────────────

def test_oferta_de_pe_nao_desliga_a_ia(ia_espia):
    """Oferta não é pergunta: com uma de pé, o fallback de IA continua valendo.
    Sem este caso, marcar tudo como `suprime_ia` passaria no grupo."""
    user_id = _uid_de_whatsapp()
    db.set_pending_action(user_id, "undo_audio", {})

    _diga(user_id, "qual a capital da mongolia")

    assert ia_espia, "a oferta de pé desligou a IA do usuário Pro"


def test_pergunta_de_pe_ainda_recusa_outra_pergunta(user_id):
    """O degrau continua existindo: pergunta NÃO é desalojada por pergunta.
    Sem este caso, marcar tudo como `oferta` passaria no grupo."""
    db.set_pending_action(user_id, "clarification", {"intent": "launches.list"})

    assert db.claim_pending_action(
        user_id, "investment_pick", {"nomes": ["cdb"]}) is False
    assert db.get_pending_action(user_id)["action_type"] == "clarification"
