"""#1507: `scripts/remover_colunas_refund_alert.py`, o drop das colunas órfãs do #1418.

O `init_db` não cria as colunas: a fixture `colunas` as recria com o DDL literal do #1418 (25eb917f) e as
derruba no fim, para o banco do worker voltar ao estado do `init_db`. Comissões pela rota real
(`_paga`, `_parcial`); o `status` e o alerta entram por SQL, que é o estado que o #1418 deixava.

A última linha, `Resumo para a thread:`, vai para a thread pública do #1508 (P4): `_resumo` exige o formato
inteiro (só contagens, nenhum id, valor ou data) e confere cada contagem com as linhas impressas acima dela.

CONTROLES (CLAUDE.md §3): sem a guarda, `test_recusa_alerta_sem_pendencia` fica vermelho (com a Q2
filtrando por `refund_alert_sent_at`, o caso `canal_ausente`); sem o `status <> 'reversed'` dela,
`test_estornada_sem_pendencia_nao_prende`; sem a checagem do `pg_proc` (ou só com `--aplicar`, só no
`public`, sem o `lower`), `test_recusa_funcao_que_cita_a_coluna`; cada filtro de Q1, Q3 e Q4 trocado ou
tirado, `test_sem_colunas_imprime_q1_q3_q4_e_nada_a_remover`; sem o `to_regclass`, `test_sem_system_event_logs_segue`;
drop sem olhar o `--aplicar`, `test_simulacao_nao_remove`; Q2 sem `order by c.id` ou sem o `notified_at`,
`test_q2_em_ordem_com_o_aviso_da_pendencia`; sem o `lock_timeout`, `test_tabela_ocupada_desiste`; com
abreviação, `test_abreviacao_nao_vale`. Positivo: `test_aplicar_remove_e_imprime_a_evidencia` fica vermelho
com uma guarda que recusa tudo.
"""
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from db.affiliate_payments import marcar_reembolso, registrar_pagamento
from db.affiliates import request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado
from test_afiliado_pendencia import _parcial
from test_afiliado_reembolso import _paga, mundo  # noqa: F401
from test_afiliado_reembolso import _logs as _tabela_de_logs  # noqa: F401  (autouse: cria system_event_logs)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from remover_colunas_refund_alert import main  # noqa: E402

COLUNAS = ["refund_alert", "refund_alert_sent_at"]
RESUMO = re.compile(r"Resumo para a thread: Q1: (\d+); Q2: (colunas ausentes|(?:1 registro|(?!1 )\d+ registros)"
                    r"(?:, todos com pendência correspondente"
                    r"|: \d+ com pendência correspondente, \d+ sem pendência, \d+ estornados sem pendência)?);"
                    r" Q3: (\d+|tabela ausente); Q4: (\d+)\. Modo: (simulação|aplicação)\. Resultado: ([^\d]+)\.")


def _existentes():
    with get_conn() as conn:
        return sorted(r["attname"] for r in conn.execute(
            "select attname from pg_attribute where attrelid = 'affiliate_commissions'::regclass"
            " and attname like 'refund_alert%%' and not attisdropped").fetchall())


@pytest.fixture
def colunas():
    with get_conn() as conn:
        conn.execute("""alter table affiliate_commissions add column if not exists refund_alert text
             check (refund_alert in ('partial', 'total'))""")
        conn.execute("""alter table affiliate_commissions add column if not exists refund_alert_sent_at timestamptz""")
    try:
        yield
    finally:
        with get_conn() as conn:
            conn.execute("alter table affiliate_commissions drop column if exists refund_alert,"
                         " drop column if exists refund_alert_sent_at")


def _alerta(com, status="pending", avisado=True):
    """`avisado=False` é o canal ausente do #1418: alerta gravado, `refund_alert_sent_at` nulo."""
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set status = %s, refund_alert = 'total', refund_alert_sent_at ="
                     " case when %s then timestamptz '2026-11-10 23:00+00' end where id = %s",
                     (status, avisado, com.id))


def _rodar(capsys, *args):
    codigo = main(list(args))
    return codigo, capsys.readouterr().out


def _criada(com, quando, status="pending"):
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set created_at = %s, status = %s where id = %s",
                     (quando, status, com.id))


def _log(comissao, quando, tipo="affiliate_refund_commission_kept"):
    with get_conn() as conn:
        conn.execute("insert into system_event_logs (level, event_type, message, details, created_at)"
                     " values ('error', %s, 'x', %s, %s)",
                     (tipo, f'{{"commission_id": {comissao}, "tipo": "total"}}', quando))


def _bloco(saida, q):
    return saida.split(f"{q}: ")[1].split("\nQ")[0]


def _resumo(saida):
    """A última linha no formato do dono, e cada contagem igual às linhas impressas antes dela."""
    *detalhe, ultima = saida.rstrip("\n").split("\n")
    m = RESUMO.fullmatch(ultima)
    assert m, ultima
    detalhe = "\n".join(detalhe) + "\n"
    for q, n in (("Q1", m[1]), ("Q3", m[3]), ("Q4", m[4])):
        bloco = _bloco(detalhe, q)
        assert bloco.startswith("tabela system_event_logs ausente") if n == "tabela ausente" else (
            bloco.startswith(f"{n} linhas") and bloco.count("\n  ") == int(n)), (q, n)
    if m[2] != "colunas ausentes":
        assert sum(x.startswith("comissão ") for x in detalhe.splitlines()) == int(m[2].split()[0])
    return m.groups()


def test_sem_colunas_imprime_q1_q3_q4_e_nada_a_remover(mundo, capsys):  # noqa: F811
    """Para cada consulta, o que TEM de aparecer e o que NÃO pode, e nunca a fatura."""
    total, estornada, com_pendencia, sem_rotulo = (_paga(_afiliado()) for _ in range(4))
    dono_da_parcial = _afiliado()
    parcial = _paga(dono_da_parcial)
    saque = request_payout(dono_da_parcial.aff["id"])["id"]  # a parcial em saque: `status_saque` é o do saque dela
    assert marcar_reembolso("stripe", total.fatura, "total", None)
    assert marcar_reembolso("stripe", parcial.fatura, "partial", None)
    assert marcar_reembolso("stripe", estornada.fatura, "total", None)
    _criada(estornada, "now()", "reversed")
    _parcial(com_pendencia)  # rótulo e pendência, pela rota
    so_no_pix = _paga(_afiliado())  # o pagamento Stripe sem rótulo; o rótulo está num `pix` com a mesma referência
    assert registrar_pagamento(so_no_pix.indicado.uid, "pix", so_no_pix.fatura, 100_000, datetime.now(timezone.utc))
    assert marcar_reembolso("pix", so_no_pix.fatura, "total", None)
    na_janela, estornada_na_janela, pendente_na_janela, antes_da_janela, depois_da_janela = (
        _paga(_afiliado()) for _ in range(5))
    _criada(na_janela, "2026-11-10 23:00+00")
    _criada(estornada_na_janela, "2026-11-10 23:00+00", "reversed")
    _criada(pendente_na_janela, "2026-11-10 23:00+00")
    _parcial(pendente_na_janela)
    _criada(antes_da_janela, "2026-11-10 21:00+00")
    _criada(depois_da_janela, "2026-11-11 05:00+00")
    base = uuid.uuid4().int % 10**9 + 10**9  # ids de comissão só do log: a Q3 não exige a comissão
    logs = {"antes_da_meia_noite": ("2026-11-10 23:30+00", True), "depois_da_meia_noite": ("2026-11-11 01:00+00", True),
            "antes_da_janela": ("2026-11-10 21:00+00", False), "depois_da_janela": ("2026-11-11 04:00+00", False)}
    for n, (quando, _) in enumerate(logs.values()):
        _log(base + n, quando)
    _log(base + 9, "2026-11-10 23:30+00", tipo="affiliate_refund_outro")
    _log(com_pendencia.id, "2026-11-10 23:30+00")  # a pendência existe: `tem_pendencia` verdadeiro
    _log(total.id, "2026-11-10 23:30+00")  # a comissão existe, a pendência não
    with get_conn() as conn:  # o log do #1508 traz `alerta_entregue`: `ja_e_o_1508` verdadeiro
        conn.execute("insert into system_event_logs (level, event_type, message, details, created_at) values"
                     " ('error', 'affiliate_refund_commission_kept', 'x', %s, '2026-11-11 02:00+00')",
                     (f'{{"commission_id": {base + 8}, "tipo": "total", "alerta_entregue": false}}',))

    codigo, saida = _rodar(capsys, "--aplicar")
    assert codigo == 0
    assert _resumo(saida)[1::4] == ("colunas ausentes", "nada a remover")
    q1, q3, q4 = (_bloco(saida, q) for q in ("Q1", "Q3", "Q4"))
    assert q1.count(f"comissao {total.id} · status pending · saque None · status_saque None ·") == 1
    assert q1.count(f"comissao {parcial.id} · status pending · saque {saque} · status_saque requested ·") == 1
    assert "rotulo total" in q1 and "rotulo partial" in q1
    for fora in (estornada, com_pendencia, sem_rotulo, so_no_pix):
        assert f"comissao {fora.id} ·" not in q1
    assert f"comissao {com_pendencia.id} · tipo total · ja_e_o_1508 False · tem_pendencia True" in q3
    assert f"comissao {total.id} · tipo total · ja_e_o_1508 False · tem_pendencia False" in q3
    assert f"comissao {base + 8} · tipo total · ja_e_o_1508 True · tem_pendencia False" in q3
    for n, (_, dentro) in enumerate(logs.values()):
        assert (f"comissao {base + n} · tipo total · ja_e_o_1508 False · tem_pendencia False" in q3) is dentro, n
    assert f"comissao {base + 9} ·" not in q3
    assert f"comissao {na_janela.id} · status pending ·" in q4
    for fora in (estornada_na_janela, pendente_na_janela, antes_da_janela, depois_da_janela):
        assert f"comissao {fora.id} ·" not in q4
    assert all(c.fatura not in saida for c in (total, parcial, na_janela))
    assert _existentes() == []


def test_sem_system_event_logs_segue(capsys):
    """A tabela nasce no startup do app, não no `init_db`: sem ela a Q3 avisa e o script segue."""
    with get_conn() as conn:
        conn.execute("alter table system_event_logs rename to sel_1507")
    try:
        codigo, saida = _rodar(capsys)
    finally:
        with get_conn() as conn:
            conn.execute("alter table sel_1507 rename to system_event_logs")
    assert codigo == 0 and "\nQ3: tabela system_event_logs ausente\nQ4: " in saida
    _, q2, q3, _, modo, resultado = _resumo(saida)
    assert (q2, q3, modo, resultado) == ("colunas ausentes", "tabela ausente", "simulação", "nada a remover")


@pytest.mark.parametrize("status, avisado", [("paid", True), ("pending", True), ("pending", False)],
                         ids=["paid", "pending", "canal_ausente"])
def test_recusa_alerta_sem_pendencia(colunas, capsys, status, avisado):
    a = _paga(_afiliado())
    _alerta(a, status, avisado)
    codigo, saida = _rodar(capsys, "--aplicar")
    assert codigo == 3 and f"sem pendência: comissões {a.id}." in saida
    assert _resumo(saida)[1::3] == ("1 registro: 0 com pendência correspondente, 1 sem pendência, 0 estornados"
                                    " sem pendência", "aplicação")
    assert _resumo(saida)[5] == "recusa, alerta sem pendência, nada removido"
    assert _existentes() == COLUNAS


def test_estornada_sem_pendencia_nao_prende(colunas, capsys):
    a = _paga(_afiliado())
    _alerta(a, "reversed")
    codigo, saida = _rodar(capsys, "--aplicar")
    assert (codigo, _existentes()) == (0, []), saida
    assert _resumo(saida)[1::4] == ("1 registro: 0 com pendência correspondente, 0 sem pendência, 1 estornados"
                                    " sem pendência", "colunas removidas")


def test_simulacao_nao_remove(colunas, mundo, capsys):  # noqa: F811
    a = _paga(_afiliado())
    _parcial(a)
    _alerta(a)
    codigo, saida = _rodar(capsys)
    assert codigo == 0 and "\nsimulação: nada removido\nResumo para a thread: " in saida
    assert _resumo(saida)[1::3] == ("1 registro, todos com pendência correspondente", "simulação")
    assert _resumo(saida)[5] == "nada removido"
    assert f"comissão {a.id} · pending · saque None · alerta total avisado 2026-11-10" in saida
    assert "pendência open avisada" in saida
    assert _existentes() == COLUNAS


def test_q2_em_ordem_com_o_aviso_da_pendencia(colunas, mundo, capsys):  # noqa: F811
    """Duas linhas, a de id maior alertada antes (vem primeiro no heap): a ordem é a do `c.id`. Só a 1ª
    pendência foi avisada, e `avisada` traz o `notified_at` dela, não o `refund_alert_sent_at`."""
    a, b = _paga(_afiliado()), _paga(_afiliado())
    _parcial(a)
    _parcial(b)
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set notified_at = null where stripe_invoice_id = %s", (b.fatura,))
        avisada = conn.execute("select notified_at from affiliate_refund_reviews where stripe_invoice_id = %s",
                               (a.fatura,)).fetchone()["notified_at"]
    _alerta(b)
    _alerta(a)
    codigo, saida = _rodar(capsys)
    assert codigo == 0 and avisada is not None
    linha_a, linha_b = (next(x for x in saida.splitlines() if x.startswith(f"comissão {c.id} ·")) for c in (a, b))
    assert saida.index(linha_a) < saida.index(linha_b)
    assert linha_a.endswith(f"pendência open avisada {avisada}") and linha_b.endswith("pendência open avisada None")
    assert _resumo(saida)[1] == "2 registros, todos com pendência correspondente"


@pytest.mark.parametrize("schema, campo, args", [("public", "new.refund_alert", ["--aplicar"]),
                                                ("public", "new.refund_alert", []),
                                                ("public", "NEW.REFUND_ALERT", ["--aplicar"]),
                                                ("outro", "new.refund_alert", ["--aplicar"])],
                         ids=["aplicar", "simulacao", "maiuscula", "outro_schema"])
def test_recusa_funcao_que_cita_a_coluna(colunas, capsys, schema, campo, args):
    """Trigger plpgsql não é objeto dependente: o `alter` passaria e todo `update` quebraria depois."""
    nome = f"t1507_{uuid.uuid4().hex[:8]}"
    qual = nome if schema == "public" else f"s{nome}.{nome}"
    try:
        with get_conn() as conn:
            if schema != "public":
                conn.execute(f"create schema s{nome}")
            conn.execute(f"create function {qual}() returns trigger language plpgsql as"
                         f" $$ begin if {campo} is not null then null; end if; return new; end $$")
            conn.execute(f"create trigger {nome} before update on affiliate_commissions"
                         f" for each row execute function {qual}()")
        codigo, saida = _rodar(capsys, *args)
        assert codigo == 3 and f"funções que citam refund_alert: {qual}()" in saida
        assert _resumo(saida)[1::3] == ("0 registros", "aplicação" if args else "simulação")
        assert _resumo(saida)[5] == "recusa, função do banco cita refund_alert, nada removido"
        assert _existentes() == COLUNAS
    finally:
        with get_conn() as conn:
            conn.execute(f"drop trigger if exists {nome} on affiliate_commissions")
            conn.execute(f"drop function if exists {qual}()")
            conn.execute(f"drop schema if exists s{nome}")


def test_aplicar_remove_e_imprime_a_evidencia(colunas, mundo, capsys):  # noqa: F811
    a = _paga(_afiliado())
    _parcial(a)
    _alerta(a)
    codigo, saida = _rodar(capsys, "--aplicar")
    assert codigo == 0
    assert saida.index(f"comissão {a.id} ·") < saida.index("removidas: refund_alert, refund_alert_sent_at")
    assert _resumo(saida)[1::4] == ("1 registro, todos com pendência correspondente", "colunas removidas")
    assert _existentes() == []
    codigo, saida = _rodar(capsys, "--aplicar")
    assert codigo == 0 and _resumo(saida)[5] == "nada a remover"


def test_tabela_ocupada_desiste(colunas, capsys):
    resultado = []
    with get_conn() as leitor:
        leitor.execute("select 1 from affiliate_commissions limit 1")  # transação aberta: trava `access share`
        t0 = time.monotonic()
        t = threading.Thread(target=lambda: resultado.append(main(["--aplicar"])), daemon=True)
        t.start()
        t.join(5)
        demora = time.monotonic() - t0
        leitor.rollback()
    assert resultado == [2] and demora < 4, (resultado, demora)
    saida = capsys.readouterr().out
    assert "tabela ocupada: nada removido" in saida and _resumo(saida)[5] == "tabela ocupada, nada removido"
    assert _existentes() == COLUNAS


def test_abreviacao_nao_vale(colunas, capsys):
    """`--aplica` não é `--aplicar`: o argparse aceitaria o prefixo e apagaria as colunas."""
    with pytest.raises(SystemExit) as erro:
        main(["--aplica"])
    assert erro.value.code == 2 and _existentes() == COLUNAS
