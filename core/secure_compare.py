"""Comparação em tempo constante que aguenta entrada não-ASCII.

`hmac.compare_digest` (e o `secrets.compare_digest`, que é o mesmo objeto) só
aceita `str` ASCII: com um caractere acentuado de qualquer lado ele levanta
`TypeError`. Como o lado esquerdo vem de header/query/cookie de quem chama,
isso vira 500 com stack trace ANTES de qualquer autenticação — o atacante
escolhe o byte e ganha o 500. Comparar bytes não tem essa restrição.

Um helper só, e não o par de `.encode()` copiado em cada site: o dia em que
alguém esquecer o `errors=` de um lado, o 500 volta (CLAUDE.md §0.7).

Quem chama: `grep -rn constant_time_eq --include='*.py'` (a contagem não mora
aqui de propósito — CLAUDE.md §2).
"""

import hmac


def constant_time_eq(fornecido: str, esperado: str) -> bool:
    """`fornecido` é o lado controlado por quem chama; `esperado`, o segredo.

    Os dois `errors=` são diferentes de propósito, cada um contra um 500 medido:

    - `esperado` → `surrogateescape`, porque é DESTE lado que o surrogate
      aparece de verdade: `os.getenv` decodifica o ambiente com
      `surrogateescape`, então um segredo com byte não-UTF-8 chega como
      `'segredo-\\udcff-cru'` e o `.encode("utf-8")` estrito levantaria
      `UnicodeEncodeError` — o mesmo 500 que este helper existe pra fechar
      (`/health` com `SMOKE_HEALTH_TOKEN` assim dava 500). `surrogateescape`
      faz o round-trip exato dos bytes do env, sem a colisão que `replace`
      criaria: dois segredos distintos continuam distintos.
    - `fornecido` → `surrogatepass`, porque é o lado que não pode levantar
      nunca, venha o caractere que vier. NÃO é peso morto, é caminho quente: o
      corpo JSON entrega surrogate SOLITÁRIO — `json.loads('{"p": "\\udcff"}')`
      dá `'\\udcff'` — e o login do admin (`_check_admin_password`) lê a senha
      dali. Trocar por `.encode("utf-8")` estrito devolve `UnicodeEncodeError`
      → 500 sem autenticação nenhuma, medido em `POST /admin/auth/login`
      (`tests/test_admin_security.py::
      test_admin_login_com_surrogate_no_corpo_json_nao_da_500`). Header e
      cookie (latin-1) e query (`replace`) é que não produzem surrogate; o
      corpo produz. `surrogateescape` não serve aqui — surrogate fora da faixa
      `\\udc80-\\udcff` voltaria a levantar. `surrogatepass` não levanta em
      NENHUMA entrada (UTF-8 codifica todo code point; o único que o Python
      recusa é o surrogate, e é exatamente o que este handler libera).

    Era `replace` deste lado até o apontamento do Codex no PR #309: `replace`
    colapsa todo surrogate em `b"?"`, então uma senha com `?` literal casava
    também com o surrogate na mesma posição — colisão sem ganho pro atacante
    (ele podia mandar `?` literal), mas colisão. `surrogatepass` preserva os
    bytes do surrogate e é injetivo, então a colisão não existe mais. **Não
    volte pra `replace`** — `test_fornecido_preserva_surrogate_sem_colidir`
    fica vermelho se alguém tentar.

    Efeito da assimetria, e é o desejado: segredo do env com byte não-UTF-8
    falha FECHADO em vez de dar 500, porque o `surrogateescape` do lado
    `esperado` devolve o byte cru e o lado `fornecido` não tem como produzi-lo
    — `b"senha-\\xff-cru"` é inalcançável. A exceção medida é o segredo cujos
    bytes crus formam a codificação de um surrogate (`\\xed\\xa0\\x80`..
    `\\xed\\xbf\\xbf`, CESU-8): esse o `fornecido` alcança mandando o
    surrogate correspondente. Não é bypass — quem manda tem de conhecer o
    segredo inteiro, e o espaço de busca continua uma string de bytes por
    tentativa. Sob `replace` ele falhava fechado; a troca cobre uma classe
    comum (senha com `?`) e abre uma que nenhum env real tem.

    Uma propriedade surpreendente pra uma função chamada `eq`, e ela sobrevive
    à troca: não é reflexiva — `eq(x, x)` é False quando o surrogate de `x`
    está na faixa `\\udc80-\\udcff` (a que o `surrogateescape` do `os.getenv`
    produz), porque o `fornecido` codifica os 3 bytes do surrogate e o
    `esperado` devolve o byte cru. É a mesma falha-fechado do parágrafo acima,
    vista do outro lado. FORA dessa faixa o lado `esperado` LEVANTA
    `UnicodeEncodeError` (`eq("\\ud800", "\\ud800")`), porque `surrogateescape`
    só codifica a faixa dele. Hoje nenhum chamador pode trazer surrogate fora
    da faixa no `esperado` (ele vem de `os.getenv`, de cookie latin-1 ou de
    hexdigest/base64 ASCII) — é armadilha pro próximo site, não bug aberto.

    Não valida vazio: `constant_time_eq("", "")` é True, igual ao
    `compare_digest`. Quem depende disso já tem o `if valor and ...` antes.
    """
    return hmac.compare_digest(
        fornecido.encode("utf-8", "surrogatepass"),
        esperado.encode("utf-8", "surrogateescape"),
    )
