"""`db/webhook_outbox.py` + `db/pix_effects.py` — a outbox do webhook do Asaas
e o registro de efeitos (§3.3, §3.4, §8.1 e §13.3).

Duas propriedades decidem dinheiro aqui:

  1. **dedup por `event_id`** — o Asaas reentrega, e o 200 da duplicata não pode
     custar trabalho nenhum (§8.1);
  2. **o par `(asaas_payment_id, effect)`** — a chave é o PAGAMENTO, não o
     evento (correção nº 8b). Dois eventos DISTINTOS do mesmo `payment.id`
     rodam os efeitos UMA vez; com a chave no evento, um `RECEIVED` reentregue
     com `event_id` novo mandaria um segundo `purchase` ao GA4 e um segundo
     `Purchase` à CAPI em cima do mesmo dinheiro.

E uma que decide PRIVACIDADE: **a minimização acontece na ESCRITA** (§13.3), não
na leitura. Campo que o Asaas passe a mandar — endereço, telefone, CPF do
titular — não chega ao banco nem cifrado.

CONTROLES NEGATIVOS MEDIDOS:

  * troque o `on conflict (event_id) do nothing` por insert seco →
    `test_reentrega_do_mesmo_evento_nao_cria_linha` vermelho (com
    `UniqueViolation`, que é o sintoma em produção: 5xx e o Asaas retentando);
  * troque a allowlist `CAMPOS_MINIMOS` por "copia o payment inteiro" →
    `test_minimizacao_descarta_campo_de_fora_da_lista` vermelho;
  * troque a chave de `pix_payment_effects` para `event_id` →
    `test_efeito_do_mesmo_pagamento_nao_repete_com_event_id_novo` vermelho.

POSITIVOS: `test_evento_novo_entra_e_fica_pendente`,
`test_efeito_novo_do_mesmo_pagamento_e_registrado` e
`test_minimizacao_preserva_o_que_o_dreno_precisa` — sem eles o grupo passaria
num código que recusa tudo e guarda nada.
"""

import json
import uuid

import pytest

from core.crypto import PiiAccessContext, decrypt_pii
from db.connection import get_conn
from db.pix_effects import EFEITOS, efeito_registrado, registrar_efeito
from db.webhook_outbox import (
    CAMPOS_MINIMOS,
    _erro_seguro,
    marcar_processado,
    minimizar,
    registrar_evento,
    registrar_falha,
)


def _evt() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def _corpo(pagamento_id: str = "pay_1", **extra) -> dict:
    """Um webhook do Asaas com o formato real, INCLUSIVE campos que não devem
    ser guardados. Se a fixture só tivesse os campos permitidos, o teste de
    minimização passaria por construção — é o defeito de fixture que o
    CLAUDE.md §3 chama de cego."""
    pagamento = {
        "id": pagamento_id,
        "externalReference": "pix:42",
        "value": 299.0,
        "netValue": 299.0,
        "status": "RECEIVED",
        "dateCreated": "2026-09-07",
        "customer": "cus_abc",
        # PII do titular — o Asaas manda, e nada disto pode chegar ao banco.
        "customerName": "Fulano de Tal",
        "customerEmail": "fulano@example.com",
        "cpfCnpj": "12345678901",
        "billingAddress": "Rua X, 123",
        "invoiceUrl": "https://asaas/i/abc",
    }
    pagamento.update(extra)
    return {"id": "evt_envelope", "event": "PAYMENT_RECEIVED", "payment": pagamento}


def _linha(event_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select event_id, event_type, payload_enc, event_version, processed_at,"
            "       purged_at, attempts, last_error"
            " from pix_webhook_events where event_id = %s",
            (event_id,),
        )
        return cur.fetchone()


# ── 1. dedup por event_id ────────────────────────────────────────────────────

def test_evento_novo_entra_e_fica_pendente():
    """POSITIVO: sem ele o grupo passaria num código que nunca grava."""
    eid = _evt()
    assert registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1700) is True
    linha = _linha(eid)
    assert linha["event_type"] == "PAYMENT_RECEIVED"
    assert linha["event_version"] == 1700
    assert linha["processed_at"] is None   # o dreno é quem fecha (1b-B)
    assert linha["attempts"] == 0


def test_reentrega_do_mesmo_evento_nao_cria_linha():
    """Caso 27 do §16: reentrega → 200 e UMA linha, zero trabalho.

    `False` vem do `returning` vazio do `on conflict do nothing`, não de um
    `select` antes do `insert` — entre os dois cabe a segunda entrega, e o Asaas
    reentrega em paralelo quando o primeiro POST demora.
    """
    eid = _evt()
    assert registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1700) is True
    assert registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1800) is False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from pix_webhook_events where event_id = %s", (eid,))
        assert cur.fetchone()["n"] == 1
    # E a reentrega NÃO reescreveu a versão da primeira: quem decide ordem é o
    # dreno pela versão gravada, não a última entrega a chegar.
    assert _linha(eid)["event_version"] == 1700


def test_marcar_processado_so_fecha_evento_aberto():
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    assert marcar_processado(eid) is True
    assert marcar_processado(eid) is False, (
        "passada atrasada reescreveu o carimbo de um evento já concluído"
    )


def test_registrar_falha_devolve_o_attempts_novo():
    """O `admin_notify` de `attempts > 5` (§8.2) é decidido pelo dreno com este
    número. Reler a linha depois do UPDATE deixaria caber outra passada entre os
    dois — o alerta sairia duplicado ou nenhuma vez."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    assert [registrar_falha(eid, "ReadTimeout") for _ in range(3)] == [1, 2, 3]
    assert _linha(eid)["last_error"] == "ReadTimeout"


@pytest.mark.parametrize("tipo,codigo,esperado", [
    ("ReadTimeout", None, "ReadTimeout"),
    ("AsaasApiError", "invalid_cpfCnpj", "AsaasApiError(invalid_cpfCnpj)"),
    # A mensagem inteira entrando pelo parâmetro `tipo` — o caminho que a
    # assinatura antiga (`erro: str`) tornava natural.
    ("Falha ao criar: CPF 12345678901 de Fulano (a@b.com)", None, "?"),
    ("AsaasApiError", "CPF 12345678901 invalido", "AsaasApiError(?)"),
    ("", None, "?"),
    # P2 do Codex no #304: o CPF SOZINHO, sem mensagem em volta. Todo dígito é
    # `isalnum()`, então a cópia divergente de `_codigo_seguro` que morava aqui
    # ACEITAVA estas três — a forma de um CPF é exatamente a de um código curto.
    ("12345678901", None, "?"),
    ("123.456.789-01", None, "?"),
    ("AsaasApiError", "12345678901", "AsaasApiError(?)"),
    # P2 do Codex no #305: o documento com PREFIXO. Nenhum destes é
    # `isdigit()`, então a recusa de só-dígitos deixava o CPF/CNPJ inteiro
    # entrar no `last_error`.
    ("CPF12345678901", None, "?"),
    ("cpf_12345678901", None, "?"),
    ("invalid_123.456.789-01", None, "?"),
    ("CNPJ12345678000199", None, "?"),
    ("AsaasApiError", "cnpj-12.345.678-0001-99", "AsaasApiError(?)"),
    # 3ª rodada: `_` era separador ACEITO e não normalizado (§2).
    ("cpf_123_456_789_01", None, "?"),
    ("123_456_789_01", None, "?"),
    ("AsaasApiError", "cpf-123.456_789-01", "AsaasApiError(?)"),
    # POSITIVO: código legítimo COM dígito continua passando. Sem estas
    # linhas, um filtro que recusasse todo dígito passaria acima — e
    # `last_error` sem diagnóstico é pior que a coluna não existir.
    ("AsaasApiError", "error_400", "AsaasApiError(error_400)"),
    ("AsaasApiError", "HTTP_502", "AsaasApiError(HTTP_502)"),
    ("AsaasApiError", "code-42", "AsaasApiError(code-42)"),
    ("AsaasApiError", "asaas_invalid_object", "AsaasApiError(asaas_invalid_object)"),
    ("payment_not_found", None, "payment_not_found"),
    ("TransactionRollbackError", "v1.2.3", "TransactionRollbackError(v1.2.3)"),
])
def test_last_error_nao_aceita_texto_livre(tipo, codigo, esperado):
    r"""P2-6 do Codex. A purga do §13.3 zera `last_error` junto com o payload,
    mas dentro dos 7 dias o valor está lá — e vai para `system_event_logs`.

    A versão anterior recebia `erro: str` e guardava os primeiros 500 chars.
    Truncar não removia nada: CPF, e-mail e nome aparecem no COMEÇO da mensagem.
    Agora a assinatura só aceita `tipo` + `codigo`, e os dois passam pelo filtro
    de FORMA — **e desde o 1b-B ela é literalmente a MESMA função**: `_erro_seguro`
    virou uma chamada a `core.services.asaas._codigo_seguro(valor, "?")`, e a
    cópia (que já divergiu uma vez, aceitando `"12345678901"` porque todo dígito
    passa no `isalnum()`, P2 do Codex no #304) morreu junto com
    `test_erro_seguro_nao_divergiu_do_codigo_seguro`. Os **29** valores que aquela
    media migraram para `tests/test_asaas_codigo_seguro.py` — não para
    `tests/test_asaas_client.py`, que é o transporte HTTP, e não eram "três".
    (O ponteiro errado e a contagem errada estavam aqui, e foi por eles que
    `"x"*60`, `"0001-12345-6"` e `"42"` sumiram sem ninguém notar.)

    *Negativo: faça `_erro_seguro` devolver `tipo` sem filtrar → as linhas com
    PII ficam vermelhas. Negativo da corrida de 11+ dígitos: apague o
    `re.search(r"\d{11,}", nu)` → só os casos com PREFIXO ficam vermelhos (CPF
    puro segue verde pelo `isdigit()`). Negativo da normalização: tire o `_` de
    `_SEPARADORES` → só as três grafias com `_` ficam vermelhas — cada mutação
    discrimina numa grafia, porque a categoria É a grafia.*
    """
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    registrar_falha(eid, tipo, codigo)
    guardado = _linha(eid)["last_error"]
    assert guardado == esperado
    for pii in ("12345678901", "12345678000199", "Fulano", "a@b.com"):
        assert pii not in guardado


# ── 2. minimização e cifra do payload (§13.3) ────────────────────────────────

def test_minimizacao_descarta_campo_de_fora_da_lista():
    """Allowlist, não denylist: campo novo do provedor fica de fora SOZINHO.
    Uma denylist precisaria que alguém previsse o nome dele."""
    reduzido = minimizar(_corpo())
    assert set(reduzido["payment"]) <= CAMPOS_MINIMOS
    for proibido in ("customerName", "customerEmail", "cpfCnpj",
                     "billingAddress", "invoiceUrl"):
        assert proibido not in reduzido["payment"]


def test_minimizacao_preserva_o_que_o_dreno_precisa():
    """POSITIVO do par. Sem ele, `minimizar` podendo devolver `{}` passaria — e
    o dreno perderia o `payment.id` e o `externalReference`, que são o que casa
    o evento com a nossa cobrança."""
    reduzido = minimizar(_corpo("pay_9"))["payment"]
    assert reduzido["id"] == "pay_9"
    assert reduzido["externalReference"] == "pix:42"
    assert reduzido["value"] == 299.0
    assert reduzido["status"] == "RECEIVED"


@pytest.mark.parametrize("corpo", [{}, {"payment": None}, {"payment": "x"}])
def test_minimizacao_nao_estoura_com_corpo_torto(corpo):
    """O formato é do PROVEDOR. O handler já respondeu 400 para corpo sem id de
    evento (§8.1); a partir daí, um `payment` ausente ou de outro tipo tem de
    virar dict vazio, não `AttributeError` no meio da gravação."""
    assert minimizar(corpo)["payment"] == {}


def test_payload_e_ilegivel_sem_a_chave_e_nao_carrega_pii():
    """Caso 46 do §16, as duas metades: o que está no banco não é texto legível,
    e o que se decifra não tem campo fora da lista.

    A asserção do texto cru é o que impede a versão "cifrei depois de guardar":
    procurar a PII na COLUNA é diferente de procurar no dict.
    """
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    cru = _linha(eid)["payload_enc"]

    for pii in ("Fulano", "fulano@example.com", "12345678901", "Rua X"):
        assert pii not in cru, f"{pii!r} legível na coluna payload_enc"
    assert "pix:42" not in cru, "o payload não está cifrado — está em texto puro"

    aberto = json.loads(decrypt_pii(cru, ctx=PiiAccessContext(
        purpose="teste_minimizacao", actor="system:test", subject_user_id=0,
        field="payload_enc")))
    assert set(aberto["payment"]) <= CAMPOS_MINIMOS
    assert aberto["payment"]["externalReference"] == "pix:42"


# ── 3. efeitos, chaveados pelo PAGAMENTO (§3.4) ──────────────────────────────

def test_efeito_novo_do_mesmo_pagamento_e_registrado():
    """POSITIVO: efeitos DIFERENTES do mesmo pagamento rodam todos. Sem ele o
    grupo passaria num código que registra o primeiro e recusa o resto — e a
    venda ficaria sem grant, sem GA4 e sem e-mail."""
    pay = f"pay_{uuid.uuid4().hex[:10]}"
    for efeito in ("stripe_cancel", "grant", "ga4", "capi", "email"):
        assert registrar_efeito(pay, efeito, _evt()) is True
        assert efeito_registrado(pay, efeito) is True


def test_efeito_do_mesmo_pagamento_nao_repete_com_event_id_novo():
    """A correção nº 8b, no caminho REAL: a plataforma reentrega o
    `PAYMENT_RECEIVED` com `event_id` NOVO. Com a chave no evento, `ga4` e `capi`
    rodariam de novo — receita duplicada em cima do mesmo dinheiro.

    Chave no PAGAMENTO: o segundo registro devolve False e o dreno pula o efeito.
    """
    pay = f"pay_{uuid.uuid4().hex[:10]}"
    assert registrar_efeito(pay, "ga4", "evt_primeiro") is True
    assert registrar_efeito(pay, "ga4", "evt_segundo_id_diferente") is False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select event_id from pix_payment_effects"
            " where asaas_payment_id = %s and effect = 'ga4'", (pay,))
        linhas = cur.fetchall()
    assert len(linhas) == 1
    # A forense guarda QUEM executou — a primeira entrega, não a última.
    assert linhas[0]["event_id"] == "evt_primeiro"


def test_efeito_de_outro_pagamento_nao_e_confundido():
    """Isolamento entre pagamentos: `ga4` de `pay_A` não marca `pay_B` como
    feito. É o que impede uma compra de sair sem GA4 porque outra já rodou."""
    a, b = f"pay_{uuid.uuid4().hex[:8]}", f"pay_{uuid.uuid4().hex[:8]}"
    registrar_efeito(a, "ga4", _evt())
    assert efeito_registrado(b, "ga4") is False


def test_efeito_desconhecido_e_recusado():
    """Um `'grantt'` gravado seria um efeito que a consulta NUNCA encontra —
    logo, reexecutado a cada passada do dreno, para sempre. Falhar alto na
    escrita é a única hora em que alguém vê."""
    with pytest.raises(ValueError):
        registrar_efeito("pay_x", "grantt", _evt())
    assert set(EFEITOS) == {
        "stripe_cancel", "grant", "ga4", "capi", "email", "revoke",
        "orphan_notified",
    }, "a lista de efeitos do §3.4 mudou — o dreno do 1b-B depende dela"
