"""
core/services/afiliado_reembolso.py — reembolso e disputa perdida da Stripe no programa de
afiliados: calcula o que a Stripe devolveu, grava a pendência e avisa o admin.

Quem decide é `db.affiliate_reviews.registrar_reembolso`, sob as travas, na mesma transação do
rótulo. O aviso vem depois do commit e não libera nada: falha dele responde 200, e a linha segue
pendente até `reenviar_pendentes`. Falha de banco ou da Stripe sobe: 5xx e a Stripe reentrega.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.observability import log_system_event_sync
from core.pg_text import limpa_para_pg
from core.services import admin_notify
from core.services.extras_assinar import _ler
from db.affiliate_reviews import faturas_do_saque, marcar_avisada, registrar_reembolso, reivindicar
from db.affiliate_reviews_manutencao import adiar_conferencia, comissoes_a_conferir, liberar_conferencia, marcar_conferencia

_FUSO = ZoneInfo("America/Sao_Paulo")
_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_REFERENCIA = re.compile(r"^[A-Za-z0-9._:/-]{4,120}$")


def tipo_do_reembolso(cobranca) -> str | None:
    """`total`, `partial` ou None pelo acumulado devolvido da cobrança."""
    devolvido = cobranca["amount_refunded"]
    if devolvido <= 0:
        return None
    return "total" if devolvido >= cobranca["amount"] else "partial"


def fatura_da_cobranca(cobranca) -> str | None:
    """Id da fatura da cobrança, ou None se ela não tem fatura.

    Dois caminhos, porque o formato do evento segue a versão de API do ENDPOINT, que não
    mora no repositório: `charge.invoice` existe nas versões antigas e saiu do SDK fixado
    (`stripe==15.6.1`, 2026-08-26). Sem ele, a volta é o `InvoicePayment` da `PaymentIntent`.
    """
    import stripe  # noqa: PLC0415

    fatura = _ler(cobranca, "invoice")
    if fatura:
        return _ler(fatura, "id") or fatura
    intent = _ler(cobranca, "payment_intent")
    intent = _ler(intent, "id") or intent
    if not intent:
        return None
    filtro = {"type": "payment_intent", "payment_intent": intent}
    for pagamento in stripe.InvoicePayment.list(payment=filtro, limit=100)["data"]:
        if pagamento["status"] == "paid":  # estornado continua "paid"; open/canceled são tentativas
            fatura = pagamento["invoice"]
            return _ler(fatura, "id") or fatura
    return None


def _texto(r: dict) -> str:
    saque = f" · saque `{r['payout_id']}`" if r["payout_id"] else ""
    quem = (f"comissão `{r['commission_id']}` ({r['commission_status']})" if r["commission_id"]
            else f"afiliado `{r['affiliate_code']}` (conta excluída)")
    cabeca = f"⚠️ **Afiliado: {{}}** {quem} · "
    if r["status"] == "recovery":
        return (cabeca.format("recuperação pendente") + f"R$ {r['recovery_cents'] / 100:.2f}{saque} · "
                "o dinheiro saiu antes do reembolso, confira.")
    onde = {"partial": "reembolso parcial", "total": "reembolso total com a comissão fora de alcance"}[r["tipo"]]
    valor = r["commission_cents"] if r["commission_cents"] is not None else r["amount_cents"]
    return cabeca.format(onde) + f"R$ {(valor or 0) / 100:.2f}{saque} · a comissão NÃO foi alterada, confira."


def _registrar_tentativa(r: dict, entregue: bool, event_id: str | None = None) -> None:
    """`error` quando o aviso não sai: a pendência segura o dinheiro, mas o admin ainda não sabe.
    `event_id` só do webhook; o worker não tem evento."""
    detalhes = {"commission_id": r["commission_id"], "payout_id": r.get("payout_id"), "tipo": r["tipo"],
                "alerta_entregue": entregue}
    if event_id is not None:
        detalhes["event_id"] = event_id
    log_system_event_sync(
        "warning" if entregue else "error", "affiliate_refund_commission_kept",
        "Reembolso de indicado sem estorno automatico da comissao.", source="affiliates", details=detalhes,
    )


def _enviar(r: dict, event_id: str | None = None) -> bool:
    """`r` já reivindicada. `notified_at` só com entrega confirmada."""
    entregue = admin_notify.notify_afiliado_alerta(_texto(r)) is True
    if entregue:
        marcar_avisada(r["id"], r["refunded_cents"], r["status"])
    _registrar_tentativa(r, entregue, event_id)
    return entregue


def reenviar_pendentes(limite: int = 20) -> int:
    """Passada do `_afiliado_alerta_worker`. Sem canal não reivindica nada; para na 1ª falha."""
    if not admin_notify.canal_configurado():
        return 0
    enviados = 0
    for _ in range(limite):
        r = reivindicar()
        if r is None or not _enviar(r):
            break
        enviados += 1
    return enviados


def validar_justificativa(valor) -> str:
    """Regra única das três ações do admin. O erro nunca ecoa o valor. Conta depois de limpar:
    NUL e surrogate viram U+FFFD, que não é texto."""
    texto = limpa_para_pg(valor).strip() if isinstance(valor, str) else ""
    if len(texto.replace("\ufffd", "").strip()) < 10 or len(texto) > 500:
        raise ValueError("Justificativa: de 10 a 500 caracteres.")
    return texto


def validar_comprovante(data_valor, referencia, minimo: date | None) -> tuple[date, str]:
    """A data do comprovante e a referência, para "Pagamento já realizado" e para a recuperação.
    A data não é futura em Brasília nem anterior a `minimo` (pedido do saque ou reembolso)."""
    if not isinstance(data_valor, str) or not _DATA.match(data_valor):
        raise ValueError("Data inválida: use AAAA-MM-DD.")
    try:
        data = date.fromisoformat(data_valor)
    except ValueError:
        raise ValueError("Data inválida: use AAAA-MM-DD.") from None
    if data > datetime.now(_FUSO).date():
        raise ValueError("A data não pode estar no futuro.")
    if minimo and data < minimo:
        raise ValueError("A data é anterior ao início deste caso.")
    limpa = limpa_para_pg(referencia).strip() if isinstance(referencia, str) else ""
    if not _REFERENCIA.match(limpa):
        raise ValueError("Referência inválida: de 4 a 120 caracteres, só letras, números e . _ : / -")
    return data, limpa


def dia_em_brasilia(instante: datetime) -> date:
    return instante.astimezone(_FUSO).date()


def tratar_evento(event) -> None:
    """`charge.refunded` e `charge.dispute.closed`. Sem fatura é no-op."""
    import stripe  # noqa: PLC0415

    objeto = event["data"]["object"]
    if event["type"] == "charge.dispute.closed":
        if objeto["status"] != "lost":
            return
        cobranca = objeto["charge"]
        if isinstance(cobranca, str):
            cobranca = stripe.Charge.retrieve(cobranca)
        # Soma o já devolvido: disputa perdida do restante deixa a cobrança sem nada.
        devolvido = objeto["amount"] + cobranca["amount_refunded"]
        tipo = "total" if devolvido >= cobranca["amount"] else "partial"
    else:
        cobranca, tipo, devolvido = objeto, tipo_do_reembolso(objeto), objeto["amount_refunded"]
    fatura = fatura_da_cobranca(cobranca)
    if not tipo or not fatura:
        log_system_event_sync(
            "warning", "affiliate_refund_sem_fatura",
            "Reembolso da Stripe sem fatura identificada.", source="affiliates",
            details={"event_type": event["type"], "event_id": event["id"]},
        )
        return
    linha = registrar_reembolso(fatura, tipo, devolvido, datetime.fromtimestamp(event["created"], timezone.utc))
    if not linha or linha["status"] not in ("open", "recovery") or linha["notified_at"]:
        return
    if not admin_notify.canal_configurado():
        _registrar_tentativa(linha, False, event["id"])
        return
    r = reivindicar(linha["id"])
    if r:
        _enviar(r, event["id"])


class SemConfirmacao(Exception):
    """A fatura ainda não permite concluir: disputa aberta, nenhuma cobrança paga ou forma inesperada
    (`payment_record`). É da própria fatura: quem varre segue para a próxima. O texto é só o motivo."""


def devolvido_na_stripe(fatura: str, chave: str) -> tuple[str | None, int, list]:
    """O tipo, o devolvido e as cobranças da fatura. Regra única da conferência e do script (A6):
    disputa perdida conta como devolvida, ganha não conta, aberta não confirma nada."""
    import stripe  # noqa: PLC0415

    from core.services.ebook_entrega import cobrancas_da_fatura  # noqa: PLC0415

    try:
        cobrancas = list(cobrancas_da_fatura(fatura, chave))
    except RuntimeError as exc:  # a forma que `cobrancas_da_fatura` recusa; erro da Stripe é `StripeError`
        raise SemConfirmacao("forma_inesperada") from exc
    if not cobrancas:
        raise SemConfirmacao("sem_cobranca")
    devolvido, inteiras = 0, True
    for c in cobrancas:
        perdida = 0
        if c["disputed"]:
            disputas = stripe.Dispute.list(charge=c["id"], limit=10, api_key=chave)["data"]
            if any(d["status"] not in ("won", "lost", "warning_closed") for d in disputas):
                raise SemConfirmacao("disputa_aberta")
            perdida = sum(d["amount"] for d in disputas if d["status"] == "lost")
        volta = min(c["amount"], c["amount_refunded"] + perdida)
        devolvido += volta
        inteiras = inteiras and volta >= c["amount"]
    return (("total" if inteiras else "partial") if devolvido else None), devolvido, cobrancas


def _adiada(commission_id: int, motivo: str) -> None:
    log_system_event_sync(
        "warning", "affiliate_commission_check_pending",
        "Conferencia de comissao na Stripe adiada.", source="affiliates",
        details={"commission_id": int(commission_id), "motivo": motivo},
    )


def conferir_comissao(commission_id: int, fatura: str, *, adiar: bool = False) -> bool | None:
    """Confere na Stripe a comissão que nasceu sem linha da fatura (A5). True se a marca saiu.

    Fora de transação. Reembolso vira pendência pela mesma máquina do webhook; sem reembolso a
    comissão segue o fluxo normal. A marca fica, e o worker tenta de novo, em dois casos:
    False quando a fatura ainda não permite concluir (`SemConfirmacao`, ou 4xx da própria fatura: 400,
    403, 404), e None quando a Stripe falhou para todas (conexão, prazo, chave, limite, 5xx) ou não há
    chave. Erro de banco sobe para quem chamou.
    `adiar`, só do worker: o False grava a hora e o motivo, e a comissão espera 1 h (#1497)."""
    import stripe  # noqa: PLC0415

    chave = os.getenv("STRIPE_SECRET_KEY")
    if not chave:
        return None
    try:
        tipo, devolvido, _ = devolvido_na_stripe(fatura, chave)
    except (SemConfirmacao, stripe.InvalidRequestError, stripe.PermissionError) as exc:
        # O texto da Stripe pode trazer a fatura: no motivo vai só o status.
        motivo = str(exc) if isinstance(exc, SemConfirmacao) else f"stripe_{exc.http_status}"
        _adiada(commission_id, motivo)
        if adiar:
            adiar_conferencia(commission_id, motivo)
        return False
    except Exception as exc:  # noqa: BLE001 — o tipo basta; o texto pode trazer a fatura
        _adiada(commission_id, type(exc).__name__)
        return None
    if tipo:
        registrar_reembolso(fatura, tipo, devolvido, datetime.now(timezone.utc), ator="sistema")
    liberar_conferencia(commission_id)
    return True


def conferir_pendentes(limite: int = 20) -> int:
    """Passada do `_afiliado_alerta_worker`. Fatura que ainda não permite concluir espera 1 h e não segura
    as seguintes; queda da Stripe para a passada sem gravar nada, que as outras bateriam nela."""
    if not os.getenv("STRIPE_SECRET_KEY"):
        return 0
    conferidas = 0
    for c in comissoes_a_conferir(limite):
        feita = conferir_comissao(c["id"], c["stripe_invoice_id"], adiar=True)
        if feita is None:
            break
        conferidas += feita
    return conferidas


class StripeIndisponivel(Exception):
    """Sem chave, ou a Stripe falhou numa fatura do saque. Quem paga recusa (503); o registro marca e segue."""


def _consultar(fatura: str, chave: str) -> tuple:
    """(tipo, devolvido, motivo de sem confirmação, erro). Erro da Stripe não sobe: a passada decide."""
    try:
        tipo, devolvido, _ = devolvido_na_stripe(fatura, chave)
        return tipo, devolvido, None, None
    except SemConfirmacao as exc:
        return None, 0, str(exc), None
    except Exception as exc:  # noqa: BLE001 — o tipo basta; o texto pode trazer a fatura
        return None, 0, None, type(exc).__name__


def conferir_saque(payout_id: int) -> dict:
    """Confere na Stripe cada fatura do saque antes de o dinheiro sair: QR, "Marcar pago" e "Pagamento já
    realizado", porque o `charge.refunded` pode se perder. HTTP fora de transação e de trava, 4 em paralelo.

    Reembolso achado vira pendência por `registrar_reembolso`, cada um na própria transação; quem recusa é a
    regra de bloqueio que já existe. Fatura sem confirmação (disputa aberta) marca a comissão (D10).
    Devolve `{"reembolso", "disputa"}`; sem chave ou com erro da Stripe, levanta `StripeIndisponivel`,
    depois de gravar o que achou."""
    chave = os.getenv("STRIPE_SECRET_KEY")
    if not chave:
        raise StripeIndisponivel("sem_chave")
    faturas = faturas_do_saque(payout_id)
    with ThreadPoolExecutor(4) as pool:
        lidas = list(zip(faturas, pool.map(lambda f: _consultar(f["fatura"], chave), faturas)))
    agora = datetime.now(timezone.utc)
    for f, (tipo, devolvido, _, _) in lidas:
        if tipo:
            registrar_reembolso(f["fatura"], tipo, devolvido, agora, ator="sistema")
    disputadas = [f["commission_id"] for f, (_, _, motivo, _) in lidas if motivo and f["commission_id"]]
    marcar_conferencia(disputadas)
    erros = sorted({erro for _, (_, _, _, erro) in lidas if erro})
    if erros:
        log_system_event_sync("warning", "affiliate_payout_check_failed", "Conferencia do saque na Stripe falhou.",
                              source="affiliates", details={"payout_id": int(payout_id), "motivo": ",".join(erros)})
        raise StripeIndisponivel(",".join(erros))
    return {"reembolso": any(tipo for _, (tipo, _, _, _) in lidas),
            "disputa": any(motivo for _, (_, _, motivo, _) in lidas)}
