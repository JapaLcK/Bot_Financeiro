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

O SEGUNDO grupo (desde 2026-09-10) é o documento que passa aqui e o **Asaas**
recusa: estruturalmente válido, e mesmo assim o `POST /v3/customers` devolve 400.
Isso virava o 503 de "tenta de novo em instantes" — mentira, porque o mesmo corpo
dá o mesmo erro para sempre. Agora é 400 com texto próprio, e a linha volta para
`draft` (nada existe no Asaas: `criar_pagamento_pix` nem chegou a rodar).

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * apague o `raise TitularRecusado(exc.code)` de
    `core/services/asaas_customers.py` →
    `test_asaas_recusa_o_titular_devolve_400_e_deixa_a_linha_em_draft` VERMELHO
    (503 no lugar do 400) e
    `test_retentativa_depois_da_recusa_nao_pergunta_nada_ao_asaas` VERMELHO junto;
  * apague **só** o `voltar_para_draft(linha["id"])` de `_emitir`, mantendo o
    `raise` → o primeiro VERMELHO na asserção `status == "draft"` e o segundo
    VERMELHO pela `consulta` extra: a linha fica `creating` e a passada seguinte
    vai PERGUNTAR ao Asaas por uma cobrança que nunca existiu;
  * troque `exc.status_code in (400, 422)` por `400 <= exc.status_code < 500` →
    `test_erro_que_nao_e_do_cliente_continua_503` VERMELHO nos TRÊS casos (429, 401
    e 403), todos acusando o CPF do cliente por erro que não é dele;
  * troque por `exc.status_code in (400, 422, 401, 403)` — a mutação que
    DISCRIMINA, porque atinge só a nossa credencial e deixa o 429 de fora → o mesmo
    teste VERMELHO em 401 e 403, e verde em 429. Até 2026-09-10 essa mutação não
    derrubava caso NENHUM da suíte: o parêntese "e, pela mesma condição, o 401/403"
    estava escrito aqui e não era medido. É o incidente de 10/09 — a NOSSA chave de
    API errada dizendo ao cliente que o CPF dele não presta.

POSITIVOS do grupo (`..._cpf_valido_...`, `..._cnpj_valido_...` e
`test_asaas_fora_do_ar_continua_503_com_a_linha_em_creating`): sem eles, um
`_documento_valido` que devolvesse `False` para tudo — ou um `criar_cliente` que
levantasse `TitularRecusado` para qualquer erro — passaria em todos os negativos e
mataria a venda inteira.
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
from core.services.asaas import AsaasApiError
from db.connection import get_conn

# O CPF estruturalmente válido dos casos do Asaas: ele PRECISA passar pelo
# `_documento_valido` para a recusa medida ser a de lá, não a daqui.
CPF_OK = "52998224725"
RECUSA_DO_ASAAS = ("O banco recusou esses dados. Confere o CPF ou CNPJ — se "
                   "estiver certo, fala com a gente.")
INDISPONIVEL = "Não consegui emitir o Pix agora. Tenta de novo em instantes."

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


# ── o documento que passa aqui e o ASAAS recusa ──────────────────────────────

def _recusa(status: int, code: str | None = "invalid_cpfCnpj") -> AsaasApiError:
    """O erro como `_raise_for_asaas_response` o constrói: contexto + status +
    `code` já filtrado, e **sem** corpo."""
    return AsaasApiError(
        f"Falha ao criar cliente no Asaas: Asaas retornou HTTP {status}"
        + (f" (code={code})" if code else ""),
        status_code=status, code=code)


def test_asaas_recusa_o_titular_devolve_400_e_deixa_a_linha_em_draft(
        user_id, vendavel, asaas_falso, monkeypatch):
    """DISCRIMINA. 400 do Asaas é culpa do dado, e o 503 dizia o contrário."""
    conta(user_id, "free", None)
    asaas_falso["cliente_falha"] = _recusa(400)
    r = _checkout(user_id, monkeypatch, CPF_OK)

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == RECUSA_DO_ASAAS
    linhas = _linhas(user_id)
    assert len(linhas) == 1 and linhas[0]["status"] == "draft", linhas
    # Nem `create` nem `qr`: a saga parou no titular, então NÃO há cobrança lá.
    assert asaas_falso["ordem"] == ["customer"], asaas_falso["ordem"]


def test_retentativa_depois_da_recusa_nao_pergunta_nada_ao_asaas(
        user_id, vendavel, asaas_falso, monkeypatch):
    """A CONVERSA, não a função (§3): duas requisições, mesmo usuário, mesmo
    banco. É este teste que mede a escolha do `draft` — com a linha em `creating`
    a substituição chama `id_remoto_vivo`, que é um GET no Asaas por uma cobrança
    que nunca existiu (e cujo modo de falha, `asaas_cancelamento_falhou`, já foi
    visto em produção)."""
    conta(user_id, "free", None)
    asaas_falso["cliente_falha"] = _recusa(400)
    assert _checkout(user_id, monkeypatch, CPF_OK).status_code == 400

    asaas_falso["cliente_falha"] = None
    asaas_falso["ordem"].clear()
    r = _checkout(user_id, monkeypatch, CPF_OK)

    assert r.status_code == 200, r.text
    assert r.json()["qr_payload"]
    assert "consulta" not in asaas_falso["ordem"], asaas_falso["ordem"]


def test_asaas_fora_do_ar_continua_503_com_a_linha_em_creating(
        user_id, vendavel, asaas_falso, monkeypatch):
    """POSITIVO do grupo. 5xx é o Asaas, não o dado: segue 503, com o mesmo texto
    de antes, e a linha fica `creating` — o estado ambíguo que a varredura trata,
    porque daí em diante pode haver cobrança lá."""
    conta(user_id, "free", None)
    asaas_falso["cliente_falha"] = _recusa(502, code=None)
    r = _checkout(user_id, monkeypatch, CPF_OK)

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == INDISPONIVEL
    assert _linhas(user_id)[0]["status"] == "creating"


@pytest.mark.parametrize("status", [429, 401, 403])
def test_erro_que_nao_e_do_cliente_continua_503(
        user_id, vendavel, asaas_falso, monkeypatch, status):
    """4xx que NÃO é o dado do titular: 503, e nunca "confere o CPF".

    429 é fila cheia e retentar ajuda — exatamente o que o 503 pede. 401 e 403 são
    a NOSSA `ASAAS_API_KEY`, e foram o incidente de 10/09: acusar o documento do
    cliente por credencial nossa errada é o pior desfecho possível. Os três juntos
    porque a condição que os separa é uma só, e 401/403 não tinham caso nenhum.

    A linha fica `creating` como no 502: daqui em diante o estado é ambíguo para a
    varredura, e é o que separa este grupo do `draft` da recusa do titular.
    """
    conta(user_id, "free", None)
    asaas_falso["cliente_falha"] = _recusa(status, code=None)
    r = _checkout(user_id, monkeypatch, CPF_OK)

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == INDISPONIVEL
    assert _linhas(user_id)[0]["status"] == "creating"


def test_a_recusa_nao_vaza_o_documento_nem_o_corpo(user_id, vendavel, asaas_falso,
                                                   monkeypatch):
    """O corpo do erro do Asaas traz o documento por extenso ("O CPF 529... é
    inválido"), e `system_event_logs` é a tabela que a purga do §13.3 NÃO alcança.
    Nem a resposta nem a linha de log podem carregar os 11 dígitos."""
    conta(user_id, "free", None)
    asaas_falso["cliente_falha"] = AsaasApiError(
        f"Falha ao criar cliente no Asaas: o CPF {CPF_OK} e invalido",
        status_code=400, code="invalid_cpfCnpj")
    r = _checkout(user_id, monkeypatch, CPF_OK)

    assert r.status_code == 400, r.text
    assert CPF_OK not in r.text
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select message, details from system_event_logs "
                    "where user_id = %s", (user_id,))
        linhas = [dict(x) for x in cur.fetchall()]
    assert linhas, "o log da recusa não foi escrito"
    assert not [x for x in linhas if CPF_OK in str(x)], linhas
