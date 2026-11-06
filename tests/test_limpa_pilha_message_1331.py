"""#1331, P3: `scripts/limpa_pilha_message_1331.sql`, o corte único das linhas antigas com dono.

O teste lê os comandos do PRÓPRIO script e os executa em ordem numa sessão real, com a tabela
temporária `system_event_logs` à frente da pública (`pg_temp` vem antes no `search_path`). As
mensagens são as que o handler antigo gravava: `Formatter().format(record)[:1000]`.

CONTROLES (CLAUDE.md §3): as mutações de `MUTACOES` são aplicadas ao texto lido do arquivo e cada
uma tem de produzir os defeitos esperados. O positivo é o mesmo `_defeitos` vazio no script como está.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import traceback
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from _log_dono_helpers import _violacao, banco_real  # noqa: F401 (banco_real é fixture autouse)

ARQ = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "limpa_pilha_message_1331.sql"
(VERSAO, MEDE1, TAMANHO, BEGIN, LOCK, TETO, UPDATE, MEDE4, LIKE4, RESIDUO, MEDE6, LIKE6) = range(12)
CABECALHO = "\nTraceback (most recent call last)"
A, B = 111, 222
INTACTA = object()


def _comandos(texto: str) -> list[str]:
    """Sem as linhas de comentário, o `;` só separa comandos: nenhum comando o tem dentro."""
    sem_comentario = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("--"))
    return [c.strip() for c in sem_comentario.split(";") if c.strip()]


CMDS = _comandos(ARQ.read_text(encoding="utf-8"))


def _antigo(msg, args=(), exc=None, exc_text=None):
    """A `message` que o `_DashboardHandler` gravava antes da #1331."""
    ei = (type(exc), exc, exc.__traceback__) if exc else None
    r = logging.LogRecord("t", logging.ERROR, __file__, 1, msg, args, ei)
    r.exc_text = exc_text
    return logging.Formatter().format(r)[:1000]


def _pega(f):
    try:
        f()
    except Exception as e:  # noqa: BLE001
        return e


def _levanta(e):
    raise e


def _encadeada():
    try:
        raise KeyError("dentro-pii@x.com")
    except KeyError as e:
        raise RuntimeError("fora") from e


def _contexto():
    try:
        raise KeyError("ctx-pii@x.com")
    except KeyError:
        raise RuntimeError("fora-pii@x.com")


def _causa_nunca_levantada():
    raise RuntimeError("fora") from ValueError("causa-pii@x.com")


def _causa_sem_error():
    raise RuntimeError("fora") from StopIteration("causa-pii@x.com")


def _contexto_sem_error():
    e = RuntimeError("fora")
    e.__context__ = StopIteration("ctx-pii@x.com")
    raise e


SIMPLES = _pega(lambda: 1 / 0)
PILHA = "".join(traceback.format_exception(SIMPLES))

# (id, dono, message antes, message depois)
CASOS = [
    (1, A, _antigo("teste_1331 falha controlada %s", ("x",), SIMPLES), "teste_1331 falha controlada x"),
    (2, A, _antigo("grupo falhou", exc=_pega(lambda: _levanta(ExceptionGroup("g", [ValueError("v")])))),
     "grupo falhou"),
    (3, A, _antigo("encadeada", exc=_pega(_encadeada)), "encadeada"),
    (4, A, _antigo("filtro redigiu", exc_text=PILHA.replace("division", "Failing row contains (omitido)")),
     "filtro redigiu"),
    (5, A, _antigo("falha: %s", (PILHA,)), "falha:"),                                # pilha nos argumentos
    (6, A, _antigo("y" * 900, exc=SIMPLES), "y" * 900),                              # pilha cortada em 1000
    (7, A, _antigo("", exc=SIMPLES), ""),
    (8, A, _antigo("linha1\nlinha2", exc=SIMPLES), "linha1\nlinha2"),
    (9, A, _antigo("termina em quebra\n", exc=SIMPLES), "termina em quebra"),
    (10, A, "sem pilha aqui", INTACTA),
    (11, A, "a palavra Traceback sozinha", INTACTA),
    (12, None, _antigo("sem dono", exc=SIMPLES), INTACTA),
    (13, B, "outro usuário, nada a cortar", INTACTA),
    (14, A, "z" * 980 + "\nTraceback (most rec", INTACTA),                           # cabeçalho truncado
    (15, A, _antigo("w" * (1000 - len(CABECALHO)), exc=SIMPLES), INTACTA),           # "...last)" sem o ":"
    (16, A, _antigo("op contexto", exc=_pega(_contexto)), "op contexto"),
    (17, A, _antigo("msg do usuario: %s", ("olha Traceback (most recent call last): rs",), SIMPLES),
     "msg do usuario: olha"),
    (18, A, _antigo("linha1\r\nlinha2\r\n", exc=SIMPLES), "linha1\r\nlinha2"),       # CRLF
    (19, A, _antigo("ação ✓ 💸 %s", ("ñ",), SIMPLES), "ação ✓ 💸 ñ"),
    (20, A, _antigo("q" * (999 - len(CABECALHO)), exc=SIMPLES), "q" * (999 - len(CABECALHO))),  # termina no ":"
    # Não começam por `Traceback`: o UPDATE deixa a linha da causa, e o resíduo a conta.
    (21, A, _antigo("op causa", exc=_pega(_causa_nunca_levantada)),
     "op causa\nValueError: causa-pii@x.com\n\n"
     "The above exception was the direct cause of the following exception:\n"),
    (22, A, _antigo("x", exc=ValueError("nunca-levantada-pii@x.com")), INTACTA),
    (23, A, _antigo("op psycopg", exc=_violacao("pii")), INTACTA),
    # Várias linhas legítimas: nem o corte nem o resíduo as pegam.
    (24, A, "resumo do dia:\nTotal: 10\nitens: 3", INTACTA),
    (25, A, "linha1\nlinha2: ok\nValorErrado sem dois pontos", INTACTA),
    (26, A, "falhou com texto do usuário: Error: algo\nsegue", INTACTA),
    # Uma alternativa da regex do resíduo por caso: só ela os alcança.
    (27, A, _antigo("op causa2", exc=_pega(_causa_sem_error)),
     "op causa2\nStopIteration: causa-pii@x.com\n\n"
     "The above exception was the direct cause of the following exception:\n"),
    (28, A, _antigo("op ctx", exc=_pega(_contexto_sem_error)),
     "op ctx\nStopIteration: ctx-pii@x.com\n\n"
     "During handling of the above exception, another exception occurred:\n"),
    (29, A, _antigo("y", exc=BaseException("termina-em-exception-pii@x.com")), INTACTA),
]
RESIDUO_ESPERADO = {21, 22, 23, 27, 28, 29}
COM_CORTE = sum(1 for c in CASOS if c[3] is not INTACTA)


def _roda(cmds: list[str], scs: str) -> SimpleNamespace:
    """Os passos 1 a 6 na ordem, numa sessão só. O `COMMIT` é a escolha do dono no passo 5."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"set standard_conforming_strings = {scs}")
        conn.execute("create temp table system_event_logs (like public.system_event_logs including defaults)")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into system_event_logs (id, level, event_type, message, source, user_id, details)"
                " values (%s, 'error', 'logger.error', %s, 't', %s, %s)",
                [(i, msg, uid, '{"logger": "t", "traceback": "Traceback (most recent call last): X"}')
                 for i, uid, msg, _ in CASOS])

        def fotografa():
            return conn.execute("select * from system_event_logs order by id").fetchall()

        def conta(n):
            return conn.execute(cmds[n]).fetchone()["count"]

        antes = fotografa()
        conn.execute(cmds[VERSAO])
        medido = conta(MEDE1)
        for c in cmds[TAMANHO:UPDATE]:
            conn.execute(c)
        tocadas = conn.execute(cmds[UPDATE]).rowcount
        depois = fotografa()
        segunda = conn.execute(cmds[UPDATE]).rowcount
        r = SimpleNamespace(
            antes=antes, medido=medido, tocadas=tocadas, depois=depois, segunda=segunda,
            depois2=fotografa(), mede4=conta(MEDE4), like4=conta(LIKE4),
            residuo={x["id"] for x in conn.execute(cmds[RESIDUO].replace("count(*)", "id"))})
        r.transacao = conn.info.transaction_status
        conn.execute("commit")
        r.mede6, r.like6 = conta(MEDE6), conta(LIKE6)
    return r


def _defeitos(r: SimpleNamespace) -> set[str]:
    d = set()
    for (i, _, msg, esperado), a, b in zip(CASOS, r.antes, r.depois):
        if b["message"] != (msg if esperado is INTACTA else esperado):
            d.add(f"id {i}")
        if {k: v for k, v in a.items() if k != "message"} != {k: v for k, v in b.items() if k != "message"}:
            d.add(f"coluna {i}")
    if r.transacao != psycopg.pq.TransactionStatus.INTRANS:
        d.add(f"transacao {r.transacao.name}")
    if r.segunda or r.depois != r.depois2:
        d.add(f"2a rodada {r.segunda}")
    for nome in ("mede4", "like4", "mede6", "like6"):
        if getattr(r, nome):
            d.add(f"{nome} {getattr(r, nome)}")
    if r.residuo != RESIDUO_ESPERADO:
        d.add(f"residuo {sorted(r.residuo)}")
    return d


def test_o_arquivo_tem_os_passos_na_ordem_e_nao_faz_commit():
    assert [c.split()[0].upper() for c in CMDS] == [
        "SELECT", "SELECT", "SELECT", "BEGIN", "SET", "SET", "UPDATE", "SELECT", "SELECT", "SELECT", "SELECT", "SELECT"]
    assert CMDS[MEDE1] == CMDS[MEDE4] == CMDS[MEDE6] and CMDS[LIKE4] == CMDS[LIKE6]
    regex = re.search(r"\(SELECT E'.*' AS re\) p", CMDS[UPDATE]).group()
    assert regex in CMDS[MEDE1], "o WHERE, o corte e a medição têm de usar a mesma regex"
    assert "~" not in CMDS[LIKE4], "a contagem do `like` não pode depender da regex"
    assert "version()" in CMDS[VERSAO]
    assert CMDS[BEGIN] == "BEGIN"
    assert CMDS[LOCK] == "SET LOCAL lock_timeout = '5s'"
    assert CMDS[TETO] == "SET LOCAL statement_timeout = '5min'"
    texto = ARQ.read_text(encoding="utf-8")
    decisao = texto[texto.index(CMDS[RESIDUO]) + len(CMDS[RESIDUO]):texto.index("-- 6. ")].splitlines()
    assert "-- COMMIT;" in decisao and "-- ROLLBACK;" in decisao, "a decisão fica entre o resíduo e o passo 6"
    assert len(CASOS) == 29 and len(CASOS[14][2]) == len(CASOS[19][2]) == 1000


@pytest.mark.parametrize("scs", ["on", "off"])
def test_o_script_corta_so_com_dono_e_e_idempotente(scs):
    r = _roda(CMDS, scs)
    assert _defeitos(r) == set()
    assert r.medido == r.tocadas == COM_CORTE == 17, (r.medido, r.tocadas)


def _update(*trocas: tuple[str, str]) -> list[str]:
    novo = CMDS[UPDATE]
    for de, para in trocas:
        assert de in novo, f"mutação sem efeito: {de!r} não está no arquivo"
        novo = novo.replace(de, para)
    return [*CMDS[:UPDATE], novo, *CMDS[UPDATE + 1:]]


MUTACOES = {
    "sem_filtro_de_dono": (_update(("s.user_id IS NOT NULL", "true")), "on", {"id 12"}),
    "corte_ingenuo": (_update(("regexp_replace(s.message, p.re, '')",
                               r"left(s.message, strpos(s.message, E'\nTraceback (most recent call last):') - 1)")),
                      "on", {"id 2", "id 5", "id 17", "id 18"}),
    "sql_da_rodada_1": (_update(("AND s.message ~ p.re", "AND s.message LIKE '%Traceback (most recent call last)%'"),
                                (r"E'\r?\n?", r"E'\n?")), "on", {"id 18", "2a rodada 1"}),
    "literal_sem_E": (_update((r"E'\r?\n?", r"'\r?\n?"), (r"\\(", r"\("), (r"\\)", r"\)")),
                      "off", {"id 1", f"mede4 {COM_CORTE}", f"like4 {COM_CORTE}"}),
}


@pytest.mark.parametrize("nome", MUTACOES)
def test_controle_negativo_mutacao_reprova(nome):
    cmds, scs, esperados = MUTACOES[nome]
    defeitos = _defeitos(_roda(cmds, scs))
    assert esperados <= defeitos, defeitos
