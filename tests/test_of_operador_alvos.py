"""PR-E — as guardas de escrita de `scripts/of_itens_operador.py`.

A Pluggy é dublada (`delete_pluggy_item` do script); registry, conexão e lock
são REAIS. Controles (medidos, ver o relato da PR):
  • negativo da guarda de estado — trocar `atual != estado_esperado` por `False`:
    `test_estado_divergente_recusa` fica vermelho (DELETE chamado);
  • negativo da conexão — tirar a checagem de conexão de `classifica_item`:
    `test_conexao_viva_recusa` e `test_conexao_gravada_sob_o_lock_e_vista` ficam
    vermelhos (sem ela o item sai INTERROMPIDO e casa com `--estado`);
  • negativo do lock — reclassificar ANTES de pegar o lock:
    `test_conexao_gravada_sob_o_lock_e_vista` vermelho;
  • negativo da caixa — tirar o `lower()` da checagem de conexão:
    `test_conexao_viva_em_outra_caixa_e_vista_mesmo_com_rastro_exato` vermelho
    (o do UUID maiúsculo sem rastro próprio fica verde pelo DESCONHECIDO);
  • positivo — `test_estado_certo_apaga_uma_vez_e_deixa_rastro`.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

import db
from db.connection import get_conn
from db.open_finance_state import pluggy_item_lock
from scripts import of_itens_operador as op
from test_of_webhook_adopt_guards import _limpa_item

_PRAZO = 8.0   # teto de toda espera deste arquivo: sonda pendurada trava o runner


@pytest.fixture()
def item():
    i = f"op-{uuid.uuid4().hex[:12]}"
    yield i
    _limpa_item(i)


@pytest.fixture()
def deletados(monkeypatch):
    chamados: list[str] = []
    monkeypatch.setattr(op, "delete_pluggy_item", lambda i, api_key=None: chamados.append(i))
    return chamados


def _interrompido(uid: int, i: str) -> None:
    db.register_item(uid, provider_item_id=i, origin="pluggy_item")


def _conecta(uid: int, i: str) -> None:
    db.save_pluggy_open_finance_item(
        uid, {"id": i, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})


def _origens(i: str) -> list[tuple]:
    with get_conn() as c:
        rows = c.execute("select origin, user_id from open_finance_item_registry"
                         " where provider_item_id = %s order by id", (i,)).fetchall()
    return [(r["origin"], r["user_id"]) for r in rows]


# ── parse: rc=2 antes de qualquer leitura ────────────────────────────────────

@pytest.mark.parametrize("ruim", ["", "12345", "a/b"])
def test_item_ruim_morre_no_parse_sem_abrir_banco(ruim, monkeypatch):
    for nome in ("apagar", "detalhe", "listar"):
        monkeypatch.setattr(op, nome, lambda *a, **k: pytest.fail("abriu o banco"))
    with pytest.raises(SystemExit) as e:
        op.main(["--item", ruim, "--apagar", "--estado", "INTERROMPIDO", "--apply"])
    assert e.value.code == 2


@pytest.mark.parametrize("argv", [
    ["--apagar", "--estado", "INTERROMPIDO", "--apply"],          # sem --item
    ["--item", "abc-1", "--apagar", "--estado", "INTERROMPIDO"],  # sem --apply
    ["--item", "abc-1", "--apagar", "--apply"],                   # sem --estado
    ["--item", "abc-1", "--apply"],                               # --apply sem --apagar
    ["--item", "abc-1", "--apagar", "--estado", "CONECTADO", "--apply"],
])
def test_escrita_incompleta_e_erro(argv, monkeypatch):
    monkeypatch.setattr(op, "apagar", lambda *a, **k: pytest.fail("escreveu"))
    with pytest.raises(SystemExit) as e:
        op.main(argv)
    assert e.value.code == 2


# ── as guardas da escrita ────────────────────────────────────────────────────

def test_estado_certo_apaga_uma_vez_e_deixa_rastro(user_id, item, deletados):
    _interrompido(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO", "--apply"]) == 0
    assert deletados == [item]
    assert _origens(item)[-1] == ("operator_delete", None)


def test_estado_divergente_recusa(user_id, item, deletados):
    _interrompido(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "REMOVIDO", "--apply"]) == 1
    assert deletados == []
    assert [o for o, _ in _origens(item)] == ["pluggy_item"], "recusa não grava rastro"


def test_conexao_viva_recusa(user_id, item, deletados):
    _interrompido(user_id, item)
    _conecta(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO", "--apply"]) == 1
    assert deletados == []


def test_falha_da_pluggy_nao_grava_operator_delete(user_id, item, monkeypatch):
    from core.services.pluggy import PluggyApiError

    def _503(i, api_key=None):
        raise PluggyApiError("fora", status_code=503)

    monkeypatch.setattr(op, "delete_pluggy_item", _503)
    _interrompido(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO", "--apply"]) == 1
    assert [o for o, _ in _origens(item)] == ["pluggy_item"]


def _esperando_advisory(prazo: float) -> bool:
    """Alguém está BLOQUEADO num advisory lock (o script, na fila do item)."""
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        with get_conn() as c:
            if c.execute("select 1 from pg_locks where locktype = 'advisory'"
                         " and not granted limit 1").fetchone():
                return True
        time.sleep(0.05)
    return False


def test_conexao_gravada_sob_o_lock_e_vista(user_id, item, deletados, monkeypatch):
    """O POST grava a conexão SEGURANDO o lock do item; o script que chegou na
    fila tem de enxergá-la quando o lock for solto — e recusar."""
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", str(int(_PRAZO * 1000)))
    _interrompido(user_id, item)
    segurando, soltar = threading.Event(), threading.Event()
    rc: list[int] = []

    def _post():
        with pluggy_item_lock(item) as ok:
            assert ok
            segurando.set()
            soltar.wait(_PRAZO)
            _conecta(user_id, item)

    def _script():
        rc.append(op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO",
                           "--apply"]))

    t_post = threading.Thread(target=_post, daemon=True)
    t_post.start()
    assert segurando.wait(_PRAZO), "o lock do POST não foi pego"
    t_script = threading.Thread(target=_script, daemon=True)
    t_script.start()
    try:
        assert _esperando_advisory(_PRAZO), "o script não entrou na fila do lock"
    finally:
        soltar.set()
        t_post.join(_PRAZO)
        t_script.join(_PRAZO)
    assert not t_script.is_alive(), "script pendurado"
    assert rc == [1] and deletados == [], f"rc={rc} deletados={deletados}"


# ── detalhe (--item): o GET na Pluggy e o dono remoto ───────────────────────

def test_detalhe_mostra_dono_remoto_e_prescreve_o_estado(user_id, item, monkeypatch, capsys):
    from core.services.pluggy import PluggyApiError

    db.register_item(None, provider_item_id=item, origin="webhook")
    monkeypatch.setattr(op, "get_pluggy_item", lambda i: {
        "status": "UPDATED", "clientUserId": "987654321989", "connector": {"name": "Nubank"}})
    op.main(["--item", item])
    saida = capsys.readouterr().out
    assert "NUNCA_ATRIBUIDO" in saida and "NÃO existe" in saida
    assert f"--item {item} --apagar --estado NUNCA_ATRIBUIDO --apply" in saida

    def _404(i):
        raise PluggyApiError("x", status_code=404)

    monkeypatch.setattr(op, "get_pluggy_item", _404)
    op.main(["--item", item])
    assert "404" in capsys.readouterr().out


# ── rodada 2: id desconhecido, caixa, falhas de rede e do rastro ────────────

def test_id_desconhecido_nao_recebe_delete(item, deletados):
    """Id fora do registry e dos logs virava NUNCA_ATRIBUIDO e era apagado."""
    assert op.main(["--item", item, "--apagar", "--estado", "NUNCA_ATRIBUIDO",
                    "--apply"]) == 1
    assert deletados == [] and _origens(item) == []


def test_uuid_maiusculo_de_item_conectado_nao_recebe_delete(user_id, deletados):
    i = str(uuid.uuid4())
    try:
        _interrompido(user_id, i)
        _conecta(user_id, i)
        assert op.main(["--item", i.upper(), "--apagar", "--estado", "NUNCA_ATRIBUIDO",
                        "--apply"]) == 1
        assert deletados == []
    finally:
        _limpa_item(i)
        _limpa_item(i.upper())


def test_conexao_viva_em_outra_caixa_e_vista_mesmo_com_rastro_exato(user_id, deletados):
    """Discrimina o `lower()` sozinho: o id MAIÚSCULO tem linha própria no
    registry (não é DESCONHECIDO) e a conexão é do minúsculo."""
    i = str(uuid.uuid4())
    try:
        db.register_item(None, provider_item_id=i.upper(), origin="webhook")
        _conecta(user_id, i)
        assert op.main(["--item", i.upper(), "--apagar", "--estado", "NUNCA_ATRIBUIDO",
                        "--apply"]) == 1
        assert deletados == []
    finally:
        _limpa_item(i)
        _limpa_item(i.upper())


def _falhas_pluggy():
    import httpx

    from core.services.pluggy import PluggyConfigError
    return [httpx.ConnectTimeout("t"), httpx.ConnectError("c"), httpx.ReadTimeout("r"),
            PluggyConfigError("sem credencial")]


@pytest.mark.parametrize("exc", _falhas_pluggy(), ids=lambda e: type(e).__name__)
def test_falha_de_rede_ou_credencial_no_delete_e_rc_1_sem_rastro(
        user_id, item, monkeypatch, exc):
    def _boom(i, api_key=None):
        raise exc

    monkeypatch.setattr(op, "delete_pluggy_item", _boom)
    _interrompido(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO", "--apply"]) == 1
    assert [o for o, _ in _origens(item)] == ["pluggy_item"]


@pytest.mark.parametrize("exc", _falhas_pluggy(), ids=lambda e: type(e).__name__)
def test_falha_de_rede_ou_credencial_no_get_do_detalhe_e_rc_1(
        user_id, item, monkeypatch, capsys, exc):
    def _boom(i):
        raise exc

    monkeypatch.setattr(op, "get_pluggy_item", _boom)
    _interrompido(user_id, item)
    assert op.main(["--item", item]) == 1
    assert "Tente de novo" in capsys.readouterr().out


def test_delete_ok_e_rastro_falho_avisa_que_apagou(user_id, item, deletados,
                                                   monkeypatch, capsys):
    def _quebra(*a, **k):
        raise RuntimeError("registry fora do ar")

    monkeypatch.setattr(op, "register_item", _quebra)
    _interrompido(user_id, item)
    assert op.main(["--item", item, "--apagar", "--estado", "INTERROMPIDO", "--apply"]) == 1
    assert deletados == [item]
    assert "APAGADO na Pluggy" in capsys.readouterr().out
