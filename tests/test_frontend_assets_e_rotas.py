"""Toda página do frontend precisa de dono, e toda referência precisa de destino.

Não há `StaticFiles` montado neste projeto: cada arquivo de `frontend/` só chega ao
navegador se alguém escreveu uma rota para ele. Isso torna possíveis duas classes de
defeito que nenhum teste pegava:

1. **asset sem rota** — o arquivo entra no repositório e nada o serve. Não quebra
   nada, não aparece em lugar nenhum, e fica. Foi o que aconteceu com o
   `_dash_mockup.html`: apagado de propósito no `84a1444` ("mockup local, entrou sem
   querer no glob") e trazido de volta pelo `c411fa8`, um commit de 17 arquivos sobre
   lançamentos que levou junto `.DS_Store` e `.claude/launch.json`.
2. **referência morta** — o HTML, o CSS ou o JS pede um caminho que nenhuma rota
   atende. Dá 404 em produção e verde no CI.

**Existir não é ser servido, e é por isso que estes testes olham para CONSTRUÇÃO e
não para menção.** Um `frontend/novo.js` só responde em `/novo.js` se alguém escreveu
a rota; um comentário citando `pagina.html` não serve página nenhuma. A primeira
versão destes testes aceitava as duas coisas e passava em quatro defeitos reais —
todos apontados na revisão do PR #209 e reproduzidos antes de serem consertados.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
IGNORADOS = {".venv", "node_modules", "__pycache__", ".claude", ".git"}

# Páginas que existem de propósito sem rota. Entrada nova aqui exige o PORQUÊ na
# mesma linha — sem isso a allowlist vira o lugar onde o defeito se esconde.
PAGINAS_SEM_ROTA_OK = {
    # Preview de desenvolvimento da arte dos agentes; `docs/agente_faria_limer.md`
    # a cita como o lugar onde o set `_AGENT_ART` é conferido a olho.
    "preview_agentes.html",
}

# `FRONTEND_DIR / "a" / "b"` e a variante em minúsculas do `core/admin_dashboard.py`.
# A cadeia INTEIRA é capturada de propósito: pegando só o primeiro literal, a rota de
# `FRONTEND_DIR / "static" / "auth-refresh.js"` virava uma checagem sobre o DIRETÓRIO
# `static`, que existe sempre — apagar o arquivo deixava o teste verde.
_CADEIA = re.compile(r'\b(?:FRONTEND_DIR|frontend_dir)\s*((?:/\s*"[^"]+"\s*)+)')
_PECA = re.compile(r'"([^"]+)"')


def _ignorado(p: Path) -> bool:
    """Sempre contra o caminho RELATIVO à raiz.

    Contra o absoluto isto fica errado sem avisar: um worktree do próprio projeto
    mora em `.claude/worktrees/<nome>/`, então TODO arquivo tem `.claude` nas partes
    e o filtro engole o repositório inteiro. O teste ficava vermelho acusando as 25
    páginas de uma vez — e num checkout comum ficaria verde medindo NADA.
    """
    return bool(IGNORADOS & set(p.relative_to(RAIZ).parts))


def _fontes_python():
    for p in RAIZ.rglob("*.py"):
        rel = p.relative_to(RAIZ)
        if _ignorado(p) or "tests" in rel.parts:
            continue
        yield p, p.read_text(errors="replace")


def _servidos():
    """Caminhos de `frontend/` que o Python de fato entrega, por construção."""
    achados = {}
    for p, texto in _fontes_python():
        for m in _CADEIA.finditer(texto):
            rel = "/".join(_PECA.findall(m.group(1)))
            achados.setdefault(rel, set()).add(str(p.relative_to(RAIZ)))
    return achados


def _rotas():
    literais, curingas = set(), set()
    padrao = re.compile(
        r'@(?:app|router)\.(?:get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']'
    )
    for p, texto in _fontes_python():
        if p.name == "mock_dashboard.py":  # script de mock, não sobe com o app
            continue
        for u in padrao.findall(texto):
            (curingas if "{" in u else literais).add(u)
    return literais, curingas


def _regex_do_curinga(rota: str) -> re.Pattern:
    """`/cards/{user_id}` casa UM segmento; `{x:path}` casa o resto.

    Comparar só o prefixo aprovava `/cards/123/typo/not-a-route`, que o FastAPI não
    atende: o parâmetro consome um segmento, não uma cauda inteira.
    """
    partes, i = [], 0
    for m in re.finditer(r"\{([^}]+)\}", rota):
        partes.append(re.escape(rota[i:m.start()]))
        partes.append(".+" if m.group(1).endswith(":path") else "[^/]+")
        i = m.end()
    partes.append(re.escape(rota[i:]))
    return re.compile("^" + "".join(partes) + "$")


def test_toda_pagina_html_tem_rota_que_a_serve():
    """Página que nenhuma construção entrega é página que ninguém consegue abrir."""
    servidos = _servidos()
    orfas = [
        f.name
        for f in sorted(FRONTEND.glob("*.html"))
        if f.name not in PAGINAS_SEM_ROTA_OK and f.name not in servidos
    ]
    assert not orfas, (
        "estas páginas existem em frontend/ e nenhuma rota as entrega: "
        f"{orfas}. Menção em comentário, docstring ou script não conta — o que conta "
        "é `FRONTEND_DIR / \"<arquivo>\"`. Ou escreva a rota, ou apague o arquivo, ou "
        "(se for preview de desenvolvimento) registre em PAGINAS_SEM_ROTA_OK com o motivo."
    )


def test_toda_rota_aponta_para_arquivo_que_existe():
    """`FileResponse` de arquivo ausente é 500 em produção e verde no CI."""
    faltando = [
        f"{rel} (citado em {', '.join(sorted(onde))})"
        for rel, onde in sorted(_servidos().items())
        if not (FRONTEND / rel).exists()
    ]
    assert not faltando, f"rota apontando para arquivo inexistente: {faltando}"


def test_toda_referencia_absoluta_tem_ROTA():
    """`/algo` no HTML/CSS/JS tem de bater com uma ROTA — existir arquivo não basta.

    Sem `StaticFiles`, `frontend/novo.js` existir não faz `/novo.js` responder. A
    primeira versão aceitava o arquivo como prova e aprovava exatamente esse 404.
    """
    literais, curingas = _rotas()
    padroes = [_regex_do_curinga(c) for c in curingas]

    # Data URI carrega `url(...)` DENTRO dele (`url(%23n)` de filtro SVG). Sem tirar
    # antes, o casamento pega o de dentro e inventa referência quebrada — foram 4
    # falsos positivos na primeira medição.
    sem_data_uri = re.compile(r'(?:"|\')data:[^"\']*(?:"|\')')
    extratores = [
        re.compile(r"""(?:src|href|poster|data-src)\s*=\s*["']([^"'>]+)["']"""),
        re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)"""),
        re.compile(r"""(?:fetch|import|register)\(\s*["']([^"']+)["']"""),
    ]

    quebradas = []
    for p in sorted(FRONTEND.rglob("*")):
        if not p.is_file() or _ignorado(p) or p.suffix.lower() not in {".html", ".css", ".js"}:
            continue
        texto = sem_data_uri.sub('""', p.read_text(errors="replace"))
        for extrator in extratores:
            for m in extrator.finditer(texto):
                u = m.group(1).strip().split("?")[0].split("#")[0]
                if not u.startswith("/") or u.startswith("//"):
                    continue
                if u in literais or any(r.match(u) for r in padroes):
                    continue
                # Base de concatenação: o JS escreve `"/auth/google/pending/" + token`
                # para a rota `/auth/google/pending/{token}`. Vale só quando termina em
                # "/" e UM segmento a mais casaria a rota — assim `/cards/123/typo/x`
                # continua reprovando, porque nem com sufixo ele casa `/cards/{id}`.
                if u.endswith("/") and any(r.match(u + "x") for r in padroes):
                    continue
                quebradas.append(f"{u} (em {p.relative_to(RAIZ)})")
    assert not quebradas, (
        "referência absoluta que nenhuma rota atende — isto é 404 em produção, e "
        f"o arquivo existir não muda nada sem StaticFiles: {sorted(set(quebradas))}"
    )
