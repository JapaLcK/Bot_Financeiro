"""
Guarda de afirmações no modo SÓ LOG, ligada em `_run_tool_loop`.

Os testes entram pelo `_run_tool_loop` — não chamam `_log_unsupported_claims`
direto. Chamar a função nova sem passar pelo caminho alterado deixaria a
FIAÇÃO sem cobertura, que é justamente o defeito que o CLAUDE.md §3 nomeia:
o teste ficaria verde com a linha da guarda removida do loop.

CLASSE CEGA: aqui o cliente OpenAI é falso, então isto não prova nada sobre a
qualidade da resposta real do modelo — só sobre a fiação. Quem exercita o
modelo de verdade é `scripts/whatsapp_qa_vault_harness.py`.
"""
from types import SimpleNamespace

import core.services.ai_chat.runner as runner
from core.services.ai_chat._context import CURRENT_USER_MESSAGE

# A tool devolveu o saldo real do user; qualquer outro valor na resposta é
# afirmação sem evidência.
MENSAGENS = [
    {"role": "system", "content": "prompt"},
    {"role": "user", "content": "qual meu saldo"},
    {"role": "tool", "content": '{"saldo": 1826.55, "receita": 2000.0}', "name": "get_balance"},
]


def _cliente_que_responde(texto):
    """Cliente OpenAI falso que devolve `texto` sem pedir tool nenhuma —
    o caminho em que a resposta é ESCRITA pelo modelo."""
    msg = SimpleNamespace(content=texto, tool_calls=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp))
    )


def _captura_log(monkeypatch):
    capturado = []
    monkeypatch.setattr(
        "core.observability.log_system_event_sync",
        lambda *a, **k: capturado.append((a, k)),
    )
    return capturado


def test_valor_sem_evidencia_de_tool_vira_log(user_id, monkeypatch):
    capturado = _captura_log(monkeypatch)
    texto = "🐷 Você tem R$ 999,99 disponível."

    resp = runner._run_tool_loop(_cliente_que_responde(texto), user_id, list(MENSAGENS))

    assert resp == texto, "modo log NUNCA altera a resposta"
    assert len(capturado) == 1
    args, kwargs = capturado[0]
    assert args[0] == "warning"
    assert args[1] == "ai_claim_unsupported"
    assert kwargs["user_id"] == user_id
    assert "R$ 999,99" in kwargs["details"]["tokens"]
    assert kwargs["details"]["kinds"] == ["dinheiro"]
    assert kwargs["details"]["n_resultados_de_tool"] == 1


def test_valor_que_veio_da_tool_nao_loga(user_id, monkeypatch):
    """Controle positivo: o caminho legítimo continua silencioso. Sem ele, um
    código que logasse SEMPRE passaria no teste de cima."""
    capturado = _captura_log(monkeypatch)
    texto = "🐷 Seu saldo é R$ 1.826,55 e sua receita foi R$ 2.000,00."

    resp = runner._run_tool_loop(_cliente_que_responde(texto), user_id, list(MENSAGENS))

    assert resp == texto
    assert capturado == []


def test_valor_da_mensagem_do_user_conta_como_evidencia(user_id, monkeypatch):
    capturado = _captura_log(monkeypatch)
    token = CURRENT_USER_MESSAGE.set("gastei 77,90 na farmacia")
    try:
        resp = runner._run_tool_loop(
            _cliente_que_responde("Anotei R$ 77,90."), user_id, list(MENSAGENS))
    finally:
        CURRENT_USER_MESSAGE.reset(token)

    assert resp == "Anotei R$ 77,90."
    assert capturado == []


def test_resposta_sem_numero_nao_loga(user_id, monkeypatch):
    capturado = _captura_log(monkeypatch)
    texto = "🐷 Seu maior gasto foi no mercado."

    assert runner._run_tool_loop(
        _cliente_que_responde(texto), user_id, list(MENSAGENS)) == texto
    assert capturado == []


def test_guarda_quebrada_nao_derruba_a_resposta(user_id, monkeypatch):
    """A guarda roda no caminho de TODA resposta de IA em produção. Se ela
    levantar, o user tem que receber a resposta assim mesmo."""
    capturado = _captura_log(monkeypatch)

    def explode(*a, **k):
        raise RuntimeError("guarda quebrada de propósito")

    monkeypatch.setattr("core.services.ai_guard.check", explode)
    texto = "🐷 Você tem R$ 999,99 disponível."

    assert runner._run_tool_loop(
        _cliente_que_responde(texto), user_id, list(MENSAGENS)) == texto
    assert capturado == [], "guarda que explodiu não loga evento"


def test_log_que_falha_nao_derruba_a_resposta(user_id, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("observabilidade fora do ar")

    monkeypatch.setattr("core.observability.log_system_event_sync", explode)
    texto = "🐷 Você tem R$ 999,99 disponível."

    assert runner._run_tool_loop(
        _cliente_que_responde(texto), user_id, list(MENSAGENS)) == texto
