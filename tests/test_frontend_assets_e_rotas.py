"""Todo arquivo do frontend precisa de alguém que o sirva.

Não há `StaticFiles` montado neste projeto: cada arquivo de `frontend/` só chega ao
navegador se alguém escreveu uma rota para ele. Um `.html`, `.js` ou `.css` que
nenhuma construção entrega é código morto que ninguém alcança — não quebra nada, não
aparece em lugar nenhum, e fica. Foi o que aconteceu com o `_dash_mockup.html`:
apagado de propósito no `84a1444` ("mockup local, entrou sem querer no glob") e
trazido de volta pelo `c411fa8`, um commit de 17 arquivos sobre lançamentos que levou
junto `.DS_Store` e `.claude/launch.json`.

O outro lado é a rota que aponta para arquivo ausente: 500 em produção, verde no CI.

**Existir não é ser servido, e menção não é construção.** Um comentário citando
`pagina.html` não serve página nenhuma, e por isso a leitura é por `ast` e por
construção — `FRONTEND_DIR / "<arquivo>"` —, não por texto.

A terceira verificação que morava aqui — toda referência absoluta do HTML/CSS/JS bate
com uma rota — saiu para PR próprio: ela precisa entender JavaScript (comentário,
string, delimitador, interpolação, fronteira de chamada) e sete rodadas de revisão
mostraram que regex não chega lá sem reprovar código correto.
"""

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
IGNORADOS = {".venv", "node_modules", "__pycache__", ".claude", ".git"}

# Páginas que existem de propósito sem rota. Entrada nova aqui exige o PORQUÊ na
# mesma linha — sem isso a allowlist vira o lugar onde o defeito se esconde.
# Caminho RELATIVO a `frontend/`, não nome de arquivo: por nome, a isenção vazaria
# para um órfão homônimo em qualquer subdiretório.
PAGINAS_SEM_ROTA_OK = {
    # Preview de desenvolvimento da arte dos agentes; `docs/agente_faria_limer.md`
    # a cita como o lugar onde o set `_AGENT_ART` é conferido a olho.
    "preview_agentes.html",
}

# `FRONTEND_DIR / "a" / "b"` e a variante em minúsculas do `core/admin_dashboard.py`.
# A cadeia INTEIRA é capturada de propósito: pegando só o primeiro literal, a rota de
# `FRONTEND_DIR / "static" / "auth-refresh.js"` virava uma checagem sobre o DIRETÓRIO
# `static`, que existe sempre — apagar o arquivo deixava o teste verde.


def _ignorado(p: Path) -> bool:
    """Sempre contra o caminho RELATIVO à raiz.

    Contra o absoluto isto fica errado sem avisar: um worktree do próprio projeto
    mora em `.claude/worktrees/<nome>/`, então TODO arquivo tem `.claude` nas partes
    e o filtro engole o repositório inteiro. O teste ficava vermelho acusando as 25
    páginas de uma vez — e num checkout comum ficaria verde medindo NADA.
    """
    return bool(IGNORADOS & set(p.relative_to(RAIZ).parts))


def _cadeia_frontend_dir(no):
    """`FRONTEND_DIR / "a" / "b"` -> `["a", "b"]`. Qualquer outra coisa -> None."""
    partes = []
    while isinstance(no, ast.BinOp) and isinstance(no.op, ast.Div):
        if not (isinstance(no.right, ast.Constant) and isinstance(no.right.value, str)):
            return None
        partes.insert(0, no.right.value)
        no = no.left
    if isinstance(no, ast.Name) and no.id in {"FRONTEND_DIR", "frontend_dir"} and partes:
        return partes
    return None


class _Coletor(ast.NodeVisitor):
    """Junta as cadeias `FRONTEND_DIR / ...` de um módulo.

    `ast` e não regex, e a diferença não é estética: o regex lia TEXTO, então uma
    docstring contendo `html_file(FRONTEND_DIR / "doc.html")` dava a página por
    servida — medido, enganava. O `ast` vê docstring como string literal e comentário
    como nada, então as duas classes somem sem filtro nenhum.

    Não desce na cadeia que casou, senão `FRONTEND_DIR / "static" / "auth-refresh.js"`
    registraria também o pedaço `static`.
    """

    def __init__(self):
        self.achados = []

    def visit_BinOp(self, no):
        partes = _cadeia_frontend_dir(no)
        if partes:
            self.achados.append("/".join(partes))
        else:
            self.generic_visit(no)


def _servidos():
    """Caminhos de `frontend/` que o Python de fato entrega, por construção.

    Teto aceito: a construção conta esteja onde estiver no código de produção, sem
    exigir que esteja dentro de um handler decorado. Exigir o decorator dá falso
    positivo MEDIDO — o `error.html` é montado em `shared.error_page_response()`, um
    helper sem rota própria, e é servido do mesmo jeito.
    """
    achados = {}
    for p in RAIZ.rglob("*.py"):
        rel = p.relative_to(RAIZ)
        if _ignorado(p) or "tests" in rel.parts:
            continue
        try:
            arvore = ast.parse(p.read_text(errors="replace"))
        except SyntaxError:
            continue
        coletor = _Coletor()
        coletor.visit(arvore)
        for caminho in coletor.achados:
            achados.setdefault(caminho, set()).add(str(rel))
    return achados


def test_toda_pagina_html_tem_rota_que_a_serve():
    """Página que nenhuma construção entrega é página que ninguém consegue abrir."""
    servidos = _servidos()
    # `.js` e `.css` também: um `frontend/unused.js` sem rota é código morto que
    # ninguém alcança, exatamente como uma página órfã. Medido em 2026-08-31 antes de
    # ampliar: zero órfãos desses dois tipos, então a ampliação não custou allowlist.
    orfas = [
        str(f.relative_to(FRONTEND))
        for f in sorted(FRONTEND.rglob("*"))
        if f.is_file()
        and f.suffix.lower() in {".html", ".js", ".css"}
        and not _ignorado(f)
        and str(f.relative_to(FRONTEND)) not in PAGINAS_SEM_ROTA_OK
        and str(f.relative_to(FRONTEND)) not in servidos
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


def _argumentos(texto: str, i: int) -> str:
    """O que ainda está DENTRO da chamada aberta antes de `i`, até fechar.

    As opções nem sempre vêm como `{` colado na vírgula — este repositório escreve
    `fetch(url, authOptions({ method: "POST" }))` —, então não dá para exigir a chave.
    Mas parar por caractere contado deixava o método de OUTRA chamada entrar na
    janela. Contar parênteses fecha exatamente onde a chamada acaba.
    """
    prof = 1
    for j in range(i, min(len(texto), i + 2000)):
        c = texto[j]
        if c == "(":
            prof += 1
        elif c == ")":
            prof -= 1
            if prof == 0:
                return texto[i:j]
    return texto[i:i + 2000]
