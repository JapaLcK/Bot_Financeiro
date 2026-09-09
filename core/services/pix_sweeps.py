"""
core/services/pix_sweeps.py — as varreduras diárias do Pix.

Neste PR há **duas**: a reconciliação da saga (§10.1) e a purga por retenção
(`purgar_retencao`, §13.2 + §13.3). As três que o §14 item 13 lista — e-mail de
fim de anual, cancelamento remoto aos 60 dias e a retenção **por prazo**
(`RETENCAO_TENTATIVA_DIAS`/`RETENCAO_PAGAMENTO_DIAS`, que apaga LINHA) — foram
**adiadas pelo dono** e vivem na issue #329. Elas não estão aqui nem como `pass`:
esqueleto vazio é scaffolding que ninguém reconfere.

A purga desta leva **não apaga linha nenhuma** — zera coluna de PII (`payload_enc`,
`last_error`, rastreio, QR, `asaas_customer_id`) e carimba `purged_at`. Por isso ela
não é uma das três adiadas: o que o dono adiou foi o prazo que decide quando a
cobrança some, não a minimização do que fica.

Plano: docs/plano_pix_anual_asaas.md §10 e §10.1.

## A regra que este módulo inteiro existe para respeitar

> **A idade decide QUANDO reconciliar. Só a reconciliação decide o que apagar.**

Idade não é prova: um `draft` velho pode ter ganhado id remoto num POST cuja
resposta se perdeu, e apagá-lo cria dinheiro sem linha nossa. Por isso as três
saídas possíveis por linha são, e são só estas:

  * o Asaas **mostra** a cobrança  → `attach` + `pending` (a saga fecha);
  * o Asaas devolve **lista vazia** → `creating` volta a `draft`; `draft` é apagado;
  * a consulta **falha ou não é entendida** → **nada acontece**, e a passada
    seguinte tenta de novo. Indisponibilidade do provedor não é evidência de
    inexistência, e `buscar_por_external_reference` levanta em vez de devolver
    `[]` justamente para o "não sei" não chegar aqui parecendo "não existe".
"""

from __future__ import annotations

from core.observability import log_system_event_sync
from core.services import admin_notify, asaas

# Quanto tempo uma cobrança pode ficar em `draft`/`creating` antes de valer a
# pena perguntar ao Asaas. É o "aos 15 min" do §10.1 — e ele decide só a HORA
# de olhar, nunca o desfecho.
RECONCILIAR_APOS_MIN = 15

# Depois de quanto tempo a mesma linha irreconciliável vira problema de
# integração e não de cobrança (§10.1, última regra).
ALERTA_APOS_HORAS = 24

_VIVA_NO_ASAAS = ("PENDING", "AWAITING_RISK_ANALYSIS", "CONFIRMED", "RECEIVED",
                  "RECEIVED_IN_CASH", "OVERDUE")


def drenar_pendentes(limite: int = 100) -> int:
    """Drena os eventos ainda abertos da outbox. Devolve quantos passaram.

    É a passada de 60 s. O `background_tasks` do handler é o caminho rápido;
    esta é a que RECUPERA — processo reiniciado no meio, efeito que levantou,
    evento que chegou enquanto o worker morria.

    **Mora aqui e não no laço do monólito** porque o monólito não pode importar
    `db/webhook_outbox.py`: `tests/test_pix_inerte.py` mede o primeiro salto
    produção → Pix, e pôr o arquivo de 8 mil linhas na allowlist apagaria a
    propriedade inteira. O monólito chama serviço; serviço fala com `db/`.

    `drenar_evento` nunca levanta (falha vira `attempts`), então um evento
    travado não segura a fila dos outros.
    """
    from core.services.pix_drain import drenar_evento
    from db.webhook_outbox import eventos_pendentes

    pendentes = eventos_pendentes(limite)
    for event_id in pendentes:
        drenar_evento(event_id)
    return len(pendentes)


def purgar_retencao() -> dict:
    """As DUAS purgas por retenção (§13.2 e §13.3). Devolve a contagem por tabela.

    Mora aqui pelo mesmo motivo de `drenar_pendentes`: o monólito não pode
    importar `db/webhook_outbox.py` nem `db/pix_charges.py` sem apagar o portão
    de inércia. O monólito chama serviço; serviço fala com `db/`.

    **Duas chamadas e não uma query parametrizada**: os predicados são
    diferentes de propósito — a outbox filtra só idade + `purged_at is null`
    (não tem coluna `user_id`), e `pix_charges` filtra `user_id is null and
    purged_at is null` (não tem prazo). Fundi-los quebra na primeira execução.
    """
    from db.pix_charges import rezerar_rastreio_de_orfas
    from db.webhook_outbox import purgar_payloads_antigos

    return {"outbox": purgar_payloads_antigos(),
            "cobrancas_orfas": rezerar_rastreio_de_orfas()}


def reconciliar_saga(limite: int = 200) -> dict:
    """Fecha as sagas que morreram no meio. Devolve a contagem por desfecho.

    Nunca levanta: é chamada de um laço de fundo, e uma linha esquisita não pode
    derrubar a varredura das outras. Falha por linha vira log e a linha fica.
    """
    from db.pix_charges_saga import listar_para_reconciliar

    contagem = {"anexadas": 0, "voltaram": 0, "apagadas": 0, "indefinidas": 0}
    for linha in listar_para_reconciliar(RECONCILIAR_APOS_MIN, limite):
        try:
            contagem[_reconciliar_uma(linha)] += 1
        except Exception as exc:  # noqa: BLE001 — uma linha não derruba a passada
            contagem["indefinidas"] += 1
            log_system_event_sync(
                "warning", "pix_reconciliacao_falhou",
                "Nao consegui reconciliar a cobranca — a linha fica como esta.",
                source="pix", details={"charge_id": linha["id"],
                                       "erro": type(exc).__name__})
    return contagem


def _reconciliar_uma(linha: dict) -> str:
    """Uma cobrança. Devolve a chave do desfecho.

    A consulta é por `external_reference`, que é NOSSO (`pix:<id>`) e unique — e
    é a única chave que existe quando o `asaas_payment_id` nunca chegou.
    """
    remotas = asaas.buscar_por_external_reference(linha["external_reference"])
    viva = _viva(remotas)
    if viva is not None:
        return "anexadas" if _anexar(linha, viva) else "indefinidas"
    if remotas:
        # Existe lá, mas DELETED/REFUNDED: não é "nunca existiu", então a regra
        # (b) não se aplica e a linha não some. `creating` volta a `draft` e a
        # passada seguinte reconsulta; `draft` fica esperando o dono comprar de
        # novo, e é a varredura de retenção (adiada, #329) que a expira.
        return "voltaram" if _voltar(linha) else "indefinidas"
    return _sem_cobranca_remota(linha)


def _viva(remotas: list[dict]) -> dict | None:
    """A primeira cobrança remota ainda pagável, se houver.

    `status` fora de `_VIVA_NO_ASAAS` (`DELETED`, `REFUNDED`) **não** vira
    attach: anexar uma cobrança morta poria a nossa linha em `pending` com um id
    que nunca vai pagar, e o cliente ficaria olhando um QR inútil.
    """
    for r in remotas:
        if isinstance(r, dict) and r.get("id") and str(r.get("status") or "") in _VIVA_NO_ASAAS:
            return r
    return None


def _anexar(linha: dict, remota: dict) -> bool:
    """Fecha a saga com o id que o Asaas confirmou ter — **e com o QR**.

    A segunda chamada (`obter_qr_pix`) não é luxo: a cobrança que a varredura
    acha é justamente aquela cujo QR o cliente nunca recebeu, e quem reabrir a
    tela cai no caminho "mesmo QR" do checkout, que LÊ a linha. Anexar só o id
    levava a `pending` sem instrumento de pagamento — e daí `_reaproveitar`
    devolve 503 em todo checkout do mesmo plano, enquanto `pending` já não volta
    para a reconciliação. Cobrança impagável e insubstituível, para sempre.

    Falha ali levanta (o Asaas recusa cobrança sem `payload`), e o `except` de
    `reconciliar_saga` a lê como "indefinidas": a linha fica em `creating`/`draft`
    e a passada seguinte tenta de novo — que é o desfecho certo, porque anexar
    sem QR é a própria armadilha acima.

    `ponytail:` `qr_expires_at` fica nulo (o parser do `expirationDate` mora no
    checkout). O que se perde é a precisão do teto do poll, não o pagamento.
    """
    from core.crypto import encrypt_pii_optional
    from db.pix_charges_saga import attach_pagamento

    qr = asaas.obter_qr_pix(str(remota["id"]))
    return attach_pagamento(linha["id"], str(remota["id"]),
                            qr_payload_enc=encrypt_pii_optional(qr["payload"]))


def id_remoto_vivo(external_reference: str) -> str | None:
    """O id da cobrança remota ainda PAGÁVEL desta referência, se houver.

    Mora aqui, e não no checkout, porque é a mesma pergunta da reconciliação
    (§10.1) — "o POST efetivou e a resposta se perdeu?" —, com a mesma consulta e
    a mesma lista de status vivos (§0.7). Quem chama é a substituição: uma linha
    `creating` **sem `asaas_payment_id`** pode ter cobrança pagável lá, e pular o
    `DELETE` por causa da coluna nula deixava as duas pagáveis ao mesmo tempo.

    **Levanta quando a consulta falha**, e é o contrato de
    `buscar_por_external_reference`: "não sei" não pode virar "não existe" num
    caminho que emite a segunda cobrança.
    """
    viva = _viva(asaas.buscar_por_external_reference(external_reference))
    return str(viva["id"]) if viva else None


def _voltar(linha: dict) -> bool:
    from db.pix_charges_saga import voltar_para_draft

    return voltar_para_draft(linha["id"])


def _sem_cobranca_remota(linha: dict) -> str:
    """**Lista vazia** — a única prova aceita de que a cobrança nunca existiu lá.

    E ela ainda não basta sozinha: a regra (b) do §10.1 é um `and` com
    `asaas_payment_id is null`, e a segunda metade está no `where` de
    `apagar_cobranca`, no banco. `creating` **nunca** expurga — volta a `draft`,
    e a passada seguinte cai aqui de novo com a linha já em `draft`.
    """
    from datetime import datetime, timedelta, timezone

    from db.pix_charges_saga import apagar_cobranca

    if linha["status"] == "creating":
        return "voltaram" if _voltar(linha) else "indefinidas"
    if apagar_cobranca(linha["id"]):
        return "apagadas"
    # Não apagou: alguém mexeu no meio (o cliente comprou de novo, o attach
    # entrou). A linha fica; o alerta só sai quando ela envelhece de verdade.
    criada = linha.get("created_at")
    if criada and criada < datetime.now(timezone.utc) - timedelta(hours=ALERTA_APOS_HORAS):
        admin_notify.notify_pix_alerta(
            f"⚠️ **Pix: cobrança irreconciliável** `{linha['external_reference']}` — "
            f"parada em `{linha['status']}` há mais de {ALERTA_APOS_HORAS}h. "
            "Problema de integração, não de cobrança.")
    return "indefinidas"
