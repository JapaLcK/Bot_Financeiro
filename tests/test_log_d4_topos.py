"""PR-9c1 da fatia D (#657, D4): os topos de tarefa de fundo e o boot do monólito sem `str(exc)`.

Os topos de tarefa (`_TOPOS`) são closures do `lifespan` e passam a sair por `falha_de_tarefa` (P3 b): a causa
(`motivo=… sqlstate=… status=…`) e os quadros da pilha, sem a linha final. O `_startup_optional`
e o health check do banco usam `causa_segura` (uma linha, sem quadros). Closure não se chama de
teste, então o app sobe de verdade pela sonda (`tests/_lifespan_probe.py`), com cada alvo
levantando um 22P02 com o marcador na mensagem.

Pilha só na 1ª falha seguida (P11, P12): a mesma causa, da mesma origem, na mesma tag volta a dar
só a linha `motivo=`, até a assinatura mudar ou passar 1 hora. A sonda roda num processo novo, então cada tag começa limpa e
falha uma vez: os quadros de todos saem.

O que a sonda NÃO alcança: o `sleep` depois da falha nos laços (60 s a 6 h, longe da janela) e o
fato de as 13 tarefas de uma volta só ficarem paradas depois do print. Os dois são desfecho e o
diff não os toca; quem confere é a leitura do diff, linha a linha.

CLASSE CEGA: a forma da linha no log do Railway e o volume de log dos laços em falha.
"""
from __future__ import annotations

import asyncio
import os
import threading
import types

import httpx
import psycopg
import pytest

import core.observability as observability
import frontend.finance_bot_websocket_custom as dashboard
from _lifespan_probe import sondar
from db.connection import get_conn
from _log_dono_helpers import _ALHEIO, _erro_real, _marcador

CAUSA = "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"

# rótulo → (alvo que a closure chama, tag do print). Os dois do monólito são importados no topo
# dele, então o nome que vale é o do monólito; os outros são import tardio dentro da closure.
_TOPOS = {
    "wa_worker": ("adapters.whatsapp.wa_app:_worker_loop", "[wa_worker]"),
    "wa_daily": ("adapters.whatsapp.wa_app:_daily_report_loop", "[wa_daily]"),
    "wa_bill_reminders": ("adapters.whatsapp.wa_app:_bill_reminder_loop", "[wa_bill_reminders]"),
    "wa_periodic": ("adapters.whatsapp.wa_app:_periodic_report_loop", "[wa_periodic]"),
    "engagement": ("core.services.engagement_scheduler:run_engagement_loop", "[engagement]"),
    "investment_accrual": ("core.services.investment_scheduler:run_investment_accrual_loop",
                           "[investment_accrual]"),
    "patrimonio_foto": ("core.services.patrimonio_foto:run_patrimonio_foto_loop", "[patrimonio_foto]"),
    "recurring_charger": ("core.services.recurring_charger:run_recurring_charger_loop", "[recurring_charger]"),
    "proactive_ai": ("core.services.proactive_ai_scheduler:run_proactive_ai_loop", "[proactive_ai]"),
    "news_bot": ("core.services.news_bot:run_news_loop", "[news_bot]"),
    "agents": ("core.services.piggy_agents:run_agents_loop", "[agents]"),
    "login_events_retention": ("core.services.login_events_retention:run_login_events_retention_loop",
                               "[login_events_retention]"),
    "table_cleanup": ("core.services.table_cleanup:run_table_cleanup_loop", "[table_cleanup]"),
    "plan_grants": ("core.services.billing_access:reprojetar_grants_recentes", "[plan_grants]"),
    "pix": ("core.services.pix_sweeps:drenar_pendentes", "[pix]"),
    "ebook": ("core.services.ebook_entrega:entregar_pendentes", "[ebook]"),
    "stripe_email": ("core.services.stripe_email_sync:sincronizar_pendentes", "[stripe_email]"),
    "remarketing": ("core.services.remarketing:rodar_uma_vez", "[remarketing]"),
    "afiliado_avisos": ("core.services.afiliado_reembolso:reenviar_avisos_pendentes", "[afiliado_avisos]"),
    "account_deletion": ("frontend.finance_bot_websocket_custom:process_due_account_deletions",
                         "[account_deletion]"),
    "of_trial_expiry": ("core.services.open_finance_trial_expiry:enforce_of_bank_limits", "[of_trial_expiry]"),
    "open_finance_proactive": ("core.services.open_finance_proactive:run_salary_notifications",
                               "[open_finance_proactive]"),
}


def test_topos_de_tarefa_imprimem_causa_e_quadros_sem_a_linha_final():
    """Cada topo passa por `falha_de_tarefa` (contado pelo embrulho da sonda), imprime a causa
    seguida dos quadros, e o marcador da mensagem não sai. O boot segue depois do
    `_startup_optional` falhar: as tarefas chamadas depois dele também rodaram."""
    marcador = _marcador()
    alvos = {r: alvo for r, (alvo, _) in _TOPOS.items()}
    alvos["startup_optional"] = "frontend.finance_bot_websocket_custom:log_admin_startup_warnings"
    resultado, diagnostico = sondar(alvos, levanta=marcador, falhas=len(_TOPOS),
                                    env={"OF_PROACTIVE_ENABLED": "1"})
    assert resultado, f"subprocesso não chegou ao fim:\n{diagnostico}"
    erro = resultado["stderr"]
    assert marcador not in erro, f"o texto da exceção saiu no log:\n{erro}"
    # Positivo: o boot seguiu e cada alvo foi chamado.
    assert all(resultado[r] for r in alvos), {r: resultado[r] for r in alvos}
    assert sorted(resultado["falhas"]) == sorted(t for _, t in _TOPOS.values()), (
        f"topo que não passou por `falha_de_tarefa`: {resultado['falhas']}\n{diagnostico}")
    linhas = erro.splitlines()
    for _, tag in _TOPOS.values():
        cabeca = f"{tag} erro: {CAUSA}"
        assert cabeca in linhas, f"sem {cabeca!r}:\n{erro}"
        assert linhas[linhas.index(cabeca) + 1].startswith('  File "'), f"{tag} sem os quadros:\n{erro}"
    assert f"WARNING: Startup ignorou 'avisos administrativos de startup': {CAUSA}" in linhas, erro


def test_banco_fora_no_boot_imprime_a_causa_e_relanca(monkeypatch, capsys):
    """O health check do banco falha: a linha leva a causa, não o texto, e o boot morre com a
    MESMA exceção (o `raise` não muda)."""
    erro = _erro_real()   # antes de dublar: ele próprio usa o banco

    async def _db_fora():
        raise erro

    monkeypatch.setattr(dashboard, "db_connect", _db_fora)

    async def _sobe():
        async with dashboard.lifespan(dashboard.app):
            pass

    with pytest.raises(psycopg.errors.InvalidTextRepresentation) as levantou:
        asyncio.run(_sobe())
    assert levantou.value is erro
    saida = capsys.readouterr().err
    assert f"ERROR: Database connection failed: {CAUSA}" in saida.splitlines(), saida
    assert str(_ALHEIO) not in saida, saida


def _levantada(tipo=psycopg.errors.InvalidTextRepresentation):
    """Com `__traceback__`, como no `except` de um topo, e o alheio na mensagem."""
    try:
        raise tipo(f"invalid input syntax for type integer: \"x{_ALHEIO}\"")
    except Exception as e:  # noqa: BLE001
        return e


@pytest.fixture
def relogio(monkeypatch):
    """Estado da P11 limpo e relógio dublado (só o de `observability`, não o `time` do processo)."""
    agora = [1000.0]
    monkeypatch.setattr(observability, "_ultima_pilha", {})
    monkeypatch.setattr(observability, "time", types.SimpleNamespace(monotonic=lambda: agora[0]))
    return agora


def _uma_falha(capsys, tag, exc) -> str:
    observability.falha_de_tarefa(tag, exc)
    saida = capsys.readouterr()
    assert saida.out == "" and str(_ALHEIO) not in saida.err, saida
    return saida.err


def _com_quadros(err: str, tag: str, causa: str = CAUSA) -> bool:
    assert err.startswith(f"{tag} erro: {causa}\n"), err
    return '\n  File "' in err


def test_mesma_causa_na_mesma_tag_tem_pilha_so_na_primeira(relogio, capsys):
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"), "a 1ª falha perdeu a pilha"
    assert _uma_falha(capsys, "[t]", _levantada()) == f"[t] erro: {CAUSA}\n"
    relogio[0] += observability._PILHA_A_CADA - 1
    assert _uma_falha(capsys, "[t]", _levantada()) == f"[t] erro: {CAUSA}\n"


def _dreno():
    raise psycopg.errors.InvalidTextRepresentation(f"invalid input syntax for type integer: \"x{_ALHEIO}\"")


def _purga():
    raise psycopg.errors.InvalidTextRepresentation(f"invalid input syntax for type integer: \"x{_ALHEIO}\"")


def _pela_mesma_volta(passo):
    """Os dois passos pela MESMA linha de fora, como no `[pix]`: só o quadro mais interno os separa."""
    try:
        passo()
    except Exception as e:  # noqa: BLE001
        return e


def test_mesma_causa_de_outra_origem_traz_a_propria_pilha(relogio, capsys):
    assert _com_quadros(_uma_falha(capsys, "[t]", _pela_mesma_volta(_dreno)), "[t]")
    err = _uma_falha(capsys, "[t]", _pela_mesma_volta(_purga))
    assert _com_quadros(err, "[t]") and ", in _purga\n" in err, "a 2ª origem herdou a 1ª pilha"
    assert not _com_quadros(_uma_falha(capsys, "[t]", _pela_mesma_volta(_purga)), "[t]"), "a mesma origem repetiu"


def _cast_no_dreno():
    with get_conn() as c:
        c.execute("select %s::int", (f"x{_ALHEIO}",))


def _cast_na_purga():
    with get_conn() as c:
        c.execute("select %s::int", (f"x{_ALHEIO}",))


@pytest.mark.parametrize("raiz", [None, os.sep], ids=["raiz_do_repo", "venv_dentro_da_raiz"])
def test_erro_do_banco_de_duas_funcoes_nossas_traz_as_duas_pilhas(relogio, capsys, monkeypatch, raiz):
    """22P02 do Postgres de verdade: o quadro mais interno de todos é do psycopg, IGUAL nas duas; a
    origem que separa é a linha nossa que chamou o banco. Com a raiz em `/`, o psycopg fica "dentro"
    dela, como o `.venv` do checkout principal: só o filtro de `site-packages` o tira."""
    if raiz:
        monkeypatch.setattr(observability, "_RAIZ", raiz)
    erros = [_pela_mesma_volta(f) for f in (_cast_no_dreno, _cast_na_purga, _cast_na_purga)]
    assert all(isinstance(e, psycopg.errors.InvalidTextRepresentation) for e in erros), erros
    saidas = [_uma_falha(capsys, "[pix]", e) for e in erros]
    assert _com_quadros(saidas[0], "[pix]"), saidas[0]
    assert _com_quadros(saidas[1], "[pix]") and ", in _cast_na_purga\n" in saidas[1], "a purga herdou a pilha do dreno"
    assert saidas[2] == f"[pix] erro: {CAUSA}\n", "a mesma origem repetiu a pilha"


def test_banco_fora_mantem_a_assinatura_entre_voltas(relogio, capsys):
    """`PoolTimeout` (porta fechada): a 2ª volta do mesmo laço sai com 1 linha."""
    from psycopg_pool import ConnectionPool, PoolTimeout

    with ConnectionPool("postgresql://127.0.0.1:1/x", min_size=1, timeout=0.2) as pool:
        def _volta():
            with pool.connection() as c:
                c.execute("select 1")
        erros = [_pela_mesma_volta(_volta) for _ in range(2)]
    capsys.readouterr()   # o aviso do pool ("error connecting") não é o que se mede
    assert all(isinstance(e, PoolTimeout) for e in erros), erros
    causa = "motivo=PoolTimeout sqlstate=None status=None"
    assert _com_quadros(_uma_falha(capsys, "[account_deletion]", erros[0]), "[account_deletion]", causa)
    assert _uma_falha(capsys, "[account_deletion]", erros[1]) == f"[account_deletion] erro: {causa}\n"


def _comum():
    raise psycopg.errors.InvalidTextRepresentation(f"invalid input syntax for type integer: \"x{_ALHEIO}\"")


def _dreno_comum():
    _comum()


def _purga_comum():
    _comum()


def test_helper_comum_de_dois_caminhos_traz_as_duas_pilhas(relogio, capsys):
    """A linha nossa mais interna é a MESMA (`_comum`); o que separa é quem a chamou."""
    erros = [_pela_mesma_volta(f) for f in (_dreno_comum, _purga_comum, _purga_comum)]
    saidas = [_uma_falha(capsys, "[pix]", e) for e in erros]
    assert _com_quadros(saidas[0], "[pix]"), saidas[0]
    assert _com_quadros(saidas[1], "[pix]") and ", in _purga_comum\n" in saidas[1], "a purga herdou a pilha do dreno"
    assert saidas[2] == f"[pix] erro: {CAUSA}\n", "a mesma chamada repetiu a pilha"


def _embrulha(passo):
    """`raise … from`: os quadros de fora são os mesmos; o dreno e a purga só aparecem na causa."""
    try:
        passo()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("embrulho") from e


def test_a_causa_encadeada_entra_na_assinatura(relogio, capsys):
    erros = [_pela_mesma_volta(lambda f=f: _embrulha(f)) for f in (_dreno, _purga, _purga)]
    causa = "motivo=RuntimeError sqlstate=None status=None"
    saidas = [_uma_falha(capsys, "[t]", e) for e in erros]
    assert _com_quadros(saidas[0], "[t]", causa) and _com_quadros(saidas[1], "[t]", causa), "a causa ficou de fora"
    assert saidas[2] == f"[t] erro: {causa}\n"


def test_cadeia_em_ciclo_termina(relogio, capsys):
    """Numa thread com prazo: sem a proteção o laço não termina, e o teste tem de REPROVAR, não travar."""
    a, b = _levantada(), _levantada(ValueError)
    a.__context__, b.__context__ = b, a
    t = threading.Thread(target=observability._origem, args=(a,), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "_origem não terminou numa cadeia em ciclo"
    assert _com_quadros(_uma_falha(capsys, "[t]", a), "[t]")


def test_mesmo_objeto_relancado_a_cada_volta_tem_pilha_so_na_primeira(relogio, capsys):
    """Um `Future` que falhou e é aguardado de novo a cada volta: relançar o MESMO objeto acrescenta os
    quadros de novo ao traceback, e o caminho é o mesmo."""
    erro = _levantada()

    def _de_novo():
        raise erro

    saidas = [_uma_falha(capsys, "[t]", _pela_mesma_volta(_de_novo)) for _ in range(4)]
    assert _com_quadros(saidas[0], "[t]"), saidas[0]
    assert saidas[1:] == [f"[t] erro: {CAUSA}\n"] * 3, saidas[1:]


def test_sem_traceback_vale_so_a_causa(relogio, capsys):
    exc = psycopg.errors.InvalidTextRepresentation("x")   # nunca levantada: `__traceback__` é None
    assert _uma_falha(capsys, "[t]", exc) == f"[t] erro: {CAUSA}\n"   # os quadros de nada são vazios
    assert observability._ultima_pilha["[t]"][0] == (CAUSA, None)
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"), "com traceback é outra assinatura"


def test_causa_nova_tag_nova_ou_uma_hora_trazem_a_pilha_de_volta(relogio, capsys):
    _uma_falha(capsys, "[t]", _levantada())
    assert _com_quadros(_uma_falha(capsys, "[u]", _levantada()), "[u]"), "a tag [u] herdou o estado da [t]"
    valor = "motivo=ValueError sqlstate=None status=None"
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada(ValueError)), "[t]", valor), "causa nova sem pilha"
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"), "a volta à 1ª causa é causa nova"
    assert not _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]")
    relogio[0] += observability._PILHA_A_CADA
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"), "1 hora depois, sem pilha"
    assert not _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"), "o relógio da tag não andou"


def test_falha_a_cada_10_s_traz_a_pilha_de_volta_depois_de_1_hora(relogio, capsys):
    """A janela conta da última PILHA, não da última falha: renovada a cada falha, um laço de 10 s
    nunca mais mostraria a pilha."""
    assert _com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]")
    com = []
    for _ in range(int(observability._PILHA_A_CADA // 10) + 6):
        relogio[0] += 10
        com.append(_com_quadros(_uma_falha(capsys, "[t]", _levantada()), "[t]"))
    assert com.count(True) == 1 and com.index(True) == observability._PILHA_A_CADA // 10 - 1, com


def _http(status: int) -> httpx.HTTPStatusError:
    try:
        raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"),
                                    response=httpx.Response(status))
    except httpx.HTTPStatusError as e:
        return e


def test_mesmo_tipo_com_status_diferente_e_causa_nova(relogio, capsys):
    causa = "motivo=HTTPStatusError sqlstate=None status={}"
    assert _com_quadros(_uma_falha(capsys, "[t]", _http(500)), "[t]", causa.format(500))
    assert not _com_quadros(_uma_falha(capsys, "[t]", _http(500)), "[t]", causa.format(500))
    assert _com_quadros(_uma_falha(capsys, "[t]", _http(503)), "[t]", causa.format(503)), "status novo sem pilha"
