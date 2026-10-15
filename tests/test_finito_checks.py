"""CHECK `finito_<coluna>` em toda coluna numérica (db/finito_checks.py).

Banco real. Quatro grupos, cada um com controle negativo (a peça desligada ou ausente
deixa o teste vermelho) e positivo (o caminho legítimo segue passando):
estrutural (o schema inteiro), semântico (o que o CHECK recusa e aceita), idempotência
e tudo-ou-nada / lock. Os canais (WhatsApp, rota, v2, tool) estão em test_finito_canais.py.
"""
import logging
import threading
import time

import psycopg
import psycopg.rows
import pytest

from db import get_conn
from db import finito_checks as F
from utils_text import VALOR_MAX


def _q(sql, args=(), fetch=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if fetch else None


def _ensure():
    r = F.ensure_finito_checks_once()
    assert r is not None
    return r


def _cur_auto():
    conn = psycopg.connect(__import__("os").environ["DATABASE_URL"], autocommit=True,
                           row_factory=psycopg.rows.dict_row)
    return conn, conn.cursor()


@pytest.fixture
def probe():
    """Tabela descartável com numeric E double precision (o schema não tem nenhuma float)."""
    _q("create table zz_finito_probe (id serial primary key, x numeric, y double precision)")
    yield "zz_finito_probe"
    _q("drop table if exists zz_finito_probe")


def _lacunas(cur) -> list[str]:
    """Colunas-alvo sem `finito_<col>` com a definição esperada (abs(col) <= VALOR_MAX)."""
    out = []
    for t, c in F.colunas_alvo(cur):
        nome = F.nome_check(c)
        assert len(nome.encode()) <= 63, nome
        cur.execute("select pg_get_constraintdef(oid) as d from pg_constraint where conname = %s "
                    "and conrelid = to_regclass(%s)", (nome, f"public.{t}"))
        d = cur.fetchone()
        if not d or f"abs({c})" not in d["d"] or str(F.limite_da_coluna(t, c)) not in d["d"] or "<=" not in d["d"]:
            out.append(f"{t}.{c}")
    return out


# ── (1) estrutural ───────────────────────────────────────────────────────────

def test_toda_coluna_numerica_do_schema_tem_o_check(probe):
    _ensure()
    with get_conn() as conn, conn.cursor() as cur:
        assert _lacunas(cur) == []
        # entrada de SEM_CHECK_FINITO obsoleta (coluna sumiu ou mudou de tipo) = lista mentindo
        for (t, c), motivo in F.SEM_CHECK_FINITO.items():
            cur.execute("select data_type from information_schema.columns where table_schema='public' "
                        "and table_name=%s and column_name=%s", (t, c))
            r = cur.fetchone()
            assert r and r["data_type"] in ("numeric", "double precision", "real"), (t, c)
            assert motivo.strip()
        cur.execute("select count(*) as n from pg_constraint where conname like 'finito\\_%%' "
                    "and not convalidated")
        assert cur.fetchone()["n"] == 0  # o VALIDATE roda no mesmo ensure


def test_controle_negativo_coluna_nova_depois_do_ensure_e_acusada(probe):
    _ensure()
    _q("alter table zz_finito_probe add column z numeric")  # coluna nascida depois do boot
    with get_conn() as conn, conn.cursor() as cur:
        assert _lacunas(cur) == ["zz_finito_probe.z"]  # o teste estrutural a acusaria
    r = _ensure()  # positivo: o boot seguinte a cobre
    assert r["criadas"] == ["zz_finito_probe.z"] and r["falhas"] == []
    with get_conn() as conn, conn.cursor() as cur:
        assert _lacunas(cur) == []


def test_controle_negativo_check_derrubado_e_acusado(probe):
    _ensure()
    _q("alter table zz_finito_probe drop constraint finito_x")
    with get_conn() as conn, conn.cursor() as cur:
        assert _lacunas(cur) == ["zz_finito_probe.x"]


# ── (2) semântico ────────────────────────────────────────────────────────────

RECUSADOS = ["NaN", "Infinity", "-Infinity", "1e14", "-1e14", "10000000000000.01"]
ACEITOS = ["0", "-5", "10000000000000", "-10000000000000", None, "2000000000"]


@pytest.mark.parametrize("coluna", ["x", "y"])
@pytest.mark.parametrize("valor", RECUSADOS)
def test_check_recusa_nao_finito_e_acima_do_limite(probe, coluna, valor):
    _ensure()
    with pytest.raises(psycopg.errors.CheckViolation) as e:
        _q(f"insert into zz_finito_probe ({coluna}) values (%s::{'numeric' if coluna == 'x' else 'float8'})",
           (valor,))
    assert F.violacao_finito(e.value) == f"finito_{coluna}"


@pytest.mark.parametrize("coluna", ["x", "y"])
@pytest.mark.parametrize("valor", ACEITOS)
def test_check_aceita_zero_negativo_limite_null_e_dois_bilhoes(probe, coluna, valor):
    _ensure()
    _q(f"insert into zz_finito_probe ({coluna}) values (%s::{'numeric' if coluna == 'x' else 'float8'})",
       (valor,))


def test_update_tambem_e_barrado(probe):
    _ensure()
    _q("insert into zz_finito_probe (x) values (1)")
    with pytest.raises(psycopg.errors.CheckViolation):
        _q("update zz_finito_probe set x = 'NaN'")
    assert _q("select x from zz_finito_probe", fetch=True)[0]["x"] == 1


def test_violacao_finito_so_reconhece_os_nossos(probe):
    _ensure()
    _q("alter table zz_finito_probe add constraint alheio check (id < 5)")
    with pytest.raises(psycopg.errors.CheckViolation) as e:
        _q("insert into zz_finito_probe (id) values (9)")
    assert F.violacao_finito(e.value) is None and F.violacao_finito(ValueError("x")) is None
    # embrulhada por __cause__ (os handlers reembrulham em RuntimeError)
    with pytest.raises(psycopg.errors.CheckViolation) as e2:
        _q("insert into zz_finito_probe (x) values ('NaN')")
    try:
        raise RuntimeError("falha ao gravar") from e2.value
    except RuntimeError as emb:
        assert F.violacao_finito(emb) == "finito_x"


# ── (3) idempotência ─────────────────────────────────────────────────────────

class _Espia:
    def __init__(self, cur):
        self.cur, self.sqls = cur, []

    def execute(self, q, *a, **k):
        self.sqls.append(repr(q).lower())
        return self.cur.execute(q, *a, **k)

    def __getattr__(self, n):
        return getattr(self.cur, n)


def _oids():
    return _q("select conrelid::regclass::text as t, conname, oid from pg_constraint "
              "where conname like 'finito\\_%%' order by 1, 2", fetch=True)


def test_segunda_chamada_nao_executa_alter_e_nao_muda_oids(probe):
    _ensure()
    antes = _oids()
    conn, cur = _cur_auto()
    try:
        esp = _Espia(cur)
        r = F.ensure_finito_checks(esp)
    finally:
        conn.close()
    assert r["faltando"] == [] and r["criadas"] == [] and r["falhas"] == []
    assert not [s for s in esp.sqls if "alter table" in s], esp.sqls
    assert _oids() == antes
    # controle negativo: com uma coluna faltando, o espião VÊ o alter
    _q("alter table zz_finito_probe drop constraint finito_y")
    conn, cur = _cur_auto()
    try:
        esp = _Espia(cur)
        F.ensure_finito_checks(esp)
    finally:
        conn.close()
    alters = [s for s in esp.sqls if "alter table" in s]
    assert alters and all("not valid" in s for s in alters if "add constraint" in s)  # sem scan sob lock forte


# ── (4) tudo-ou-nada ─────────────────────────────────────────────────────────

def test_coluna_suja_impede_qualquer_constraint_e_o_aviso_nao_traz_o_dado(
        user_id, probe, sem_check_finito, caplog):
    _ensure()
    sem_check_finito("launches", "valor")
    sem_check_finito("bank_movement_declarations", "amount")  # OUTRA tabela, também faltando
    _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1e20, now())",
       (user_id,))
    with caplog.at_level(logging.WARNING, logger="db.finito_checks"):
        r = _ensure()
    assert r["sujas"] == ["launches.valor"]
    assert sorted(r["faltando"]) == ["bank_movement_declarations.amount", "launches.valor"]
    assert r["criadas"] == []
    with get_conn() as conn, conn.cursor() as cur:  # nada foi adicionado, nem na tabela limpa
        assert sorted(_lacunas(cur)) == ["bank_movement_declarations.amount", "launches.valor"]
    log = caplog.text
    assert "launches.valor" in log and "1e20" not in log and "100000000000000000000" not in log
    assert [x.levelname for x in caplog.records if "valor fora do limite" in x.getMessage()] == ["ERROR"]
    # positivo: limpo, o próximo ensure adiciona as duas
    _q("update launches set valor = 1 where user_id = %s", (user_id,))
    r = _ensure()
    assert r["sujas"] == [] and sorted(r["criadas"]) == [
        "bank_movement_declarations.amount", "launches.valor"]
    with get_conn() as conn, conn.cursor() as cur:
        assert _lacunas(cur) == []


def test_sujo_numa_tabela_nao_derruba_o_init_db(user_id, sem_check_finito):
    from db import init_db
    _ensure()
    sem_check_finito("launches", "valor")
    sem_check_finito("bank_movement_declarations", "amount")
    _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1e20, now())",
       (user_id,))
    init_db()  # o piso é em tarefa de fundo: dado sujo nunca trava o boot


def test_linha_suja_que_aparece_entre_o_prescan_e_o_add_desfaz_a_rodada(
        user_id, sem_check_finito, monkeypatch, caplog):
    """Corrida: o pré-scan achou tudo limpo, uma escrita legítima cruza 1e13 antes do ADD (soma
    agregada). O VALIDATE acha a linha: o que esta rodada criou é desfeito (tudo-ou-nada)."""
    _ensure()
    sem_check_finito("launches", "valor")
    sem_check_finito("bank_movement_declarations", "amount")
    original = F.colunas_fora_do_limite

    def prescan_e_depois_escrita(cur, colunas):
        achadas = original(cur, colunas)
        assert achadas == []
        _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1e20, now())",
           (user_id,))
        return achadas

    monkeypatch.setattr(F, "colunas_fora_do_limite", prescan_e_depois_escrita)
    with caplog.at_level(logging.WARNING, logger="db.finito_checks"):
        r = _ensure()
    assert r["sujas"] == ["launches.valor"] and r["criadas"] == [], r
    with get_conn() as conn, conn.cursor() as cur:  # nem a tabela limpa ficou com a constraint
        assert sorted(_lacunas(cur)) == ["bank_movement_declarations.amount", "launches.valor"]
    assert "100000000000000000000" not in caplog.text
    # positivo: limpo, o próximo ensure instala as duas
    monkeypatch.setattr(F, "colunas_fora_do_limite", original)
    _q("update launches set valor = 1 where user_id = %s", (user_id,))
    r = _ensure()
    assert sorted(r["criadas"]) == ["bank_movement_declarations.amount", "launches.valor"]


def test_nome_longo_cabe_em_63_bytes_e_o_ensure_continua_idempotente():
    comum = "a" * 58
    n1, n2 = F.nome_check(comum + "x"), F.nome_check(comum + "y")
    assert len(n1.encode()) <= 63 and len(n2.encode()) <= 63 and n1 != n2
    assert n1 == F.nome_check(comum + "x") and F.nome_check("valor") == "finito_valor"
    _q(f"create table zz_finito_longo (id serial primary key, {comum}x numeric, {comum}y numeric)")
    try:
        _ensure()
        r = _ensure()  # 2ª rodada: nada faltando (o nome truncado pelo Postgres ainda é achado)
        assert r["faltando"] == [] and r["falhas"] == [] and r["criadas"] == [], r
    finally:
        _q("drop table if exists zz_finito_longo")


def test_check_pendente_de_boot_interrompido_entra_no_prescan(user_id, sem_check_finito):
    """ADD NOT VALID commitado e VALIDATE não rodou; linha suja sob ele + outra constraint faltando:
    o pré-scan acha a suja e NADA novo é criado."""
    _ensure()
    sem_check_finito("launches", "valor")  # NOT VALID ao fim do teste; aqui, ausente
    _q("alter table launches add constraint finito_valor check (abs(valor) <= 10000000000000) not valid")
    _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1, now())", (user_id,))
    _q("alter table launches drop constraint finito_valor")  # solta p/ plantar o sujo sem violar
    _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1e20, now())", (user_id,))
    _q("alter table launches add constraint finito_valor check (abs(valor) <= 10000000000000) not valid")
    sem_check_finito("bank_movement_declarations", "amount")
    r = _ensure()
    assert r["sujas"] == ["launches.valor"] and r["criadas"] == [], r
    with get_conn() as conn, conn.cursor() as cur:
        assert F.nome_check("amount") not in {n for (_t, n) in F._constraints(cur) if _t == "bank_movement_declarations"}


def test_quantidade_de_unidades_tem_limite_proprio_nao_o_do_dinheiro():
    """Cripto de preço ínfimo passa de 10**13 UNIDADES; só NaN/Infinity/lixo (> 10**30) é barrado."""
    from decimal import Decimal
    from db.of_snapshots import _numero, _quantidade
    tab, col = "open_finance_investment_snapshots", "quantity"
    assert F.limite_da_coluna(tab, col) == 10**30 and F.limite_da_coluna(tab, "balance") == VALOR_MAX
    assert _quantidade(10**20) == Decimal(10**20) and _numero(10**20) is None  # o dinheiro segue em 1e13
    assert _quantidade(10**31) is None and _quantidade("NaN") is None and _quantidade(float("inf")) is None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select pg_get_constraintdef(oid) as d from pg_constraint where conname = %s "
                    "and conrelid = to_regclass(%s)", (F.nome_check(col), f"public.{tab}"))
        assert str(10**30) in cur.fetchone()["d"]  # a constraint REAL da coluna, não só a função
    _q("create table zz_finito_qtd (q numeric)")
    try:
        _q(f"alter table zz_finito_qtd add constraint c check ({F.expr_finito('q', 10**30).as_string()})")
        _q("insert into zz_finito_qtd values (1e20), (null)")  # aceita unidades enormes
        for ruim in ("'NaN'", "'Infinity'", "1e31"):
            with pytest.raises(psycopg.errors.CheckViolation):
                _q(f"insert into zz_finito_qtd values ({ruim})")
    finally:
        _q("drop table if exists zz_finito_qtd")


class _CurFalho:
    """Cursor cujo `execute` levanta as primeiras `falhas` vezes (como o lock_timeout estourando)."""
    def __init__(self, falhas):
        self.falhas, self.chamadas = falhas, 0

    def execute(self, _q):
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise psycopg.errors.LockNotAvailable("lock_timeout")


def test_desfaz_tenta_3_vezes_e_devolve_o_que_nao_saiu():
    assert F._desfaz(_CurFalho(2), [("launches", "finito_valor")]) == []        # perde 2 locks, o 3º passa
    cur = _CurFalho(99)
    assert F._desfaz(cur, [("launches", "finito_valor")]) == [("launches", "finito_valor")]
    assert cur.chamadas == 3


def test_rollback_que_nao_sai_nao_declara_sucesso_e_o_proximo_boot_desfaz(user_id, sem_check_finito, monkeypatch, caplog):
    """Corrida suja o ADD; o DROP perde o lock: `criadas`/log não mentem e o boot seguinte remove a sobra."""
    _ensure()
    sem_check_finito("launches", "valor")
    sem_check_finito("bank_movement_declarations", "amount")
    original, real_desfaz = F.colunas_fora_do_limite, F._desfaz

    def prescan_e_depois_escrita(cur, colunas):
        achadas = original(cur, colunas)
        _q("insert into launches (user_id, tipo, valor, criado_em) values (%s, 'despesa', 1e20, now())", (user_id,))
        return achadas

    monkeypatch.setattr(F, "colunas_fora_do_limite", prescan_e_depois_escrita)
    monkeypatch.setattr(F, "_desfaz", lambda cur, cs: list(cs))  # nenhum DROP saiu
    with caplog.at_level(logging.WARNING, logger="db.finito_checks"):
        r = _ensure()
    assert r["sujas"] == ["launches.valor"] and sorted(r["criadas"]) == [
        "bank_movement_declarations.amount", "launches.valor"], r
    assert "NÃO desfeitas" in caplog.text
    monkeypatch.setattr(F, "colunas_fora_do_limite", original)
    monkeypatch.setattr(F, "_desfaz", real_desfaz)
    r = _ensure()  # a sobra agora é `pendente` e há linha suja: o pré-scan a remove
    assert r["sujas"] == ["launches.valor"], r
    with get_conn() as conn, conn.cursor() as cur:
        assert not [k for k in F._constraints(cur) if k[0] in ("launches", "bank_movement_declarations")
                    and k[1] in ("finito_valor", "finito_amount") and not F._constraints(cur)[k]]


# ── (5) lock ─────────────────────────────────────────────────────────────────

def test_tabela_travada_vira_falha_sem_travar_as_outras(sem_check_finito):
    _ensure()
    sem_check_finito("launches", "valor")
    sem_check_finito("pockets", "balance")
    solta = threading.Event()
    travou = threading.Event()

    def segura():
        with get_conn() as conn:
            conn.execute("lock table launches in access share mode")
            travou.set()
            solta.wait(10)

    t = threading.Thread(target=segura)
    t.start()
    try:
        assert travou.wait(5)
        t0 = time.monotonic()
        r = _ensure()
        assert time.monotonic() - t0 < 3
        assert r["falhas"] == ["launches"] and r["criadas"] == ["pockets.balance"], r
    finally:
        solta.set()
        t.join()
    r = _ensure()  # positivo: solta a trava, o próximo boot cria
    assert r["falhas"] == [] and r["criadas"] == ["launches.valor"]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select current_setting('lock_timeout') as v")
        assert cur.fetchone()["v"] == "0"  # restaurado no finally (a conexão volta ao pool limpa)
