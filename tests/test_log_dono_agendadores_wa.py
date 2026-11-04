"""#657 C2 (PR-7 do grupo D), os laços do WhatsApp em `adapters/whatsapp/wa_app.py`: a falha
de um usuário grava o dono na coluna `user_id` de `system_event_logs`, só com tipo + sqlstate
(D6) e sem o corpo da Meta (que pode trazer o número), e o laço segue para o próximo usuário.

Cada teste roda o TICK real com A e B, A falhando e B seguindo: a prova é sobre B (recebeu).
Postgres real, o `_DashboardHandler` do root, `build_user_export_zip` e `delete_user_data`
reais. Trocados: o relógio, a lista de usuários, as identidades e o `send_template`. A
exceção de envio tem a forma do `wa_client.py` (`WA send_template failed {status}: {r.text}`).

T10 relatório diário (`_daily_report_tick`: o par logger+evento do envio e o template ausente);
T11 lembrete de contas (`_bill_reminder_tick`: consulta, envio e marcação); T12 resumo semanal
(`_periodic_report_tick`/`_send_periodic_template`/`_liberar_claim_semanal`).
T13 SEM teste de comportamento: `_daily_report_tick` `:501-513` (marcação de lembrete de
cartão) é código morto, porque o `if reminders: … continue` sai antes do `for`. A cobertura
dele é o ratchet de `test_log_dono_na_coluna.py` e o `pyflakes`.

CONTROLES (CLAUDE.md §3), medidos no PR: tirar o `as exc` de `_liberar_claim_semanal` → T12
`release` vermelho (o tick levanta `NameError` e B não recebe); voltar `exc` no par do diário
→ T10 vermelho; voltar `{exc}` no evento do diário → T10 vermelho; tirar um `extra` → o caso
daquele site vermelho. Cada laço roda com a falha no 1º visitado (o laço SEGUE) e no 2º (a linha
é do dono da volta ATUAL, não do primeiro da lista). Positivos: B recebe em todos; a linha de B
sobrevive à exclusão de A.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest

import db
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    FALHA_NO, FONE, _isola_e_apaga, _limpa, _linhas, _marca, _marcador, _nenhuma, _uid, _uma, _violacao,
    banco_real)
from adapters.whatsapp import wa_app as wa
from conftest import usuario_pagante
from core.observability import log_system_event_sync
from test_resumo_semanal import SEGUNDA, _prefs_semanal, _semear, job  # noqa: F401 (job é fixture)

SRC = wa.__name__


def _numero() -> str:
    return "55219" + str(10_000_000 + uuid.uuid4().int % 89_999_999)


def _erro_meta(marcador: str) -> RuntimeError:
    return RuntimeError(f'WA send_template failed 400: {{"error": {{"details": "{FONE} {marcador}"}}}}')


def _contas(monkeypatch, a: int, b: int) -> dict[int, str]:
    nums = {a: _numero(), b: _numero()}
    monkeypatch.setattr(wa, "list_identities_by_user",
                        lambda u: [{"provider": "whatsapp", "external_id": nums[u]}])
    return nums


def _dono(nums: dict[int, str], to: str) -> int:
    return next(u for u, n in nums.items() if to.endswith(n[-8:]))


def _envio(monkeypatch, nums, falha):
    """`send_template` falso: levanta `falha(uid)` quando não é None; devolve os uids que receberam."""
    recebidos: list[int] = []

    def send(to, *a, **k):
        u = _dono(nums, to)
        if (e := falha(u)) is not None:
            raise e
        recebidos.append(u)
    monkeypatch.setattr(wa, "send_template", send)
    return recebidos


# ── T10. relatório diário ─────────────────────────────────────────────────────

def _diario(monkeypatch, falha_no: int = 1) -> tuple[int, int, dict[int, str]]:
    """Prefs, corte do Grátis e claim reais; 23:59, depois de qualquer hora configurada. A é o
    `falha_no`-ésimo da lista."""
    a, b = _uid(), _uid()
    for u in (a, b):
        db.set_daily_report_enabled(u, True)
    for k, v in (("WA_PROACTIVE_TEMPLATE_NAME", "daily_opener"), ("WA_PROACTIVE_TEMPLATE_INCLUDE_REPORT", "0"),
                 ("WA_PROACTIVE_TEMPLATE_STOP_BUTTON", "0")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(wa, "now_tz", lambda: datetime(2026, 10, 7, 23, 59))
    monkeypatch.setattr(wa, "list_users_with_daily_report_enabled", lambda: [a, b] if falha_no == 1 else [b, a])
    monkeypatch.setattr(wa, "build_daily_report_text", lambda uid: "resumo")
    monkeypatch.setattr(wa, "build_due_bill_reminders", lambda uid, today: [])
    return a, b, _contas(monkeypatch, a, b)


@FALHA_NO
def test_t10_diario_a_falha_no_envio_e_b_recebe(monkeypatch, falha_no):
    a, b, nums = _diario(monkeypatch, falha_no)
    marcador = _marcador()
    recebidos = _envio(monkeypatch, nums, lambda u: _erro_meta(marcador) if u == a else None)
    marca = _marca()
    wa._daily_report_tick()

    assert recebidos == [b], recebidos
    da_a = _linhas(marca, "user_id = %s", (a,))
    par = _uma(da_a, "logger.warning", SRC, "WA daily report send error")
    evento = _uma(da_a, "whatsapp_daily_report_send_failed", "wa_app")
    for linha in (par, evento):
        _limpa(linha, marcador, nums[a])
    assert "causa=RuntimeError sqlstate=None" in par["message"], par
    assert "RuntimeError" in evento["message"] and evento["details"]["sqlstate"] is None, evento
    da_b = _linhas(marca, "user_id = %s", (b,))
    ok_b = _uma(da_b, "whatsapp_daily_report_template_sent", "wa_app")
    _nenhuma(da_b, "logger.warning", "WA daily report send error")
    _nenhuma(da_b, "whatsapp_daily_report_send_failed")
    _isola_e_apaga(a, b, {par["id"], evento["id"]}, ok_b["id"], marca)


def test_t10_sem_template_cada_um_tem_a_sua_linha(monkeypatch):
    a, b, nums = _diario(monkeypatch)
    monkeypatch.delenv("WA_PROACTIVE_TEMPLATE_NAME")
    recebidos = _envio(monkeypatch, nums, lambda u: None)
    marca = _marca()
    wa._daily_report_tick()

    assert recebidos == []
    linha_a, linha_b = (_uma(_linhas(marca, "user_id = %s", (u,)), "logger.warning", SRC, "WA daily report skipped")
                        for u in (a, b))
    _isola_e_apaga(a, b, {linha_a["id"]}, linha_b["id"], marca)


# ── T11. lembrete de contas a pagar ──────────────────────────────────────────

_CONTAS = [
    ("consulta", "WA bill reminder query error", "causa=UniqueViolation sqlstate=23505"),
    ("envio", "WA bill reminder send error", "causa=RuntimeError sqlstate=None"),
    ("marcacao", "WA bill reminder mark error", "causa=UniqueViolation sqlstate=23505"),
]


@FALHA_NO
@pytest.mark.parametrize("etapa,prefixo,causa", _CONTAS, ids=[c[0] for c in _CONTAS])
def test_t11_contas_a_falha_numa_etapa_e_b_recebe(monkeypatch, etapa, prefixo, causa, falha_no):
    import db.bills as bills  # o tick importa os três nomes DENTRO da função, de `db.bills`
    a, b = _uid(), _uid()
    nums, marcador = _contas(monkeypatch, a, b), _marcador()
    conta_de = {a: 900_000_001, b: 900_000_002}
    marcadas: list[int] = []
    monkeypatch.delenv("WA_BILL_REMINDER_HOUR", raising=False)
    monkeypatch.setattr(wa, "_bill_reminder_template_config", lambda: {"name": "conta", "language_code": "pt_BR"})
    monkeypatch.setattr(wa, "now_tz", lambda: datetime(2026, 10, 1, 10, 0))
    monkeypatch.setattr(bills, "list_users_with_pending_bills", lambda: [a, b] if falha_no == 1 else [b, a])

    def due(uid, today, days_before):
        if etapa == "consulta" and uid == a:
            raise _violacao(marcador)
        return [{"id": conta_de[uid], "name": "Luz", "amount": 120, "due_date": date(2026, 10, 3)}]

    def marca_enviado(uid, bill_id, today):
        if etapa == "marcacao" and bill_id == conta_de[a]:
            raise _violacao(marcador)
        marcadas.append(bill_id)
    monkeypatch.setattr(bills, "list_due_bill_reminders", due)
    monkeypatch.setattr(bills, "mark_bill_reminder_sent", marca_enviado)
    recebidos = _envio(monkeypatch, nums, lambda u: _erro_meta(marcador) if etapa == "envio" and u == a else None)
    marca = _marca()
    wa._bill_reminder_tick()

    assert b in recebidos and conta_de[b] in marcadas, (recebidos, marcadas)
    linha = _uma(_linhas(marca, "user_id = %s", (a,)), "logger.warning", SRC, prefixo)
    _limpa(linha, marcador, nums[a])
    assert causa in linha["message"], linha
    da_b = _linhas(marca, "user_id = %s", (b,))
    ok_b = _uma(da_b, "whatsapp_bill_reminder_sent", "wa_app")
    _nenhuma(da_b, "logger.warning", prefixo)
    _isola_e_apaga(a, b, {linha["id"]}, ok_b["id"], marca)


# ── T12. resumo semanal ──────────────────────────────────────────────────────

_SEMANAL = [
    ("build", [("logger.error", SRC, "WA weekly report build failed")], "causa=UniqueViolation sqlstate=23505"),
    ("envio", [("logger.warning", SRC, "WA weekly report send error"),
               ("whatsapp_weekly_report_send_failed", "wa_app", "")], "causa=RuntimeError sqlstate=None"),
    ("release", [("logger.error", SRC, "WA weekly report release failed"),
                 ("logger.error", SRC, "WA weekly report build failed")], "causa=UniqueViolation sqlstate=23505"),
]


@FALHA_NO
@pytest.mark.parametrize("etapa,esperadas,causa", _SEMANAL, ids=[s[0] for s in _SEMANAL])
def test_t12_semanal_um_falha_e_o_outro_recebe(job, etapa, esperadas, causa, falha_no):  # noqa: F811
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    for u in (a, b):
        _semear(u)
        db.set_weekly_report_enabled(u, True)
    nums, marcador = _contas(job.mp, a, b), _marcador()
    job.mp.setattr(wa, "log_system_event_sync", log_system_event_sync)  # o `job` o troca por no-op
    # O tick itera um `set`: a ordem não é a da lista. Falha o `falha_no`-ésimo VISITADO (o build
    # roda uma vez por usuário, na ordem da visita, antes do envio e do release dele).
    visitados: list[int] = []
    build_real, release_real = wa.build_weekly_report_summary, wa.release_weekly_report_claim

    def alvo_(uid) -> bool:
        return len(visitados) >= falha_no and visitados[falha_no - 1] == uid

    def build(uid, **kw):
        visitados.append(uid)
        if etapa in ("build", "release") and alvo_(uid):
            raise _violacao(marcador)
        return build_real(uid, **kw)

    def release(uid, period_date):
        if etapa == "release" and alvo_(uid):
            raise _violacao(marcador)
        return release_real(uid, period_date)
    job.mp.setattr(wa, "build_weekly_report_summary", build)
    job.mp.setattr(wa, "release_weekly_report_claim", release)
    recebidos = _envio(job.mp, nums, lambda u: _erro_meta(marcador) if etapa == "envio" and alvo_(u) else None)
    marca = _marca()
    job.tick(a, b)

    assert len(visitados) == 2, visitados
    alvo = visitados[falha_no - 1]
    outro = b if alvo == a else a
    assert recebidos == [outro], (recebidos, alvo)
    linhas = _linhas(marca, "user_id = %s", (alvo,))
    achadas = [_uma(linhas, *e) for e in esperadas]
    for linha in achadas:
        _limpa(linha, marcador, nums[alvo])
        if linha["event_type"].startswith("logger."):
            assert causa in linha["message"], linha
        else:
            assert "RuntimeError" in linha["message"] and linha["details"]["sqlstate"] is None, linha
    if etapa == "build":
        assert _prefs_semanal(alvo) is None, "o claim do que falhou não foi devolvido"
    if etapa == "release":
        assert (alvo, SEGUNDA.date()) in wa._WEEKLY_RELEASE_PENDENTES
    do_outro = _linhas(marca, "user_id = %s", (outro,))
    ok = _uma(do_outro, "whatsapp_weekly_report_template_sent", "wa_app")
    for e in esperadas:
        _nenhuma(do_outro, *e[::2])
    _isola_e_apaga(alvo, outro, {x["id"] for x in achadas}, ok["id"], marca)
