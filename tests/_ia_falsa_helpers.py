"""IA falsa dos testes do WA_IA_PRIMEIRO (`core/services/wa_ia_primeiro.py`).

`openai.OpenAI` trocado por um cliente com rodadas roteirizadas, no molde de
`tests/test_ai_chat_quota_reservation.py`: o runner de verdade roda inteiro
(cota, histórico, tools, banco), só o modelo é falso.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import db


def chamada(nome: str, args: dict, ident: str = "c1"):
    d = {"id": ident, "type": "function",
         "function": {"name": nome, "arguments": json.dumps(args)}}
    return SimpleNamespace(model_dump=lambda: d)


def com_tools(*calls):
    return SimpleNamespace(content=None, tool_calls=list(calls))


def texto(t: str):
    return SimpleNamespace(content=t, tool_calls=[])


def lancamento(valor, alvo="mercado", tipo="despesa", **extra):
    return com_tools(chamada("add_launch", {"tipo": tipo, "valor": valor, "alvo": alvo, **extra}))


class _Registro(list):
    def __init__(self):
        super().__init__()
        self.creates: list[dict] = []


def openai_falso(monkeypatch, *rodadas):
    """Cada `create` consome uma rodada: mensagem pronta, Exception (levanta)
    ou callable(messages) -> mensagem. Sem rodada sobrando, levanta. Devolve
    os kwargs com que o cliente foi criado (timeout, max_retries); os de cada
    `create` (sem as mensagens) ficam em `.creates`."""
    import openai
    fila = list(rodadas)
    clientes = _Registro()

    def create(**kw):
        clientes.creates.append({k: v for k, v in kw.items() if k != "messages"})
        if not fila:
            raise AssertionError("IA falsa chamada além do roteiro")
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        msg = r(kw["messages"]) if callable(r) else r
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    def fabrica(**kw):
        clientes.append(kw)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(openai, "OpenAI", fabrica)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    return clientes


def lancamentos(uid: int) -> list[dict]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select tipo, valor from launches where user_id=%s order by id", (uid,))
        rows = cur.fetchall()
        conn.commit()
    return [{"tipo": r["tipo"], "valor": float(r["valor"])} for r in rows]


def liga_flag(monkeypatch, ids: str | None = None):
    monkeypatch.setenv("WA_IA_PRIMEIRO", "1")
    if ids is None:
        monkeypatch.delenv("WA_IA_PRIMEIRO_USER_IDS", raising=False)
    else:
        monkeypatch.setenv("WA_IA_PRIMEIRO_USER_IDS", ids)


def desliga_flag(monkeypatch):
    monkeypatch.delenv("WA_IA_PRIMEIRO", raising=False)
    monkeypatch.delenv("WA_IA_PRIMEIRO_USER_IDS", raising=False)
