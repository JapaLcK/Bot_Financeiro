"""A purga por idade da outbox e a guarda que impede repor PII nela
(`db/webhook_outbox.py`, §13.3 do plano; issues #315 e #316).

Arquivo próprio, e não mais casos em `tests/test_pix_outbox.py`: aquele mede
dedup, minimização e cifra, e está a 4 linhas do teto de 350
(`tests/test_max_lines_python.py`). Assunto diferente, arquivo diferente.

As duas propriedades que decidem PRIVACIDADE aqui:

  1. **a purga zera `payload_enc` E `last_error` na mesma passada**, contando de
     `received_at` — processado ou não. `last_error` é escrito por um filtro de
     FORMA (`_erro_seguro`), e forma não separa nome de código: `'Fulano'` e
     `'joao_silva'` passam inteiros. Uma purga que zera só o payload deixa o nome
     do titular na tabela para sempre;
  2. **`registrar_falha` não escreve em linha purgada** (`and purged_at is null`).
     O caminho é a corrida: a passada que já leu o payload falha num efeito
     DEPOIS de a varredura ter zerado a linha — e a varredura, que filtra
     `purged_at is null`, nunca a revisita.

CONTROLES NEGATIVOS MEDIDOS (rodados um a um, com o resto do grupo verde):

  * tire `and purged_at is null` do UPDATE de `registrar_falha` →
    `test_falha_em_linha_purgada_nao_repoe_pii` (D6-a) VERMELHO;
  * tire `last_error = null` do UPDATE da purga →
    `test_purga_aos_7_dias_zera_payload_e_last_error` (D6-c) VERMELHO e
    `test_evento_dentro_da_janela_fica_intacto` (D6-e) VERDE — é a combinação que
    separa a cláusula nova de uma varredura que zera a tabela inteira;
  * conte de `processed_at` em vez de `received_at` →
    `test_purga_alcanca_o_evento_do_titular_excluido` (D6-d) VERMELHO, porque o
    evento TRAVADO — que é justamente o de quem pediu exclusão da conta — tem
    `processed_at` nulo e sairia da varredura para sempre;
  * tire `purgar_payloads_antigos()` — ou `rezerar_rastreio_de_orfas()` — de
    `core/services/pix_sweeps.py::purgar_retencao` →
    `test_purgar_retencao_roda_as_DUAS_purgas` VERMELHO nas DUAS mutações, e só
    ele: os outros casos chamam as folhas direto e não veem o elo.

POSITIVOS: `test_falha_em_linha_viva_grava_normalmente` (D6-b, o par da guarda) e
D6-e. Sem eles o grupo passaria num `registrar_falha` que nunca grava e numa
purga que apaga tudo — os dois piores que o bug.
"""

import uuid

from db.connection import get_conn
from db.webhook_outbox import (
    RETENCAO_OUTBOX_DIAS,
    purgar_payloads_antigos,
    registrar_evento,
    registrar_falha,
)


def _evt() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def _corpo(pagamento_id: str = "pay_1", ref: str = "pix:1") -> dict:
    return {"id": "evt_envelope", "event": "PAYMENT_RECEIVED",
            "payment": {"id": pagamento_id, "externalReference": ref,
                        "value": 299.0, "status": "RECEIVED"}}


def _linha(event_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select payload_enc, last_error, attempts, received_at,"
            "       processed_at, purged_at"
            " from pix_webhook_events where event_id = %s",
            (event_id,),
        )
        return cur.fetchone()


def _envelhecer(event_id: str, dias: float) -> None:
    """Empurra `received_at` para trás. É o único jeito de medir uma janela de 7
    dias sem esperar 7 dias — e mexe só na coluna que a purga lê."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events"
                "   set received_at = now() - %s * interval '1 day'"
                " where event_id = %s",
                (dias, event_id),
            )
        conn.commit()


def _marcar_purgada(event_id: str) -> None:
    """O estado que a corrida do #316 produz: a varredura já passou."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_webhook_events"
                "   set payload_enc = null, last_error = null, purged_at = now()"
                " where event_id = %s",
                (event_id,),
            )
        conn.commit()


# ── D6-a e D6-b: a guarda de `registrar_falha` e o positivo do par ───────────

def test_falha_em_linha_purgada_nao_repoe_pii():
    """D6-a. Linha já purgada: `registrar_falha` não toca em nada e devolve 0.

    Sem `and purged_at is null`, esta chamada REPÕE `last_error` numa linha que a
    varredura (filtro `purged_at is null`) nunca mais visita — PII permanente.
    """
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    registrar_falha(eid, "ReadTimeout")          # a passada anterior já falhou
    _marcar_purgada(eid)

    assert registrar_falha(eid, "AsaasApiError", "invalid_cpfCnpj") == 0

    depois = _linha(eid)
    assert depois["last_error"] is None, "a guarda deixou PII voltar para a linha"
    assert depois["payload_enc"] is None
    assert depois["attempts"] == 1, "attempts andou numa linha purgada"


def test_falha_em_linha_viva_grava_normalmente():
    """D6-b, POSITIVO do par: sem ele a guarda podia ser `where false`.

    O `attempts` devolvido é o que o dreno usa para decidir o `admin_notify` de
    `attempts > 5` (§8.2) — se a guarda matasse o caminho comum, o alerta nunca
    sairia e ninguém notaria.
    """
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)

    assert registrar_falha(eid, "ReadTimeout") == 1
    assert registrar_falha(eid, "ReadTimeout") == 2

    viva = _linha(eid)
    assert viva["last_error"] == "ReadTimeout"
    assert viva["attempts"] == 2
    assert viva["purged_at"] is None


# ── D6-c a D6-f: a varredura por idade ───────────────────────────────────────

def test_purga_aos_7_dias_zera_payload_e_last_error():
    """D6-c (#315). As DUAS colunas na mesma passada, e só elas.

    A asserção é sobre o que foi ANULADO, não sobre uma lista fechada do que
    sobrou (§13.3): `received_at` é o predicado da própria purga e `processed_at`
    é o do `idx_pix_webhook_pendentes` — nenhum dos dois pode ser tocado.
    """
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    registrar_falha(eid, "ReadTimeout")
    _envelhecer(eid, RETENCAO_OUTBOX_DIAS + 1)
    antes = _linha(eid)
    assert antes["payload_enc"] and antes["last_error"], "fixture não montou o caso"

    assert purgar_payloads_antigos() >= 1

    depois = _linha(eid)
    assert depois["payload_enc"] is None
    assert depois["last_error"] is None, (
        "`last_error` sobreviveu à purga — é a coluna que carrega o nome da "
        "classe da exceção e o `code` do Asaas, e o filtro de forma não separa "
        "nome de código"
    )
    assert depois["purged_at"] is not None
    assert depois["received_at"] == antes["received_at"]
    assert depois["processed_at"] is None, "a purga mexeu no predicado da fila"


def test_purga_alcanca_o_evento_do_titular_excluido():
    """D6-d (#315). O evento TRAVADO de uma cobrança já pseudonimizada.

    É o caso que a alternativa rejeitada — "limpar na exclusão da conta" — nunca
    cobriria: `pix_webhook_events` não tem `user_id` nem FK, e o único vínculo
    com o titular mora dentro do `payload_enc` cifrado. Sobra a idade.

    E é o caso que morre se alguém contar de `processed_at`: evento travado tem
    `processed_at` nulo, e `null < now() - interval` nunca é verdadeiro.
    """
    import secrets

    from conftest import _cleanup_user, ensure_user
    from db.pix_charges import buscar_por_external_reference, criar_cobranca

    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    cobranca = criar_cobranca(
        uid, public_token=secrets.token_urlsafe(16), plan="pro_max",
        plan_stored="pro_max", price_cents=29900, credit_cents=0,
        amount_cents=29900, duration_days=365,
    )
    ref = cobranca["external_reference"]
    _cleanup_user(uid)
    orfa = buscar_por_external_reference(ref)
    assert orfa is not None and orfa["user_id"] is None, (
        "a cobrança não ficou órfã — a fixture não montou o caso do #315"
    )

    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(ref=ref), 1)
    registrar_falha(eid, "AsaasApiError", "unauthorized")
    _envelhecer(eid, RETENCAO_OUTBOX_DIAS + 1)
    assert _linha(eid)["processed_at"] is None, "o caso é o evento TRAVADO"

    purgar_payloads_antigos()

    depois = _linha(eid)
    assert depois["payload_enc"] is None
    assert depois["last_error"] is None
    assert depois["purged_at"] is not None


def test_evento_dentro_da_janela_fica_intacto():
    """D6-e, POSITIVO: sem ele o grupo passaria numa varredura que zera a tabela
    inteira — que é pior que o bug, porque apaga o diagnóstico de todo evento
    vivo. 3 dias está dentro dos 7."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    registrar_falha(eid, "ReadTimeout")
    _envelhecer(eid, 3)

    purgar_payloads_antigos()

    novo = _linha(eid)
    assert novo["payload_enc"] is not None
    assert novo["last_error"] == "ReadTimeout"
    assert novo["purged_at"] is None


def test_purga_repetida_nao_reescreve_o_carimbo():
    """D6-f. `purged_at is null` no `where`: sem ela a varredura diária
    reescreveria o carimbo todo dia e `purged_at` deixaria de datar a purga."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    _envelhecer(eid, RETENCAO_OUTBOX_DIAS + 1)

    purgar_payloads_antigos()
    primeiro = _linha(eid)["purged_at"]
    assert primeiro is not None

    purgar_payloads_antigos()
    assert _linha(eid)["purged_at"] == primeiro


# ── o ELO: quem CHAMA as duas folhas ────────────────────────────────────────

def test_purgar_retencao_roda_as_DUAS_purgas(user_id):
    """O elo que faltava. Todos os casos acima — e os de `test_pix_privacidade.py`
    — chamam as FOLHAS direto, e a sonda do lifespan troca `purgar_retencao` por
    um marcador. Com `return {"outbox": 0, "cobrancas_orfas": 0}` no lugar do
    corpo, as duas suítes ficavam VERDES.

    Os helpers da cobrança vêm importados de `test_pix_privacidade.py` em vez de
    copiados: é a mesma fixture, e duas versões dela é o CLAUDE.md §0.7.

    *Negativo, as duas medidas: tire `purgar_payloads_antigos()` de
    `purgar_retencao` → VERMELHO aqui; tire `rezerar_rastreio_de_orfas()` →
    VERMELHO aqui. Em nenhuma das duas outro caso da suíte fica vermelho.*
    """
    from core.services.pix_sweeps import purgar_retencao
    from test_pix_privacidade import _nova, _orfanar, _por_ref, _sujar

    cobranca = _nova(user_id)
    _sujar(cobranca["id"])
    _orfanar(cobranca["id"])
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    _envelhecer(eid, RETENCAO_OUTBOX_DIAS + 1)

    contagem = purgar_retencao()

    assert contagem["outbox"] >= 1, "`purgar_payloads_antigos` não foi chamada"
    assert _linha(eid)["payload_enc"] is None
    assert contagem["cobrancas_orfas"] >= 1, "`rezerar_rastreio_de_orfas` não foi chamada"
    assert _por_ref(cobranca["external_reference"])["ga_client_id"] is None
