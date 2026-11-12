"""#1415: o ZIP da exportação LGPD leva os dados de afiliado do titular, e só os dele.

Banco real e `build_user_export_zip` real. T é afiliado (comissões, saque pago, chave trocada
depois do saque, indicado que excluiu a conta) e também foi indicado por P; O é outro afiliado
com chave e saque; Z é afiliado sem indicado; N não tem nada.

CONTROLES (CLAUDE.md §3), cada um injetado à mão em `db/privacy.py`, rodado e revertido:
  - `where` das comissões com `or true` → vermelho em 2 (e 6, 7).
  - colunas das comissões → `c.*` → vermelho em 5, 6 (e 7).
  - sem o `pop` de `pix_key_enc` → vermelho em 5, 6 (e 8, 9).
  - saque com a chave do cadastro em vez do snapshot → vermelho em 3.
  - sem o `try` da decifragem → vermelho em 9; `except RuntimeError` → vermelho em 9[v1:ção].
  - `count(*)` no lugar de `count(r.id)` → vermelho em 8.
  - `join` (inner) em `affiliate_referrals` → vermelho em 8 (e 9).
  - referral só com `referred_user_id is not null` → vermelho em 1.
  - sem `v.amount_cents <> c.amount_cents` → vermelho em 2.
  - `join` (inner) em `affiliate_refund_reviews` → vermelho em 2 (e 7).
  - `actor=f"user:{uid}"` no lugar de `ATOR_TITULAR` → vermelho em 10.
  - `db/privacy.py` da `main` → vermelho em 1-10 (`KeyError: 'afiliado'` em 1 e 8).
Positivo: os casos 1-4 e 7 provam que o dado legítimo sai inteiro (valores, chave em claro,
contagem igual à da tela); os vazios (8) provam que ninguém ganha linha que não tem.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import db
from core.crypto import ATOR_TITULAR, encrypt_pii_optional, hash_pii_optional
from db.affiliate_reviews import registrar_reembolso, resolver_parcial
from db.affiliates import (
    get_affiliate_stats,
    mark_payout_paid,
    record_commission_for_invoice,
    record_referral,
    request_payout,
    set_affiliate_pix_key,
)
from db.connection import get_conn
from db.privacy import build_user_export_zip
from test_afiliado_area import _afiliado, _comissao, _conta
from test_afiliado_reembolso import _paga

AGORA = datetime(2026, 11, 5, tzinfo=timezone.utc)
DATASETS = ("afiliado", "afiliado_comissoes", "afiliado_saques", "indicacao_recebida")
COLUNAS = {
    "afiliado": {"code", "status", "commission_bps", "created_at", "indicacoes", "chave_pix"},
    "afiliado_comissoes": {"id", "invoice_amount_cents", "amount_cents", "commission_bps", "paid_months",
                           "status", "available_at", "payout_id", "payment_check_pending", "created_at",
                           "original_amount_cents"},
    "afiliado_saques": {"id", "amount_cents", "status", "requested_at", "paid_at", "note", "chave_pix"},
    "indicacao_recebida": {"code_used", "created_at"},
}


def _chave(c, plain):
    enc = encrypt_pii_optional(plain)
    set_affiliate_pix_key(c.aff["id"], hash_pii_optional(plain), enc)
    return enc


def _q(sql, *params):
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _parcial(com, aprovado, nota):
    registrar_reembolso(com.fatura, "partial", 10_000, AGORA)
    rev = _q("select id from affiliate_refund_reviews where stripe_invoice_id = %s", com.fatura)[0]
    assert resolver_parcial(rev["id"], "adm", aprovado, nota, 10_000)


def _zip(uid):
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    csvs = {n: z.read(n).decode() for n in z.namelist() if n.startswith("csv/")}
    celulas = {v for t in csvs.values() for linha in csv.reader(io.StringIO(t)) for v in linha}
    return SimpleNamespace(
        dados=json.loads(z.read("dados.json"))["dados"],
        manifesto=json.loads(z.read("manifesto.json")),
        texto=z.read("dados.json").decode() + "".join(csvs.values()),
        csvs=csvs, celulas=celulas,
    )


def _cenario():
    s = uuid.uuid4().hex[:10]
    k1, k2, ko = f"k1-{s}@pix.test", f"k2-{s}@pix.test", f"ko-{s}@pix.test"
    t = _afiliado()
    enc_k1 = _chave(t, k1)
    c1 = _comissao(t, 120)
    saque = request_payout(t.aff["id"], pix_key_enc=encrypt_pii_optional(k1))["id"]
    assert mark_payout_paid(saque, note="pago teste")
    enc_k2 = _chave(t, k2)  # trocou a chave DEPOIS do saque: o saque segue com K1
    nota = f"nota-revisao-{s}"
    fp, fc = _paga(t, 100), _paga(t, 50)
    _parcial(fp, 6_000, nota)   # aprovado menor: original 10_000
    _parcial(fc, 5_000, nota)   # aprovado cheio: sem original
    excluido = _conta(plano=False)
    assert record_referral(t.aff["code"], excluido.uid)
    db.delete_user_data(excluido.uid)

    p = _afiliado()
    assert record_referral(p.aff["code"], t.uid)
    ft = f"in_{uuid.uuid4().hex[:12]}"
    com_p = record_commission_for_invoice(t.uid, ft, 50_000)

    o = _afiliado()
    enc_ko = _chave(o, ko)
    fo = _comissao(o, 80)
    request_payout(o.aff["id"], pix_key_enc=encrypt_pii_optional(ko))

    encs_t = {enc_k1, enc_k2} | {r["pix_key_enc"] for r in _q(
        "select pix_key_enc from affiliate_payouts where affiliate_id = %s", t.aff["id"])}
    encs_o = {enc_ko} | {r["pix_key_enc"] for r in _q(
        "select pix_key_enc from affiliate_payouts where affiliate_id = %s", o.aff["id"])}
    return SimpleNamespace(t=t, p=p, o=o, k1=k1, k2=k2, ko=ko, c1=c1, fp=fp, fc=fc, ft=ft, fo=fo,
                           saque=saque, nota=nota, com_p=com_p, encs_t=encs_t, encs_o=encs_o,
                           indicados=[c1.indicado, fp.indicado, fc.indicado, excluido])


def test_1_cadastro_com_contagem_da_tela_e_chave_atual():
    c = _cenario()
    a = _zip(c.t.uid).dados["afiliado"]
    assert len(a) == 1
    linha = a[0]
    assert (linha["code"], linha["status"], linha["commission_bps"]) == (c.t.aff["code"], "active", 1000)
    assert linha["created_at"]
    assert linha["chave_pix"] == c.k2
    assert linha["indicacoes"] == 4 == get_affiliate_stats(c.t.aff["id"])["referrals"]  # inclui o excluído


def test_2_comissoes_do_titular_com_o_valor_original():
    c = _cenario()
    rows = {r["id"]: r for r in _zip(c.t.uid).dados["afiliado_comissoes"]}
    banco = {r["id"]: r for r in _q(
        "select id, invoice_amount_cents, amount_cents, commission_bps, paid_months, status, payout_id,"
        " payment_check_pending from affiliate_commissions where affiliate_id = %s", c.t.aff["id"])}
    assert set(rows) == set(banco) == {c.c1.id, c.fp.id, c.fc.id}
    for i, b in banco.items():
        assert {k: rows[i][k] for k in b} == dict(b)
    assert (rows[c.fp.id]["amount_cents"], rows[c.fp.id]["original_amount_cents"]) == (6_000, 10_000)
    assert rows[c.fc.id]["original_amount_cents"] is None
    assert rows[c.c1.id]["original_amount_cents"] is None
    assert rows[c.c1.id]["payout_id"] == c.saque


def test_3_saque_com_a_chave_do_pedido():
    c = _cenario()
    rows = _zip(c.t.uid).dados["afiliado_saques"]
    ids = [r["id"] for r in _q("select id from affiliate_payouts where affiliate_id = %s", c.t.aff["id"])]
    assert [r["id"] for r in rows] == ids == [c.saque]
    assert (rows[0]["status"], rows[0]["note"], rows[0]["chave_pix"]) == ("paid", "pago teste", c.k1)
    assert rows[0]["amount_cents"] == 12_000 and rows[0]["paid_at"]


def test_4_indicacao_recebida_so_codigo_e_data():
    c = _cenario()
    rows = _zip(c.t.uid).dados["indicacao_recebida"]
    assert len(rows) == 1 and rows[0]["code_used"] == c.p.aff["code"] and rows[0]["created_at"]


def test_5_colunas_exatas():
    c = _cenario()
    z = _zip(c.t.uid)
    for nome in DATASETS:
        assert z.dados[nome], nome
        for linha in z.dados[nome]:
            assert set(linha) == COLUNAS[nome], nome
    cabecalho = next(csv.reader(io.StringIO(z.csvs["csv/afiliado_comissoes.csv"])))
    assert cabecalho == sorted(COLUNAS["afiliado_comissoes"])


def test_6_nada_de_indicado_de_outro_afiliado_nem_cifrado():
    c = _cenario()
    z = _zip(c.t.uid)
    proibidos = (c.encs_t | c.encs_o | {c.ko, c.c1.fatura, c.fp.fatura, c.fc.fatura, c.ft, c.fo.fatura,
                                         c.o.aff["code"], c.nota}
                 | {i.email for i in c.indicados})
    assert all(enc for enc in c.encs_t) and len(c.encs_t) == 3
    vazou = sorted(x for x in proibidos if x in z.texto)
    assert vazou == []
    assert "adm" not in z.celulas and '"adm"' not in z.texto
    assert c.com_p["id"] not in {r["id"] for r in z.dados["afiliado_comissoes"]}


def test_7_lado_do_afiliado_nao_identifica_o_indicado():
    c = _cenario()
    z = _zip(c.p.uid)
    assert z.dados["afiliado"][0]["indicacoes"] == 1
    rows = z.dados["afiliado_comissoes"]
    assert [r["id"] for r in rows] == [c.com_p["id"]]
    assert not {"referred_user_id", "stripe_invoice_id"} & set(rows[0])
    assert [x for x in (c.ft, c.t.email, c.k1, c.k2) if x in z.texto] == []


def test_8_vazios():
    z_af = _afiliado()
    n = _conta()
    z = _zip(z_af.uid).dados
    assert z["afiliado"] == [{"code": z_af.aff["code"], "status": "active", "commission_bps": 1000,
                              "created_at": z["afiliado"][0]["created_at"], "indicacoes": 0, "chave_pix": None}]
    assert [z[k] for k in DATASETS[1:]] == [[], [], []]
    zn = _zip(n.uid)
    assert [zn.dados[k] for k in DATASETS] == [[], [], [], []]
    assert [zn.manifesto["datasets"][k] for k in DATASETS] == [0, 0, 0, 0]
    for k in DATASETS:
        assert zn.csvs[f"csv/{k}.csv"].strip() == "sem_dados"


@pytest.mark.parametrize("cifrado", ["v1:nao-e-fernet", "v1:ção"])  # não-ASCII: UnicodeEncodeError
def test_9_linha_indecifravel_vira_marcador_sem_derrubar_o_zip(caplog, cifrado):
    z_af = _afiliado()
    with get_conn() as conn:
        conn.execute("update affiliates set pix_key_enc = %s where id = %s", (cifrado, z_af.aff["id"]))
        conn.commit()
    with caplog.at_level(logging.WARNING, logger="db.privacy_export"):
        z = _zip(z_af.uid)
    assert z.dados["afiliado"][0]["chave_pix"] == "(indecifrável)"
    assert cifrado not in z.texto
    avisos = [r for r in caplog.records if "indecifrável na exportação" in r.getMessage()]
    assert len(avisos) == 1 and cifrado not in avisos[0].getMessage()


def test_10_cada_chave_decifrada_e_auditada_em_nome_do_titular(monkeypatch):
    c = _cenario()
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")  # o conftest desliga por padrão
    _zip(c.t.uid)
    linhas = _q("select id, purpose, field, actor from pii_access_log where subject_user_id = %s"
                " and purpose = 'data_export'", c.t.uid)
    try:
        assert len(linhas) == 2  # 1 do cadastro + 1 saque
        assert {(r["field"], r["actor"]) for r in linhas} == {("pix_key", ATOR_TITULAR)}
    finally:
        with get_conn() as conn:
            conn.execute("delete from pii_access_log where id = any(%s)", ([r["id"] for r in linhas],))
            conn.commit()
