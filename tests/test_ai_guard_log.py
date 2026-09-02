"""
Guarda de afirmações no modo SÓ LOG, ligada em `_run_tool_loop`.

Os testes entram pelo `_run_tool_loop` — não chamam `_log_unsupported_claims`
direto. Chamar a função nova sem passar pelo caminho alterado deixaria a
FIAÇÃO sem cobertura, que é o defeito que o CLAUDE.md §3 nomeia.

E o cliente falso pede uma tool na primeira volta: a mensagem `role="tool"`
precisa NASCER dentro do turno, como no fluxo real. Uma versão anterior destes
testes a punha no histórico de entrada, e com isso não conseguia distinguir
evidência do turno atual de evidência de turno anterior — que é exatamente o
defeito que o Codex apontou no PR #238.

CLASSE CEGA: o cliente OpenAI é falso, então isto não prova nada sobre a
qualidade da resposta do modelo — só sobre a fiação. Quem exercita o modelo de
verdade é `scripts/whatsapp_qa_vault_harness.py`.
"""
from types import SimpleNamespace

import core.services.ai_chat.runner as runner
from core.services.ai_chat._context import CURRENT_USER_MESSAGE

EVIDENCIA = '{"saldo": 1826.55, "receita": 2000.0}'


class _ToolCall:
    def __init__(self, name="get_balance", args="{}"):
        self._d = {"id": "call_1", "type": "function",
                   "function": {"name": name, "arguments": args}}

    def model_dump(self):
        return dict(self._d)


def _cliente(resposta_final, *, pede_tool=True):
    """1ª volta: o modelo pede uma tool (ou não). 2ª volta: escreve o texto."""
    voltas = []
    if pede_tool:
        voltas.append(SimpleNamespace(content=None, tool_calls=[_ToolCall()]))
    voltas.append(SimpleNamespace(content=resposta_final, tool_calls=None))
    it = iter(voltas)

    def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=next(it))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _prepara(monkeypatch, evidencia=EVIDENCIA):
    """Captura o log e faz a tool devolver `evidencia`."""
    capturado = []
    monkeypatch.setattr("core.observability.log_system_event_sync",
                        lambda *a, **k: capturado.append((a, k)))
    monkeypatch.setattr(runner, "_dispatch_tool", lambda uid, name, args: (evidencia, None))
    return capturado


def _historico(user_id):
    return [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "qual meu saldo"},
    ]


def test_valor_sem_evidencia_de_tool_vira_log(user_id, monkeypatch):
    capturado = _prepara(monkeypatch)
    texto = "🐷 Você tem R$ 999,99 disponível."

    assert runner._run_tool_loop(_cliente(texto), user_id, _historico(user_id)) == texto

    assert len(capturado) == 1
    args, kwargs = capturado[0]
    # `info`, não `warning`: warning entra em `backend_warnings_24h` no painel
    # do admin, lido como "problemas de backend". Este evento tem contador
    # próprio (`ai_claim_unsupported_24h`).
    assert args[0] == "info"
    assert args[1] == "ai_claim_unsupported"
    assert kwargs["user_id"] == user_id
    assert "R$ 999,99" in kwargs["details"]["tokens"]
    assert kwargs["details"]["kinds"] == ["dinheiro"]


def test_valor_que_veio_da_tool_deste_turno_nao_loga(user_id, monkeypatch):
    """Controle positivo: o caminho legítimo continua silencioso. Sem ele, um
    código que logasse SEMPRE passaria no teste de cima."""
    capturado = _prepara(monkeypatch)
    texto = "🐷 Seu saldo é R$ 1.826,55 e sua receita foi R$ 2.000,00."

    assert runner._run_tool_loop(_cliente(texto), user_id, _historico(user_id)) == texto
    assert capturado == []


def test_valor_de_turno_ANTERIOR_nao_conta_como_evidencia(user_id, monkeypatch):
    """Regressão do achado do Codex no PR #238 — e é o cenário da cena 18.

    O histórico traz o limite ANTIGO (100.000) de um turno passado; a tool
    deste turno devolve o NOVO (8.000). A resposta repete o antigo. Contando o
    histórico como evidência, a guarda ficava cega justamente pro dado obsoleto
    que ela nasceu pra pegar.
    """
    capturado = _prepara(monkeypatch, evidencia='{"limite": 8000.00, "usado": 1768.80}')
    historico = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "limite nubank"},
        {"role": "tool", "content": '{"limite": 100000.00}', "name": "get_card_limit"},
        {"role": "assistant", "content": "Limite R$ 100.000,00"},
        {"role": "user", "content": "limite nubank"},
    ]
    texto = "🐷 Seu limite é R$ 100.000,00."

    assert runner._run_tool_loop(_cliente(texto), user_id, historico) == texto

    assert len(capturado) == 1, "valor obsoleto do histórico TEM que ser pego"
    assert "R$ 100.000,00" in capturado[0][1]["details"]["tokens"]


def test_valor_da_mensagem_do_user_conta_como_evidencia(user_id, monkeypatch):
    capturado = _prepara(monkeypatch)
    token = CURRENT_USER_MESSAGE.set("gastei 77,90 na farmacia")
    try:
        assert runner._run_tool_loop(
            _cliente("Anotei R$ 77,90."), user_id, _historico(user_id)) == "Anotei R$ 77,90."
    finally:
        CURRENT_USER_MESSAGE.reset(token)
    assert capturado == []


def test_resposta_sem_numero_nao_loga(user_id, monkeypatch):
    capturado = _prepara(monkeypatch)
    texto = "🐷 Seu maior gasto foi no mercado."
    assert runner._run_tool_loop(_cliente(texto), user_id, _historico(user_id)) == texto
    assert capturado == []


def test_guarda_quebrada_nao_derruba_a_resposta(user_id, monkeypatch):
    """A guarda roda no caminho de TODA resposta de IA em produção. Se ela
    levantar, o user tem que receber a resposta assim mesmo."""
    capturado = _prepara(monkeypatch)

    def explode(*a, **k):
        raise RuntimeError("guarda quebrada de propósito")

    monkeypatch.setattr("core.services.ai_guard.check", explode)
    texto = "🐷 Você tem R$ 999,99 disponível."

    assert runner._run_tool_loop(_cliente(texto), user_id, _historico(user_id)) == texto
    assert capturado == [], "guarda que explodiu não loga evento"


def test_log_que_falha_nao_derruba_a_resposta(user_id, monkeypatch):
    monkeypatch.setattr(runner, "_dispatch_tool", lambda uid, n, a: (EVIDENCIA, None))

    def explode(*a, **k):
        raise RuntimeError("observabilidade fora do ar")

    monkeypatch.setattr("core.observability.log_system_event_sync", explode)
    texto = "🐷 Você tem R$ 999,99 disponível."

    assert runner._run_tool_loop(_cliente(texto), user_id, _historico(user_id)) == texto
