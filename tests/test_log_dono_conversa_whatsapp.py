"""#657 C1a (PR-6a do grupo D), ponta a ponta: a falha da CONVERSA grava o dono na coluna
`user_id` de `system_event_logs`, sem o texto cru do erro do banco, e a linha sai na
exportação do titular e some na exclusão da conta (critério da #220).

Tudo real a partir da mensagem: `handle_incoming`/`process_message` com estado no
Postgres, o `_DashboardHandler` de verdade no root (`_configure_root_logger`, sem trocar
o `log_system_event_sync`), `build_user_export_zip` e `delete_user_data` (`db/privacy.py`).
Só o passo que falha é trocado, levantando a `UniqueViolation` que o psycopg produz, com
um marcador e um telefone no `DETAIL`. Só o envio ao WhatsApp é falso.

P2 (c) do dono: na falha de topo fica a pilha (os quadros) + tipo + sqlstate; a linha
final da exceção (com o `DETAIL`) não vai para `details` nem para o stderr.

CONTROLES (CLAUDE.md §3), medidos no PR:
  • tirar o `extra={"user_id": uid_for_log}` do `handle_incoming FAILED` → T1 vermelho
    (a linha do logger não tem a coluna de A, não sai no ZIP e sobrevive à exclusão);
  • voltar `{exc}` na message do `message_processing_failed` → T1 vermelho (marcador);
  • voltar `{e}` no `whatsapp_updates_disable_button_error` → T2 vermelho;
  • voltar o bloco do daily à base, ou tirar o `extra` do tutorial → T4 vermelho;
  • voltar a `format_tb(exc.__traceback__)` (só a exceção de fora) → T5 e T3 encadeado vermelhos.
  • tirar o `try` de dentro do `quadros_da_cadeia` → T6 vermelho (a exceção sobe do `except`).
Positivos: a linha de B sobrevive à exclusão de A; a resposta amigável continua; a
segunda mensagem de A é respondida; a outra conta do PARAR é desligada.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile
from types import SimpleNamespace

import psycopg
import pytest

import core.handle_incoming as hi
import core.pg_text as pg_text
import core.observability as observability
import db
import db.privacy as privacy
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundMessage
from conftest import promote_to_pro
from core.admin_dashboard import ensure_admin_tables
from core.types import IncomingMessage
from db.connection import get_conn
from test_senha_obrigatoria_whatsapp import _manda, enviadas, env  # noqa: F401 (fixtures)
from test_wa_parar import _a_e_b_com_conta, _com_senha_ligada, _opt_out
from utils_text import normalize_text

FONE = "11987654321"
GASTO = "gastei 50,90 no açaí da Dona Zélia"
AMIGAVEL = "Ocorreu um erro interno"


@pytest.fixture(autouse=True)
def banco_real():
    """O `_DashboardHandler` real no root, gravando de fato em `system_event_logs`."""
    asyncio.run(ensure_admin_tables())  # `system_event_logs` não vem de db/schema.py
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler)
               for h in logging.getLogger().handlers), \
        "sem o _DashboardHandler no root nada chega ao banco e o grupo não mede nada"


def _violacao(marcador: str) -> psycopg.errors.UniqueViolation:
    return psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "launches_nota_key"\n'
        f"DETAIL:  Key (user_id, nota)=(1, {marcador} {FONE}) already exists.")


def _levanta(marcador: str):
    def _f(*a, **k):
        raise _violacao(marcador)
    return _f


def _encadeada(marcador: str):
    """`raise X from e` sobre o erro do banco (como db/accounts.py): o quadro do banco e o
    sqlstate só existem na causa, e o texto de fora também traz o DETAIL."""
    def _banco():
        raise _violacao(marcador)

    def _f(*a, **k):
        try:
            _banco()
        except psycopg.Error as e:
            raise RuntimeError(f"falhou: {e}") from e
    return _f


def _uid() -> int:
    """Id abaixo de 2e9: o `_normalize_user_id` comprime os maiores (id de WhatsApp)."""
    uid = 1_000_000 + uuid.uuid4().int % 1_000_000_000
    db.ensure_user(uid)
    promote_to_pro(uid)
    return uid


def _marca() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(id), 0) as m from system_event_logs")
        m = int(cur.fetchone()["m"])
        conn.commit()
    return m


def _linhas(marca: int, where: str = "true", args: tuple = ()) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, event_type, message, source, user_id, details from system_event_logs"
                    f" where id > %s and {where} order by id", (marca, *args))
        r = cur.fetchall()
        conn.commit()
    return [dict(x) for x in r]


def _uma(linhas: list[dict], event_type: str, source: str, prefixo: str = "") -> dict:
    """A linha única do `event_type`, com o `source` exato (o painel filtra e rotula por eles)."""
    achadas = [x for x in linhas if x["event_type"] == event_type and x["message"].startswith(prefixo)]
    assert len(achadas) == 1, (event_type, prefixo, [(x["event_type"], x["message"][:80]) for x in linhas])
    assert achadas[0]["source"] == source, achadas[0]
    return achadas[0]


def _exportados(uid: int) -> tuple[str, set[int]]:
    with zipfile.ZipFile(io.BytesIO(privacy.build_user_export_zip(uid))) as zf:
        bruto = zf.read("dados.json").decode()
    return bruto, {e["id"] for e in json.loads(bruto)["dados"]["eventos_sistema"]}


def _vazou(texto: str, *proibidos: str) -> str | None:
    """O 1º proibido que aparece no texto, comparando NORMALIZADO: o código loga a `nota` já
    passada pelo `normalize_text` ("acai da dona zelia"), sem acento nem maiúscula. Proibido que
    normaliza para vazio casaria com tudo e esconderia os seguintes: falha alto. O `DETAIL:` do
    psycopg vai cru, para não casar com "details"."""
    dobrado = normalize_text(texto)
    for p in (*proibidos, FONE, "already exists"):
        assert normalize_text(p or ""), f"proibido vazio ({p!r}) não confere nada"
        if normalize_text(p) in dobrado:
            return p
    return "DETAIL:" if "DETAIL:" in texto else None


def _sem_vazamento(linha: dict, *proibidos: str) -> None:
    texto = linha["message"] + json.dumps(linha["details"], ensure_ascii=False)
    assert not (p := _vazou(texto, *proibidos)), f"{p!r} vazou em {linha['event_type']}: {texto}"


def _conversa(uid: int, texto: str) -> str:
    outs = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto))
    return "\n".join(o.text for o in outs or [])


# ── T1. handle_incoming real, dois usuários, exportação e exclusão ───────────

def test_falha_da_conversa_tem_dono_sai_na_exportacao_e_some_na_exclusao(monkeypatch):
    a, b = _uid(), _uid()
    mark_a, mark_b = f"MARCADOR-{uuid.uuid4().hex}", f"MARCADOR-{uuid.uuid4().hex}"
    marca, real = _marca(), db.add_launch_and_update_balance

    monkeypatch.setattr(db, "add_launch_and_update_balance", _levanta(mark_b))
    assert AMIGAVEL in _conversa(b, "gastei 12,30 no pão de queijo")
    monkeypatch.setattr(db, "add_launch_and_update_balance", _levanta(mark_a))
    assert AMIGAVEL in _conversa(a, GASTO)

    da_a = _linhas(marca, "user_id = %s", (a,))
    evento = _uma(da_a, "message_processing_failed", "handle_incoming/whatsapp")
    logger = _uma(da_a, "logger.error", "core.handle_incoming", "handle_incoming FAILED")
    for linha in (evento, logger):
        _sem_vazamento(linha, mark_a, "açaí", "Zélia")
    assert "causa=UniqueViolation sqlstate=23505" in logger["message"], logger
    assert evento["details"]["sqlstate"] == "23505" and "UniqueViolation" in evento["message"], evento
    # P2 (c): os quadros da pilha ficam (onde quebrou), a linha final do erro não.
    assert 'File "' in evento["details"]["traceback"], evento
    assert "add_launch_and_update_balance" in evento["details"]["traceback"], evento
    da_b = _uma(_linhas(marca, "user_id = %s", (b,)), "message_processing_failed", "handle_incoming/whatsapp")

    bruto_a, ids_a = _exportados(a)
    assert {evento["id"], logger["id"]} <= ids_a, "a falha de A não saiu na exportação de A"
    assert da_b["id"] not in ids_a and mark_a not in bruto_a and mark_b not in bruto_a
    _, ids_b = _exportados(b)
    assert not ids_b & {evento["id"], logger["id"]}, "linha de A na exportação de B"

    # Rode a conversa: outro assunto, sem a falha, é respondido e não grava erro.
    monkeypatch.setattr(db, "add_launch_and_update_balance", real)
    marca2 = _marca()
    resposta = _conversa(a, "quanto gastei esse mês?")
    assert resposta.strip() and AMIGAVEL not in resposta, resposta
    assert not [x for x in _linhas(marca2, "user_id = %s", (a,)) if x["event_type"]
                in ("message_processing_failed", "logger.error")]

    privacy.delete_user_data(a)

    assert _linhas(marca, "user_id = %s", (a,)) == [], "log de A sobreviveu à exclusão"
    restos = _linhas(marca, "(message like %s or details::text like %s)", (f"%{mark_a}%", f"%{mark_a}%"))
    assert restos == [], restos
    assert [x["id"] for x in _linhas(marca, "user_id = %s", (b,))
            if x["event_type"] == "message_processing_failed"] == [da_b["id"]], \
        "a exclusão de A levou a linha de B"


# ── T2. PARAR com falha numa das duas contas do número ───────────────────────

def test_parar_com_falha_na_segunda_conta_grava_na_coluna_dela(enviadas, monkeypatch):  # noqa: F811
    a, b, wa_id = _a_e_b_com_conta()
    real, marcador = db.set_whatsapp_updates_opt_out, f"MARCADOR-{uuid.uuid4().hex}"

    def so_b_falha(uid, v):
        if uid == b:
            raise _violacao(marcador)
        real(uid, v)
    monkeypatch.setattr(wr, "set_whatsapp_updates_opt_out", so_b_falha)
    marca = _marca()
    _manda(wa_id, "PARAR")

    assert enviadas == [wr.OPT_OUT_FALHOU_WA], enviadas
    assert _opt_out(a) and not _opt_out(b)
    da_b = _linhas(marca, "user_id = %s", (b,))
    evento = _uma(da_b, "whatsapp_updates_disable_button_error", "wa_runtime")
    logger = _uma(da_b, "logger.error", wr.__name__, "WA updates disable button error")
    for linha in (evento, logger):
        _sem_vazamento(linha, marcador)
    assert evento["details"]["sqlstate"] == "23505", evento
    assert "causa=UniqueViolation sqlstate=23505" in logger["message"], logger
    _, ids_a = _exportados(a)
    assert not ids_a & {evento["id"], logger["id"]}, "falha de B na exportação de A"
    _, ids_b = _exportados(b)
    assert {evento["id"], logger["id"]} <= ids_b, "falha de B fora da exportação de B"


# ── T3. topo do WhatsApp: a falha que derruba a mensagem inteira ─────────────

@pytest.mark.parametrize("falha", [_levanta, _encadeada], ids=["direta", "encadeada"])
def test_topo_do_whatsapp_grava_o_dono_e_o_stderr_nao_leva_o_detail(enviadas, monkeypatch, capsys, falha):  # noqa: F811
    uid, wa_id = _com_senha_ligada(True)
    marcador = f"MARCADOR-{uuid.uuid4().hex}"
    monkeypatch.setattr(wr, "handle_incoming", falha(marcador))
    marca = _marca()
    _manda(wa_id, GASTO)

    assert enviadas == [wr._DELIVERY_FAILURE_MESSAGE], enviadas
    linhas = _linhas(marca, "user_id = %s", (uid,))
    evento = _uma(linhas, "whatsapp_message_processing_failed", "wa_runtime")
    logger = _uma(linhas, "logger.error", wr.__name__, "WA message processing failed")
    for linha in (evento, logger):
        _sem_vazamento(linha, marcador, "açaí", "Zélia")
    causa = "RuntimeError" if falha is _encadeada else "UniqueViolation"  # o tipo de fora; o sqlstate, da cadeia
    assert f"causa={causa} sqlstate=23505" in logger["message"], logger
    assert evento["details"]["sqlstate"] == "23505", evento
    err = capsys.readouterr().err
    assert marcador not in err and FONE not in err and "DETAIL" not in err, err
    assert 'File "' in err and "process_message" in err, "a pilha sumiu do stderr: " + err
    if falha is _encadeada:  # a causa: o quadro do banco, só com o tipo na separação
        assert "in _banco" in err and "Causado por UniqueViolation:" in err, err
    _, ids = _exportados(uid)
    assert {evento["id"], logger["id"]} <= ids


# ── T4. os pares do mesmo `except`: botões de relatório e de menu ────────────
# A guarda não os vê (o uid não está nos argumentos do logger): só este teste os prende.

_BOTOES = [
    ("button_reply", wr.WA_DAILY_REPORT_DISABLE_ID, wr.h_report, "disable",
     "whatsapp_daily_report_disable_button_error", "WA daily_report_disable button error"),
    ("button_reply", wr.WA_WEEKLY_REPORT_DISABLE_ID, wr.h_report, "disable_weekly",
     "whatsapp_weekly_report_disable_button_error", "WA weekly_report_disable button error"),
    ("button_reply", wr.WA_MONTHLY_REPORT_DISABLE_ID, wr.h_report, "disable_monthly",
     "whatsapp_monthly_report_disable_button_error", "WA monthly_report_disable button error"),
    ("button_reply", "tut_start", wr, "handle_tutorial_button",
     "whatsapp_tutorial_button_error", "WA tutorial button error"),
    ("list_reply", "help_cc", wr, "send_help_section", "whatsapp_help_menu_error", "WA help menu error"),
    ("list_reply", "cmds_saldo", wr, "send_commands_section",
     "whatsapp_commands_menu_error", "WA commands menu error"),
]


@pytest.mark.parametrize("tipo,botao,alvo,funcao,event_type,prefixo", _BOTOES, ids=[b[1] for b in _BOTOES])
def test_botao_com_falha_grava_as_duas_linhas_na_coluna(enviadas, monkeypatch, caplog,  # noqa: F811
                                                        tipo, botao, alvo, funcao, event_type, prefixo):
    uid, wa_id = _com_senha_ligada(True)
    marcador = f"MARCADOR-{uuid.uuid4().hex}"
    monkeypatch.setattr(alvo, funcao, _levanta(marcador))
    marca = _marca()
    with caplog.at_level(logging.WARNING):
        wr.process_message(InboundMessage(
            wa_id=wa_id, text="", timestamp="1", attachments=[],
            raw={"id": f"wamid.{uuid.uuid4().hex}", "type": "interactive",
                 "interactive": {"type": tipo, tipo: {"id": botao, "title": "x"}}}))

    records = [r for r in caplog.records if r.getMessage().startswith(prefixo)]
    assert len(records) == 1, [r.getMessage() for r in caplog.records]
    assert getattr(records[0], "user_id", None) == uid and records[0].exc_info is None, records[0].__dict__
    linhas = _linhas(marca, "user_id = %s", (uid,))
    evento = _uma(linhas, event_type, "wa_runtime")
    logger = _uma(linhas, "logger.error", wr.__name__, prefixo)
    for linha in (evento, logger):
        _sem_vazamento(linha, marcador)
    assert evento["details"]["sqlstate"] == "23505", evento
    assert "causa=UniqueViolation sqlstate=23505" in logger["message"], logger


# ── T5. exceção encadeada pelo handle_incoming: a pilha da causa e o sqlstate ficam ──

def test_falha_encadeada_guarda_o_quadro_do_banco_sem_o_detail(monkeypatch):
    a, marcador = _uid(), f"MARCADOR-{uuid.uuid4().hex}"
    monkeypatch.setattr(db, "add_launch_and_update_balance", _encadeada(marcador))
    marca = _marca()
    assert AMIGAVEL in _conversa(a, GASTO)

    linhas = _linhas(marca, "user_id = %s", (a,))
    evento = _uma(linhas, "message_processing_failed", "handle_incoming/whatsapp")
    logger = _uma(linhas, "logger.error", "core.handle_incoming", "handle_incoming FAILED")
    for linha in (evento, logger):
        _sem_vazamento(linha, marcador, "açaí", "Zélia")
    tb = evento["details"]["traceback"]
    assert "in _banco" in tb and "Causado por UniqueViolation:" in tb, tb
    assert evento["details"]["sqlstate"] == "23505", evento
    assert "causa=RuntimeError sqlstate=23505" in logger["message"], logger


# ── T6. a pilha não pode derrubar o `except` de último recurso ───────────────

def test_falha_ao_montar_a_pilha_nao_derruba_a_resposta_nem_o_log(monkeypatch):
    def quebra(_tb):
        raise RuntimeError("format_tb quebrou")
    monkeypatch.setattr(pg_text, "traceback", SimpleNamespace(format_tb=quebra))
    a, marcador = _uid(), f"MARCADOR-{uuid.uuid4().hex}"
    monkeypatch.setattr(db, "add_launch_and_update_balance", _encadeada(marcador))
    marca = _marca()
    assert AMIGAVEL in _conversa(a, GASTO)

    linhas = _linhas(marca, "user_id = %s", (a,))
    for linha in (_uma(linhas, "message_processing_failed", "handle_incoming/whatsapp"),
                  _uma(linhas, "logger.error", "core.handle_incoming", "handle_incoming FAILED")):
        _sem_vazamento(linha, marcador, "açaí", "Zélia")


def test_vazou_acha_a_forma_normalizada_e_recusa_proibido_vazio():
    t = f"nota='acai da dona zelia' DETAIL:  Key (nota)=({FONE})"
    assert _vazou(t, "Açaí") == "Açaí" and _vazou(t, "zzz") == FONE
    assert _vazou("DETAIL:  Key", "zzz") == "DETAIL:" and _vazou("details detailed", "zzz") is None
    for vazio in ("", None, " - "):
        with pytest.raises(AssertionError, match="proibido vazio"):
            _vazou(t, "zzz", vazio)
