"""
core/services/pix_checkout.py — a saga de emitir (e substituir) a cobrança Pix.

Aqui mora TUDO o que decide dinheiro na venda: a flag, o preço, o crédito, a recusa,
o cancelamento remoto e a ordem dos passos. `frontend/routes/billing_pix.py` é fino de
propósito — ele valida a entrada, traduz exceção em status HTTP e não sabe o que é saga.

Plano: docs/plano_pix_anual_asaas.md §7, §9, §10 e §13.6.

## A ordem dos passos, e por que ela não é negociável (§10)

    draft  →  creating  →  POST no Asaas  →  pending

Cada seta é um commit local. O mundo remoto NÃO é transacional, então a
varredura (`core/services/pix_sweeps.py`) é quem fecha o que morrer no meio —
`creating` é o estado ambíguo por definição e **nunca** é apagado por relógio.

**A substituição cancela no Asaas ANTES de criar a nova** (§10, correção nº 6), igual
ao `Session.expire` do caminho do Stripe. Falhando o `DELETE`, é 503 e **nada é criado**:
dois QRs pagáveis do mesmo usuário seriam duas cobranças contra o mesmo crédito.

## O caso "fechei a aba e voltei" — 200 com o MESMO QR

Decisão do dono, 2026-09-09: cobrança ativa **do mesmo plano** devolve o mesmo
QR, com 200, e não 409. Só plano DIFERENTE — ou QR já VENCIDO — substitui. É por
isso que `buscar_ativa` traz o `qr_payload_enc` junto, decifrado por
`pix_checkout_resposta.qr_da_linha` com `purpose="pix_qr_read"` (§13.6): é o
único caminho do repositório que tira o instrumento de pagamento do banco.

## Os dois números de dinheiro vêm de lugares OPOSTOS

`ASAAS_MIN_CHARGE_CENTS` é **env sem default** (ausente → 503): o dono não o fixou. O
**PREÇO** é o contrário — constante em `pix_pricing.PRECOS_ANUAIS_CENTS`, porque env
seria a terceira fonte do mesmo número, sem nada comparando as três.
"""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta, timezone

from core.crypto import encrypt_pii_optional
from core.observability import log_system_event_sync
from core.services import asaas
from core.services.pix_checkout_resposta import (
    VENCIMENTO_DIAS, expira, qr_da_linha, resposta)
from core.services.pix_pricing import (  # noqa: I001
    DURACAO_DIAS, PRECOS_ANUAIS_CENTS, CoberturaJaPaga, plano_da_cobranca)

# Estados de onde a substituição consegue partir. `paid` e os terminais não
# estão aqui: cobrança paga não se substitui, se renova (§11).
_SUBSTITUIVEIS = ("draft", "creating", "pending", "canceling")


class CheckoutIndisponivel(RuntimeError):
    """Venda impossível AGORA, sem culpa do cliente → **503**.

    Uma classe com `codigo`, e não uma classe por motivo: quem chama devolve o
    mesmo 503 para flag desligada, env faltando e Asaas fora — a diferença
    interessa ao log, não ao cliente.
    """

    def __init__(self, codigo: str, mensagem: str | None = None):
        super().__init__(mensagem or codigo)
        self.codigo = codigo


class StripeAtivo(RuntimeError):
    """Assinatura no cartão viva e sem `confirm_cancel_stripe` → **409** (§9).

    Carrega o `current_period_end` porque o modal do PR 2 mostra a data, e
    porque reconsultar o Stripe na tradução do erro poderia devolver outra coisa
    (mesma regra de `CoberturaJaPaga`, que também não deixa ninguém reconsultar).
    """

    ERRO = "stripe_active"

    def __init__(self, current_period_end):
        super().__init__("assinatura ativa no Stripe")
        self.current_period_end = current_period_end


class Vitalicio(RuntimeError):
    """Vitalício de brinde (`grandfathered`) → **409** (dono, 2026-09-09).

    Sem esta recusa a venda ia até o fim e o acesso NÃO mudava:
    `recompute_entitlement` sai cedo para esse status (`billing_access.py:358`).
    Cliente pagava por acesso que já tem. Mesmo `error` que o caminho do Stripe
    já devolve (`_billing_checkout_for_user`).
    """

    ERRO = "lifetime"


def pix_annual_available() -> bool:
    """A flag de VENDA. Lida AQUI e em nenhum outro lugar (§5.3 do plano).

    O monólito chama esta função em vez de ler `ASAAS_PIX_ANNUAL_ENABLED`
    direto, e não é estilo: `tests/test_pix_destino_inerte.py` proíbe a marca
    `ASAAS_` fora dos módulos do Pix, então ler a env no `plans-config`
    derrubaria o portão. Ele está fazendo trabalho real ali.

    **Não é consultada no webhook** (§8.1): cobrança já emitida continua sendo
    paga e concedida com a flag desligada — caso 56 do §16.
    """
    return (os.getenv("ASAAS_PIX_ANNUAL_ENABLED") or "").strip() in ("1", "true", "True")


def _grants_para_precificar(user_id: int) -> list[dict]:
    """Os grants ativos do usuário **com `amount_cents`** — o "join" que
    `plano_da_cobranca` exige do chamador, e sem o qual todo crédito vira 0. Em
    Python porque as duas metades filtram por `user_id` cada uma (§0).
    """
    from db.pix_charges_saga import valores_por_cobranca
    from db.plan_grants import list_grants

    valores = valores_por_cobranca(user_id)
    ativos = []
    for g in list_grants(user_id):
        if g["status"] != "active":
            continue
        item = dict(g)
        item["amount_cents"] = (valores.get(str(g["external_ref"]))
                                if g["source"] == "pix" else None)
        ativos.append(item)
    return ativos


def _stripe_vivo(user_id: int) -> tuple[str, datetime] | None:
    """`(subscription_id, fim do período pago)` se há assinatura no cartão.

    Reusa os helpers do monólito, como `core/services/billing_access.py` já faz
    e pelo mesmo motivo (§0.1). Import tardio porque o monólito importa o router
    que importa este módulo.

    `ponytail:` este é o SEGUNDO consumidor, e o `ponytail:` de `billing_access`
    dizia que aí o upgrade é extrair para `core/services/stripe_lookup.py`. Não
    extraí: `_sg` tem dezenas de usos no monólito e sairia junto — vira
    refatoração do arquivo de 8 mil linhas dentro de um PR de venda.

    Falha de consulta **levanta**, nunca "não tem": vender Pix por cima de um
    cartão vivo é cobrança dupla, e o lado seguro do "não sei" é não vender.
    """
    from db import get_auth_user

    conta = get_auth_user(user_id) or {}
    customer = (conta.get("stripe_customer_id") or "").strip()
    if not customer:
        return None
    import stripe as _stripe
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        _find_active_subscription, _sg, _sub_period_end_ts,
    )
    try:
        sub = _find_active_subscription(_stripe, customer)
    except Exception as exc:  # noqa: BLE001 — indisponibilidade não é ausência
        raise CheckoutIndisponivel("stripe_indisponivel") from exc
    if sub is None:
        return None
    ts = _sub_period_end_ts(sub)
    fim = (datetime.fromtimestamp(ts, tz=timezone.utc) if ts
           else datetime.now(timezone.utc))
    return (str(_sg(sub, "id") or ""), fim)


def _cancelar_remota(linha: dict) -> None:
    """`canceling` → `DELETE` no Asaas → `canceled`. Falhou, ninguém cria nada.

    Marcar `canceling` ANTES do `DELETE` (§10) deixa rastro para a varredura
    repetir o cancelamento se o processo morrer no meio.

    **Coluna nula não é prova de que não há cobrança lá.** Em `creating` o POST
    pode ter efetivado com a resposta (ou o QR) perdida: `id_remoto_vivo`
    pergunta ao Asaas pela `external_reference` antes de dar a linha por
    fantasma. Sem isso a substituição pulava o `DELETE` e deixava duas cobranças
    pagáveis contra o mesmo crédito — o furo que o §10 existe para fechar, e o
    `PAYMENT_RECEIVED` aceita `canceled` como origem (§11).
    """
    from core.services.pix_sweeps import id_remoto_vivo
    from db.pix_charges import transicionar

    transicionar(linha["id"], de=_SUBSTITUIVEIS, para="canceling")
    try:
        pagamento = linha["asaas_payment_id"] or (
            id_remoto_vivo(linha["external_reference"])
            if linha["status"] == "creating" else None)
        if pagamento:
            asaas.deletar_pagamento(pagamento)
    except Exception as exc:  # noqa: BLE001 — consulta que falha é 503 também
        raise CheckoutIndisponivel("asaas_cancelamento_falhou") from exc
    transicionar(linha["id"], de=("canceling",), para="canceled", apagar_qr=True)


def criar_checkout(user_id: int, *, plan_stored: str, cpf_cnpj: str, nome: str,
                   email: str | None = None, rastreio: dict[str, str] | None = None,
                   confirm_cancel_stripe: bool = False) -> dict:
    """Emite (ou reaproveita) a cobrança Pix anual. **Roda sob lock do usuário.**

    Levanta `CheckoutIndisponivel` (503), `Vitalicio`, `StripeAtivo` e
    `CoberturaJaPaga` (409) — esses quatro decidem ANTES de escrever qualquer
    coisa. A quinta é `TitularRecusado` (400, de `asaas_customers`): ela sai do
    meio da saga e **deixa a linha em `draft`** (ver `_emitir`). O `cpf_cnpj`
    atravessa sem tocar em disco: vai para `criar_cliente` e morre lá."""
    if not pix_annual_available():
        raise CheckoutIndisponivel("pix_annual_desligado")
    # DEPOIS da env: com a flag em 0 o vitalício leva 503 `pix_annual_desligado`, não
    # este 409. E aqui, não no router — dinheiro, e o guarda vai onde os chamadores
    # passam. O `.lower()` é só nosso: o gêmeo do Stripe
    # (`finance_bot_websocket_custom.py:4189`) compara CRU — registro, não conserto.
    from db import get_auth_user  # noqa: PLC0415
    if ((get_auth_user(user_id) or {}).get("last_payment_status") or "").lower() == "grandfathered":
        raise Vitalicio()
    # **Sem default, nunca** — ver o topo. Inline: virou a única leitura de env
    # de dinheiro do módulo quando o preço deixou de ser env (§0.2).
    bruto = (os.getenv("ASAAS_MIN_CHARGE_CENTS") or "").strip()
    if not (bruto.isascii() and bruto.isdigit()) or int(bruto) <= 0:
        raise CheckoutIndisponivel("asaas_min_charge_nao_configurado")
    min_cents = int(bruto)
    # `None` = plano fora da tabela (`free`, ou lixo que passou pela rota) → 503.
    preco = PRECOS_ANUAIS_CENTS.get(plan_stored)
    if preco is None:
        raise CheckoutIndisponivel("preco_anual_nao_configurado")

    stripe_sub = _stripe_vivo(user_id)
    if stripe_sub and not confirm_cancel_stripe:
        raise StripeAtivo(stripe_sub[1])

    # A recusa do §7 (`CoberturaJaPaga`) sai daqui, de uma função PURA, antes de
    # qualquer escrita e antes de qualquer chamada ao Asaas.
    venda = plano_da_cobranca(_grants_para_precificar(user_id), plan_stored,
                                preco, min_cents)

    linha = _criar_ou_substituir(user_id, plan_stored, venda, stripe_sub, rastreio)
    # `access_starts_at` da LINHA só é escrito no pagamento (§7), então ela o traz
    # nulo aqui — quem já o calculou é `plano_da_cobranca`, e é esse valor que a
    # tela mostra. Sem passá-lo, `starts_at` saía nulo em todo checkout.
    if linha is None:  # reaproveitou a cobrança que já existia
        return _reaproveitar(user_id, plan_stored, venda["access_starts_at"])
    return _emitir(dict(linha, access_starts_at=venda["access_starts_at"]),
                   cpf_cnpj, nome, email)


def _criar_ou_substituir(user_id, plan_stored, venda, stripe_sub, rastreio):
    """Insere o `draft`, substituindo a ativa quando o plano MUDA. Devolve a
    linha nova, ou **`None`** quando a que já existe é do mesmo plano — aí quem
    chama devolve o mesmo QR (decisão do dono)."""
    from db.pix_charges import criar_cobranca
    from db.pix_charges_saga import buscar_ativa

    def _inserir():
        return criar_cobranca(
            user_id, public_token=secrets.token_urlsafe(16), plan=plan_stored,
            plan_stored=venda["plan_stored"], price_cents=venda["price_cents"],
            credit_cents=venda["credit_cents"], amount_cents=venda["amount_cents"],
            duration_days=DURACAO_DIAS,
            stripe_subscription_id=stripe_sub[0] if stripe_sub else None,
            stripe_period_end_at=stripe_sub[1] if stripe_sub else None,
            rastreio=rastreio)

    linha = _inserir()
    if linha is not None:
        return linha

    ativa = buscar_ativa(user_id)
    if ativa is None:
        # Perdeu o `on conflict` e a cobrança sumiu entre os dois comandos
        # (varredura apagou um `draft`). Uma retentativa resolve; inventar um
        # segundo caminho aqui seria estado a mais para o mesmo desfecho.
        raise CheckoutIndisponivel("cobranca_ativa_sumiu")
    # QR VENCIDO não se reaproveita: o `OVERDUE` do Asaas pode atrasar ou se
    # perder, e `pending` não volta para a reconciliação — devolver o mesmo
    # código expirado prendia o cliente sem cobrança pagável para sempre.
    vencido = (ativa["qr_expires_at"] is not None
               and ativa["qr_expires_at"] <= datetime.now(timezone.utc))
    if ativa["plan"] == plan_stored and ativa["status"] == "pending" and not vencido:
        return None
    _cancelar_remota(ativa)
    nova = _inserir()
    if nova is None:
        raise CheckoutIndisponivel("cobranca_ativa_persistiu")
    return nova


def _reaproveitar(user_id: int, plan_stored: str, starts_at) -> dict:
    """O mesmo QR, com 200. Relê a linha porque `_cancelar_remota` não rodou."""
    from db.pix_charges_saga import buscar_ativa

    ativa = buscar_ativa(user_id) or {}
    qr = qr_da_linha(ativa) if ativa else None
    if not qr:
        # QR apagado (terminal) ou linha que sumiu: não há o que devolver, e
        # inventar um QR novo aqui pularia o `criar_cobranca`. 503 e o cliente
        # tenta de novo — na próxima passada não haverá cobrança ativa.
        raise CheckoutIndisponivel("cobranca_ativa_sem_qr")
    log_system_event_sync("info", "pix_checkout_reaproveitado",
                          "Checkout Pix devolveu a cobranca ativa do mesmo plano.",
                          source="pix", user_id=user_id,
                          details={"plan": plan_stored})
    return resposta(dict(ativa, access_starts_at=starts_at), qr)


def _emitir(linha: dict, cpf_cnpj: str, nome: str, email: str | None) -> dict:
    """`draft` → `creating` → POST → QR → `pending`. A saga do §10, na ordem.

    `creating` é gravado ANTES da chamada remota: é ele que diz à varredura "pode
    haver cobrança lá que não conhecemos". Depois, seria um `draft` que a regra
    (b) do §10.1 acharia seguro apagar.
    """
    from core.services.asaas_customers import TitularRecusado, criar_cliente
    from core.services.email_service import plan_display_name
    from db.pix_charges import transicionar
    from db.pix_charges_saga import attach_pagamento, voltar_para_draft

    transicionar(linha["id"], de=("draft",), para="creating")
    vence = date.today() + timedelta(days=VENCIMENTO_DIAS)
    try:
        cliente = criar_cliente(nome=nome, cpf_cnpj=cpf_cnpj, email=email)
        pagamento = asaas.criar_pagamento_pix(
            customer_id=cliente, valor_cents=int(linha["amount_cents"]),
            due_date=vence.isoformat(),
            external_reference=linha["external_reference"],
            # NOME COMERCIAL, não o slug: esta linha é a descrição da FATURA que
            # o pagador lê no app do banco, e ela vinha saindo "PigBank anual
            # (pro_max)". Nome, e não o tier público (`pro`), porque ali não há
            # legenda nenhuma para traduzir um slug. `plan_display_name` é a
            # fonte que o e-mail de confirmação já usa, chaveada pelo MESMO
            # valor legado da coluna (§0.7) — o cliente lê o mesmo nome nos
            # dois, e o fallback genérico mora lá dentro em vez de aqui.
            # Separador ASCII de propósito: como cada app de banco renderiza um
            # travessão (U+2014) na descrição do Pix não dá para verificar aqui,
            # e o hífen não perde nada. O texto era ASCII puro antes do #350.
            descricao=f"{plan_display_name(linha['plan'])} - plano anual")
        qr = asaas.obter_qr_pix(str(pagamento.get("id") or ""))
    except TitularRecusado:
        # Nada existe no Asaas: `criar_pagamento_pix` nem rodou (o porquê, em
        # `asaas_customers`). `draft` poupa a passada seguinte de perguntar.
        voltar_para_draft(linha["id"])
        raise
    except Exception as exc:  # noqa: BLE001 — a linha fica `creating` de propósito
        raise CheckoutIndisponivel("asaas_emissao_falhou") from exc

    attach_pagamento(linha["id"], str(pagamento["id"]),
                     qr_payload_enc=encrypt_pii_optional(qr["payload"]),
                     due_date=vence, qr_expires_at=expira(qr))
    linha = dict(linha, asaas_payment_id=str(pagamento["id"]), status="pending",
                 qr_expires_at=expira(qr))
    return resposta(linha, qr["payload"])
