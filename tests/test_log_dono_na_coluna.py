"""O dono de um log vai na COLUNA `user_id`, não no texto, e sem texto cru (#657).

O `_DashboardHandler` (`core/observability.py`) grava `logger.warning()`+ no
`system_event_logs` com `user_id = record.user_id`, que só existe com
`extra={"user_id": <int>}`. Sem ele a coluna fica nula e o uid vai para o texto:
a exclusão de conta (FK) e o filtro por usuário do admin não acham a linha.
`log_system_event*` recebe a coluna direto em `user_id=`. Com a coluna
preenchida, o texto não pode levar `str(exc)` nem traceback: um erro do psycopg
traz `DETAIL: Key (…)=(…)` com dado da linha. Use `type(exc).__name__` e
`getattr(exc, "sqlstate", None)`, no padrão do #656.

Classes (as da issue; a regra adapta o `varre.py` da varredura do #657):
  LOG_MSG_NULL   logger WARNING+ com dono nos argumentos (ou num `extra=` que
                 não é `{"user_id": <int>}`) e a coluna nula
  EVT_MSG_NULL   log_system_event* com coluna nula e dono em `message`
  EVT_DET_NULL   log_system_event* com coluna nula e dono em `details`
  LOG_PII_NULL   logger WARNING+ sem coluna, com telefone/e-mail nos argumentos:
                 por nome, por chave de dict (`{"to": to}`) ou por marcador no
                 texto (`"to=%s"`, `f"phone={n}"`)
  EVT_PII_NULL   idem, em log_system_event*
  EVT_COL_RAW    log_system_event* com coluna e a exceção crua no texto
  LOG_EXTRA_RAW  logger com `extra=` de dono e texto cru (`exc`, `exc_info`,
                 `.exception`)
  LOG_PII_COL    logger com `extra=` de dono e telefone/e-mail por nome ou chave
                 (o logger também escreve no stderr, D4)
Telefone dentro de `mask_phone(...)` não conta (D5 da #657): o dono do número
pode ser desconhecido, e aí o log fica sem coluna, mascarado.

A dívida de hoje mora em `tests/_log_dono_baseline.py` e é um RATCHET por
(arquivo, função, classe): chave nova ou contagem acima reprova, e contagem
ABAIXO também reprova, até alguém diminuir a entrada. Consertou um log? Rode,
da raiz do repositório,

    PYTHONPATH=. .venv/bin/python tests/test_log_dono_na_coluna.py --regenera

e confira no diff que só houve entrada diminuída ou removida. O comando recusa
chave nova ou contagem maior: dívida nova não entra por ele. Rename de função
ou arquivo aparece como chave nova mais entrada que caiu: edite a chave à mão.
Por ser contagem por função, TROCAR uma dívida por outra na mesma função passa.

O QUE ESTA GUARDA NÃO VÊ (medido na varredura do #657, não é prova de
completude):
  * objeto opaco na mensagem (`{res}`, um dict com uids dentro);
  * uid com outro nome (`pid`, `row[0]`, `alvo`);
  * wrapper novo em volta do logger ou do `log_system_event*`, e a função
    passada como valor (`to_thread(log_system_event_sync, …)`, `partial`);
  * mensagem montada fora da chamada (`msg = f"… {uid}"; logger.warning(msg)`);
  * logger sem "log" no nome do receptor;
  * exceção guardada em variável que não é a do `except … as e`;
  * `extra=` que não é dict literal conta como coluna nula: reprova só se
    houver dono visível nos argumentos (`logger.warning("x", extra=ctx)` passa),
    e o texto cru dele não é conferido;
  * com a coluna preenchida, texto do usuário (`descr=%r`) e o marcador de
    telefone no texto não são conferidos, só o nome e a chave;
  * em `core/services/email_service.py::send_email` o e-mail vai em `to_log`
    nos `logger.*` e na `message` dos `_log_email_event`, atrás de wrapper:
    zerar a entrada `_log_email_event`/EVT_PII_NULL NÃO fecha o e-mail;
  * chamada com um `mask_phone` ignora o marcador de telefone no texto: um
    segundo número cru ao lado do mascarado só é visto pelo nome da variável;
  * o dono como atributo de objeto (`user.id`, `u.id`): o nome que casa é `id`;
  * os atributos de exceção seguros (`motivo`, `codigo`, `sqlstate`…) valem
    pelo NOME, em qualquer exceção: um `codigo` com texto livre passaria;
  * funções HOMÔNIMAS no mesmo arquivo (`class A: def g` e `def g`) dividem a
    chave: um conserto numa e uma regressão na outra se anulam. Em 127494b9
    nenhuma chave da dívida coincide.
O universo é o `git ls-files` (`_varre_repo`): arquivo não rastreado não entra,
nem qualquer diretório chamado `tests` (`_IGNORADOS`), em qualquer nível.
O `.venv/bin/python` dos comandos é o do checkout principal: num worktree sem
`.venv`, use o interpretador de lá.
`print` fica fora: a guarda dele é outra fatia da #657.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

import test_log_falha_traceback as _tlft
from _log_dono_baseline import DIVIDA
from test_log_falha_traceback import _apelidos, _nome
from test_max_lines_python import _repo_de_brinquedo

_DONO = re.compile(r"(^|_)(user_?id|uid|uids|user_ids|dono|owner|owner_id|session_uid"
                   r"|target_uid|client_user_id)($|_)|_uid$|^uid_", re.I)
_PII = re.compile(r"(^|_)(e?mail|email_addr|phone|telefone|celular|wa_id|whatsapp"
                  r"|to_number|from_number|recipient|cpf)($|_)|^to$", re.I)
_TXT_DONO = re.compile(r"\b(user_id|uid|user|dono|owner|usuario|usuário)\s*[=:]\s*(%|\{)", re.I)
_TXT_PII = re.compile(r"\b(to|phone|telefone|celular|e-?mail|wa_id|cpf)\s*[=:]\s*(%|\{)", re.I)
_NIVEIS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "fatal"}
_EVT = frozenset({"log_system_event", "log_system_event_sync", "_log_com_teto", "_log_event"})
_N_MSG = {"_log_event": 2}      # scripts/account_deletion_job.py: (level, message, details)
# Atributos da exceção que não trazem o `DETAIL: Key (…)`, direto ou sob
# `e.diag.`: `diag.message_detail`, `pgerror` e `args` trazem, e ficam fora.
# `motivo` e `codigo` são o código curto enumerado das exceções nossas
# (db/accounts.py:340; `CheckoutIndisponivel`/`TitularRecusado`, este pelo
# `_codigo_seguro`). `code` fica FORA: é nome genérico de terceiros
# (`SystemExit("texto").code` é texto livre).
_EXC_SEGURA = {"sqlstate", "constraint_name", "status_code", "__class__", "motivo", "codigo"}

# Fora do ratchet, com o motivo. Chave que nenhum código produz reprova (órfã).
JUSTIFICADAS: dict[tuple[str, str, str], str] = {
    ("core/observability.py", "_log_falha", "LOG_EXTRA_RAW"):
        "o `exc_info=com_traceback` do helper é preso ponto a ponto pela "
        "allowlist de tests/test_log_falha_traceback.py",
}


def _texto(n: ast.AST) -> str | None:
    """String literal; f-string com cada `{…}` virando `{`, para o marcador
    `user_id=` de `f"… user_id={pid}"` casar como o de `"… user_id=%s"`."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{" for v in n.values)
    return None


def _mascara(n: ast.AST) -> bool:
    return isinstance(n, ast.Call) and _nome(n.func) == "mask_phone"


def _cita(nos: list, nome: re.Pattern, texto: re.Pattern | None) -> bool:
    """Nome, atributo, subscript ou chave de dict (o dono ou o telefone no
    `details`) que casa `nome`; marcador `texto` em string. Não contam o que
    está dentro de `mask_phone(...)` nem a chave cujo valor é literal
    (`{"to": "<omitido>"}`)."""
    for topo in nos:
        mascarado = {id(x) for c in ast.walk(topo) if _mascara(c) for x in ast.walk(c)}
        for n in ast.walk(topo):
            if id(n) in mascarado:
                continue
            ident = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute)
                     else n.slice.value if isinstance(n, ast.Subscript)
                     and isinstance(n.slice, ast.Constant) else None)
            if isinstance(ident, str) and nome.search(ident):
                return True
            txt = _texto(n) if texto else None
            if txt is not None and texto.search(txt):
                return True
            if isinstance(n, ast.Dict) and any(
                    isinstance(k, ast.Constant) and isinstance(k.value, str)
                    and nome.search(k.value) and id(v) not in mascarado
                    and not isinstance(v, ast.Constant)
                    for k, v in zip(n.keys, n.values)):
                return True
    return False


def _dono(nos: list) -> bool:
    return _cita(nos, _DONO, _TXT_DONO)


def _pii(nos: list) -> bool:
    """Com um `mask_phone` na chamada, o marcador `to=%s` sozinho não reprova:
    o que preenche o `%s` é o argumento, e o nome dele ainda é conferido."""
    mascara = any(_mascara(x) for topo in nos for x in ast.walk(topo))
    return _cita(nos, _PII, None if mascara else _TXT_PII)


def _coluna(extra: ast.AST | None) -> bool:
    """O handler lê SÓ `record.user_id` (core/observability.py). Vale a chave
    literal `"user_id"` com valor que possa ser int: `None`, texto e `str(…)`
    deixam a coluna nula ou fazem o INSERT falhar."""
    if not isinstance(extra, ast.Dict):
        return False
    for k, v in zip(extra.keys, extra.values):
        if isinstance(k, ast.Constant) and k.value == "user_id":
            return not (isinstance(v, ast.Constant) and not isinstance(v.value, int)) \
                and not isinstance(v, ast.JoinedStr) and _nome(getattr(v, "func", None)) != "str"
    return False


def _cru(nos: list, excs: set[str]) -> bool:
    """A variável do `except` usada como VALOR, ou um `format_exc()`. Só
    `type(e)`, `isinstance(e, …)` e os atributos de `_EXC_SEGURA` (direto ou por
    `getattr` com nome literal) não expõem o texto."""
    for topo in nos:
        seguros = set()
        for n in ast.walk(topo):
            if isinstance(n, ast.Call) and n.args and (
                    _nome(n.func) in {"type", "isinstance"} or _nome(n.func) == "getattr"
                    and len(n.args) > 1 and isinstance(n.args[1], ast.Constant)
                    and n.args[1].value in _EXC_SEGURA):
                seguros.add(id(n.args[0]))
            if isinstance(n, ast.Attribute) and n.attr in _EXC_SEGURA:
                raiz = n.value.value if isinstance(n.value, ast.Attribute) \
                    and n.value.attr == "diag" else n.value      # e.diag.sqlstate
                seguros.add(id(raiz))
        for n in ast.walk(topo):
            if isinstance(n, ast.Name) and n.id in excs and id(n) not in seguros:
                return True
            if isinstance(n, ast.Call) and _nome(n.func) in {
                    "format_exc", "format_exception", "print_exc"}:
                return True
    return False


def _e_logger(receptor: ast.AST) -> bool:
    r = ast.unparse(receptor).lower()
    return "log" in r


def _classes(c: ast.Call, excs: set[str], evt: set[str] = _EVT) -> list[str]:
    kws = {k.arg: k.value for k in c.keywords if k.arg}
    f = c.func
    if isinstance(f, ast.Attribute) and f.attr in _NIVEIS and _e_logger(f.value):
        nivel = f.attr
        if nivel == "log" and c.args and isinstance(c.args[0], ast.Attribute):
            nivel = c.args[0].attr.lower()        # logger.log(logging.INFO, …)
        args = [*c.args, *(v for k, v in kws.items() if k not in {"extra", "exc_info"})]
        extra = kws.get("extra")
        exc_info = kws.get("exc_info")
        cru = _cru(args, excs) or nivel == "exception" or (exc_info is not None and not (
            isinstance(exc_info, ast.Constant) and exc_info.value in (None, False)))
        if _coluna(extra):
            # o logger também vai ao stderr, onde telefone e e-mail não podem ir
            # (D4). Só nome e chave: com o marcador de texto, `"to=%s",
            # to_user_id` viraria falso positivo.
            return [k for k, sim in (("LOG_EXTRA_RAW", cru),
                                     ("LOG_PII_COL", _cita(args, _PII, None))) if sim]
        if nivel in {"debug", "info"}:            # não vão ao banco
            return []
        # `extra={"uid": uid}` entra: o dono é conhecido e a coluna fica nula.
        return [k for k, sim in (("LOG_MSG_NULL", _dono(args + ([extra] if extra else []))),
                                 ("LOG_PII_NULL", _pii(args))) if sim]
    nm = _nome(f)
    if nm not in evt:
        return []
    col = kws.get("user_id")
    pos = c.args[1:] if nm == "_log_com_teto" else c.args
    n_msg = _N_MSG.get(nm, 3)
    msg = [*pos[:n_msg], *([kws["message"]] if "message" in kws else [])]
    det = [*pos[n_msg:], *(v for k, v in kws.items()
                           if k not in {"message", "user_id", "source", "level", "event_type"})]
    if col is None or (isinstance(col, ast.Constant) and col.value is None):
        return [k for k, sim in (("EVT_MSG_NULL", _dono(msg)), ("EVT_DET_NULL", _dono(det)),
                                 ("EVT_PII_NULL", _pii(msg + det))) if sim]
    return ["EVT_COL_RAW"] if _cru(msg + det, excs) else []


def _achados(fonte: str | bytes, rel: str) -> list[tuple[str, str, str]]:
    """Extrator para `_varre_repo`: (arquivo, função mais interna, classe)."""
    arvore = ast.parse(fonte)
    evt = _apelidos(arvore, _EVT)                 # `import … as lse`, `lse = …`
    func: dict[ast.AST, str] = {}
    excs: dict[ast.AST, set[str]] = {}
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for filho in ast.walk(no):
                func[filho] = no.name     # BFS: a função mais INTERNA sobrescreve
        if isinstance(no, ast.ExceptHandler) and no.name:
            for filho in ast.walk(no):
                excs.setdefault(filho, set()).add(no.name)
    return [(rel, func.get(no, "<módulo>"), classe) for no in ast.walk(arvore)
            if isinstance(no, ast.Call) for classe in _classes(no, excs.get(no, set()), evt)]


def _compara(atual: Counter, base: dict) -> tuple[list, list]:
    """(acima, abaixo) como [(chave, base, atual)]. Chave nova tem base 0."""
    acima = sorted((k, base.get(k, 0), n) for k, n in atual.items() if n > base.get(k, 0))
    abaixo = sorted((k, b, atual[k]) for k, b in base.items() if atual[k] < b)
    return acima, abaixo


def _atual(achados: list) -> Counter:
    return Counter(k for k in achados if k not in JUSTIFICADAS)


def test_ratchet_do_dono_na_coluna():
    achados = _tlft._varre_repo(_achados)
    orfas = sorted(set(JUSTIFICADAS) - set(achados))
    assert not orfas, f"JUSTIFICADAS com entrada que nenhum código produz, remova: {orfas}"
    acima, abaixo = _compara(_atual(achados), DIVIDA)
    erros = []      # uma falha só: num rename, o "acima" e o "abaixo" vêm juntos
    if acima:
        erros.append(
            "Log novo com o dono fora da coluna ou com texto cru (#657). Ponha o uid "
            "em `extra={\"user_id\": <int>}` (logger) ou `user_id=` (log_system_event*) "
            "e troque `str(exc)`/`exc_info` por `type(exc).__name__` + sqlstate. "
            "Trocou de LOG_MSG_NULL para LOG_EXTRA_RAW na mesma função? NÃO é rename: "
            "com a coluna, o texto também não pode levar `exc`/`exc_info`/`.exception`; "
            "registre `type(exc).__name__` e o sqlstate (D6). "
            "Chave nova (base 0) junto de uma entrada que DIMINUIU, com a MESMA classe, "
            "pode ser só RENAME de função ou arquivo: aí a dívida é a mesma, edite a "
            "chave à mão em tests/_log_dono_baseline.py (o --regenera recusa chave "
            "nova). Regra no docstring de tests/test_log_dono_na_coluna.py. "
            "(arquivo, função, classe), base -> agora: "
            + "; ".join(f"{k} {b} -> {n}" for k, b, n in acima))
    if abaixo:
        erros.append(
            "A dívida DIMINUIU: diminua a entrada em tests/_log_dono_baseline.py (ou "
            "remova se chegou a 0)."
            + ("" if acima else      # com chave nova junto, o --regenera recusa
               " Comando: PYTHONPATH=. .venv/bin/python tests/test_log_dono_na_coluna.py "
               "--regenera.")
            + " (arquivo, função, classe), base -> agora: "
            + "; ".join(f"{k} {b} -> {n}" for k, b, n in abaixo))
    assert not erros, "\n\n".join(erros)


def test_varredura_ponta_a_ponta_pelo_git(tmp_path, monkeypatch):
    """O `test_ratchet_do_dono_na_coluna` de verdade, num repo de brinquedo (a
    `DIVIDA` e as `JUSTIFICADAS` trocadas): o rastreado vira chave acima da base
    (inclusive latin-1 com cookie de coding), o solto não rastreado não entra,
    a base maior que o código reprova com "DIMINUIU", o rename reprova numa
    falha só com os dois lados (sem o comando que recusaria), a JUSTIFICADAS
    órfã reprova, e rastreado que não parseia reprova com o nome."""
    log = "import logging\nlogger = logging.getLogger(__name__)\n"
    raiz = _repo_de_brinquedo(tmp_path, {
        "core/novo.py": f"{log}def tick(user_id):\n    logger.warning('x user=%s', user_id)\n"
                        .encode(),
        "core/latin.py": b"# -*- coding: latin-1 -*-\n" + log.encode()
                         + b"def f(user_id):\n    logger.warning('ol\xe1 user=%s', user_id)\n",
    })
    (raiz / "core" / "solto.py").write_text(
        f"{log}def g(user_id):\n    logger.warning('x user=%s', user_id)\n", encoding="utf-8")
    monkeypatch.setattr(_tlft, "_RAIZ", raiz)
    monkeypatch.setitem(globals(), "JUSTIFICADAS", {})
    chave = ("core/novo.py", "tick", "LOG_MSG_NULL")
    latin = ("core/latin.py", "f", "LOG_MSG_NULL")
    NOVO, MENOS = "Log novo com o dono", "A dívida DIMINUIU"

    def ratchet(divida: dict) -> str:
        monkeypatch.setitem(globals(), "DIVIDA", divida)
        with pytest.raises(AssertionError) as falha:
            test_ratchet_do_dono_na_coluna()
        return str(falha.value)

    msg = ratchet({})
    assert NOVO in msg and MENOS not in msg and "solto" not in msg
    assert f"{latin} 0 -> 1" in msg and f"{chave} 0 -> 1" in msg
    msg = ratchet({latin: 1, chave: 2})
    assert MENOS in msg and NOVO not in msg and f"{chave} 2 -> 1" in msg and "Comando:" in msg
    velha = ("core/novo.py", "tick_velho", "LOG_MSG_NULL")      # rename de função
    msg = ratchet({latin: 1, velha: 1})
    assert NOVO in msg and MENOS in msg and f"{chave} 0 -> 1" in msg and f"{velha} 1 -> 0" in msg
    assert "Comando:" not in msg                # o --regenera recusaria a chave nova
    monkeypatch.setitem(globals(), "DIVIDA", {latin: 1, chave: 1})
    test_ratchet_do_dono_na_coluna()                            # base = código: verde
    monkeypatch.setitem(globals(), "JUSTIFICADAS",
                        {("core/novo.py", "inexistente", "LOG_MSG_NULL"): "x"})
    assert "JUSTIFICADAS com entrada" in ratchet({latin: 1, chave: 1})

    (raiz / "core" / "quebrado.py").write_text("def (:\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(raiz), "add", "-A"], check=True)
    with pytest.raises(AssertionError, match="core/quebrado.py"):
        _tlft._varre_repo(_achados)


def _regenera() -> None:
    """Reescreve só o dict `DIVIDA` de `_log_dono_baseline.py`. Recusa subir."""
    atual = _atual(_tlft._varre_repo(_achados))
    acima, _ = _compara(atual, DIVIDA)
    if acima:
        sys.exit("recusado, dívida nova não entra por aqui (rename? edite a chave à "
                 "mão; LOG_MSG_NULL -> LOG_EXTRA_RAW NÃO é rename, tire o texto cru): "
                 + "; ".join(f"{k} {b} -> {n}" for k, b, n in acima))
    base = Path(__file__).with_name("_log_dono_baseline.py")
    cabeca = base.read_text(encoding="utf-8").partition("\nDIVIDA: ")[0]
    corpo = "".join(f"    {k!r}: {n},\n" for k, n in sorted(atual.items()))
    base.write_text(f"{cabeca}\nDIVIDA: dict[tuple[str, str, str], int] = {{\n{corpo}}}\n",
                    encoding="utf-8")
    print(f"{base}: {len(atual)} chaves, {sum(atual.values())} ocorrências")


if __name__ == "__main__":
    if sys.argv[1:] != ["--regenera"]:
        sys.exit(f"uso: {sys.argv[0]} --regenera (recebido: {sys.argv[1:]})")
    _regenera()
