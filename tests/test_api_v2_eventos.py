"""`GET /api/v2/eventos` (SSE), pelo monólito real (mount + middlewares do pai).

Sessão real (`_issue_session_token`) e Postgres real; o stream é lido pelo app
ASGI direto (`_apoio_sse`), porque o `TestClient` trava com stream infinito.
O isolamento entre usuários está em `test_api_v2_isolamento.py`.

Controle positivo de tudo: o aviso chega ao dono. Os negativos: sessão revogada
não recebe (e o stream fecha sozinho), recusa antes do stream no envelope, teto
de streams, e o registro de inscritos não vaza.
"""
import asyncio

import pytest
from psycopg_pool import PoolTimeout

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import libera, sessao_de
from _apoio_sse import Pedido
from api.v2 import app as app_v2, eventos
from api.v2.sessao import usuario_atual
from conftest import promote_to_pro
from core.sessions import revoke_session
from frontend.routes.shared import WWW_AUTHENTICATE_401

PATH = "/api/v2/eventos"
AVISO_OF = b'data: {"recurso":"open_finance"}\n\n'


@pytest.fixture
def dono(monkeypatch, user_id):
    promote_to_pro(user_id)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    libera(monkeypatch, user_id)
    return user_id, sessao_de(user_id)


def pedido(s, **kw):
    return Pedido(dashboard.app, PATH, cookies={dashboard.DASHBOARD_COOKIE_NAME: s["dashboard"]}, **kw)


def test_aviso_chega_ao_dono(dono):
    uid, s = dono

    async def cena():
        p = await pedido(s).abrir()
        eventos.avisar(uid, "open_finance")
        pedaco = await p.ler()
        await p.fechar()
        return p, pedaco

    p, pedaco = asyncio.run(cena())
    assert p.status == 200
    assert p.headers["content-type"].startswith("text/event-stream")
    assert pedaco == AVISO_OF


def test_sessao_revogada_nao_recebe_o_aviso_e_o_stream_fecha(dono):
    uid, s = dono

    async def cena():
        p = await pedido(s).abrir()
        assert revoke_session(uid, s["jti"])
        eventos.avisar(uid, "open_finance")
        return await p.ate_o_fim()

    assert b"data:" not in asyncio.run(cena())
    assert uid not in eventos._inscritos


@pytest.mark.parametrize("cai", ["revogada", "fora_da_chave"])
def test_fecha_sozinho_sem_aviso_na_rechecagem(dono, monkeypatch, cai):
    uid, s = dono
    monkeypatch.setattr(eventos, "RECHECAGEM_S", 0.05)

    async def cena():
        p = await pedido(s).abrir()
        await asyncio.sleep(0.2)
        assert not p.fim, "a rechecagem com a sessão válida não pode fechar"
        if cai == "revogada":
            assert revoke_session(uid, s["jti"])
        else:
            monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
        return await p.ate_o_fim(prazo=2)

    assert asyncio.run(cena()) == b""
    assert uid not in eventos._inscritos


def test_sem_credencial_401_no_envelope_com_www_authenticate():
    async def cena():
        p = await Pedido(dashboard.app, PATH).abrir()
        return p, await p.ate_o_fim()

    p, corpo = asyncio.run(cena())
    assert p.status == 401
    assert b'"code":"unauthenticated"' in corpo
    assert p.headers.get("www-authenticate") == WWW_AUTHENTICATE_401["WWW-Authenticate"]


def test_fora_da_chave_404_dashboard_v2_disabled(dono, monkeypatch):
    uid, s = dono
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")

    async def cena():
        p = await pedido(s).abrir()
        return p, await p.ate_o_fim()

    p, corpo = asyncio.run(cena())
    assert p.status == 404
    assert b'"code":"dashboard_v2_disabled"' in corpo


def test_teto_de_streams_429_e_fechar_um_libera_a_vaga(dono):
    uid, s = dono

    async def cena():
        abertos = [await pedido(s).abrir() for _ in range(eventos.MAX_STREAMS)]
        sexto = await pedido(s).abrir()
        corpo = await sexto.ate_o_fim()
        await abertos.pop().fechar()
        de_novo = await pedido(s).abrir()
        for p in abertos + [de_novo]:
            await p.fechar()
        return [p.status for p in abertos], sexto.status, corpo, de_novo.status

    statuses, sexto, corpo, de_novo = asyncio.run(cena())
    assert statuses == [200] * (eventos.MAX_STREAMS - 1)
    assert sexto == 429 and b'"code":"rate_limited"' in corpo
    assert de_novo == 200
    assert uid not in eventos._inscritos, "o registro vazou inscrito de stream fechado"


def test_teto_vale_com_aberturas_simultaneas(dono):
    uid, s = dono

    async def na_hora():  # sem threadpool: todas chegam ao `_vaga` antes de qualquer stream começar
        return uid

    app_v2.dependency_overrides[usuario_atual] = na_hora
    try:
        async def cena():
            ps = await asyncio.gather(*(pedido(s).abrir() for _ in range(eventos.MAX_STREAMS + 7)))
            recusados = [p for p in ps if p.status != 200]
            corpos = [await p.ate_o_fim() for p in recusados]
            inscritos = len(eventos._inscritos.get(uid, ()))
            abertos = [p for p in ps if p.status == 200]
            eventos.avisar(uid, "open_finance")  # a vaga reservada é a que recebe
            pedacos = [await p.ler() for p in abertos]
            for p in abertos:
                await p.fechar()
            return [p.status for p in ps], corpos, inscritos, pedacos

        statuses, corpos, inscritos, pedacos = asyncio.run(cena())
    finally:
        app_v2.dependency_overrides.pop(usuario_atual, None)
    assert statuses.count(200) == eventos.MAX_STREAMS and statuses.count(429) == 7
    assert all(b'"code":"rate_limited"' in c for c in corpos)
    assert inscritos == eventos.MAX_STREAMS
    assert pedacos == [AVISO_OF] * eventos.MAX_STREAMS
    assert uid not in eventos._inscritos


def test_erro_no_meio_do_stream_registra_uma_vez_e_nao_escapa(dono, monkeypatch):
    import api.v2.erros as erros
    import core.admin_dashboard as admin

    uid, s = dono
    chamadas = []

    async def grava(*args, **kwargs):
        chamadas.append(kwargs)

    def pool_esgotado(request):
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(erros, "log_system_event", grava)
    monkeypatch.setattr(admin, "log_system_event", grava)

    async def cena():
        p = await pedido(s).abrir()
        # Só a rechecagem: a dependência da rota já rodou com o `usuario_atual` real.
        monkeypatch.setattr(eventos, "usuario_atual", pool_esgotado)
        eventos.avisar(uid, "open_finance")
        return await p.ate_o_fim()  # `ate_o_fim` aguarda a tarefa: exceção escapando falha aqui

    assert b"data:" not in asyncio.run(cena())
    assert [(c["details"]["exc_type"], c["details"]["status_code"]) for c in chamadas] == [("PoolTimeout", 503)]
    assert uid not in eventos._inscritos
