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
    executor — mantenha assim, inclusive a decriptação (`_resolver_email`).

    **NENHUM VALOR DO SNAPSHOT CHEGA AO ENVIO.** O funil diz QUEM considerar e
    nada mais: estado da inadimplência, consentimento de e-mail e o próprio
    ENDEREÇO saem de `lembrete_ainda_vale`, uma leitura fresca por lembrete; o
    consentimento de WhatsApp sai de `_wa_lembrete`, no ponto de envio dele. A
    janela de elegibilidade é a única coisa deliberadamente NÃO revalidada —
    revalidá-la faria um lote lento descartar lembrete legítimo, que é o erro
    oposto e pior.

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

    # Allowlist é filtro PURO (`set` de dois ids, sem I/O) e fica aqui para nem
    # entrar no laço. O snapshot passa a servir APENAS para dizer QUEM
    # considerar: nenhum valor dele chega ao envio (ver `lembrete_ainda_vale`).
    elegiveis = [r for r in rows
                 if int(r["user_id"]) not in plan_service._ACCESS_ALLOWLIST]

    for _linha_do_funil in elegiveis:
        user_id = int(_linha_do_funil["user_id"])
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
            atual = await loop.run_in_executor(None, lembrete_ainda_vale, user_id)
        except Exception as exc:
            logger.error("[cobranca] revalidacao falhou user_id=%s: %s", user_id, exc)
            continue
        if atual is None:
            logger.info("[cobranca] lembrete abortado: ciclo fechou ou opt-out de"
                        " engajamento durante o lote → user_id=%s", user_id)
            continue
        # DECRIPTAÇÃO NO PONTO DO ENVIO, com o endereço que a revalidação acabou
        # de ler. `try` próprio para manter a granularidade do log e a garantia
        # de que uma linha ruim não derruba o lote (a exceção do
        # `core.crypto.decrypt_pii` viraria abandono dos candidatos seguintes,
        # porque o chamador embrulha esta função inteira). O log leva `user_id` e
        # a exceção e nenhum e-mail — o que falhou foi obter o endereço.
        try:
            email = await loop.run_in_executor(
                None, _resolver_email, user_id, atual)
        except Exception as exc:
            logger.error("[cobranca] decriptacao do e-mail falhou"
                         " user_id=%s: %s", user_id, exc)
            continue
        if not email:
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


def _resolver_email(user_id: int, linha: dict) -> str | None:
    """Endereço para onde o lembrete vai, a partir da linha que
    `db.dunning.lembrete_ainda_vale` acabou de ler. **Uma decriptação por
    lembrete, não por candidato.**

    Substituiu um `_decifrar_lote` que decifrava TODOS os candidatos de uma vez
    dentro de `core.crypto.pii_audit_batch` (uma escrita de auditoria em lote).
    A troca foi feita para o endereço deixar de vir do snapshot do funil — e
    ela melhora os três eixos, medido (200 candidatos, `pii_access_log`
    conferida com `count(*)`, 2026-09-09; remedir antes de reusar):

        lote,  N=200 candidatos : 14,1 ms, 200 linhas de auditoria
        aqui,  M=10  enviados   : 13,1 ms,  10 linhas
        aqui,  M=20  enviados   : 20,9 ms,  20 linhas
        aqui,  M=60  enviados   : 56,9 ms,  60 linhas

    Decifra-se MENOS PII (só de quem foi de fato contatado) e a trilha fica
    VERDADEIRA — o lote registrava acesso ao e-mail de todo candidato, incluindo
    os que a dedupe descartava, que em regime são a maioria (a janela de dedupe
    é de 6 dias e a de elegibilidade de 3, então quem já recebeu continua no
    funil). O tempo é comparável em M pequeno e maior em M grande, sempre em
    dezenas de ms, dentro do executor e uma vez por tick de 24 h.

    **Não reintroduza `pii_audit_batch` aqui.** O buffer dele é
    `threading.local()` (`core/crypto.py:74`), não task-local: um `with` aberto
    em volta de um laço com `await` fica ativo na thread do event loop enquanto
    ela atende OUTRAS corrotinas, e todo handler que decifrasse PII no intervalo
    teria a entrada de auditoria dele capturada no nosso buffer, publicada só no
    nosso `__exit__` e perdida em silêncio se o nosso flush falhasse.

    `email_enc` manda quando existe; o `email` em claro é o caso legado. Nunca
    cai de um para o outro: `email_enc` que existe e não decifra é problema de
    CHAVE, e a exceção sobe para o `except` do chamador (§ o `try` de lá é o que
    isola a linha ruim do resto do lote).
    """
    if linha.get("email_enc"):
        return decrypt_pii_optional(
            linha["email_enc"],
            ctx=PiiAccessContext(
                purpose="send_payment_reminder_email",
                actor="system:engagement",
                subject_user_id=user_id,
                field="email",
            ),
        )
    return linha.get("email")


def _pago_por_outro_caminho(user_id: int) -> bool:
    """True se um grant `pix` ou `admin` vigente sustenta o acesso desta conta
    independentemente do cartão. `legacy` NÃO conta — ele é a reconstrução do
    MESMO acesso de cartão feita pelo backfill de grants."""
    from core.services.billing_access import grant_vigente
    from db.plan_grants import list_grants
    return grant_vigente(list_grants(user_id), datetime.now(timezone.utc),
                         sources=("pix", "admin"))
