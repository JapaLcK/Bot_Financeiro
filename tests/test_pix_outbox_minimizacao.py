"""MINIMIZAÇÃO do payload da outbox (§13.3) — o que pode ser guardado.

Arquivo próprio, separado de `tests/test_pix_outbox.py` (dedup, efeitos,
attempts), porque o assunto é PRIVACIDADE e a defesa é de outra natureza: lá se
mede contabilidade de eventos; aqui se mede que dado pessoal não chega ao banco.

**A regra é de FORMA, não de nome**, e isso é o conserto de dois defeitos
medidos:

  * a allowlist de CHAVES era rasa. `customer` é campo permitido, e o Asaas pode
    mandá-lo EXPANDIDO — nome, CPF, e-mail, telefone dentro de um campo cujo nome
    está na lista. Também mediu-se dict aninhado em `status` e lista sob `value`;
  * a filtragem por forma barrava a ESTRUTURA e deixava passar escalar de
    QUALQUER tamanho: 1 MB numa string, cifrado, na tabela.

Filtrar por forma (escalar passa truncado; dict vira o `id`; o resto vira
`None`) não depende de alguém prever qual campo o provedor vai expandir da
próxima vez — que é a única defesa que sobrevive a uma API de terceiro.

As asserções olham a COLUNA, não só o dict: é a coluna que sobrevive 7 dias e é
o que um dump levaria.
"""

import json
import uuid

import pytest

from core.crypto import PiiAccessContext, decrypt_pii
from db.connection import get_conn
from db.webhook_outbox import (
    CAMPOS_MINIMOS,
    LIMITE_POR_CAMPO,
    minimizar,
    registrar_evento,
)


def _evt() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def _corpo(pagamento_id: str = "pay_1", **extra) -> dict:
    """Um webhook com o formato real, INCLUSIVE campos que não devem ser
    guardados. Fixture só com campos permitidos faria o teste passar por
    construção."""
    pagamento = {
        "id": pagamento_id, "externalReference": "pix:42", "value": 299.0,
        "netValue": 299.0, "status": "RECEIVED", "dateCreated": "2026-09-07",
        "customer": "cus_abc",
        "customerName": "Fulano de Tal", "customerEmail": "fulano@example.com",
        "cpfCnpj": "12345678901", "billingAddress": "Rua X, 123",
        "invoiceUrl": "https://asaas/i/abc",
    }
    pagamento.update(extra)
    return {"id": "evt_envelope", "event": "PAYMENT_RECEIVED", "payment": pagamento}


def _linha(event_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select payload_enc from pix_webhook_events where event_id = %s",
                    (event_id,))
        return cur.fetchone()


def test_minimizacao_descarta_campo_de_fora_da_lista():
    """Allowlist, não denylist: campo novo do provedor fica de fora SOZINHO."""
    reduzido = minimizar(_corpo())
    assert set(reduzido["payment"]) <= CAMPOS_MINIMOS
    for proibido in ("customerName", "customerEmail", "cpfCnpj",
                     "billingAddress", "invoiceUrl"):
        assert proibido not in reduzido["payment"]


def test_minimizacao_preserva_o_que_o_dreno_precisa():
    """POSITIVO. Sem ele, `minimizar` devolvendo `{}` passaria — e o dreno
    perderia o `payment.id` e o `externalReference`, que casam o evento com a
    cobrança."""
    reduzido = minimizar(_corpo("pay_9"))["payment"]
    assert reduzido["id"] == "pay_9"
    assert reduzido["externalReference"] == "pix:42"
    assert reduzido["value"] == 299.0
    assert reduzido["status"] == "RECEIVED"


@pytest.mark.parametrize("corpo", [{}, {"payment": None}, {"payment": "x"}])
def test_minimizacao_nao_estoura_com_corpo_torto(corpo):
    """O formato é do PROVEDOR. `payment` ausente ou de outro tipo tem de virar
    dict vazio, não `AttributeError` no meio da gravação."""
    assert minimizar(corpo)["payment"] == {}


def test_payload_e_ilegivel_sem_a_chave_e_nao_carrega_pii():
    """Caso 46 do §16: o que está na coluna não é legível, e o que se decifra não
    tem campo fora da lista. A asserção do texto CRU é o que impede a versão
    "cifrei depois de guardar"."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", _corpo(), 1)
    cru = _linha(eid)["payload_enc"]
    for pii in ("Fulano", "fulano@example.com", "12345678901", "Rua X"):
        assert pii not in cru, f"{pii!r} legível na coluna payload_enc"
    assert "pix:42" not in cru, "o payload não está cifrado"

    aberto = json.loads(decrypt_pii(cru, ctx=PiiAccessContext(
        purpose="teste_minimizacao", actor="system:test", subject_user_id=0,
        field="payload_enc")))
    assert set(aberto["payment"]) <= CAMPOS_MINIMOS
    assert aberto["payment"]["externalReference"] == "pix:42"


# ── minimização em PROFUNDIDADE (a allowlist rasa não bastava) ───────────────

CORPO_EXPANDIDO = {
    "id": "evt_1", "event": "PAYMENT_RECEIVED",
    "payment": {
        "id": "pay_1",
        "externalReference": "pix:42",
        "value": 299.0,
        "status": "RECEIVED",
        # `customer` está na allowlist — e o Asaas pode mandá-lo EXPANDIDO.
        "customer": {
            "id": "cus_abc", "name": "Fulano de Tal", "cpfCnpj": "12345678901",
            "email": "fulano@example.com", "mobilePhone": "11999998888",
        },
    },
}


def test_chave_permitida_com_objeto_dentro_nao_vaza():
    """O furo que a allowlist de CHAVES não via: `customer` é campo permitido,
    então filtrar só o primeiro nível copiava o objeto inteiro — com nome, CPF,
    e-mail e telefone do titular — para dentro do payload.

    A redução é por FORMA (escalar passa; dict vira o `id`), não por nome: não
    depende de alguém prever qual campo o provedor vai expandir da próxima vez.
    """
    reduzido = minimizar(CORPO_EXPANDIDO)["payment"]
    assert reduzido["customer"] == "cus_abc", "o `id` é o que o dreno usa para casar"
    achatado = json.dumps(reduzido)
    for pii in ("Fulano", "12345678901", "fulano@example.com", "11999998888"):
        assert pii not in achatado, f"{pii!r} sobreviveu à minimização"


@pytest.mark.parametrize("campo,valor", [
    ("status", {"code": "RECEIVED", "operadorNome": "Fulano de Tal"}),
    ("value", [{"cpf": "12345678901"}]),
    ("dateCreated", {"data": "2026-09-07", "ip": "200.1.2.3"}),
])
def test_estrutura_em_qualquer_campo_permitido_e_descartada(campo, valor):
    """As outras duas formas que o Tester mediu, mais uma. Nenhuma delas é
    `customer` — que é o ponto: a defesa não pode ser uma lista de campos
    suspeitos, porque a próxima expansão será num campo que ninguém listou."""
    corpo = {"id": "e", "event": "X", "payment": {campo: valor, "id": "pay_1"}}
    reduzido = minimizar(corpo)["payment"]
    assert reduzido[campo] is None, f"{campo} manteve a estrutura: {reduzido[campo]!r}"
    assert "Fulano" not in json.dumps(reduzido)
    assert "12345678901" not in json.dumps(reduzido)


def test_escalares_continuam_passando_inteiros():
    """POSITIVO, e sem ele o grupo passaria num código que zera TUDO — o dreno
    perderia `value`, `status` e `externalReference`, e nenhuma cobrança seria
    conferida contra o que entrou."""
    reduzido = minimizar(_corpo("pay_5"))["payment"]
    assert reduzido["id"] == "pay_5"
    assert reduzido["value"] == 299.0
    assert reduzido["status"] == "RECEIVED"
    assert reduzido["externalReference"] == "pix:42"
    assert reduzido["customer"] == "cus_abc"


def test_payload_cifrado_nao_carrega_pii_de_customer_expandido():
    """Ponta a ponta: o que chega à COLUNA, com o `customer` expandido. Olhar o
    dict não bastava — é a coluna que sobrevive 7 dias e pode ser dumpada."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED", CORPO_EXPANDIDO, 1)
    cru = _linha(eid)["payload_enc"]
    for pii in ("Fulano", "12345678901", "fulano@example.com", "11999998888"):
        assert pii not in cru
    aberto = json.loads(decrypt_pii(cru, ctx=PiiAccessContext(
        purpose="teste_minimizacao", actor="system:test", subject_user_id=0,
        field="payload_enc")))
    assert aberto["payment"]["customer"] == "cus_abc"


def test_escalar_gigante_e_truncado():
    """O teto que a filtragem por FORMA não dava: ela barrava a ESTRUTURA e
    deixava passar escalar de qualquer tamanho.

    Medido: 1 MB numa string, cifrado, na tabela. Nenhum dos nove campos do
    §13.3 é texto livre — o maior (`externalReference`, `pix:<id>`) tem menos de
    30 chars —, então 200 é folga de quase 7×, e o que passa de lá é carga, não
    dado.

    Trunca em vez de descartar: o começo de um valor esquisito é o que serve
    para depurar por que ele era esquisito.
    """
    corpo = {"id": "e", "event": "X",
             "payment": {"id": "pay_1", "externalReference": "x" * 1_000_000}}
    reduzido = minimizar(corpo)["payment"]
    assert len(reduzido["externalReference"]) == LIMITE_POR_CAMPO
    assert reduzido["id"] == "pay_1", "o campo curto passa inteiro"


def test_id_expandido_tambem_respeita_o_teto():
    """O `id` extraído de um dict passa pela MESMA redução — senão o teto teria
    um caminho por baixo: `{"customer": {"id": "x"*1MB}}`."""
    corpo = {"id": "e", "event": "X",
             "payment": {"customer": {"id": "y" * 5000, "name": "Fulano"}}}
    reduzido = minimizar(corpo)["payment"]
    assert len(reduzido["customer"]) == LIMITE_POR_CAMPO
    assert "Fulano" not in json.dumps(reduzido)


def test_payload_cifrado_tem_tamanho_limitado():
    """POSITIVO ponta a ponta: o que chega à COLUNA fica pequeno. Sem o teto,
    esta linha era ~1,3 MB."""
    eid = _evt()
    registrar_evento(eid, "PAYMENT_RECEIVED",
                     {"id": "e", "event": "X",
                      "payment": {"id": "p", "status": "z" * 1_000_000}}, 1)
    assert len(_linha(eid)["payload_enc"]) < 4000


# `ids=` explícito: sem ele o pytest usa o repr do parâmetro, e o `10 ** 4000`
# imprime 4.001 dígitos no nome do teste — em toda saída, de todo CI.
ESCALARES_GIGANTES = [
    ("int gigante", 10 ** 4000),
    ("float com expoente longo", float("1e308")),
    ("str gigante", "z" * 100_000),
]


@pytest.mark.parametrize("rotulo,valor", ESCALARES_GIGANTES,
                         ids=[r for r, _ in ESCALARES_GIGANTES])
def test_o_teto_vale_para_QUALQUER_escalar_nao_so_str(rotulo, valor):
    """O teto era sobre o TIPO `str`, não sobre o tamanho — e um `int` gigante
    passava inteiro: 4.351 bytes medidos na coluna. O único freio era o
    `json.loads` do CPython estourando antes, o que não é política de retenção.

    Nenhum caso anterior usava `int`, então o teste do teto passava sem medir
    esta metade. Agora o corte é pelo tamanho SERIALIZADO, seja qual for o tipo.
    """
    corpo = {"id": "e", "event": "X", "payment": {"id": "pay_1", "value": valor}}
    guardado = minimizar(corpo)["payment"]["value"]
    assert len(str(guardado)) <= LIMITE_POR_CAMPO, f"{rotulo} passou inteiro"


@pytest.mark.parametrize("valor", [299.0, 29900, True, False, None, "pix:42"])
def test_escalar_que_CABE_mantem_tipo_e_valor(valor):
    """POSITIVO do par, e ele protege dinheiro: sem esta asserção, um teto que
    convertesse tudo em string passaria — e o dreno confere `value` em centavos
    contra o esperado. `bool` está aqui porque é subclasse de `int`: sem a saída
    antecipada, `True` viraria a string `"True"`."""
    corpo = {"id": "e", "event": "X", "payment": {"value": valor, "id": "p"}}
    guardado = minimizar(corpo)["payment"]["value"]
    assert guardado == valor and type(guardado) is type(valor)
