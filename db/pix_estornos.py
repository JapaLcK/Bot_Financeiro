"""
db/pix_estornos.py — o marcador "o estorno total deste pagamento CHEGOU" (#1437).

A D3 do dreno pergunta se o estorno chegou, e não se o `revoke` rodou: com o `revoke` falhando, só
este marcador registra que o dinheiro foi devolvido. Ele nasce na RECEPÇÃO (`marcar_estorno`, no
cursor da outbox, antes do 200) para todo `PAYMENT_REFUNDED` autenticado com `payment.id` válido,
e o dreno o reafirma (`estorno_no_dreno`).

Uma tabela, `pix_estornos_recebidos` (DDL em `db/schema.py`), e não `pix_payment_effects`: o
marcador não é efeito executado. Guarda só o necessário (decisão do dono, 2ª rodada):
  * `asaas_payment_id` e `origem` (`<ambiente>:<conta>`, de `ASAAS_BASE_URL` e `ASAAS_CONTA`);
  * `classificacao`: `nosso` (há cobrança nossa), `alheio` (referência de outro formato e nenhuma
    cobrança com o id: o Pix avulso da conta do negócio, §11) ou `nao_identificado` (o resto);
  * `event_id` e as datas. Nada de valor, cliente ou referência crua.

**O bloqueio é só pelo id, sem olhar a origem nem a classe**: uma rotação de configuração não pode
fazer um estorno antigo deixar de bloquear (falharia aberto). E só o pagamento correspondente é
bloqueado: recompra, renovação e 2º pagamento têm id novo. **Nada é apagado aqui**; a limpeza dos
`alheio` é issue à parte (nunca `nao_identificado`, nunca id com linha em `pix_charges`).
"""

from __future__ import annotations

import os
import re

from .connection import get_conn

# `ponytail:` regex fixa, não env — o formato é escrito por nós, num lugar só
# (`criar_cobranca`). É ele que separa o nosso dinheiro do Pix avulso que a
# conta do NEGÓCIO também recebe (§11, correção 5).
REFERENCIA_NOSSA = re.compile(r"^pix:[0-9]+$")

# O `payment.id` que vira marcador: texto puro, sem espaço nem quebra. Não exige o prefixo `pay_`:
# um formato novo do Asaas perderia o marcador em silêncio, e um id que não é nosso não bloqueia
# nada nosso — o erro barato é o lado frouxo.
ID_DO_PAGAMENTO = re.compile(r"[A-Za-z0-9_-]{1,100}")

# Antes do pagamento: o estorno do MESMO id leva a cobrança a `canceled` e apaga o QR (R3.3).
# `refunded` exigiria ter havido `paid` (o afiliado gravaria pagante); `canceled` libera a recompra.
# Linha SEM id só cancela fora de `creating`: `creating` sem id é a emissão em voo (o `POST` pode já
# ter criado o pagamento no Asaas), e cancelá-la por um estorno de OUTRO pagamento deixaria o novo
# vivo no Asaas, órfão; quem resolve `creating` é a reconciliação da saga (`pix_sweeps`).
ANTES_DO_PAGAMENTO = ("draft", "creating", "pending", "canceling")

# Fonte única de "o estorno total deste pagamento chegou?": o marcador, ou o `revoke` (a baixa desfeita
# de um `refunded_partial` grava só ele). Lida aqui e na transição para `paid`
# (`transicionar(..., sem_estorno=True)`). Um `%s` só: o id.
ESTORNO_SQL = ("exists (select 1 from (values (%s::text)) v(pid)"
               " where exists (select 1 from pix_estornos_recebidos e where e.asaas_payment_id = v.pid)"
               " or exists (select 1 from pix_payment_effects f"
               " where f.asaas_payment_id = v.pid and f.effect = 'revoke'))")

_GRAVAR = """
insert into pix_estornos_recebidos (asaas_payment_id, origem, classificacao, event_id)
values (%(pid)s::text, %(origem)s::text, case
  when exists (select 1 from pix_charges where asaas_payment_id = %(pid)s::text)
    or (%(nossa)s::boolean and exists (select 1 from pix_charges where external_reference = %(ref)s::text)) then 'nosso'
  when %(ref)s::text is not null and not %(nossa)s::boolean then 'alheio'
  else 'nao_identificado' end, %(eid)s::text)
on conflict (asaas_payment_id, origem) do update
  set classificacao = 'nosso', classificado_em = now()
  where excluded.classificacao = 'nosso' and pix_estornos_recebidos.classificacao <> 'nosso'
"""


def origem() -> str:
    """`sandbox:<conta>` ou `producao:<conta>`. Só para classificar e limpar: nunca restringe o bloqueio."""
    ambiente = "sandbox" if "sandbox" in (os.getenv("ASAAS_BASE_URL") or "") else "producao"
    return f"{ambiente}:{(os.getenv('ASAAS_CONTA') or '').strip() or 'sem_conta'}"


def gravar(pid: str, referencia, event_id: str, cur) -> None:
    """Grava ou promove o marcador no cursor de quem chama (sem commit). A classe sobe para `nosso`
    quando a cobrança aparece; a reentrega não muda o resto."""
    ref = referencia if isinstance(referencia, str) and referencia else None
    cur.execute(_GRAVAR, {"pid": pid, "origem": origem(), "ref": ref, "eid": event_id,
                          "nossa": bool(ref and REFERENCIA_NOSSA.match(ref))})


def marcar_estorno(event_type: str, corpo: dict, event_id: str, cur) -> None:
    """Na RECEPÇÃO, no cursor da outbox: todo `PAYMENT_REFUNDED` com `payment.id` válido, também na
    duplicata e sem exigir a nossa referência. O id vem do corpo CRU: um objeto que `minimizar`
    reduziria ao `id` não marca."""
    pagamento = corpo.get("payment")
    pid = pagamento.get("id") if isinstance(pagamento, dict) else None
    if event_type == "PAYMENT_REFUNDED" and isinstance(pid, str) and ID_DO_PAGAMENTO.fullmatch(pid):
        gravar(pid, pagamento.get("externalReference"), event_id, cur)


def estorno_registrado(asaas_payment_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"select {ESTORNO_SQL} as sim", (asaas_payment_id,))
            return bool(cur.fetchone()["sim"])


def estorno_no_dreno(cobranca: dict, payment_id: str, event_id: str) -> dict:
    """O dreno do `PAYMENT_REFUNDED`, antes de transição e efeito: grava o marcador como `nosso` (o
    evento recebido antes do deploy chega aqui sem ele) e cancela a cobrança estornada antes do
    pagamento, se o id é o dela (ou ela ainda não tem id e não está em emissão). Devolve a cobrança como ficou."""
    from .pix_charges import transicionar

    with get_conn() as conn:
        with conn.cursor() as cur:
            gravar(payment_id, cobranca["external_reference"], event_id, cur)
        conn.commit()
    # O id e o "fora de `creating`" no próprio `UPDATE`, nunca no dicionário lido antes: o checkout
    # pode levar a linha a `creating` ou a `pending` com id novo nesse meio-tempo (Codex, #1595).
    return transicionar(cobranca["id"], de=ANTES_DO_PAGAMENTO, para="canceled", apagar_qr=True,
                        so_do_pagamento=payment_id) or cobranca
