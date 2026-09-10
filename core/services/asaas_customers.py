"""
core/services/asaas_customers.py — o cliente do Asaas (`/v3/customers`).

Assunto próprio, e a divisão foi por TAMANHO antes de ser por assunto:
`core/services/asaas.py` bateu no teto de 350 quando o `obter_qr_pix` entrou, e
o §2.3 do plano do 1b-B já nomeava este arquivo como a saída. O assunto sustenta
a divisão sozinho — pagamento e titular do pagamento são coisas diferentes, e é
aqui que o **CPF passa** (uma vez, sem tocar em disco).

Plano: docs/plano_pix_anual_asaas.md §14 item 10.

**Nada deste módulo é persistido além do `id`.** É a decisão fechada do dono:
`cpfCnpj` obrigatório e **não persistido**. Ele entra no corpo do POST e não
sai daqui — não vai para log, não vai para `details` de auditoria, não vai para
coluna nenhuma. O que guardamos é `pix_charges.asaas_customer_id`.
"""

from __future__ import annotations

from core.services.asaas import AsaasApiError, _request

def criar_cliente(*, nome: str, cpf_cnpj: str, email: str | None = None) -> str:
    """`POST /v3/customers` — devolve **só o `id`** do cliente no Asaas.

    O `cpfCnpj` é obrigatório para emitir Pix e **não é persistido por nós**:
    ele entra neste corpo e não sai daqui (ver o topo deste módulo).

    Devolver o `id` em vez do dict inteiro é a mesma regra do `AsaasApiError`
    não carregar corpo: a resposta ecoa nome, CPF e e-mail, e um dict inteiro é
    o que acaba num `details` de `log_system_event` — persistido e lido pelo
    painel admin. `id` ausente levanta em vez de virar `""`: customer vazio
    criaria a cobrança seguinte sem dono no Asaas.
    """
    corpo: dict = {"name": nome, "cpfCnpj": cpf_cnpj}
    if email:
        corpo["email"] = email
    dados = _request("POST", "/v3/customers",
                     contexto="Falha ao criar cliente no Asaas", json=corpo)
    cliente_id = dados.get("id") if isinstance(dados, dict) else None
    if not isinstance(cliente_id, str) or not cliente_id:
        raise AsaasApiError(
            "Criacao de cliente no Asaas devolveu resposta sem `id`",
            status_code=None,
        )
    return cliente_id
