"""Contrato do `constant_time_eq` — o que o docstring dele promete, asserido.

Sem fixture, sem mock, sem TestClient: é função pura. Os testes de rota
(`test_health_com_segredo_surrogate_no_env_nao_da_500`,
`test_admin_login_com_surrogate_no_corpo_json_nao_da_500`) provam que o 500
fecha em produção; estes aqui prendem a ASSIMETRIA dos dois `errors=`, que
até agora só existia em prosa e sobreviveria intacta a uma "simplificação"
(CLAUDE.md §0.7).
"""

import pytest

from core.secure_compare import constant_time_eq as eq


def test_caminho_legitimo():
    """Controle positivo: sem ele o grupo passaria num helper que recusa tudo."""
    assert eq("abc", "abc") is True
    assert eq("abc", "abd") is False
    assert eq("café", "café") is True


def test_segredo_com_byte_nao_utf8_nao_casa_com_interrogacao_literal():
    """O caso que fecha o B1 — vermelho sob QUALQUER mutação do lado `esperado`.

    - `esperado` → `replace`: os dois lados viram `b"senha-?-cru"` e isto
      passa a ser True. Um segredo com byte não-UTF-8 no env casaria com um
      literal que qualquer um digita.
    - `esperado` → `.encode("utf-8")` estrito: levanta `UnicodeEncodeError`,
      que é o 500 pré-autenticação que o helper existe pra fechar.

    Só o `surrogateescape` faz o round-trip exato dos bytes do env: falha
    FECHADO (False), sem colisão e sem exceção.
    """
    segredo = b"senha-\xff-cru".decode("utf-8", "surrogateescape")

    assert eq("senha-?-cru", segredo) is False


def test_segredos_distintos_nao_colidem():
    """`replace` no lado `esperado` colapsaria `\\udcff` e `\\udcfe` no mesmo
    `b"senha-?-cru"` — dois segredos diferentes, um único literal que abre os
    dois. `surrogateescape` mantém 0xff ≠ 0xfe."""
    a = b"senha-\xff-cru".decode("utf-8", "surrogateescape")
    b = b"senha-\xfe-cru".decode("utf-8", "surrogateescape")

    assert a != b
    assert eq(a, b) is False
    assert eq("senha-?-cru", a) is False
    assert eq("senha-?-cru", b) is False


def test_nao_e_injetiva():
    """Docstring, linhas 52-53: consequência do `replace` do lado `fornecido`,
    sem ganho pro atacante (ele pode mandar `?` literal de qualquer jeito).

    Vermelho se `fornecido` virar `.encode("utf-8")` estrito
    (`UnicodeEncodeError`) ou `surrogateescape` (`\\ud800` fora da faixa)."""
    assert eq("\udcff", "?") is True
    assert eq("\ud800", "?") is True
    assert eq("a\udcffb", "a?b") is True


def test_nao_e_reflexiva_na_faixa_do_surrogateescape():
    """Docstring, linhas 54-61: `eq(x, x)` é False quando o surrogate de `x`
    está em `\\udc80-\\udcff` — o lado `fornecido` faz `?`, o `esperado`
    devolve o byte cru. É a mesma falha-fechado vista do outro lado."""
    assert eq("\udcff", "\udcff") is False
    assert eq("\udc80", "\udc80") is False


def test_esperado_fora_da_faixa_levanta():
    """Docstring, linhas 58-61: `surrogateescape` só codifica `\\udc80-\\udcff`.
    Fora dela o lado `esperado` LEVANTA — armadilha pro próximo call site, não
    bug aberto (hoje `esperado` vem de `os.getenv`, cookie latin-1 ou
    hexdigest/base64 ASCII). Se algum dia isto parar de levantar, foi porque
    alguém mexeu no `errors=` do lado errado."""
    with pytest.raises(UnicodeEncodeError):
        eq("x", "\ud800")


def test_vazio_com_vazio_e_true():
    """Docstring, linha 63: o helper NÃO valida vazio, igual ao
    `compare_digest`. É o invariante de que os `if <valor> and ...` dos call
    sites dependem — sem eles, segredo não configurado casaria com header
    ausente."""
    assert eq("", "") is True
    assert eq("", "x") is False
    assert eq("x", "") is False
