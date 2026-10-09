"""Guarda de classe da ordem de lock (#622): escritor novo sem `_lock_user`.

A regra está na docstring de `db.bank_movements._lock_user`. Aqui só o que dá para
checar por AST: função de `db/` que abre a PRÓPRIA transação (`get_conn(`), escreve
ou trava (`insert`/`update`/`delete`/`for update`), toca 2+ famílias entre launches,
caixinhas, investimentos, lotes e accounts, e não chama `_lock_user`.

LIMITE MEDIDO: só pega função que abre `get_conn` e toca 2+ famílias NO PRÓPRIO CORPO.
Remover o `_lock_user` do renome (`update_pocket_meta` só toca `pockets` e delega o
histórico a `_renomear_no_historico`) ou o ramo create/delete_pocket do `_precisa_lock`
deixa este guard VERDE; quem protege esses dois é `test_lock_ordem_caixinha.py`. Também
é cego a função que recebe o cursor do chamador e a tabela montada por f-string.
Cego também a `_lock_user` CONDICIONAL (basta o nome aparecer: o undo,
`delete_launch_and_rollback`, o toma só em alguns ramos) e a f-string
(`delete_user_data` monta `delete from {table}`): esses dois quem protege são
`test_lock_ordem_undo_comum.py` e `test_lock_ordem_exclusao.py`.
Entrada nova na allowlist exige o motivo escrito.
"""
import ast
import pathlib
import re

_FAMILIAS = {
    "launches": r"\b(?:from|into|update|join)\s+launches\b",
    "pockets": r"\b(?:from|into|update|join)\s+pockets\b",
    "investments": r"\b(?:from|into|update|join)\s+investments\b",
    "lotes": r"\b(?:from|into|update|join)\s+(?:pocket|investment)_lots\b",
    "accounts": r"\b(?:from|into|update|join)\s+accounts\b",
}
_ESCREVE = r"\b(?:insert\s+into|update|delete\s+from|for\s+update)\b"

_PERMITIDOS = {
    ("db/pockets.py", "create_pocket"): "só INSERT: não pede lock em linha existente",
    ("db/schema.py", "init_db"): "DDL/migração sob SCHEMA_INIT_LOCK, sem tráfego de usuário",
    ("db/accounts.py", "add_launch_and_update_balance"):
        "o próprio `update accounts` é o primeiro lock da transação: é a ordem da regra",
}


def _achados(fonte: str, arquivo: str) -> set[tuple[str, str]]:
    achados = set()
    for fn in ast.walk(ast.parse(fonte)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        chamadas = {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                    for c in ast.walk(fn) if isinstance(c, ast.Call)}
        if "get_conn" not in chamadas or "_lock_user" in chamadas:
            continue
        sql = " ".join(n.value.lower() for n in ast.walk(fn)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str))
        if re.search(_ESCREVE, sql) and sum(bool(re.search(rx, sql)) for rx in _FAMILIAS.values()) >= 2:
            achados.add((arquivo, fn.name))
    return achados


def test_a_varredura_enxerga_o_que_promete():
    """Sem isto a guarda podia ficar cega e continuar verde."""
    fonte = ('def f(uid):\n  with get_conn() as c:\n    c.execute("select 1 from pockets for update")\n'
             '    c.execute("delete from launches where id=1")\n'
             'def g(uid):\n  with get_conn() as c:\n    _lock_user(c, uid)\n'
             '    c.execute("select 1 from pockets for update")\n    c.execute("delete from launches")\n')
    assert _achados(fonte, "x.py") == {("x.py", "f")}


def test_escritor_de_varias_familias_sem_lock_user_esta_na_allowlist():
    raiz = pathlib.Path(__file__).resolve().parent.parent
    achados = set()
    for arq in sorted((raiz / "db").glob("*.py")):
        achados |= _achados(arq.read_text(), arq.relative_to(raiz).as_posix())
    assert achados == set(_PERMITIDOS), (
        f"novos: {sorted(achados - set(_PERMITIDOS))}; velhos: {sorted(set(_PERMITIDOS) - achados)}. "
        "Escritor novo chama `_lock_user` (docstring dele); se não precisa, entre na allowlist com o motivo.")
