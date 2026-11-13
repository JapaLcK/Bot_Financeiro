"""Semeador genérico do T3 de `tests/test_exportacao_completa.py`: uma linha marcada por tabela exportada.

Para cada tabela principal de `EXPORTADAS`, e os pais dela por FK, insere uma linha da pessoa: FK para `users`
recebe o uid, FK para outra tabela recebe a linha DESTA pessoa já semeada (nunca a de outra), texto recebe
`<marca>_<coluna>`, jsonb `{"m": ...}`, e o resto o mínimo que o tipo aceita. Coluna `*_enc` fica nula: um
cifrado falso viraria aviso de "indecifrável" no log, e o aviso vira linha de `eventos_sistema`.
Primeiro tenta com todo texto/jsonb preenchido; se o banco recusar, só com o obrigatório. `_FIXOS` cobre os CHECK.
Tabela que não entra volta com o erro do banco; quem cobra é o teste ("todo dataset tem linha").
"""
from __future__ import annotations

import json
import uuid

from db import ensure_user, get_conn

_COLUNAS = """
    select a.attname as nome, format_type(a.atttypid, a.atttypmod) as tipo, a.attnotnull as obrig,
           (a.atthasdef or a.attidentity <> '') as tem_default, a.attgenerated <> '' as gerada,
           (select e.enumlabel from pg_enum e where e.enumtypid = a.atttypid order by e.enumsortorder limit 1) as enum
    from pg_attribute a where a.attrelid = to_regclass(%s) and a.attnum > 0 and not a.attisdropped
"""
_FKS = """
    select c.conrelid::regclass::text as tabela, c.confrelid::regclass::text as pai, c.confdeltype as regra,
           array(select a.attname from unnest(c.conkey) with ordinality k(n, i)
                 join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.n order by k.i) as cols,
           array(select a.attname from unnest(c.confkey) with ordinality k(n, i)
                 join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.n order by k.i) as pcols
    from pg_constraint c where c.contype = 'f' and c.connamespace = 'public'::regnamespace
"""
_TEXTO = ("text", "jsonb", "character varying")


_POR_TIPO = {"integer": 1, "bigint": 1, "smallint": 1, "boolean": False, "date": "2026-11-01", "inet": "10.0.0.1"}
# O que o CHECK da tabela exige e o tipo sozinho não acerta.
_FIXOS = {
    "metas": {"tipo": "meta", "alvo": 100, "fonte_tipo": "investido", "fonte_ref": None},
    "subscription_marks": {"status": "assinatura"},
    "of_cash_links": {"kind": "saque", "origem": "auto", "status": "ativo"},
    "pix_charges": {"status": "pending", "external_reference": "pix:{uid}", "credit_cents": 0, "extras": "[]"},
}


def _valor(tipo: str, enum: str | None, marca: str):
    if enum:
        return enum
    if tipo in _POR_TIPO:
        return _POR_TIPO[tipo]
    if tipo.startswith("character varying("):
        return marca[:int(tipo[18:-1])]
    if tipo in ("text", "character varying"):
        return marca
    if tipo == "jsonb":
        return json.dumps({"m": marca})
    if tipo == "uuid":
        return str(uuid.uuid4())
    if tipo.startswith(("numeric", "double", "real")):
        return 1
    if tipo.startswith("timestamp"):
        return "2026-11-01"
    return "{}" if tipo.endswith("[]") else None


def _linha(tabela, colunas, fks, minhas, uid, marca, so_obrig):
    linha = {}
    for fk in fks.get(tabela, []):
        for col, pcol in zip(fk["cols"], fk["pcols"]):
            linha.setdefault(col, uid if fk["pai"] == "users" else minhas.get(fk["pai"], {}).get(pcol))
    for c in colunas:
        if c["nome"] in linha or c["gerada"] or c["nome"].endswith("_enc"):
            continue
        texto = c["tipo"].startswith(_TEXTO)
        if c["obrig"] and not c["tem_default"] or (texto and not so_obrig):
            linha[c["nome"]] = _valor(c["tipo"], c["enum"], f"{marca}_{c['nome']}")
    linha.update({k: v.format(uid=uid) if isinstance(v, str) else v for k, v in _FIXOS.get(tabela, {}).items()})
    obrig = {c["nome"] for c in colunas if c["obrig"]}
    return {k: v for k, v in linha.items() if v is not None or k not in obrig}


def _insere(tabela, colunas, fks, minhas, uid, marca) -> str | None:
    """Insere e guarda a linha em `minhas`; devolve o erro do banco na última tentativa, se nenhuma entrou."""
    erro = None
    for so_obrig in (False, True):
        linha = _linha(tabela, colunas, fks, minhas, uid, marca, so_obrig)
        with get_conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(f"insert into {tabela} ({', '.join(linha)}) values ({', '.join(['%s'] * len(linha))})"
                            " returning *", list(linha.values()))
            except Exception as exc:
                conn.rollback()
                erro = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                continue
            minhas[tabela] = dict(cur.fetchone())
            conn.commit()
            return None
    return erro


def semeia(prefixo: str, tabelas: set[str]) -> tuple[int, str, list[tuple[str, str, object]], dict[str, str]]:
    """Cria a pessoa e semeia `tabelas` e os pais delas. Devolve:
    - uid e marca (= prefixo + uid);
    - [(tabela, coluna, valor)] de toda FK `set null` (para `users` ou para outra tabela) das linhas semeadas: a
      limpeza do conftest apaga o usuário em cascata, e essas linhas sobrariam soltas. Apague-as antes;
    - {tabela: erro do banco} do que não entrou."""
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    marca = f"{prefixo}{uid}"
    ensure_user(uid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_FKS)
        fks: dict[str, list] = {}
        for fk in cur.fetchall():
            fks.setdefault(fk["tabela"], []).append(fk)
        pendentes = set(tabelas)
        while True:
            pais = {fk["pai"] for t in pendentes for fk in fks.get(t, []) if fk["pai"] != "users"} - pendentes
            if not pais:
                break
            pendentes |= pais
        colunas = {}
        for t in pendentes:
            cur.execute(_COLUNAS, (t,))
            colunas[t] = cur.fetchall()
    minhas: dict[str, dict] = {}
    erros: dict[str, str] = {}
    tentadas: set[str] = set()
    while faltam := sorted(pendentes - tentadas):  # o pai antes da filha: FK opcional sem pai ficaria nula
        prontas = [t for t in faltam if {fk["pai"] for fk in fks.get(t, [])} & pendentes <= tentadas | {t}] or faltam
        for t in prontas:
            if erro := _insere(t, colunas[t], fks, minhas, uid, marca):
                erros[t] = erro
            tentadas.add(t)
    soltas = [(t, c, minhas[t][c]) for t in minhas for fk in fks.get(t, []) if fk["regra"] == "n"
              for c in fk["cols"] if minhas[t][c] is not None]
    return uid, marca, soltas, erros
