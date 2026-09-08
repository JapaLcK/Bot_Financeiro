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


def test_fornecido_preserva_surrogate_sem_colidir():
    """O apontamento do Codex no PR #309, virado em portão.

    Com `replace` no lado `fornecido` todo surrogate virava `b"?"`, então uma
    senha com `?` literal casava também com o surrogate na mesma posição.
    `surrogatepass` preserva os 3 bytes do surrogate: a colisão morre e a
    função passa a ser injetiva deste lado.

    Injetividade é o argumento inteiro da troca, então cada handler que APAGA
    ou ESCAPA o surrogate precisa aqui da pré-imagem ASCII que ele criaria. Só
    com o `?` o grupo passava verde sob `ignore`, `backslashreplace` e
    `xmlcharrefreplace` — e `ignore` é PIOR que o `replace` apontado, porque dá
    pré-imagem infinita para qualquer segredo (`eq("abc\\ud800", "abc")`,
    `eq("\\ud800abc", "abc")`, …).

    Vermelho sob QUALQUER mutação do `errors=` do lado `fornecido`. As seis
    medidas: `replace`, `ignore`, `backslashreplace` e `xmlcharrefreplace`
    viram algum assert em True; `.encode("utf-8")` estrito e `surrogateescape`
    levantam `UnicodeEncodeError` em `\\ud800` (fora da faixa dele).
    """
    assert eq("\udcff", "?") is False  # mata `replace`
    assert eq("\ud800", "?") is False
    assert eq("a\udcffb", "a?b") is False
    assert eq("abc\ud800", "abc") is False  # mata `ignore`
    assert eq("abc\ud800", "abc\\ud800") is False  # mata `backslashreplace`
    assert eq("abc\ud800", "abc&#55296;") is False  # mata `xmlcharrefreplace`

    # o `?` literal continua casando com a senha que tem `?` — o que morreu foi
    # a segunda pré-imagem, não o caminho legítimo.
    assert eq("a?b", "a?b") is True


def test_segredo_cesu8_e_alcancavel_classe_conhecida_e_aceita():
    """Docstring, parágrafo da assimetria — a ÚNICA classe que a troca abre.

    Segredo cujos bytes crus formam a codificação CESU-8 de um surrogate
    (`\\xed\\xa0\\x80`..`\\xed\\xbf\\xbf`) passa a ser alcançável: o
    `surrogatepass` do lado `fornecido` produz exatamente esses 3 bytes quando
    recebe o surrogate correspondente. São 2048 segredos, e o diferencial
    exaustivo sobre 1–3 bytes não achou outro ganho de alcance.

    É DECIDIDO, não bug — não "conserte": quem manda tem de conhecer o segredo
    inteiro (não é bypass), e nenhum env real tem esses bytes. O assert existe
    pra que a afirmação não more só na prosa do docstring (CLAUDE.md §0.7).
    """
    segredo = b"\xed\xa0\x80".decode("utf-8", "surrogateescape")

    assert eq("\ud800", segredo) is True


def test_nao_e_reflexiva_na_faixa_do_surrogateescape():
    """Docstring, bloco da não-reflexividade: `eq(x, x)` é False quando o
    surrogate de `x` está em `\\udc80-\\udcff` — o `fornecido` codifica os 3
    bytes do surrogate, o `esperado` devolve o byte cru. É a mesma
    falha-fechado vista do outro lado, e ela sobreviveu à troca de `replace`
    por `surrogatepass`."""
    assert eq("\udcff", "\udcff") is False
    assert eq("\udc80", "\udc80") is False


def test_esperado_fora_da_faixa_levanta():
    """Docstring, bloco da não-reflexividade: `surrogateescape` só codifica
    `\\udc80-\\udcff`.
    Fora dela o lado `esperado` LEVANTA — armadilha pro próximo call site, não
    bug aberto (hoje `esperado` vem de `os.getenv`, cookie latin-1 ou
    hexdigest/base64 ASCII). Se algum dia isto parar de levantar, foi porque
    alguém mexeu no `errors=` do lado errado."""
    with pytest.raises(UnicodeEncodeError):
        eq("x", "\ud800")


def test_vazio_com_vazio_e_true():
    """Docstring, último parágrafo: o helper NÃO valida vazio, igual ao
    `compare_digest`. É o invariante de que os `if <valor> and ...` dos call
    sites dependem — sem eles, segredo não configurado casaria com header
    ausente."""
    assert eq("", "") is True
    assert eq("", "x") is False
    assert eq("x", "") is False
