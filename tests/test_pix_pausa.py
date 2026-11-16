"""Pausa da concessão do Pix (#1574, `db/pix_pausa.py`). Postgres real e o dreno real.

O que está medido aqui (a migração está em `test_pix_pausa_migracao.py`):
  * a venda feita na pausa fica guardada, sem tentativa, e concede na retomada (positivo);
  * `test_conversa_estorno_na_pausa_drena_antes_da_compra`: com vendas pausadas na fila, o estorno drena
    durante a pausa e a compra estornada nunca concede. Depende da pausa: sem ela, a varredura drena a
    compra antes do estorno e saem acesso, e-mail e comissão;
  * recusa do gatilho que chega ao código novo (dreno em voo na ativação, baixa desfeita que reconcede)
    deixa o evento aberto, sem tentativa e sem alerta de travado;
  * a versão anterior é barrada pelo banco; Stripe, admin e cartão passam (positivos);
  * a ativação espera a escrita em voo.
A purga e o aviso de pausa longa estão em `test_pix_pausa_retencao.py`.
O teste completo do dono (estorno recebido pela versão anterior, sem o marcador, drenado pelo script com
a pausa ativa) é do PR-1 (#1437): sem o marcador, quem segura o reenvio aqui é a D3 (`revoke` registrado).
Controle negativo no arquivo: `test_sem_o_gatilho_a_chamada_passa`. Os rodados à mão estão no relato.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import entregar, evento, ler, mundo_externo, nova_cobranca
from core.services.pix_drain import EFEITOS_DE_COMPRA, ROTEAMENTO, drenar_evento
from core.services.pix_sweeps import drenar_pendentes
from db.affiliates import record_commission_for_invoice, record_commission_for_pix
from db.connection import get_conn
from db.pix_charges import transicionar
from db.pix_pausa import EVENTOS_DE_COMPRA, FALHA_PAUSA, GATILHOS, SQLSTATE_PAUSA
from db.plan_grants import list_grants, upsert_grant
from test_afiliado_area import _conta
from test_afiliado_comissao_primeira import _afiliado
from test_afiliado_pagantes import _indicado


def _sql(q, args=None):
    with get_conn() as conn:
        cur = conn.execute(q, args)
        linhas = cur.fetchall() if q.lstrip().startswith("select") else None
        conn.commit()
    return linhas


def ativar():
    _sql("select pix_pausa_ativar('teste')")  # o comando do runbook


def retomar():
    _sql("update pix_pausa_concessao set retomada_em = now() where retomada_em is null")  # o do runbook


def limpar_pausa():
    """Sem a linha, a guarda da purga não vaza para outro teste. `delete` e `truncate` são recusados pelo
    gatilho: desligado, apagado e religado numa transação só, ninguém de fora o vê desligado."""
    with get_conn() as conn:
        conn.execute("alter table pix_pausa_concessao disable trigger trg_pausa_pix_sem_delete")
        conn.execute("delete from pix_pausa_concessao")
        conn.execute("alter table pix_pausa_concessao enable trigger trg_pausa_pix_sem_delete")
        conn.commit()


@pytest.fixture
def externo(monkeypatch):
    garantir_system_event_logs()
    yield mundo_externo(monkeypatch)
    limpar_pausa()


@pytest.fixture
def varredura(monkeypatch):
    """A outbox é do worker inteiro: a varredura só enxerga os eventos que o teste põe na lista, na ordem
    e com o `limite` do SQL real (o filtro da pausa e o `order by` continuam valendo)."""
    import db.webhook_outbox as outbox

    meus, real = [], outbox.eventos_pendentes
    monkeypatch.setattr(outbox, "eventos_pendentes",
                        lambda limite=100: [e for e in real(10_000) if e in meus][:limite])
    return meus


def _grants_pix(uid):
    return [g for g in list_grants(uid) if g["source"] == "pix"]


def _grants_pix_ativos(uid):
    return [g for g in _grants_pix(uid) if g["status"] == "active"]


def _comissoes(uid):
    return [(r["stripe_invoice_id"], r["status"]) for r in _sql(
        "select stripe_invoice_id, status from affiliate_commissions where referred_user_id = %s order by id", (uid,))]


def _aberto_sem_tentativa(event_id, tentativas=0, erro=None):
    ev = evento(event_id)
    assert (ev["processed_at"], ev["attempts"], ev["last_error"]) == (None, tentativas, erro), ev


def _venda(afiliado=True):
    """Conta sem plano e cobrança `pending`; com `afiliado`, a conta é indicada (a venda gera comissão)."""
    uid = (_indicado(_afiliado()) if afiliado else _conta(plano=False)).uid
    conta(uid, "free", None)
    return uid, nova_cobranca(uid)


def _janela():
    agora = datetime.now(timezone.utc)
    return agora, agora + timedelta(days=365)


# ── a conversa ───────────────────────────────────────────────────────────────

def test_venda_durante_a_pausa_fica_aberta_e_concede_na_retomada(externo, varredura):
    """POSITIVO da pausa: ela segura a venda, não a engole."""
    uid, cob = _venda()
    ativar()
    ids = [entregar(tipo, cob) for tipo in EVENTOS_DE_COMPRA]  # o caminho do webhook (background_tasks)
    varredura.extend(ids)
    assert drenar_pendentes() == 0, "a compra pausada não é listada (nem conta como drenada)"
    for e in ids:
        _aberto_sem_tentativa(e)
    assert ler(cob["id"])["status"] == "pending"
    assert _grants_pix(uid) == [] and _comissoes(uid) == []
    assert (externo["ga4"], externo["capi"], externo["email"]) == (0, 0, 0)

    retomar()
    assert drenar_pendentes() == 2
    assert all(evento(e)["processed_at"] for e in ids)
    assert ler(cob["id"])["status"] == "paid"
    assert len(_grants_pix_ativos(uid)) == 1
    assert _comissoes(uid) == [(cob["external_reference"], "pending")]
    assert (externo["ga4"], externo["capi"], externo["email"]) == (1, 1, 1)


def test_conversa_estorno_na_pausa_drena_antes_da_compra(externo, varredura):
    """Na pausa chegam vendas de outros clientes e, do indicado, o pagamento e o estorno dele; o caminho
    rápido falhou, então só a varredura (lote de 3) os drena. O estorno não pode ficar atrás das compras
    pausadas: drenado na pausa, ele registra o `revoke`, e a compra, na retomada, não concede nada."""
    ativar()
    outros = [_venda(afiliado=False) for _ in range(3)]
    varredura.extend(entregar("PAYMENT_RECEIVED", cob, drenar=False) for _, cob in outros)
    uid, cob = _venda()
    varredura.append(entregar("PAYMENT_RECEIVED", cob, drenar=False))
    estorno = entregar("PAYMENT_REFUNDED", cob, drenar=False)
    varredura.append(estorno)
    drenar_pendentes(limite=3)
    assert evento(estorno)["processed_at"] is not None, "o estorno ficou atrás das compras pausadas"
    reenvio = entregar("PAYMENT_CONFIRMED", cob)  # reenviado durante a pausa
    _aberto_sem_tentativa(reenvio)

    retomar()
    varredura.append(reenvio)
    drenar_pendentes(limite=3)
    drenar_pendentes(limite=3)
    depois = entregar("PAYMENT_RECEIVED", cob)  # e depois da retomada
    assert all(evento(e)["processed_at"] for e in [*varredura, depois])
    assert all(len(_grants_pix_ativos(u)) == 1 for u, _ in outros), "a pausa engoliu venda alheia"
    assert _grants_pix(uid) == [] and _comissoes(uid) == []
    assert externo["email"] == 3 and externo["ga4"] == 3, "saiu e-mail ou Purchase do pagamento estornado"


# ── recusa do gatilho no código novo ─────────────────────────────────────────

def test_dreno_em_voo_na_ativacao_fica_aberto_sem_tentativa(externo, monkeypatch):
    """A compra já reservada quando a pausa liga: a transição passou, o `grant` bate no gatilho. O evento
    fica aberto com `FALHA_PAUSA`, sem tentativa, e concede na retomada."""
    import core.services.pix_drain as dreno

    uid, cob = _venda()
    original = dreno.EXECUTORES["stripe_cancel"]
    monkeypatch.setitem(dreno.EXECUTORES, "stripe_cancel", lambda c, e: (ativar(), original(c, e))[1])
    e = entregar("PAYMENT_RECEIVED", cob)
    _aberto_sem_tentativa(e, erro=FALHA_PAUSA)
    assert _grants_pix(uid) == [] and _comissoes(uid) == []
    monkeypatch.setitem(dreno.EXECUTORES, "stripe_cancel", original)
    retomar()
    drenar_evento(e)
    assert evento(e)["processed_at"] is not None and len(_grants_pix_ativos(uid)) == 1


def undone_que_reconcede_na_pausa(externo, monkeypatch, passadas=7):
    """UNDONE que morreu entre revogar e reabrir; na pausa o Asaas confirma o Pix e o UNDONE tenta reconceder
    (e bate no gatilho) `passadas` vezes. Devolve `(uid, event_id)`."""
    import core.services.pix_baixa_desfeita as baixa

    uid, cob = _venda(afiliado=False)
    entregar("PAYMENT_RECEIVED", cob)
    real = baixa.reabrir_baixa
    monkeypatch.setattr(baixa, "reabrir_baixa", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caiu")))
    e = entregar("PAYMENT_RECEIVED_IN_CASH_UNDONE", cob)
    monkeypatch.setattr(baixa, "reabrir_baixa", real)
    assert _grants_pix_ativos(uid) == [] and evento(e)["attempts"] == 1
    ativar()
    externo["get_status"], externo["alerta"] = "RECEIVED", []
    for _ in range(passadas):
        drenar_evento(e)
    return uid, e


def test_baixa_desfeita_que_reconcede_nao_conta_tentativa(externo, monkeypatch):
    """O UNDONE bate no gatilho a cada passada, sem somar tentativa e sem "evento travado"."""
    uid, e = undone_que_reconcede_na_pausa(externo, monkeypatch)
    _aberto_sem_tentativa(e, tentativas=1, erro=FALHA_PAUSA)
    assert not [a for a in externo["alerta"] if "travado" in a] and _grants_pix_ativos(uid) == []
    retomar()
    drenar_evento(e)
    assert evento(e)["processed_at"] is not None and len(_grants_pix_ativos(uid)) == 1


# ── a versão anterior e o recorte dos gatilhos ───────────────────────────────

def _recusada(chamada):
    with pytest.raises(psycopg.Error) as erro:
        chamada()
    assert erro.value.sqlstate == SQLSTATE_PAUSA, erro.value


def _chamadas(uid, cob):
    """Uma chamada direta por gatilho, como a versão antiga as faria. Mesma ordem de `GATILHOS`."""
    inicio, fim = _janela()
    return {
        "trg_pix_pausa_cobranca": lambda: transicionar(cob["id"], de="pending", para="paid",
                                                       access_starts_at=inicio, access_expires_at=fim),
        "trg_pix_pausa_grant_novo": lambda: upsert_grant(uid, "pix", str(cob["id"]), "pro_max", inicio, fim, 1),
        "trg_pix_pausa_grant": lambda: _sql("update plan_grants set status = 'active' where source = 'pix'"
                                            " and external_ref = %s", (str(cob["id"]),)),
        "trg_pix_pausa_comissao": lambda: record_commission_for_pix({**cob, "user_id": uid, "paid_at": inicio}, 12),
    }


def _grant_revogado(uid, cob):
    """A linha que o gatilho de update pega (antes de ativar a pausa)."""
    _chamadas(uid, cob)["trg_pix_pausa_grant_novo"]()
    _sql("update plan_grants set status = 'revoked' where source = 'pix' and external_ref = %s", (str(cob["id"]),))


def test_chamadas_diretas_da_versao_antiga(externo):
    uid, cob = _venda()
    chamadas = _chamadas(uid, cob)
    assert list(chamadas) == [g[0] for g in GATILHOS]
    _grant_revogado(uid, cob)
    ativar()
    for chamada in chamadas.values():
        _recusada(chamada)
    assert ler(cob["id"])["status"] == "pending"
    assert _grants_pix_ativos(uid) == [] and _comissoes(uid) == []


@pytest.mark.parametrize("gatilho,tabela", [(g[0], g[1]) for g in GATILHOS])
def test_sem_o_gatilho_a_chamada_passa(externo, gatilho, tabela):
    """CONTROLE NEGATIVO de cada gatilho: desligado, a mesma chamada passa com a pausa ativa."""
    uid, cob = _venda()
    if gatilho == "trg_pix_pausa_grant":
        _grant_revogado(uid, cob)
    ativar()
    _sql(f"alter table {tabela} disable trigger {gatilho}")
    try:
        _chamadas(uid, cob)[gatilho]()
    finally:
        _sql(f"alter table {tabela} enable trigger {gatilho}")
    gravou = {"pix_charges": lambda: ler(cob["id"])["status"] == "paid",
              "plan_grants": lambda: len(_grants_pix_ativos(uid)) == 1,
              "affiliate_commissions": lambda: _comissoes(uid) == [(cob["external_reference"], "pending")]}
    assert gravou[tabela](), "a chamada passou sem gravar: o controle não mede nada"


def test_excluir_conta_de_pagante_pix_durante_a_pausa(externo):
    """`delete_user_data` pseudonimiza a cobrança paga (`user_id` nulo, `purged_at`) com a pausa ativa: o
    gatilho só pega a MUDANÇA para `paid`. POSITIVO também de update em cobrança já paga e em grant Pix já ativo."""
    import db

    uid, cob = _venda()
    entregar("PAYMENT_RECEIVED", cob)
    ativar()
    _sql("update pix_charges set qr_payload_enc = null where id = %s", (cob["id"],))
    _sql("update plan_grants set ends_at = ends_at + interval '1 day' where source = 'pix' and external_ref = %s",
         (str(cob["id"]),))
    db.delete_user_data(uid)
    linha = ler(cob["id"])
    assert (linha["status"], linha["user_id"], linha["purged_at"] is not None) == ("paid", None, True)


def test_stripe_admin_e_cartao_passam(externo):
    """POSITIVO dos gatilhos: o `WHEN` só pega o Pix."""
    uid, cob = _venda()
    inicio, fim = _janela()
    ativar()
    assert upsert_grant(uid, "stripe", f"sub_{uuid.uuid4().hex[:12]}", "pro", inicio, fim, 1)
    assert upsert_grant(uid, "admin", f"admin:{uid}", "pro_max", inicio, fim, 1)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    assert record_commission_for_invoice(uid, fatura, 4990)
    assert [c for c, _ in _comissoes(uid)] == [fatura]
    # e a cobrança Pix anda para estados que não concedem
    assert transicionar(cob["id"], de="pending", para="canceled", apagar_qr=True)


def test_os_eventos_que_concedem_sao_os_retidos():
    """`EVENTOS_DE_COMPRA` (a fonte de `pix_pausa_retem`) são os que o `ROTEAMENTO` leva aos efeitos de compra.
    Evento novo que conceda e fique fora dela: o código novo bateria no gatilho a cada passada."""
    assert {t for t, (_, _, ef) in ROTEAMENTO.items() if set(ef) & set(EFEITOS_DE_COMPRA)} == set(EVENTOS_DE_COMPRA)


# ── ativar e avisar ──────────────────────────────────────────────────────────

def test_ativar_espera_a_escrita_em_voo(externo):
    """Grant Pix gravado numa transação aberta antes da ativação: `pix_pausa_ativar` só volta depois do
    commit dela. Quando a pausa está confirmada, nada que passou pelo gatilho sem pausa ainda está em voo."""
    uid, cob = _venda(afiliado=False)
    ativou = threading.Event()
    with get_conn() as escrita:
        inicio, fim = _janela()
        escrita.execute("insert into plan_grants (user_id, source, external_ref, plan_stored, starts_at, ends_at,"
                        " status) values (%s, 'pix', %s, 'pro_max', %s, %s, 'active')", (uid, str(cob["id"]), inicio, fim))
        t = threading.Thread(target=lambda: (ativar(), ativou.set()))
        t.start()
        assert not ativou.wait(0.5), "ativou com a escrita do grant ainda aberta"
        antes_do_commit = _sql("select clock_timestamp() as t")[0]["t"]
        escrita.commit()
    t.join(10)
    assert ativou.is_set() and len(_grants_pix_ativos(uid)) == 1
    desde = _sql("select ativa_desde from pix_pausa_concessao")[0]["ativa_desde"]
    assert desde > antes_do_commit, "`ativa_desde` tem de ser o fim da espera, não o começo da chamada"
    _recusada(_chamadas(uid, cob)["trg_pix_pausa_cobranca"])
