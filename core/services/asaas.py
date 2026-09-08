"""
core/services/asaas.py — cliente HTTP do Asaas (cobrança Pix).

Molde: `core/services/pluggy.py`. Mesmo `httpx`, mesma forma de erro, mesma
regra de NÃO carregar o corpo da resposta.

Plano: docs/plano_pix_anual_asaas.md §10 e §10.1.

**Fatia INERTE (PR 1b-A): nenhum módulo de produção importa este arquivo.**
Os chamadores são o checkout e a varredura de reconciliação, no PR 1b-B.

Fora daqui de propósito: a criação de CLIENTE (`POST /customers`). Ela carrega
`cpfCnpj` — obrigatório para o Asaas e **não persistido** por nós —, e essa
decisão é de fluxo de checkout, não de transporte HTTP. Entra no 1b-B, junto de
quem a chama.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class AsaasConfigError(RuntimeError):
    """Falta configuração (`ASAAS_API_KEY`). Quem chama devolve 503 — não é
    erro do usuário nem do provedor."""


class AsaasApiError(RuntimeError):
    """Erro HTTP do Asaas.

    `status_code` existe para distinguir **404** (a cobrança não existe lá, e
    isso é resposta de negócio na reconciliação do §10.1) de **5xx/429** (o
    provedor está fora, e indisponibilidade NÃO é evidência de inexistência).
    Sem ele, o único jeito de decidir seria regex na mensagem — e a decisão que
    depende disso apaga cobrança.

    **NÃO carrega o corpo da resposta**, pelo mesmo motivo medido no Pluggy: o
    corpo de erro traz PII do titular (nome, e-mail, CPF do cliente da cobrança),
    e `str(exc)` desta exceção vira `details` de `log_system_event` — PERSISTIDO
    em `system_event_logs` e lido pelo painel admin. Aqui é pior que no Pluggy:
    a mesma string vai para `pix_webhook_events.last_error`, que SOBREVIVE à
    purga do payload (§13.3) e à exclusão da conta.
    """

    def __init__(self, message: str, *, status_code: int | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _base_url() -> str:
    """Sandbox e produção são hosts DIFERENTES no Asaas, então a base é env e
    não constante — apontar para produção num teste de sandbox move dinheiro
    real."""
    return (os.getenv("ASAAS_BASE_URL") or "https://api.asaas.com").rstrip("/")


def _timeout() -> float:
    """Env malformada NÃO derruba a venda: cai no padrão e segue.

    `float("vinte")` estourava `ValueError` dentro do `_request`, ou seja na
    hora da cobrança, por um erro de digitação no painel de env. Timeout é
    parâmetro operacional — o lado seguro é o padrão, não a recusa."""
    try:
        return float(os.getenv("ASAAS_TIMEOUT") or 20)
    except ValueError:
        return 20.0


def _api_key() -> str:
    chave = (os.getenv("ASAAS_API_KEY") or "").strip()
    if not chave:
        raise AsaasConfigError("ASAAS_API_KEY precisa estar configurada.")
    return chave


def _codigo_seguro(valor: Any) -> str:
    """Só o que PARECE código curto passa (mesma regra do `safe_code` do
    Pluggy): letras, dígitos, `_`, `-` e `.`, até 60 chars.

    A descrição do erro do Asaas vem no MESMO objeto que o `code`
    (`{"errors": [{"code": …, "description": "O CPF/CNPJ 123… é inválido"}]}`),
    e a descrição carrega o dado do titular. Filtrar por FORMA é o que impede
    alguém de ampliar isto para "só o campo description, que é curtinho".
    """
    texto = str(valor or "")
    if not texto or len(texto) > 60:
        return ""
    if not all(c.isalnum() or c in "_-." for c in texto):
        return ""
    # Só-dígitos é recusado: a forma de um CPF ("12345678901") é exatamente a de
    # um código curto, e o código do Asaas é sempre nominal
    # ("invalid_cpfCnpj"). Recusar identificador puramente numérico custa nada —
    # não existe `code` só-dígitos na API — e fecha o caminho por onde um
    # documento entraria numa string persistida.
    return "" if texto.replace("-", "").replace(".", "").isdigit() else texto


def _raise_for_asaas_response(resp: httpx.Response, contexto: str) -> None:
    """Levanta `AsaasApiError` SEM o corpo. Sobra o HTTP status e, quando tem
    forma de código, o `code` do Asaas.

    Testado sem transporte nenhum, com uma `httpx.Response` fabricada: a
    ausência do corpo é propriedade da FUNÇÃO, não da chamada de rede.
    """
    if resp.is_success:
        return
    try:
        corpo: Any = resp.json()
    except ValueError:
        corpo = None
    # O Asaas devolve `{"errors": [{"code": ..., "description": ...}]}`.
    code = ""
    if isinstance(corpo, dict):
        erros = corpo.get("errors")
        if isinstance(erros, list) and erros and isinstance(erros[0], dict):
            code = _codigo_seguro(erros[0].get("code"))
    raise AsaasApiError(
        f"{contexto}: Asaas retornou HTTP {resp.status_code}"
        + (f" (code={code})" if code else ""),
        status_code=resp.status_code,
        code=code or None,
    )


def _request(metodo: str, path: str, *, contexto: str,
             json: dict | None = None,
             params: dict | None = None) -> dict | list:
    """Chamada autenticada. `path` começa com '/'.

    Um ponto só para base, header, timeout e tratamento de erro: as três
    operações abaixo são o mesmo transporte com verbo diferente, e duplicar o
    `_raise_for_asaas_response` em cada uma é como uma delas voltaria a vazar o
    corpo (CLAUDE.md §0.1).

    `access_token` no HEADER, nunca em query string: URL vai para log de proxy.
    """
    # ANTES de abrir o cliente: falta de configuração é 503 nosso, e não pode
    # depender de a leitura da env calhar de acontecer antes do socket.
    chave = _api_key()
    try:
        with httpx.Client(timeout=_timeout()) as client:
            resp = client.request(
                metodo,
                f"{_base_url()}{path}",
                headers={"access_token": chave,
                         "Content-Type": "application/json"},
                json=json,
                params=params,
            )
    except httpx.HTTPError as exc:
        # `ConnectError`, `ReadTimeout`, `RemoteProtocolError` — TODO erro de
        # transporte do httpx desce de `HTTPError`. Sem este `except` eles
        # escapavam crus, e a promessa de que "indisponibilidade vira
        # AsaasApiError" era só docstring. O 1b-B decide APAGAR COBRANÇA com
        # base nesse contrato (§10.1 regra (b)): um `ReadTimeout` que vazasse
        # como outra exceção pode ser engolido por um `except Exception` do
        # chamador e virar "consultei e não achou".
        #
        # `status_code=None` diz "não houve resposta", que é diferente de 404.
        # `type(exc).__name__` e não `str(exc)`: a mensagem do httpx carrega a
        # URL, e a URL pode carregar parâmetro de busca.
        raise AsaasApiError(
            f"{contexto}: falha de transporte ({type(exc).__name__})",
            status_code=None,
        ) from exc
    _raise_for_asaas_response(resp, contexto)
    try:
        return resp.json()
    except ValueError as exc:
        # 200 com corpo não-JSON (HTML de proxy) ou 204 sem corpo. `is_success`
        # já passou, então o erro cairia aqui como `ValueError` solto no meio da
        # venda. Vira o mesmo tipo do resto, com o status preservado.
        raise AsaasApiError(
            f"{contexto}: Asaas respondeu HTTP {resp.status_code} sem JSON válido",
            status_code=resp.status_code,
        ) from exc


def criar_pagamento_pix(*, customer_id: str, valor_cents: int, due_date: str,
                        external_reference: str,
                        descricao: str | None = None) -> dict:
    """`POST /v3/payments` com `billingType: PIX`.

    `valor_cents` é inteiro aqui e vira reais na fronteira — a divisão por 100
    acontece num lugar só, o mais tarde possível, porque o Asaas fala em reais
    e o resto do sistema fala em centavos (§3.2: "centavos inteiros, sem float").

    `external_reference` é `pix:<id>` e é o que amarra o evento à nossa linha:
    é o formato que o §11 usa para separar dinheiro NOSSO de outro Pix que a
    conta do negócio recebe.

    `customer_id` vem de fora porque a criação de cliente é do 1b-B (ver o topo
    do arquivo).
    """
    corpo = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": round(int(valor_cents) / 100, 2),
        "dueDate": due_date,
        "externalReference": external_reference,
    }
    if descricao:
        corpo["description"] = descricao
    return _request("POST", "/v3/payments", contexto="Falha ao criar cobranca Pix",
                    json=corpo)


def buscar_por_external_reference(external_reference: str) -> list[dict]:
    """`GET /v3/payments?externalReference=…` — a consulta que RECONCILIA (§10.1).

    Devolve a lista `data`, e a lista **vazia é uma resposta**: é a única prova
    aceita de que a cobrança nunca ganhou id remoto, e portanto a única que
    autoriza apagar a linha. Falha de rede levanta `AsaasApiError` justamente
    para não ser confundida com ela — indisponibilidade do provedor não é
    evidência de inexistência, e tratar as duas igual apaga dinheiro sem linha.
    """
    dados = _request("GET", "/v3/payments",
                     contexto="Falha ao consultar cobranca no Asaas",
                     params={"externalReference": external_reference})
    lista = dados.get("data")
    return lista if isinstance(lista, list) else []


def deletar_pagamento(asaas_payment_id: str) -> dict:
    """`DELETE /v3/payments/{id}` — cancelamento remoto.

    Roda ANTES de criar a cobrança substituta (§10, correção nº 6): falhando,
    quem chama devolve 503 e NÃO cria nada. Dois QRs pagáveis do mesmo usuário
    seriam duas cobranças precificadas contra o mesmo crédito.
    """
    return _request("DELETE", f"/v3/payments/{asaas_payment_id}",
                    contexto="Falha ao cancelar cobranca no Asaas")
