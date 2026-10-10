"""`scripts/corrigir_sinal_cartao_of.py` — conexão PAUSED, onde o sync não chega.

Estado semeado como a produção tem hoje (regra velha): compra gravada como estorno, estorno
como compra e o pagamento `Transfer - Internal` como compra. A foto é de `credit_transactions`
e `credit_bills` inteiras (não só a contagem): é ela que um dry-run que escrevesse alteraria.

CONTROLES NEGATIVOS: o dry-run chamando `_sync_imported_credit_updates` muda a foto → vermelho;
o dry-run contando só o sinal (sem o predicado do sync) → `test_dry_run_conta_o_que_o_apply_muda` vermelho.
"""
from __future__ import annotations

import pytest

import db
from scripts import corrigir_sinal_cartao_of as script
from tests._of_cash_helpers import q
from tests.test_of_cartao_sinal import A, B, D, faturas, linhas, regra_velha, rodar  # noqa: F401
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)


def foto(*uids):
    return [q(f"select * from {t} where user_id=%s order by id", (u,), True)
            for u in uids for t in ("credit_transactions", "credit_bills")]


@pytest.fixture
def dois_usuarios(uid_pro, rodar, regra_velha):
    u, v = uid_pro, uid_pro + 1
    db.ensure_user(v)
    with regra_velha():
        rodar(u, [A, B, D])
        rodar(v, [A, D])
    q("update open_finance_connections set status='PAUSED' where user_id=%s", (u,))
    return u, v


def test_dry_run_conta_e_nao_escreve(dois_usuarios):
    u, v = dois_usuarios
    antes = foto(u, v)
    (rel,) = script.main(["--user", str(u)])
    assert (rel["a_corrigir"], rel["a_remover"], rel["faturas"]) == (2, 1, 1)
    assert script.main([]) and foto(u, v) == antes      # sem --user: todos, e continua sem escrever


def test_apply_corrige_so_o_usuario_pedido_e_repete_zero(dois_usuarios):
    u, v = dois_usuarios
    antes_v = foto(v)
    script.main(["--user", str(u), "--apply"])
    assert set(linhas(u)) == {"a", "b"} and faturas(u) == [(90, "open")]
    assert (linhas(u)["a"]["valor"], linhas(u)["b"]["valor"]) == (120, -30)
    assert foto(v) == antes_v                            # V intacto
    (de_novo,) = script.main(["--user", str(u), "--apply"])
    assert de_novo["alteradas"] == 0
    (rel,) = script.main(["--user", str(u)])
    assert (rel["a_corrigir"], rel["a_remover"]) == (0, 0)


def test_controle_negativo_dry_run_que_escreve_muda_a_foto(dois_usuarios, monkeypatch):
    """O teste compara a foto: um dry-run que chamasse a função de escrita seria pego."""
    u, v = dois_usuarios
    antes = foto(u, v)
    original = script.conta_usuario

    def conta_escrevendo(user_id):
        script._sync_imported_credit_updates(user_id, None)
        return original(user_id)

    monkeypatch.setattr(script, "conta_usuario", conta_escrevendo)
    script.main(["--user", str(u)])
    assert foto(u, v) != antes


@pytest.mark.parametrize("campo,sql", [
    ("categoria", "update credit_transactions set categoria='velha', categoria_editada=false where id=%s"),
    ("data", "update credit_transactions set purchased_at = purchased_at - 60 where id=%s"),
    ("data_e_fatura", "with nb as (insert into credit_bills (user_id, card_id, period_start, period_end, total, status) "
                      "select user_id, card_id, period_start - 60, period_end - 60, 0, 'open' from credit_bills "
                      "where id = (select bill_id from credit_transactions where id=%s) returning id) "
                      "update credit_transactions set purchased_at = purchased_at - 60, "
                      "bill_id = (select id from nb) where id=%s"),
])
def test_dry_run_conta_o_que_o_apply_muda(uid_pro, rodar, campo, sql):
    """Conexão PAUSED com SINAL CERTO e categoria/data velha: o apply (o próprio sync) reescreve
    a linha, então o dry-run tem de contá-la. dry-run -> apply -> dry-run = 0."""
    rodar(uid_pro, [A])                                   # regra nova: sinal certo
    q("update open_finance_connections set status='PAUSED' where user_id=%s", (uid_pro,))
    ct = linhas(uid_pro)["a"]["id"]
    q(sql, (ct,) * sql.count("%s"))
    antes = foto(uid_pro)
    bill_antes = q("select bill_id from credit_transactions where id=%s", (ct,), True)[0]["bill_id"]

    (rel,) = script.main(["--user", str(uid_pro)])
    assert (rel["a_corrigir"], rel["a_remover"], rel["faturas"]) == (1, 0, 1)
    assert rel["fatura_destino_desconhecida"] == (0 if campo == "categoria" else 1)
    assert foto(uid_pro) == antes                         # dry-run não escreve (nem cria fatura)

    (ap,) = script.main(["--user", str(uid_pro), "--apply"])
    assert ap["alteradas"] == rel["a_corrigir"] + rel["a_remover"]   # a foto bate com a previsão
    depois = q("select categoria, bill_id from credit_transactions where id=%s", (ct,), True)[0]
    assert depois["categoria"] != "velha"
    assert (depois["bill_id"] != bill_antes) is (campo == "data_e_fatura")  # a data move de fatura
    (rel,) = script.main(["--user", str(uid_pro)])
    assert (rel["a_corrigir"], rel["a_remover"]) == (0, 0)


def test_usuario_inexistente_aborta():
    with pytest.raises(SystemExit):
        script.main(["--user", "999999999999"])
