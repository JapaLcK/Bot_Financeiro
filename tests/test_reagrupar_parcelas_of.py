"""`scripts/reagrupar_parcelas_of.py` — backfill do group_id das parcelas do OF (#1290).

Estado do bug: compras importadas (grupo certo) com o group_id antigo espalhado por SQL (um uuid por
linha) e uma compra "antiga certa" com a parcela nova partida. A foto é de `credit_transactions`
SEM group_id e de `credit_bills` inteira: o script só pode mexer em `group_id`.

CONTROLES NEGATIVOS (cada um vermelho com o conserto desligado; tabela no relato do PR):
- sem compare-and-set (`and group_id is not distinct from`) -> test_linha_mexida_entre_leitura_e_escrita_fica_de_fora;
- sem o dono na leitura (`ct.user_id=c.user_id`) -> test_outro_usuario_* (o `and user_id=%s` do UPDATE é defesa em
  profundidade: com a leitura certa, nenhum teste o discrimina);
- sem `source = 'open_finance'` -> test_manual_e_ofx_ficam;
- outra chave (card_id diferente do import) -> test_parcela_futura_cai_no_mesmo_grupo;
- dry-run que escreve -> test_dry_run_nao_grava_e_conta.
Positivos: o apply junta (1 grupo por compra), a 2ª rodada dá 0, os contadores acendem.
"""
from __future__ import annotations

import pytest

import db
from conftest import usuario_pagante
from scripts import reagrupar_parcelas_of as script
from tests._of_cash_helpers import dia, q
from tests.test_of_categoria_pt import _cartao
from tests.test_of_parcelas_grupo import _p
from utils_date import today_tz

A, B, C = "2026-03-10T10:00:00.000Z", "2026-05-02T10:00:00.000Z", "2026-04-01T10:00:00.000Z"


def _compras(loja="Loja"):
    """A: 3 parcelas, B: 2, C: 4 (4ª só chega depois)."""
    return ([_p(f"a{i}", i, 3, dia(10, 3 + i), loja, A) for i in (1, 2, 3)]
            + [_p(f"b{i}", i, 2, dia(2, 4 + i), "Outra", B) for i in (1, 2)]
            + [_p(f"c{i}", i, 4, dia(1, 3 + i), "Mercado", C) for i in (1, 2, 3)])


def _ids(uid, *idents):
    return [r["id"] for r in q(
        "select ct.id from credit_transactions ct join open_finance_transactions o on o.imported_credit_tx_id=ct.id "
        "where ct.user_id=%s and o.provider_transaction_id = any(%s) order by ct.id", (uid, list(idents)), True)]


def estado(uid, txs=None):
    """Importa e espalha o group_id antigo (uuid por linha), menos c1/c2 (grupo antigo certo). -> cid."""
    cid = _cartao(uid, txs or _compras())
    q("update credit_transactions set group_id=gen_random_uuid() where user_id=%s and id <> all(%s)",
      (uid, _ids(uid, "c1", "c2")))
    return cid


def grupos(uid):
    return sorted(r["n"] for r in q("select count(*) n from credit_transactions where user_id=%s and source='open_finance' "
                                    "group by group_id", (uid,), True))


def foto(*uids):
    """Tudo, menos group_id, das duas tabelas."""
    return [q(f"select to_jsonb(t) - 'group_id' r from {t} t "
              "where user_id=%s order by id", (u,), True) for u in uids for t in ("credit_transactions", "credit_bills")]


def gids(uid):
    return q("select id, group_id from credit_transactions where user_id=%s order by id", (uid,), True)


@pytest.fixture
def dois():
    u, v = usuario_pagante(), usuario_pagante()
    return u, v


def test_dry_run_nao_grava_e_conta(dois):
    u, v = dois
    estado(u), estado(v)
    antes, g = foto(u, v), (gids(u), gids(v))
    (rel,) = script.main(["--user", str(u)])
    assert (rel["linhas_que_mudam"], rel["grupos_antes"], rel["grupos_depois"], rel["grupos_que_se_juntam"]) == (6, 7, 3, 3)
    assert script.main([]) and (foto(u, v), (gids(u), gids(v))) == (antes, g)  # nada gravado, nem group_id


def test_apply_junta_uma_compra_por_grupo_e_repete_zero(dois):
    u, v = dois
    estado(u), estado(v)
    antes = foto(u, v)
    assert grupos(u) == [1, 1, 1, 1, 1, 1, 2] and grupos(v) == grupos(u)
    (rel,) = script.main(["--user", str(u), "--apply"])
    assert rel["gravadas"] == 6 and grupos(u) == [2, 3, 3]            # C ainda sem a 4ª parcela
    assert grupos(v) == [1, 1, 1, 1, 1, 1, 2]                       # outro usuário intacto
    (de_novo,) = script.main(["--user", str(u), "--apply"])
    assert (de_novo["gravadas"], de_novo["linhas_que_mudam"]) == (0, 0)
    script.main(["--apply"])
    assert grupos(v) == [2, 3, 3] and foto(u, v) == antes             # nenhuma outra coluna mexeu


def test_parcela_futura_cai_no_mesmo_grupo(user_id):
    """A chave do script é a do import: a 4ª parcela de C, importada DEPOIS do apply, junta com as outras."""
    cid = estado(user_id)
    script.main(["--user", str(user_id), "--apply"])
    _cartao(user_id, _compras() + [_p("c4", 4, 4, dia(1, 7), "Mercado", C)], cid)
    assert grupos(user_id) == [2, 3, 4]


def test_manual_e_ofx_ficam():
    """Parcelamento manual e OFX com espelho OF forjado apontando para eles: source != open_finance não muda."""
    uid = usuario_pagante()
    estado(uid)
    card = db.create_card(uid, "Manual", closing_day=28, due_day=10)
    db.add_credit_purchase_installments(uid, card, 300, "compras", "Sofa", today_tz(), 3)
    db.add_credit_purchase_installments(uid, card, 200, "compras", "Tv", today_tz(), 2)
    manual, ofx = q("select id from credit_transactions where user_id=%s and source='manual' order by id",
                    (uid,), True)[::3]
    q("update credit_transactions set source='ofx' where id=%s", (ofx["id"],))
    for ident, alvo in (("a1", manual["id"]), ("a2", ofx["id"])):    # espelho do OF aponta para a linha alheia
        q("update open_finance_transactions set imported_credit_tx_id=%s where provider_transaction_id=%s", (alvo, ident))
    antes = [r for r in gids(uid) if r["id"] in (manual["id"], ofx["id"])]
    script.main(["--user", str(uid), "--apply"])
    assert [r for r in gids(uid) if r["id"] in (manual["id"], ofx["id"])] == antes


def test_outro_usuario_nao_e_tocado_nem_por_espelho_forjado(dois):
    """O espelho OF de V aponta (imported_credit_tx_id forjado) para uma linha de U, com outra loja."""
    u, v = dois
    estado(u)
    estado(v, _compras("SEGREDO"))
    (alvo,) = _ids(u, "a1")
    q("update open_finance_transactions set imported_credit_tx_id=%s where provider_transaction_id='a1' "
      "and account_id in (select a.id from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id "
      "where c.user_id=%s)", (alvo, v))
    antes_u = gids(u)
    (rel_v,) = script.main(["--user", str(v)])
    assert rel_v["linhas_que_mudam"] == 5                              # a linha de U não entra na conta de V (6 - a1)
    script.main(["--user", str(v), "--apply"])
    assert gids(u) == antes_u                                          # V não escreveu em U
    script.main(["--apply"])
    assert grupos(u) == [2, 3, 3]                                      # e U agrupa pelo PRÓPRIO espelho


def test_linha_mexida_entre_leitura_e_escrita_fica_de_fora(user_id, monkeypatch):
    estado(user_id)
    (alvo,) = _ids(user_id, "a1")
    original = script._plano

    def plano_e_depois_mexe(cur, uid):
        rel, mudam = original(cur, uid)
        q("update credit_transactions set group_id='11111111-1111-1111-1111-111111111111' where id=%s", (alvo,))
        return rel, mudam

    monkeypatch.setattr(script, "_plano", plano_e_depois_mexe)
    (rel,) = script.main(["--user", str(user_id), "--apply"])
    assert rel["gravadas"] == 5 and str({r["id"]: r["group_id"] for r in gids(user_id)}[alvo]).startswith("11111111")


def test_contadores_de_divergencia_acendem(user_id):
    txs = _compras() + [
        _p("d1", 1, 2, dia(5, 4), "Sofa", "2026-04-05T10:00:00.000Z"), _p("d2", 2, 2, dia(5, 5), "Sofa", "2026-04-05T10:00:00.000Z"),
        _p("e1", 1, 2, dia(5, 4), "Sem data", None), _p("e2", 2, 2, dia(5, 5), "Sem data", None)]
    f = _p("f1", 1, 2, dia(6, 4), "x", "2026-04-06T10:00:00.000Z"), _p("f2", 2, 2, dia(6, 5), "x", "2026-04-06T10:00:00.000Z")
    for p in f:
        p["description"] = "Padaria"                                   # o banco não mandou o "n/N"
    estado(user_id, txs + list(f))
    d1, d2 = _ids(user_id, "d1", "d2")
    q("update credit_transactions set nota='Meu sofá' where id=%s", (d1,))
    q("update credit_transactions set categoria='viagem', installment_no=1 where id=%s", (d2,))
    (rel,) = script.main(["--user", str(user_id)])
    assert (rel["juntados_com_nota_divergente"], rel["juntados_com_categoria_divergente"],
            rel["juntados_com_installment_no_repetido"]) == (1, 1, 1)
    assert (rel["linhas_sem_purchaseDate"], rel["linhas_sem_corte_de_sufixo"]) == (2, 2)
    assert set(rel) == {"user", *script.CONTADORES}                    # só contagens: nada de descrição nem valor


def test_grupo_certo_que_se_parte_aparece_no_dry_run(user_id):
    """Espelho mais novo de UMA parcela com outro purchaseDate (reconexão): o grupo certo vira dois — o dono tem de ver."""
    _cartao(user_id, [_p(f"g{i}", i, 3, dia(10, 3 + i), "Loja", A) for i in (1, 2, 3)])
    q("update open_finance_transactions set raw = jsonb_set(raw, '{creditCardMetadata,purchaseDate}', '\"2026-03-11T10:00:00.000Z\"') "
      "where provider_transaction_id='g3'")
    (rel,) = script.main(["--user", str(user_id)])
    assert (rel["grupos_antes"], rel["grupos_depois"], rel["grupos_que_se_partem"]) == (1, 2, 1)


def test_usuario_com_raw_estranho_nao_para_os_outros(dois, capsys):
    u, v = dois
    estado(u), estado(v)
    q("update open_finance_transactions o set raw = '{\"creditCardMetadata\": [1]}' from credit_transactions ct "
      "where ct.id = o.imported_credit_tx_id and ct.user_id = %s", (u,))
    out = script.main(["--apply"])
    assert v in {r["user"] for r in out} and u not in {r["user"] for r in out}
    assert grupos(v) == [2, 3, 3]                                      # o outro usuário foi reagrupado (A, B, C)
    assert f"'user': {u}, 'erro': 'AttributeError'" in capsys.readouterr().out


def test_falha_no_meio_do_apply_nao_grava_nada_daquele_usuario(user_id, monkeypatch, capsys):
    """O 3º UPDATE estoura: a transação do usuário volta inteira (nada de commit por linha) e a falha é contada."""
    import psycopg
    estado(user_id)
    antes, n, orig = gids(user_id), {"k": 0}, psycopg.Cursor.execute

    def execute(self, query, params=None, **kw):
        if isinstance(query, str) and query.startswith("update credit_transactions set group_id"):
            n["k"] += 1
            if n["k"] == 3:
                raise RuntimeError("boom")
        return orig(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    falhas: list[int] = []
    out = script.main(["--user", str(user_id), "--apply"], falhas=falhas)
    monkeypatch.undo()
    assert n["k"] == 3 and out == [] and falhas == [user_id] and gids(user_id) == antes
    assert "RuntimeError" in capsys.readouterr().out


def test_grupo_antigo_compartilhado_move_junto_e_null_fica(user_id):
    """Duas parcelas com o MESMO uuid antigo mudam juntas para o grupo da nova; linha com group_id null fica null."""
    _cartao(user_id, _compras())
    c1, c2 = _ids(user_id, "c1", "c2")
    (a1,) = _ids(user_id, "a1")
    q("update credit_transactions set group_id='22222222-2222-2222-2222-222222222222' where id = any(%s)", ([c1, c2],))
    q("update credit_transactions set group_id=null where id=%s", (a1,))
    (rel,) = script.main(["--user", str(user_id), "--apply"])
    g = {r["id"]: r["group_id"] for r in gids(user_id)}
    assert g[a1] is None
    assert g[c1] == g[c2] == g[_ids(user_id, "c3")[0]]
    assert rel["gravadas"] == 2
