"""
db/pending.py — Ações pendentes de confirmação (ex: "apagar lançamento?").
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .connection import commits_ambiguos, get_conn
from .users import ensure_user


def advance_pending_action(user_id: int, action_type: str,
                           old_payload: dict, new_payload: dict | None,
                           minutes: int = 10,
                           new_action_type: str | None = None,
                           old_created_at: datetime | None = None) -> bool:
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

    `old_created_at` fecha o ABA. Só `(action_type, payload)` identifica o
    CONTEÚDO da linha, não a instância: se outra tarefa consome a pendência e o
    usuário repete o MESMO comando, nasce uma linha nova de conteúdo idêntico e
    o CAS de quem estava atrasado passa — executando a ação duas vezes, que é
    exatamente o que ele existe para impedir. `created_at` é reescrito a cada
    gravação (`set_pending_action`, o UPDATE abaixo, o default do INSERT), então
    serve de versão da linha. Passe sempre que tiver a linha lida em mãos.
    """
    versao = "" if old_created_at is None else " and created_at = %s"
    extra: tuple = () if old_created_at is None else (old_created_at,)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if new_payload is None:
                cur.execute(
                    "delete from pending_actions "
                    "where user_id = %s and action_type = %s and payload = %s"
                    + versao,
                    (user_id, action_type, Jsonb(old_payload)) + extra,
                )
            else:
                cur.execute(
                    "update pending_actions "
                    "set action_type = %s, payload = %s, created_at = now(), "
                    "    expires_at = %s "
                    "where user_id = %s and action_type = %s and payload = %s"
                    + versao,
                    (new_action_type or action_type,
                     Jsonb(new_payload),
                     datetime.now(timezone.utc) + timedelta(minutes=minutes),
                     user_id, action_type, Jsonb(old_payload)) + extra,
                )
            gravou = cur.rowcount == 1
        conn.commit()
    return gravou



def consume_pending_action(user_id: int, pending: dict) -> bool:
    """Apaga a pendência SÓ SE ela ainda for a que você leu. True se apagou.

    Atalho do `advance_pending_action(..., None)` para o caso mais comum: quem
    consome tem a linha em mãos e quer apagar *aquela*, não "o que estiver lá".
    `clear_pending_action` apaga incondicionalmente — se outra tarefa (Discord,
    ou a outra plataforma do mesmo usuário) armou uma pergunta nova no
    meio-tempo, ela some e o usuário fica com uma pergunta na tela cuja resposta
    já não resolve nada.

    False significa "a linha que eu li não está mais lá". Quem perde:

    - **dinheiro ou destrutivo** (pagar, apagar, registrar) — NÃO executa. O
      False é o porteiro: sem ele as duas tarefas executam a mesma ação.
    - **abandono / limpeza de estado** — segue e ignora o resultado: se perdeu,
      não havia nada seu para abandonar.
    """
    return advance_pending_action(
        user_id,
        pending.get("action_type") or "",
        pending.get("payload") or {},
        None,
        old_created_at=pending.get("created_at"),
    )


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


@contextmanager
def restore_pending_on_error(user_id: int, pending: dict, minutes: int = 10):
    """Devolve a pendência se o trabalho reivindicado estourar, e re-levanta.

    `consume_pending_action` roda ANTES do trabalho (é o porteiro que impede a
    segunda thread de executar a mesma ação). Se o trabalho levanta, a pergunta
    já foi apagada: o usuário perde a confirmação, não recebe nada útil e refaz
    o fluxo do zero — regressão contra o `clear` que vinha DEPOIS.

    A devolução é `create_pending_action_if_absent`, nunca `set_pending_action`:
    entre a reivindicação e a falha outra tarefa pode ter armado uma pergunta
    nova, que já apareceu na tela do usuário. Gravar por cima a deixaria órfã —
    uma operação antiga não sobrescreve uma pendência mais nova. Se já há algo
    lá, o que se perde é a pendência desta operação, recuperável repetindo o
    comando.

    `minutes` é o prazo do fluxo que armou a pergunta (crédito usa 20, o valor
    de conta do WhatsApp usa 30) — devolver com o default encurtaria o prazo.

    NÃO devolve se algum `commit()` chegou a estourar no meio (`commits_ambiguos`):
    a conexão que cai ENQUANTO o Postgres confirma o COMMIT levanta com a
    transação possivelmente gravada, e de fora isso é idêntico a uma falha antes
    dela. Devolver a pergunta ali faz a próxima resposta REPETIR um trabalho já
    commitado — um aporte debita a origem e credita o destino duas vezes, sem
    chave de idempotência que segure (Codex, PR #144). Errar para o lado de não
    devolver custa a pergunta, que o usuário refaz repetindo o comando; errar
    para o outro custa dinheiro, que não tem retentativa.

    A fronteira NÃO é o `return` da função transacional: é a primeira tentativa
    de commit. Por isso a checagem é genérica (vale para os 8 sites e para
    qualquer commit no caminho, inclusive o implícito do `with get_conn()`) em
    vez de uma lista de exceções "seguras" por site.
    """
    antes = commits_ambiguos()
    try:
        yield
    except Exception:
        if commits_ambiguos() == antes:
            create_pending_action_if_absent(
                user_id,
                pending.get("action_type") or "",
                pending.get("payload") or {},
                minutes,
            )
        raise


# ---------------------------------------------------------------------------
# REGISTRO DE PENDÊNCIAS — a fonte de verdade dos três predicados
# ---------------------------------------------------------------------------
# `pending_actions` tem UMA linha por usuário (`on conflict (user_id)`), então
# todo fluxo que quer lembrar de algo disputa o mesmo espaço. Três decisões
# diferentes dependem do TIPO da pendência, e até a issue #136 elas viviam em
# três listas separadas que ninguém importava da outra — cada pendência nova
# precisava ser lembrada nos três lugares, e esquecer um era silencioso.
#
# A tabela abaixo é a única lista. As três perguntas continuam SENDO TRÊS (elas
# divergem de propósito — ver o que cada coluna significa); o que mudou é que se
# responde as três no mesmo lugar, uma linha por tipo.
#
# ── as três colunas, e o teste OBSERVÁVEL de cada uma ──────────────────────
#
# `oferta` — pode ser desalojada por uma pergunta?
#     É oferta SÓ SE o `_send_reply_with_optional_buttons`
#     (adapters/whatsapp/wa_runtime.py) a consome no MESMO turno em que ela
#     nasce: o bot já concluiu o trabalho e anexou um botão à resposta
#     ("categoria errada?", "quer desfazer?"). Se ainda estiver de pé no turno
#     seguinte é porque o botão não foi tocado — ignorar é a resposta mais
#     comum, e o usuário refaz pelo comando normal.
#     Se ela ESPERA resposta do usuário, é PERGUNTA — mesmo tendo "offer" no
#     nome. `confirm_recurring_offer` esteve marcada como oferta e não é: o bot
#     pergunta "isso virou gasto fixo?" em TEXTO e o "sim" seguinte é a resposta
#     dela. Desalojando-a, o "sim" caía em "não entendi" e derrubava DUAS
#     pendências de uma vez.
#     Marcar uma PERGUNTA como oferta perde o estado sem aviso; marcar uma
#     OFERTA como pergunta faz ela bloquear a linha por 10 min — foi o que
#     acontecia com `delete_credit_purchase` (medido: com a oferta de pé, o
#     `claim_pending_action` de uma pergunta devolvia False).
#
# `suprime_ia` — enquanto está de pé, o fallback de IA fica desligado?
#     Sim SÓ SE a resposta natural do usuário chega até o `handle_incoming` E o
#     classificador não a reconhece. Um número solto ("1200", "132,50") ou um
#     substantivo ("cinema") sai como `out_of_scope`/baixa confiança, e o
#     fallback de IA roda ANTES do `route()` — então sem esta coluna a IA
#     sequestra a resposta da própria pergunta que o bot fez. É o bug da #132.
#     Fica FORA quem é consumida pelo runtime do WhatsApp ANTES do
#     `handle_incoming` (`bill_pay_amount`, `recategorize_launch_text`):
#     incluí-las desligaria a IA do usuário Pro sem motivo.
#     Fica FORA também quem é respondida com "sim"/"não", que o classificador
#     reconhece como `confirm.yes`/`confirm.no` com confiança alta.
#     Suprime SEMPRE, sem tentar adivinhar antes se a mensagem "parece" uma
#     resposta: medido no #133, o refinamento salvava 1 mudança de assunto em 5
#     (as outras 4 voltam pra IA pelo fallback pós-route) e, em troca, mandava
#     pra IA as 5 formas faladas de responder o valor.
#
# `sobrevive_audio` — um áudio no meio NÃO pode sobrescrevê-la?
#     Sim quando perder a pendência custa trabalho já feito pelo usuário. As
#     confirmações destrutivas ficam de FORA de propósito: perdê-las é
#     fail-safe (o delete não acontece), e protegê-las reintroduziria o footgun
#     "apagar #285" → [áudio] → "sim" apaga #285 — o guard anti-órfão do
#     `intent_router` só dispara com comando de TEXTO, não com áudio.
#
# GAPS CONHECIDOS, deixados como estão para não misturar conserto de
# comportamento com a unificação (issue #136, fatia 2):
#   - `confirm_media_launch` e `recategorize_launch_text` são perguntas com
#     `sobrevive_audio=False`: um áudio no meio apaga a confirmação da nota
#     fiscal escaneada / a categoria que o usuário ia digitar.
#
# Tipo AUSENTE da tabela = as três colunas False, que é exatamente o
# comportamento de hoje para um tipo não listado. Nada muda em silêncio — e o
# `tests/test_pending_registry.py` reprova qualquer tipo novo que o código grave
# sem passar por aqui.
class _Pendencia(NamedTuple):
    oferta: bool
    suprime_ia: bool
    sobrevive_audio: bool


_AUSENTE = _Pendencia(False, False, False)


_REGISTRO: dict[str, _Pendencia] = {
    #                                          oferta suprime_ia sobrevive_audio
    # ── perguntas retomadas pelo route() ──────────────────────────────────
    "clarification":             _Pendencia(   False,   True,      True),
    "credit_card_setup":         _Pendencia(   False,   True,      True),
    "credit_card_set_primary":   _Pendencia(   False,   True,      True),
    "credit_delete_card":        _Pendencia(   False,   True,      True),
    "installment_pending":       _Pendencia(   False,   True,      True),
    "pay_bill_choice":           _Pendencia(   False,   True,      True),
    "funding_source_choice":     _Pendencia(   False,   True,      True),
    "investment_pick":           _Pendencia(   False,   True,      True),
    "bill_amount_expected":      _Pendencia(   False,   True,      True),
    # "quanto foi *aluguel*?" — a resposta é um número solto, igualzinho à
    # conta variável. Estava fora do `suprime_ia` e a IA do usuário Pro
    # sequestrava a resposta (medido: a IA recebia "1200" e o item da fila
    # ficava sem valor). Já estava na lista do áudio, o que mostra que a
    # ausência era esquecimento, não decisão.
    "multi_launch_values":       _Pendencia(   False,   True,      True),

    # ── perguntas respondidas com "sim"/"não" (classificador reconhece) ───
    "confirm_recurring_offer":   _Pendencia(   False,   False,     True),
    "confirm_media_launch":      _Pendencia(   False,   False,     False),
    "delete_launch":             _Pendencia(   False,   False,     False),
    "delete_launch_bulk":        _Pendencia(   False,   False,     False),
    "delete_pocket":             _Pendencia(   False,   False,     False),
    "delete_investment":         _Pendencia(   False,   False,     False),

    # ── perguntas consumidas pelo runtime do WhatsApp antes do handle_incoming
    "bill_pay_amount":           _Pendencia(   False,   False,     True),
    "recategorize_launch_text":  _Pendencia(   False,   False,     False),

    # ── ofertas de conveniência (botão anexado, consumido no mesmo turno) ─
    "recategorize_launch_offer": _Pendencia(   True,    False,     False),
    "undo_audio":                _Pendencia(   True,    False,     False),
    # O botão "🗑️ Apagar" pós-compra no crédito. É consumida pelo
    # `_send_reply_with_optional_buttons` no mesmo turno, como as duas acima —
    # mas estava fora da lista de ofertas, então uma que ficasse de pé (botão
    # não tocado) bloqueava QUALQUER pergunta nova por 10 minutos.
    "delete_credit_purchase":    _Pendencia(   True,    False,     False),
}


def eh_oferta_de_conveniencia(action_type: str | None) -> bool:
    """Pode ser desalojada por uma pergunta? Ver a coluna `oferta` acima."""
    return bool(action_type and _REGISTRO.get(action_type, _AUSENTE).oferta)


def suprime_fallback_de_ia(action_type: str | None) -> bool:
    """Desliga o fallback de IA enquanto está de pé? Coluna `suprime_ia`."""
    return bool(action_type and _REGISTRO.get(action_type, _AUSENTE).suprime_ia)


def sobrevive_a_audio(action_type: str | None) -> bool:
    """Um áudio no meio NÃO pode sobrescrevê-la? Coluna `sobrevive_audio`."""
    return bool(action_type and _REGISTRO.get(action_type, _AUSENTE).sobrevive_audio)


# A MESMA pergunta ("quanto veio a conta este mês?") feita por DUAS portas: por
# texto/IA ela vira `bill_amount_expected` (core/handlers/bills.py,
# core/services/ai_chat/tools/bills.py) e pelo botão "✅ Já paguei" vira
# `bill_pay_amount` (adapters/whatsapp/wa_runtime.py). Os tipos são diferentes
# só porque cada porta tem o seu consumidor; para decidir quem cede a linha,
# contam como uma pergunta só. Não é uma quarta coluna: é um grupo de
# equivalência entre dois tipos, não um predicado sobre um tipo.
#
# Sem isto o botão perdia o claim para a pergunta de texto de OUTRA conta: quem
# tinha dito "paguei a luz" e depois tocou "Já paguei" na ÁGUA via a pergunta da
# Água na tela, mandava "132,50" e pagava a LUZ. Vale a pergunta mais recente —
# é a que o usuário está lendo.
_PERGUNTA_DE_VALOR_DE_CONTA: frozenset[str] = frozenset({
    "bill_amount_expected",
    "bill_pay_amount",
})


def claim_pending_action(user_id: int, action_type: str, payload: dict,
                         minutes: int = 10) -> bool:
    """Arma uma PERGUNTA na linha do usuário. True se conseguiu.

    Linha livre → insere (condicional, para duas tarefas simultâneas não se
    apagarem). Ocupada por oferta de conveniência → desaloja, condicionado ao
    que foi lido, para não atropelar algo que chegou no meio. Ocupada por outra
    pergunta → devolve False, e quem chamou degrada para um texto que pede
    para terminar a pergunta viva primeiro (a forma completa "paguei luz
    132,50" NÃO funciona nesse estado — ver
    `core/handlers/bills.py:pergunta_de_valor_sem_contexto`).

    Mesmo desenho do `_devolve_head` (core/handlers/launches.py), com a
    diferença de que lá o CAS roda em laço: lá o que se perde é um lançamento
    do usuário, aqui é só o contexto de uma pergunta que tem texto de
    recuperação.
    """
    # ponytail: uma tentativa só. Perder a corrida cai no texto degradado (que
    # não perde dinheiro, só pede para terminar a pergunta viva); virar laço só
    # se aparecer disputa de verdade nessa linha.
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
    if not eh_oferta_de_conveniencia(atual["action_type"]) and not mesma_pergunta:
        return False
    return advance_pending_action(
        user_id, atual["action_type"], atual.get("payload") or {},
        payload, minutes, new_action_type=action_type,
        old_created_at=atual.get("created_at"),
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
        # Condicional no prazo, não `clear`: entre esta leitura e o delete outra
        # tarefa pode ter armado uma pendência NOVA (prazo novo). Duas leituras
        # simultâneas da linha vencida se atropelavam — a segunda apagava a
        # pendência que a primeira tinha acabado de criar.
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from pending_actions "
                    "where user_id = %s and expires_at <= now()",
                    (user_id,),
                )
            conn.commit()
        return None

    return row


def clear_pending_action(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
        conn.commit()
