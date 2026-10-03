"""Barreira da fatura (#759, classe do #754): compra de A apontando para a fatura de B não entra.

Nenhum escritor grava essa linha; é defesa em profundidade. Cada join `credit_bills` por
`bill_id` exige `b.user_id = ct.user_id` (ou `b.user_id = %s`, o usuário da consulta): desde o
#772 `credit_bills.user_id` é NOT NULL, com backfill pelo dono do cartão
(`test_credit_bills_user_id_not_null.py`), e a guarda voltou à forma simples.

Cena (`_cena`): A tem 80 "mercado" na fatura dela, 4321 "mercado" na fatura de B (a linha
corrompida) e 50 "mercado" num 2º cartão; B tem 1. A vê 130, nunca 4321; B vê 1. A compra do
2º cartão é o controle POSITIVO: a guarda não derruba fatura legítima de A. Nos parcelamentos
(`_parcelado`), a parcela 1 de 3x300 vai para a fatura de B: A vê as parcelas 2 e 3 (200),
nunca 300.

Controle NEGATIVO medido no #759 por mutação, um site por vez (edição temporária da linha,
reposta por cópia do arquivo): sem a guarda, os 15 joins deixam vermelho o teste com banco do
ponto e o portão. Ex.: get_largest_expenses → A sai [4321.0, 80.0, 50.0];
sum_spent_in_category_this_month e _spent_by_bucket → 4451.0; _detect_category_spike →
"R$ 4.451,00 contra..."; anticipate → TypeError (fatura None); undo → removed_count 3;
list_installment_groups → (3, 300.0).

As escritas que apagam compra de A (antecipar, desfazer avulsa/parcelado, remover avulsa)
só ajustam fatura de A (`user_id = %s` em db/cards.py). A fatura de B segue recusada: o total
dela fica em 1 (sem o filtro de usuário, `greatest` daria 0). Antecipar e desfazer parcelado
nem leem a parcela na fatura de B (a guarda de leitura); desfazer/remover avulsa acham a
compra só por `user_id`, e ali a escrita é a única barreira.
"""
from __future__ import annotations

import ast
import re
import subprocess
from datetime import date, timedelta

import db
from conftest import usuario_pagante
from db import insights
from db.household_budget import _spent_by_bucket
from tests._patrimonio_helpers import q
from tests.test_fonte_unica_q36 import _proprios
from tests.test_resumo_mes_regra import FIM, INICIO, semeia_a_em_fatura_de_b
from tests.test_tipo_legado_na_tendencia import _grava, _mes_passado
from utils_text import fmt_brl

ATE = FIM - timedelta(days=1)


def _cena(mes_corrente: bool = False) -> tuple[int, int]:
    """`mes_corrente`: as faturas de A e B passam a fechar hoje — budgets e insights leem
    `date.today()`. (Cada cartão tem uma fatura só; o unique de período não morde.)"""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    q("update credit_transactions set categoria = 'mercado' where user_id = %s and valor = 4321", (a,))
    outro = db.create_card(a, "Inter", closing_day=10, due_day=17)
    db.add_credit_purchase(a, outro, 50, "mercado", "2º cartão", INICIO.replace(day=5))
    if mes_corrente:
        q("""update credit_bills set period_end = %s
              where card_id in (select id from credit_cards where user_id in (%s, %s))""",
          (date.today(), a, b))
    return a, b


def test_maiores_gastos_categorias_e_lista_contam_o_2o_cartao_e_nunca_a_de_b():
    a, b = _cena()
    for uid, valores in ((a, [80.0, 50.0]), (b, [1.0])):
        maiores = db.get_largest_expenses(uid, INICIO, ATE, by_bill_month=True)
        assert [m["valor"] for m in maiores if m["fonte"] == "credito"] == valores, uid
        cats = db.get_top_expense_categories(uid, INICIO, ATE, by_bill_month=True)
        assert [c["total"] for c in cats] == [sum(valores)], uid
    for uid, cat, valores in ((a, "mercado", [50.0, 80.0]), (b, "x", [1.0])):
        itens, _ = db.list_launches_by_category(uid, cat, INICIO, ATE)
        assert sorted(i["valor"] for i in itens if i["fonte"] == "credito") == valores, uid


def test_orcamento_conta_o_2o_cartao_e_nunca_a_de_b():
    a, b = _cena(mes_corrente=True)
    hoje = date.today()
    for uid, cat, total in ((a, "mercado", 130.0), (b, "x", 1.0)):
        db.upsert_budget(uid, cat, 1000)
        assert db.sum_spent_in_category_this_month(uid, cat) == total, uid
        assert db.sum_spent_in_category_period(uid, cat, hoje.replace(day=1), hoje) == total, uid
        assert [x["spent"] for x in db.get_budgets_status_for_month(uid)["budgets"]] == [total], uid


def test_potes_do_orcamento_domestico_contam_o_2o_cartao_e_nunca_a_de_b():
    a, b = _cena()
    for uid, total in ((a, 130.0), (b, 1.0)):
        assert sum(_spent_by_bucket(uid, INICIO.year, INICIO.month).values()) == total, uid


def test_alertas_de_categoria_e_de_mes_queimando_contam_o_2o_cartao_e_nunca_a_de_b(monkeypatch):
    """Detectores chamados direto (`compute_active_insights` engole exceção) e progresso do
    mês fixo em 50%. Spike: 130 contra média 80 (sem o 2º cartão dá 80 e o alerta some). Burn:
    530 + 130 = 66% da receita (sem o 2º cartão, 61% e lista vazia). Sem caso
    do B: ele não tem histórico para alerta nenhum, e [] ali seria verde por construção."""
    a, _ = _cena(mes_corrente=True)
    for meses in (1, 2):
        _grava(a, "despesa", 80, dia=_mes_passado(5, meses))
        _grava(a, "receita", 1000, categoria="salario", dia=_mes_passado(5, meses))
    _grava(a, "despesa", 530, categoria="aluguel")
    monkeypatch.setattr(insights, "_month_progress_pct", lambda *_a, **_k: 50.0)
    assert [s["message"] for s in insights._detect_category_spike(a)] == [
        f"{fmt_brl(130.0)} contra média de {fmt_brl(80.0)} nos 3 meses anteriores. Vale conferir."]
    assert [x["message"] for x in insights._detect_salary_burn_fast(a)] == [
        "Já consumiu 66% da sua receita média mensal e só 50% do mês passou. Vale dar uma freada."]


# ── parcelamentos (db/cards.py, alias `t`), inclusive as duas escritas ──────

def _parcelado():
    """3x300 de A: parcela 1 na fatura aberta de B (período mais antigo que o da parcela 2),
    as parcelas 2 e 3 nas faturas de A. Todas abertas."""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    cartao_a = q("select id from credit_cards where user_id = %s", (a,))["id"]
    fatura_b = q("select id from credit_bills where user_id = %s", (b,))["id"]
    r, _ = db.add_credit_purchase_installments(a, cartao_a, 300, "casa", "3x", INICIO.replace(day=5), 3)
    p1, p2, _ = r["tx_ids"]
    q("update credit_transactions set bill_id = %s where id = %s", (fatura_b, p1))
    return a, r["group_id"], fatura_b, p1, p2


def _total(fatura: int) -> float:
    return float(q("select total from credit_bills where id = %s", (fatura,))["total"])


def _fatura_de(tx: int) -> int:
    return q("select bill_id from credit_transactions where id = %s", (tx,))["bill_id"]


def test_listas_e_impacto_do_parcelamento_contam_as_de_a_e_nunca_a_de_b():
    a, gid, *_ = _parcelado()
    grupos = [(g["n_registered"], float(g["total"])) for g in db.list_installment_groups(a)
              if str(g["group_id"]) == gid]
    assert grupos == [(2, 200.0)]
    det = [g for g in db.list_installment_groups_detailed(a) if g["group_id"] == gid]
    assert [p["installment_no"] for p in det[0]["parcelas"]] == [2, 3]
    assert db.get_installment_group_summaries(a, [gid])[gid]["valor_restante"] == 200.0
    imp = db.get_installment_group_delete_impact(a, gid)
    assert (imp["full_total"], imp["future_count"]) == (200.0, 2)


def test_antecipar_pega_a_parcela_dela_e_nao_toca_a_de_b():
    """Sem a barreira a parcela 1 (fatura de B, a mais antiga) é a escolhida e a busca da
    fatura com `user_id = A` volta None: TypeError."""
    a, gid, fatura_b, p1, p2 = _parcelado()
    fatura_p2 = _fatura_de(p2)
    antes = _total(fatura_p2)
    assert db.anticipate_installment(a, gid)["anticipated_installment_no"] == 2
    assert _total(fatura_p2) == antes - 100
    assert q("select bill_id from credit_transactions where id = %s", (p1,))["bill_id"] == fatura_b


def _engorda(fatura: int, extra: int, pago: int) -> None:
    """Muda o ramo da escrita: com sobra (`extra`) e/ou pagamento (`pago`) na fatura."""
    q("update credit_bills set total = total + %s, paid_amount = %s where id = %s", (extra, pago, fatura))


def test_antecipar_derruba_o_total_da_fatura_dela():
    for pago in (0, 50):  # 50: ramo que fecha a fatura como paga
        a, gid, fatura_b, p1, p2 = _parcelado()
        dela, antes_b = _fatura_de(p2), _total(fatura_b)
        _engorda(dela, 50, pago)
        antes = _total(dela)
        assert db.anticipate_installment(a, gid)["anticipated_installment_no"] == 2
        assert _total(dela) == antes - 100, pago
        assert (_fatura_de(p1), _total(fatura_b)) == (fatura_b, antes_b)


def test_desfazer_apaga_as_dela_e_deixa_a_da_fatura_de_b_intocada():
    for extra, pago in ((0, 0), (50, 0), (50, 50)):  # um ramo da escrita da fatura cada
        a, gid, fatura_b, p1, p2 = _parcelado()
        dela = _fatura_de(p2)  # só a parcela 2 nela, como a 3 na dela
        _engorda(dela, extra, pago)
        antes, antes_dela = _total(fatura_b), _total(dela)
        assert db.undo_installment_group(a, gid)["removed_count"] == 2
        assert q("select group_id::text as g from credit_transactions where id = %s", (p1,))["g"] == gid
        assert (_total(fatura_b), _total(dela)) == (antes, antes_dela - 100), (extra, pago)
        assert q("select status from credit_bills where id = %s", (dela,))["status"] == ("paid" if pago else "open")


def test_apagar_compra_avulsa_derruba_a_do_2o_cartao_e_nunca_a_de_b():
    for apagar in (db.undo_credit_transaction, db.remove_single_credit_transaction):
        a, b = _cena()
        tx = {int(r["valor"]): r for r in q(
            "select id, bill_id, valor from credit_transactions where user_id = %s", (a,), fetch=True)}
        _engorda(tx[50]["bill_id"], 0, 50)  # paga: o desfazer a fecha; o remover não mexe no status
        antes = _total(tx[50]["bill_id"])
        apagar(a, tx[50]["id"])
        assert _total(tx[50]["bill_id"]) == antes - 50, apagar
        fecha = apagar is db.undo_credit_transaction
        assert q("select status from credit_bills where id = %s", (tx[50]["bill_id"],))["status"] == (
            "paid" if fecha else "open")
        apagar(a, tx[4321]["id"])
        assert _total(tx[4321]["bill_id"]) == 1.0, apagar


def test_fatura_de_b_nao_muda_com_as_escritas_de_a():
    """A fatura de B tem compra de A pendurada. Nenhuma escrita de A muda o registro dela.
    Ramos: (999, 950) fecha paga; (999, 0) só baixa; (50, 30) zera.
    Parcelado: a leitura nem acha a parcela 1 (antecipa a 2, desfaz 2); avulsa: só a escrita barra."""
    for total, pago in ((999, 950), (999, 0), (50, 30)):
        for acao in (db.undo_credit_transaction, db.remove_single_credit_transaction,
                     db.undo_installment_group, db.anticipate_installment):
            if acao in (db.undo_installment_group, db.anticipate_installment):
                a, alvo, fatura_b, *_ = _parcelado()  # parcela 1 de A na fatura de B
            else:
                a, b = usuario_pagante(), usuario_pagante()
                semeia_a_em_fatura_de_b(a, b)
                fatura_b = q("select id from credit_bills where user_id = %s", (b,))["id"]
                alvo = q("select id from credit_transactions where user_id = %s and bill_id = %s",
                         (a, fatura_b))["id"]
            q("update credit_bills set total = %s, paid_amount = %s where id = %s",
              (total, pago, fatura_b))
            antes = q("select * from credit_bills where id = %s", (fatura_b,))
            acao(a, alvo)
            assert q("select * from credit_bills where id = %s", (fatura_b,)) == antes, (acao, total, pago)


# ── portão de varredura: todo join de fatura por bill_id tem a barreira ─────

_JOIN = re.compile(r"join\s+credit_bills\s+(?:as\s+)?(\w+)\s+on\s+\1\.id\s*=\s*\w+\.bill_id", re.I)
_GUARDA = re.compile(r"\b(\w+)\.user_id\s*=\s*(?:%s|(\w+)\.user_id\b)", re.I)


def _sem_guarda(texto: str) -> bool:
    """Guarda = `X.user_id = %s` ou `X.user_id = Y.user_id` (Y != X), com X alias de um join
    da unidade. Reprova `coalesce(X.user_id` e `X.user_id is null`: a fatura sem dono voltou."""
    juncoes = _JOIN.findall(texto)
    aliases = {x.lower() for x in juncoes}
    if not aliases:
        return False
    nomes = "|".join(map(re.escape, aliases))
    if re.search(rf"coalesce\(\s*(?:{nomes})\.user_id\b|\b(?:{nomes})\.user_id\s+is\s+null", texto, re.I):
        return True
    guardas = [m for m in _GUARDA.finditer(texto)
               if m[1].lower() in aliases and (m[2] or "").lower() != m[1].lower()]
    return len(juncoes) > len(guardas)


def _texto(no) -> str:
    """Strings PRÓPRIAS da unidade (partes literais de f-string entram; função aninhada é
    unidade à parte), menos a docstring: docstring que cita a guarda não é guarda."""
    doc = no.body[0].value if no.body and isinstance(no.body[0], ast.Expr) else None
    return "\n".join(n.value for n in _proprios(no)
                     if n is not doc and isinstance(n, ast.Constant) and isinstance(n.value, str))


def _unidades():
    """(arquivo, função | "<módulo>") → `_texto` da unidade."""
    arquivos = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True,
                              check=True).stdout.split()
    for path in arquivos:
        if path.startswith(("tests/", "harness_tests/")):
            continue
        for no in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
                yield (path, getattr(no, "name", "<módulo>")), _texto(no)


def test_todo_join_de_fatura_por_bill_id_tem_a_barreira():
    """Em cada unidade, nº de guardas >= nº de joins (`_sem_guarda`). Prende a classe TEXTUAL; quem prova
    comportamento são os testes com banco acima. O portão NÃO enxerga: join sem alias ou
    com `using`, join por vírgula, subconsulta correlacionada, SQL montado em funções
    diferentes, guarda presente mas errada (parâmetro trocado, `or true` depois), e guarda
    sobrando mascarando um join sem guarda na mesma unidade — guarda vinda de outra string
    da unidade (outro SQL, string solta que não é a docstring, docstring de classe dentro
    do módulo); a docstring da função/módulo não conta (`_texto`)."""
    com_join, faltando = set(), []
    for unidade, texto in _unidades():
        if _JOIN.search(texto):
            com_join.add(unidade)
            if _sem_guarda(texto):
                faltando.append(unidade)
    assert {("db/insights.py", "_detect_category_spike"), ("db/resumo_mes.py", "<módulo>")} <= com_join
    assert not faltando, f"join credit_bills por bill_id sem b.user_id = (%s | ct.user_id): {faltando}"


def test_o_contador_do_portao_distingue_com_e_sem_guarda():
    j = "join credit_bills b on b.id = ct.bill_id"
    g = "b.user_id = ct.user_id"
    assert _sem_guarda(j)
    assert not _sem_guarda(j + " and " + g)
    assert not _sem_guarda(j + " WHERE ct.user_id = %s AND B.USER_ID = %s")
    assert _sem_guarda(j + " where ct.user_id = %s")  # só o dono da compra não é guarda da fatura
    assert _sem_guarda(j + " and coalesce(b.user_id, (select cb.user_id from credit_cards cb"
                           " where cb.id = b.card_id)) = ct.user_id")
    assert _sem_guarda(j + " and (b.user_id = ct.user_id or b.user_id is null)")
    assert _sem_guarda(j + " and x.user_id = ct.user_id")  # alias que não é o da fatura
    assert _sem_guarda(j + f" and {g} join credit_bills f on f.id = x.bill_id")
    assert not _sem_guarda("join credit_bills b on b.card_id = c.id and b.user_id = c.user_id")

    def fn(doc, sql):
        return _texto(ast.parse(f'def f():\n    """{doc}"""\n    return "{sql}"').body[0])
    assert _sem_guarda(fn(f"o join tem a barreira {g}", j))
    assert not _sem_guarda(fn("qualquer", f"{j} and {g}"))
