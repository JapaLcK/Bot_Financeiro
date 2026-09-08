"""
core/services/payment_reminder.py — o lembrete de pagamento do 6º dia de atraso.

Quem está com o cartão falhado há 6 dias recebe UM lembrete de que a cobrança
continua pendente. **Nada é bloqueado, pausado ou removido** — nem aqui nem em
nenhum outro lugar deste PR. A regra de acesso por inadimplência é assunto de
outro trabalho; até ela existir, nenhuma mensagem daqui pode prometer perda de
acesso (foi o erro que reprovou a versão anterior deste código, que mandava
"amanhã eu pauso" sem nada pausar depois).

Mora em módulo próprio, e não dentro do `engagement_scheduler`, por dois
motivos: o assunto é cobrança (não engajamento) e o scheduler já batia no teto
de 350 linhas do `tests/test_max_lines_python.py` (§0.5). Mesmo desenho do
`trial_downsell.py`: a regra vive aqui, o tick de 24 h só a chama.

Chamado por `engagement_scheduler.run_engagement_loop`, no mesmo molde
isolado do `_check_trial_ending` e do `_check_free_upgrade_nudge` — falha aqui
não afeta os outros e-mails.

FREIO PRÓPRIO, `PAYMENT_REMINDER_ENABLED`, **default DESLIGADO**: ver
`payment_reminder_enabled()` abaixo. Ele NÃO é a `DUNNING_BLOCK_ENABLED` de
antes com outro nome — aquela ligava o corte, e o lembrete pegava carona no
`if` dela. Quando o corte saiu, a carona sumiu com ele e este caminho ficou
sem interruptor por acidente; a env nova repõe a propriedade que o desenho já
tinha (mesma convenção de `PAYWALL_ENABLED` e `PLANS_V2_ENABLED`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)


def payment_reminder_enabled() -> bool:
    """Lembrete de pagamento ligado? Default DESLIGADO, lido dinamicamente do
    ambiente a cada tick pra ligar/desligar SEM REDEPLOY — mesmo formato de
    `plan_service.paywall_enabled`. Liga com `PAYMENT_REMINDER_ENABLED=true`.

    Sem cache de módulo de propósito: este caminho manda e-mail (e, quando o
    template existir, WhatsApp) para cliente real, e a única forma de parar um
    envio errado não pode ser um deploy.

    Mora aqui, junto do consumidor, e NÃO em `billing_dunning` — aquele arquivo
    é a casa da lista de status e está com ZERO import de propósito (ele entra
    no caminho do webhook); pôr um `os.getenv` lá estragaria isso por nada.
    """
    return (os.getenv("PAYMENT_REMINDER_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on"
    )


async def check_payment_reminder() -> None:
    """Lembra quem está com o cartão em atraso há 6 dias de que a cobrança
    continua pendente.

    INERTE sem `PAYMENT_REMINDER_ENABLED`, e a guarda é a PRIMEIRA linha: nem a
    query do funil roda com a flag desligada.

    Janela no SQL (`db.dunning.list_payment_reminder_candidates`), abrindo no
    6º dia e com `PAYMENT_REMINDER_WINDOW_DAYS` de largura. Ela NÃO é de 1 dia,
    e a razão está na constante: o tick não é de 24 h exatos (o `sleep` de
    `run_engagement_loop` vem depois do trabalho) e restart/deploy atrasam
    muito mais — com 24 h, um tick perdido sumia com o único lembrete do ciclo.
    Quem impede o segundo lembrete é a dedupe abaixo, e a invariante que amarra
    a largura ao tamanho dela está em `core/services/billing_dunning`.

    Dedupe por `system_event_logs` (`recent_event_exists`,
    `PAYMENT_REMINDER_DEDUPE_DAYS`), e não por coluna
    como o `trial_downsell_sent_at`: inadimplência RECORRE, e uma coluna
    precisaria ser zerada quando o pagamento entra — virando uma quarta coisa
    para esquecer. Custo declarado: `system_event_logs` é purgável, então um
    "Limpar" no painel pode fazer o lembrete sair repetido. Mesmo trade-off que
    o `trial_ending_email_sent` já aceita.

    Pula allowlist e quem tem grant `pix`/`admin` vigente — nesses a cobrança
    do cartão não é o que sustenta o acesso, e lembrar de pagar algo que já
    está pago por outro caminho é ruído.

    E **revalida o estado imediatamente antes de enviar**
    (`db.dunning.lembrete_ainda_vale`): o funil é um snapshot, o lote não tem
    `LIMIT`, e quem pagou no meio dele não pode receber "a cobrança continua
    pendente". A máquina completa está em `docs/dunning_estados_eventos.md`.

    **UMA LINHA RUIM NÃO DERRUBA O LOTE.** O chamador embrulha esta função
    inteira num `except`, então toda exceção que escapa daqui abandona os
    candidatos SEGUINTES — e eles podem sair da janela antes do próximo tick.
    Por isso toda operação por linha que pode levantar tem `except` PRÓPRIO,
    com log específico e `continue`: decriptação de PII, checagem de grant,
    revalidação e envio. Enumeração da classe (com quem levanta, quem não
    levanta e por quê) em `tests/test_payment_reminder_lote.py`. Não troque isso
    por um `try` em volta do corpo do laço: a granularidade é o que faz o log
    dizer QUAL etapa falhou.

    **NADA DE ESCRITA SÍNCRONA NO EVENT LOOP.** Este tick roda no event loop
    único do Uvicorn e o funil não tem `LIMIT`: I/O feito aqui, e não num
    executor, atrasa request e webhook da Stripe. Todo o laço abaixo já vai por
    executor — mantenha assim. A decriptação de PII era a exceção e foi para
    `_decifrar_lote` (medição e o motivo da forma, na docstring dela).

    E-MAIL é o caminho garantido; o WhatsApp é melhoria e mora em
    `core/services/payment_reminder_wa.py`.
    """
    if not payment_reminder_enabled():
        return

    from core.observability import recent_event_exists
    from core.services import plan_service
    from core.services.billing_dunning import (
        DUNNING_GRACE_DAYS,
        PAYMENT_REMINDER_DEDUPE_DAYS,
    )
    from core.services.email_service import send_payment_reminder_email
    # Reuso, não cópia (§0.1): a minimização de PII em log já existe lá.
    from core.services.engagement_scheduler import _mask_email
    # O canal WhatsApp mora em módulo irmão (assunto próprio + teto de linhas).
    from core.services.payment_reminder_wa import _wa_lembrete
    from db.dunning import lembrete_ainda_vale, list_payment_reminder_candidates

    loop = asyncio.get_event_loop()
    dashboard_url = os.getenv("DASHBOARD_URL", "https://pigbankai.com")

    try:
        rows = await loop.run_in_executor(
            None, list_payment_reminder_candidates, DUNNING_GRACE_DAYS)
    except Exception as exc:
        logger.error("[cobranca] Falha ao buscar candidatos: %s", exc, exc_info=True)
        return

    # Allowlist ANTES da decriptação: é filtro PURO (`set` de dois ids, sem
    # I/O), e assim não se decifra nem se AUDITA PII de quem já foi descartado.
    # O conjunto decifrado fica idêntico ao de antes desta mudança — zero delta
    # de acesso a PII.
    elegiveis = [r for r in rows
                 if int(r["user_id"]) not in plan_service._ACCESS_ALLOWLIST]
    candidatos = await loop.run_in_executor(None, _decifrar_lote, elegiveis)

    for user_id, email in candidatos:
        # SEM `try`, e isso é medido, não descuido: `recent_event_exists`
        # (`core/observability.py:288-313`) tem `except Exception` próprio e
        # devolve `False` em QUALQUER falha ("melhor mandar duplicado que
        # perder", docstring dela). Medido nos três modos — banco inalcançável,
        # URL inválida, `DATABASE_URL` vazia — nenhum levantou. Um `try` aqui
        # embrulharia código que provadamente não levanta (§0.2);
        # `tests/test_payment_reminder_lote.py` amarra essa dependência para o
        # dia em que aquele `except` sair.
        #
        # A assimetria com `lembrete_ainda_vale` abaixo é de PROPÓSITO: a
        # dedupe falha ABERTA (manda, no pior caso duplicado) e a revalidação
        # falha FECHADA (não manda). Perguntas diferentes, direções seguras
        # opostas.
        if await loop.run_in_executor(
            None, recent_event_exists, "payment_reminder_sent", user_id,
            PAYMENT_REMINDER_DEDUPE_DAYS,
        ):
            continue
        # Grant pix/admin vigente: o acesso desta conta não depende do cartão,
        # então o lembrete não tem assunto. Reusa o PREDICADO (`grant_vigente`)
        # em vez de uma segunda lista de sources para divergir.
        try:
            if await loop.run_in_executor(None, _pago_por_outro_caminho, user_id):
                continue
        except Exception as exc:
            logger.error("[cobranca] checagem de grant falhou user_id=%s: %s", user_id, exc)
            continue
        # REVALIDAÇÃO, a última coisa antes do envio. `rows` é UM snapshot e o
        # lote não tem `LIMIT`: quem pagasse depois da query — em especial
        # enquanto as linhas anteriores são processadas — recebia "a cobrança
        # continua pendente" e a dedupe registrava o sucesso. E-mail errado para
        # cliente PAGANTE, que é a categoria que este caminho existe para
        # consertar (célula nº 28 de `docs/dunning_estados_eventos.md`).
        #
        # Leitura DIRETA (`lembrete_ainda_vale`), não `get_auth_user`:
        # aquele tem cache de 10 s e pode mentir exatamente nesta corrida. E
        # não é claim atômico — gravar a dedupe antes do envio é o bug que a
        # rodada 1 consertou. Falha de leitura NÃO manda: neste ponto o silêncio
        # é o erro recuperável (o tick seguinte tenta de novo, e a janela tem
        # `PAYMENT_REMINDER_WINDOW_DAYS` de largura justamente para isso).
        try:
            if not await loop.run_in_executor(None, lembrete_ainda_vale, user_id):
                logger.info("[cobranca] lembrete abortado: ciclo fechou durante"
                            " o lote → user_id=%s", user_id)
                continue
        except Exception as exc:
            logger.error("[cobranca] revalidacao falhou user_id=%s: %s", user_id, exc)
            continue
        try:
            ok = await loop.run_in_executor(
                None, send_payment_reminder_email, email, dashboard_url)
            if not ok:
                continue
            logger.info("[cobranca] lembrete enviado → user_id=%s (%s)",
                        user_id, _mask_email(email))
            # O opt-out do canal NÃO vem daqui: `_wa_lembrete` o lê fresco, no
            # ponto do envio dele. A versão anterior passava o valor do
            # snapshot do funil num parâmetro obrigatório, e obrigatório
            # protege contra OMISSÃO, não contra OBSOLESCÊNCIA — quem
            # desligasse o canal durante o lote recebia mensagem de todo jeito.
            wa = await loop.run_in_executor(None, _wa_lembrete, user_id)
            log_system_event_sync(
                "info",
                "payment_reminder_sent",
                "Lembrete de pagamento (cartao em atraso) enviado.",
                source="engagement_scheduler",
                user_id=user_id,
                details={"email": True, "whatsapp": wa},
            )
        except Exception as exc:
            logger.error("[cobranca] falha enviando user_id=%s: %s", user_id, exc)


def _decifrar_lote(rows: list[dict]) -> list[tuple[int, str]]:
    """Resolve o e-mail de cada candidato e devolve os pares `(user_id, email)`
    que dão para usar. **UMA ida ao banco para o audit de PII, e nenhuma no
    event loop.**

    Cada `decrypt_pii` chama `core.crypto._record_access`, que sem batch faz
    `get_conn` + `execute` + `commit` IMEDIATOS (`core/crypto.py:266-280`).
    Como o funil não tem `LIMIT` e este tick roda dentro do event loop único do
    Uvicorn, era uma ida ao banco BLOQUEANTE por linha, competindo com request
    e webhook da Stripe. Medido aqui (200 linhas, `pii_access_log` conferida
    com `count(*)` nos dois modos): 142,5 ms solto (0,712 ms/linha) × 9,3 ms em
    lote (0,046 ms/linha) — 15,4× menos tempo, e agora esse tempo está numa
    thread do executor, então o loop bloqueia ZERO.

    Reuso, não invenção (§0.1): `pii_audit_batch` já existe exatamente para
    isso ("endpoints que decifram muitos campos", `core/crypto.py:70-73`) e faz
    um `executemany` no `__exit__`.

    **Por que uma função separada, e não um `with` em volta do laço `async`.**
    O buffer de `pii_audit_batch` é `threading.local()`
    (`core/crypto.py:74`), NÃO task-local. Um `with` em volta de um laço com
    `await` dentro fica aberto na thread do event loop enquanto o loop atende
    OUTRAS corrotinas — e todo handler de request que decifrasse PII nesse
    intervalo teria a entrada de audit dele capturada no NOSSO buffer
    (`_record_access:261-264`), publicada só no nosso `__exit__` e perdida em
    silêncio se o nosso flush falhasse. Aqui `__enter__`, os `append` e o
    `__exit__` acontecem todos dentro da MESMA chamada de
    `run_in_executor`, numa thread só nossa. Não troque isto por um `with` em
    volta do laço achando que simplifica.

    A ISOLAÇÃO POR LINHA da rodada 4 continua, e continua por desenho: o `try`
    é de cada `decrypt_pii_optional`, então linha ruim vira `continue` e não
    derruba as seguintes — nem o flush, que roda no `__exit__` de todo jeito.
    Linha que falha não gera entrada de audit (o `_record_access` de
    `decrypt_pii` só roda DEPOIS de decifrar, `core/crypto.py:242`), o que está
    certo: acesso que não aconteceu não se registra.

    `continue`, e NUNCA `row["email"]` como fallback: `email_enc` que existe e
    não decifra é sinal de problema de CHAVE, não permissão para usar a coluna
    em claro. O log leva `user_id` e a exceção e nenhum e-mail — não há o que
    mascarar com `_mask_email` (o que falhou foi justamente obter o endereço),
    e as mensagens de `core/crypto.py` carregam versão e nome de env, nunca o
    valor.
    """
    from core.crypto import pii_audit_batch
    prontos: list[tuple[int, str]] = []
    with pii_audit_batch():
        for row in rows:
            user_id = int(row["user_id"])
            if row.get("email_enc"):
                try:
                    email = decrypt_pii_optional(
                        row["email_enc"],
                        ctx=PiiAccessContext(
                            purpose="send_payment_reminder_email",
                            actor="system:engagement",
                            subject_user_id=user_id,
                            field="email",
                        ),
                    )
                except Exception as exc:
                    logger.error("[cobranca] decriptacao do e-mail falhou"
                                 " user_id=%s: %s", user_id, exc)
                    continue
            else:
                email = row["email"]
            if email:
                prontos.append((user_id, email))
    return prontos


def _pago_por_outro_caminho(user_id: int) -> bool:
    """True se um grant `pix` ou `admin` vigente sustenta o acesso desta conta
    independentemente do cartão. `legacy` NÃO conta — ele é a reconstrução do
    MESMO acesso de cartão feita pelo backfill de grants."""
    from core.services.billing_access import grant_vigente
    from db.plan_grants import list_grants
    return grant_vigente(list_grants(user_id), datetime.now(timezone.utc),
                         sources=("pix", "admin"))
