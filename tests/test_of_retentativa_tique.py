"""Onda 5, PR-B2, rodada 2: o tique sob ataque (disjuntor, ordem, prazo, falha no
meio) e o texto do E13. Helpers e a fixture `amb` moram em
`tests/test_of_retentativa.py`; tabelas em `docs/open_finance_estados.md` §2.2.

CONTROLES, medidos em 2026-09-30 (mutação numa cópia do arquivo de produção,
restaurada e conferida com `cmp`; remeça se mexer no código):

  negativos:
    o desfecho perder o status de `/investments`        → 429 em `/investments` (as 2 formas)
    ordenar só por `id` (sem `last_attempt_at`)         → `test_mais_antiga_primeiro...` (em `test_of_retentativa.py`);
                                                          desde a fila em memória a fome dos venenos não depende dele
    cortar o K antes do `filtrar_por_acesso`            → conta cortada toma a vaga do K
    `connection_not_found` fora dos neutros             → readoção no meio não zera a conta
    `connection_paused` / `connection_deleted` fora     → pausa/remoção no meio (um caso cada)
    tirar a checagem de classe da rechecagem            → `LOGIN_ERROR` visto no meio
    tirar o `rechecados` do resumo                      → readoção ANTES da rechecagem
    rechecar por `provider_item_id`, não por `id`       → readoção ANTES da rechecagem
    voltar a carimbar a tentativa no orquestrador       → 5xx em `no_accounts`, coalescido
    tirar a ordenação por `_TENTADOS` (fila em memória) → `no_accounts` persistente (3 e 6),
                                                          `_INFLIGHT` preso, nunca tentado antes
    tirar o descarte de `_TENTADOS` (item fora da fila)  → a memória não cresce sem limite
    descartar `_TENTADOS` por idade (`4 × prazo_sec`)    → `test_fome_com_o_relogio_de_producao` (N = 6, 9)
    tirar o `wait_for`/`shield` (esperar sem prazo)     → sync pendurado
    `wait_for(..., prazo_sec)` no lugar de `restante`   → o prazo estoura em 60%
    tirar o `try` do laço (`_open_finance_refresh`)     → exceção segura o PATCH periódico
    tirar o `finally` do resumo                         → exceção perde o `of_retry_tick`
    `OF_RETRY_MAX_PER_TICK` inválido desligar           → valor inválido cai no padrão
    E13 sem detalhe próprio (`connection_ui_state`)     → card e toast do E13
    PATCH periódico já no 1º tique (`com_patch` ignora o boot) → o 1º tique não pode ter PATCH
    tirar o 1º `sleep` curto (esperar o intervalo inteiro) → o 1º tique espera 10 min
  positivos: sync que termina dentro do prazo segue; valor inválido da flag cai no
  padrão (20) e a retentativa roda; o erro comum mantém "Tentaremos de novo
  automaticamente"; E12 (`status='ERROR'` do webhook) idem.

Tempo: o ponto de espera do sync pendurado usa prazo de 0,5 s e um `Event` com
teto de 8 s, então nenhum caso passa de ~9 s mesmo vermelho.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from psycopg.types.json import Jsonb

import core.services.pluggy_sync as ps
import db
from conftest import promote_to_pro
from db_support import invalidate_auth_user_cache
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.of_retentativa as orq
import frontend.routes.open_finance as of_routes
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_of_item_ownership import eventos, sem_indice_unico  # noqa: F401
from test_of_leitura_incompleta import _ui_pela_rota
from test_of_retentativa import (  # noqa: F401
    _Pluggy, _envelhece, _health, _linha, _nova, _retenta, _um_tique, amb)


def _readota(amb, conexao: dict, outro_uid: int) -> dict:
    """Apaga a linha e cria outra, de OUTRO dono, para o mesmo item."""
    db.ensure_user(outro_uid)
    amb.meus.add(outro_uid)
    nova: dict = {}

    def _faz():
        with get_conn() as conn:
            conn.execute("delete from open_finance_connections where id=%s", (conexao["id"],))
            conn.commit()
        nova.update(_nova(outro_uid, reason="read_failed", item=conexao["item"]))
        nova["antes"] = _linha(nova["id"])

    return nova, _faz


# ── o 429 de `/investments` (o caso de produção que originou a Onda) ─────────

@pytest.mark.parametrize("forma", ["com_contas", "sem_contas"])
def test_429_em_investments_para_o_tique(user_id, amb, monkeypatch, forma):
    """`/investments` é fail-soft no sync (`investments_read_failed`, desfecho `ok`),
    e mesmo assim é rate limit: o tique para. Pela chamada real do sync."""
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(4)]
    chamadas: list[str] = []

    def _inv(item_id, _k=None):
        chamadas.append(item_id)
        raise PluggyApiError("rate", status_code=429)

    monkeypatch.setattr(ps, "list_pluggy_investments", _inv)
    if forma == "sem_contas":
        monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, _k=None: [])
    tick = _retenta(amb)
    assert chamadas == [conexoes[0]["item"]], f"tentou {len(chamadas)} itens sob 429"
    assert tick["details"]["interrompido"] == "429" and tick["level"] == "warning"


# ── a ordem e a fome ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("venenos, tiques", [(3, 2), (9, 4), (20, 7)])
def test_venenos_atrasam_os_saudaveis_por_um_mais_venenos_div_3_tiques(
        user_id, amb, venenos, tiques):
    """O disjuntor de 3 falhas seguidas faz cada tique gastar-se em 3 itens que falham
    sempre. Os saudáveis, atrás deles na fila (tentativa mais recente), são lidos no
    tique `venenos // 3 + 1` (medido: 3 → 2, 9 → 4, 20 → 7), e não em `ceil(E/K)`.
    O veneno tentado vai para o fim da fila: pela coluna (a F carimba `last_attempt_at`)
    e pela memória do orquestrador (`_TENTADOS`); sem as duas, os mesmos 3 ficariam na
    frente para sempre (fome total)."""
    veneno = [_nova(user_id, reason="read_failed",
                    last_attempt_at=f"now() - interval '{30 + i} hours'") for i in range(venenos)]
    sadios = [_nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
              for _ in range(2)]
    for c in veneno:
        amb.pluggy.falha[c["item"]] = PluggyApiError("x", status_code=500)
    lido_no_tique = None
    for n in range(1, 12):
        amb.pluggy.contas.clear()
        tick = _retenta(amb)
        if n == 1:
            assert tick["details"]["interrompido"] == "falhas_seguidas"
        if any(amb.pluggy.contas[c["item"]] for c in sadios):
            lido_no_tique = n
            break
        _envelhece(*[c["id"] for c in veneno + sadios])
    assert lido_no_tique == tiques


# ── o corte por acesso vem ANTES do K ────────────────────────────────────────

def test_conta_cortada_nao_toma_vaga_do_k(user_id, amb, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "2")
    cortado = user_id + 1
    db.ensure_user(cortado)
    amb.meus.add(cortado)
    cortadas = [_nova(cortado, reason="read_failed", last_attempt_at="now() - interval '9 hours'")
                for _ in range(2)]
    com = [_nova(user_id, reason="read_failed") for _ in range(2)]
    _retenta(amb)
    assert [amb.pluggy.contas[c["item"]] for c in com] == [1, 1]
    assert [amb.pluggy.chamadas[c["item"]] for c in cortadas] == [0, 0]


# ── rechecagem ───────────────────────────────────────────────────────────────

def test_readocao_antes_da_rechecagem_nao_chega_ao_novo_dono(user_id, amb):
    """A rechecagem é por `id`: com a linha apagada devolve vazio. Por
    `provider_item_id` ela acharia a linha do OUTRO dono, que não passou por
    `filtrar_por_acesso`, e a sincronizaria."""
    c = _nova(user_id, reason="read_failed")
    nova, faz = _readota(amb, c, user_id + 1)
    amb.ganchos[c["id"]] = faz          # roda ANTES da rechecagem daquela linha
    tick = _retenta(amb)
    assert amb.pluggy.chamadas[c["item"]] == 0
    assert _linha(nova["id"]) == nova["antes"]
    assert tick["details"]["rechecados"] == 1 and tick["details"]["tentados"] == 0


def test_a_rechecagem_tambem_confere_a_classe(user_id, amb):
    """Entre a listagem e a vez do item, a saúde viu `LOGIN_ERROR` (só o usuário
    resolve). A linha continua passando nos filtros do SQL; quem a barra é o
    classificador, sem custo de Pluggy."""
    a, b = _nova(user_id, reason="read_failed"), _nova(user_id, reason="read_failed")

    def _viu_login_error():
        with get_conn() as conn:
            conn.execute("update open_finance_connections set status='ERROR', "
                         "status_reason=null, health=%s where id=%s",
                         (Jsonb(_health("LOGIN_ERROR")), b["id"]))
            conn.commit()

    amb.ganchos[b["id"]] = _viu_login_error
    tick = _retenta(amb)
    assert (amb.pluggy.contas[a["item"]], amb.pluggy.chamadas[b["item"]]) == (1, 0)
    assert tick["details"]["rechecados"] == 1


# ── neutros do disjuntor ─────────────────────────────────────────────────────

def _seq_FFXF(user_id, amb, muda_antes_do_run):
    """F F X F: o `X` é o desfecho neutro provocado por `muda_antes_do_run` (gancho
    DEPOIS da rechecagem do 3º item). Se o neutro contasse como `ok` a conta
    zerava e o 5º item seria lido; como neutro, a 3ª falha (4º item) abre."""
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(5)]
    for i in (0, 1, 3):
        amb.pluggy.falha[conexoes[i]["item"]] = PluggyApiError("x", status_code=503)
    amb.ganchos[("depois", conexoes[2]["id"])] = lambda: muda_antes_do_run(conexoes[2])
    tick = _retenta(amb)
    assert tick["details"]["interrompido"] == "falhas_seguidas"
    assert amb.pluggy.chamadas[conexoes[4]["item"]] == 0
    assert tick["details"]["neutros"] == 1


def test_connection_not_found_e_neutro(user_id, amb):
    def _readota_no_meio(c):
        _nova_linha, faz = _readota(amb, c, user_id + 1)
        faz()

    _seq_FFXF(user_id, amb, _readota_no_meio)


@pytest.mark.parametrize("terminal", ["PAUSED", "DELETED"])
def test_pausada_ou_removida_no_meio_e_neutro(user_id, amb, terminal):
    def _termina(c):
        with get_conn() as conn:
            conn.execute("update open_finance_connections set status=%s where id=%s",
                         (terminal, c["id"]))
            conn.commit()

    _seq_FFXF(user_id, amb, _termina)


# ── o prazo e o sync pendurado ───────────────────────────────────────────────

def test_sync_pendurado_nao_segura_o_tique_e_segue_vivo(user_id, amb):
    """O prazo vale também DURANTE o sync, não só antes de cada item: o tique solta
    a espera (`wait_for` sobre `shield`) e o sync continua vivo no `_INFLIGHT`."""
    c = _nova(user_id, reason="read_failed")
    antes = _linha(c["id"])["last_sync_at"]
    solta = threading.Event()
    amb.pluggy.durante_contas = lambda item: solta.wait(8)

    async def _main():
        t0 = time.monotonic()
        resumo = await amb.orq.retentar_leituras(prazo_sec=0.5)
        demorou = time.monotonic() - t0
        tarefa = of_routes._INFLIGHT.get(c["item"])
        vivo = tarefa is not None and not tarefa.done()
        solta.set()
        if tarefa is not None:
            await tarefa
        return resumo, demorou, vivo

    resumo, demorou, vivo = asyncio.run(_main())
    assert demorou < 3, f"o tique esperou {demorou:.1f}s por um sync pendurado"
    assert resumo["interrompido"] == "prazo" and resumo["pendurados"] == 1
    assert vivo, "o sync foi cancelado junto com a espera"
    assert _linha(c["id"])["last_sync_at"] > antes, "o sync não terminou depois de solto"


def test_sync_que_termina_dentro_do_prazo_segue(user_id, amb):
    c = _nova(user_id, reason="read_failed")
    tick = _retenta(amb, prazo_sec=60)
    assert tick["details"]["ok"] == 1 and tick["details"]["interrompido"] is None
    assert amb.pluggy.contas[c["item"]] == 1


# ── falha no meio da etapa ───────────────────────────────────────────────────

def test_excecao_na_retentativa_nao_segura_o_patch_periodico(user_id, amb, monkeypatch, capsys):
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    chamadas: list = []
    monkeypatch.setattr(ps, "request_pluggy_refresh",
                        lambda **kw: chamadas.append(kw) or {"claimed": [], "failures": []})

    def _boom(**_kw):
        raise RuntimeError("banco fora: Key (user_id)=(123456)")

    monkeypatch.setattr(amb.orq, "list_connections_para_retentar", _boom)
    _nova(user_id, reason="read_failed")
    _um_tique(monkeypatch, tiques=2)       # o PATCH só roda do 2º tique em diante
    assert chamadas, "o PATCH periódico foi pulado porque a retentativa levantou"
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "123456" not in err, "o print deve levar só o tipo"


def test_excecao_no_meio_ainda_registra_o_tique(user_id, amb, monkeypatch):
    a, b = _nova(user_id, reason="read_failed"), _nova(user_id, reason="read_failed")
    real = amb.orq.list_connections_para_retentar

    def _2a_rechecagem_levanta(**kw):
        if kw.get("id") == b["id"]:
            raise RuntimeError("banco fora")
        return real(**kw)

    monkeypatch.setattr(amb.orq, "list_connections_para_retentar", _2a_rechecagem_levanta)
    with pytest.raises(RuntimeError):
        asyncio.run(amb.orq.retentar_leituras(prazo_sec=3600))
    ticks = [e for e in amb.eventos if e["event"] == "of_retry_tick"]
    assert len(ticks) == 1 and ticks[0]["level"] == "warning"
    assert ticks[0]["details"]["interrompido"] == "erro" and ticks[0]["details"]["tentados"] == 1
    assert amb.pluggy.contas[a["item"]] == 1


# ── `OF_RETRY_MAX_PER_TICK` ──────────────────────────────────────────────────

@pytest.mark.parametrize("valor, desligado", [
    ("0", True), ("-1", True),
    # Convenção das `OF_*` inteiras (`_env_int`): o que não é inteiro cai no padrão.
    ("", False), ("abc", False), ("off", False), ("1.5", False), ("20", False)])
def test_so_zero_ou_negativo_desliga_a_retentativa(user_id, amb, monkeypatch, valor, desligado):
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", valor)
    c = _nova(user_id, reason="read_failed")
    r = asyncio.run(amb.orq.retentar_leituras(prazo_sec=3600))
    assert (r.get("skipped") == "disabled") is desligado, r
    assert amb.pluggy.contas[c["item"]] == (0 if desligado else 1)


# ── o texto do E13 (DECISÃO 1 = B) ───────────────────────────────────────────

@pytest.mark.parametrize("estado, detalhe", [
    ("E13_item_em_error", "O banco teve um erro — atualize de novo mais tarde"),
    ("E13_read_failed_item_em_error", "O banco teve um erro — atualize de novo mais tarde"),
    # positivos: os outros `error_recoverable` continuam com a promessa, agora verdadeira
    ("E1_read_failed", "Tentaremos de novo automaticamente"),
    ("E12_error_do_webhook", "Tentaremos de novo automaticamente"),
])
def test_detalhe_do_erro_temporario_pela_rota(user_id, estado, detalhe):
    kw = {"E13_item_em_error": dict(status="ERROR", health=_health("ERROR")),
          "E13_read_failed_item_em_error": dict(reason="read_failed", health=_health("ERROR")),
          "E1_read_failed": dict(reason="read_failed"),
          "E12_error_do_webhook": dict(status="ERROR")}[estado]
    promote_to_pro(user_id)
    c = _nova(user_id, **kw)
    ui = _ui_pela_rota(user_id, c["item"])
    assert (ui["state"], ui["label"], ui["detail"]) == ("error_recoverable", "Erro temporário",
                                                        detalhe)


# ── o prazo vale para o RESTANTE, não para o prazo inteiro ───────────────────

def test_a_espera_do_sync_respeita_o_restante_e_nao_o_prazo_inteiro(user_id, amb):
    """Prazo de 1,0 s; o 1º sync gasta ~0,6 s e o 2º pendurado. O tique tem de sair por
    volta de 1,0 s. Com `wait_for(shield(t), prazo_sec)` (o prazo inteiro) sairia por
    ~1,6 s, estourando o prazo em 60%."""
    a = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '5 hours'")
    b = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '4 hours'")
    solta = threading.Event()
    amb.pluggy.durante_contas = lambda item: time.sleep(0.6) if item == a["item"] else solta.wait(10)

    async def _main():
        t0 = time.monotonic()
        r = await amb.orq.retentar_leituras(prazo_sec=1.0)
        demorou = time.monotonic() - t0
        solta.set()
        t = of_routes._INFLIGHT.get(b["item"])
        if t:
            await t
        return r, demorou

    r, demorou = asyncio.run(_main())
    assert r["interrompido"] == "prazo" and r["pendurados"] == 1
    assert demorou < 1.35, f"o tique levou {demorou:.2f}s com prazo de 1,0 s"


# ── a fila em memória (`_TENTADOS`): quem foi tentado vai para depois ─────────
# O que a coluna `last_attempt_at` não faz: `no_accounts` com o `GET /items` falhando
# não grava nada na linha (e a âncora da "Pluggy à frente" não pode ser carimbada), e um
# `_INFLIGHT` preso não é tentado de verdade. A memória do processo só ordena a fila.

@pytest.mark.parametrize("venenos, tique_do_saudavel", [(2, 1), (3, 2), (6, 3)])
def test_no_accounts_com_get_items_persistente_atrasa_e_nao_deixa_com_fome(
        user_id, amb, venenos, tique_do_saudavel):
    """`no_accounts` com a Pluggy à frente e o `GET /items` falhando SEMPRE: não grava nada
    na linha. Cada tique gasta 3 tentativas neles, e a memória os manda para o fim, então
    os saudáveis atrás de N são lidos no tique `N // 3 + 1` (2 → 1, 3 → 2, 6 → 3), como
    qualquer outro "veneno" (antes: com 3 ou mais, em nenhum tique)."""
    ruins = [_nova(user_id, reason="no_accounts", health=_health(horas=1),
                   last_attempt_at=f"now() - interval '{30 + i} hours'") for i in range(venenos)]
    sadios = [_nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
              for _ in range(2)]
    for c in ruins:
        amb.pluggy.falha[c["item"]] = PluggyApiError("x", status_code=500)
    lido_no_tique = None
    for n in range(1, 8):
        amb.pluggy.contas.clear()
        _retenta(amb)
        if any(amb.pluggy.contas[c["item"]] for c in sadios):
            lido_no_tique = n
            break
        _envelhece(*[c["id"] for c in ruins + sadios])
    assert lido_no_tique == tique_do_saudavel
    assert venenos // 3 + 1 == tique_do_saudavel


def test_inflight_preso_nao_toma_a_vaga_do_k_para_sempre(user_id, amb, monkeypatch):
    """Um `_INFLIGHT` que nunca termina é coalescido a cada tique. Com K=1 ele tomava a
    única vaga (era risco aceito); a memória o manda para o fim e o saudável atrás é
    lido no tique seguinte."""
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "1")
    preso = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '30 hours'")
    sadio = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
    of_routes._INFLIGHT[preso["item"]] = object()
    assert _retenta(amb)["details"]["coalescidos"] == 1     # tique 1: gasta a vaga no preso
    assert amb.pluggy.contas[sadio["item"]] == 0
    _envelhece(preso["id"], sadio["id"])
    _retenta(amb)                                           # tique 2: o saudável
    assert amb.pluggy.contas[sadio["item"]] == 1


def test_item_nunca_tentado_vem_antes_do_tentado(user_id, amb, monkeypatch):
    """K=1. O mais antigo (`no_accounts`, falha sem gravar nada) é tentado no tique 1; no
    tique 2 o nunca tentado vem antes dele, embora a listagem o ponha em 2º. Depois que o
    nunca tentado sai da lista (leu), o tentado volta (rodízio, não descarte)."""
    monkeypatch.setenv("OF_RETRY_MAX_PER_TICK", "1")
    velho = _nova(user_id, reason="no_accounts", health=_health(horas=1),
                  last_attempt_at="now() - interval '30 hours'")
    novo = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
    amb.pluggy.falha[velho["item"]] = PluggyApiError("x", status_code=500)
    _retenta(amb)
    assert (amb.pluggy.chamadas[velho["item"]], amb.pluggy.chamadas[novo["item"]]) == (3, 0)
    _envelhece(velho["id"], novo["id"])
    amb.pluggy.chamadas.clear()
    _retenta(amb)
    assert (amb.pluggy.chamadas[velho["item"]], amb.pluggy.contas[novo["item"]]) == (0, 1)
    _envelhece(velho["id"])
    amb.pluggy.chamadas.clear()
    _retenta(amb)
    assert amb.pluggy.chamadas[velho["item"]] == 3


def test_a_memoria_da_fila_sai_com_o_item_e_nao_com_a_idade(user_id, amb):
    """Descarte por CANDIDATURA: sai o `id` que não está mais entre as candidatas (item que
    saiu da fila), e fica o que ainda está, por mais velho que seja. Por idade a rotação se
    perdia (ver `test_fome_com_o_relogio_de_producao`). Limitada pela fila elegível."""
    c = _nova(user_id, reason="read_failed")
    amb.pluggy.falha[c["item"]] = PluggyApiError("x", status_code=500)
    amb.orq._TENTADOS[999_999_999] = amb.orq._quando()                # item que não existe mais
    amb.orq._TENTADOS[c["id"]] = amb.orq._quando() - 100 * 24 * 3600  # 100 dias, ainda candidato
    _retenta(amb, prazo_sec=10)
    assert 999_999_999 not in amb.orq._TENTADOS, "o item que saiu da fila continua na memória"
    assert c["id"] in amb.orq._TENTADOS and amb.orq._TENTADOS[c["id"]] > amb.orq._quando() - 60


def _relogio_de_producao(monkeypatch, amb):
    """`_quando` anda 6 h + 60 s entre tiques (o tique dorme o intervalo e ainda gasta o
    tempo da saúde e do sync). Devolve a função que avança um tique."""
    t = {"v": 1_000_000.0}
    monkeypatch.setattr(amb.orq, "_quando", lambda: t["v"])
    return lambda: t.__setitem__("v", t["v"] + 6 * 3600 + 60)


@pytest.mark.parametrize("tipo", ["no_accounts", "read_failed"])
@pytest.mark.parametrize("venenos", [3, 5, 6, 9])
def test_fome_com_o_relogio_de_producao(user_id, amb, monkeypatch, tipo, venenos):
    """Com o tempo passando de verdade para `_TENTADOS` (tiques que duram mais que o
    intervalo, prazo = metade dele), os saudáveis atrás de N venenos continuam sendo lidos
    no tique `N // 3 + 1`. O descarte por idade (`4 × prazo_sec`) guardava só 1 tique: os
    venenos voltavam a "nunca tentados" e os saudáveis nunca eram lidos (N = 6 e 9)."""
    avanca = _relogio_de_producao(monkeypatch, amb)
    ruins = [_nova(user_id, reason=("no_accounts" if tipo == "no_accounts" else "read_failed"),
                   **({"health": _health(horas=1)} if tipo == "no_accounts" else {}),
                   last_attempt_at=f"now() - interval '{30 + i} hours'") for i in range(venenos)]
    sadios = [_nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
              for _ in range(2)]
    for c in ruins:
        amb.pluggy.falha[c["item"]] = PluggyApiError("x", status_code=500)
    lido = None
    for n in range(1, 12):
        amb.pluggy.contas.clear()
        _retenta(amb, prazo_sec=3 * 3600)
        if any(amb.pluggy.contas[c["item"]] for c in sadios):
            lido = n
            break
        _envelhece(*[c["id"] for c in ruins + sadios])
        avanca()
    assert lido == venenos // 3 + 1, f"{tipo} N={venenos}: saudáveis lidos no tique {lido}"


# ── RISCOS ACEITOS (`docs/open_finance_estados.md` §2.2) ─────────────────────
# Estes testes afirmam o comportamento ATUAL e conhecido, não um ideal. Documentam o
# limite: uma mudança que os quebre precisa de decisão (do dono), não de "conserto".

def test_risco_aceito_5xx_persistente_em_investments_nao_abre_o_disjuntor(
        user_id, amb, monkeypatch):
    """RISCO ACEITO (§2.2). `/investments` é fail-soft no sync: só o 429 viaja no
    desfecho e abre o disjuntor. Um 5xx persistente ali deixa os itens `ok`: os 5 são
    lidos por inteiro no tique (custo limitado por K e pelo intervalo)."""
    conexoes = [_nova(user_id, reason="read_failed") for _ in range(5)]
    lidos: list[str] = []

    def _inv(item_id, _k=None):
        lidos.append(item_id)
        raise PluggyApiError("boom", status_code=503)

    monkeypatch.setattr(ps, "list_pluggy_investments", _inv)
    tick = _retenta(amb)
    assert len(lidos) == len(conexoes) and tick["details"]["interrompido"] is None


# ── o 1º tique, 10 min depois do boot ────────────────────────────────────────

@pytest.mark.parametrize("refresh_ligado", ["1", "0"])
def test_primeiro_tique_espera_10_min_so_com_get_e_o_patch_entra_do_segundo(
        monkeypatch, refresh_ligado):
    """O laço espera `_PRIMEIRO_TIQUE_SEC` (600 s), roda saúde e retentativa, e SÓ do 2º
    tique em diante, depois do intervalo, o PATCH periódico (e só com `OF_REFRESH_ENABLED`):
    o PATCH no boot é o que o Railway repetia a cada deploy."""
    eventos_: list = []
    monkeypatch.setenv("OF_REFRESH_ENABLED", refresh_ligado)
    monkeypatch.setenv("OF_REFRESH_INTERVAL_SEC", "4321")
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: eventos_.append("saude") or {})
    monkeypatch.setattr(ps, "request_pluggy_refresh",
                        lambda **kw: eventos_.append("patch") or {"claimed": [], "failures": []})

    async def _retentativa(**kw):
        eventos_.append("retentativa")
        return {}

    async def _sleep(sec, *a, **kw):
        eventos_.append(("sleep", sec))
        if sum(1 for e in eventos_ if isinstance(e, tuple)) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(orq, "retentar_leituras", _retentativa)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dashboard._open_finance_refresh())
    assert dashboard._PRIMEIRO_TIQUE_SEC == 600
    assert eventos_ == [("sleep", 600), "saude", "retentativa",
                        ("sleep", 4321), "saude", "retentativa",
                        *(["patch"] if refresh_ligado == "1" else []),
                        ("sleep", 4321)]


def _roda_laco(monkeypatch, *, interval, ligado, tiques, tick_falha_no=()):
    """O laço de verdade, com o corpo do tique trocado por um registro: devolve os `sleep`
    pedidos e o `com_patch` de cada tique (o 1º tique em `tick_falha_no` levanta)."""
    sleeps, chamadas = [], []

    async def _tick(*, interval, com_patch):
        chamadas.append(com_patch)
        if len(chamadas) in tick_falha_no:
            raise RuntimeError("boom")

    async def _sleep(sec, *a, **kw):
        sleeps.append(sec)
        if len(sleeps) > tiques:
            raise asyncio.CancelledError

    monkeypatch.setenv("OF_REFRESH_INTERVAL_SEC", str(interval))
    monkeypatch.setenv("OF_REFRESH_ENABLED", ligado)
    monkeypatch.setattr(dashboard, "_open_finance_tick", _tick)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dashboard._open_finance_refresh())
    return sleeps, chamadas


@pytest.mark.parametrize("ligado", ["1", "0"])
def test_tique_que_levanta_no_primeiro_nao_adia_o_patch_do_segundo(monkeypatch, ligado):
    """O 1º tique levanta: o PATCH continua sendo do 2º (o flag do 1º tique cai antes do
    corpo, e não depois dele)."""
    sleeps, chamadas = _roda_laco(monkeypatch, interval=4321, ligado=ligado, tiques=3,
                                  tick_falha_no=(1,))
    assert sleeps[:3] == [600, 4321, 4321]
    assert chamadas == [False, ligado == "1", ligado == "1"]


def test_intervalo_menor_que_o_primeiro_tique_nao_espera_mais_que_o_intervalo(monkeypatch):
    sleeps, _ = _roda_laco(monkeypatch, interval=60, ligado="1", tiques=2)
    assert sleeps[:2] == [60, 60]


# ── LIMITE CONHECIDO (`docs/open_finance_estados.md` §2.2, "Limite da fila em memória") ──

@pytest.mark.parametrize("venenos", [3, 6])
def test_limite_conhecido_com_deploy_a_cada_tique_a_fila_em_memoria_e_inerte(
        user_id, amb, venenos):
    """LIMITE CONHECIDO, não comportamento desejado. A fila em memória só ajuda se o
    processo ficar vivo por 2 ou mais tiques. Com deploy mais frequente que ~12 h cada
    processo vê UM tique com a memória vazia, a ordem volta à da listagem e a fome dos
    `no_accounts` com `GET /items` falhando (3 ou mais) volta. A versão durável exige uma
    coluna (migration), fora do B2. Se este teste quebrar, quem mexeu resolveu o limite:
    atualize a §2.2."""
    ruins = [_nova(user_id, reason="no_accounts", health=_health(horas=1),
                   last_attempt_at=f"now() - interval '{30 + i} hours'") for i in range(venenos)]
    sadios = [_nova(user_id, reason="read_failed", last_attempt_at="now() - interval '3 hours'")
              for _ in range(2)]
    for c in ruins:
        amb.pluggy.falha[c["item"]] = PluggyApiError("x", status_code=500)
    lido = False
    for _ in range(5):                       # 5 deploys, 1 tique cada
        amb.orq._TENTADOS.clear()            # processo novo
        amb.pluggy.contas.clear()
        _retenta(amb, prazo_sec=3 * 3600)
        lido = lido or any(amb.pluggy.contas[c["item"]] for c in sadios)
        _envelhece(*[c["id"] for c in ruins + sadios], horas=24)
    assert lido is False


# ── valor capturado antes de um `await` e usado depois (apontamentos do Codex, #727) ──
# O acesso (`filtrar_por_acesso`) e o prazo restante eram decididos uma vez e usados
# depois de uma passada que pode durar horas ou de uma consulta lenta. Agora são
# reavaliados POR ITEM, imediatamente antes de agendar o sync.

def _dois_donos_com_acesso(user_id, amb, monkeypatch):
    """A (o `user_id`, na frente da fila) e B, ambos com direito de uso e uma conexão
    `read_failed`; devolve `(a, b, expira_b)`, onde `expira_b()` tira o plano de B."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    dono_b = user_id + 1
    db.ensure_user(dono_b)
    promote_to_pro(dono_b)
    amb.meus.add(dono_b)
    a = _nova(user_id, reason="read_failed", last_attempt_at="now() - interval '5 hours'")
    b = _nova(dono_b, reason="read_failed", last_attempt_at="now() - interval '3 hours'")

    def _expira_b():
        with get_conn() as conn:
            conn.execute("update auth_accounts set plan='free' where user_id=%s", (dono_b,))
            conn.commit()
        invalidate_auth_user_cache(dono_b)

    return a, b, _expira_b


def test_acesso_que_expira_durante_a_passada_barra_o_item_seguinte(user_id, amb, monkeypatch):
    """O plano do dono do 2º item expira enquanto o 1º sincroniza: `filtrar_por_acesso` já
    tinha aprovado B no começo da passada. O 2º não pode sincronizar (0 chamadas à
    Pluggy), e como foi PULADO não entra em `_TENTADOS` nem conta como tentado."""
    a, b, expira_b = _dois_donos_com_acesso(user_id, amb, monkeypatch)
    amb.pluggy.durante_contas = lambda item: expira_b() if item == a["item"] else None
    tick = _retenta(amb)
    assert amb.pluggy.contas[a["item"]] == 1
    assert amb.pluggy.chamadas[b["item"]] == 0, "o item de quem perdeu o acesso sincronizou"
    d = tick["details"]
    assert d["sem_acesso"] == 1 and d["tentados"] == 1 and b["item"] not in d["items"]
    assert b["id"] not in amb.orq._TENTADOS


def test_acesso_valido_durante_a_passada_continua_sincronizando(user_id, amb, monkeypatch):
    a, b, _expira_b = _dois_donos_com_acesso(user_id, amb, monkeypatch)
    tick = _retenta(amb)
    assert (amb.pluggy.contas[a["item"]], amb.pluggy.contas[b["item"]]) == (1, 1)
    assert tick["details"]["sem_acesso"] == 0 and tick["details"]["tentados"] == 2


def test_rechecagem_lenta_que_estoura_o_prazo_nao_agenda_o_sync(user_id, amb):
    """A consulta da rechecagem demora mais que o prazo restante (no pool pode esperar até
    30 s): o `restante` calculado ANTES dela estava velho e o sync era agendado mesmo
    assim. Depois dela, `restante <= 0` para o tique antes de agendar."""
    c = _nova(user_id, reason="read_failed")
    amb.ganchos[("depois", c["id"])] = lambda: time.sleep(1.3)
    tick = _retenta(amb, prazo_sec=1.0)
    assert amb.pluggy.chamadas[c["item"]] == 0, "agendou um sync depois do prazo"
    d = tick["details"]
    assert d["interrompido"] == "prazo" and d["tentados"] == 0 and c["id"] not in amb.orq._TENTADOS


def test_rechecagem_lenta_com_prazo_folgado_sincroniza(user_id, amb):
    c = _nova(user_id, reason="read_failed")
    amb.ganchos[("depois", c["id"])] = lambda: time.sleep(0.3)
    tick = _retenta(amb, prazo_sec=60)
    assert amb.pluggy.contas[c["item"]] == 1 and tick["details"]["interrompido"] is None


@pytest.mark.parametrize("expected, marca", [(None, True), ("dono", True), ("outro", False)])
def test_sync_in_progress_esgotado_so_marca_a_linha_do_dono_esperado(
        user_id, amb, monkeypatch, expected, marca):
    """O ramo do `sync_in_progress` esgotado (B1) usa a MESMA regra de dono da exceção
    final: a linha capturada é de OUTRO dono (readoção) → não marca nada. Sem
    `expected_user_id` (webhook) ou com o dono certo, marca `read_failed`."""
    c = _nova(user_id, reason=None)
    monkeypatch.setattr(of_routes, "sync_pluggy_item",
                        lambda item, **kw: {"ok": False, "reason": "sync_in_progress",
                                            "item_id": item})
    esperado = {None: None, "dono": user_id, "outro": user_id + 7}[expected]
    asyncio.run(of_routes._run_pluggy_sync_bg(c["item"], esperado))
    assert (_linha(c["id"])["status_reason"] == "read_failed") is marca


# ── a retentativa que encontra o item em voo NÃO marca `_DIRTY` (Codex, #727) ─────────
# O `_DIRTY` não tem dono: a rodada suja roda sem `expected_user_id` e sem
# `filtrar_por_acesso` (é a semântica do webhook). Se a retentativa o marcasse, a rodada
# extra sincronizaria depois de o dono perder o acesso, ou o OUTRO dono de uma readoção.
# A retentativa é melhor esforço: o próximo tique reavalia a elegibilidade e o acesso.

def _em_voo_e_depois(user_id, amb, monkeypatch, *, muda, quem_encontra="retentativa"):
    """O item está em `_INFLIGHT` (um sync bloqueado, como o do webhook). Chega `quem_encontra`
    (a retentativa ou um webhook) e o item é coalescido; `muda()` altera o mundo; o sync em
    voo é solto. Devolve `(resumo da retentativa ou None, dirty marcado, leituras de contas)`."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    c = _nova(user_id, reason="read_failed")
    solta, dentro, n = threading.Event(), threading.Event(), {"v": 0}

    def _hook(_item):
        n["v"] += 1
        if n["v"] == 1:          # só o 1º sync (o em voo) bloqueia; a rodada suja não
            dentro.set()
            solta.wait(10)

    amb.pluggy.durante_contas = _hook
    saida = {}

    async def _main():
        em_voo = of_routes._schedule_pluggy_sync(c["item"])   # o sync em voo
        for _ in range(200):
            if dentro.is_set():
                break
            await asyncio.sleep(0.02)
        if quem_encontra == "retentativa":
            saida["resumo"] = await amb.orq.retentar_leituras(prazo_sec=1.0)
        else:
            assert of_routes._schedule_pluggy_sync(c["item"]) is None     # webhook
        saida["dirty"] = c["item"] in of_routes._DIRTY
        muda(c)
        solta.set()
        await em_voo
        for _ in range(100):                      # espera uma eventual rodada suja acabar
            if c["item"] not in of_routes._INFLIGHT:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)

    asyncio.run(_main())
    return c, saida.get("resumo"), saida["dirty"], amb.pluggy.contas[c["item"]]


def _expira_o_plano(user_id):
    def _f(_c):
        with get_conn() as conn:
            conn.execute("update auth_accounts set plan='free' where user_id=%s", (user_id,))
            conn.commit()
        invalidate_auth_user_cache(user_id)
    return _f


def _readota_para(amb, outro):
    def _f(c):
        db.ensure_user(outro)
        amb.meus.add(outro)
        with get_conn() as conn:
            conn.execute("delete from open_finance_connections where id=%s", (c["id"],))
            conn.commit()
        _nova(outro, reason="read_failed", item=c["item"])
    return _f


@pytest.mark.parametrize("mundo", ["acesso_expira", "readocao"])
def test_retentativa_que_encontra_o_item_em_voo_nao_marca_dirty_nem_relê(
        user_id, amb, monkeypatch, mundo):
    """Depois de o sync em voo terminar não há leitura extra: nem com o plano do dono
    expirado no meio, nem com a linha readotada por outro dono. O desfecho segue contando
    como coalescido."""
    muda = _expira_o_plano(user_id) if mundo == "acesso_expira" else _readota_para(amb, user_id + 1)
    c, resumo, dirty, leituras = _em_voo_e_depois(user_id, amb, monkeypatch, muda=muda)
    assert resumo["coalescidos"] == 1 and resumo["tentados"] == 1
    assert dirty is False, "a retentativa marcou `_DIRTY`"
    assert leituras == 1, f"{leituras} leituras: a rodada suja sincronizou depois da mudança"


def test_webhook_que_encontra_o_item_em_voo_continua_marcando_dirty_e_relê(
        user_id, amb, monkeypatch):
    """Positivo: a base não muda. O webhook coalesce em `_DIRTY` e a rodada suja roda uma
    vez mais, como sempre (sem dono e sem corte por plano: comportamento da base)."""
    c, _resumo, dirty, leituras = _em_voo_e_depois(
        user_id, amb, monkeypatch, muda=lambda c: None, quem_encontra="webhook")
    assert dirty is True and leituras == 2
    assert of_routes._INFLIGHT == {} and of_routes._DIRTY == set()


def test_webhook_no_meio_de_um_sync_da_retentativa_vira_dirty_e_relê_uma_vez(user_id, amb):
    """C2: o sync em voo é o da PRÓPRIA retentativa e chega um webhook. O webhook marca
    `_DIRTY` e a re-execução cobre o evento (a retentativa não perde nada)."""
    c = _nova(user_id, reason="read_failed")
    solta = threading.Event()
    amb.pluggy.durante_contas = lambda item: solta.wait(5)

    async def _main():
        alvo = asyncio.create_task(amb.orq.retentar_leituras(prazo_sec=3600))
        for _ in range(200):
            if c["item"] in of_routes._INFLIGHT:
                break
            await asyncio.sleep(0.02)
        assert of_routes._schedule_pluggy_sync(c["item"]) is None
        assert c["item"] in of_routes._DIRTY
        amb.pluggy.durante_contas = None
        solta.set()
        await alvo
        for _ in range(100):
            if amb.pluggy.contas[c["item"]] >= 2 and c["item"] not in of_routes._INFLIGHT:
                break
            await asyncio.sleep(0.05)

    asyncio.run(_main())
    assert amb.pluggy.contas[c["item"]] == 2
    assert of_routes._INFLIGHT == {} and of_routes._DIRTY == set()


def test_sync_solto_pelo_prazo_e_coalescido_no_tique_seguinte_nao_ganha_rodada_suja(user_id, amb):
    """O `shield` solta o tique e o sync segue vivo; o tique seguinte o encontra em voo e só
    o conta como coalescido. Liberado, ele NÃO roda de novo (antes a retentativa marcava
    `_DIRTY` e havia uma 2ª leitura); o webhook, esse sim, ganha a rodada suja
    (`test_webhook_que_encontra_o_item_em_voo_continua_marcando_dirty_e_relê`)."""
    c = _nova(user_id, reason="read_failed")
    solta = threading.Event()
    amb.pluggy.durante_contas = lambda item: solta.wait(10)
    saida = {}

    async def _main():
        saida["r1"] = await amb.orq.retentar_leituras(prazo_sec=0.4)
        t1 = of_routes._INFLIGHT.get(c["item"])
        saida["vivo"] = t1 is not None and not t1.done()
        saida["r2"] = await amb.orq.retentar_leituras(prazo_sec=0.4)
        saida["dirty"] = c["item"] in of_routes._DIRTY
        amb.pluggy.durante_contas = None
        solta.set()
        await t1
        await asyncio.sleep(0.3)

    asyncio.run(_main())
    assert saida["r1"]["interrompido"] == "prazo" and saida["r1"]["pendurados"] == 1
    assert saida["vivo"] and saida["dirty"] is False
    assert saida["r2"]["coalescidos"] == 1 and saida["r2"]["tentados"] == 1
    assert amb.pluggy.contas[c["item"]] == 1
    assert of_routes._INFLIGHT == {} and of_routes._DIRTY == set()
