"""O filtro de FORMA do `code` do Asaas — `_codigo_seguro`, e só ele.

Arquivo próprio, e o motivo é medido: `tests/test_asaas_client.py` estava em 316
linhas e os casos migrados o levariam a 401, acima do teto de 350
(`tests/test_max_lines_python.py`). O assunto sustenta a divisão sozinho — lá é
o transporte HTTP (mock de `httpx`, status, corpo), aqui é uma função pura sobre
uma tabela de strings, sem fixture nenhuma.

**Estes casos não nasceram aqui.** Eles vinham de
`test_erro_seguro_nao_divergiu_do_codigo_seguro`, que guardava a CÓPIA da regra
que vivia em `db/webhook_outbox.py::_erro_seguro`. No 1b-B a cópia virou import
(o dreno entrou na allowlist do portão de inércia, §5.2 do plano) e aquele teste
morreu — **mas os casos não podiam morrer junto**: teste apagado sem os casos
migrados é o buraco por onde o CPF já passou uma vez (P2 do Codex no #304).
"""

import pytest

from core.services.asaas import _codigo_seguro

_COM_FORMA_DE_DOCUMENTO = [
    "12345678901",            # CPF puro: todo dígito é isalnum(), e é o furo original
    "123.456.789-01",         # separador `.` e `-`
    "CPF12345678901",         # prefixo alfabético
    "cpf_123_456_789_01",     # separador `_`, a terceira grafia
    "CNPJ12345678000199",     # 14 dígitos
    "cnpj-12.345.678-0001-99",
    # AGÊNCIA-CONTA-DÍGITO. Voltou na migração dos 29 valores (ver o cabeçalho):
    # não é CPF, é dado bancário do titular, e cai pela MESMA cláusula — tirados
    # os separadores sobra `0001123456`, que é `isdigit()`. **VEREDITO MEDIDO em
    # 2026-09-09** (remedir antes de reusar): `_codigo_seguro("0001-12345-6")`
    # devolve `""` e `safe_code` da Pluggy também. Os dois RECUSAM, e a hipótese
    # de que este valor separava os dois filtros era falsa.
    "0001-12345-6",
]

_CODIGO_LEGITIMO = ["AsaasApiError", "invalid_cpfCnpj", "ReadTimeout", "v1.2.3",
                    "error_400", "HTTP_502", "code-42", "payment_not_found"]


@pytest.mark.parametrize("valor", _COM_FORMA_DE_DOCUMENTO)
def test_forma_de_documento_e_recusada_em_qualquer_grafia(valor):
    """DISCRIMINA. A categoria é "corrida com forma de documento em QUALQUER
    grafia que o filtro permita" — foi tratando caso e não categoria que o mesmo
    defeito voltou três vezes.

    *Negativo 1: apague o `nu.isdigit()` -> so `"0001-12345-6"` fica vermelho.
    `"12345678901"` continua VERDE: quem o pega e a corrida de 11+ digitos, que
    esta mutacao nao toca (MEDIDO em 2026-09-09, remedir antes de reusar).
    Negativo 2: apague a corrida de 11+ digitos -> as grafias com prefixo e com
    separador ficam vermelhas. Negativo 3: tire o `_` de `_SEPARADORES` -> so as
    grafias com `_` ficam vermelhas. Cada mutacao discrimina numa grafia,
    porque a categoria E a grafia.*
    """
    assert _codigo_seguro(valor) == ""
    assert _codigo_seguro(valor, "?") == "?", "o `fallback` não está sendo usado"


@pytest.mark.parametrize("valor", _CODIGO_LEGITIMO)
def test_codigo_legitimo_passa_inteiro(valor):
    """POSITIVO do grupo, e sem ele um filtro que recusa TUDO passaria acima —
    o que é pior que o bug, porque apagaria o `last_error` de toda falha real."""
    assert _codigo_seguro(valor) == valor
    assert _codigo_seguro(valor, "?") == valor


# ── os dois limites, e onde a regra NÃO é a mesma do Pluggy ─────────────────
#
# A docstring de `_codigo_seguro` dizia "mesma regra do `safe_code` do Pluggy".
# **Medido em 2026-09-09, é falso nos dois sentidos** (remedir antes de reusar), e
# os dois valores abaixo são exatamente onde: `"42"` (asaas RECUSA, Pluggy aceita)
# e `"x"*60` (asaas ACEITA, Pluggy recusa acima de 20). Os dois vinham dos 29 casos
# migrados e sumiram — `"x"*61` sobreviveu sozinho, e limite sem o lado de dentro
# passa num filtro que recusa tudo.

def test_so_digitos_curto_e_recusado_mesmo_sem_forma_de_documento():
    """`"42"` não parece CPF nenhum e mesmo assim cai — a cláusula é
    `nu.isdigit()`, não "tem 11 dígitos".

    **Aqui `_codigo_seguro` é mais ESTRITO que o `safe_code` do Pluggy**, que
    aceita até 6 dígitos e devolveria `"42"` inteiro. Custo zero: não existe
    `code` só-dígitos na API do Asaas. Está escrito porque a docstring da função
    chama as duas de "mesma regra", e nesta linha elas não são.

    *Negativo: apague o `nu.isdigit()` → vermelho aqui e em `"0001-12345-6"`.
    `"12345678901"` fica VERDE — quem o recusa é a corrida de 11+ dígitos, que a
    mutação não toca (MEDIDO em 2026-09-09, remedir antes de reusar).*
    """
    assert _codigo_seguro("42") == ""
    assert _codigo_seguro("42", "?") == "?"


def test_o_limite_de_60_e_inclusivo_dos_dois_lados():
    """O PAR do limite. `"x"*61` já estava na suíte; `"x"*60` — o lado que passa —
    tinha sumido, e sem ele a regra `len > 60` valeria igual escrita `len >= 0`.

    **Aqui `_codigo_seguro` é mais FROUXO que o Pluggy**, cujo `_CODE_FORMAT` para
    em 20 caracteres. É a outra metade da divergência do teste acima.

    *Negativo: troque `len(texto) > 60` por `>= 60` → a primeira asserção fica
    vermelha e a segunda continua verde, que é o par discriminando.*
    """
    assert _codigo_seguro("x" * 60) == "x" * 60
    assert _codigo_seguro("x" * 61) == ""
    assert _codigo_seguro("x" * 61, "?") == "?"


def test_erro_seguro_usa_a_funcao_unica_com_o_fallback_certo():
    """O contrato do chamador: `db/webhook_outbox.py` mudou de cópia para import,
    e o `"?"` (que era a única diferença medida entre as duas) virou parâmetro.

    *Negativo: faça `_erro_seguro` devolver `tipo` sem filtrar → a linha do CPF
    fica vermelha, que é o valor indo para `last_error` e `system_event_logs`.*
    """
    from db.webhook_outbox import _erro_seguro

    assert _erro_seguro("AsaasApiError", "invalid_cpfCnpj") == "AsaasApiError(invalid_cpfCnpj)"
    assert _erro_seguro("CPF12345678901", None) == "?"
    assert _erro_seguro("AsaasApiError", "12345678901") == "AsaasApiError(?)"


