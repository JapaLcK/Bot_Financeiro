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


class TitularRecusado(RuntimeError):
    """O Asaas recusou o CADASTRO do titular → **400**, e não os nossos 503.

    A separação é por CULPA, e ela é medível pelo status HTTP:

      * **400 e 422** — o Asaas leu o corpo e disse que o dado não presta
        (`cpfCnpj`, ou o e-mail que veio do nosso cadastro). Retentar com o mesmo
        corpo dá o mesmo erro para sempre, então 503 "tenta de novo em instantes"
        é mentira: o cliente tem de saber que precisa mudar alguma coisa;
      * **401 e 403** — é a NOSSA credencial de API (a env que `asaas.py` lê, e
        que este módulo não pode sequer NOMEAR: o portão de
        `tests/test_pix_destino_inerte.py` mede a marca no texto). Foi o
        incidente de 10/09, e acusar o CPF do cliente por chave nossa errada é o
        pior desfecho possível. Os dois têm caso próprio desde 2026-09-10
        (`test_erro_que_nao_e_do_cliente_continua_503`): até lá, esta lista
        AFIRMAVA a separação e nenhum teste a media — pôr 401/403 dentro do
        `in (...)` de `criar_cliente` deixava a suíte Pix inteira verde;
      * **429** — throttle. Retentar ajuda, que é exatamente o que o 503 pede;
      * **5xx** e falha de transporte (`status_code is None`) — o Asaas, não o
        dado. Continuam 503.

    Carrega **só** o `code`, que já passou pelo `_codigo_seguro` (`asaas.py:96`).
    Nunca `str(exc)`, nunca o corpo, nunca o `description`: a descrição do erro do
    Asaas vem no mesmo objeto que o `code` e traz o documento do titular por
    extenso — e o `details` de quem loga isto é PERSISTIDO em `system_event_logs`,
    que a purga do §13.3 não alcança. Mesma forma de `CheckoutIndisponivel`: uma
    classe com `codigo`, e não uma classe por motivo.
    """

    def __init__(self, codigo: str | None = None):
        super().__init__(codigo or "titular_recusado")
        self.codigo = codigo


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
    try:
        dados = _request("POST", "/v3/customers",
                         contexto="Falha ao criar cliente no Asaas", json=corpo)
    except AsaasApiError as exc:
        # Só o que o Asaas classificou como corpo ruim. Todo o resto sobe INTACTO
        # e continua virando o 503 de quem chama — ver a classe acima.
        if exc.status_code in (400, 422):
            raise TitularRecusado(exc.code) from exc
        raise
    cliente_id = dados.get("id") if isinstance(dados, dict) else None
    if not isinstance(cliente_id, str) or not cliente_id:
        raise AsaasApiError(
            "Criacao de cliente no Asaas devolveu resposta sem `id`",
            status_code=None,
        )
    return cliente_id
