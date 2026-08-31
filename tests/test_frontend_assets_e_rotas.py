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
   atende e nenhum arquivo satisfaz. Dá 404 em produção e verde no CI.

Estes testes fecham as duas. Medido em 2026-08-31, antes de existirem: a classe 2
estava zerada e a 1 tinha dois casos.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
IGNORADOS = {".venv", "node_modules", "__pycache__", ".claude", ".git"}


def _ignorado(p: Path) -> bool:
    """Sempre contra o caminho RELATIVO à raiz.

    Contra o absoluto isto fica errado sem avisar: um worktree do próprio projeto
    mora em `.claude/worktrees/<nome>/`, então TODO arquivo tem `.claude` nas partes
    e o filtro engole o repositório inteiro. O teste ficava vermelho acusando as 25
    páginas de uma vez — e num checkout comum ficaria verde medindo nada.
    """
    return bool(IGNORADOS & set(p.relative_to(RAIZ).parts))

# Páginas que existem de propósito sem rota. Entrada nova aqui exige o PORQUÊ na
# mesma linha — sem isso a allowlist vira o lugar onde o defeito se esconde.
PAGINAS_SEM_ROTA_OK = {
    # Preview de desenvolvimento da arte dos agentes; `docs/agente_faria_limer.md`
    # a cita como o lugar onde o set `_AGENT_ART` é conferido a olho.
    "preview_agentes.html",
}


def _fontes_python():
    for p in RAIZ.rglob("*.py"):
        if _ignorado(p) or "tests" in p.relative_to(RAIZ).parts:
            continue
        yield p, p.read_text(errors="replace")


def _fontes_web():
    for p in sorted(FRONTEND.rglob("*")):
        if not p.is_file() or _ignorado(p):
            continue
        if p.suffix.lower() in {".html", ".css", ".js"}:
            yield p, p.read_text(errors="replace")


def test_toda_pagina_html_tem_alguem_que_a_serve():
    """Página que nenhum Python menciona é página que ninguém consegue abrir."""
    fontes = list(_fontes_python())
    orfas = [
        f.name
        for f in sorted(FRONTEND.glob("*.html"))
        if f.name not in PAGINAS_SEM_ROTA_OK
        and not any(f.name in texto for _, texto in fontes)
    ]
    assert not orfas, (
        "estas páginas existem em frontend/ e nenhum .py as menciona, então nenhuma "
        f"rota as serve: {orfas}. Ou escreva a rota, ou apague o arquivo, ou (se for "
        "preview de desenvolvimento) registre em PAGINAS_SEM_ROTA_OK com o motivo."
    )


def test_toda_rota_aponta_para_arquivo_que_existe():
    """`FileResponse` de arquivo ausente é 500 em produção e verde no CI."""
    padrao = re.compile(
        r'FRONTEND_DIR\s*/\s*"([^"]+)"'
        r'|FileResponse\(\s*[^,)]*?["\']([^"\']+\.(?:html|js|css|png|json|xml|txt|ico|svg))["\']'
    )
    faltando = []
    for p, texto in _fontes_python():
        for m in padrao.finditer(texto):
            nome = m.group(1) or m.group(2)
            if nome and not (FRONTEND / nome.lstrip("/")).exists():
                faltando.append(f"{nome} (citado em {p.relative_to(RAIZ)})")
    assert not faltando, f"rota apontando para arquivo inexistente: {faltando}"


def _rotas_do_app():
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


def test_toda_referencia_absoluta_tem_destino():
    """`/algo` no HTML/CSS/JS tem de bater com uma rota ou com um arquivo real."""
    literais, curingas = _rotas_do_app()
    arquivos = {
        "/" + str(f.relative_to(FRONTEND))
        for f in FRONTEND.rglob("*")
        if f.is_file() and not _ignorado(f) and "routes" not in f.parts
    }
    prefixos = tuple(c.split("{")[0] for c in curingas if c.split("{")[0])

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
    for p, texto in _fontes_web():
        texto = sem_data_uri.sub('""', texto)
        for extrator in extratores:
            for m in extrator.finditer(texto):
                u = m.group(1).strip().split("?")[0].split("#")[0]
                if not u.startswith("/") or u.startswith("//"):
                    continue
                if u in literais or u in arquivos or (prefixos and u.startswith(prefixos)):
                    continue
                quebradas.append(f"{u} (em {p.relative_to(RAIZ)})")
    assert not quebradas, (
        "referência absoluta que nenhuma rota atende e nenhum arquivo satisfaz — "
        f"isto é 404 em produção: {sorted(set(quebradas))}"
    )
