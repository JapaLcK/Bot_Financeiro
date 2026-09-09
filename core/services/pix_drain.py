"""
core/services/pix_drain.py — o dreno da outbox do webhook do Asaas (§8.2).

O handler grava o evento e responde 200; **tudo o que acontece com o dinheiro
acontece aqui**: casar o evento com a cobrança, avançar a máquina de estados do
§11 e rodar os efeitos.

Plano: docs/plano_pix_anual_asaas.md §8.2, §11, §12 e §17.1.

## Duas operações INDEPENDENTES, cada uma idempotente por si

A transição avança o estado **se ainda não avançou**; os efeitos saem sempre de
`ROTEAMENTO` e cada um é pulado individualmente pelo par
`(asaas_payment_id, effect)`. **Nenhuma das duas é porteira da outra** — a regra
`aplicou == False → efeitos = []` foi removida no #304 por ser um ponto de perda
de dinheiro (a transição commita antes dos efeitos; morrer no meio zerava a
lista na retentativa).

## As decisões do dono que estão em CÓDIGO aqui, e não em prosa

  * **estorno parcial nunca revoga E nunca concede** — `PARTIALLY_REFUNDED` só
    transiciona de `paid`/`paid_orphan`; de qualquer outro estado é zero
    transição e zero efeito. É o que resolve a divergência §11 × §8.2 a favor do
    §11 e fecha a pendência 1: o parcial fora de ordem encontra a cobrança em
    `pending`, **não a move**, e o `RECEIVED` que chega depois concede pelo
    caminho normal;
  * **chargeback não revoga na abertura** — o curinga `PAYMENT_CHARGEBACK_*`
    saiu. São três eventos nomeados, nenhum transiciona, e o
    `AWAITING_CHARGEBACK_REVERSAL` (que significa que NÓS GANHAMOS) não podia
    mesmo continuar revogando o acesso do cliente. Quem revoga quando perdemos é
    o `PAYMENT_REFUNDED` que chega depois. **Nome novo cai em desconhecido**;
  * **dinheiro devolvido não é reconcedido** — com `(payment_id, 'revoke')` já
    registrado, os efeitos de COMPRA não rodam. É a regra "quem concede é o
    pagamento, nunca o estorno" lida do outro lado, para o estorno TOTAL que
    chega antes do `RECEIVED`. Usa só `pix_payment_effects`, que **nunca é
    purgada** — a única autoridade que sobrevive a uma morte no meio.

## Onde o `processed_at` é escrito, e por que FORA da reserva

`reservar_evento` segura um `select … for update skip locked` na linha da outbox
**enquanto a transação viver**. Escrever nessa mesma linha por OUTRA conexão do
pool durante o `with` é um bloqueio que o detector de deadlock do Postgres não
enxerga — do lado dele há um só esperador; o outro lado espera em Python. Por
isso o desfecho é decidido dentro do `with` e ESCRITO depois dele.

A janela entre soltar a reserva e carimbar `processed_at` é reentrante e
inofensiva: outra passada refaz o trabalho e cada efeito é pulado pelo registro.
"""

from __future__ import annotations

import json
import re

from psycopg import errors

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync
from core.services import admin_notify
from core.services.pix_drain_effects import (
    EXECUTORES,
    alertar,
    janela_de_acesso,
)
from db.pix_charges import (
    buscar_por_asaas_payment_id,
    buscar_por_external_reference,
    transicionar,
)
from db.pix_effects import efeito_registrado, lock_efeito, registrar_efeito
from db.webhook_outbox import marcar_processado, registrar_falha, reservar_evento

# `ponytail:` regex fixa, não env — o formato é escrito por nós, num lugar só
# (`criar_cobranca`). É ele que separa o nosso dinheiro do Pix avulso que a
# conta do NEGÓCIO também recebe (§11, correção 5).
REFERENCIA_NOSSA = re.compile(r"^pix:[0-9]+$")

EFEITOS_DE_COMPRA = ("stripe_cancel", "grant", "ga4", "capi", "email")
_ORIGENS_DE_PAGAMENTO = ("pending", "canceling", "canceled", "expired")
_APAGAM_O_QR = ("paid", "paid_orphan", "canceled", "expired")

# `event_type` → (origens permitidas, destino, efeitos). `origens` vazia quer
# dizer **nenhuma transição**, e é diferente de ausente: ausente cai em
# desconhecido e vira log de formato novo do provedor.
#
# A tabela é chaveada por `(evento, estado de origem)` e não pelo evento
# sozinho: é isso que faz o estorno parcial fora de ordem não mover uma cobrança
# `pending`. Origem que falta = transição que não acontece, sem exceção e sem
# efeito colateral.
ROTEAMENTO: dict[str, tuple[tuple[str, ...], str | None, tuple[str, ...]]] = {
    # No Pix só o `RECEIVED` ocorre; o `CONFIRMED` fica por robustez (§11).
    "PAYMENT_RECEIVED": (_ORIGENS_DE_PAGAMENTO, "paid", EFEITOS_DE_COMPRA),
    "PAYMENT_CONFIRMED": (_ORIGENS_DE_PAGAMENTO, "paid", EFEITOS_DE_COMPRA),
    "PAYMENT_CREATED": ((), None, ()),
    "PAYMENT_OVERDUE": (("pending", "canceling"), "expired", ()),
    "PAYMENT_DELETED": (("pending", "canceling"), "canceled", ()),
    "PAYMENT_REFUNDED": (("paid", "paid_orphan", "refunded_partial"),
                        "refunded", ("revoke",)),
    # Só de `paid`/`paid_orphan`, e sem efeito nenhum: o parcial nunca revoga e
    # nunca concede (decisão do dono, pendências 1 e 4).
    "PAYMENT_PARTIALLY_REFUNDED": (("paid", "paid_orphan"),
                                   "refunded_partial", ()),
    # Estorno agendado e estorno negado: revogar no primeiro cortaria acesso por
    # algo que ainda pode ser negado, e o segundo não tem o que reverter.
    "PAYMENT_REFUND_IN_PROGRESS": ((), None, ()),
    "PAYMENT_REFUND_DENIED": ((), None, ()),
    # Os TRÊS nomes do chargeback, nenhum transicionando (decisão do dono,
    # pendência 2). O `AWAITING_CHARGEBACK_REVERSAL` é a nossa VITÓRIA.
    "PAYMENT_CHARGEBACK_REQUESTED": ((), None, ()),
    "PAYMENT_CHARGEBACK_DISPUTE": ((), None, ()),
    "PAYMENT_AWAITING_CHARGEBACK_REVERSAL": ((), None, ()),
}

_ALERTAM = ("PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED",
            "PAYMENT_CHARGEBACK_DISPUTE")


def drenar_evento(event_id: str) -> None:
    """Drena UM evento da outbox. Nunca levanta — falha vira `attempts`.

    `reservar_evento` rende `None` para evento inexistente, já processado ou
    travado por outra passada: nos três a ação é a mesma, sair.
    """
    with reservar_evento(event_id) as evt:
        if evt is None:
            return
        falha = _decidir(event_id, evt)
    # Fora do `with` de propósito — ver o cabeçalho do módulo.
    if falha is None:
        marcar_processado(event_id)
        return
    tentativas = registrar_falha(event_id, *falha)
    if tentativas > 5:
        admin_notify.notify_pix_alerta(
            f"⚠️ **Pix: evento travado** `{event_id}` — {tentativas} tentativas, "
            f"último erro `{falha[0]}`."
        )


def _decidir(event_id: str, evt: dict) -> tuple[str, str | None] | None:
    """O dreno propriamente dito. `None` = concluído; tupla = falha retentável."""
    if evt["payload_enc"] is None:
        return None  # purgado (§13.3): fecha o evento e sai, sem falhar
    dados = _payload(evt["payload_enc"])
    pagamento = dados.get("payment") if isinstance(dados.get("payment"), dict) else {}
    payment_id = pagamento.get("id")
    referencia = pagamento.get("externalReference")

    cobranca = None
    if isinstance(payment_id, str) and payment_id:
        cobranca = buscar_por_asaas_payment_id(payment_id)
    if cobranca is None and isinstance(referencia, str) and referencia:
        cobranca = buscar_por_external_reference(referencia)
    if cobranca is None:
        _sem_cobranca(referencia, pagamento)
        return None

    rota = ROTEAMENTO.get(evt["event_type"])
    if rota is None:
        log_system_event_sync(
            "warning", "asaas_evento_desconhecido",
            f"Tipo de evento do Asaas sem rota: {evt['event_type']}",
            source="pix", details={"event_type": evt["event_type"]},
        )
        return None
    origens, destino, efeitos = rota
    payment_id = payment_id or cobranca["asaas_payment_id"]

    # B) GUARDA DE ÓRFÃO, antes do laço: titular excluído (§13.4). Nenhum efeito
    # de compra — não há a quem conceder —, e o `CHECK` da janela não se aplica.
    if cobranca["user_id"] is None:
        return _orfao(event_id, cobranca, destino, origens, payment_id)

    # D3 — dinheiro devolvido não é reconcedido. Vale só para efeito de COMPRA:
    # o `revoke` de um estorno que chegue depois continua rodando.
    if set(efeitos) & set(EFEITOS_DE_COMPRA) and efeito_registrado(payment_id, "revoke"):
        log_system_event_sync(
            "warning", "pix_received_apos_estorno",
            "Pagamento confirmado depois de estorno ja registrado — nenhum "
            "efeito de compra executado.",
            source="pix", user_id=cobranca["user_id"],
            details={"charge_id": cobranca["id"]},
        )
        admin_notify.notify_pix_alerta(
            f"⚠️ **Pix: pagamento depois do estorno** cobrança `{cobranca['id']}` — "
            "acesso NÃO concedido, confira no painel do Asaas."
        )
        return None

    if destino and origens:
        # A linha NOVA substitui a lida no começo: os efeitos leem
        # `access_starts_at`/`access_expires_at`, que só existem depois desta
        # escrita. Sem a troca o `grant` recebia `None` nos dois e o `not null`
        # de `plan_grants` derrubava a concessão de TODA venda — medido.
        # `None` (a transição não aplicou) obriga a RELER: uma das razões do
        # `None` é outra passada ter aplicado no meio, e aí a linha lida lá em
        # cima está velha — sem janela. Medido com duas threads: sem a releitura
        # a segunda passada chamava `grant` com `access_*` nulo e morria de
        # `NotNullViolation`, o que ESCONDIA a duplicata que D5-a mede.
        nova, falha = _transicionar(cobranca, origens, destino, payment_id)
        if falha:
            return falha
        cobranca = nova or buscar_por_asaas_payment_id(payment_id) or cobranca

    if evt["event_type"] in _ALERTAM:
        alertar(evt["event_type"], cobranca, payment_id)

    for efeito in efeitos:
        with lock_efeito(payment_id, efeito):
            if efeito_registrado(payment_id, efeito):
                continue
            try:
                EXECUTORES[efeito](cobranca, evt)
            except Exception as exc:  # noqa: BLE001 — vira attempts, não 500 mudo
                return (type(exc).__name__, getattr(exc, "code", None))
            registrar_efeito(payment_id, efeito, event_id)
    return None


def _payload(payload_enc: str) -> dict:
    """Decifra o corpo minimizado da outbox.

    **`subject_user_id=None` é o único valor honesto aqui, e não um esquecimento.**
    O dono só se descobre DEPOIS de casar o evento com a cobrança, e casar exige
    o `externalReference`, que está dentro deste texto — inventar um sujeito (0,
    -1) gravaria linha falsa em `pii_access_log`, que é registro de compliance.
    A coluna é anulável no DDL justamente para "sujeito desconhecido".
    """
    bruto = decrypt_pii_optional(payload_enc, ctx=PiiAccessContext(
        purpose="pix_webhook_drain", actor="system:pix_drain",
        subject_user_id=None, field="asaas_webhook_payload"))
    try:
        dados = json.loads(bruto or "{}")
    except ValueError:
        dados = {}
    return dados if isinstance(dados, dict) else {}


def _sem_cobranca(referencia, pagamento: dict) -> None:
    """Ramo A do §8.2 — pagamento sem linha nossa.

    Fora do formato `^pix:[0-9]+$` é dinheiro de TERCEIRO (a conta Asaas é a do
    negócio e recebe outros Pix): log com contagem, **sem alerta e sem linha**.
    Alerta que dispara por dinheiro que não é problema é alerta que ninguém lê
    no dia em que for.
    """
    if not isinstance(referencia, str) or not REFERENCIA_NOSSA.match(referencia):
        log_system_event_sync(
            "info", "asaas_evento_fora_do_escopo",
            "Pagamento do Asaas fora do nosso formato de referencia.",
            source="pix", details={"tem_referencia": bool(referencia)},
        )
        return
    _registrar_nao_casado(pagamento, referencia)
    admin_notify.notify_pix_alerta(
        f"🚨 **Pix nosso sem cobrança** `{referencia}` — pagamento com "
        "referência do PigBank e nenhuma linha em `pix_charges`."
    )


def _registrar_nao_casado(pagamento: dict, referencia: str) -> None:
    """Insere em `pix_unmatched_payments` — fila de conciliação, não venda.

    `on conflict do nothing`: o Asaas reentrega, e a mesma conciliação não pode
    empilhar. Sem `user_id` porque não há dono — é esse o ponto da tabela.
    """
    from db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_unmatched_payments"
                "  (asaas_payment_id, external_reference, value, net_value,"
                "   status, date_created, customer, notified_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, now())"
                " on conflict (asaas_payment_id) do nothing",
                (str(pagamento.get("id") or ""), referencia,
                 pagamento.get("value"), pagamento.get("netValue"),
                 pagamento.get("status"), pagamento.get("dateCreated"),
                 pagamento.get("customer")),
            )
        conn.commit()


def _orfao(event_id, cobranca, destino, origens, payment_id):
    """Titular excluído: a transição acontece, os efeitos de compra NÃO.

    O destino de pagamento vira `paid_orphan`, e é o `user_id is null` que
    dispensa a janela — a exceção do `CHECK` é por titular ausente, não por
    status (§17.1, pendência 3).
    """
    if destino and origens:
        alvo = "paid_orphan" if destino == "paid" else destino
        transicionar(cobranca["id"], de=origens, para=alvo,
                     asaas_payment_id=payment_id,
                     apagar_qr=alvo in _APAGAM_O_QR)
    with lock_efeito(payment_id, "orphan_notified"):
        if not efeito_registrado(payment_id, "orphan_notified"):
            registrar_efeito(payment_id, "orphan_notified", event_id)
            admin_notify.notify_pix_alerta(
                f"🚨 **Pix órfão** cobrança `{cobranca['id']}` — pagamento de "
                "conta já excluída, nenhum acesso concedido."
            )
    return None


def _transicionar(cobranca, origens, destino, payment_id):
    """Avança o estado, **sempre com a janela** quando o destino é `paid`.

    Devolve `(linha nova ou None, falha ou None)`.

    `CheckViolation` (o `pix_charges_pago_tem_janela`, §17.1 pendência 3) é
    falha RETENTÁVEL: vira `attempts` e o evento fica aberto na outbox. Engoli-la
    e carimbar `processed_at` seria dinheiro dentro e acesso nenhum, para sempre.
    """
    janela: dict = {}
    if destino == "paid":
        inicio, fim = janela_de_acesso(cobranca)
        janela = {"access_starts_at": inicio, "access_expires_at": fim}
    try:
        nova = transicionar(cobranca["id"], de=origens, para=destino,
                            asaas_payment_id=payment_id,
                            apagar_qr=destino in _APAGAM_O_QR, **janela)
    except errors.CheckViolation as exc:
        return (None, (type(exc).__name__, None))
    return (nova, None)
