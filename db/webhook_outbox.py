"""
db/webhook_outbox.py — a outbox do webhook do Asaas e o registro de efeitos.

Duas tabelas e nenhuma mensageria: sem broker, sem DLQ, sem backoff
configurável (§3.4 do plano). O handler grava aqui e responde 200; quem executa
é o dreno.

Plano: docs/plano_pix_anual_asaas.md §3.3, §3.4, §8.1, §8.2 e §13.3.

**Fatia INERTE (PR 1b-A): nenhum módulo de produção importa este arquivo.**
O handler e o dreno são o PR 1b-B.

# ponytail: outbox por varredura em loop, não fila. Generalizar só quando
# houver um SEGUNDO produtor de eventos — com um, broker é infraestrutura
# para problema que não existe.

O que este arquivo NÃO tem, e o corte é deliberado:

  • **o `select … for update skip locked` do dreno**. Ele é a semântica de
    concorrência da leitura, e semântica de concorrência sem consumidor não se
    mede: um `skip locked` escrito hoje passaria verde sem provar que duas
    instâncias não executam o mesmo efeito. Vai junto com o dreno, no 1b-B.
  • **a leitura do `payload_enc`**. Decifrar exige um `PiiAccessContext`, e o
    `subject_user_id` dele só existe DEPOIS de casar o evento com a cobrança —
    que é trabalho do dreno. Inventar um sujeito aqui (0, -1) gravaria linha
    falsa em `pii_access_log`, que é registro de compliance.
  • **as três constantes de retenção** (`RETENCAO_*`). O próprio plano congelou
    `RETENCAO_PAGAMENTO_DIAS` "sem valor em código até validação jurídica"
    (§13.1), e as outras duas só têm sentido com a varredura que as consome.
"""

from __future__ import annotations

import json

from core.crypto import encrypt_pii_optional

from .connection import get_conn

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

# Efeitos válidos do §3.4. A lista existe para o insert recusar typo — um
# `'grantt'` gravado seria um efeito que NUNCA é encontrado pela consulta e que
# portanto reexecuta para sempre.
EFEITOS = (
    "stripe_cancel", "grant", "ga4", "capi", "email", "revoke",
    "orphan_notified",
)


# Tipos que podem ser guardados como VALOR de um campo permitido. A allowlist
# de CHAVES não basta: `customer` é um campo permitido, e o Asaas pode mandá-lo
# **expandido** — `{"id": …, "name": …, "cpfCnpj": …, "email": …}`. Filtrar só o
# primeiro nível copiaria o objeto inteiro, com o CPF dentro de um campo cujo
# nome está na lista. Medido pelo Tester, em três formas: `customer` expandido,
# dict aninhado em `status` e lista sob `value`.
_ESCALARES = (str, int, float, bool, type(None))

# Teto por campo. Nenhum dos nove campos do §13.3 é texto livre: o maior é o
# `externalReference` (`pix:<id>`), com menos de 30 chars. 200 é folga de quase
# 7×, e o que ele impede é o campo permitido virar CARGA — a filtragem por forma
# barrava a ESTRUTURA e deixava passar escalar de qualquer tamanho: 1 MB numa
# string, cifrado, na tabela, medido. Truncar é melhor que descartar: o começo
# de um valor esquisito é o que serve para depurar por que ele era esquisito.
LIMITE_POR_CAMPO = 200


def _valor_seguro(valor):
    """Escalar passa (truncado); estrutura é reduzida ao `id` que serve de chave.

    A regra é de FORMA, não de nome: não depende de alguém prever qual campo o
    provedor vai expandir da próxima vez. Um dict com `id` vira o `id` (é o que
    o dreno usa para casar); qualquer outra estrutura vira `None`, e o campo
    fica registrado como presente-mas-descartado.
    """
    # O teto é sobre o TAMANHO SERIALIZADO, não sobre o tipo `str`. A versão
    # anterior só truncava `str`, e um `int` gigante passava inteiro: medido,
    # 4.351 bytes gravados na coluna (o teto real era o `json.loads` do CPython
    # estourando antes, o que não é uma política de retenção).
    #
    # `bool` e `None` saem antes de propósito: `bool` é subclasse de `int`, e
    # `str(True)` viraria a string "True", trocando o tipo de um campo curto.
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

    Mantém `event`/`id` do envelope e, de `payment`, só `CAMPOS_MINIMOS` — e de
    cada um só o VALOR ESCALAR (ver `_valor_seguro`). Chave permitida com objeto
    dentro é o furo que uma allowlist rasa não vê.

    Um `payment` ausente ou de outro tipo vira dict vazio em vez de estourar: o
    handler já respondeu 400 para corpo sem id de evento (§8.1), e o resto do
    formato é do provedor, não nosso.
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
    """Grava o evento na outbox. Devolve **True** se a linha é nova, **False**
    na duplicata.

    É o `insert … on conflict (event_id) do nothing returning` do §8.1: o Asaas
    reentrega, e o 200 da duplicata não pode custar trabalho nenhum. O `returning`
    vazio é a resposta — não há `select` antes, que teria a corrida entre duas
    entregas simultâneas do mesmo evento.

    O payload é **minimizado e depois cifrado** (§13.3). A ordem importa: cifrar
    primeiro e minimizar depois guardaria o dado pessoal cifrado no banco, que é
    exatamente o que a purga de 7 dias tenta desfazer.
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


def registrar_falha(event_id: str, erro: str) -> int:
    """Incrementa `attempts` e grava `last_error`. Devolve o `attempts` NOVO.

    Devolve o número, e não `None`, porque quem decide o `admin_notify` de
    `attempts > 5` (§8.2) é o dreno, e ele não pode reler a linha para saber:
    entre o UPDATE e o SELECT cabe outra passada, e o alerta sairia duplicado ou
    nenhuma vez. `returning attempts` é a leitura da própria escrita.

    `erro` é TRUNCADO: mensagem de exceção do Asaas pode carregar corpo de
    resposta com PII do titular (é a razão de `core/services/asaas.py` não
    incluir o corpo), e `last_error` sobrevive à purga do payload para forense.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events"
                "   set attempts = attempts + 1, last_error = %s"
                " where event_id = %s"
                " returning attempts",
                ((erro or "")[:500], event_id),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["attempts"]) if row else 0


def efeito_registrado(asaas_payment_id: str, effect: str) -> bool:
    """O par `(asaas_payment_id, effect)` já rodou? (§3.4, correção nº 8b.)

    Chave pelo PAGAMENTO e não pelo evento: `PAYMENT_RECEIVED` reentregue com
    `event_id` NOVO — que a plataforma pode emitir — reexecutaria `ga4`, `capi`
    e `email` se a chave fosse o evento. Receita duplicada no GA4 e segundo
    Purchase na CAPI em cima do mesmo dinheiro.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from pix_payment_effects"
                " where asaas_payment_id = %s and effect = %s",
                (asaas_payment_id, effect),
            )
            return cur.fetchone() is not None


def registrar_efeito(asaas_payment_id: str, effect: str, event_id: str) -> bool:
    """Marca o efeito como executado. Devolve **False** se outra passada já o
    tinha registrado.

    O `on conflict do nothing` fecha a janela que o `efeito_registrado` sozinho
    deixa: entre a consulta e o registro cabe outra passada do dreno. A consulta
    evita o TRABALHO (chamar o Stripe, o GA4); este insert evita a LINHA
    duplicada — as duas são necessárias e nenhuma substitui a outra.

    `event_id` é forense: diz QUAL entrega executou o efeito. Nada é decidido
    por ele.
    """
    if effect not in EFEITOS:
        raise ValueError(f"efeito desconhecido: {effect!r}")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_payment_effects (asaas_payment_id, effect, event_id)"
                " values (%s, %s, %s)"
                " on conflict (asaas_payment_id, effect) do nothing"
                " returning effect",
                (asaas_payment_id, effect, event_id),
            )
            novo = cur.fetchone() is not None
        conn.commit()
    return novo
