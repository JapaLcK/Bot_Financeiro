"""Apoio dos testes do chat IA em streaming: OpenAI falsa que VALIDA o histórico
(todo `tool_calls` com seus `tool`, como o servidor da OpenAI), contas pelo
TestClient e o leitor de linhas do histórico."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import openai

import db
import db_support
from core.services.ai_chat import streaming
from test_quiz_conta import _conta_quiz, _email, _navegador
from test_senha_obrigatoria import _com_senha


def rodada(texto=None, *chamadas, ao_fim=None, erro=None, a_cada_chunk=None):
    return SimpleNamespace(texto=texto, chamadas=chamadas, ao_fim=ao_fim, erro=erro,
                           a_cada_chunk=a_cada_chunk)


def _pares(messages):
    faltam = []
    for m in messages:
        if m["role"] == "tool":
            assert faltam and m["tool_call_id"] == faltam.pop(0), "tool sem tool_calls"
        else:
            assert not faltam, "tool_calls órfão no histórico"
            faltam = [tc["id"] for tc in m.get("tool_calls") or []]
    assert not faltam, "tool_calls órfão no fim do histórico"


class Fluxo:
    def __init__(self, chunks, ao_fim, pausa=0, a_cada_chunk=None):
        self.chunks, self.ao_fim, self.pausa = iter(chunks), ao_fim, pausa
        self.a_cada_chunk, self.lidos, self.fechado = a_cada_chunk, 0, False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            time.sleep(self.pausa)
            c = next(self.chunks)
        except StopIteration:
            if self.ao_fim:
                self.ao_fim()
            raise
        self.lidos += 1
        if self.a_cada_chunk:
            self.a_cada_chunk(self.lidos)
        return c

    def close(self):
        self.fechado = True


def _chunk(content=None, tcs=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tcs))])


def _tc(i, ident=None, nome=None, args=""):
    return SimpleNamespace(index=i, id=ident, function=SimpleNamespace(name=nome, arguments=args))


def _chunks(r):
    out = []
    if r.texto:
        passo = max(1, len(r.texto) // 10)
        out += [_chunk(r.texto[i:i + passo]) for i in range(0, len(r.texto), passo)]
    for i, (nome, args) in enumerate(r.chamadas):
        js = json.dumps(args)
        out += [_chunk(tcs=[_tc(i, f"c{i}-{id(r) % 9999}", nome, js[:1])]), _chunk(tcs=[_tc(i, args=js[1:])])]
    return out


def openai_falso(monkeypatch, *rodadas, pausa=0, chips=()):
    """Cada `create` consome uma rodada. Devolve `.creates` (kwargs), `.fluxos`,
    `.clientes` (kwargs do construtor) e `.chips`: a chamada com `response_format`
    (os chips) vai para a fila `chips` (cada item: JSON str, ou exceção a levantar)
    e não conta em `.creates`."""
    fila, fila_chips = list(rodadas), list(chips)
    reg = SimpleNamespace(creates=[], fluxos=[], clientes=[], chips=[])

    def create(**kw):
        if "response_format" in kw:
            reg.chips.append(kw)
            assert fila_chips, "chips chamados além do roteiro"
            c = fila_chips.pop(0)
            if isinstance(c, BaseException):
                raise c
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=c))])
        reg.creates.append(kw)
        _pares(kw["messages"])
        assert fila, "IA falsa chamada além do roteiro"
        r = fila.pop(0)
        if r.erro:
            raise r.erro
        if kw.get("stream"):
            f = Fluxo(_chunks(r), r.ao_fim, pausa, r.a_cada_chunk)
            reg.fluxos.append(f)
            return f
        chamadas = []
        for i, (nome, args) in enumerate(r.chamadas):
            d = {"id": f"c{i}-{id(r) % 9999}", "type": "function",
                 "function": {"name": nome, "arguments": json.dumps(args)}}
            chamadas.append(SimpleNamespace(model_dump=lambda d=d: d))
        msg = SimpleNamespace(content=r.texto, tool_calls=chamadas)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    cliente = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: (reg.clientes.append(kw), cliente)[1])
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    return reg


def executar(uid, texto, cancela=None, cards=()):
    eventos = []
    cancela = cancela or threading.Event()
    r = streaming.executar(uid, texto, monthly_limit=100, emissor=eventos.append, cancela=cancela, cards=cards)
    return r, eventos, cancela


def linhas(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, role, content, tool_calls from ai_messages where user_id=%s order by id", (uid,))
        rows = cur.fetchall()
        conn.commit()
    return rows


def esgota_a_cota(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set ai_messages_this_month = 100000,"
                    " ai_month_reset_at = now() where user_id = %s", (uid,))
        conn.commit()


def conta():
    client = _navegador()
    uid = int(_conta_quiz(client, _email()).json()["user_id"])
    _com_senha(uid)
    db.mark_plan_selected(uid)
    db.update_user_plan(uid, "pro_max", datetime.now(timezone.utc) + timedelta(days=30))
    db_support.invalidate_auth_user_cache(uid)
    return uid, client


def eventos_sse(resp):
    return [json.loads(l[6:]) for l in resp.text.split("\n\n") if l.startswith("data: ")]
