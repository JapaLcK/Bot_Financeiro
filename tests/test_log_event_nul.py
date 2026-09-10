"""Issue #357 — a linha de auditoria que SOME em silêncio, sem 500 e sem rastro.

Quinto mecanismo da família #310/#317, e o único que status nenhum mede: aqui o
`except Exception` é **por projeto** (perder um log não pode derrubar a
requisição), então NUL (`\\u0000`) ou surrogate solitário em qualquer parâmetro
faz o INSERT ser recusado e a linha simplesmente não existir. Nos campos
`text` quem levanta é o psycopg, antes do servidor (`PostgreSQL text fields
cannot contain NUL`); no `details` (`jsonb`) é o servidor. Mesma consequência.

São **três tabelas e quatro INSERTs**, não só o `details` de um deles:
`system_event_logs` (duas cópias do mesmo INSERT — `core/observability.py` e
`core/admin_dashboard.py`), `audit_events` (`core/audit.py`) e
`auth_login_events` (`core/admin_dashboard.py:150`, o irmão direto que fica
50 linhas ACIMA do primeiro conserto e que a varredura inicial não pegou).

E são todos os campos `text`, não só o `details` — sanear só o `details`
deixaria o `core/services/pix_drain.py:170-174` (event_type do webhook do
Asaas no `message`) e o `_DashboardHandler` (`core/observability.py:59`:
qualquer `logger.warning(f"…{entrada}")` do repo vira `message`) abertos.

CONTROLE NEGATIVO do grupo: `limpa_para_pg` → identidade (`lambda v: v`) nos
três módulos (`observability`, `audit`, `admin_dashboard`). Quem discrimina está NOMEADO: toda a matriz venenosa e o teste
anônimo do `wa_verify` ficam vermelhos; os controles positivos continuam verdes.
Injetar num caso hoje verde (acento, emoji) não discriminaria nada.

CONTROLES POSITIVOS: `test_controle_positivo_details_limpo_grava_identico` e o
irmão da auditoria LEEM O BANCO DE VOLTA e comparam o dict — sem eles, um
saneador destrutivo (`_limpa_str` → `""`) passaria em toda a matriz. O
`test_email_claro_e_email_enc_guardam_o_MESMO_valor` cobre o par
clara/cifrada, que a matriz não vê: ela só lê a coluna clara.
"""
import asyncio
import copy
import uuid

import pytest
from starlette.requests import Request

import adapters.whatsapp.wa_app as wa_app
import core.admin_dashboard as admin_dashboard
import core.audit as audit
import core.observability as observability
from _corpo_json_helpers import _admin_tables  # noqa: F401  (fixture autouse)
from core.crypto import PiiAccessContext, decrypt_pii_optional
from db.connection import get_conn

NUL = "\x00"
SURR_ALTO = "\ud800"
SURR_BAIXO = "\udc00"
VENENOS = [NUL, SURR_ALTO, SURR_BAIXO]
VENENO_IDS = ["nul", "surrogate_alto", "surrogate_baixo"]
FFFD = "�"

CAMPOS = ["level", "event_type", "message", "source", "details"]
CAMPOS_AUDIT = ["event", "ip", "user_agent", "details"]
CAMPOS_LOGIN = ["email", "ip_address", "user_agent", "failure_reason"]


def _marcador(prefixo: str) -> str:
    """Chave limpa dentro do `details`: é por ela que a linha é reencontrada,
    inclusive quando o campo envenenado é o próprio `event_type`."""
    return f"{prefixo}-357-{uuid.uuid4()}"


def _select(sql: str, param):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (param,))
            return [dict(r) for r in cur.fetchall() or []]


def _eventos(marcador: str):
    return _select(
        "select level, event_type, message, source, details from system_event_logs "
        "where details->>'marcador' = %s",
        marcador,
    )


def _auditorias(marcador: str):
    return _select(
        "select event, ip, user_agent, details from audit_events "
        "where details->>'marcador' = %s",
        marcador,
    )


def _campos(campo: str, veneno: str, marcador: str) -> dict:
    """Envio limpo com UM campo envenenado. `AAA…BBB` cerca o veneno: prova que
    o resto da string sobrevive e que o campo não virou vazio (vazio passaria
    por vários asserts de graça)."""
    p = f"AAA{veneno}BBB"
    kw = {
        "level": "warning",
        "event_type": "log_event_nul_357",
        "message": "mensagem de teste",
        "source": "test_log_event_nul",
        "details": {"marcador": marcador},
    }
    if campo == "details":
        kw["details"]["veneno"] = p
    else:
        kw[campo] = p
    return kw


def _grava_sync(kw: dict) -> None:
    observability.log_system_event_sync(
        kw["level"], kw["event_type"], kw["message"],
        source=kw["source"], details=kw["details"],
    )


def _grava_async(kw: dict) -> None:
    asyncio.run(admin_dashboard.log_system_event(
        kw["level"], kw["event_type"], kw["message"],
        source=kw["source"], details=kw["details"],
    ))


GRAVADORES = [_grava_sync, _grava_async]
GRAVADOR_IDS = ["sync_observability", "async_admin_dashboard"]


def _assert_saneado(linhas, campo):
    assert len(linhas) == 1, f"a linha de auditoria sumiu em silêncio: {linhas}"
    valor = linhas[0]["details"]["veneno"] if campo == "details" else linhas[0][campo]
    assert FFFD in valor, valor
    assert valor.startswith("AAA") and valor.endswith("BBB"), valor


# --------------------------------------------------------------------------
# Matriz — 5 campos × 3 venenos × 2 funções (as duas cópias do mesmo INSERT)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("grava", GRAVADORES, ids=GRAVADOR_IDS)
@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("campo", CAMPOS)
def test_campo_venenoso_nao_perde_o_system_event_log(campo, veneno, grava):
    marcador = _marcador(campo)
    grava(_campos(campo, veneno, marcador))
    _assert_saneado(_eventos(marcador), campo)


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("campo", CAMPOS_AUDIT)
def test_campo_venenoso_nao_perde_o_audit_event(campo, veneno):
    """`audit_events` é a tabela LGPD (senha, MFA, Open Finance, IP novo) — e o
    `user_agent` vem de header do cliente. Perder linha aqui é pior que em
    `system_event_logs`, que é purgável por projeto."""
    marcador = _marcador(campo)
    p = f"AAA{veneno}BBB"
    kw = {"event": "account_reset", "ip": "10.0.0.1", "user_agent": "curl/8",
          "details": {"marcador": marcador}}
    if campo == "details":
        kw["details"]["veneno"] = p
    else:
        kw[campo] = p
    audit.record_audit_event(
        None, kw["event"], ip=kw["ip"], user_agent=kw["user_agent"],
        details=kw["details"],
    )
    _assert_saneado(_auditorias(marcador), campo)


# --------------------------------------------------------------------------
# `auth_login_events` — o irmão direto, no MESMO arquivo, 50 linhas acima
# --------------------------------------------------------------------------

def _login_events(marcador: str):
    return _select(
        "select email, ip_address, user_agent, failure_reason from auth_login_events "
        "where email like %s",
        f"%{marcador}%",
    )


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("campo", CAMPOS_LOGIN)
def test_campo_venenoso_nao_perde_o_auth_login_event(campo, veneno):
    """Mesma forma, mesmo `except Exception` que engole — e o vetor aqui é o
    mais exposto dos três: `email` vem do corpo JSON de `/auth/login`, rota
    anônima, e `LoginBody.email` é `str` puro (sem `EmailStr`,
    `frontend/finance_bot_websocket_custom.py:2671`), então o NUL chega cru.

    Header morreu como vetor (medido: o h11 recusa NUL em header, e header é
    latin-1, então surrogate solitário também não passa) — quem chega
    envenenado é corpo JSON e query string. `ip_address`, `user_agent` e
    `failure_reason` entram na matriz porque são campos `text` do MESMO
    INSERT: um só derruba a linha inteira.

    `success=True` de propósito: com `False`, `log_auth_login_event` agenda o
    `schedule_auth_failure_spike_check` numa task de fundo que o `asyncio.run`
    mata ao fechar o loop — ruído, e não é o que está sob teste.
    """
    marcador = _marcador(campo)
    p = f"AAA{veneno}BBB"
    # O marcador vive DENTRO do e-mail: `auth_login_events` não tem `details`,
    # e o e-mail é o único campo presente em toda linha. Quando é ele o
    # envenenado, o marcador (limpo) sobrevive ao saneamento e o `like` acha.
    kw = {"email": f"{marcador}@x.com", "ip_address": "10.0.0.1",
          "user_agent": "curl/8", "failure_reason": "invalid_credentials"}
    kw[campo] = f"{p}-{marcador}@x.com" if campo == "email" else p
    asyncio.run(admin_dashboard.log_auth_login_event(
        kw["email"], True, ip_address=kw["ip_address"],
        user_agent=kw["user_agent"], failure_reason=kw["failure_reason"],
    ))

    linhas = _login_events(marcador)
    assert len(linhas) == 1, f"a linha de auth_login_events sumiu em silêncio: {linhas}"
    valor = linhas[0][campo]
    assert FFFD in valor, valor
    # `.upper()` na volta porque o `email` é gravado com `.lower()`.
    assert valor.upper().startswith("AAA") and "BBB" in valor.upper(), valor


# --------------------------------------------------------------------------
# Controles positivos — leem o banco de volta e comparam o VALOR
# --------------------------------------------------------------------------

LIMPO = {"texto": "pão à vista 😀", "n": [1, 2.5, None, True], "obj": {"k": "ok"}}
MSG_LIMPA = "conta paga: R$ 1,50 à vista 😀"


@pytest.mark.parametrize("grava", GRAVADORES, ids=GRAVADOR_IDS)
def test_controle_positivo_details_limpo_grava_identico(grava):
    """Um saneador destrutivo responderia igual em toda a matriz acima. Aqui o
    `details` volta do jsonb IDÊNTICO — inclusive na ordem das chaves, que o
    `pop`+reinsere do `limpa_para_pg` preserva — e o emoji continua um
    caractere só."""
    marcador = _marcador("positivo")
    details = {"marcador": marcador, **copy.deepcopy(LIMPO)}
    esperado = copy.deepcopy(details)
    grava({"level": "info", "event_type": "log_event_nul_357",
           "message": MSG_LIMPA, "source": "test_log_event_nul",
           "details": details})

    # A ordem das chaves NÃO se mede lendo de volta — o `jsonb` normaliza
    # (comprimento, depois bytes), MEDIDO. O que dá para medir é o dict que o
    # `limpa_para_pg` mutou NO LUGAR: o `pop`+reinsere preserva a ordem.
    assert list(details) == list(esperado), details

    linhas = _eventos(marcador)
    assert len(linhas) == 1, linhas
    assert linhas[0]["details"] == esperado, linhas[0]["details"]
    assert linhas[0]["message"] == MSG_LIMPA, linhas[0]["message"]


def test_controle_positivo_audit_limpo_grava_identico():
    marcador = _marcador("positivo-audit")
    details = {"marcador": marcador, **copy.deepcopy(LIMPO)}
    esperado = copy.deepcopy(details)
    audit.record_audit_event(
        None, "account_reset", ip="10.0.0.1", user_agent="curl/8 à toa 😀",
        details=details,
    )

    linhas = _auditorias(marcador)
    assert len(linhas) == 1, linhas
    assert linhas[0]["details"] == esperado, linhas[0]["details"]
    assert linhas[0]["user_agent"] == "curl/8 à toa 😀", linhas[0]["user_agent"]


@pytest.mark.parametrize("local", ["Fulano", f"fu{NUL}lano"], ids=["limpo", "nul"])
def test_email_claro_e_email_enc_guardam_o_MESMO_valor(local):
    """A coluna clara e a cifrada não podem divergir — a `email_enc` é o que o
    `scripts/migrate_pii_to_encrypted.py` e o painel leem. Por isso o
    saneamento é aplicado ao `normalized_email` ANTES de cifrar, e não dentro
    da tupla do INSERT: sanear só a tupla gravaria o NUL dentro do blob.

    O caso limpo é o controle positivo do par — sem ele, um saneador que
    zerasse os dois lados passaria (`None == None`)."""
    marcador = _marcador("enc")
    # Espaco e maiuscula de proposito: o `.strip().lower()` roda ANTES do
    # saneamento, e e o resultado dele que tem de ir para as DUAS colunas.
    asyncio.run(admin_dashboard.log_auth_login_event(
        f"  {local}-{marcador}@X.com  ", True))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select email, email_enc from auth_login_events where email like %s",
                (f"%{marcador}%",),
            )
            linhas = [dict(r) for r in cur.fetchall() or []]
    assert len(linhas) == 1, linhas
    claro = decrypt_pii_optional(
        linhas[0]["email_enc"],
        ctx=PiiAccessContext(purpose="test", actor="pytest", subject_user_id=None),
    )
    assert linhas[0]["email"], linhas[0]
    assert claro == linhas[0]["email"], (claro, linhas[0]["email"])
    assert NUL not in claro, repr(claro)


# --------------------------------------------------------------------------
# Ponta a ponta ANÔNIMO — o caso da issue
# --------------------------------------------------------------------------

def test_wa_verify_anonimo_com_nul_no_mode_nao_perde_a_auditoria(monkeypatch):
    """`GET /webhook` não pede autenticação, e `hub.mode` vai cru para o
    `details`. Antes do saneamento, `hub.mode=sub%00` apagava o próprio registro
    `whatsapp_webhook_verify_failed` (medido: 0 linhas).

    O `log_system_event_sync` NÃO é mockado aqui — é o INSERT de verdade que
    está sob teste. (Por isso o `_wa_verify` de `test_wa_webhook_signature.py`,
    que mocka o log, não serve.)
    """
    monkeypatch.setattr(wa_app, "VERIFY_TOKEN", "verify-token-de-teste")
    # O `mode` carrega um uuid: `event_type` é constante da rota e não escopa
    # nada, então sem isto uma segunda passagem pela rota na mesma base daria
    # `linhas = 2` e o assert quebraria por acúmulo, não por regressão.
    sufixo = uuid.uuid4().hex
    req = Request({"type": "http", "method": "GET", "headers": [],
                   "query_string": f"hub.mode=sub%00{sufixo}&hub.verify_token=errado".encode()})

    resp = asyncio.run(wa_app.wa_verify(req))

    assert resp.status_code == 403
    linhas = _select(
        "select details->>'mode' as mode from system_event_logs "
        "where event_type = 'whatsapp_webhook_verify_failed' "
        "and details->>'mode' = %s",
        f"sub{FFFD}{sufixo}",
    )
    assert len(linhas) == 1, "a linha whatsapp_webhook_verify_failed sumiu em silêncio"
