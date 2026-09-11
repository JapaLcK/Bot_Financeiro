"""
db/webhook_outbox.py — a outbox do webhook do Asaas.

Uma tabela, `pix_webhook_events`, e nenhuma mensageria: sem broker, sem DLQ, sem
backoff configurável (§3.4 do plano). O handler grava aqui e responde 200; quem
executa é o dreno.

O registro de efeitos (`pix_payment_effects`) mora em `db/pix_effects.py` desde
o 1b-B — dois assuntos, duas tabelas, dois arquivos.

Plano: docs/plano_pix_anual_asaas.md §3.3, §3.4, §8.1, §8.2 e §13.3.

**Deixou de ser inerte no 1b-B**: o handler (`frontend/routes/billing_pix.py`)
grava, o dreno (`core/services/pix_drain.py`) lê e a varredura purga.

# ponytail: outbox por varredura em loop, não fila. Generalizar só quando
# houver um SEGUNDO produtor de eventos — com um, broker é infraestrutura
# para problema que não existe.

O que este arquivo NÃO tem, e o corte é deliberado:

  • **a leitura do `payload_enc`**. Decifrar exige um `PiiAccessContext`, e o
    `subject_user_id` dele só existe DEPOIS de casar o evento com a cobrança — que é
    trabalho do dreno. Inventar um sujeito aqui (0, -1) gravaria linha falsa em
    `pii_access_log`, que é registro de compliance. `reservar_evento` entrega o
    `payload_enc` CIFRADO e para aí.
  • **as outras duas constantes de retenção**. `RETENCAO_OUTBOX_DIAS` está aqui porque a
    varredura que o consome está aqui (§13.3); as de `pix_charges` moram em
    `db/pix_charges_saga.py`, e o plano congelou `RETENCAO_PAGAMENTO_DIAS` até
    validação jurídica (§13.1).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from core.crypto import encrypt_pii_optional

from .connection import get_conn

# Retenção da outbox (§13.1/§13.3): 7 dias a partir de `received_at`, processado
# ou não. Mora aqui, e não em `db/pix_charges.py`, porque constante mora com a
# query que a consome — `purgar_payloads_antigos`, logo abaixo.
RETENCAO_OUTBOX_DIAS = 7

# MINIMIZAÇÃO NA ESCRITA (§13.3): o resto do corpo do webhook é descartado
# ANTES do insert, não depois. O que sobra é o que o dreno precisa para casar o
# evento com a cobrança, decidir a transição e conferir o valor.
#
# É allowlist e não denylist de propósito: campo novo que o Asaas passe a mandar
# — um endereço, um telefone do titular — fica de fora sozinho. Uma denylist
# precisaria que alguém previsse o nome dele.
CAMPOS_MINIMOS: frozenset[str] = frozenset({
    "id",                   # o payment.id, estável entre eventos irmãos
    "externalReference",    # "pix:<id>", o que casa com a nossa linha
    "value",
    "netValue",
    "status",
    "dateCreated",
    "customer",             # id do cliente no Asaas, não o nome nem o CPF
})

# Tipos que podem ser guardados como VALOR de um campo permitido. A allowlist de CHAVES
# não basta: `customer` é permitido e o Asaas pode mandá-lo **expandido** —
# `{"id": …, "name": …, "cpfCnpj": …, "email": …}` —, e filtrar só o primeiro nível
# copiaria o objeto inteiro, com o CPF dentro de um campo cujo nome está na lista.
# Medido em três formas: `customer` expandido, dict em `status`, lista sob `value`.
_ESCALARES = (str, int, float, bool, type(None))

# Teto por campo. Nenhum dos nove campos do §13.3 é texto livre: o maior é o
# `externalReference` (`pix:<id>`), com menos de 30 chars, então 200 é folga de quase 7×.
# Ele impede o campo permitido virar CARGA — a filtragem por forma barrava a ESTRUTURA e
# deixava passar escalar de qualquer tamanho: 1 MB numa string, cifrado, na tabela,
# medido. Truncar bate descartar: o começo de um valor esquisito serve para depurar.
LIMITE_POR_CAMPO = 200


def _valor_seguro(valor):
    """Escalar passa (truncado); estrutura é reduzida ao `id` que serve de chave.

    A regra é de FORMA, não de nome: não depende de alguém prever qual campo o
    provedor vai expandir da próxima vez. Um dict com `id` vira o `id` (é o que
    o dreno usa para casar); qualquer outra estrutura vira `None`, e o campo
    fica registrado como presente-mas-descartado.
    """
    # O teto é sobre o TAMANHO SERIALIZADO, não sobre o tipo `str`: a versão anterior só
    # truncava `str` e um `int` gigante passava inteiro — medido, 4.351 bytes na coluna
    # (o teto real era o `json.loads` do CPython estourando antes, o que não é política
    # de retenção). `bool` e `None` saem antes de propósito: `bool` é subclasse de `int`,
    # e `str(True)` viraria a string "True", trocando o tipo de um campo curto.
    if valor is None or isinstance(valor, bool):
        return valor
    if isinstance(valor, (str, int, float)):
        texto = valor if isinstance(valor, str) else str(valor)
        # Devolve o valor ORIGINAL quando cabe: um `value` de 299.0 continua
        # float, e o dreno confere centavos sem reparsear string.
        return texto[:LIMITE_POR_CAMPO] if len(texto) > LIMITE_POR_CAMPO else valor
    if isinstance(valor, dict):
        alvo = valor.get("id")
        return _valor_seguro(alvo) if isinstance(alvo, _ESCALARES) else None
    return None


def minimizar(corpo: dict) -> dict:
    """FUNÇÃO PURA: corpo cru do webhook → o subconjunto que pode ser guardado.

    Mantém `event`/`id` do envelope e, de `payment`, só `CAMPOS_MINIMOS` — e de cada um
    só o VALOR ESCALAR (ver `_valor_seguro`). Chave permitida com objeto dentro é o furo
    que uma allowlist rasa não vê.

    Um `payment` ausente ou de outro tipo vira dict vazio em vez de estourar: o handler
    já respondeu 400 para corpo sem id de evento (§8.1), e o resto do formato é do
    provedor, não nosso.
    """
    pagamento = corpo.get("payment")
    if not isinstance(pagamento, dict):
        pagamento = {}
    return {
        "event": _valor_seguro(corpo.get("event")),
        "id": _valor_seguro(corpo.get("id")),
        "payment": {k: _valor_seguro(v) for k, v in pagamento.items()
                    if k in CAMPOS_MINIMOS},
    }


def registrar_evento(event_id: str, event_type: str, corpo: dict,
                     event_version: int) -> bool:
    """Grava o evento na outbox. Devolve **True** se a linha é nova, **False** na duplicata.

    É o `insert … on conflict (event_id) do nothing returning` do §8.1: o Asaas reentrega,
    e o 200 da duplicata não pode custar trabalho nenhum. O `returning` vazio é a
    resposta — não há `select` antes, que teria a corrida entre duas entregas simultâneas
    do mesmo evento.

    O payload é **minimizado e depois cifrado** (§13.3). A ordem importa: cifrar primeiro
    e minimizar depois guardaria o dado pessoal cifrado no banco, que é exatamente o que
    a purga de 7 dias tenta desfazer.
    """
    payload_enc = encrypt_pii_optional(
        json.dumps(minimizar(corpo), ensure_ascii=False, sort_keys=True, default=str)
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_webhook_events"
                "  (event_id, event_type, payload_enc, event_version)"
                " values (%s, %s, %s, %s)"
                " on conflict (event_id) do nothing"
                " returning event_id",
                (event_id, event_type, payload_enc, int(event_version)),
            )
            novo = cur.fetchone() is not None
        conn.commit()
    return novo


@contextmanager
def reservar_evento(event_id: str):
    """Reserva o evento para UMA passada do dreno. Rende a linha, ou `None`.

    É o `select … and processed_at is null for update skip locked` do §8.2.
    **Context manager, e não função, porque `for update` só vale enquanto a transação
    viver**: devolver a linha e fechar a conexão soltaria o lock no `return` e o
    `skip locked` viraria decoração. Quem drena roda DENTRO do `with`.

    `None` rende para evento inexistente, já processado ou travado por outra passada —
    o chamador não precisa distinguir, a ação é a mesma. O `payload_enc` sai
    **cifrado**: decifrar é do dreno, que é quem tem o `subject_user_id` do
    `PiiAccessContext` (ver o cabeçalho).

    `ponytail:` teto — a transação fica aberta pelo dreno inteiro, chamadas externas
    incluídas: uma conexão do pool presa por evento. Com a vazão de uma venda anual
    isso não chega perto do teto do pool; se chegar, a saída é reservar, soltar e
    reconferir `processed_at` antes de cada efeito.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select event_id, event_type, payload_enc, event_version,"
                "       attempts, received_at, purged_at"
                "  from pix_webhook_events"
                " where event_id = %s and processed_at is null"
                " for update skip locked",
                (event_id,),
            )
            row = cur.fetchone()
        yield row


def purgar_payloads_antigos() -> int:
    """Zera `payload_enc` **e** `last_error` dos eventos com mais de
    `RETENCAO_OUTBOX_DIAS`; devolve quantas linhas foram purgadas.

    §13.3, e as três cláusulas são o conteúdo — nenhuma é zelo:
      • **conta de `received_at`, processado ou não** (correção nº 10): contar de
        `processed_at` deixaria o evento TRAVADO — que é justamente o órfão, de quem
        pediu exclusão da conta — guardando PII para sempre;
      • **`last_error = null` na MESMA operação**, porque quem escreve nessa coluna é um
        filtro de FORMA (`_erro_seguro`) e forma não separa nome de código: `'Fulano'` e
        `'joao_silva'` atravessam inteiros;
      • **`purged_at is null`** — sem ela o carimbo seria reescrito todo dia.

    **Sem `user_id` no `where`, e não é esquecimento:** a tabela não tem a coluna nem FK,
    e o único vínculo com o titular mora dentro do `payload_enc`, cifrado com Fernet
    não-determinístico. Copiar o predicado da varredura vizinha do §13.2 (`user_id is
    null and purged_at is null`) dá erro de coluna inexistente.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events"
                "   set payload_enc = null, last_error = null, purged_at = now()"
                " where received_at < now() - %s * interval '1 day'"
                "   and purged_at is null",
                (RETENCAO_OUTBOX_DIAS,),
            )
            purgadas = cur.rowcount
        conn.commit()
    return purgadas


def marcar_processado(event_id: str) -> bool:
    """Fecha o evento. Devolve True se ele estava aberto.

    `where processed_at is null` não é zelo: sem ele, uma passada atrasada
    reescreveria o carimbo de um evento já concluído e o `idx_pix_webhook_pendentes`
    deixaria de descrever a fila.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events set processed_at = now()"
                " where event_id = %s and processed_at is null"
                " returning event_id",
                (event_id,),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def registrar_falha(event_id: str, tipo: str, codigo: str | None = None) -> int:
    """Incrementa `attempts` e grava `last_error`. Devolve o `attempts` NOVO.

    Devolve o número, e não `None`, porque quem decide o `admin_notify` de
    `attempts > 5` (§8.2) é o dreno, e ele não pode reler a linha para saber:
    entre o UPDATE e o SELECT cabe outra passada, e o alerta sairia duplicado ou
    nenhuma vez. `returning attempts` é a leitura da própria escrita.

    **Não aceita texto livre, e a assinatura é o conserto.** A versão anterior recebia
    `erro: str` e guardava os primeiros 500 caracteres — e CPF, e-mail e nome aparecem
    justamente NO COMEÇO de uma mensagem de erro, então truncar não removia nada (P2-6
    do Codex). A purga do §13.3 zera esta coluna aos 7 dias, junto com o `payload_enc` —
    mas dentro da janela o valor ESTÁ lá, e a mesma string vai para `system_event_logs`,
    onde a purga só alcança PARTE: a exclusão de conta apaga por `user_id`
    (`db/privacy.py:837`), e este erro nasce sem dono (`log_system_event_sync` tem
    `user_id: int | None = None`), então linha `user_id is null` nada alcança — e
    retenção automática por idade não existe (as duas purgas da tabela são manuais).
    **`_erro_seguro` continua obrigatório apesar da purga**: o que passa fica para sempre.

    **`and purged_at is null` (#316) é o que impede esta função de repor PII.** O caminho
    alcançável é a CORRIDA: a passada que já leu o payload executa os efeitos enquanto a
    varredura do §13.3 carimba `purged_at` e zera as duas colunas; um efeito falha, e sem
    a guarda esta função REPORIA `last_error` numa linha que a varredura (filtro
    `purged_at is null`) nunca revisita — PII permanente. Passada NOVA não chega aqui:
    sem payload o dreno carimba `processed_at` e sai (§13.3), sem falhar.

    TETO QUE A GUARDA CRIA, e quem lê o retorno precisa saber: em linha purgada o
    `returning` vem vazio e a função devolve **`0`**, então `0` passa a significar duas
    coisas — "não existe" e "está purgada" —, e o `admin_notify` de `attempts > 5` (§8.2)
    **nunca dispara para linha purgada**. Aceitável porque o dreno que encontra
    `payload_enc is null` carimba `processed_at` e sai sem falhar (§13.3): purgada não
    volta a acumular tentativa. Distinguir os dois zeros exige consultar a linha.

    `tipo` é o NOME DA CLASSE da exceção (`type(exc).__name__`) e `codigo` é o
    `AsaasApiError.code`, que já nasce filtrado por `_codigo_seguro`. Os dois
    passam pelo mesmo filtro de FORMA aqui, porque "o chamador promete que é
    seguro" é como a PII entra — e o chamador é o dreno do 1b-B, que ainda não
    existe para prometer nada.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events"
                "   set attempts = attempts + 1, last_error = %s"
                " where event_id = %s and purged_at is null"
                " returning attempts",
                (_erro_seguro(tipo, codigo), event_id),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["attempts"]) if row else 0


def _erro_seguro(tipo: str, codigo: str | None) -> str:
    """`Tipo(codigo)`, com os dois filtrados por FORMA — nunca por confiança.

    **Era uma CÓPIA passo a passo do `_codigo_seguro` de
    `core/services/asaas.py`, e agora é um import** (§5.2 do plano do 1b-B). A
    cópia não era preferência: importar aquele módulo daqui deixava
    `tests/test_pix_inerte.py` vermelho, porque a varredura por `ast` não
    distinguia inerte importando inerte. O 1b-B pôs os módulos do Pix na própria
    `CHAMADORES_PERMITIDOS`, e com isso a única diferença medida entre as duas —
    `"?"` aqui, `""` lá — virou o parâmetro `fallback`.

    `test_erro_seguro_nao_divergiu_do_codigo_seguro` morreu junto, e os **29**
    valores que ela media migraram para `tests/test_asaas_codigo_seguro.py` — não
    para `tests/test_asaas_client.py`, e não eram "três". Teste apagado sem os
    casos migrados é o buraco por onde o CPF já passou uma vez, e a migração de
    fato perdeu três (`"x"*60`, `"0001-12345-6"`, `"42"`), repostos em
    2026-09-09.

    `tipo` é o NOME DA CLASSE da exceção (`type(exc).__name__`) e `codigo` é o
    `AsaasApiError.code`. Os dois passam pelo mesmo filtro **aqui**, porque "o
    chamador promete que é seguro" é como a PII entra.
    """
    from core.services.asaas import _codigo_seguro

    base = _codigo_seguro(tipo, "?")
    return f"{base}({_codigo_seguro(codigo, '?')})" if codigo else base


def eventos_pendentes(limite: int = 100) -> list[str]:
    """`event_id` dos eventos ainda abertos, mais antigos primeiro.

    É o que o laço de 60 s do monólito drena. O `background_tasks` do handler é
    o caminho rápido; este é o que RECUPERA — processo reiniciado no meio, efeito
    que levantou, evento que chegou enquanto o worker morria.

    Sem `for update` de propósito: quem reserva é `reservar_evento`, com
    `skip locked`, uma linha de cada vez. Uma lista morna é o certo aqui — dois
    workers pegando a mesma lista disputam a reserva, e o perdedor sai.

    Indexado por `idx_pix_webhook_pendentes` (`processed_at is null`).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select event_id from pix_webhook_events"
                " where processed_at is null"
                " order by received_at asc limit %s",
                (int(limite),),
            )
            return [r["event_id"] for r in cur.fetchall()]
