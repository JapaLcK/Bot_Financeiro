"""PL-06 D13: o marcador de insights nunca segura o aviso SSE dos outros usuários.

O `escutar_banco` marca o cache do dono ANTES de avisar (`eventos._Marcador`), numa conexão async própria com
`lock_timeout`/`statement_timeout` curtos e teto no cliente. Aqui o marcador de A fica emperrado (lock de outra
transação, pool síncrono cheio, threadpool cheio, conexão muda, connect pendurado) e o aviso de B tem de chegar dentro
de `TETO`, com o obstáculo preso por `SEGURA`.

Controle negativo (medido no PR): a versão da rodada 1 (pool síncrono via `run_in_threadpool`, `get_conn(timeout=2)`)
deixa os 4 casos vermelhos — B espera o lock inteiro (`SEGURA`), os PoolTimeout em fila, ou uma vaga do threadpool.
Sem o teto do cliente o caso da conexão muda pendura o ouvinte inteiro; sem a pausa, o connect pendurado custa
`connect_timeout` (2 s) por aviso, em série.
Positivo: nos casos de pool e threadpool o marcador de A é gravado mesmo assim (não depende deles).
"""
import asyncio
import os
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import anyio.to_thread
import psycopg
import pytest

import db
from conftest import usuario_pagante
from api.v2 import eventos
from core import ai_patterns
from test_insights_contrato import _orcamento_apertando, _usuario, llm  # noqa: F401
from tests.test_api_v2_eventos_escrita import _sql, barreira, ouvindo

SEGURA = 6.0  # quanto o obstáculo fica preso se ninguém o soltar
TETO = 2.5  # o prazo do marcador é 0,3-0,5 s; folga para a máquina carregada


def _marcador(uid):
    r = _sql("select generated_at from ai_proactive_cache where user_id = %s and kind = 'insights_mudou'", uid)
    return r[0]["generated_at"] if r else None


def _notifica(uid):
    """O que o trigger manda no commit de uma escrita de `uid`, sem passar pelo pool."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("select pg_notify('pb_escrita', %s)", (str(uid),))


async def _chega(avisados, uid, t0, prazo=SEGURA * 3):
    while uid not in avisados:
        assert time.monotonic() - t0 < prazo, "o aviso não chegou"
        await asyncio.sleep(0.01)
    return time.monotonic() - t0


@pytest.mark.parametrize("emperra", ["lock_users", "lock_cache", "pool_cheio", "threadpool_cheio"])
def test_aviso_de_b_chega_com_o_marcador_de_a_emperrado(monkeypatch, capsys, emperra):
    a, b, c = (usuario_pagante("pro") for _ in range(3))
    if emperra == "lock_cache":
        _sql("insert into ai_proactive_cache (user_id, kind, payload) values (%s, 'insights_mudou', '{}')", a)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            solta = threading.Event()
            if emperra.startswith("lock"):  # exclusão/merge/reset de A em curso, sem commit
                x = await asyncio.to_thread(psycopg.connect, os.environ["DATABASE_URL"])
                sql = ("delete from users where id = %s" if emperra == "lock_users"
                       else "delete from ai_proactive_cache where user_id = %s")
                await asyncio.to_thread(x.execute, sql, (a,))
                fim = threading.Thread(target=lambda: (solta.wait(SEGURA), x.rollback(), x.close()))
            elif emperra == "pool_cheio":
                pool = db.connection._get_pool()
                presas = [pool.getconn(timeout=5) for _ in range(pool.max_size)]
                fim = threading.Thread(target=lambda: (solta.wait(SEGURA), [pool.putconn(c) for c in presas]))
            else:  # rotas `def` síncronas ocupando todas as vagas do threadpool do anyio
                lim = anyio.to_thread.current_default_thread_limiter()
                ocupadas = [asyncio.ensure_future(anyio.to_thread.run_sync(solta.wait, SEGURA))
                            for _ in range(int(lim.total_tokens))]
                await asyncio.sleep(0.2)
                fim = None
            if fim:
                fim.start()
            try:
                await asyncio.to_thread(_notifica, a)
                t0 = time.monotonic()
                await asyncio.to_thread(_notifica, b)
                atraso = await _chega(avisados, b, t0)
            finally:
                solta.set()
                if fim:
                    await asyncio.to_thread(fim.join)
                else:
                    await asyncio.gather(*ocupadas)
            await barreira(avisados)
            await asyncio.to_thread(_notifica, c)
            await barreira(avisados)
            return atraso, avisados.count(a)

    atraso, avisos_a = asyncio.run(cena())
    assert atraso < TETO, f"o aviso de B esperou {atraso:.2f} s pelo marcador de A ({emperra})"
    # travado: pulado na hora, e o aviso extra de A sai quando o lock solta e a marca grava
    assert avisos_a == (2 if emperra.startswith("lock") else 1)
    assert _marcador(a) is not None and _marcador(c) is not None
    assert "marcador de insights falhou" not in capsys.readouterr().err  # ninguém falhou nem pausou
    if emperra in ("pool_cheio", "threadpool_cheio"):
        assert _marcador(a) is not None  # positivo: o marcador não depende do pool nem do threadpool


def test_conexao_do_marcador_que_cai_reabre_no_mesmo_aviso(monkeypatch):
    """Conexão ociosa derrubada (provedor, restart do pgbouncer): o aviso seguinte reabre e marca ANTES de sair (sem a
    2ª tentativa, a marca iria no aviso seguinte e o refetch deste acharia o cache válido)."""
    uid = usuario_pagante("pro")
    no_aviso = []

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(_notifica, uid)
            await barreira(avisados)
            antes = _marcador(uid)
            mortas = _sql("select pg_terminate_backend(pid) from pg_stat_activity "
                          "where application_name = 'pb_insights_marca' and datname = current_database()")
            espia = eventos.avisar
            monkeypatch.setattr(eventos, "avisar", lambda u, r: (u == uid and no_aviso.append(_marcador(uid)), espia(u, r)))
            await asyncio.to_thread(_notifica, uid)
            await barreira(avisados)
            return antes, len(mortas)

    antes, mortas = asyncio.run(cena())
    assert antes is not None and mortas == 1
    assert len(no_aviso) == 1 and no_aviso[0] > antes


def test_marcador_nao_volta_no_tempo():
    """Dois ouvintes (deploy com duas réplicas): o que grava depois com um `now()` mais velho não recua a marca."""
    uid = usuario_pagante("pro")
    _sql("insert into ai_proactive_cache (user_id, kind, payload, generated_at) "
         "values (%s, 'insights_mudou', '{}', now() + interval '1 hour')", uid)
    futuro = _marcador(uid)

    async def marca():
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            await ai_patterns.invalidar_insights(c, [uid])

    asyncio.run(marca())
    assert _marcador(uid) == futuro


class _Proxy:
    """TCP até o Postgres; `congela()` emudece as conexões abertas para sempre, sem fechar nada (NAT que esqueceu o
    mapeamento: sem RST), e segura as novas até `vivo.set()`. O servidor nunca vê a query: nenhum prazo DELE corta."""

    def __init__(self, alvo):
        self.vivo, self.fechado, self.socks, self.mudos, self.atraso = threading.Event(), False, [], set(), 0.0
        self.vivo.set()
        self.s = socket.create_server(("127.0.0.1", 0))
        self.porta = self.s.getsockname()[1]
        threading.Thread(target=self._aceita, args=(alvo,), daemon=True).start()

    def _aceita(self, alvo):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            u = socket.create_connection(alvo)
            self.socks += [c, u]
            for de, para, resposta in ((c, u, False), (u, c, True)):
                threading.Thread(target=self._bombeia, args=(de, para, resposta), daemon=True).start()

    def _bombeia(self, de, para, resposta):
        n = 0
        try:
            while d := de.recv(65536):
                if de in self.mudos:
                    continue  # o byte some: nem chega, nem volta erro
                self.vivo.wait()
                n += 1
                if resposta and n > 2:  # `atraso`: o connect é rápido, a resposta do upsert é que demora
                    time.sleep(self.atraso)
                if self.fechado:
                    return
                para.sendall(d)
        except OSError:
            pass

    def congela(self):
        self.mudos = set(self.socks)
        self.vivo.clear()

    def fecha(self):
        self.fechado = True
        self.vivo.set()
        for x in (self.s, *self.socks):
            x.close()


def _marcador_por(monkeypatch, host, porta):
    """Só as conexões NOVAS do ouvinte (a do marcador; o LISTEN já está aberto) vão para `host:porta`."""
    p = urlsplit(os.environ["DATABASE_URL"])
    url = p._replace(netloc=p.netloc.replace(f"{p.hostname}:{p.port}", f"{host}:{porta}")).geturl()
    assert url != os.environ["DATABASE_URL"]
    monkeypatch.setattr(eventos, "os", SimpleNamespace(environ={**os.environ, "DATABASE_URL": url}))


def test_conexao_muda_do_marcador_nao_pendura_o_ouvinte(monkeypatch):
    """B paga o teto do upsert e pausa; C passa na pausa."""
    p = urlsplit(os.environ["DATABASE_URL"])
    a, b, c = (usuario_pagante("pro") for _ in range(3))
    px = _Proxy((p.hostname, p.port))

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", px.porta)
            await asyncio.to_thread(_notifica, a)
            await barreira(avisados)
            marcou = _marcador(a) is not None
            px.congela()
            t0 = time.monotonic()
            await asyncio.to_thread(_notifica, b)
            atraso_b = await _chega(avisados, b, t0)
            t0 = time.monotonic()
            await asyncio.to_thread(_notifica, c)
            atraso_c = await _chega(avisados, c, t0)
            largadas = [t for t in asyncio.all_tasks() if not t.done() and "invalidar_insights" in repr(t.get_coro())]
            return marcou, atraso_b, atraso_c, largadas

    try:
        marcou, atraso_b, atraso_c, largadas = asyncio.run(cena())
    finally:
        px.fecha()
    assert marcou  # o marcador abriu pelo proxy: o congelamento acerta a conexão dele
    assert atraso_b < TETO, f"o aviso de B esperou {atraso_b:.2f} s pela conexão muda"
    assert atraso_c < TETO  # e o ouvinte segue: na pausa o próximo não paga de novo
    assert largadas == []  # a tarefa largada terminou (fechar antes de cancelar; senão o cancel espera a rede 5 s)


def test_connect_pendurado_do_marcador_nao_custa_por_aviso(monkeypatch):
    surdo = socket.create_server(("127.0.0.1", 0), backlog=50)  # aceita o TCP (backlog) e nunca responde
    us = [usuario_pagante("pro") for _ in range(4)]

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", surdo.getsockname()[1])
            t0 = time.monotonic()
            for u in us:
                await asyncio.to_thread(_notifica, u)
            return await _chega(avisados, us[-1], t0)

    try:
        atraso = asyncio.run(cena())
    finally:
        surdo.close()
    # o 1º paga o `connect_timeout` (2 s); os outros passam na pausa. Sem a pausa: 4 × 2 s.
    assert atraso < 4.0, f"4 avisos com o connect pendurado: o último chegou em {atraso:.2f} s"


async def _ate_n_avisos(avisados, uid, n, prazo=SEGURA * 3):
    t0 = time.monotonic()
    while avisados.count(uid) < n:
        assert time.monotonic() - t0 < prazo, f"{avisados.count(uid)} aviso(s), esperava {n}"
        await asyncio.sleep(0.01)


def _porta_fechada():
    s = socket.create_server(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()  # connect recusado na hora ("too many clients", banco reiniciando)
    return porta


@pytest.mark.parametrize("volta_por", ["aviso", "batimento"])
def test_marca_pendente_da_pausa_e_gravada_quando_a_pausa_acaba(monkeypatch, volta_por):
    """P × aviso guarda o uid; fim da pausa: o próximo aviso (ou o batimento, sem aviso nenhum) marca todos."""
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 0.5)
    if volta_por == "batimento":
        monkeypatch.setattr(eventos, "BATIMENTO_S", 0.3)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", _porta_fechada())
            await asyncio.to_thread(_notifica, a)
            await _chega(avisados, a, time.monotonic())
            await barreira(avisados)  # P × aviso: a sentinela também espera a pausa
            na_pausa = _marcador(a)
            monkeypatch.setattr(eventos, "os", os)  # o banco volta
            await asyncio.sleep(1.5)  # passa a pausa (e, no caso do batimento, alguns batimentos)
            if volta_por == "aviso":
                await asyncio.to_thread(_notifica, b)
                await barreira(avisados)
            return na_pausa, _marcador(a), _marcador(b)

    na_pausa, marca_a, marca_b = asyncio.run(cena())
    assert na_pausa is None  # o connect recusou: A ficou pendente
    assert marca_a is not None  # a marca de A não se perdeu na pausa
    assert (marca_b is not None) == (volta_por == "aviso")


def test_soluco_de_rede_nao_perde_a_escrita_seguinte(monkeypatch, llm):
    """Ra3 do Tester: 1,2 s de rede muda no marcador e, na pausa, um gasto de C que leva o orçamento a 110 %. Espera o
    aviso extra de C em vez de um tempo fixo: a folga não depende da máquina."""
    p = urlsplit(os.environ["DATABASE_URL"])
    a, c = usuario_pagante("pro"), _usuario("pro")
    _orcamento_apertando(c)  # 90 %
    assert [i["severity"] for i in ai_patterns.generate_ai_insights(c)] == ["warning"]
    px = _Proxy((p.hostname, p.port))
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 1.0)
    monkeypatch.setattr(eventos, "BATIMENTO_S", 0.3)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", px.porta)
            await asyncio.to_thread(_notifica, a)
            await barreira(avisados)
            antes = _marcador(a)
            px.congela()
            threading.Timer(1.2, px.vivo.set).start()
            await asyncio.to_thread(_notifica, a)  # este upsert estoura o teto: A fica pendente, marcador pausado
            await barreira(avisados)
            await asyncio.to_thread(db.add_launch_and_update_balance, c, "despesa", 20, "Mercado X", None,
                                    categoria="mercado")  # 110 %
            await _ate_n_avisos(avisados, c, 2)
            return antes, _marcador(a), await asyncio.to_thread(ai_patterns.generate_ai_insights, c)

    try:
        antes, marca_a, depois = asyncio.run(cena())
    finally:
        px.fecha()
    assert [i["severity"] for i in depois] == ["critical"], "cache velho: a escrita de C caiu numa pausa"
    assert marca_a > antes  # a escrita de A do soluço não se perdeu: foi no lote seguinte
    assert llm == []


def test_marca_antes_de_avisar(monkeypatch):
    """No instante do aviso do dono o marcador dele já está gravado: o refetch do SSE nunca acha o cache válido."""
    uid = usuario_pagante("pro")
    no_aviso = []

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            espia = eventos.avisar

            def registra(u, recurso):
                if u == uid:
                    no_aviso.append(_marcador(uid))
                espia(u, recurso)
            monkeypatch.setattr(eventos, "avisar", registra)
            await asyncio.to_thread(_notifica, uid)
            await barreira(avisados)

    asyncio.run(cena())
    assert len(no_aviso) == 1 and no_aviso[0] is not None


def test_marca_atrasada_avisa_o_painel_depois_de_gravar(monkeypatch, llm):
    """O refetch do 1º aviso devolve o velho (marcador fora); a marca grava depois e sai um 2º aviso, cujo refetch traz o
    novo. No instante desse aviso a marca já está gravada. Depois disso, os batimentos não repetem."""
    c = _usuario("pro")
    _orcamento_apertando(c)  # 90 %
    assert [i["severity"] for i in ai_patterns.generate_ai_insights(c)] == ["warning"]
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 0.5)
    monkeypatch.setattr(eventos, "BATIMENTO_S", 0.3)
    no_aviso = []

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            espia = eventos.avisar
            monkeypatch.setattr(eventos, "avisar", lambda u, r: (u == c and no_aviso.append(_marcador(c)), espia(u, r)))
            _marcador_por(monkeypatch, "127.0.0.1", _porta_fechada())
            await asyncio.to_thread(db.add_launch_and_update_balance, c, "despesa", 20, "Mercado X", None,
                                    categoria="mercado")  # 110 %
            await _ate_n_avisos(avisados, c, 1)
            velho = await asyncio.to_thread(ai_patterns.generate_ai_insights, c)
            monkeypatch.setattr(eventos, "os", os)  # o banco volta
            await _ate_n_avisos(avisados, c, 2)
            novo = await asyncio.to_thread(ai_patterns.generate_ai_insights, c)
            await asyncio.sleep(1.0)  # alguns batimentos
            return velho, novo, avisados.count(c)

    velho, novo, avisos = asyncio.run(cena())
    assert [i["severity"] for i in velho] == ["warning"]  # sem a marca, o refetch do 1º aviso é o velho
    assert [i["severity"] for i in novo] == ["critical"]
    assert no_aviso[0] is None and no_aviso[1] is not None  # o aviso extra saiu DEPOIS de gravar
    assert avisos == 2
    assert llm == []


def _avisos(monkeypatch):
    avisos = []
    monkeypatch.setattr(eventos, "avisar", lambda uid, recurso: avisos.append(uid))
    return avisos


def test_gravacao_falha_nao_avisa_e_mantem_pendente(monkeypatch, capsys):
    a = usuario_pagante("pro")
    avisos, real = _avisos(monkeypatch), ai_patterns.invalidar_insights
    m = eventos._Marcador()
    m.pendentes = {a: None}

    async def quebrado(*x):
        raise RuntimeError("banco recusou")

    async def cena():
        async with m:
            monkeypatch.setattr(ai_patterns, "invalidar_insights", quebrado)
            await m.marcar()
            na_falha = list(avisos), set(m.pendentes), m.pausa_ate > time.monotonic()
            monkeypatch.setattr(ai_patterns, "invalidar_insights", real)
            m.pausa_ate = 0.0
            await m.marcar()
            return na_falha

    assert asyncio.run(cena()) == ([], {a}, True)
    assert avisos == [a] and not m.pendentes  # positivo: gravou, avisou
    assert "RuntimeError" in capsys.readouterr().err


def test_conexao_recem_aberta_que_cai_pausa_e_mantem_pendente(monkeypatch):
    """Diferente da conexão antiga caída (reabre 1x): recém-aberta e caída é o banco com problema, não insiste."""
    a = usuario_pagante("pro")
    avisos, chamadas = _avisos(monkeypatch), []
    m = eventos._Marcador()

    async def cai(conn, uids):
        chamadas.append(uids)
        await conn.close()
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(ai_patterns, "invalidar_insights", cai)

    async def cena():
        async with m:
            await m.marcar(a)

    asyncio.run(cena())
    assert chamadas == [[a]] and avisos == [] and list(m.pendentes) == [a]
    assert m.pausa_ate > time.monotonic()


def test_uid_corrente_sem_aviso_duplicado(monkeypatch):
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    avisos = _avisos(monkeypatch)
    m = eventos._Marcador()

    async def cena():
        async with m:
            m.pendentes = {a: None}
            await m.marcar(b)  # A atrasado ganha o extra; B é o corrente: o `avisar` normal vem depois
            m.pendentes = {a: None}
            await m.marcar(a)  # A atrasado E corrente: o normal cobre

    asyncio.run(cena())
    assert avisos == [a]
    assert _marcador(a) is not None and _marcador(b) is not None


def test_reconexao_do_listen_zera_a_pausa(monkeypatch):
    """Pausa bem maior que o teste: só a reconexão a tira; o pendente é marcado no batimento seguinte, com aviso extra."""
    a = usuario_pagante("pro")
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 600.0)
    monkeypatch.setattr(eventos, "BATIMENTO_S", 0.3)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", _porta_fechada())
            await asyncio.to_thread(_notifica, a)
            await _ate_n_avisos(avisados, a, 1)
            monkeypatch.setattr(eventos, "os", os)  # o banco volta
            await asyncio.sleep(0.5)
            na_pausa = _marcador(a)  # alguns batimentos e nada: é a pausa, não a falta de ocasião
            _sql("select pg_terminate_backend(pid) from pg_stat_activity "
                 "where application_name = 'pb_escrita_listen' and datname = current_database()")
            await _ate_n_avisos(avisados, a, 2)
            return na_pausa, _marcador(a)

    na_pausa, depois = asyncio.run(cena())
    assert na_pausa is None and depois is not None


def test_banco_lento_nao_custa_teto_por_aviso(monkeypatch):
    """R4b2 do Tester: conexão de pé e resposta mais lenta que o teto. O 1º aviso paga o teto e pausa; os demais passam."""
    p = urlsplit(os.environ["DATABASE_URL"])
    a, *us = (usuario_pagante("pro") for _ in range(6))
    px = _Proxy((p.hostname, p.port))

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            _marcador_por(monkeypatch, "127.0.0.1", px.porta)
            await asyncio.to_thread(_notifica, a)
            await barreira(avisados)
            px.atraso = eventos.TETO_MARCADOR_S * 1.2
            t0 = time.monotonic()
            for u in us:
                await asyncio.to_thread(_notifica, u)
            return await _chega(avisados, us[-1], t0)

    try:
        atraso = asyncio.run(cena())
    finally:
        px.fecha()
    assert atraso < 3.5, f"5 avisos com banco lento: o último chegou em {atraso:.2f} s (N × teto seria ~5 s)"


@pytest.mark.parametrize("trava", ["users", "cache"])
def test_lote_com_usuario_travado_marca_os_demais_e_preserva_ele(monkeypatch, capsys, trava):
    """Exclusão/merge (`users`) ou reset de conta (o próprio marcador) em curso: B e C gravam, A fica pendente sem custar
    espera e, solto o lock, grava na próxima marcação com o aviso dele."""
    a, b, c = (usuario_pagante("pro") for _ in range(3))
    _sql("insert into ai_proactive_cache (user_id, kind, payload, generated_at) "
         "values (%s, 'insights_mudou', '{}', now() - interval '1 hour')", a)
    antes = _marcador(a)
    avisos = _avisos(monkeypatch)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys([a, b, c])
    x = psycopg.connect(os.environ["DATABASE_URL"])
    x.execute("delete from users where id = %s" if trava == "users"
              else "delete from ai_proactive_cache where user_id = %s", (a,))

    async def cena():
        async with m:
            await m.marcar()
            travado = set(m.pendentes), sorted(avisos)
            t0 = time.monotonic()
            await m.marcar()  # o batimento com A ainda preso
            custo = time.monotonic() - t0
            x.rollback()
            await m.marcar()
            return travado, custo

    try:
        (pendentes, avisados), custo = asyncio.run(cena())
    finally:
        x.close()
    assert pendentes == {a} and avisados == sorted([b, c])
    assert _marcador(b) is not None and _marcador(c) is not None
    assert custo < eventos.TETO_MARCADOR_S  # `skip locked` não espera
    assert not m.pendentes and avisos.count(a) == 1 and _marcador(a) > antes
    assert "marcador de insights falhou" not in capsys.readouterr().err


def test_conta_excluida_sai_dos_pendentes(monkeypatch, capsys):
    from db.privacy import delete_user_data
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    avisos = _avisos(monkeypatch)
    delete_user_data(a)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys([a, b])

    async def cena():
        async with m:
            await m.marcar()

    asyncio.run(cena())
    assert not m.pendentes and avisos == [b]
    assert _marcador(a) is None
    assert "marcador de insights falhou" not in capsys.readouterr().err


def test_lock_que_nao_da_para_pular_cai_no_prazo_do_servidor(monkeypatch, capsys):
    """INSERT não comitado da mesma chave: `skip locked` não pula. Quem corta é o `lock_timeout`, não o teto do cliente,
    e a marca fica pendente até a transação sair."""
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    monkeypatch.setattr(eventos, "PAUSA_MARCADOR_S", 0.2)
    x = psycopg.connect(os.environ["DATABASE_URL"])
    x.execute("insert into ai_proactive_cache (user_id, kind, payload) values (%s, 'insights_mudou', '{}')", (a,))

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(_notifica, a)
            t0 = time.monotonic()
            await asyncio.to_thread(_notifica, b)
            atraso = await _chega(avisados, b, t0)
            x.rollback()
            await asyncio.sleep(0.3)
            await barreira(avisados)
            return atraso

    try:
        atraso = asyncio.run(cena())
    finally:
        x.close()
    err = capsys.readouterr().err
    assert atraso < TETO
    assert "LockNotAvailable" in err and "TimeoutError" not in err, err
    assert _marcador(a) is not None and _marcador(b) is not None


def _marcar_ate_esvaziar(m, limite):
    async def cena():
        n = 0
        async with m:
            while m.pendentes and n < limite:
                await m.marcar()
                n += 1
        return n
    return asyncio.run(cena())


def test_backlog_maior_que_o_lote_drena_em_partes_sem_descartar(monkeypatch):
    us = [usuario_pagante("pro") for _ in range(7)]
    monkeypatch.setattr(eventos, "LOTE_MARCADOR", 3)
    avisos = _avisos(monkeypatch)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys(us)
    assert _marcar_ate_esvaziar(m, 10) == 3  # 3 + 3 + 1
    assert sorted(avisos) == sorted(us)  # exatamente 1 aviso extra cada, ninguém descartado
    assert all(_marcador(u) is not None for u in us)


@pytest.mark.parametrize("ja_pendente", [False, True])
def test_uid_corrente_entra_no_lote_com_a_fila_cheia(monkeypatch, ja_pendente):
    c, *fila = (usuario_pagante("pro") for _ in range(6))
    monkeypatch.setattr(eventos, "LOTE_MARCADOR", 3)
    avisos = _avisos(monkeypatch)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys(fila + [c] * ja_pendente)

    async def cena():
        async with m:
            await m.marcar(c)

    asyncio.run(cena())
    assert _marcador(c) is not None and c not in m.pendentes  # marcado antes do `avisar` dele
    assert sorted(avisos) == sorted(fila[:2])  # o resto do lote; o corrente não ganha extra
    assert sorted(m.pendentes) == sorted(fila[2:])


def test_travados_nao_matam_de_fome_os_demais(monkeypatch):
    """Os travados têm os menores ids e chegam primeiro: ocupam o lote inteiro se a fila não os mandar para o fim."""
    a, b, *livres = sorted(usuario_pagante("pro") for _ in range(5))
    monkeypatch.setattr(eventos, "LOTE_MARCADOR", 2)
    _avisos(monkeypatch)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys([a, b, *livres])
    x = psycopg.connect(os.environ["DATABASE_URL"])
    for u in (a, b):
        x.execute("delete from users where id = %s", (u,))

    async def cena():
        async with m:
            for _ in range(4):
                await m.marcar()

    try:
        asyncio.run(cena())
    finally:
        x.rollback()
        x.close()
    assert all(_marcador(u) is not None for u in livres)
    assert sorted(m.pendentes) == [a, b]  # preservados


def test_lote_que_falha_preserva_todos_na_ordem(monkeypatch):
    us = [usuario_pagante("pro") for _ in range(5)]
    monkeypatch.setattr(eventos, "LOTE_MARCADOR", 2)
    avisos = _avisos(monkeypatch)
    m = eventos._Marcador()
    m.pendentes = dict.fromkeys(us)

    async def quebrado(*x):
        raise RuntimeError("banco recusou")

    monkeypatch.setattr(ai_patterns, "invalidar_insights", quebrado)

    async def cena():
        async with m:
            await m.marcar()

    asyncio.run(cena())
    assert list(m.pendentes) == us and avisos == []


def _nota(payload):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("select pg_notify('pb_escrita', %s)", (payload,))


def test_payload_fora_de_bigint_nao_envenena_o_lote(monkeypatch):
    b = usuario_pagante("pro")

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(_nota, str(2**70))
            await asyncio.to_thread(_notifica, b)
            await barreira(avisados)

    asyncio.run(cena())
    assert _marcador(b) is not None


def test_marcador_preso_com_o_cache_do_mesmo_usuario_livre_e_pulado(monkeypatch):
    """O `livre` filtra por `kind`: a linha do cache de A, livre, não pode fazer o INSERT esbarrar no marcador preso."""
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    _sql("insert into ai_proactive_cache (user_id, kind, payload) values (%s, 'insights_mudou', '{}'), (%s, %s, '{}')",
         a, a, ai_patterns._cache_kind("insights"))
    x = psycopg.connect(os.environ["DATABASE_URL"])
    x.execute("select 1 from ai_proactive_cache where user_id = %s and kind = 'insights_mudou' for update", (a,))

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(_notifica, a)
            await asyncio.to_thread(_notifica, b)
            await barreira(avisados)

    try:
        asyncio.run(cena())
    finally:
        x.rollback()
        x.close()
    assert _marcador(b) is not None
