"""
db/pix_charges_saga.py — a saga de criação, substituição e reconciliação.

Saiu de `db/pix_charges.py` (§2.2 do plano do 1b-B) porque aquele arquivo bateu
no teto de 350 linhas quando `criar_cobranca` ganhou o `rastreio`. A divisão é
por ASSUNTO: lá ficam a criação, a transição e as buscas; aqui fica **o que
acontece quando o mundo local e o Asaas discordam** — fechar a saga com o id
remoto, decidir se uma cobrança some, e listar quem precisa ser reconciliado.

**Desvio declarado do plano.** O Arquiteto nomeou este módulo
`db/pix_charges_sweep.py`, com a justificativa de que `attach_pagamento` "é
chamada só pela varredura" — o próprio docstring dela dizia isso. **Medido, é
falso a partir deste PR:** o checkout também a chama, é ele quem fecha a saga no
caminho feliz (`draft` → `creating` → POST no Asaas → `pending`), e a varredura
só a repete quando aquele caminho morreu no meio. Arquivo chamado "sweep" com o
checkout dentro seria a mesma mentira que o `test_pix_rota_inerte.py` deixou de
contar neste PR. `saga` cobre os dois chamadores porque é o nome do problema,
não o do horário em que ele roda.

Plano: docs/plano_pix_anual_asaas.md §10 e §10.1.

**Nenhuma função daqui apaga por RELÓGIO.** A idade decide *quando* reconciliar;
só a resposta do Asaas decide o que some (§10.1). Por isso `apagar_cobranca`
exige `asaas_payment_id is null` no `where` e nunca alcança `creating`.
"""

from __future__ import annotations

from .connection import get_conn
from .pix_charges import _COLUNAS


def attach_pagamento(charge_id: int, asaas_payment_id: str, *, qr_payload_enc: str | None = None,
                     due_date=None, qr_expires_at=None) -> bool:
    """Grava o id remoto na cobrança `creating` e a leva a `pending`. Devolve
    True se aplicou.

    **Dois chamadores, e é de propósito.** O CHECKOUT chama no caminho feliz,
    logo depois do POST ao Asaas: é o passo "resposta salva" do §10. A VARREDURA
    chama na saída do estado AMBÍGUO, quando o POST efetivou e a resposta se
    perdeu — ela reconcilia por `externalReference` e fecha a mesma saga.

    `where asaas_payment_id is null` faz o attach ser idempotente sem `select`
    antes: o checkout e a varredura podem correr juntos, e a segunda devolve
    False em vez de sobrescrever o id que a primeira gravou.

    `qr_payload_enc` chega **já cifrado** por quem chama (§13.6): este módulo não
    decide política de PII, e o QR nunca aparece em log, em `details` de
    auditoria nem em mensagem de erro.

    Sem `user_id` no `where` pelo mesmo motivo do `transicionar` — e com a mesma
    ressalva: `charge_id` é enumerável, então a segurança é do chamador.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set asaas_payment_id = %s, status = 'pending',"
                "       qr_payload_enc = coalesce(%s, qr_payload_enc),"
                "       due_date = coalesce(%s, due_date),"
                "       qr_expires_at = coalesce(%s, qr_expires_at)"
                " where id = %s and status in ('creating', 'draft')"
                "   and asaas_payment_id is null"
                " returning id",
                (asaas_payment_id, qr_payload_enc, due_date, qr_expires_at,
                 int(charge_id)),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def buscar_ativa(user_id: int) -> dict | None:
    """A cobrança que ocupa o índice parcial de "ativa" deste usuário, **com o
    QR cifrado junto**.

    É a leitura da decisão do §10: reaproveitar, substituir ou criar. O
    `qr_payload_enc` vem porque o caminho "fechei a aba e voltei" devolve **o
    mesmo QR** (decisão do dono, 2026-09-09) — e é a única função deste
    repositório que tira o instrumento de pagamento do banco. Quem decifra passa
    `PiiAccessContext(purpose="pix_qr_read")` (§13.6); o `_COLUNAS` continua sem
    ele, então o poll e o dreno não o veem nem por acidente.

    **Filtra por `user_id`** (CLAUDE.md §0): quem chama é o checkout, que tem
    dono na requisição. É o oposto das quatro exceções de `db/pix_charges.py`.

    Os estados são `ESTADOS_ATIVOS`, montado a partir da mesma tupla que o
    índice parcial usa — sem lista reescrita (§0.7).
    """
    from .pix_charges import ESTADOS_ATIVOS

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select {_COLUNAS}, qr_payload_enc from pix_charges"
                " where user_id = %s and status = any(%s)"
                " order by id desc limit 1",
                (int(user_id), list(ESTADOS_ATIVOS)),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def valores_por_cobranca(user_id: int) -> dict[str, int]:
    """`str(pix_charges.id)` → `amount_cents`, para o "join" que
    `plano_da_cobranca` exige do chamador.

    Ela documenta que `amount_cents` **não existe em `plan_grants`** e que
    esquecer o join zera todo crédito de upgrade em silêncio. O join é feito em
    Python (`plan_grants.external_ref` é `text`, o `id` daqui é `bigint`) porque
    assim as duas metades continuam filtrando por `user_id` — um join em SQL
    entre as duas tabelas com `user_id` só de um lado é como vazamento entre
    contas entra.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, amount_cents from pix_charges where user_id = %s",
                (int(user_id),),
            )
            return {str(r["id"]): int(r["amount_cents"]) for r in cur.fetchall()}


def gravar_stripe_period_end(charge_id: int, quando, *, access_starts_at=None,
                             access_expires_at=None) -> bool:
    """Grava o `stripe_period_end_at` RECONFIRMADO no Stripe. True se aplicou.

    A criação grava a ESTIMATIVA que o checkout leu (§8.2); o efeito
    `stripe_cancel` lê o `current_period_end` de verdade no pagamento e chama
    isto. `where stripe_subscription_id is not null` porque só a migração tem
    período a reconfirmar.

    **A janela vem junto, e no MESMO update.** Quem decide se ela muda é
    `_janela_adiada` (só adia, nunca antecipa); passá-la aqui em vez de num
    segundo comando evita a linha ficar um instante com período novo e janela
    velha — que é exatamente o instante em que o efeito `grant` roda. `None` nos
    dois mantém o que está gravado (`coalesce`), que é o caminho comum.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set stripe_period_end_at = %s,"
                "       stripe_cancel_scheduled_at = now(),"
                "       access_starts_at = coalesce(%s, access_starts_at),"
                "       access_expires_at = coalesce(%s, access_expires_at)"
                " where id = %s and stripe_subscription_id is not null"
                " returning id",
                (quando, access_starts_at, access_expires_at, int(charge_id)),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def listar_para_reconciliar(minutos: int, limite: int = 200) -> list[dict]:
    """Cobranças `draft`/`creating` paradas há mais de `minutos`.

    **A idade só decide QUANDO olhar** (§10.1). O que sai da base é decidido
    pela resposta do Asaas, em `core/services/pix_sweeps.py` — nunca aqui.

    `creating` entra porque é o estado ambíguo por definição: o POST pode ter
    efetivado com a resposta perdida. `draft` entra porque um POST que nunca
    saiu deixa a linha ocupando o índice parcial de "ativa", e o cliente não
    consegue comprar de novo até ela sair.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select {_COLUNAS} from pix_charges"
                " where status in ('draft', 'creating')"
                "   and created_at < now() - make_interval(mins => %s)"
                " order by created_at asc limit %s",
                (int(minutos), int(limite)),
            )
            return [dict(r) for r in cur.fetchall()]


def voltar_para_draft(charge_id: int) -> bool:
    """`creating` → `draft`: o Asaas confirmou que a cobrança não existe lá.

    Não apaga. `draft` é o estado de onde a passada seguinte cai na regra (b) do
    §10.1 — e o §10.1 é literal em dizer que **`creating` nunca expurga**.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges set status = 'draft'"
                " where id = %s and status = 'creating'"
                "   and asaas_payment_id is null"
                " returning id",
                (int(charge_id),),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou


def apagar_cobranca(charge_id: int) -> bool:
    """Apaga a linha — **só** com a regra (b) do §10.1 já provada por quem chama.

    As duas condições do `where` são a mesma regra escrita no BANCO, e não uma
    precondição em Python: `status = 'draft'` (nunca `creating`, nunca `pending`,
    nunca nada que já é pagável) **e** `asaas_payment_id is null`. A prova que
    falta — lista vazia no `GET /payments?externalReference=` — é do chamador,
    porque só ele fala com o Asaas; falha de consulta lá vira "não faz nada", e
    a linha continua aqui para a próxima passada (§10.1).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from pix_charges"
                " where id = %s and status = 'draft' and asaas_payment_id is null"
                " returning id",
                (int(charge_id),),
            )
            aplicou = cur.fetchone() is not None
        conn.commit()
    return aplicou
