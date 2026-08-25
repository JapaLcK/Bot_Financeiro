"""
db/pending.py — Ações pendentes de confirmação (ex: "apagar lançamento?").
"""
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .connection import get_conn
from .users import ensure_user


def advance_pending_action(user_id: int, action_type: str,
                           old_payload: dict, new_payload: dict | None,
                           minutes: int = 10,
                           new_action_type: str | None = None) -> bool:
    """Avança (ou apaga) a pendência SÓ SE ela ainda for `old_payload`.

    Compare-and-swap. Duas respostas do mesmo usuário podem ser processadas em
    paralelo: `adapters/discord/discord_bot.py:122` é um `on_message` async sem
    lock, e o `launch.py` sobe o Discord num processo separado do uvicorn — um
    usuário com as duas plataformas ligadas é alcançado pelos dois ao mesmo
    tempo. (O webhook do WhatsApp, sozinho, NÃO corre: `wa_app.py` enfileira em
    `_queue` e um `_worker_loop` único consome um payload por vez.)
    Sem isso as duas leem a mesma fila, registram o MESMO item e o segundo valor
    some. Aqui a segunda escrita não pega: o Postgres serializa o UPDATE na
    linha, a condição `payload = <o que eu li>` já não vale, `rowcount` volta 0 e
    quem chamou relê a fila e reavalia.

    Sem lock de propósito. Um `pg_advisory_xact_lock` numa conexão dedicada
    segura uma conexão do pool durante todo o trabalho: com o pool em 8, oito
    usuários simultâneos consomem o pool só em locks e o bot inteiro para.

    Devolve True se gravou, False se outra thread já tinha avançado. Gravar
    renova o prazo (`minutes`), como o `set_pending_action` que ela substitui —
    senão uma fila longa expiraria 10 min depois da PRIMEIRA pergunta, não da
    última resposta.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if new_payload is None:
                cur.execute(
                    "delete from pending_actions "
                    "where user_id = %s and action_type = %s and payload = %s",
                    (user_id, action_type, Jsonb(old_payload)),
                )
            else:
                cur.execute(
                    "update pending_actions "
                    "set action_type = %s, payload = %s, created_at = now(), "
                    "    expires_at = %s "
                    "where user_id = %s and action_type = %s and payload = %s",
                    (new_action_type or action_type,
                     Jsonb(new_payload),
                     datetime.now(timezone.utc) + timedelta(minutes=minutes),
                     user_id, action_type, Jsonb(old_payload)),
                )
            gravou = cur.rowcount == 1
        conn.commit()
    return gravou



def create_pending_action_if_absent(user_id: int, action_type: str, payload: dict,
                                    minutes: int = 10) -> bool:
    """Cria a pendência SÓ SE o usuário não tiver nenhuma. Devolve True se criou.

    Irmã do `advance_pending_action` para o caso "não havia linha". O
    `set_pending_action` faz upsert incondicional: duas devoluções simultâneas
    (dois itens reivindicados que estouraram, ex. os dois batendo o teto de
    plano) veem a fila vazia e cada uma grava a SUA — a última apaga a primeira
    e um item some. Aqui a segunda insere zero linhas, devolve False, e quem
    chamou relê e prepende na fila que a primeira acabou de criar.
    """
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pending_actions (user_id, action_type, payload, expires_at) "
                "values (%s, %s, %s, %s) on conflict (user_id) do nothing",
                (user_id, action_type, Jsonb(payload), expires_at),
            )
            criou = cur.rowcount == 1
        conn.commit()
    return criou


# ---------------------------------------------------------------------------
# ORDEM DE PRIORIDADE DA LINHA ÚNICA DE `pending_actions`
# ---------------------------------------------------------------------------
# `pending_actions` tem UMA linha por usuário (`on conflict (user_id)`), então
# todo fluxo que quer lembrar de algo disputa o mesmo espaço. Só existem dois
# degraus, porque só isso é preciso para decidir quem cede:
#
#   1. PERGUNTAS — o bot parou e espera uma resposta que ele NÃO consegue
#      reconstruir sozinho: quanto veio a conta, qual cartão, confirma apagar,
#      qual o valor do item da fila. Perder isso perde dinheiro ou trabalho já
#      feito pelo usuário. É tudo o que NÃO está na lista abaixo. Nunca é
#      desalojado: quem chega depois é que se vira sem estado.
#
#   2. OFERTAS DE CONVENIÊNCIA (a lista) — o bot já concluiu o trabalho e
#      anexou um BOTÃO à resposta: "categoria errada?", "quer desfazer?". Elas
#      são consumidas no mesmo turno em que nascem, pelo
#      `_send_reply_with_optional_buttons` (adapters/whatsapp/wa_runtime.py:
#      181-211), que faz `clear_pending_action` antes de enviar. Se ainda
#      estiverem de pé no turno seguinte é porque o botão não foi tocado —
#      ignorar é a resposta mais comum, e o usuário refaz pelo comando normal
#      ("muda a categoria do #12"). Pode ser desalojada por qualquer pergunta.
#
# `confirm_recurring_offer` ESTEVE nesta lista e não é oferta: o nome engana. O
# bot pergunta "isso virou gasto fixo? (sim ou não)" em TEXTO e ela NÃO é
# consumida pelo runtime — sobrevive para o turno seguinte de propósito, porque
# o "sim" é a resposta dela. Desalojando-a, o "sim" seguinte caía em "não
# entendi bem o que você quis fazer" e derrubava as DUAS pendências: o Spotify
# não virava gasto fixo e a pergunta da conta morria junto. É pergunta.
#
# Sem esse degrau, a gravação condicional recusava por causa de QUALQUER linha:
# um "gastei 50 no mercado" deixava a oferta de recategorizar de pé por 10 min
# e o "paguei a luz" seguinte já não conseguia guardar de qual conta falava.
_OFERTAS_DE_CONVENIENCIA: frozenset[str] = frozenset({
    "recategorize_launch_offer",
    "undo_audio",
})

# A MESMA pergunta ("quanto veio a conta este mês?") feita por DUAS portas: por
# texto/IA ela vira `bill_amount_expected` (core/handlers/bills.py,
# core/services/ai_chat/tools/bills.py) e pelo botão "✅ Já paguei" vira
# `bill_pay_amount` (adapters/whatsapp/wa_runtime.py). Os tipos são diferentes
# só porque cada porta tem o seu consumidor; para decidir quem cede a linha,
# contam como uma pergunta só.
#
# Sem isto o botão perdia o claim para a pergunta de texto de OUTRA conta: quem
# tinha dito "paguei a luz" e depois tocou "Já paguei" na ÁGUA via a pergunta da
# Água na tela, mandava "132,50" e pagava a LUZ. Vale a pergunta mais recente —
# é a que o usuário está lendo.
_PERGUNTA_DE_VALOR_DE_CONTA: frozenset[str] = frozenset({
    "bill_amount_expected",
    "bill_pay_amount",
})

# OBSERVAÇÃO (não unificado neste PR): "isto é pergunta?" está enumerado em
# TRÊS lugares, com conteúdos diferentes e nenhum deles importa o outro:
#   1. esta lista (pelo avesso: pergunta é o que NÃO está aqui);
#   2. `_RESUMABLE_PENDING_TYPES` (core/handle_incoming.py), que decide se a IA
#      é suprimida;
#   3. um literal inline em core/handle_incoming.py (~:357), que decide se o
#      `undo_audio` pode sobrescrever a pendência.
# Divergem: `bill_pay_amount` só existe na (3); `multi_launch_values` e
# `confirm_recurring_offer` só existem na (3). Cada nova pendência precisa ser
# lembrada nos três. Unificar é issue separada — as três perguntas são
# diferentes ("cede a linha?", "suprime a IA?", "pode sobrescrever?") e juntar
# sem enumerar estado × evento troca bug conhecido por bug novo.


def claim_pending_action(user_id: int, action_type: str, payload: dict,
                         minutes: int = 10) -> bool:
    """Arma uma PERGUNTA na linha do usuário. True se conseguiu.

    Linha livre → insere (condicional, para duas tarefas simultâneas não se
    apagarem). Ocupada por oferta de conveniência → desaloja, condicionado ao
    que foi lido, para não atropelar algo que chegou no meio. Ocupada por outra
    pergunta → devolve False, e quem chamou degrada para o texto que funciona
    sem estado.

    Mesmo desenho do `_devolve_head` (core/handlers/launches.py), com a
    diferença de que lá o CAS roda em laço: lá o que se perde é um lançamento
    do usuário, aqui é só o contexto de uma pergunta que tem texto de
    recuperação.
    """
    # ponytail: uma tentativa só. Perder a corrida cai no texto degradado, que
    # funciona; virar laço só se aparecer disputa de verdade nessa linha.
    atual = get_pending_action(user_id)
    if atual is None:
        return create_pending_action_if_absent(user_id, action_type, payload, minutes)
    # A MESMA pergunta de novo (tocou "Já paguei" na conta A e depois na B;
    # disse "paguei a luz" e depois "paguei a água"; disse "paguei a luz" e
    # depois tocou "Já paguei" na água) não é disputa: a primeira já morreu na
    # tela do usuário e a segunda é o que ele acabou de pedir. "Mesma pergunta"
    # é por tipo igual OU pelo grupo das duas portas acima.
    # Continua CAS — quem perder a corrida cai no texto degradado.
    mesma_pergunta = (
        atual["action_type"] == action_type
        or (atual["action_type"] in _PERGUNTA_DE_VALOR_DE_CONTA
            and action_type in _PERGUNTA_DE_VALOR_DE_CONTA)
    )
    if atual["action_type"] not in _OFERTAS_DE_CONVENIENCIA and not mesma_pergunta:
        return False
    return advance_pending_action(
        user_id, atual["action_type"], atual.get("payload") or {},
        payload, minutes, new_action_type=action_type,
    )


def set_pending_action(user_id: int, action_type: str, payload: dict, minutes: int = 10):
    """Cria/atualiza uma ação pendente de confirmação (persistente no Postgres)."""
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into pending_actions (user_id, action_type, payload, expires_at)
                values (%s, %s, %s, %s)
                on conflict (user_id)
                do update set action_type = excluded.action_type,
                              payload = excluded.payload,
                              created_at = now(),
                              expires_at = excluded.expires_at
                """,
                (user_id, action_type, Jsonb(payload), expires_at),
            )
        conn.commit()


def get_pending_action(user_id: int):
    """Retorna a ação pendente se existir e não estiver expirada. Senão None."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select user_id, action_type, payload, created_at, expires_at "
                "from pending_actions where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return None

    if row["expires_at"] <= datetime.now(timezone.utc):
        clear_pending_action(user_id)
        return None

    return row


def clear_pending_action(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
        conn.commit()
