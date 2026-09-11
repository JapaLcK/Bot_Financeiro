"""Contrato de instalação do Microsoft Clarity nas páginas públicas seguras."""

import ast
from pathlib import Path

from frontend.routes import shared

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
STATIC_PAGES = FRONTEND / "routes" / "static_pages.py"
_PROJECT_ID = "clarity-teste-123"


def _paginas_com_clarity() -> set[str]:
    """Arquivos que fizeram opt-in na chamada real a `html_file`.

    AST impede comentário ou docstring de entrar no inventário como se fosse
    rota instrumentada. A lista é intencionalmente fechada: nova página precisa
    decidir se uma gravação de sessão é adequada, em vez de herdar o script.
    """
    tree = ast.parse(STATIC_PAGES.read_text(encoding="utf-8"))
    pages = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "html_file" and node.args):
            continue
        clarity = any(
            keyword.arg == "clarity"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )
        first_arg = node.args[0]
        if not clarity or not isinstance(first_arg, ast.BinOp):
            continue
        if isinstance(first_arg.right, ast.Constant) and isinstance(first_arg.right.value, str):
            pages.add(first_arg.right.value)
    return pages


def test_snippet_do_clarity_usa_o_projeto_configurado(monkeypatch):
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", _PROJECT_ID)
    snippet = shared.clarity_snippet()

    assert "https://www.clarity.ms/tag/" in snippet
    assert _PROJECT_ID in snippet
    assert "window,document,'clarity','script'" in snippet


def test_staging_desliga_o_snippet_do_clarity(monkeypatch):
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "")
    assert shared.clarity_snippet() == ""


def test_html_so_injeta_clarity_por_opt_in(monkeypatch):
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", _PROJECT_ID)
    sem_clarity = shared.html_file(FRONTEND / "login.html").body.decode()
    com_clarity = shared.html_file(FRONTEND / "index.html", clarity=True).body.decode()

    assert _PROJECT_ID not in sem_clarity
    assert _PROJECT_ID in com_clarity


def test_rotas_institucionais_e_precos_fazem_opt_in():
    assert _paginas_com_clarity() == {
        "index.html",
        "whatsapp.html",
        "funcionalidades.html",
        "comandos.html",
        "agents.html",
        "como-funciona.html",
        "precos.html",
    }


def test_suporte_faz_opt_in_sem_expor_login_ou_cadastro():
    source = STATIC_PAGES.read_text(encoding="utf-8")
    trecho_suporte = source[source.index("async def serve_suporte"):source.index("@router.get(\"/ddf")]

    assert "inject_tracking(template.replace(\"{{FAQ}}\", faq), clarity=True)" in trecho_suporte
    for page in ("login.html", "cadastro.html", "completar-cadastro.html", "home.html", "comecar.html"):
        assert page not in _paginas_com_clarity()


def test_csp_permite_biblioteca_do_clarity():
    from frontend.finance_bot_websocket_custom import _SECURITY_HEADERS

    csp = _SECURITY_HEADERS["Content-Security-Policy"]
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "https://www.clarity.ms" in script_src
