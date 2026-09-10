"""CPF/CNPJ estruturalmente inválido é recusado ANTES de `pix_charges` e do Asaas.

O 400 do documento é a fronteira de confiança do checkout Pix: até 2026-09-10 ele
media só o TAMANHO, então `11111111111` e um CPF com o DV trocado abriam linha em
`pix_charges`, chegavam ao Asaas e só lá caíam. Aqui as duas provas moram
juntas — o status 400 **e** o banco vazio (`_linhas`) **e** o Asaas intocado
(`asaas_falso["ordem"]`), porque recusar com a saga já rodada não é recusar.

`11111111111`, `00000000000`, `99999999999` e `00000000000000` PASSAM no mod-11
puro (medido): por isso a sequência repetida é caso à parte e tem caso próprio.

CONTROLES NEGATIVOS MEDIDOS (um a um, em `frontend/routes/billing_pix.py`):

  * apague `if len(set(doc)) == 1: return False` →
    `test_documento_invalido_nao_toca_o_banco_nem_o_asaas` vermelho nos casos
    `11111111111`, `00000000000` e `00000000000000` (viram 200);
  * troque o corpo de `_documento_valido` por `return len(doc) in (11, 14)` (o
    comportamento anterior) → o mesmo teste vermelho em `52998224724` e
    `11222333000182`, e os positivos seguem verdes;
  * troque o filtro ASCII do começo de `billing_pix_checkout` (o
    `c in "0123456789"` que monta `doc`) de volta para `c.isdigit()` → o mesmo
    teste vermelho em `1234567890²` (500, `int("²")` estoura) e em
    `٥٢٩٩٨٢٢٤٧٢٥` (200, dígito não-ASCII fecha o mod-11 e vai ao Asaas), e o
    positivo `52998224725５` vermelho junto (400: o `５` sobrevive e viram 12
    dígitos). Os demais positivos seguem verdes.

POSITIVOS do grupo (`..._cpf_valido_...` e `..._cnpj_valido_...`): sem eles, um
`_documento_valido` que devolvesse `False` para tudo passaria em todos os
negativos e mataria a venda inteira.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 - `asaas_falso`/`vendavel` são fixtures
    _linhas,
    asaas_falso,
    vendavel,
)

client = TestClient(dashboard.app)


@pytest.fixture(autouse=True)
def _zera_rate_limit():
    """`/billing/pix/checkout` é 20/hour POR IP e todo teste daqui usa o mesmo
    TestClient — sem zerar, os últimos POSTs do arquivo levariam 429 no lugar do
    status medido."""
    from frontend.routes import shared as routes_shared

    routes_shared.limiter.reset()
    yield


def _checkout(user_id, monkeypatch, doc: str, plan: str = "pro"):
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return client.post("/billing/pix/checkout",
                       headers={dashboard.CSRF_HEADER_NAME: token},
                       json={"plan": plan, "cpf_cnpj": doc})


@pytest.mark.parametrize("doc", [
    "11111111111",       # passa no mod-11 puro; recusado pelo caso especial
    "00000000000",       # idem
    "52998224724",       # CPF com o dígito verificador trocado
    "11222333000182",    # CNPJ com o dígito verificador trocado
    "00000000000000",    # CNPJ de sequência repetida, que também passa no mod-11
    "123456789",         # tamanho que não é nem 11 nem 14
    "1234567890²",       # `str.isdigit()` é True para `²` e o `int()` estourava (500)
    "٥٢٩٩٨٢٢٤٧٢٥",       # `52998224725` em árabe-indiano: fechava o mod-11 (200)
])
def test_documento_invalido_nao_toca_o_banco_nem_o_asaas(user_id, vendavel,
                                                         asaas_falso, monkeypatch,
                                                         doc):
    conta(user_id, "free", None)
    r = _checkout(user_id, monkeypatch, doc)

    assert r.status_code == 400, f"{doc}: {r.status_code} {r.text}"
    assert r.json()["detail"] == "Informe um CPF ou CNPJ válido."
    assert _linhas(user_id) == [], f"{doc} abriu linha em pix_charges"
    assert asaas_falso["ordem"] == [], f"{doc} chegou ao Asaas: {asaas_falso['ordem']}"


def test_cpf_valido_emite_a_cobranca(user_id, vendavel, asaas_falso, monkeypatch):
    """POSITIVO: a venda legítima continua saindo, pelo HTTP inteiro."""
    conta(user_id, "free", None)
    r = _checkout(user_id, monkeypatch, "52998224725")

    assert r.status_code == 200, r.text
    assert asaas_falso["ordem"] == ["customer", "create", "qr"]
    linhas = _linhas(user_id)
    assert len(linhas) == 1 and linhas[0]["status"] == "pending"


def test_cnpj_valido_emite_a_cobranca(user_id, vendavel, asaas_falso, monkeypatch):
    """POSITIVO do ramo de 14 dígitos: o CNPJ não foi fechado junto com a recusa."""
    conta(user_id, "free", None)
    r = _checkout(user_id, monkeypatch, "11222333000181")

    assert r.status_code == 200, r.text
    assert asaas_falso["ordem"] == ["customer", "create", "qr"]
    linhas = _linhas(user_id)
    assert len(linhas) == 1 and linhas[0]["status"] == "pending"


@pytest.mark.parametrize("doc", [
    "529.982.247-25",    # a máscara que o campo da /precos manda
    "52998224725５",      # `５` fullwidth no fim: o filtro ASCII descarta e sobra
                         # o CPF válido — antes de 2026-09-10 dava 400 (12 dígitos
                         # sobreviviam ao `isdigit()`). O cliente normaliza igual
                         # (`\D` em JS é ASCII-only), então não há divergência.
])
def test_cpf_com_lixo_ao_redor_dos_digitos_passa(user_id, vendavel, asaas_falso,
                                                 monkeypatch, doc):
    """Sem este caso, a validação podia estar recusando exatamente o que o
    usuário digita."""
    conta(user_id, "free", None)
    r = _checkout(user_id, monkeypatch, doc)

    assert r.status_code == 200, r.text
    assert len(_linhas(user_id)) == 1
