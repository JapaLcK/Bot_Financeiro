"""A saga do checkout Pix (§7, §9, §10) — com estado REAL no banco.

O que é mockado é só o MUNDO LÁ FORA (Asaas e Stripe), porque é ele que se
conta. `pix_charges`, `plan_grants` e `auth_accounts` são de verdade: a classe de
bug que mais aparece neste repositório é a do estado que outro fluxo deixou no
banco (CLAUDE.md §3), e um `db` mockado é cego para ela.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * troque a ordem de `_cancelar_remota` (criar antes de deletar, que é a v4 do
    plano) → `test_substituicao_deleta_no_asaas_antes_de_criar` VERMELHO na
    ordem das chamadas, e é o furo financeiro: dois QRs pagáveis do mesmo dono,
    duas cobranças precificadas contra o MESMO crédito;
  * faça o `DELETE` que falha ser engolido → `test_delete_que_falha_nao_cria_nada`
    VERMELHO com duas cobranças ativas;
  * devolva 409 no caso "mesmo plano" (em vez do mesmo QR) →
    `test_mesma_cobranca_ativa_do_mesmo_plano_devolve_o_mesmo_qr` VERMELHO;
  * dê default a `ASAAS_MIN_CHARGE_CENTS` →
    `test_env_de_dinheiro_ausente_recusa_a_venda` VERMELHO, e é o caso em que um
    número inventado vira cobrança de verdade;
  * devolva a leitura da env para `if not bruto.isdigit() or int(bruto) <= 0` →
    `test_env_de_dinheiro_malformada_recusa_a_venda` VERMELHO em `²` (o `int()`
    estoura, 500 no lugar do 503) e em `٥٠٠` (`int("٥٠٠") == 500` e a venda SAI, a
    500 centavos que ninguém digitou). `-5` e `abc` seguem verdes com e sem;
  * apague a guarda de `grandfathered` de `criar_checkout` →
    `test_vitalicio_nao_compra_o_anual` VERMELHO, com a cobrança criada e o Asaas
    chamado — o dinheiro entrando por acesso que o cliente já tem.

POSITIVO do grupo: `test_venda_nova_emite_pending_com_o_qr_cifrado`. Sem ele o
grupo passaria num checkout que **recusa tudo**, que é pior que o bug.
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 - `asaas_falso`/`vendavel` são fixtures
    PRECO,
    _comprar,
    _linhas,
    _marcar_paga,
    asaas_falso,
    vendavel,
)
from core.services.pix_checkout import (
    CheckoutIndisponivel,
    Vitalicio,
    pix_annual_available,
)
from core.services.pix_pricing import CoberturaJaPaga
from db.connection import get_conn

# ── o caminho legítimo, que é o POSITIVO do grupo ────────────────────────────

def test_venda_nova_emite_pending_com_o_qr_cifrado(user_id, vendavel, asaas_falso):
    """POSITIVO. A saga inteira: `draft` → `creating` → POST → `pending`.

    O QR fica CIFRADO em repouso (§13.6) — a asserção é sobre a coluna crua, não
    sobre o que a função devolveu, porque é a coluna que um dump expõe.
    """
    conta(user_id, "free", None)
    r = _comprar(user_id)

    assert asaas_falso["ordem"] == ["customer", "create", "qr"]
    assert asaas_falso["cpf_visto"] == "12345678901", "o CPF não chegou ao Asaas"
    linha = _linhas(user_id)[0]
    assert linha["status"] == "pending"
    assert linha["asaas_payment_id"] == f"pay_{asaas_falso['marca']}_1"
    assert linha["amount_cents"] == PRECO and linha["credit_cents"] == 0
    assert linha["external_reference"] == f"pix:{linha['id']}"
    assert linha["qr_payload_enc"] and "000201" not in linha["qr_payload_enc"], (
        "o QR está legível na coluna — instrumento ao portador esperando um dump"
    )
    assert r["qr_payload"] == f"000201-pay_{asaas_falso['marca']}_1"
    assert r["qr_image"].startswith("data:image/svg+xml;base64,")
    assert r["public_token"] == linha["public_token"]
    assert set(r) == {"public_token", "qr_payload", "qr_image", "expires_at",
                      "amount_cents", "credit_cents", "starts_at", "agendada",
                      "plan"}, (
        "o contrato que a tela do PR 2 consome mudou de forma"
    )
    # O PAR que prova por que `agendada` teve de existir: nesta compra — conta
    # `free`, sem plano nenhum — o acesso é IMEDIATO e mesmo assim `starts_at`
    # vem preenchido (com `agora`). A tela gatilhava pela presença da data e
    # dizia "começa em <hoje>, assim que o plano atual terminar" a quem já tinha
    # acesso e nunca teve plano.
    assert r["starts_at"] is not None, "o contrato perdeu a data do começo"
    assert r["agendada"] is False, (
        f"compra imediata marcada como agendada: starts_at={r['starts_at']}"
    )


def test_o_cpf_nao_e_persistido_em_lugar_nenhum(user_id, vendavel, asaas_falso):
    """Decisão fechada do dono: `cpfCnpj` obrigatório e **não persistido**.

    Varre a linha inteira, coluna a coluna, em vez de conferir as que eu lembrei
    de olhar — uma coluna nova guardando o documento passaria numa lista fixa.
    """
    conta(user_id, "free", None)
    _comprar(user_id)
    linha = _linhas(user_id)[0]
    assert not [c for c, v in linha.items() if "12345678901" in str(v)]


# ── as recusas: 503 sem número inventado, 409 sem reconsultar o banco ────────

def test_env_de_dinheiro_ausente_recusa_a_venda(user_id, vendavel, asaas_falso,
                                                monkeypatch):
    """DISCRIMINA. Número de dinheiro que o dono não fixou não ganha default.

    Uma env só desde 2026-09-09 — o PREÇO virou constante e não pode faltar. O
    mínimo do Asaas continua vindo de env porque o dono não o fixou.

    A asserção mais importante é a segunda: **nada foi escrito e nada foi
    chamado**. Uma recusa que já criou o `draft` deixa o índice parcial ocupado e
    o cliente não consegue comprar nem depois de a env aparecer.
    """
    conta(user_id, "free", None)
    monkeypatch.delenv("ASAAS_MIN_CHARGE_CENTS")
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id)
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


@pytest.mark.parametrize("bruto", ["²", "٥٠٠", "-5", "abc"])
def test_env_de_dinheiro_malformada_recusa_a_venda(user_id, vendavel, asaas_falso,
                                                   monkeypatch, bruto):
    """DISCRIMINA. Env MALFORMADA é o mesmo estado da ausente: recusa, não default.

    Os dois primeiros casos são os que mordiam. `"²"` tem `isdigit()` True e
    `int()` que estoura — `ValueError` cru saindo do contrato do módulo. `"٥٠٠"`
    é pior porque é silencioso: `int("٥٠٠") == 500` (medido), então a venda ia
    até o fim com um mínimo que ninguém digitou. `-5` e `abc` já eram recusados
    com e sem o conserto, e estão aqui como a moldura da categoria.
    """
    conta(user_id, "free", None)
    monkeypatch.setenv("ASAAS_MIN_CHARGE_CENTS", bruto)
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id)
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


def test_plano_fora_da_tabela_de_precos_recusa_a_venda(user_id, vendavel,
                                                       asaas_falso):
    """O par da constante: preço que não existe continua sendo 503, não um
    número chutado. `free` está em `_STORED_PLAN_TO_TIER` e **não** em
    `PRECOS_ANUAIS_CENTS` — é exatamente o caso que o `dict.get` tem de recusar.
    """
    conta(user_id, "free", None)
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id, plano="free")
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


def test_vitalicio_nao_compra_o_anual(user_id, vendavel, asaas_falso):
    """Decisão do dono (2026-09-09): `grandfathered` **não** compra o Pix anual.

    O modo de falha que ela fecha não é teórico — é dinheiro entrando sem nada
    em troca: `recompute_entitlement` sai cedo para `grandfathered`
    (`core/services/billing_access.py:358`), então a venda ia até o fim, a
    cobrança virava `paid`, o grant nascia e **o acesso não mudava**.

    As duas asserções finais são o ponto: **nada escrito, nada chamado**. Uma
    recusa que já criou o `draft` deixaria o índice parcial ocupado.
    """
    # O estado REAL do vitalício: plano pago e `plan_expires_at` NULL. O par
    # positivo abaixo usa exatamente o mesmo, trocando SÓ o
    # `last_payment_status` — assim o que discrimina é a guarda, e não o plano.
    conta(user_id, "pro_max", None, "grandfathered")
    with pytest.raises(Vitalicio) as capturado:
        _comprar(user_id)
    assert capturado.value.ERRO == "lifetime", "o front lê este código"
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


def test_quem_nao_e_vitalicio_continua_comprando(user_id, vendavel, asaas_falso):
    """POSITIVO do par. Sem ele a guarda poderia recusar TODO mundo — que é pior
    que o bug, porque derruba a venda inteira em vez de uma conta.

    Mesmo plano e mesmo `plan_expires_at` do teste acima; muda só o
    `last_payment_status`."""
    conta(user_id, "pro_max", None, "active")
    _comprar(user_id)
    assert len(_linhas(user_id)) == 1


def test_flag_desligada_nao_vende(user_id, vendavel, asaas_falso, monkeypatch):
    """A flag do §14: o PR sobe com ela em 0 e nenhuma venda sai."""
    conta(user_id, "free", None)
    monkeypatch.setenv("ASAAS_PIX_ANNUAL_ENABLED", "0")
    assert pix_annual_available() is False
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id)
    assert _linhas(user_id) == []


def test_cobertura_ja_paga_recusa_sem_escrever(user_id, vendavel, asaas_falso):
    """O 409 do §7 sobe INTACTO do `pix_pricing`, e antes de qualquer escrita.

    `CoberturaJaPaga` carrega plano e data para a rota não reconsultar o banco —
    o resultado da reconsulta poderia divergir do que motivou a recusa.

    O cenário é o que o dono MEDIU (§7): já existe um grant Pix **futuro** de
    Pro, pago, e uma segunda compra de Pro daria 365 dias sobrepostos, 0 dias
    novos e R$ 499,00 cobrados. Os grants apontam para cobranças REAIS, porque o
    `amount_cents` que `plano_da_cobranca` exige vem do join com `pix_charges`.
    """
    conta(user_id, "free", None)
    plus = _comprar(user_id, "pro")
    _marcar_paga(user_id, plus["public_token"], dias=-1)      # vigente, tier Plus
    pro = _comprar(user_id, "pro_max")
    _marcar_paga(user_id, pro["public_token"], dias=300)      # FUTURO, já pago
    antes = len(_linhas(user_id))

    with pytest.raises(CoberturaJaPaga) as capturado:
        _comprar(user_id, "pro_max")
    assert capturado.value.plano == "pro_max"
    assert capturado.value.cobertura_ate is not None
    assert len(_linhas(user_id)) == antes, "a recusa criou cobrança"


def test_grant_orfao_nao_vira_credito_zero(user_id, vendavel, asaas_falso):
    """DISCRIMINA a regra do §7: **"não sei" nunca vira um valor que decide
    dinheiro.** Grant `pix` cuja cobrança não existe mais não pode ser tratado
    como crédito 0 — isso cobraria o preço CHEIO em silêncio.

    *Negativo: troque o `raise` de `_exigir_amount_cents` por `amount_cents = 0`
    → este teste fica verde emitindo cobrança, e é a cobrança cheia por cima de
    um crédito legítimo.*

    `ponytail:` TETO — hoje isso é inalcançável em produção (nenhuma varredura
    apaga cobrança com grant vivo; a de retenção ficou na issue #329), e por isso
    o desfecho é `ValueError` → 500. Quando a #329 entrar, apagar cobrança paga
    de grant ativo trava o checkout daquele usuário para sempre; o conserto é a
    retenção não apagar linha com grant `active`, não engolir o erro aqui.
    """
    from datetime import datetime, timedelta, timezone

    from db.plan_grants import upsert_grant

    conta(user_id, "free", None)
    inicio = datetime.now(timezone.utc) - timedelta(days=10)
    upsert_grant(user_id, "pix", "999999999", "pro_max", inicio,
                 inicio + timedelta(days=365), 1, last_event_id="e1")
    with pytest.raises(ValueError, match="amount_cents"):
        _comprar(user_id)
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


# ── rastreio publicitário: escritor e purga no mesmo PR (§6 do plano) ───────

def test_rastreio_e_gravado_e_a_exclusao_o_zera(user_id, vendavel, asaas_falso):
    """As três colunas nascem com ESCRITOR e PURGA (decisão do dono, 2026-09-07).

    Um teste só para as duas metades de propósito: coluna de rastreio com
    escritor e sem purga é dado publicitário que sobrevive à exclusão da conta, e
    separar as asserções deixaria essa metade passar sozinha.
    """
    from db.privacy import delete_user_data

    conta(user_id, "free", None)
    _comprar(user_id, rastreio={"ga_client_id": "GA1.1.9", "fbp": "fb.1.2.3",
                                "fbc": "fb.1.2.abc"})
    linha = _linhas(user_id)[0]
    assert (linha["ga_client_id"], linha["fbp"], linha["fbc"]) == (
        "GA1.1.9", "fb.1.2.3", "fb.1.2.abc")

    delete_user_data(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_charges where id = %s", (linha["id"],))
        depois = dict(cur.fetchone())
    assert depois["user_id"] is None
    assert (depois["ga_client_id"], depois["fbp"], depois["fbc"]) == (None, None, None)
    assert depois["qr_payload_enc"] is None
    assert depois["amount_cents"] == PRECO, "o valor financeiro não podia sumir"


def test_agendada_sai_da_data_e_nao_da_presenca_dela():
    """O OUTRO lado do par acima, sem banco: a `resposta()` é função pura.

    `test_venda_nova_emite_pending_com_o_qr_cifrado` prova o imediato
    (`agendada is False` com `starts_at` preenchido). Sem este, `agendada`
    poderia ser a constante `False` e aquele caso continuaria verde — a compra
    agendada é a que carrega a promessa de data para a tela.
    """
    from datetime import datetime, timedelta, timezone

    from core.services.pix_checkout_resposta import resposta

    base = {"public_token": "tok", "qr_expires_at": None, "amount_cents": 19900,
            "credit_cents": 0, "plan": "pro", "user_id": 1}
    agora = datetime.now(timezone.utc)
    futuro = agora + timedelta(days=40)

    assert resposta(dict(base, access_starts_at=futuro), "000201")["agendada"] is True
    assert resposta(dict(base, access_starts_at=agora), "000201")["agendada"] is False
    assert resposta(dict(base, access_starts_at=None), "000201")["agendada"] is False
