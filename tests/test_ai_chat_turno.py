"""Um turno do chat IA por usuário por vez (`db/ai_turno.py`).

O cancelamento é cooperativo: depois do "parar" a thread ainda despacha as tools
da rodada. Sem a trava, a mensagem seguinte do usuário (app ou WhatsApp) grava o
`user` entre o `assistant(tool_calls)` e o `tool`, e a OpenAI recusa o histórico
em todo turno futuro. Controle negativo: `test_sem_a_trava_o_historico_envenena`.
"""
import threading
from contextlib import contextmanager

import pytest

import db
from core.services.ai_chat import runner
from _ia_stream_helpers import executar, linhas, openai_falso, rodada, _pares


@contextmanager
def _sem_trava(*_a, **_k):
    yield True


class _Tool:
    """Segura o despacho da 1ª tool até o teste soltar."""
    def __init__(self, monkeypatch):
        self.dentro, self.solta, real = threading.Event(), threading.Event(), runner._dispatch_tool
        self.n = 0

        def dispatch(*a):
            self.n += 1
            if self.n == 1:
                self.dentro.set()
                assert self.solta.wait(10)
            return real(*a)

        monkeypatch.setattr(runner, "_dispatch_tool", dispatch)


def _em_thread(fn, saida):
    def alvo():
        try:
            saida.append(fn())
        except BaseException as e:  # o AssertionError da OpenAI falsa também
            saida.append(e)
    t = threading.Thread(target=alvo)
    t.start()
    return t


def _roteiro(monkeypatch, uid, *, espera_antes_de_soltar=0.4):
    """Turno 1 (stream) parado dentro da tool; turno 2 chega; só então a tool solta."""
    tool = _Tool(monkeypatch)
    cancela = threading.Event()
    openai_falso(monkeypatch, rodada(None, ("get_balance", {})), rodada("Resposta da segunda."),
                 rodada("Resposta da terceira."))
    s1, s2 = [], []
    t1 = _em_thread(lambda: executar(uid, "primeira", cancela)[0], s1)
    assert tool.dentro.wait(10)
    cancela.set()  # o app abortou, mas a thread ainda está na tool
    t2 = _em_thread(lambda: runner.chat(uid, "segunda", monthly_limit=100), s2)
    t2.join(espera_antes_de_soltar)
    tool.solta.set()
    t1.join(20), t2.join(20)
    return s1, s2


def test_o_turno_2_espera_o_1_e_o_historico_fica_em_ordem(monkeypatch, pro_user_id):
    s1, s2 = _roteiro(monkeypatch, pro_user_id)
    assert s1[0].interrompida and s2 == ["Resposta da segunda."], (s1, s2)
    assert [x["role"] for x in linhas(pro_user_id)] == [
        "user", "assistant", "tool", "system", "user", "assistant"]
    assert runner.chat(pro_user_id, "terceira", monthly_limit=100) == "Resposta da terceira."


def test_sem_a_trava_o_historico_envenena(monkeypatch, pro_user_id):
    monkeypatch.setattr(runner, "trava_do_turno", _sem_trava)
    _, s2 = _roteiro(monkeypatch, pro_user_id)
    # a OpenAI falsa recusou o histórico órfão (o runner a vê como erro da OpenAI)
    assert s2 == [runner.ERROR_MSG], s2
    with pytest.raises(AssertionError, match="órfão"):
        _pares(db.ai_get_recent_messages(pro_user_id, 20))


def test_usuarios_diferentes_nao_se_bloqueiam(monkeypatch, pro_user_id):
    from conftest import usuario_pagante
    outro = usuario_pagante()
    tool = _Tool(monkeypatch)
    cancela = threading.Event()
    openai_falso(monkeypatch, rodada(None, ("get_balance", {})), rodada("Oi, outro."))
    t1 = _em_thread(lambda: executar(pro_user_id, "primeira", cancela)[0], [])
    assert tool.dentro.wait(10)
    saida = []
    _em_thread(lambda: runner.chat(outro, "oi", monthly_limit=100), saida).join(5)
    tool.solta.set()
    t1.join(20)
    assert saida == ["Oi, outro."], "o turno do outro usuário ficou preso atrás do primeiro"


def test_espera_estourada_responde_amigavel_sem_gravar_nem_reservar(monkeypatch, pro_user_id):
    monkeypatch.setattr(runner, "TURNO_ESPERA_S", 0.3)
    tool = _Tool(monkeypatch)
    openai_falso(monkeypatch, rodada(None, ("get_balance", {})), rodada("Fim do primeiro."))
    s1 = []
    t1 = _em_thread(lambda: runner.chat(pro_user_id, "primeira", monthly_limit=100), s1)
    assert tool.dentro.wait(10)
    antes = len(linhas(pro_user_id))
    assert runner.chat(pro_user_id, "segunda", monthly_limit=100) == runner._OCUPADO
    with pytest.raises(runner.TurnoOcupado):  # o WhatsApp devolve ao roteador, com o motivo certo
        runner.chat(pro_user_id, "segunda", monthly_limit=100, ia_primeiro=True)
    assert len(linhas(pro_user_id)) == antes and db.ai_get_usage_this_month(pro_user_id) == 1
    tool.solta.set()
    t1.join(20)
    assert s1 == ["Fim do primeiro."]


def test_a_trava_e_solta_depois_de_excecao(monkeypatch, pro_user_id):
    def quebra(*a, **k):
        raise RuntimeError("boom")

    real = runner._chat_inner
    monkeypatch.setattr(runner, "_chat_inner", quebra)
    with pytest.raises(RuntimeError):
        runner.chat(pro_user_id, "oi", monthly_limit=100)
    monkeypatch.setattr(runner, "_chat_inner", real)
    monkeypatch.setattr(runner, "TURNO_ESPERA_S", 0.3)  # se a trava vazou, estoura aqui
    openai_falso(monkeypatch, rodada("Livre."))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == "Livre."


# ── "sim"/"não" que disputam a MESMA pendência, com a trava ligada ───────────

def _dois_ao_mesmo_tempo(monkeypatch, uid, texto):
    """Pendência `delete_all_launches` armada; duas threads mandam `texto` e as
    duas leem a pendência (na chegada) antes de qualquer uma pegar a vez."""
    db.add_launch_and_update_balance(uid, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    assert db.ai_set_pending_action(uid, "delete_all_launches", {}, "apagar TODOS os seus lançamentos")
    barreira, local, real = threading.Barrier(2, timeout=10), threading.local(), db.ai_get_pending_action

    def leitura(u):
        pend = real(u)
        if not getattr(local, "leu", False):  # a 1ª leitura de cada thread = a chegada
            local.leu = True
            barreira.wait()
        return pend

    monkeypatch.setattr(db, "ai_get_pending_action", leitura)
    saidas = [[], []]
    ts = [_em_thread(lambda s=s: runner.chat(uid, texto, monthly_limit=100), s) for s in saidas]
    for t in ts:
        t.join(30)
    monkeypatch.setattr(db, "ai_get_pending_action", real)
    return sorted(str(s[0]) for s in saidas)


@pytest.mark.parametrize("texto, vencedor, restam", [
    ("sim", "Apaguei 1 lançamento", 0),            # confirma: executa uma vez
    ("não", "👍 Beleza, não fiz nada.", 1),         # simétrico: cancela uma vez
])
def test_dois_sim_ou_nao_simultaneos_o_perdedor_recebe_cas_perdido(monkeypatch, pro_user_id, texto, vencedor, restam):
    reg = openai_falso(monkeypatch)  # qualquer chamada à OpenAI estoura
    respostas = _dois_ao_mesmo_tempo(monkeypatch, pro_user_id, texto)
    assert runner._CAS_PERDIDO in respostas and any(vencedor in r for r in respostas), respostas
    assert reg.creates == [] and db.ai_get_usage_this_month(pro_user_id) == 0
    assert db.count_launches(pro_user_id) == restam


def test_controle_negativo_sem_o_conserto_o_perdedor_gasta_vaga_e_vai_ao_modelo(monkeypatch, pro_user_id):
    monkeypatch.setattr(runner, "_ainda_e_a_mesma", lambda *a: True)
    reg = openai_falso(monkeypatch, rodada("Posso ajudar em algo?"))
    respostas = _dois_ao_mesmo_tempo(monkeypatch, pro_user_id, "sim")
    assert "Posso ajudar em algo?" in respostas and len(reg.creates) == 1
    assert db.ai_get_usage_this_month(pro_user_id) == 1


def test_sim_comum_sem_pendencia_vai_ao_modelo(monkeypatch, pro_user_id):
    """O Piggy perguntou 'quer que eu abra as transações?': o 'sim' é conversa."""
    reg = openai_falso(monkeypatch, rodada("Abrindo as transações."))
    assert runner.chat(pro_user_id, "sim", monthly_limit=100) == "Abrindo as transações."
    assert len(reg.creates) == 1 and db.ai_get_usage_this_month(pro_user_id) == 1


def test_ia_primeiro_ocupada_loga_o_motivo_certo(monkeypatch, pro_small_uid):
    """Trava estourada no WhatsApp: o roteador atende, e o log não diz 'desistiu'."""
    import core.handle_incoming as hi
    from core.services import ai_chat, wa_ia_primeiro

    def ocupada(*a, **k):
        raise runner.TurnoOcupado()

    logs = []
    monkeypatch.setattr(wa_ia_primeiro, "ativo", lambda uid: True)
    monkeypatch.setattr(wa_ia_primeiro, "fica_no_roteador", lambda uid, t: False)
    monkeypatch.setattr(ai_chat, "chat", ocupada)
    monkeypatch.setattr(hi, "log_system_event_sync", lambda lvl, ev, motivo, **k: logs.append((ev, motivo)))
    assert hi._ia_primeiro(pro_small_uid, "oi") is hi._IA_TENTOU
    assert logs == [("wa_ia_primeiro_fallback", "a IA estava terminando a resposta anterior")]


# ── "sim"/"não" cego: chega SEM pendência, e o turno anterior arma uma na espera ─

def _cego(monkeypatch, uid, texto):
    """Turno 1 ("apaga tudo") parado na tool que arma `delete_all_launches` (B);
    `texto` chega sem pendência e espera a trava; o turno 1 arma B e solta."""
    db.add_launch_and_update_balance(uid, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    tool = _Tool(monkeypatch)
    reg = openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada("Apago tudo mesmo? Sim ou não."))
    s1, s2 = [], []
    t1 = _em_thread(lambda: runner.chat(uid, "apaga tudo", monthly_limit=100), s1)
    assert tool.dentro.wait(10) and db.ai_get_pending_action(uid) is None
    t2 = _em_thread(lambda: runner.chat(uid, texto, monthly_limit=100), s2)
    t2.join(0.5)
    tool.solta.set()
    t1.join(20), t2.join(20)
    return s1, s2, reg


@pytest.mark.parametrize("texto", ["sim", "não"])
def test_confirmacao_cega_nao_toca_a_pendencia_que_o_usuario_nao_viu(monkeypatch, pro_user_id, texto):
    s1, s2, reg = _cego(monkeypatch, pro_user_id, texto)
    assert s2 == [runner._PERGUNTA_NOVA], s2
    assert db.count_launches(pro_user_id) == 1 and db.ai_get_pending_action(pro_user_id) is not None
    assert len(reg.creates) == 2 and db.ai_get_usage_this_month(pro_user_id) == 1  # só o turno 1
    # positivo: o "sim" seguinte já viu B e executa
    if texto == "sim":
        assert "Apaguei" in runner.chat(pro_user_id, "sim", monthly_limit=100)
        assert db.count_launches(pro_user_id) == 0
    else:
        assert "não fiz nada" in runner.chat(pro_user_id, "não", monthly_limit=100)
        assert db.count_launches(pro_user_id) == 1 and db.ai_get_pending_action(pro_user_id) is None
