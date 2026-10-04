"""Assinaturas pelo Recurring Payments da Pluggy: cliente, gravação no sync,
montagem da lista (`core/services/assinaturas.py`) e a migração do flag de
silêncio. Banco real; a Pluggy é mockada no cliente (`httpx.MockTransport`) ou
no sync (`list_pluggy_recurring_payments`)."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

import core.services.pluggy as pluggy
import core.services.pluggy_sync as ps
import db
from _apoio_assinaturas import HOJE, conta, dez_e_vinte_centavos, mensais, netflix_no_cartao, rp, semeia, tx
from api.v2.assinaturas import Assinatura, Assinaturas
from core.services.assinaturas import listar_assinaturas
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from db.schema import RECURRING_SEED_SILENT_SQL


def _q(sql, *args):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


# ── 1. Cliente ───────────────────────────────────────────────────────────────

def _pluggy_responde(monkeypatch, status, corpo, pedidos):
    def _handler(req):
        pedidos.append(req)
        return httpx.Response(status, json=corpo)

    def _fabrica(*a, **kw):
        return httpx.Client(transport=httpx.MockTransport(_handler), **kw)
    monkeypatch.setattr(pluggy, "httpx", SimpleNamespace(Client=_fabrica, Response=httpx.Response))


def test_cliente_posta_no_enrichment_com_chave_e_item(monkeypatch):
    pedidos = []
    lista = [{"description": "NETFLIX.COM", "averageAmount": -39.9, "occurrences": ["t1"]}]
    _pluggy_responde(monkeypatch, 200, {"recurringPayments": lista}, pedidos)
    assert pluggy.list_pluggy_recurring_payments("item-1", "chave-x") == lista
    (req,) = pedidos
    assert (req.method, str(req.url)) == ("POST", "https://enrichment-api.pluggy.ai/recurring-payments")
    assert req.headers["X-API-KEY"] == "chave-x"
    assert req.read() == b'{"itemId":"item-1"}'


def test_cliente_403_vira_erro_sem_o_corpo(monkeypatch):
    _pluggy_responde(monkeypatch, 403, {"code": "FORBIDDEN", "message": "CPF 123.456.789-00"}, [])
    with pytest.raises(PluggyApiError) as exc:
        pluggy.list_pluggy_recurring_payments("item-1", "k")
    assert exc.value.status_code == 403
    assert "123.456" not in str(exc.value)


def test_cliente_200_sem_a_lista_e_falha(monkeypatch):
    _pluggy_responde(monkeypatch, 200, {"results": []}, [])
    with pytest.raises(PluggyApiError):
        pluggy.list_pluggy_recurring_payments("item-1", "k")


# ── 2. Sync: falha mantém o último resultado ─────────────────────────────────

def _item_de(cid):
    return _q("select provider_item_id from open_finance_connections where id=%s", cid)[0]["provider_item_id"]


def _sync(monkeypatch, cid, recorrentes):
    item = _item_de(cid)
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {
        "id": item, "status": "UPDATED", "executionStatus": "SUCCESS",
        "updatedAt": "2026-09-30T11:00:00.000Z", "lastUpdatedAt": "2026-09-30T11:00:00.000Z"})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [
        {"id": "acc-cc", "type": "CREDIT", "name": "Cartão", "balance": "0"}])
    monkeypatch.setattr(ps, "list_pluggy_transactions", lambda a, k=None, **kw: [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_recurring_payments", recorrentes)
    return ps.sync_pluggy_item(item)


def _linhas_rp(cid):
    return _q("select id, fetched_at from of_recurring_payments where connection_id=%s order by id", cid)


def _falha(i, k):
    raise PluggyApiError("Falha: Pluggy retornou HTTP 503", status_code=503)


def test_falha_na_pluggy_mantem_o_ultimo_resultado(monkeypatch, user_id):
    cid = netflix_no_cartao(user_id)
    antes = _linhas_rp(cid)
    assert len(antes) == 1
    assert _sync(monkeypatch, cid, _falha)["ok"] is True
    assert _linhas_rp(cid) == antes


@pytest.mark.parametrize("ruim", [
    {"description": "NETFLIX.COM", "average_amount": -39.9, "occurrences": ["nf-0"]},
    {"description": "NETFLIX.COM", "averageAmount": -39.9, "occurrences": [{"id": "nf-0"}]},
])
def test_lista_sem_nenhum_item_valido_e_falha(monkeypatch, user_id, ruim):
    """Formato mudou na Pluggy: não apaga nem carimba (o carimbo consumiria o silêncio)."""
    cid = netflix_no_cartao(user_id)
    antes = (_linhas_rp(cid), _fetched(cid))
    assert _sync(monkeypatch, cid, lambda i, k: [ruim])["ok"] is True
    assert (_linhas_rp(cid), _fetched(cid)) == antes


def test_positivo_item_valido_ao_lado_do_ruim_grava(monkeypatch, user_id):
    cid = netflix_no_cartao(user_id)
    antes = _linhas_rp(cid)
    bom = {"description": "Spotify", "averageAmount": -21.9, "occurrences": ["x"]}
    assert _sync(monkeypatch, cid, lambda i, k: [bom, {"description": "y"}])["ok"] is True
    (depois,) = _linhas_rp(cid)
    assert depois["id"] != antes[0]["id"]


def _fetched(cid):
    return _q("select recurring_fetched_at f from open_finance_connections where id=%s", cid)[0]["f"]


def test_positivo_lista_vazia_lida_apaga(monkeypatch, user_id):
    cid = netflix_no_cartao(user_id)
    assert _sync(monkeypatch, cid, lambda i, k: [])["ok"] is True
    assert _linhas_rp(cid) == []
    assert _q("select recurring_fetched_at from open_finance_connections where id=%s",
              cid)[0]["recurring_fetched_at"] is not None


# ── 3. Cartão e conta ────────────────────────────────────────────────────────

def test_netflix_no_cartao_sai_em_servicos_com_o_meio(user_id):
    netflix_no_cartao(user_id)
    lista = listar_assinaturas(user_id, HOJE)
    assert lista["outras"] == []
    (it,) = lista["servicos"]
    assert it["meio"] == {"tipo": "cartao", "nome": "Nubank Mastercard", "final": "1234"}
    assert (it["valor"], it["dia"], it["meses"]) == (Decimal("39.9"), 5, 3)
    assert (it["ultima"], it["desde"], it["proxima"]) == ("2026-09-05", "2026-07-05", "2026-10-05")
    assert (it["status"], it["valor_anterior"], it["marcada"]) == ("ativa", None, False)
    assert (lista["total_mensal"], lista["total_anual"]) == (Decimal("39.9"), Decimal("478.8"))
    # Campo interno não vaza (o chat do Detetive manda o item inteiro ao modelo).
    assert set(it) == set(Assinatura.model_fields)


def test_conta_bank_sai_como_conta(user_id):
    txs = mensais("sp", [-21.9] * 3, ultima=date(2026, 9, 12))
    semeia(user_id, [conta("acc-1", txs, numero="12345-6")], [rp("Spotify P0AB12CD", -21.9, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert it["meio"] == {"tipo": "conta", "nome": "Conta", "final": None}


# ── 5. Reajuste ──────────────────────────────────────────────────────────────

def test_total_soma_exata_na_escala_gravada(user_id):
    # `str`, não `Decimal`: Decimal("0.3") == Decimal("0.30").
    dez_e_vinte_centavos(user_id)
    lista = listar_assinaturas(user_id, HOJE)
    assert (str(lista["total_mensal"]), str(lista["total_anual"])) == ("0.30", "3.60")


def test_reajuste_vira_um_item_com_valor_anterior(user_id):
    txs = mensais("nf", [-39.9, -39.9, -44.9], desc="NETFLIX.COM")
    semeia(user_id, [conta("acc-1", txs)], [rp("NETFLIX.COM", -41.57, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert (it["valor"], it["valor_anterior"], it["reajuste_em"]) == (Decimal("44.9"), Decimal("39.9"), "2026-09-05")


def test_reajuste_partido_em_dois_grupos_vira_um_item(user_id):
    velho = mensais("a", [-39.9] * 3, ultima=date(2026, 6, 5), desc="NETFLIX.COM")
    novo = mensais("b", [-59.9] * 3, desc="NETFLIX.COM")
    semeia(user_id, [conta("acc-1", velho + novo)],
           [rp("NETFLIX.COM", -39.9, velho), rp("NETFLIX.COM", -59.9, novo)])
    lista = listar_assinaturas(user_id, HOJE)
    (it,) = lista["servicos"]
    assert (it["valor"], it["valor_anterior"], it["meses"], lista["total_mensal"]) == (Decimal("59.9"), Decimal("39.9"), 6, Decimal("59.9"))


def test_meses_conta_meses_distintos(user_id):
    txs = [tx("m1", -39.9, date(2026, 8, 5)), tx("m2", -39.9, date(2026, 8, 20)),
           tx("m3", -39.9, date(2026, 9, 5))]
    semeia(user_id, [conta("acc-1", txs)], [rp("NETFLIX.COM", -39.9, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert it["meses"] == 2


def test_descricao_sem_chave_fica_fora(user_id):
    lixo = mensais("l", [-19.9] * 3)
    nf = mensais("nf", [-39.9] * 3, ultima=date(2026, 9, 6))
    semeia(user_id, [conta("acc-1", lixo + nf)], [rp("*** ---", -19.9, lixo), rp("NETFLIX.COM", -39.9, nf)])
    lista = listar_assinaturas(user_id, HOJE)
    assert ([x["chave"] for x in lista["servicos"] + lista["outras"]], lista["chaves"]) == (["netflix"], ["netflix"])


def test_tipo_inesperado_da_pluggy_nao_derruba_a_lista(user_id):
    txs = mensais("d", [-29.9] * 3, merchant={"name": 123, "category": ["x"]}, cc=["lixo"])
    semeia(user_id, [conta("acc-1", txs)], [rp("NETFLIX.COM", -29.9, txs)])
    lista = listar_assinaturas(user_id, HOJE)
    assert lista["servicos"][0]["nome"] == "NETFLIX.COM"
    Assinaturas(**lista)  # o response_model aceita


def test_parcela_so_numa_ocorrencia_do_meio_tira_o_grupo(user_id):
    txs = mensais("p", [-80] * 3, desc="LOJA Y")
    txs[1]["raw"]["creditCardMetadata"] = {"installmentNumber": 2, "totalInstallments": 5}
    semeia(user_id, [conta("acc-1", txs)], [rp("LOJA Y", -80, txs)])
    lista = listar_assinaturas(user_id, HOJE)
    assert (lista["servicos"], lista["outras"], lista["chaves"]) == ([], [], [])


# Descarte por grupo da Pluggy, antes de juntar por chave: o grupo vizinho com o
# mesmo descritor não derruba o legítimo.
def test_parcela_com_o_mesmo_descritor_nao_derruba_a_assinatura(user_id):
    icloud = mensais("ic", [-14.9] * 3, desc="APPLE.COM/BILL")
    parc = [tx(f"ap-{i}", -499, date(2026, m, 15), desc="APPLE.COM/BILL",
               cc={"installmentNumber": i, "totalInstallments": 10}) for i, m in ((1, 7), (2, 8), (3, 9))]
    semeia(user_id, [conta("acc-1", icloud + parc, tipo="CREDIT")],
           [rp("APPLE.COM/BILL", -14.9, icloud), rp("APPLE.COM/BILL", -499, parc)])
    lista = listar_assinaturas(user_id, HOJE)
    assert [(x["chave"], x["valor"], x["valor_anterior"]) for x in lista["servicos"]] == [
        ("apple com bill", Decimal("14.9"), None)]
    assert lista["outras"] == []


def test_movimento_interno_com_a_mesma_chave_nao_derruba_o_legitimo(user_id):
    ok = mensais("ok", [-29.9] * 3)
    fatura = mensais("ft", [-800] * 3, ultima=date(2026, 9, 20), category="Credit card payment")
    semeia(user_id, [conta("acc-1", ok + fatura)], [rp("NUBANK", -29.9, ok), rp("NUBANK", -800, fatura)])
    lista = listar_assinaturas(user_id, HOJE)
    assert [(x["chave"], x["valor"]) for x in lista["servicos"] + lista["outras"]] == [("nubank", Decimal("29.9"))]


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
def test_conexao_pausada_ou_apagada_fica_fora(user_id, status):
    cid = netflix_no_cartao(user_id)
    _q("update open_finance_connections set status=%s where id=%s", status, cid)
    assert listar_assinaturas(user_id, HOJE)["servicos"] == []


@pytest.mark.parametrize("dias,status", [(40, "ativa"), (41, "possivelmente_cancelada")])
def test_fronteira_dos_40_dias(user_id, dias, status):
    txs = mensais("f", [-25] * 3, ultima=HOJE - timedelta(days=dias))
    semeia(user_id, [conta("acc-1", txs)], [rp("Spotify", -25, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert it["status"] == status


# ── 6. Receita fica fora ─────────────────────────────────────────────────────

def test_receita_nao_aparece(user_id):
    sal = mensais("sal", [5000] * 3)
    nf = mensais("nf", [-39.9] * 3, ultima=date(2026, 9, 6))
    semeia(user_id, [conta("acc-1", sal + nf)],
           [rp("Salario ACME", 5000, sal), rp("NETFLIX.COM", -39.9, nf)])
    lista = listar_assinaturas(user_id, HOJE)
    assert [x["chave"] for x in lista["servicos"] + lista["outras"]] == ["netflix"]


# ── 7. Exclusões ─────────────────────────────────────────────────────────────

def test_parcela_e_movimento_interno_saem_e_pix_vai_para_outras(user_id):
    parc = [tx(f"pc-{i}", -100, date(2026, m, 10), cc={"installmentNumber": i, "totalInstallments": 10})
            for i, m in ((1, 7), (2, 8), (3, 9))]
    fatura = mensais("ft", [-800] * 3, category="Credit card payment")
    mesma = mensais("sp", [-300] * 3, category="Same person transfer")
    pix = mensais("px", [-150] * 3, category="Transfer - PIX")
    semeia(user_id, [conta("acc-1", parc + fatura + mesma + pix)],
           [rp("LOJA X PARC", -100, parc), rp("PAGAMENTO FATURA", -800, fatura),
            rp("TRANSF MESMA TITULARIDADE", -300, mesma), rp("PIX MARIA", -150, pix)])
    lista = listar_assinaturas(user_id, HOJE)
    assert lista["servicos"] == []
    assert [(x["chave"], x["categoria"]) for x in lista["outras"]] == [("pix maria", "transferências")]


# ── 8. Status com data fixa ──────────────────────────────────────────────────

def test_status_pela_ultima_cobranca(user_id):
    velha = mensais("v", [-55] * 3, ultima=date(2026, 3, 15), desc="x")
    nova = mensais("n", [-25] * 3, ultima=date(2026, 9, 11))
    semeia(user_id, [conta("acc-1", velha + nova)],
           [rp("NETFLIX.COM", -55, velha), rp("Spotify", -25, nova)])
    lista = listar_assinaturas(user_id, HOJE)
    assert {x["chave"]: x["status"] for x in lista["servicos"]} == {
        "netflix": "possivelmente_cancelada", "spotify": "ativa"}
    assert lista["total_mensal"] == 25.0


# ── 9. Descrições reais ──────────────────────────────────────────────────────
# Controle (medido): sem o passo 5 em `utils_text.CATEGORY_KEYWORDS`, YouTubePremium
# e APPLE.COM/BILL caem em "outras".

@pytest.mark.parametrize("desc", ["NETFLIX.COM", "PG *NETFLIX", "GOOGLE *YouTubePremium",
                                  "Google One", "APPLE.COM/BILL", "Spotify P0AB12CD"])
def test_descricao_real_cai_em_servicos(user_id, desc):
    txs = mensais("d", [-19.9] * 3)
    semeia(user_id, [conta("acc-1", txs)], [rp(desc, -19.9, txs)])
    lista = listar_assinaturas(user_id, HOJE)
    assert (len(lista["servicos"]), lista["outras"]) == (1, [])


@pytest.mark.parametrize("desc,cat", [("claro flex", "Telecommunications"),
                                      ("clube latam pass matri", "Mileage programs")])
def test_recorrente_que_nao_e_servico_vai_para_outras(user_id, desc, cat):
    txs = mensais("d", [-49.9] * 3, category=cat)
    semeia(user_id, [conta("acc-1", txs)], [rp(desc, -49.9, txs)])
    lista = listar_assinaturas(user_id, HOJE)
    assert (lista["servicos"], len(lista["outras"])) == ([], 1)


def test_merchant_category_de_streaming_e_servico(user_id):
    txs = mensais("d", [-29.9] * 3, merchant={"name": "Xpto Midia", "category": "Video Streaming"})
    semeia(user_id, [conta("acc-1", txs)], [rp("XPTO MIDIA LTDA", -29.9, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert it["nome"] == "Xpto Midia"


# ── 13. Migração do flag de silêncio ─────────────────────────────────────────

def test_migracao_antigas_true_novas_false(user_id):
    db.ensure_user(user_id)
    ins = ("insert into open_finance_connections (user_id, provider, provider_item_id, status,"
           " institution_id, institution_name) values (%s, 'pluggy', %s, 'UPDATED', 'i', 'B') returning id")
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute("alter table open_finance_connections drop column recurring_seed_silent")
            cur.execute(ins, (user_id, "mig-velha"))
            velha = cur.fetchone()["id"]
            cur.execute(RECURRING_SEED_SILENT_SQL)
            cur.execute(ins, (user_id, "mig-nova"))
            nova = cur.fetchone()["id"]
            cur.execute("select id, recurring_seed_silent s from open_finance_connections"
                        " where id = any(%s)", ([velha, nova],))
            assert {r["id"]: r["s"] for r in cur.fetchall()} == {velha: True, nova: False}
        finally:
            conn.rollback()
