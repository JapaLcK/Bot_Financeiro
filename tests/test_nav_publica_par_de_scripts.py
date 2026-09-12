"""O nav-auth.js e o nav-burger.js são um PAR: nenhuma página carrega um só.

O menu da conta (nav-auth.js) e o menu recolhido do mobile (nav-burger.js) moram
em arquivos separados desde que o nav-auth.js bateu o teto de 350 linhas do
`eslint.config.mjs`. Separar o código não separou a dependência: o CSS que o
nav-burger.js injeta contém `.nav{flex-wrap:wrap;column-gap:6px}` e
`.nav .nav-right{order:2;margin-left:0}` — e `.nav-right` é justamente o
container onde o `renderAccountMenu` do nav-auth.js escreve. Uma página que
carregue só o nav-auth.js perde o layout mobile do usuário LOGADO, não só o
botão do menu; e uma que carregue só o nav-burger.js recolhe os links e some com
o menu da conta.

Por isso a asserção é de CONJUNTO e não "existe em N páginas": o defeito que
importa é a divergência entre as duas listas, e ela nasce de esquecer uma página
ao acrescentar a segunda tag.

O terceiro caso é sobre a URL: quem invalida o cache é o `stamp_asset_versions`,
que só reescreve `?v=` já escrito COM dígitos (`_ASSET_VER_RE`). Escrever a URL
nua, ou `?v=x`, deixa a tag passar intacta e um cliente com o par meio velho em
cache (`caches.match` casa a query) roda um CSS que não conversa com o outro
arquivo. Mesmo método do `test_par_pix_sai_versionado_por_hash_do_conteudo`.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.shared import FRONTEND_DIR, _asset_hash

client = TestClient(dashboard.app)

PAR = ["nav-auth.js", "nav-burger.js"]


def _paginas_com(nome: str) -> set:
    """Páginas cujo texto contém `src="/<nome>`. Menção em prosa não casa com o
    `src="/`, mas tag COMENTADA casa e conta como presente — é substring, não
    parser. Serve porque o defeito procurado é tag ausente, não tag desligada.
    """
    alvo = f'src="/{nome}'
    return {
        p.name
        for p in FRONTEND_DIR.glob("*.html")
        if alvo in p.read_text(encoding="utf-8")
    }


def test_as_mesmas_paginas_carregam_os_dois_scripts_da_nav():
    auth, burger = (_paginas_com(n) for n in PAR)
    assert auth == burger, (
        "o nav-auth.js e o nav-burger.js são um par e estas páginas carregam só "
        f"um dos dois: {sorted(auth ^ burger)}. Só com nav-auth.js, o layout "
        "mobile do usuário logado quebra junto com o burger (o CSS de `.nav` e o "
        "`order:2` do `.nav-right` moram no nav-burger.js); só com nav-burger.js, "
        "o menu da conta desaparece."
    )
    # Controle positivo: sem ele, `set() == set()` passa verde numa árvore em que
    # alguém apagou as DUAS tags de todas as páginas — o pior caso possível.
    assert len(auth) == 12, (
        f"são {len(auth)} páginas públicas com o par da nav, e este controle "
        "espera 12. Se você criou ou apagou uma página pública com .nav, "
        f"atualize o 12. Senão, alguém perdeu as duas tags de uma página: {sorted(auth)}"
    )


def test_o_par_sai_versionado_por_hash_do_conteudo():
    """`?v=1` literal não chega ao navegador: sai o hash do conteúdo de cada um."""
    html = client.get("/").text
    for nome in PAR:
        esperado = _asset_hash(nome, (FRONTEND_DIR / nome).stat().st_mtime_ns)
        # A lista INTEIRA, não o primeiro `search`: `== [esperado]` reprova de uma
        # vez a URL nua (`[]`), o literal do arquivo vazando (`["1"]`, mesma chave
        # de cache de antes) e o `?v=x` que o `_ASSET_VER_RE` não casa (`["x"]`).
        achados = re.findall(rf"/{re.escape(nome)}\?v=([0-9A-Za-z]+)", html)
        assert achados == [esperado], (
            f"na / o /{nome} devia sair exatamente uma vez com o hash "
            f"{esperado}, e saiu {achados}. Lista vazia = URL sem `?v=` (ou com "
            "`?v=` não-numérico, que o `_ASSET_VER_RE` não reescreve): o "
            "cache-buster morre e um cliente roda meio par velho."
        )
