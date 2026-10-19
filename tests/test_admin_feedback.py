"""Tela do dono `/admin/feedback` (PR 4c): acesso, pareamento pergunta x resposta POR
USUÁRIO, filtro do 👎, saída sem identificação, semana em BRT e auditoria.

O banco de teste tem dado de outros testes: tudo aqui acha as próprias linhas pelo id
ou mede por DELTA (antes x depois)."""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import core.feedback_routes as feedback_routes
import frontend.finance_bot_websocket_custom as dashboard
from db import ensure_user, get_conn
from db.connection import close_pool
from db.ai_feedback import resumo_semanal
from token_utils import make_dashboard_token

BRT = timezone(timedelta(hours=-3))


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _admin_client() -> TestClient:
    c = TestClient(dashboard.app, base_url="https://testserver")
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-admin-csrf")
    r = c.post("/admin/auth/login", headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"},
               json={"username": "admin", "password": "secret-admin"})
    assert r.status_code == 200
    return c


@pytest.fixture()
def usuarios():
    uids = []

    def _mk():
        uid = int(uuid.uuid4().int % 10_000_000_000)
        ensure_user(uid)
        uids.append(uid)
        return uid

    yield _mk
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from ai_messages where user_id = any(%s)", (uids,))
        cur.execute("delete from pii_access_log where purpose = 'render_admin_ai_feedback'"
                    " and subject_user_id = any(%s)", (uids,))
        conn.commit()


def _msg(uid, role, content, quando, *, nota=None, motivo=None, texto=None, tool_calls=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into ai_messages (user_id, role, content, created_at, feedback, feedback_motivo,"
            " feedback_texto, tool_calls) values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (uid, role, content, quando, nota, motivo, texto,
             json.dumps(tool_calls) if tool_calls else None),
        )
        mid = cur.fetchone()["id"]
        conn.commit()
    return mid


def _agora():
    return datetime.now(timezone.utc)


@pytest.fixture(params=["relogio_real", "segunda_0002_brt"])
def relogio(request, monkeypatch):
    """Os testes que leem `resumo_semanal(1)` (só a semana corrente) não podem depender do
    relógio: na segunda entre 00:00 e 00:05 BRT, `agora - 5min` cai na semana ANTERIOR. A
    variante `segunda_0002_brt` congela o `_agora()` do teste na segunda desta semana, 00:02 BRT."""
    if request.param == "segunda_0002_brt":
        h = datetime.now(BRT)
        seg = (h - timedelta(days=h.weekday())).replace(hour=0, minute=2, second=0, microsecond=0)
        monkeypatch.setattr(sys.modules[__name__], "_agora", lambda: seg)


def _carimbo():
    """Um instante já passado e DENTRO da semana corrente em BRT (nunca no futuro, nunca na anterior)."""
    agora = _agora().astimezone(BRT)
    segunda = (agora - timedelta(days=agora.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(agora - timedelta(minutes=5), segunda + timedelta(seconds=1))


def _negativos(c, limit=200):
    r = c.get(f"/admin/api/feedback?limit={limit}")
    assert r.status_code == 200, r.text
    return r.json()


def _item(j, mid):
    return next((i for i in j["itens"] if i["id"] == mid), None)


# ── Acesso ─────────────────────────────────────────────────────────────────

def test_api_sem_token_401_e_pagina_303():
    c = TestClient(dashboard.app)
    assert c.get("/admin/api/feedback").status_code == 401
    r = c.get("/admin/feedback", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/admin/login")


def test_usuario_comum_e_jwt_de_outro_tipo_nao_entram(usuarios):
    token = make_dashboard_token(usuarios())
    falso = pyjwt.encode({"sub": "admin", "type": "user",
                          "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                         dashboard.JWT_SECRET, algorithm="HS256")
    for t in (token, falso):
        c = TestClient(dashboard.app, base_url="https://testserver")
        c.cookies.set("admin_auth_token", t)
        assert c.get("/admin/api/feedback").status_code == 401
        assert c.get("/admin/api/feedback", headers={"Authorization": f"Bearer {t}"}).status_code == 401
        assert c.get("/admin/feedback", follow_redirects=False).status_code == 303


def test_admin_nao_configurado_503(monkeypatch):
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "")
    assert TestClient(dashboard.app).get("/admin/api/feedback").status_code == 503


def test_admin_valido_200_sem_cache_e_so_leitura():
    c = _admin_client()
    pagina = c.get("/admin/feedback", follow_redirects=False)
    assert pagina.status_code == 200 and pagina.headers["cache-control"] == "no-store"
    api = c.get("/admin/api/feedback")
    assert api.status_code == 200 and api.headers["cache-control"] == "no-store"
    for m in ("post", "put", "patch", "delete"):
        r = getattr(c, m)("/admin/api/feedback", headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"})
        assert r.status_code == 405, m


@pytest.mark.parametrize("q", ["limit=0", "limit=201", "offset=-1", "limit=x",
                               "offset=1000001", "offset=9223372036854775808"])
def test_limites_invalidos_422(q):
    assert _admin_client().get(f"/admin/api/feedback?{q}").status_code == 422


def test_offset_no_teto_ainda_responde_200():
    assert _admin_client().get("/admin/api/feedback?offset=1000000").status_code == 200


# ── Pareamento pergunta x resposta, filtro, saída ──────────────────────────

def test_pergunta_e_a_do_proprio_dono_com_usuarios_intercalados(usuarios):
    a, b = usuarios(), usuarios()
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    _msg(a, "user", "pergunta de A", t)
    _msg(b, "user", "pergunta de B", t + timedelta(seconds=1))   # a mais recente do banco antes da resposta de A
    ra = _msg(a, "assistant", "resposta A", t + timedelta(seconds=2), nota=-1)
    rb = _msg(b, "assistant", "resposta B", t + timedelta(seconds=3), nota=-1)
    _msg(a, "user", "pergunta nova de A", t + timedelta(seconds=4))  # posterior: não pode parear
    j = _negativos(_admin_client())
    assert _item(j, ra)["pergunta"] == "pergunta de A"
    assert _item(j, rb)["pergunta"] == "pergunta de B"


def test_pergunta_e_a_ultima_do_usuario_numa_conversa_com_ferramenta(usuarios):
    """A ordem real de `db/ai_chat.py`: user -> assistant(tool_calls, content NULL) -> tool ->
    assistant final. A pergunta é a ÚLTIMA `user` ("certa"), não a primeira ("antiga") e não a
    mensagem `tool` que vem logo antes da resposta."""
    a = usuarios()
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    _msg(a, "user", "antiga", t)
    _msg(a, "assistant", "resposta antiga", t + timedelta(seconds=1))
    _msg(a, "user", "certa", t + timedelta(seconds=2))
    _msg(a, "assistant", None, t + timedelta(seconds=3),
         tool_calls=[{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}])
    _msg(a, "tool", '{"ok": true}', t + timedelta(seconds=4))
    final = _msg(a, "assistant", "resposta final", t + timedelta(seconds=5), nota=-1)
    assert _item(_negativos(_admin_client()), final)["pergunta"] == "certa"


def test_so_menos_um_aparece_e_rotulo_do_motivo(usuarios):
    u = usuarios()
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    neg = _msg(u, "assistant", "ruim", t, nota=-1, motivo="outro", texto="<b>errou</b>")
    pos = _msg(u, "assistant", "boa", t + timedelta(seconds=1), nota=1)
    sem = _msg(u, "assistant", "sem nota", t + timedelta(seconds=2))
    j = _negativos(_admin_client())
    assert _item(j, pos) is None and _item(j, sem) is None
    i = _item(j, neg)
    assert (i["motivo"], i["rotulo"], i["texto"]) == ("outro", "Outro", "<b>errou</b>")
    assert j["total"] >= 1


def test_paginacao_total_e_offset(usuarios):
    u = usuarios()
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    ids = [_msg(u, "assistant", f"r{k}", t + timedelta(seconds=k), nota=-1) for k in range(3)]
    c = _admin_client()
    j = c.get("/admin/api/feedback?limit=1&offset=0").json()
    assert len(j["itens"]) == 1 and j["total"] >= 3
    # a página 2 é a próxima mais antiga: limit=200 contém as duas na mesma ordem
    todos = [i["id"] for i in _negativos(c)["itens"]]
    assert todos.index(ids[2]) < todos.index(ids[1]) < todos.index(ids[0])


def test_offset_traz_os_itens_seguintes_sem_o_primeiro(usuarios):
    u = usuarios()
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    for k in range(3):
        _msg(u, "assistant", f"r{k}", t + timedelta(seconds=k), nota=-1)
    c = _admin_client()
    todos = [i["id"] for i in _negativos(c)["itens"]]
    p2 = [i["id"] for i in c.get("/admin/api/feedback?limit=2&offset=1").json()["itens"]]
    assert p2 == todos[1:3] and todos[0] not in p2


def test_total_conta_so_os_negativos_por_diferenca(usuarios):
    u = usuarios()
    c = _admin_client()
    antes = _negativos(c)["total"]
    t = datetime.now(timezone.utc) - timedelta(minutes=5)
    _msg(u, "assistant", "ruim", t, nota=-1)
    _msg(u, "assistant", "boa", t, nota=1)
    _msg(u, "assistant", "sem", t)
    _msg(u, "user", "do usuario", t, nota=-1)   # role errado: não é resposta
    assert _negativos(c)["total"] - antes == 1


def _chaves(no, acc=None):
    acc = set() if acc is None else acc
    if isinstance(no, dict):
        for k, v in no.items():
            acc.add(k)
            _chaves(v, acc)
    elif isinstance(no, list):
        for v in no:
            _chaves(v, acc)
    return acc


def test_saida_sem_identificacao_do_usuario(usuarios):
    u = usuarios()
    _msg(u, "user", "oi", datetime.now(timezone.utc) - timedelta(minutes=2))
    _msg(u, "assistant", "ruim", datetime.now(timezone.utc) - timedelta(minutes=1), nota=-1)
    r = _admin_client().get("/admin/api/feedback")
    chaves = _chaves(r.json())
    assert not chaves & {"user_id", "email", "nome", "name"}, chaves
    assert not any(p in k for k in chaves for p in ("user", "email", "nome", "name", "phone"))
    assert f'"{u}"' not in r.text and f": {u}" not in r.text


# ── Semana em BRT ──────────────────────────────────────────────────────────

@pytest.fixture()
def sessao_utc(monkeypatch):
    """Mede que a query NÃO depende do fuso da sessão: o `align_process_tz` pode deixar a sessão em
    BRT (e então a falta do `at time zone` passa despercebida) ou em outro fuso. Força a sessão em UTC
    (PGTZ=UTC, pool reaberto) e reabre o pool de novo no fim."""
    monkeypatch.setenv("PGTZ", "UTC")
    close_pool()
    yield
    monkeypatch.undo()
    close_pool()


def test_semana_em_brt_domingo_2330_e_segunda_0100_ficam_em_semanas_diferentes(usuarios, sessao_utc):
    u = usuarios()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("show timezone")
        assert cur.fetchone()["TimeZone"] == "UTC"
        conn.commit()
    # 10 anos atrás NÃO: tem de cair nas 12 semanas. Domingo e segunda da semana passada.
    hoje = datetime.now(BRT)
    segunda_atual = (hoje - timedelta(days=hoje.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    seg_passada = segunda_atual - timedelta(days=7)
    dom = seg_passada + timedelta(days=6, hours=23, minutes=30)   # dom 23:30 BRT = seg 02:30 UTC
    seg = segunda_atual + timedelta(hours=1)                      # seg 01:00 BRT = seg 04:00 UTC
    assert dom.astimezone(timezone.utc).date() == seg.astimezone(timezone.utc).date()
    antes = {s["semana"]: s["positivos"] for s in resumo_semanal(4)}
    _msg(u, "assistant", "x", dom, nota=1)
    _msg(u, "assistant", "y", seg, nota=1)
    depois = {s["semana"]: s["positivos"] for s in resumo_semanal(4)}
    assert depois.get(seg_passada.date(), 0) - antes.get(seg_passada.date(), 0) == 1
    assert depois.get(segunda_atual.date(), 0) - antes.get(segunda_atual.date(), 0) == 1


def test_contagens_por_motivo_por_diferenca(usuarios, relogio):
    u = usuarios()
    t = _carimbo()
    sem = lambda: {k: v for s in resumo_semanal(1) for k, v in
                   [("pos", s["positivos"]), ("neg", s["negativos"]), ("sem", s["sem_motivo"]),
                    ("num", s["por_motivo"].get("numero_errado", 0))]}
    antes = sem() or {"pos": 0, "neg": 0, "sem": 0, "num": 0}
    _msg(u, "assistant", "a", t, nota=1)
    _msg(u, "assistant", "b", t, nota=-1, motivo="numero_errado")
    _msg(u, "assistant", "c", t, nota=-1, motivo="numero_errado")
    _msg(u, "assistant", "d", t, nota=-1)
    _msg(u, "user", "e", t)                      # role errado: não conta
    depois = sem()
    assert {k: depois[k] - antes[k] for k in antes} == {"pos": 1, "neg": 3, "sem": 1, "num": 2}


# ── Auditoria ──────────────────────────────────────────────────────────────

def test_auditoria_uma_linha_por_usuario_com_message_ids(usuarios, monkeypatch):
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")
    a, b = usuarios(), usuarios()
    t = datetime.now(timezone.utc) - timedelta(minutes=5)
    a1 = _msg(a, "assistant", "1", t, nota=-1)
    a2 = _msg(a, "assistant", "2", t + timedelta(seconds=1), nota=-1)
    b1 = _msg(b, "assistant", "3", t + timedelta(seconds=2), nota=-1)
    assert _negativos(_admin_client()) is not None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select subject_user_id, actor, field, endpoint, extra from pii_access_log"
                    " where purpose = 'render_admin_ai_feedback' and subject_user_id = any(%s)", ([a, b],))
        linhas = {r["subject_user_id"]: r for r in cur.fetchall()}
        conn.commit()
    assert set(linhas) == {a, b}
    assert sorted(linhas[a]["extra"]["message_ids"]) == sorted([a1, a2])
    assert linhas[b]["extra"]["message_ids"] == [b1]
    assert linhas[a]["actor"] == "admin:admin" and linhas[a]["endpoint"] == "/admin/api/feedback"


def test_privacidade_avisa_que_a_equipe_le_as_avaliacoes():
    p = (Path(__file__).parent.parent / "frontend" / "privacy.html").read_text()
    assert ("Quando você avalia uma resposta do Piggy, a pergunta, a resposta e o seu comentário "
            "podem ser lidos pela nossa equipe para melhorar o Piggy.") in p


def test_admin_linka_a_tela_de_feedback():
    admin = (Path(__file__).parent.parent / "frontend" / "admin-dashboard.html").read_text()
    assert 'href="/admin/feedback"' in admin


# ── Motivo fora de MOTIVOS (não há CHECK no banco) ─────────────────────────

def test_motivo_desconhecido_nao_some_da_tabela_nem_da_lista(usuarios, relogio):
    u = usuarios()
    t = _carimbo()
    antes = {s["semana"]: s for s in resumo_semanal(1)}
    mid = _msg(u, "assistant", "x", t, nota=-1, motivo="motivo_antigo")
    _msg(u, "assistant", "y", t, nota=-1, motivo="numero_errado")
    semana = resumo_semanal(1)[0]
    ant = antes.get(semana["semana"], {"outros_motivos": 0, "negativos": 0})
    assert semana["outros_motivos"] - ant["outros_motivos"] == 1
    assert semana["negativos"] - ant["negativos"] == 2
    assert "motivo_antigo" not in semana["por_motivo"]
    for s in resumo_semanal(12):  # a conta fecha em toda semana
        assert s["negativos"] == sum(s["por_motivo"].values()) + s["outros_motivos"] + s["sem_motivo"]
    i = _item(_negativos(_admin_client()), mid)
    assert (i["motivo"], i["rotulo"]) == ("motivo_antigo", None)


# ── Janela de 12 semanas e role no resumo ──────────────────────────────────

def _semana_de(dt):
    d = dt.astimezone(BRT)
    return (d - timedelta(days=d.weekday())).date()


def test_janela_de_12_semanas_inclui_a_11a_anterior_e_corta_a_12a(usuarios):
    u = usuarios()
    dentro = datetime.now(timezone.utc) - timedelta(weeks=11)
    fora = datetime.now(timezone.utc) - timedelta(weeks=12)
    antes = {s["semana"]: s["positivos"] for s in resumo_semanal(12)}
    _msg(u, "assistant", "a", dentro, nota=1)
    _msg(u, "assistant", "b", fora, nota=1)
    depois = {s["semana"]: s["positivos"] for s in resumo_semanal(12)}
    assert depois.get(_semana_de(dentro), 0) - antes.get(_semana_de(dentro), 0) == 1
    assert _semana_de(fora) not in depois


def test_feedback_em_linha_de_usuario_nao_entra_no_resumo(usuarios):
    u = usuarios()
    t = datetime.now(timezone.utc) - timedelta(minutes=5)
    soma = lambda: tuple(sum(s[k] for s in resumo_semanal(1)) for k in ("positivos", "negativos"))
    antes = soma()
    _msg(u, "user", "a", t, nota=1)
    _msg(u, "user", "b", t, nota=-1)
    assert soma() == antes


# ── Auditoria falha FECHADA ────────────────────────────────────────────────

SEGREDO = "conteudo-que-nao-pode-sair-sem-auditoria"


@pytest.fixture()
def auditoria_ligada(monkeypatch):
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")
    eventos = []

    async def _registra(*args, **kwargs):
        eventos.append((args, kwargs))

    monkeypatch.setattr(admin_dashboard, "log_system_event", _registra)
    return eventos


def _linhas_de(uids):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from pii_access_log where purpose = 'render_admin_ai_feedback'"
                    " and subject_user_id = any(%s)", (uids,))
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def _dois_com_negativo(usuarios):
    a, b = usuarios(), usuarios()
    t = datetime.now(timezone.utc) - timedelta(minutes=5)
    _msg(a, "assistant", SEGREDO + "-a", t, nota=-1)
    _msg(b, "assistant", SEGREDO + "-b", t + timedelta(seconds=1), nota=-1)
    return a, b


def _503_sem_conteudo(r, eventos):
    assert r.status_code == 503 and r.headers["cache-control"] == "no-store"
    assert SEGREDO not in r.text and "itens" not in r.json()
    assert [e[0][1] for e in eventos if e[0][1].startswith("admin_feedback")] == ["admin_feedback_auditoria"]
    assert SEGREDO not in repr(eventos)   # o log também não leva o conteúdo


def test_insert_da_auditoria_falhando_da_503_sem_dados(usuarios, auditoria_ligada, monkeypatch):
    a, b = _dois_com_negativo(usuarios)

    def _quebra(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(feedback_routes, "_conecta", _quebra)
    _503_sem_conteudo(_admin_client().get("/admin/api/feedback"), auditoria_ligada)
    assert _linhas_de([a, b]) == 0


def test_usuario_apagado_entre_a_leitura_e_o_insert_da_503_e_nao_grava_linha_nenhuma(
        usuarios, auditoria_ligada, monkeypatch):
    a, b = _dois_com_negativo(usuarios)
    real = feedback_routes.listar_negativos

    def _le_e_apaga_a(limit, offset):
        pagina = real(limit, offset)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from users where id = %s", (a,))
            conn.commit()
        return pagina

    monkeypatch.setattr(feedback_routes, "listar_negativos", _le_e_apaga_a)
    _503_sem_conteudo(_admin_client().get("/admin/api/feedback"), auditoria_ligada)
    assert _linhas_de([a, b]) == 0   # nem a linha do usuário que ainda existe


def test_caminho_feliz_200_com_as_linhas_gravadas(usuarios, auditoria_ligada):
    a, b = _dois_com_negativo(usuarios)
    r = _admin_client().get("/admin/api/feedback")
    assert r.status_code == 200 and SEGREDO in r.text
    assert _linhas_de([a, b]) == 2
    assert not [e for e in auditoria_ligada if e[0][1].startswith("admin_feedback")]


def test_pagina_vazia_nao_exige_auditoria(auditoria_ligada, monkeypatch):
    monkeypatch.setattr(feedback_routes, "listar_negativos", lambda l, o: {"total": 0, "itens": []})
    monkeypatch.setattr(feedback_routes, "_conecta", lambda *a, **k: 1 / 0)  # tocar no banco quebra
    assert _admin_client().get("/admin/api/feedback").status_code == 200


def test_pii_audit_disabled_continua_desligando_a_auditoria(usuarios, monkeypatch):
    monkeypatch.setenv("PII_AUDIT_DISABLED", "1")
    a, b = _dois_com_negativo(usuarios)
    assert _admin_client().get("/admin/api/feedback").status_code == 200
    assert _linhas_de([a, b]) == 0


# ── O resumo só vem com offset 0 ───────────────────────────────────────────

def test_resumo_so_e_pedido_com_offset_zero(monkeypatch):
    chamadas = []
    real = feedback_routes.resumo_semanal
    monkeypatch.setattr(feedback_routes, "resumo_semanal", lambda *a: chamadas.append(1) or real(*a))
    c = _admin_client()
    assert c.get("/admin/api/feedback?offset=1").json()["semanas"] is None and not chamadas
    assert isinstance(c.get("/admin/api/feedback").json()["semanas"], list) and chamadas == [1]


# ── A auditoria tem teto de tempo (statement_timeout) ──────────────────────

def test_insert_da_auditoria_travado_estoura_o_teto_e_da_503(usuarios, auditoria_ligada, monkeypatch):
    import contextlib
    import os
    import threading
    import time
    import psycopg
    a, _ = _dois_com_negativo(usuarios)
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "200")
    c = _admin_client()
    with contextlib.closing(psycopg.connect(os.environ["DATABASE_URL"], options="-c lock_timeout=5000ms")) as lock:
        lock.execute("lock table pii_access_log in access exclusive mode")
        solta = threading.Timer(4, lock.rollback)   # sem teto, o INSERT esperaria até aqui e daria 200
        solta.start()
        try:
            t0 = time.monotonic()
            r = c.get("/admin/api/feedback")
            gasto = time.monotonic() - t0
        finally:
            solta.cancel()
            solta.join()
            lock.rollback()
    assert r.status_code == 503 and gasto < 3, (r.status_code, gasto)
