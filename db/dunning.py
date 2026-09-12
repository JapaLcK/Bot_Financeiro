"""
db/dunning.py — o relógio da inadimplência de cartão (`auth_accounts.past_due_since`).

Camada de banco de UM assunto: a PRIMEIRA falha de cobrança do ciclo. O
vocabulário (lista de status, janela, largura) mora em
`core/services/billing_dunning`; quem lê o funil é
`core/services/payment_reminder`; quem escreve são os ramos de cobrança do
webhook do Stripe.

**Desde o corte do Grátis, o acesso DEPENDE desta coluna — e só numa direção.**
`core.services.billing_dunning.carencia_aberta` a lê no lado DIREITO do OR de
`plan_service.tem_direito_hoje`, que `has_app_access` consulta. O relógio só
CONCEDE tempo a quem já perdeu o direito pago; ele NUNCA tira acesso de
ninguém. Quem inverter essa direção bloqueia cliente pagante por um ciclo
inteiro de retentativa (célula 29). Esta linha dizia "NADA de acesso depende
desta coluna" e passou a mentir no PR do corte.

**A máquina inteira — estados × eventos, com o que cada célula faz hoje e o que
deveria fazer — está em `docs/dunning_estados_eventos.md`.** Leia antes de
mexer em qualquer writer deste arquivo ou dos ramos que o chamam: rodadas
seguidas de revisão consertaram transições isoladas desta mesma máquina, e a
tabela existe para a próxima não repetir o método. A contagem de rodadas não
vem escrita aqui de propósito — ela sobe, e envelhece em silêncio (§2); quem
apontou o quê está na tabela do fim daquele arquivo.

Saiu de `db/plans.py` (que estava em 350/350, o teto de
`tests/test_max_lines_python.py`) quando o predicado de status entrou no
`claim`. É separação por assunto, não por tamanho: `plans.py` é trial e escada
de planos, e estas funções são cobrança. Importe daqui — não há
re-export em `db/plans.py`, de propósito (§0.7).
"""

from __future__ import annotations

import logging

from .connection import get_conn

logger = logging.getLogger(__name__)


def claim_past_due_since(user_id: int) -> bool:
    """Carimba o início da inadimplência. True se foi ESTA chamada que carimbou.

    A idempotência é SQL, não Python (decisão do dono): UMA instrução com
    `past_due_since is null` no `where`, sem read-modify-write. A Stripe manda
    um `invoice.payment_failed` por smart retry, e reentrega o mesmo evento em
    cima de 5xx — o relógio NÃO pode reiniciar em nenhum dos dois casos.

    **O `where` também exige o status ATUAL em `PAST_DUE_PAYMENT_STATUSES`, e
    isso é a INVARIANTE mantida na escrita, não código defensivo.** Sem o
    predicado, o carimbo era incondicional e criava o órfão que a invariante
    declara impossível: `invoice.payment_failed` e `invoice.paid` são duas
    requisições, cada uma com o próprio `set_payment_status`, e há um `await`
    entre o `set_payment_status(past_due)` do ramo falho e este UPDATE (ele roda
    em `asyncio.to_thread`). No intervalo, o ramo pago escreve `active` e zera o
    relógio — e o UPDATE incondicional o repunha com o status já fora da lista.
    O guard de `Subscription.retrieve` do ramo falho NÃO fecha isso: ele roda
    ANTES do `set_payment_status`, então a corrida entre os nossos dois handlers
    continua aberta depois dele. A normalização é a mesma de
    `list_payment_reminder_candidates` (`lower(coalesce(...))`) e a lista vem da
    constante, nunca de literal (§0.7).

    O `rowcount` sai de graça e diz se foi esta chamada que abriu o ciclo (um
    `coalesce` no `set` seria idempotente também, mas o `RETURNING` veria o
    valor novo e não diria isso). **NÃO o use como SUPRESSOR de e-mail**: ele já
    foi a chave do "seu pagamento falhou" e o carimbo COMMITA antes do envio,
    então SMTP fora do ar na 1ª entrega calava o ciclo inteiro (medido: 1ª
    entrega + 3 reentregas da Stripe = 0 e-mails). Quem suprime é o `_fire_email`
    do webhook, que grava a chave DEPOIS de o envio confirmar; ele usa este
    `rowcount` só para AMPLIAR (ciclo novo → manda mesmo dentro da janela de
    dedupe), nunca para calar.

    Com o predicado novo, `rowcount 0` passa a ter DOIS significados — "já
    carimbado" e "status não elegível" — e o uso no webhook continua correto
    justamente porque ele só AMPLIA: os dois casos caem em
    `dedup_days=DUNNING_GRACE_DAYS`, que é a janela normal, e não em "não
    mande". No caso novo (a corrida acima) isso é o comportamento desejado por
    si: quem acabou de pagar não precisa de um "sua cobrança falhou" com
    `dedup_days=0`.
    """
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = now()"
                " where user_id = %s and past_due_since is null"
                "   and lower(coalesce(last_payment_status, '')) = any(%s)",
                (int(user_id), list(PAST_DUE_PAYMENT_STATUSES)),
            )
            carimbou = cur.rowcount == 1
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    return carimbou


def clear_past_due_since(user_id: int, *, nao_mais_novo_que: int) -> None:
    """Zera o relógio: pagou, cancelou ou a assinatura morreu — ciclo fechado.

    **O limite mora AQUI, na escrita, e o parâmetro é obrigatório** — mesma
    forma do predicado de status do `claim` acima, e pela mesma razão. Uma
    versão anterior era incondicional e delegava ao chamador a pergunta "este
    evento tem autoridade?", respondida pelo retorno de
    `_materializar_assinatura` (False = evento VELHO). Aquele gate era
    insuficiente por construção, e a tabela de estados × eventos
    (`docs/dunning_estados_eventos.md`, célula E1/E2 nº 5) mostra por quê:
    `upsert_grant` devolve o `id` DE PROPÓSITO para versão IGUAL (reentrega do
    mesmo evento, e o irmão do mesmo segundo do §6.1), e o
    `invoice.payment_failed` que abre o ciclo NOVO não escreve grant, logo não
    avança a marca d'água de versão. Resultado: a reentrega de um `paid`
    ANTERIOR ao ciclo atual passava pelo gate e apagava o relógio que a falha
    nova tinha acabado de carimbar.

    A pergunta certa não é "este evento é válido?", é **"este evento é mais novo
    que o ciclo que estou apagando?"** — e ela se responde no `where`:
    `past_due_since <= to_timestamp(<created do evento>)`. Isso separa a
    reentrega VELHA (recusa) da reentrega DO MESMO CICLO (aceita — o 5xx que cai
    entre o grant e o clear, célula nº 6), que é o caso que um gate por "o
    upsert APLICOU?" quebraria. `nao_mais_novo_que` é epoch em SEGUNDOS, o mesmo
    vocabulário de `_event_version` do webhook. Relógio NULL faz o predicado
    valer NULL: zero linhas, que é o no-op correto.

    ponytail: o relógio é `now()` do NOSSO banco e o `created` é do Stripe. Se o
    `payment_failed` for processado com atraso MAIOR que o intervalo entre a
    falha e o pagamento, o clear legítimo vira no-op e o relógio sobrevive — ele
    some sozinho no próximo status fora da lista
    (`db_support.set_payment_status_impl`). Carimbar o relógio com o `created`
    do evento falho troca esse erro por um pior (falho reentregue com `created`
    de dias atrás nasce fora da janela do lembrete): a coluna ancora a JANELA
    além da ORDEM, e para a janela `now()` é a resposta certa. Ver a ressalva na
    tabela.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = null"
                " where user_id = %s and past_due_since <= to_timestamp(%s)",
                (int(user_id), int(nao_mais_novo_que)),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


def encerrar_ciclo_de_atraso(user_id: int) -> None:
    """Zera o relógio INCONDICIONALMENTE, porque o ciclo acabou de vez.

    Irmã de `clear_past_due_since`, e a diferença é a REGRA, não o caso: lá o
    predicado preserva um ciclo ainda recuperável por uma cobrança futura. **Num
    evento TERMINAL não há cobrança a recuperar, logo não há ciclo a preservar,
    e por construção não existe evento mais novo que queira o relógio de volta.**

    **SEM parâmetro de versão, e a ausência é o desenho**: `nao_mais_novo_que=None`
    foi RECUSADO — dar significado ao `None` transforma o valor que um descuido
    produz no valor que DESLIGA a proteção que o parâmetro obrigatório comprou.

    **NÃO escreve `last_payment_status`** (os writers continuam DOIS) e **UM
    call site**, prendido por `tests/test_dunning_encerramento_terminal.py`.

    A ordem no ramo (`set_payment_status('unpaid')` e só então esta), por que
    ela importa, e o que ela NÃO garante — o par não é atômico, e falha entre os
    dois UPDATEs deixa órfão, aberto de propósito porque erra para o lado que
    CONCEDE acesso e a reentrega limpa — estão na seção E4 de
    `docs/dunning_estados_eventos.md`, junto com a célula 32.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = null where user_id = %s",
                (int(user_id),),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


def ciclo_de_atraso_aberto(user_id: int) -> bool:
    """O ciclo de inadimplência ainda está aberto? Leitura DIRETA, no ponto do
    envio pelo canal de WHATSAPP (`core.services.payment_reminder_wa._wa_lembrete`).

    **É o PRIMEIRO TERMO de `lembrete_ainda_vale`, e a duplicação é
    DELIBERADA** — as duas perguntas não são a mesma. Aquele responde "o
    lembrete por E-MAIL ainda se sustenta?" e por isso soma o consentimento do
    canal de e-mail (`engagement_opt_out`) e devolve o endereço; numa query só
    porque são colunas da MESMA linha. Este responde só "o ciclo ainda está
    aberto?", que é a VERDADE DA MENSAGEM, comum aos dois canais.

    Gatear o WhatsApp pelo `engagement_opt_out` seria a imagem espelhada do erro
    que `tests/test_payment_reminder_consentimento.py::test_opt_out_de_whatsapp_nao_tira_o_email`
    existe para impedir: um canal decidindo pela preferência do OUTRO. E
    devolver `email`/`email_enc` puxaria PII em claro para um caminho que não
    tem uso para ela. O consentimento do canal de WhatsApp continua onde já
    estava, `db.reports.get_whatsapp_updates_opt_out`.

    **O nome é recuperado de propósito**: era assim que `lembrete_ainda_vale` se
    chamava antes de absorver o segundo termo.

    NÃO é `get_auth_user`, pela mesma razão de `lembrete_ainda_vale`: aquele tem
    cache de 10 s (`db_support._auth_user_cache`) e uma leitura cacheada pode
    mentir exatamente na corrida que esta função existe para pegar — aqui a
    corrida é a requisição HTTP do envio do e-mail, que roda entre a
    revalidação do lote e este ponto (célula nº 31 de
    `docs/dunning_estados_eventos.md`).
    """
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from auth_accounts"
                " where user_id = %s and past_due_since is not null"
                "   and lower(coalesce(last_payment_status, '')) = any(%s)",
                (int(user_id), list(PAST_DUE_PAYMENT_STATUSES)),
            )
            return cur.fetchone() is not None


def lembrete_ainda_vale(user_id: int) -> dict | None:
    """O lembrete de cobrança por E-MAIL ainda se sustenta? Leitura DIRETA, no
    ponto do envio. Devolve **o material de e-mail ATUAL** (`email`,
    `email_enc`) quando sim, e `None` quando não.

    Revalidação do `core/services/payment_reminder`: o funil é UM snapshot, o
    lote não tem `LIMIT`, e todo valor que vem dele pode ter envelhecido antes
    do dispatch. **DOIS termos, e eles respondem perguntas diferentes:**

    • **estado da inadimplência** (relógio + status, da mesma constante e com a
      mesma normalização do funil) — quem pagou no meio do lote receberia "a
      cobrança continua pendente", e-mail errado para cliente PAGANTE (célula
      nº 28 de `docs/dunning_estados_eventos.md`);
    • **consentimento do canal de e-mail** (`engagement_opt_out`) — quem
      desligou os e-mails de engajamento no meio do lote receberia um de todo
      jeito. É o MESMO predicado que o `where` do funil aplica no snapshot;
      aqui ele é reavaliado fresco.

    Uma query só para os dois: são colunas da MESMA linha de `auth_accounts`.

    **Antes chamava-se `ciclo_de_atraso_aberto` e tinha só o primeiro termo, e
    a docstring dizia "janela e opt-out não são o que torna a mensagem FALSA".**
    Isso valia para a JANELA e não para o opt-out, e os dois foram excluídos
    pelo mesmo argumento — o erro de tirar conclusão do caso examinado em vez
    da categoria (§2), na sua terceira aparição nestas mesmas vinte linhas. A
    janela continua FORA, e agora com a razão certa: revalidá-la faria um lote
    lento descartar lembrete legítimo, que é o erro oposto e pior. Opt-out não
    tem esse problema — se a pessoa desligou, não mandar é exatamente o certo.

    NÃO é `get_auth_user`: aquele tem cache de 10 s
    (`db_support._auth_user_cache`) e uma leitura cacheada pode mentir
    exatamente na corrida que esta função existe para pegar. E NÃO é um claim
    atômico: gravar a chave de dedupe antes do envio é o bug que a rodada 1
    consertou (`_fire_email` grava DEPOIS de o envio confirmar, de propósito).

    **Devolve o e-mail em vez de um `bool`, e isso fecha um terceiro valor de
    snapshot.** O endereço vinha decifrado do lote do funil, então trocar de
    e-mail durante o lote fazia o lembrete ir para o ANTIGO — e endereço que a
    pessoa REMOVEU da conta pode não ser mais dela (e-mail de trabalho de um
    emprego que ela deixou é o caso óbvio), o que transforma "entrega velha" em
    divulgação de situação de pagamento a TERCEIRO. Como esta função já lê a
    linha, as duas colunas a mais no `select` não custam query nova — mas o
    RESTO da mudança não é grátis: a decriptação passou de uma por candidato
    (em lote, uma escrita de auditoria) para uma por lembrete enviado (uma
    escrita cada), e acima de M ≈ 6,5 % de N isso custa mais tempo. A troca
    está medida em `core.services.payment_reminder._resolver_email`.

    Devolve o CIFRADO, não o claro: cripto de PII é assunto de `core/crypto` e
    da camada de serviço (`core.services.payment_reminder._resolver_email`),
    não da camada de banco.

    O consentimento do canal de WHATSAPP não está aqui de propósito: ele é lido
    no ponto de envio DELE, que é outro
    (`core.services.payment_reminder_wa._wa_lembrete`).
    """
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select email, email_enc from auth_accounts"
                " where user_id = %s and past_due_since is not null"
                "   and lower(coalesce(last_payment_status, '')) = any(%s)"
                "   and coalesce(engagement_opt_out, false) = false",
                (int(user_id), list(PAST_DUE_PAYMENT_STATUSES)),
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None


def list_payment_reminder_candidates(grace_days: int = 7) -> list[dict]:
    """Contas do LEMBRETE DE PAGAMENTO: cartão em atraso, a partir do 6º dia.

    Janela `[grace_days - 1, grace_days - 1 + PAYMENT_REMINDER_WINDOW_DAYS)` de
    idade de `past_due_since`. A largura é constante nomeada e não `1` porque a
    cadência real do tick é MAIOR que 24 h: o `sleep` de
    `run_engagement_loop` só começa depois de todo o trabalho de engajamento,
    trial, nudge e lembrete, e um restart ou uma falha operacional atrasam
    muito mais que isso. Com janela de exatamente 24 h, um tick antes de a
    conta entrar + o tick seguinte depois de ela sair = o único lembrete do
    ciclo nunca sai. A largura tem TETO: veja a invariante em
    `core/services/billing_dunning` (largura < janela de dedupe), que é o que
    impede o mesmo ciclo de receber dois lembretes.

    O funil grosso é SQL (status, relógio, e-mail, opt-out); o filtro fino que
    precisa de Python (allowlist, grant pix/admin) é do chamador, como em
    `list_trial_downsell_candidates`.

    **`whatsapp_updates_opt_out` NÃO aparece aqui — nem no `where`, nem no
    `select` — e as duas ausências são decisão separada.**

    Fora do `where`: este funil serve os DOIS canais e o `engagement_opt_out`
    dele é o opt-out do canal de E-MAIL, que é o caminho garantido. Quem
    desligou só o WhatsApp continua com direito ao aviso de cobrança por
    e-mail; filtrar o opt-out de WhatsApp aqui trocaria uma violação de
    consentimento por um erro PIOR — perder aviso legítimo de cobrança de quem
    nunca pediu para perdê-lo. Amarrado por
    `tests/test_payment_reminder_consentimento.py::test_opt_out_de_whatsapp_nao_tira_o_email`.

    Fora do `select`: uma versão anterior a trazia daqui "de graça" e o canal
    recebia o valor por parâmetro. Só que valor de SNAPSHOT envelhece — quem
    desligasse o canal durante o lote recebia mensagem de todo jeito —, então a
    leitura foi para o ponto de uso
    (`core.services.payment_reminder_wa._wa_lembrete`) e a coluna aqui virou
    morta. **Não a traga de volta para "economizar uma query"**: a economia é de
    uma leitura por lembrete ENVIADO e o preço é o gate de consentimento voltar
    a decidir com dado velho.
    """
    from core.services.billing_dunning import (
        PAST_DUE_PAYMENT_STATUSES,
        PAYMENT_REMINDER_WINDOW_DAYS,
    )
    inicio = int(grace_days) - 1
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, email, email_enc
                from auth_accounts
                where past_due_since is not null
                  and lower(coalesce(last_payment_status, '')) = any(%s)
                  and coalesce(engagement_opt_out, false) = false
                  and email is not null and email <> ''
                  and past_due_since <= now() - make_interval(days => %s)
                  and past_due_since >  now() - make_interval(days => %s)
                """,
                (list(PAST_DUE_PAYMENT_STATUSES),
                 inicio, inicio + PAYMENT_REMINDER_WINDOW_DAYS),
            )
            return [dict(r) for r in cur.fetchall() or []]
