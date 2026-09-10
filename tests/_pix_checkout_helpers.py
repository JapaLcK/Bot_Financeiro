"""Fixtures da saga do checkout Pix, compartilhadas pelos dois arquivos.

Sem prefixo `test_` de propósito — o pytest não coleta este arquivo. Ele existe
pelo mesmo motivo de `tests/_dreno_pix_helpers.py`: dois arquivos do mesmo
assunto não podem carregar duas cópias do Asaas falso, senão um dia eles medem
coisas diferentes achando que medem a mesma (CLAUDE.md §0.7).

**O que é mockado é só o MUNDO LÁ FORA** (Asaas e Stripe), porque é ele que se
conta. `pix_charges`, `plan_grants` e `auth_accounts` são de verdade: a classe
de bug que mais aparece neste repositório é a do estado que outro fluxo deixou
no banco (§3), e um `db` mockado é cego para ela.
"""
from __future__ import annotations

import uuid

import pytest

from _billing_grants_helpers import garantir_system_event_logs
from core.services import pix_checkout
from core.services.pix_checkout import criar_checkout
from core.services.pix_pricing import PRECOS_ANUAIS_CENTS
from db.connection import get_conn

# LIDO da fonte, não copiado (§0.7). Era `49900` cravado aqui e por acaso batia
# com o preço real; hoje o preço é constante de produção, e um teste que fixa o
# próprio número deixaria de medir a venda no dia em que ele mudar.
PRECO = PRECOS_ANUAIS_CENTS["pro_max"]


@pytest.fixture()
def asaas_falso(monkeypatch):
    """Asaas por CONTADOR, e a lista `ordem` é o que prova a saga.

    Ela guarda o nome de cada chamada remota na sequência em que aconteceu — sem
    isso, "deleta antes de criar" não é mensurável, e foi exatamente essa ordem
    que o §10 corrigiu (nº 6).
    """
    import core.services.asaas as a
    import core.services.asaas_customers as ac

    # Prefixo único por teste: `asaas_payment_id` é `unique` na tabela e as
    # linhas sobrevivem entre os testes do arquivo — um contador só colidiria.
    marca = uuid.uuid4().hex[:8]
    # `remotas` é o que o `GET /payments?externalReference=` devolve. Ele entrou
    # no falso quando a substituição passou a PERGUNTAR pela cobrança ambígua
    # (linha `creating` sem `asaas_payment_id`): sem o stub o teste sairia para a
    # rede, e com `[]` o comportamento é o de antes — nada remoto para deletar.
    # Uma exceção aqui simula o provedor fora do ar.
    # `descricoes` guarda o que foi para o campo que o PAGADOR lê na fatura —
    # a única saída deste PR visível para cliente hoje. Lista, e não o último
    # valor: um teste que compra duas vezes tem de conseguir ver as duas.
    # `cliente_falha` é o `AsaasApiError` que o `POST /v3/customers` levanta —
    # o interruptor do `qr_falha`/`delete_falha`, com o erro dentro em vez de um
    # booleano: quem classifica 400/422 é o `criar_cliente` DE VERDADE, e o
    # status é justamente o que se quer variar.
    estado = {"ordem": [], "delete_falha": False, "n": 0, "marca": marca,
              "remotas": [], "qr_falha": False, "descricoes": [],
              "cliente_falha": None}
    real_cliente = ac.criar_cliente

    def _request_falso(*a, **kw):
        raise estado["cliente_falha"]

    def _cliente(**kw):
        estado["ordem"].append("customer")
        estado["cpf_visto"] = kw.get("cpf_cnpj")
        if estado["cliente_falha"] is not None:
            # Passa pelo `criar_cliente` real, só sem transporte: um falso que
            # já levantasse `TitularRecusado` mediria a si mesmo, e a regra dos
            # status (400/422 sim, 401/403/429/5xx não) não seria testada.
            return real_cliente(**kw)
        return "cus_1"

    def _pagamento(**kw):
        estado["n"] += 1
        estado["ordem"].append("create")
        estado["descricoes"].append(kw.get("descricao"))
        return {"id": f"pay_{marca}_{estado['n']}"}

    def _qr(pid):
        estado["ordem"].append("qr")
        if estado["qr_falha"]:
            raise a.AsaasApiError("QR nao veio", status_code=502)
        return {"payload": f"000201-{pid}", "expirationDate": "2026-12-31 23:59:59"}

    def _por_referencia(ref):
        estado["ordem"].append("consulta")
        if isinstance(estado["remotas"], Exception):
            raise estado["remotas"]
        return estado["remotas"]

    def _delete(pid):
        estado["ordem"].append(f"delete:{pid}")
        if estado["delete_falha"]:
            raise a.AsaasApiError("Falha ao cancelar", status_code=502)
        return {"deleted": True}

    monkeypatch.setattr(ac, "_request", _request_falso)
    monkeypatch.setattr(ac, "criar_cliente", _cliente)
    monkeypatch.setattr(a, "criar_pagamento_pix", _pagamento)
    monkeypatch.setattr(a, "obter_qr_pix", _qr)
    monkeypatch.setattr(a, "deletar_pagamento", _delete)
    monkeypatch.setattr(a, "buscar_por_external_reference", _por_referencia)
    # Sem assinatura no cartão: o ramo do §9 tem teste próprio.
    monkeypatch.setattr(pix_checkout, "_stripe_vivo", lambda uid: None)
    return estado


@pytest.fixture()
def vendavel(monkeypatch):
    """Flag ligada e a env de dinheiro presente.

    Uma env só desde 2026-09-09: o PREÇO virou constante em
    `core.services.pix_pricing.PRECOS_ANUAIS_CENTS` e não se configura mais.
    """
    garantir_system_event_logs()
    monkeypatch.setenv("ASAAS_PIX_ANNUAL_ENABLED", "1")
    monkeypatch.setenv("ASAAS_MIN_CHARGE_CENTS", "500")


def _comprar(user_id, plano="pro_max", **kw):
    return criar_checkout(user_id, plan_stored=plano, cpf_cnpj="12345678901",
                          nome="Fulano", email="f@x.com", **kw)


def _marcar_paga(uid: int, token: str, dias: int = -1) -> None:
    """Leva a cobrança a `paid` e cria o grant que ela sustenta — o estado que o
    pagamento deixaria. Usa as funções de produção (§0.7): a cobertura vigente
    tem de ser a mesma que o dreno produz, senão o teste mede outro sistema."""
    from datetime import datetime, timedelta, timezone

    from db.pix_charges import buscar_por_public_token, transicionar
    from db.plan_grants import upsert_grant

    linha = buscar_por_public_token(uid, token)
    inicio = datetime.now(timezone.utc) + timedelta(days=dias)
    fim = inicio + timedelta(days=365)
    transicionar(linha["id"], de=("pending",), para="paid",
                 access_starts_at=inicio, access_expires_at=fim, apagar_qr=True)
    upsert_grant(uid, "pix", str(linha["id"]), linha["plan_stored"], inicio, fim,
                 1, last_event_id="e1")


def _linhas(uid: int) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_charges where user_id = %s order by id", (uid,))
        return [dict(r) for r in cur.fetchall()]


