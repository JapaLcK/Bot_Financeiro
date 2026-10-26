"""Controles por trecho da guarda `tests/test_log_dono_na_coluna.py` (#657).

Cada par tem a forma que reprova e a forma correta, que tem de passar limpa: sem
o lado limpo, uma guarda que acusa tudo passaria. Separado do arquivo da guarda
só pelo teto de 350 linhas.
"""
from __future__ import annotations

from test_log_dono_na_coluna import _achados

_EVT = 'log_system_event_sync("error", "e", "m", '


def _de(corpo: str, cabeca: str = "") -> list[str]:
    fonte = cabeca + "def f(user_id, wa_id, n, to, reply_to, pid, to_user_id):\n    try:\n        pass\n" \
        "    except Exception as exc:\n" + "".join(f"        {linha}\n" for linha in corpo.splitlines())
    return [classe for _, _, classe in _achados(fonte, "x.py")]


def _confere(pares, cabeca: str = "") -> None:
    for ruim, classes, boa in pares:
        assert _de(ruim, cabeca) == classes, (ruim, _de(ruim, cabeca))
        assert _de(boa, cabeca) == [], (boa, _de(boa, cabeca))


def test_dono_fora_da_coluna():
    _confere([
        ('logger.warning("x user=%s", user_id)', ["LOG_MSG_NULL"],
         'logger.warning("x", extra={"user_id": user_id})'),
        ('logger.warning(f"falha user_id={pid}")', ["LOG_MSG_NULL"],
         'logger.warning("falha", extra={"user_id": pid})'),
        # o handler só lê `record.user_id`: outra chave, None ou str deixam nula
        ('logger.warning("x", extra={"uid": user_id})', ["LOG_MSG_NULL"],
         'logger.warning("x", extra={"user_id": user_id})'),
        ('logger.warning("x user=%s", user_id, extra={"user_id": None})', ["LOG_MSG_NULL"],
         'logger.warning("x user=%s", user_id, extra={"user_id": user_id})'),
        ('logger.warning("x", extra={"user_id": str(user_id)})', ["LOG_MSG_NULL"],
         'logger.warning("x", extra={"user_id": int(user_id)})'),
        (_EVT + 'details={"user_id": user_id})', ["EVT_DET_NULL"], _EVT + "user_id=user_id)"),
        ('log_system_event_sync("error", "e", f"m uid={user_id}")', ["EVT_MSG_NULL"],
         _EVT + "user_id=user_id)"),
        # `_log_event(level, message, details)`: o 3º posicional é details
        ('_log_event("error", "m", {"user_id": user_id})', ["EVT_DET_NULL"],
         '_log_event("error", "m", {"n": 1})'),
        ('log.info("x user=%s", user_id)', [], 'log.debug("x")'),   # info não vai ao banco
        ('logger.log(logging.WARNING, "x user=%s", user_id)', ["LOG_MSG_NULL"],
         'logger.log(logging.INFO, "x user=%s", user_id)'),
    ])


def test_telefone_e_email():
    _confere([
        ('logger.warning("to=%s", wa_id)', ["LOG_PII_NULL"],
         'logger.warning("to=%s", mask_phone(wa_id))'),
        (_EVT + 'details={"phone": n})', ["EVT_PII_NULL"], _EVT + 'details={"phone": mask_phone(n)})'),
        (_EVT + 'details={"to": to})', ["EVT_PII_NULL"], _EVT + 'details={"to": mask_phone(to)})'),
        ('logger.warning("wa_id=%s", reply_to)', ["LOG_PII_NULL"],
         'logger.warning("wa_id=%s", mask_phone(reply_to))'),
        ('logger.error("phone=%s", n)', ["LOG_PII_NULL"], 'logger.error("phone=%s", mask_phone(n))'),
        ('logger.error(f"to={n}")', ["LOG_PII_NULL"], 'logger.error(f"to={mask_phone(n)}")'),
        ('logger.error("email: %s", n)', ["LOG_PII_NULL"], 'logger.error("falha no envio")'),
        # chave renomeada não esconde o `to`; chave com valor literal não é dado
        (_EVT + 'details={"destino": to})', ["EVT_PII_NULL"], _EVT + 'details={"to": "<omitido>"})'),
        ('logger.error("falha para %s", to)', ["LOG_PII_NULL"],
         'logger.error("falha para %s", mask_phone(to))'),
        # com a coluna o logger ainda escreve no stderr (D4): nome/chave, sem marcador
        ('logger.warning("e=%s", type(exc).__name__, to, extra={"user_id": user_id})',
         ["LOG_PII_COL"],
         'logger.warning("to=%s", mask_phone(to), user_id, extra={"user_id": user_id})'),
        ('logger.exception("x %s", to, extra={"user_id": user_id})',
         ["LOG_EXTRA_RAW", "LOG_PII_COL"],
         'logger.warning("to=%s", to_user_id, extra={"user_id": user_id})'),
    ])


def test_texto_cru_com_a_coluna():
    col = _EVT + 'user_id=user_id, details={"e": '
    _confere([
        (col + "str(exc)})", ["EVT_COL_RAW"],
         col + 'type(exc).__name__, "s": getattr(exc, "sqlstate", None)})'),
        # `diag.*` e `pgerror` trazem o `DETAIL: Key (…)` do psycopg
        (col + "exc.diag.message_detail})", ["EVT_COL_RAW"], col + "exc.diag.constraint_name})"),
        (col + "exc.pgerror})", ["EVT_COL_RAW"], col + "exc.sqlstate})"),
        (col + 'getattr(exc, "pgerror", None)})', ["EVT_COL_RAW"],
         col + 'getattr(exc, "constraint_name", None)})'),
        # `code` é genérico de terceiros (`SystemExit("texto").code`); `codigo` é o nosso
        (col + "exc.code})", ["EVT_COL_RAW"], col + "exc.codigo})"),
        ('logger.exception("x", extra={"user_id": user_id})', ["LOG_EXTRA_RAW"],
         'logger.warning("x %s", type(exc).__name__, extra={"user_id": user_id})'),
        ('log.error("x", exc_info=True, extra={"user_id": user_id})', ["LOG_EXTRA_RAW"],
         'log.error("x %s", exc.__class__.__name__, extra={"user_id": user_id})'),
    ])


def test_apelido_de_import_e_de_atribuicao():
    ruim = 'lse("error", "e", "m", user_id=user_id, details={"e": str(exc)})'
    boa = 'lse("error", "e", "m", user_id=user_id, details={"e": type(exc).__name__})'
    _confere([(ruim, ["EVT_COL_RAW"], boa)],
             "from core.system_event_log import log_system_event_sync as lse\n")
    _confere([(ruim, ["EVT_COL_RAW"], boa)],
             "from core import system_event_log\nlse = system_event_log.log_system_event_sync\n")
    assert _de(ruim) == []      # sem o apelido, `lse` é só um nome qualquer
