"""Infra compartilhada dos testes do "Testar o Piggy" (WhatsApp): mundo falso
(só o envio `wa_client.*` e o modelo `conversa._chamar_modelo` são falsos),
Postgres real, `process_message` como porta.

Importe `mundo` e `_demo_limpo` no arquivo de teste (a segunda é autouse)."""
import re
import secrets
import uuid

import psycopg
import pytest

import db
import db_support
from _paywall_gate_helpers import cadastro_novo, com_plano
from adapters.whatsapp import wa_client
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundMessage
from core.crypto import hash_pii
from core.services.demo import conversa
from db import demo_funnel as funil
from db.connection import get_conn

GATILHO = "Oi Piggy! Quero testar o PigBank 🐷"


def _q(sql, args=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _um(sql, args=()):
    rows = _q(sql, args)
    return rows[0] if rows else None


def _numero() -> str:
    return f"5511{secrets.randbelow(10**9):09d}"


def _sessao(n: str):
    return _um("select * from demo_sessions where wa_hash = %s order by opened_at desc",
               (funil.wa_hash(n),))


def _impressao() -> dict:
    """Impressão de TODA tabela (menos demo_sessions e system_event_logs) + o
    last_value de toda sequência. Pega insert, delete e UPDATE (count(*) não via
    update) e insert+delete com id gerado (a sequência anda). Teto: insert+delete
    com id explícito, sem sequência, volta à mesma impressão."""
    tabelas = [r["table_name"] for r in _q(
        "select table_name from information_schema.tables"
        " where table_schema = current_schema() and table_type = 'BASE TABLE'")]
    imp = {t: _um("select md5(coalesce(string_agg(x::text, ',' order by x::text), '')) as h"
                  f' from "{t}" x')["h"]
           for t in sorted(tabelas) if t not in ("demo_sessions", "system_event_logs")}
    imp["__sequencias__"] = _q("select sequencename, last_value from pg_sequences"
                               " where schemaname = current_schema() order by 1")
    return imp


def _msg(wa_id, texto, *, raw_extra=None, attachments=None, msg_id=None, tipo="text"):
    raw = {"id": msg_id or f"wamid.{uuid.uuid4().hex}", "type": tipo}
    raw.update(raw_extra or {})
    return InboundMessage(wa_id=wa_id, text=texto, timestamp="1",
                          attachments=attachments or [], raw=raw)


def _manda(n, texto, **kw):
    wr.process_message(_msg(n, texto, **kw))


class Mundo:
    def __init__(self):
        self.saida = []      # (canal, tipo, corpo): canal "demo" = wa_client, "normal" = wa_runtime
        self.listas = []     # kwargs de cada send_interactive_list do demo
        self.chamadas = []   # `messages` de cada chamada ao modelo
        self.falha = False

    def textos(self):
        return [c for k, t, c in self.saida if k == "demo" and t == "text"]

    def demo(self):
        return [s for s in self.saida if s[0] == "demo"]

    def normal(self):
        return [s for s in self.saida if s[0] == "normal"]


@pytest.fixture
def mundo(monkeypatch):
    m = Mundo()

    def modelo(messages):
        m.chamadas.append(messages)
        if m.falha:
            raise RuntimeError("modelo caiu")
        return f"resposta {len(m.chamadas)}"

    def demo(tipo):
        def enviar(*a, **k):
            m.saida.append(("demo", tipo, k.get("body")))
            if tipo == "lista":
                m.listas.append(k)
            return {"messages": [{"id": "x"}]}
        return enviar

    def normal(tipo):
        return lambda *a, **k: m.saida.append(("normal", tipo, k.get("body") or str(a)))

    def boom(*a, **k):
        raise AssertionError("download_media não pode ser chamado no demo")

    monkeypatch.setattr(conversa, "_chamar_modelo", modelo)
    monkeypatch.setattr(wa_client, "send_text", demo("text"))
    monkeypatch.setattr(wa_client, "send_interactive_list", demo("lista"))
    monkeypatch.setattr(wa_client, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wa_client, "download_media", boom)
    monkeypatch.setattr(wr, "download_media", boom)
    for nome in ("send_text", "send_interactive_buttons", "send_interactive_list"):
        monkeypatch.setattr(wr, nome, normal(nome))
    monkeypatch.setattr(wr, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr, "send_welcome", normal("welcome"))
    return m


@pytest.fixture(autouse=True)
def _demo_limpo(monkeypatch):
    # teto alto: linhas de outros testes (24h) não podem lotar o demo daqui
    monkeypatch.setenv("DEMO_DAILY_MAX", "1000000")
    conversa._hist.clear()
    t0 = _um("select clock_timestamp() as t")["t"]
    yield
    _q("delete from demo_sessions where created_at >= %s", (t0,))
    conversa._hist.clear()


def _conta_com_telefone(n: str) -> int:
    uid = cadastro_novo()
    _q("update auth_accounts set phone_e164 = %s, phone_hash = %s where user_id = %s",
       (n, hash_pii(n, kind="phone"), uid))
    db_support.invalidate_auth_user_cache(uid)
    return uid


def _abre(n):
    _manda(n, GATILHO)
    assert _sessao(n) is not None, "o gatilho devia abrir a sessão"


def _liga_por_codigo(n):
    """Número ligado a uma conta paga pelo `vincular CODIGO` (sem telefone que case)."""
    b = com_plano()
    _manda(n, f"vincular {db.create_link_code(b)}")
    return b


@pytest.fixture
def sql(monkeypatch):
    """Todo (SQL, parâmetros) executado (psycopg.Cursor.execute/executemany) a partir daqui, na
    ordem. O que nenhuma impressão de tabela vê (insert+update+delete da mesma
    linha) passa por aqui, e é por aqui que se lê o que o handler de log grava em
    system_event_logs (tabela que nem existe no banco isolado do pytest).
    Use `sql.clear()` para começar a janela e `_escritas(sql)` para ler."""
    vistos, real, real_many = [], psycopg.Cursor.execute, psycopg.Cursor.executemany

    def gravador(self, query, params=None, *a, **k):
        vistos.append((query if isinstance(query, str) else repr(query), params))
        return real(self, query, params, *a, **k)

    def gravador_many(self, query, params_seq, *a, **k):
        params_seq = list(params_seq)  # pode ser gerador: consumir uma vez só
        vistos.append((query if isinstance(query, str) else repr(query), params_seq))
        return real_many(self, query, params_seq, *a, **k)

    monkeypatch.setattr(psycopg.Cursor, "execute", gravador)
    monkeypatch.setattr(psycopg.Cursor, "executemany", gravador_many)
    return vistos


_ESCRITA = re.compile(r"\s*(insert|update|delete|truncate|alter|create|drop)\b", re.I)
_ALVO = re.compile(r"\s*(?:insert\s+into|update|delete\s+from)\s+\"?(\w+)", re.I)


def _escritas(vistos) -> list[str]:
    """Escritas/DDL cujo ALVO não é a demo_sessions nem o log (system_event_logs).
    O alvo é a tabela logo depois do verbo: citar demo_sessions num subselect ou
    comentário não absolve a escrita. Escrita sem alvo legível (truncate, alter...) conta."""
    out = []
    for q, _ in vistos:
        if not _ESCRITA.match(q):
            continue
        m = _ALVO.match(q)
        if not m or m.group(1).lower() not in ("demo_sessions", "system_event_logs"):
            out.append(q)
    return out


def _params_do_log(vistos) -> str:
    """Os parâmetros de tudo que foi gravado em system_event_logs, como texto."""
    return repr([p for q, p in vistos if "system_event_logs" in q])
