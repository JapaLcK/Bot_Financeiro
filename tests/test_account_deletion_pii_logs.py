"""Resíduo de PII em `system_event_logs` no caminho da EXCLUSÃO de conta.

Assunto separado de `tests/test_account_deletion_pluggy.py` (que é sobre falar
com a Pluggy na exclusão): aqui nenhum caso toca em Open Finance — a conta é
semeada SEM conexão, e o que se mede é o que sobra escrito no banco depois de a
conta sumir. Os dois defeitos são ANTERIORES à PR-C; o dono decidiu fechá-los
aqui.

O mecanismo, comum aos dois: `system_event_logs` tem FK `user_id` com
`on delete cascade`, mas as linhas destes dois caminhos são gravadas com a
COLUNA `user_id` NULL (e depois do commit da exclusão, quando preencher a coluna
violaria a FK). Cascata nenhuma as leva — então qualquer identificador que caia
no `message` ou no `details` fica no banco para sempre, que é exatamente o que a
exclusão existe para remover.

CONTROLES DO GRUPO (CLAUDE.md §3), injetados em caso que estava VERDE:

- negativo — `core/services/email_service.py`, trocar o `log_recipient=False` de
  `send_account_deletion_completed_email` por `True` (o default de todos os
  outros e-mails): T13 vermelho;
- negativo — `core/services/email_service.py`, repor no `except` de `send_email` o
  `erro = str(exc).replace(to, to_log) if not log_recipient else str(exc)` da
  rodada 2 (redação por substituição literal): T15 vermelho nas 7 formas —
  4 pelo ENDEREÇO na tabela (`maiusculas`, `dominio_maiusculo`, `url_encoded`,
  `json_escapado`, medido em 23/09/2026, inclusive na linha espelhada
  `event_type='logger.error'`) e as outras 3 (`literal`, `entre_sinais`,
  `sdk_validation_error`) por persistirem o texto do provedor em
  `details.error`;
- negativo — `scripts/account_deletion_job.py`, trocar
  `persistido = {**summary, "errors": len(errors)}` por `persistido = summary`:
  T14 vermelho;
- positivo — os três casos exigem que o rastro OPERACIONAL continue: T13 que a
  linha `email_sent` do e-mail final continue existindo (com assunto), T14 que o
  resumo do cron continue existindo com a CONTAGEM de erros, T15 que a linha
  `email_failed` continue dizendo POR QUE o e-mail não saiu (tipo da exceção e,
  no erro do SDK, `code`/`error_type`).
  Sem eles o grupo passaria numa versão que simplesmente para de logar — pior
  que o defeito.

CLASSE CEGA declarada: a varredura é textual (`like '%<uid>%'`,
`like '%<email>%'`). PII que aparecesse CIFRADA, truncada ou reformatada
(uid em hex, e-mail com o domínio cortado) passa limpa por ela.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

from resend.exceptions import raise_for_code_and_type

import core.services.email_service as email_service
import db
from db.connection import get_conn

# Capturado no IMPORT do módulo, que acontece na coleção — antes do stub autouse
# `_block_outbound_network` (tests/conftest.py:260) trocar `send_email` por
# `lambda *a, **kw: True`. T13 precisa do CORPO real de `send_email` (é ele que
# escreve o log); só o transporte é falso. Sem isto seria preciso
# `importlib.reload`, que deixaria o kill switch de rede desligado para o resto
# da sessão (o `monkeypatch.undo` restauraria a função real, não o stub).
_SEND_EMAIL_REAL = email_service.send_email

ASSUNTO_FINAL = "Conta excluída — PigBank"
SENHA = "senha-certa-123"


def _email_de(uid: int) -> str:
    return f"delete-{uid}@t.local"


def _semeia(uid: int) -> None:
    """O mínimo que ESTES casos discriminam: a conta agendada e vencida. Sem
    conexão de Open Finance de propósito — nenhum caso daqui fala com a Pluggy,
    e conta sem conexão nem pede apiKey (travado por T6 do arquivo irmão)."""
    from db.users import _hash_password

    db.ensure_account_deletion_columns()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                  (user_id, email, password_hash, deletion_status,
                   deletion_requested_at, deletion_scheduled_for)
                values (%s, %s, %s, 'scheduled',
                        now() - interval '8 days', now() - interval '1 minute')
                """,
                (uid, _email_de(uid), _hash_password(SENHA)),
            )
        conn.commit()


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` não nasce de `db/schema.py` — é criada
    preguiçosamente por `core/admin_dashboard.py`. Sem ela o INSERT do log falha
    em silêncio e os dois casos não mediriam nada."""
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


@pytest.fixture
def marca() -> int:
    """Fronteira de leitura E de limpeza. As linhas escritas aqui vão com a
    coluna `user_id` NULL (é o assunto do arquivo), então cascata nenhuma as
    leva no teardown da fixture `user_id` — quem limpa é este `yield`."""
    m = _max_id()
    yield m
    with get_conn() as conn:
        conn.execute("delete from system_event_logs where id > %s", (m,))
        conn.commit()


def _max_id() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select coalesce(max(id), 0) as m from system_event_logs")
            m = int(cur.fetchone()["m"])
        conn.commit()
    return m


def _linhas_com(agulha: str, uid: int) -> list[dict]:
    """Toda a tabela, não só as linhas novas: o ponto é que NADA cite a conta.

    `ilike`, não `like`: o texto de erro do provedor devolve o endereço na caixa
    que ele quiser (T15), e `like` deixaria passar `DELETE-123@T.LOCAL`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, event_type, user_id, message, details::text as det
                from system_event_logs
                where user_id = %s
                   or message ilike %s
                   or details::text ilike %s
                """,
                (uid, f"%{agulha}%", f"%{agulha}%"),
            )
            linhas = cur.fetchall()
        conn.commit()
    return linhas


def _novos(marca: int, event_type: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, event_type, message, details from system_event_logs "
                "where id > %s and event_type = %s order by id",
                (marca, event_type),
            )
            linhas = cur.fetchall()
        conn.commit()
    return linhas


def _existe_usuario(uid: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from users where id = %s", (uid,))
            achou = cur.fetchone() is not None
        conn.commit()
    return achou


# ── T13 ─────────────────────────────────────────────────────────────────────

def test_t13_email_final_nao_deixa_o_endereco_no_banco(user_id, monkeypatch, marca):
    """O e-mail "Conta excluída" sai DEPOIS do commit da exclusão
    (`scripts/account_deletion_job.py:95`), e `send_email` logava
    `details={"to": <email>}` + `message="E-mail enviado para <email>"` numa
    linha sem coluna `user_id`: o endereço da conta excluída ficava no banco
    para sempre. O `_DashboardHandler` (`core/observability.py`) espelha o
    `logger.warning/error` do mesmo `send_email` na MESMA tabela, então a
    redação vale para os dois.

    Fica o rastro operacional: `event_type=email_sent` + o assunto.
    """
    _semeia(user_id)
    email = _email_de(user_id)
    enviados: list[dict] = []

    class _ResendFalso:
        class Emails:
            @staticmethod
            def send(params):
                enviados.append(params)
                return {"id": "fake"}

    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(email_service, "_get_resend", lambda: _ResendFalso)

    from scripts.account_deletion_job import run as run_account_deletion_job

    assert run_account_deletion_job(limit=10) == 0
    assert [p["to"] for p in enviados] == [[email]], \
        f"o e-mail final tinha que ter saído para o endereço da conta: {enviados}"
    assert not _existe_usuario(user_id), "a conta tinha que ter sido excluída"

    assert _linhas_com(email, user_id) == [], \
        "o endereço da conta excluída ficou em system_event_logs"

    enviado = _novos(marca, "email_sent")
    assert len(enviado) == 1, \
        f"o operador precisa saber que o e-mail final saiu, veio {enviado}"
    assert enviado[0]["details"]["subject"] == ASSUNTO_FINAL, \
        f"o assunto é o que sobra no lugar do endereço: {enviado[0]['details']}"


# ── T14 ─────────────────────────────────────────────────────────────────────

def test_t14_raise_depois_do_commit_nao_deixa_o_uid_no_banco(user_id, monkeypatch, marca):
    """Tudo depois do `conn.commit()` de `delete_user_data` roda com a conta JÁ
    APAGADA: a 2ª conexão (verificação pós-commit), os deletes/counts dela e o
    `raise RuntimeError` de sobras — cujo texto interpola o uid. A exceção sobe
    para `process_due_account_deletions`, vira `errors=[{"user_id": …}]`, e o
    cron gravava isso em `message` E `details` de uma linha sem coluna
    `user_id`. Como `auth_accounts` já saiu, não há agendamento a restaurar e
    nada retenta: a linha é permanente.

    GATILHO DECLARADO: a 2ª `get_conn()` de `delete_user_data` falha com
    `OperationalError` — queda de conexão entre statements, o que um Postgres
    gerenciado faz. NÃO é o único: o `RuntimeError` de sobras do próprio código
    (`db/privacy.py:1137-1141`) está do mesmo lado do commit e o Tester o
    alcançou sem injetar falha nenhuma, só com uma recriação concorrente da
    conta na janela. Este caso usa o gatilho mais curto dos dois.

    A mensagem da `RuntimeError` NÃO mudou: ela é diagnóstico e continua indo
    para o log de aplicação (o `print` do job, retenção finita), que é onde o
    dono aceitou o identificador. O conserto é não PERSISTIR o texto.
    """
    import psycopg

    import db.privacy as privacy

    _semeia(user_id)
    real_get_conn = privacy.get_conn
    chamadas = {"n": 0}

    def _get_conn_instavel(*a, **kw):
        if sys._getframe(1).f_code.co_name == "delete_user_data":
            chamadas["n"] += 1
            if chamadas["n"] == 2:  # a conexão de VERIFICAÇÃO PÓS-COMMIT
                raise psycopg.OperationalError("server closed the connection unexpectedly")
        return real_get_conn(*a, **kw)

    monkeypatch.setattr(privacy, "get_conn", _get_conn_instavel)

    from scripts.account_deletion_job import run as run_account_deletion_job

    codigo = run_account_deletion_job(limit=10)
    monkeypatch.undo()

    assert chamadas["n"] == 2, \
        f"o gatilho não alcançou a conexão pós-commit (chamadas={chamadas})"
    assert codigo == 1, "o cron tinha que sair 1 com erro na lista"
    assert not _existe_usuario(user_id), \
        "PREMISSA DO CASO: o commit passou, a conta está APAGADA"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from auth_accounts where user_id = %s", (user_id,))
            agendamento = cur.fetchone()
        conn.commit()
    assert agendamento is None, \
        "a conta saiu do banco: não há agendamento a restaurar, nada retenta"

    assert _linhas_com(str(user_id), user_id) == [], \
        "o uid de uma conta já apagada ficou em system_event_logs"

    resumo = _novos(marca, "account_deletion_job")
    assert len(resumo) == 1, f"o resumo do cron tinha que continuar existindo: {resumo}"
    assert resumo[0]["details"]["errors"] == 1, \
        f"a CONTAGEM de erros é o que sobra no lugar da lista: {resumo[0]['details']}"


# ── T15 ─────────────────────────────────────────────────────────────────────

# As formas em que o texto de erro do PROVEDOR pode ecoar o destinatário. As
# quatro do meio vazavam com a redação por substituição literal
# (`str(exc).replace(to, "<destinatário omitido>")`): `replace` é lista NEGRA e
# só acha a forma exata; regex de e-mail (`[\w.+%-]+@[\w.-]+`) seria a mesma
# lista negra por outro caminho, porque `url_encoded` e `json_escapado` não têm
# `@` no texto. Não é hipótese de laboratório: `Invalid `to` field` é a
# mensagem literal do Resend, e `%40`/`\u0040` são a mesma mensagem passando por
# querystring e por JSON.
_FORMAS = {
    "literal":           "Invalid `to` field: {e} is not a valid address",
    "maiusculas":        "Invalid `to` field: {E} is not a valid address",
    "dominio_maiusculo": "Invalid `to` field: {ed} is not a valid address",
    "url_encoded":       "POST /emails?to={u} -> 422",
    "json_escapado":     '{{"to":["{j}"],"error":"bounced"}}',
    "entre_sinais":      "Invalid `to` field: < {e} > rejected",
    # 7ª: mesmo texto da `literal`, mas levantado como o erro REAL do SDK
    # (`resend.exceptions.raise_for_code_and_type`, o que `resend/request.py`
    # chama com o corpo do 422). As 6 de cima levantam `RuntimeError`, que não
    # tem `code` nem `error_type` — sem este caso o ramo que loga os dois enums
    # do `ResendError` tem cobertura ZERO.
    "sdk_validation_error": "Invalid `to` field: {e} is not a valid address",
}

# O que a LISTA BRANCA deixa em `details.error` por forma. Escrito à mão, não
# derivado da implementação: se o formato mudar, o teste tem que falhar.
_ERRO_ESPERADO = "ValidationError code=422 type=validation_error"


def _texto_do_provedor(forma: str, email: str) -> str:
    local, dominio = email.split("@")
    return _FORMAS[forma].format(
        e=email,
        E=email.upper(),
        ed=f"{local}@{dominio.upper()}",
        u=email.replace("@", "%40"),
        j=email.replace("@", r"\u0040"),
    )


@pytest.mark.parametrize("forma", list(_FORMAS))
def test_t15_texto_de_erro_do_provedor_nao_persiste_o_endereco(forma, user_id, monkeypatch, marca):
    """O e-mail final pode FALHAR, e aí o `str(exc)` do provedor entrava verbatim
    em duas linhas permanentes: o `details.error`/`message` do `email_failed` e o
    espelho do `logger.error` que o `_DashboardHandler` grava na MESMA tabela.
    Texto controlado pelo provedor persistido numa tabela sem purga.

    O conserto é LISTA BRANCA: sob `log_recipient=False` o texto do provedor não
    é persistido — vai o tipo da exceção (+ o `code` do `ResendError`, quando
    houver), que é o que separa as causas para o operador. A varredura é pela
    PARTE LOCAL do endereço (o que identifica a pessoa) na tabela INTEIRA, com
    `ilike`, e por isso pega os dois canais de uma vez.
    """
    email = _email_de(user_id)
    parte_local = email.split("@")[0]
    texto = _texto_do_provedor(forma, email)
    assert parte_local.lower() in texto.lower(), \
        f"PREMISSA: a forma {forma} tem que conter o endereço, senão não mede nada"

    class _ResendQueFalha:
        class Emails:
            @staticmethod
            def send(params):
                if forma == "sdk_validation_error":
                    raise_for_code_and_type(code=422, error_type="validation_error", message=texto)
                raise RuntimeError(texto)

    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(email_service, "_get_resend", lambda: _ResendQueFalha)

    assert email_service.send_account_deletion_completed_email(email) is False, \
        "send_email nunca levanta: falha do provedor volta False"

    assert _linhas_com(parte_local, user_id) == [], \
        f"forma {forma}: o endereço da conta excluída ficou em system_event_logs"

    falha = _novos(marca, "email_failed")
    assert len(falha) == 1, \
        f"o operador precisa saber que o e-mail final NÃO saiu, veio {falha}"
    esperado = _ERRO_ESPERADO if forma == "sdk_validation_error" else "RuntimeError"
    assert falha[0]["details"]["error"] == esperado, \
        f"a CAUSA é o que sobra no lugar do texto do provedor: {falha[0]['details']}"
    assert falha[0]["details"]["subject"] == ASSUNTO_FINAL, falha[0]["details"]
