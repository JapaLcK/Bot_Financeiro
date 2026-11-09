"""Controles por trecho da guarda `tests/test_log_d4_guarda.py` (D4 da #657).

Cada par tem a forma que reprova e a forma correta, que tem de passar limpa: sem o lado limpo, uma
guarda que acusa tudo passaria.
"""
from __future__ import annotations

from test_log_d4_guarda import _assuntos_abertos, _classes_d4
from test_log_dono_na_coluna import _achados


def _de(corpo: str) -> list[str]:
    fonte = "def f(user_id, wa_id, nota, to, email, r, cat, n):\n    try:\n        pass\n" \
        "    except Exception as exc:\n" + "".join(f"        {linha}\n" for linha in corpo.splitlines())
    return [classe for _, _, classe in _achados(fonte, "x.py", _classes_d4)]


def _confere(pares) -> None:
    for ruim, classe, boa in pares:
        assert _de(ruim) == [classe], (ruim, _de(ruim))
        assert _de(boa) == [], (boa, _de(boa))


def test_excecao_crua():
    _confere([
        ('print(f"x {exc}")', "PRINT_CRU", 'print(f"x {causa_segura(exc)}")'),
        ('print(f"x {detalhe_seguro(exc)}")', "PRINT_CRU", 'print(f"x {_falha_da_pluggy(exc)}")'),
        ('print(f"x {exc!r}")', "PRINT_CRU", 'print(f"x {type(exc).__name__}")'),
        ("traceback.print_exc()", "PRINT_CRU", "print(quadros_da_cadeia(exc)[0])"),
        ("sys.stderr.write(str(exc))", "PRINT_CRU", 'sys.stderr.write("x")'),
        ('logger.debug("x %s", exc)', "BAIXO_CRU", 'logger.debug("x %s", causa_segura(exc))'),
        ('logger.info("x", exc_info=True)', "BAIXO_CRU", 'logger.info("x")'),
        ('logger.log(logging.INFO, "x %s", exc)', "BAIXO_CRU", 'logger.log(logging.INFO, "x")'),
    ])


def test_telefone_e_email():
    _confere([
        ('print(f"to={to}")', "PRINT_PII", 'print(f"to={mask_phone(to)}")'),
        ("print(email)", "PRINT_PII", "print(mascarar_email(email))"),
        ('logger.debug("wa=%s", wa_id)', "BAIXO_PII", 'logger.debug("wa=%s", mask_phone(wa_id))'),
    ])


def test_texto_do_usuario_e_segredo():
    _confere([
        ('print(f"nota={nota}")', "PRINT_TXT", 'print(f"chars={len(nota)}")'),
        ("print(r.text)", "PRINT_TXT", "print(r.status_code)"),
        ('logger.info("nota=%r", nota)', "BAIXO_TXT", 'logger.info("chars=%s", len(nota))'),
        ('logger.info("x %s", cat)', "BAIXO_TXT", 'logger.info("x")'),
        ('logger.info("x %s", r.category)', "BAIXO_TXT", 'logger.info("x %s", r.reason)'),
        ('logger.info("token=%s", r)', "BAIXO_TXT", 'logger.info("x %s", user_id)'),
        ('logger.info("x %s", categorias)', "BAIXO_TXT", 'logger.info("x %s", len(categorias))'),
        ('logger.info("x %s", cats)', "BAIXO_TXT", 'logger.info("x %s", len(cats))'),
        ('logger.info("x %s", categories)', "BAIXO_TXT", 'logger.info("x %s", len(categories))'),
        ('logger.info("x %s", r.get("nota"))', "BAIXO_TXT", 'logger.info("x %s", r.get("valor"))'),
        ('logger.log(20, "x %s", nota)', "BAIXO_TXT", 'logger.log(20, "x")'),
        ('sys.stdout.buffer.write(nota)', "PRINT_TXT", 'sys.stdout.buffer.write(b"x")'),
        ('warnings.warn(nota)', "PRINT_TXT", 'warnings.warn("x")'),
        ('sys.__stdout__.write(nota)', "PRINT_TXT", 'sys.__stdout__.write("x")'),
        ('logger.info("x %s", notas)', "BAIXO_TXT", 'logger.info("x %s", len(notas))'),
        ('logger.info("x %s", senhas)', "BAIXO_TXT", 'logger.info("x")'),
        ('logger.info("x %s", passwords)', "BAIXO_TXT", 'logger.info("x")'),
        ('logger.info("x %s", (r.get("text") or {}).get("body"))', "BAIXO_TXT",
         'logger.info("x %s", (r.get("meta") or {}).get("id"))'),
        ('logger.info("x %s", r.get("text").get("body"))', "BAIXO_TXT", 'logger.info("x %s", r.get("id"))'),
        ('logger.info("x %s", token)', "BAIXO_TXT", 'logger.info("x %s", token[-6:])'),
        ('logger.info("x %s", token[-20:])', "BAIXO_TXT", 'logger.info("x %s", token[-6:])'),
        ('logger.info("x %s", token[+6:])', "BAIXO_TXT", 'logger.info("x %s", token[-6:])'),
        ('logger.info("x %s", token[-6::-1])', "BAIXO_TXT", 'logger.info("x %s", token[-6:])'),
        ('logger.info("x %s", token[-0:])', "BAIXO_TXT", 'logger.info("x %s", token[-1:])'),
        ('logger.info("x %s", token[-True:])', "BAIXO_TXT", 'logger.info("x %s", token[-6:])'),
        ('logger.info("x %s", textos)', "BAIXO_TXT", 'logger.info("x %s", len(textos))'),
        ('logger.info("x %s", perguntas)', "BAIXO_TXT", 'logger.info("x %s", len(perguntas))'),
        ('logger.info("x %s", secrets)', "BAIXO_TXT", 'logger.info("x %s", len(secrets))'),
        ('logger.info("x %s", cookies)', "BAIXO_TXT", 'logger.info("x %s", len(cookies))'),
        ('logger.info("x %s", code[-6:])', "BAIXO_TXT", 'logger.info("x %s", len(code))'),
        ('logger.info("x %s", senha[-6:])', "BAIXO_TXT", 'logger.info("x %s", len(senha))'),
        ('logger.info("x %s", nota[-6:])', "BAIXO_TXT", 'logger.info("x %s", len(nota))'),
        ('logger.info("x %s", getattr(r, "nota"))', "BAIXO_TXT", 'logger.info("x %s", getattr(r, "valor"))'),
        ('logger.info("título: %s", r)', "BAIXO_TXT", 'logger.info("x: %s", r)'),
        ('logger.info("x %s", nota, extra={"user_id": user_id})', "BAIXO_TXT",
         'logger.info("x", extra={"user_id": user_id})'),
    ])


def test_codigo_e_url_secreta():
    _confere([
        ('logger.info("x %s", codigo)', "BAIXO_TXT", 'logger.info("x %s", len(codigo))'),
        ('logger.info("x %s", code)', "BAIXO_TXT", 'logger.info("x %s", r.status_code)'),
        ('logger.info("x %s", verification_code)', "BAIXO_TXT", 'logger.info("x %s", series_code)'),
        ('logger.info("code=%s", r)', "BAIXO_TXT", 'logger.info("x %s", r)'),
        ('logger.info("código=%s", r)', "BAIXO_TXT", 'logger.info("x %s", r)'),
        ('logger.info("code=%s n=%s", r, len(r))', "BAIXO_TXT", 'logger.info("code=%s", len(r))'),
        ('logger.info("token=%s wa=%s", r, mask_phone(n))', "BAIXO_TXT", 'logger.info("wa=%s", mask_phone(n))'),
        ('print(f"code={r} {len(r)}")', "PRINT_TXT", 'print(f"code={len(r)}")'),
        ('logger.info("x %s", backup_codes)', "BAIXO_TXT", 'logger.info("x %s", len(backup_codes))'),
        ('logger.info("x %s", recovery_codes)', "BAIXO_TXT", 'logger.info("x %s", len(recovery_codes))'),
        ('logger.info("x %s", codigos)', "BAIXO_TXT", 'logger.info("x %s", len(codigos))'),
        ('logger.info("x %s", reset_url)', "BAIXO_TXT", 'logger.info("x %s", base_url)'),
        ('print(reset_link)', "PRINT_TXT", 'print("x")'),
        ('logger.info("x %s", api_key)', "BAIXO_TXT", 'logger.info("x %s", bool(api_key))'),
        ('print(access_token)', "PRINT_TXT", 'print(bool(access_token))'),
    ])


def test_wamid():
    _confere([
        ('logger.info("id=%s", msg_id)', "LOG_WAMID", 'logger.info("id=%s", mask_wamid(msg_id))'),
        ('logger.warning("x %s", message_id)', "LOG_WAMID", 'logger.warning("x %s", mask_wamid(message_id))'),
        ('logger.info("x %s", wamid)', "LOG_WAMID", 'logger.info("x %s", mask_wamid(wamid))'),
        ('print(message_ids)', "LOG_WAMID", 'print([mask_wamid(m) for m in message_ids])'),
        ('logger.info("message_id=%s", n)', "LOG_WAMID", 'logger.info("message_id=%s", mask_wamid(n))'),
        ('print(f"wamid={n}")', "LOG_WAMID", 'print(f"wamid={mask_wamid(n)}")'),
        ('logger.warning("message_id=%s e=%s", n, r)', "LOG_WAMID",
         'logger.warning("message_id=%s e=%s", mask_wamid(n), r)'),
        ('_log_event("info", "m", {"message_id": msg_id})', "LOG_WAMID",
         '_log_event("info", "m", {"message_id": mask_wamid(msg_id)})'),
        ('log_system_event_sync("info", "e", "m", details={"message_id": mid})', "LOG_WAMID",
         'log_system_event_sync("info", "e", "m", details={"message_id": mask_wamid(mid)})'),
        ('log_system_event_sync("info", "e", "m", details={"message_ids": [m.get("id") for m in r]})',
         "LOG_WAMID",
         'log_system_event_sync("info", "e", "m", details={"message_ids": '
         '[mask_wamid(m.get("id")) for m in r]})'),
    ])


def test_o_que_a_guarda_nao_acusa():
    for numero in ('print(f"nota={len(nota)}")', 'logger.info("nota=%s", len(nota))', 'logger.info("x %s", cat_id)',
                   'logger.info("x %s", category_id)', 'logger.info("x %s", cat_key_sql)',
                   'logger.info("x %s", usage.prompt_tokens)', 'logger.info("x %s", text_len)'):
        assert _de(numero) == [], numero
    assert _de('print(f"user={user_id}")') == []            # a D4 permite uid
    assert _de('logger.warning("x %s", nota)') == []        # WARNING+ é da guarda do dono e da #1327


def test_assunto_de_send_email():
    def abertos(corpo: str) -> int:
        return len(_assuntos_abertos(f"async def f(to, nome):\n    {corpo}\n", "x.py"))

    for ruim in ('send_email(to, f"x {nome}", "h")', 'send_email(to=to, subject=nome, html_body="h")',
                 'await asyncio.to_thread(send_email, to, f"x {nome}", "h")',
                 'loop.run_in_executor(None, send_email, to, nome, "h")',
                 'email_service.send_email(to, nome, "h")'):
        assert abertos(ruim) == 1, ruim
    for bom in ('send_email(to, "x", "h")', 'send_email(to, f"x", "h")',
                'send_email(to, f"x {nome}", "h", log_subject="Rótulo")',
                'await asyncio.to_thread(send_email, to, "x", "h")',
                'await asyncio.to_thread(send_email, to, nome, "h", log_subject="Rótulo")'):
        assert abertos(bom) == 0, bom
    assert len(_assuntos_abertos("from e import send_email as se\nse('a', b, 'h')\n", "x.py")) == 1
