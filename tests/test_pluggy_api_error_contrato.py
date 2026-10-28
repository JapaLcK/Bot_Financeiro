"""Contrato da `PluggyApiError` (#657, opção c): a mensagem é toda NOSSA.

O `_falha_da_pluggy` (`core/services/pluggy_sync.py`) põe `str(exc)` da
`PluggyApiError` no stdout, que não passa pela exclusão LGPD. Isso só é seguro
enquanto nenhuma mensagem dela carregar dado da resposta: o corpo de erro da
Pluggy traz nome, CPF e número de conta do titular. Esta guarda AST lê toda
chamada fora de `tests/` (universo `git ls-files`, pelo `_varre_repo` de
`tests/test_log_falha_traceback.py`) e exige:

- todo argumento de `PluggyApiError(...)` é literal, ou f-string, `+`, `IfExp`
  e `or` que só interpolam nomes da `_BRANCA_ERRO`;
- todo argumento de `incompleta(...)` (`pluggy_investments.py`) é literal;
- o contexto (2º argumento) de `_raise_for_pluggy_response(...)` segue a mesma
  regra, com a `_BRANCA_CONTEXTO`.

O teste de comportamento do mesmo contrato (o corpo com PII não entra no
`str(exc)` nem no stderr) é `tests/test_of_log_stderr.py::
test_pluggy_api_error_nunca_leva_o_corpo_da_resposta`.

CLASSE CEGA (a guarda lê o NOME, não o valor):
- nome da lista branca reatribuído com dado da resposta (`context = resp.text`,
  `motivo = data["message"]`) passa;
- wrapper com outro nome que recebe a mensagem num parâmetro de nome da lista
  branca (como o próprio `incompleta(motivo)`, que só é coberto porque a guarda
  conhece o nome dele), apelido de import (`PluggyApiError as E`), reescrita de
  `exc.args` antes do `raise` (`e.args = (str(data),)`), nova instância pelo
  tipo (`raise type(e)(str(data))`) e subclasse que monta a mensagem no
  `super().__init__` passam.

CONTROLES (medidos): `{data}` interpolado no raise de `pluggy_bills.py:41`,
`incompleta(f"{item}")` e `{body}` no contexto de um `_raise_for_pluggy_response`
deixam `test_mensagem_da_pluggy_api_error_e_toda_nossa` vermelho; a árvore de
hoje fica verde.
"""
from __future__ import annotations

import ast

from test_log_falha_traceback import _varre_repo

_BRANCA_ERRO = {"context", "code", "max_pages", "motivo", "resp.status_code"}
_BRANCA_CONTEXTO = {"path", "item_id"}


def _nome(no: ast.AST) -> str | None:
    """`a` ou `a.b` (o atributo pontuado), senão None."""
    if isinstance(no, ast.Name):
        return no.id
    if isinstance(no, ast.Attribute) and (base := _nome(no.value)):
        return f"{base}.{no.attr}"
    return None


def _so_nosso(no: ast.AST, branca: set[str]) -> bool:
    if isinstance(no, ast.Constant):
        return True
    if isinstance(no, ast.JoinedStr):
        return all(_so_nosso(v, branca) for v in no.values)
    if isinstance(no, ast.FormattedValue):
        return _so_nosso(no.value, branca)
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        return _so_nosso(no.left, branca) and _so_nosso(no.right, branca)
    if isinstance(no, ast.IfExp):   # o teste não entra na mensagem
        return _so_nosso(no.body, branca) and _so_nosso(no.orelse, branca)
    if isinstance(no, ast.BoolOp):  # `code or None`
        return all(_so_nosso(v, branca) for v in no.values)
    return _nome(no) in branca


def _chamadas(fonte: bytes, rel: str) -> list[tuple[str, int, str, bool]]:
    """Extrator para `_varre_repo`: (arquivo, linha, chamada, segura?)."""
    achados = []
    for no in ast.walk(ast.parse(fonte)):
        if not isinstance(no, ast.Call):
            continue
        alvo = _nome(no.func) or ""
        nome = alvo.rsplit(".", 1)[-1]
        if nome == "PluggyApiError":
            args = [*no.args, *(k.value for k in no.keywords)]
            ok = all(_so_nosso(a, _BRANCA_ERRO) for a in args)
        elif nome == "incompleta":
            args = [*no.args, *(k.value for k in no.keywords)]
            ok = all(isinstance(a, ast.Constant) for a in args)
        elif nome == "_raise_for_pluggy_response":
            ctx = no.args[1] if len(no.args) > 1 else next(
                (k.value for k in no.keywords if k.arg == "context"), None)
            ok = ctx is not None and _so_nosso(ctx, _BRANCA_CONTEXTO)
        else:
            continue
        achados.append((rel, no.lineno, nome, ok))
    return achados


def test_mensagem_da_pluggy_api_error_e_toda_nossa():
    achados = _varre_repo(_chamadas)
    por_tipo = {n: sum(1 for a in achados if a[2] == n)
                for n in ("PluggyApiError", "incompleta", "_raise_for_pluggy_response")}
    # Piso: com zero chamadas de um tipo, a guarda passaria vazia (renomeação, universo errado).
    assert all(por_tipo.values()), f"guarda sem universo: {por_tipo}"
    fora = [f"{rel}:{linha} {nome}" for rel, linha, nome, ok in achados if not ok]
    assert not fora, (
        "mensagem de PluggyApiError com valor fora da lista branca: o `str(exc)` vai "
        f"ao stdout pelo `_falha_da_pluggy` e não pode levar dado da resposta: {fora}")
