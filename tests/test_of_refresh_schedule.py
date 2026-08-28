"""G3 — quando o refresh roda, e quantas vezes.

Fatos medidos que originaram o grupo: o tick fazia `while True: refresh_all(); sleep()`,
ou seja, refrescava NO BOOT — 15 rodadas em 48h, 10 delas nos 5 minutos seguintes a um
deploy (o Railway sobe container novo a cada deploy). E o intervalo de 6h vivia só na
memória do processo, então reiniciar zerava o cooldown.

CONTROLE NEGATIVO do grupo (MEDIDO, as duas sabotagens juntas → 3 vermelhos):
  • mover o `await asyncio.sleep(interval)` para DEPOIS do request em
    `_open_finance_refresh` → `test_boot_nao_refresca` vermelho;
  • trocar `claim_items_for_refresh` pela listagem sem reserva (o antigo
    `list_pluggy_item_ids`) → `test_cooldown_persistido...` e
    `test_origem_fica_gravada...` vermelhos.
`test_claims_concorrentes...` exercita o `claim` direto (é a primitiva); ele fica
verde nessas duas sabotagens porque não passa pelo chamador.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

import pytest

import db
import core.services.pluggy_sync as ps
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn
from utils_date import _tz


def _conexao(user_id: int, item_id: str) -> dict:
    return db.save_pluggy_open_finance_item(
        user_id,
        {"id": item_id, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}},
    )


def _linha(item_id: str) -> dict:
    rows = db.get_connections_by_item_id(item_id)
    assert len(rows) == 1
    return rows[0]


# ── 16. o boot não refresca ──────────────────────────────────────────────────

def test_boot_nao_refresca(monkeypatch):
    """Sobe a task do tick e avança o relógio ZERO: nada pode sair para a Pluggy."""
    chamadas = []
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    monkeypatch.setattr(ps, "claim_items_for_refresh",
                        lambda **kw: chamadas.append("claim") or [])
    monkeypatch.setattr(ps, "run_of_health_check",
                        lambda **kw: chamadas.append("health") or {})

    dormidas = []

    async def _sleep_falso(segundos):
        dormidas.append(segundos)
        raise asyncio.CancelledError   # corta o loop na PRIMEIRA espera

    async def _corre():
        monkeypatch.setattr(asyncio, "sleep", _sleep_falso)
        with pytest.raises(asyncio.CancelledError):
            await dashboard._open_finance_refresh()

    asyncio.run(_corre())

    assert dormidas == [6 * 60 * 60], "o tick tem que DORMIR antes de qualquer coisa"
    assert chamadas == [], f"nada podia ter rodado no boot: {chamadas}"


# ── 17. dois claims concorrentes: o item sai para UM só ──────────────────────

def test_claims_concorrentes_entregam_o_item_uma_vez_so(user_id):
    _conexao(user_id, "item-claim")
    resultados: list[list] = []

    def _claim():
        resultados.append(db.claim_items_for_refresh(
            cooldown_sec=3600, jitter_pct=0, origin="periodic", limit=10, user_id=user_id,
        ))

    import threading
    t1, t2 = threading.Thread(target=_claim), threading.Thread(target=_claim)
    t1.start(); t2.start(); t1.join(); t2.join()

    ganhos = [r for lote in resultados for r in lote if r["provider_item_id"] == "item-claim"]
    assert len(ganhos) == 1, f"o mesmo item foi reivindicado {len(ganhos)}x"


# ── 18. cooldown persistido ──────────────────────────────────────────────────

def test_cooldown_persistido_barra_o_segundo_tick(user_id, monkeypatch):
    _conexao(user_id, "item-cd")
    patches = []
    monkeypatch.setattr(ps, "update_pluggy_item",
                        lambda item, key=None: patches.append(item) or True)
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    monkeypatch.setenv("OF_REFRESH_MIN_INTERVAL_SEC", "21600")
    monkeypatch.setenv("OF_REFRESH_JITTER_PCT", "0")

    ps.request_pluggy_refresh(origin="periodic", user_id=user_id)
    assert patches == ["item-cd"]

    # segundo tick, dentro do cooldown: zero PATCH
    ps.request_pluggy_refresh(origin="periodic", user_id=user_id)
    assert patches == ["item-cd"], "cooldown não segurou o segundo tick"

    # vencido o cooldown (o relógio de verdade seria 6h; envelhecemos a linha)
    _envelhece("item-cd")
    ps.request_pluggy_refresh(origin="periodic", user_id=user_id)
    assert patches == ["item-cd", "item-cd"]


def _envelhece(item_id: str) -> None:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "update open_finance_connections set next_refresh_at = now() - interval '1 minute'"
                " where provider_item_id=%s",
                (item_id,),
            )
        c.commit()


# ── 19. jitter dentro de ±10% ────────────────────────────────────────────────

@pytest.mark.parametrize("semente", [1, 7, 99])
def test_jitter_fica_dentro_de_dez_por_cento(user_id, semente, monkeypatch):
    item = f"item-jit-{semente}"
    _conexao(user_id, item)
    random.seed(semente)   # o jitter é do Postgres; a semente fixa o resto do teste

    antes = datetime.now(_tz())
    db.claim_items_for_refresh(cooldown_sec=21600, jitter_pct=10, origin="periodic",
                              limit=10, user_id=user_id)
    alvo = _linha(item)["next_refresh_at"]

    delta = (alvo - antes).total_seconds()
    assert 21600 * 0.90 - 5 <= delta <= 21600 * 1.10 + 5, delta


# ── 20. origem ───────────────────────────────────────────────────────────────

def test_origin_startup_nao_dispara_nada(user_id, monkeypatch):
    _conexao(user_id, "item-boot")
    patches = []
    monkeypatch.setattr(ps, "update_pluggy_item", lambda item, key=None: patches.append(item))
    monkeypatch.setattr(ps, "claim_items_for_refresh",
                        lambda **kw: pytest.fail("startup não pode nem reivindicar item"))

    out = ps.request_pluggy_refresh(origin="startup")

    assert out["triggered"] == 0
    assert out["skipped"] == "startup"
    assert patches == []


def test_origem_fica_gravada_na_conexao(user_id, monkeypatch):
    _conexao(user_id, "item-org")
    monkeypatch.setattr(ps, "update_pluggy_item", lambda item, key=None: True)
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)

    ps.request_pluggy_refresh(origin="periodic", user_id=user_id)
    assert _linha("item-org")["last_refresh_origin"] == "periodic"

    _envelhece("item-org")
    ps.request_pluggy_refresh(origin="manual", user_id=user_id)
    assert _linha("item-org")["last_refresh_origin"] == "manual"


# ── job de saúde (é ele que tira do ACTIVE quem perdeu o item) ───────────────

def test_job_de_saude_marca_item_missing_sem_carimbar_sucesso(user_id, monkeypatch):
    """Com o refresh DESLIGADO e sem webhook, este job é a única coisa que
    executa a verificação — é o que faz uma conexão morta sair de ACTIVE."""
    from core.services.pluggy import PluggyApiError

    conexao = _conexao(user_id, "item-saude")
    _marca_sync(conexao["id"])
    antes = _linha("item-saude")["last_sync_at"]

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item",
                        lambda i, k=None: (_ for _ in ()).throw(PluggyApiError("sumiu", status_code=404)))

    out = ps.run_of_health_check()

    assert out["missing"] >= 1
    linha = _linha("item-saude")
    assert linha["status"] == "ERROR"
    assert linha["status_reason"] == "item_missing"
    assert linha["last_sync_at"] == antes, "medir saúde não é sincronizar"


def test_job_de_saude_grava_health_sem_mexer_no_sucesso(user_id, monkeypatch):
    conexao = _conexao(user_id, "item-saude2")
    _marca_sync(conexao["id"])
    antes = _linha("item-saude2")["last_sync_at"]

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "executionStatus": "PARTIAL_SUCCESS",
        "statusDetail": {"creditCards": {"isUpdated": False,
                                         "lastUpdatedAt": "2026-08-12T03:00:00Z"}},
    })

    ps.run_of_health_check()

    linha = _linha("item-saude2")
    assert linha["health"]["stale_products"] == ["CREDIT"]
    # RODADA 4: item vivo passou a sair do resolvedor como ACTIVE mesmo com o
    # espelho vazio (era `None`="não mexe", e "não mexe" nunca tirava um ERROR).
    # O que o job continua não podendo mexer é o SUCESSO — a asserção abaixo.
    assert linha["status"] == "ACTIVE", "item vivo é ACTIVE, não erro"
    assert linha["last_sync_at"] == antes


def _marca_sync(connection_id: int) -> None:
    """Deixa a conexão com um sucesso anterior gravado (o que o job não pode mexer)."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "update open_finance_connections set last_sync_at = now() - interval '2 days',"
                " health = null where id=%s",
                (connection_id,),
            )
        c.commit()


# ── refresh MANUAL: cooldown por item ────────────────────────────────────────
# Antes, o pull-to-refresh do app chamava `loadData` (leitura de snapshot). Agora
# ele chama o /refresh, e sem cooldown cada puxão viraria um PATCH por banco —
# medido pelo Tester: 5 POSTs seguidos = 5 PATCH.
# CONTROLE NEGATIVO: trocar `claim_manual_refresh(...)` por `liberados = items`
# em `refresh_and_sync_pluggy_user` deixa os dois primeiros testes vermelhos.

def _mundo_pluggy(monkeypatch, patches: list):
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "update_pluggy_item",
                        lambda item, key=None: patches.append(item) or True)
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "executionStatus": "SUCCESS",
        "statusDetail": {"accounts": {"isUpdated": True,
                                      "lastUpdatedAt": "2026-08-20T11:00:00Z"}}})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [
        {"id": f"acc-{i}", "name": "Conta", "type": "BANK", "currencyCode": "BRL",
         "balance": "10.00"}])
    monkeypatch.setattr(ps, "list_pluggy_transactions", lambda acc, k=None, **kw: [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)


def test_cinco_puxoes_seguidos_dao_um_patch_so(user_id, monkeypatch):
    _conexao(user_id, "item-ptr")
    patches: list[str] = []
    _mundo_pluggy(monkeypatch, patches)

    saidas = [ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=0) for _ in range(5)]

    assert patches == ["item-ptr"], f"a rajada virou {len(patches)} PATCH: {patches}"
    assert saidas[0]["items"][0]["state"] == "updated"
    estados = [s["items"][0]["state"] for s in saidas[1:]]
    assert estados == ["rate_limited"] * 4, estados
    assert all(s["refreshed"] == 0 for s in saidas[1:]), "puxão em cooldown não pede coleta"
    # rate_limited NÃO é falha: os dados são de segundos atrás.
    assert all(s["ok"] is True for s in saidas), [s["ok"] for s in saidas]


def test_passado_o_cooldown_o_manual_volta_a_pedir(user_id, monkeypatch):
    """CONTROLE POSITIVO: o cooldown é curto e o manual continua furando o tick
    de 6h — o conserto não pode transformar o botão em enfeite."""
    _conexao(user_id, "item-ptr2")
    patches: list[str] = []
    _mundo_pluggy(monkeypatch, patches)

    ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=0)
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update open_finance_connections set last_refresh_requested_at ="
                        " now() - interval '5 minutes' where provider_item_id='item-ptr2'")
        c.commit()
    saida = ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=0)

    assert patches == ["item-ptr2", "item-ptr2"]
    assert saida["items"][0]["state"] == "updated"


def test_conexao_pausada_ou_removida_nao_leva_patch(user_id, monkeypatch):
    """PAUSED (trial vencido) e DELETED: o item nem existe mais na Pluggy. E o
    veredito NÃO pode ser verde — era o caminho pelo qual `state:"paused"` caía
    no default do refreshVerdict e virava "Tudo em dia!"."""
    p1 = _conexao(user_id, "item-pausado")
    p2 = _conexao(user_id, "item-removido")
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update open_finance_connections set status='PAUSED' where id=%s", (p1["id"],))
            cur.execute("update open_finance_connections set status='DELETED' where id=%s", (p2["id"],))
        c.commit()
    patches: list[str] = []
    _mundo_pluggy(monkeypatch, patches)

    saida = ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=0)

    assert patches == [], f"item terminal não pode levar PATCH: {patches}"
    estados = {i["item_id"]: i["state"] for i in saida["items"]}
    assert estados == {"item-pausado": "paused", "item-removido": "removed"}
    assert saida["ok"] is False, "pausado/removido nunca é 'tudo em dia'"


# ── job de saúde: o motivo `item_missing` tem que poder VOLTAR atrás ─────────
# CONTROLE NEGATIVO: voltar `status_reason = coalesce(%s, status_reason)` em
# `mark_sync_result` (ou tirar o `volta` do run_of_health_check) deixa este
# teste vermelho — a tela continua mandando refazer a conexão.

def test_item_que_volta_saudavel_para_de_mandar_refazer_a_conexao(user_id, monkeypatch):
    from core.services.pluggy import PluggyApiError
    from core.services.pluggy_health import connection_ui_state

    _conexao(user_id, "item-404-transitorio")
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("sumiu", status_code=404)))

    ps.run_of_health_check()
    assert connection_ui_state(_linha("item-404-transitorio"))["state"] == "item_missing"

    # 12h depois (o job só remede saúde velha — OF_HEALTH_MAX_AGE_SEC), o item
    # voltou e a passada seguinte confirma que está saudável.
    _envelhece_saude("item-404-transitorio")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "executionStatus": "SUCCESS",
        "statusDetail": {"accounts": {"isUpdated": True,
                                      "lastUpdatedAt": "2026-08-20T11:00:00Z"}}})
    ps.run_of_health_check()

    linha = _linha("item-404-transitorio")
    assert linha["status_reason"] is None, "o motivo tinha que cair quando o item voltou"
    assert connection_ui_state(linha)["state"] != "item_missing"


def _envelhece_saude(item_id: str) -> None:
    """Empurra `health.observed_at` pro passado — o job só remede saúde velha."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "update open_finance_connections"
                " set health = jsonb_set(health, '{observed_at}',"
                "     to_jsonb((now() - interval '2 days')::text))"
                " where provider_item_id=%s",
                (item_id,),
            )
        c.commit()


def test_job_de_saude_nao_apaga_motivo_que_ele_nao_sabe_avaliar(user_id, monkeypatch):
    """CONTROLE POSITIVO/limite: com o espelho AINDA vazio, `no_accounts` fica de
    pé. O job não leu `/accounts` — ele só não desmente o que o espelho confirma.
    Limpar aqui devolveria a mentira do "Atualizado" por outro caminho."""
    conexao = _conexao(user_id, "item-motivo")
    db.mark_sync_result(conexao["id"], ok=False, status_reason="no_accounts")
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}})

    ps.run_of_health_check()

    assert _linha("item-motivo")["status_reason"] == "no_accounts"


# ── disjuntor do job de saúde ────────────────────────────────────────────────
# O job roda ligado por default e ESCREVE status='ERROR' em conexão de usuário.
# Credencial apontando pro client errado → 404 para todos → uma passada marcaria
# a base inteira como item_missing, que é pegajoso.
# CONTROLE NEGATIVO: tirar o bloco do disjuntor faz o primeiro teste ficar
# vermelho (5 conexões viram ERROR).

def test_disjuntor_aborta_passada_com_404_generalizado(user_id, monkeypatch):
    from core.services.pluggy import PluggyApiError

    ids = [f"item-disj-{n}" for n in range(5)]
    for item in ids:
        _conexao(user_id, item)
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("sumiu", status_code=404)))

    out = ps.run_of_health_check()

    assert out["aborted"] == "too_many_missing", out
    assert out["missing"] == 0
    for item in ids:
        linha = _linha(item)
        assert linha["status"] == "UPDATED", f"{item} não podia ter sido escrito"
        assert linha["status_reason"] is None
        assert linha["health"] is None


def test_disjuntor_nao_atrapalha_um_item_realmente_sumido(user_id, monkeypatch):
    """CONTROLE POSITIVO: 1 ausente em 5 está abaixo do limite e continua sendo
    marcado — sem isto o disjuntor viraria um 'nunca marque nada'."""
    from core.services.pluggy import PluggyApiError

    ids = [f"item-mix-{n}" for n in range(5)]
    for item in ids:
        _conexao(user_id, item)

    def _get(i, k=None):
        if i == "item-mix-3":
            raise PluggyApiError("sumiu", status_code=404)
        return {"id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}}

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", _get)

    out = ps.run_of_health_check()

    assert out.get("aborted") is None, out
    assert out["missing"] == 1 and out["checked"] == 4
    assert _linha("item-mix-3")["status_reason"] == "item_missing"
    assert _linha("item-mix-0")["status"] == "ACTIVE", "quem respondeu vivo não vira erro"


# ── RODADA 3: o disjuntor tem que CONVERGIR, e o motivo tem que cair inteiro ──

def test_disjuntor_converge_quando_alguem_responde(user_id, monkeypatch):
    """6 mortos em 10 conexões: 60% > 50%, mas 4 responderam — a credencial está
    boa, logo os 6 sumiram de verdade e TÊM que ser marcados na mesma passada.

    Medido antes do conserto: tick 1 marcava 0 de 6; as 4 sadias saíam do lote
    (saúde fresca) e do tick 2 em diante a amostra era só a dos mortos, 100% >
    50% PARA SEMPRE — as 6 nunca saíam de "Atualizado"."""
    from core.services.pluggy import PluggyApiError

    vivos = [f"item-conv-ok-{n}" for n in range(4)]
    mortos = [f"item-conv-x-{n}" for n in range(6)]
    for item in vivos + mortos:
        _conexao(user_id, item)

    def _get(i, k=None):
        if i in mortos:
            raise PluggyApiError("sumiu", status_code=404)
        return {"id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}}

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", _get)

    out = ps.run_of_health_check()

    assert out.get("aborted") is None, out
    assert (out["checked"], out["missing"]) == (4, 6), out
    assert all(_linha(i)["status_reason"] == "item_missing" for i in mortos)


def test_disjuntor_ainda_abre_quando_ninguem_responde(user_id, monkeypatch):
    """CONTROLE POSITIVO do conserto acima: com credencial errada NINGUÉM
    responde, e aí o disjuntor continua abrindo — o conserto não pode virar um
    'marque sempre'. (É o cenário do `test_disjuntor_aborta...` acima, aqui com
    a condição de saída explícita: `checked == 0`.)"""
    from core.services.pluggy import PluggyApiError

    ids = [f"item-cred-{n}" for n in range(6)]
    for item in ids:
        _conexao(user_id, item)
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("sumiu", status_code=404)))

    out = ps.run_of_health_check()

    assert out["aborted"] == "too_many_missing" and out["checked"] == 0, out
    assert all(_linha(i)["status_reason"] is None for i in ids)


def test_item_que_volta_limpa_status_e_motivo_juntos(user_id, monkeypatch):
    """A6 estava pela metade: o job limpava o motivo e deixava o `status='ERROR'`
    que ele mesmo tinha escrito. Medido: a tela saía de "Refaça a conexão" e
    entrava em "Erro temporário" — para sempre, pelo mesmo motivo."""
    from core.services.pluggy import PluggyApiError
    from core.services.pluggy_health import connection_ui_state

    conexao = _conexao(user_id, "item-volta-inteiro")
    _espelha_uma_conta(conexao["id"])
    _set_last_sync(conexao["id"])   # ONDA 2: voltar ao verde exige sync real no passado
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("sumiu", status_code=404)))
    ps.run_of_health_check()
    assert _linha("item-volta-inteiro")["status"] == "ERROR"

    _envelhece_saude("item-volta-inteiro")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}})
    ps.run_of_health_check()

    linha = _linha("item-volta-inteiro")
    assert (linha["status"], linha["status_reason"]) == ("ACTIVE", None), linha
    assert connection_ui_state(linha)["state"] == "updated"


def test_no_accounts_cai_quando_o_espelho_tem_dado(user_id, monkeypatch):
    """`no_accounts` deixa de ser pegajoso: quem responde se ele ainda vale é o
    ESPELHO (`has_data`, lido na mesma query do job), não a memória."""
    from core.services.pluggy_health import connection_ui_state

    conexao = _conexao(user_id, "item-tinha-dado")
    _espelha_uma_conta(conexao["id"])
    db.mark_sync_result(conexao["id"], ok=False, status="ACTIVE",
                        status_reason="no_accounts", at=None)
    _set_last_sync(conexao["id"])
    assert connection_ui_state(_linha("item-tinha-dado"))["state"] == "no_accounts"

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}})
    ps.run_of_health_check()

    linha = _linha("item-tinha-dado")
    assert linha["status_reason"] is None
    assert connection_ui_state(linha)["state"] == "updated"


def _set_last_sync(connection_id: int) -> None:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update open_finance_connections set last_sync_at=now() where id=%s",
                        (connection_id,))
        c.commit()


def _espelha_uma_conta(connection_id: int) -> None:
    db.save_open_finance_sync(connection_id, [{
        "provider_account_id": f"acc-{connection_id}", "name": "Conta", "type": "BANK",
        "currency": "BRL", "balance": 10, "raw": {}, "transactions": [],
    }])


# ── RODADA 3: uma coluna, dois relógios (§0.7) ──────────────────────────────

def test_tick_periodico_nao_queima_o_cooldown_do_botao(user_id):
    """Medido antes: depois de um tick, `claim_manual_refresh` voltava vazio por
    120s — o usuário apertava "Atualizar" e recebia "já está tudo em dia" sem
    que nada tivesse sido pedido. `last_refresh_requested_at` é o relógio do
    MANUAL; o do tick é `next_refresh_at`."""
    _conexao(user_id, "item-2relogios")

    reivindicados = db.claim_items_for_refresh(
        cooldown_sec=3600, jitter_pct=0, origin="periodic", limit=10, user_id=user_id)
    assert [r["provider_item_id"] for r in reivindicados] == ["item-2relogios"]

    manual = db.claim_manual_refresh(user_id, ["item-2relogios"], cooldown_sec=120)
    assert manual == ["item-2relogios"], "o botão do usuário não pode ficar preso ao tick"

    # CONTROLE POSITIVO: o cooldown do manual continua valendo contra ELE MESMO.
    assert db.claim_manual_refresh(user_id, ["item-2relogios"], cooldown_sec=120) == []


# ── RODADA 4: a metade `has_data=False` do item que volta ───────────────────
# O teste acima (`test_item_que_volta_limpa_status_e_motivo_juntos`) semeava o
# espelho e só exercitava `has_data=True`; a metade sem espelho não tinha teste, e
# era exatamente ela que ficava presa em ERROR (o resolvedor devolvia "não mexe").
# Com `OF_REFRESH_ENABLED` off (default) não há sync completo que a tire depois.
# CONTROLE NEGATIVO (medido): devolver `None` no ramo de espelho vazio de
# `resolve_connection_state` deixa este teste vermelho no par e na UI.

def test_item_que_volta_com_espelho_vazio_tambem_sai_de_error(user_id, monkeypatch):
    from core.services.pluggy import PluggyApiError
    from core.services.pluggy_health import connection_ui_state

    _conexao(user_id, "item-volta-vazio")          # sem `_espelha_uma_conta`: espelho VAZIO
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("sumiu", status_code=404)))
    ps.run_of_health_check()
    assert (_linha("item-volta-vazio")["status"],
            _linha("item-volta-vazio")["status_reason"]) == ("ERROR", "item_missing")

    # o item voltou: três passadas seguidas do job (o repro do defeito ficava em
    # "Erro temporário" da 1ª à 5ª).
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}})
    for _ in range(3):
        _envelhece_saude("item-volta-vazio")
        ps.run_of_health_check()

    linha = _linha("item-volta-vazio")
    assert (linha["status"], linha["status_reason"]) == ("ACTIVE", None), linha
    assert connection_ui_state(linha)["state"] != "error_recoverable"


def test_no_accounts_com_espelho_ainda_vazio_fica_de_pe_e_sem_error(user_id, monkeypatch):
    """A metade `has_data=False` do `test_no_accounts_cai_quando_o_espelho_tem_dado`:
    o motivo continua, o status sai do ERROR, e a tela diz "Sem dados" — não
    "Erro temporário"."""
    from core.services.pluggy_health import connection_ui_state

    conexao = _conexao(user_id, "item-vazio-mesmo")
    db.mark_sync_result(conexao["id"], ok=False, status="ERROR",
                        status_reason="no_accounts", at=None)
    _set_last_sync(conexao["id"])
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": i, "status": "UPDATED", "statusDetail": {"accounts": {"isUpdated": True}}})

    ps.run_of_health_check()

    linha = _linha("item-vazio-mesmo")
    assert (linha["status"], linha["status_reason"]) == ("ACTIVE", "no_accounts"), linha
    ui = connection_ui_state(linha)
    assert (ui["state"], ui["label"]) == ("no_accounts", "Sem dados")


# ── RODADA 4: jitter não pode agendar no PASSADO ───────────────────────────
# Medido com OF_REFRESH_JITTER_PCT=400: 14 de 40 agendamentos caíam no passado e o
# cooldown persistido (a razão de existir da coluna) deixava de valer.
# CONTROLE NEGATIVO (medido): tirar o clamp de `claim_items_for_refresh` deixa
# este teste vermelho.

def test_jitter_absurdo_nunca_agenda_no_passado(user_id):
    for n in range(20):
        _conexao(user_id, f"item-jit-abs-{n}")
    antes = datetime.now(_tz())

    db.claim_items_for_refresh(cooldown_sec=3600, jitter_pct=400, origin="periodic",
                              limit=50, user_id=user_id)

    alvos = [_linha(f"item-jit-abs-{n}")["next_refresh_at"] for n in range(20)]
    passado = [a for a in alvos if a <= antes]
    assert passado == [], f"{len(passado)}/20 agendamentos no passado"
    assert all((a - antes).total_seconds() >= 3600 * 0.5 - 5 for a in alvos), alvos
