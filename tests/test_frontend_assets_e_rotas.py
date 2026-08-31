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

import ast
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



_ROTAS = None


def _expandir(rotas, vistos=None, descartadas=None):
    """Achata `app.routes`, entrando nos routers incluídos.

    Isto NÃO é zelo: com o `fastapi==0.141.1` do requirements, cada
    `include_router()` vira um `_IncludedRouter` que **não** tem `path_regex`, e as
    rotas dele moram em `.original_router.routes` (já com o prefixo aplicado). Um
    filtro ingênuo por `hasattr(r, "path_regex")` descarta os 11 routers do projeto
    em silêncio — o teste fica verde no venv local, que está no 0.115.6, e vermelho
    no CI. Foi exatamente o que aconteceu.

    `descartadas` recolhe o que não foi nem rota nem container, para o teste poder
    reprovar em vez de medir menos sem avisar.
    """
    vistos = set() if vistos is None else vistos
    for r in rotas:
        if id(r) in vistos:
            continue
        vistos.add(id(r))
        if hasattr(r, "path_regex"):
            yield r
            continue
        sub = getattr(r, "original_router", None) or getattr(r, "router", None) or r
        subrotas = getattr(sub, "routes", None)
        if subrotas:
            yield from _expandir(subrotas, vistos, descartadas)
        elif descartadas is not None:
            descartadas.append(type(r).__name__)


def _rotas():
    """As rotas REAIS do app, não as que um regex acha no fonte.

    Foi assim que este teste parou de crescer. Parseando o fonte, cada detalhe do
    Python virava um remendo: `@router.get` comentado contava como rota, docstring
    citando um decorator contaria também, o método tinha de ser adivinhado, e o
    curinga precisava de um casador escrito à mão — que errava justamente o caso de
    `/pockets/{user_id}/{pocket_name:path}/history`, aprovando qualquer sufixo porque
    o `:path` devolvia cedo demais.

    O app responde tudo isso de graça: caminho, métodos e a `path_regex` que o
    próprio Starlette compila. Custa ~0,5 s de import, uma vez.
    """
    global _ROTAS
    if _ROTAS is None:
        from frontend.finance_bot_websocket_custom import app  # noqa: PLC0415

        descartadas = []
        achadas = list(_expandir(app.routes, descartadas=descartadas))
        assert not descartadas, (
            "há entrada em app.routes que não é rota nem container conhecido: "
            f"{sorted(set(descartadas))}. O FastAPI mudou de forma — conserte "
            "_expandir() em vez de deixar o teste medir menos em silêncio."
        )
        # `APIWebSocketRoute` tem `methods = None`. O fallback ingênuo `or {"GET"}`
        # fazia `/ws/{user_id}` valer como destino HTTP, e aí um
        # `<script src="/ws/qualquer-coisa">` passava. Websocket ganha um método que
        # nenhuma referência HTTP pede.
        _ROTAS = [
            (
                r.path,
                {m.upper() for m in getattr(r, "methods", None)} if getattr(r, "methods", None) else {"WEBSOCKET"},
                r.path_regex,
            )
            for r in achadas
        ]
    return _ROTAS


def _casa_com_interpolacao(ref: str, rota: str) -> bool:
    """Compara padrão com padrão, segmento a segmento.

    Só para referência com `${...}`, onde o valor de runtime é desconhecido — e é
    preciso, porque em `/admin/api/affiliates/payouts/${id}/${action}` o `action`
    vale 'paid' ou 'reject', que são LITERAIS da rota. Trocar a interpolação por um
    texto inventado e comparar reprovava uma chamada correta.
    """
    a = ref.strip("/").split("/")
    b = rota.strip("/").split("/")

    def igual(seg_ref, seg_rota):
        return seg_rota.startswith("{") or "${" in seg_ref or seg_rota == seg_ref

    for i, sr in enumerate(b):
        if sr.startswith("{") and sr[1:-1].endswith(":path"):
            # A cauda come um ou mais segmentos, MAS o que vem depois dela na rota
            # continua obrigatório: `/pockets/{u}/{nome:path}/history` exige o
            # `/history` no fim. Devolver True aqui aprovava qualquer sufixo — foi o
            # bug que o casador escrito à mão tinha, e ele sobreviveu neste ramo
            # quando as rotas passaram a vir do app (a regex do Starlette só é usada
            # quando a referência NÃO tem interpolação).
            resto = b[i + 1:]
            if len(a) < i + 1 + len(resto):
                return False
            cauda = a[len(a) - len(resto):] if resto else []
            return all(igual(x, y) for x, y in zip(cauda, resto))
        if i >= len(a):
            return False
        if not igual(a[i], sr):
            return False
    return len(a) == len(b)



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

    | origem                      | método exigido            |
    |-----------------------------|---------------------------|
    | `src`/`href`/`poster`       | GET (é o que o navegador faz) |
    | `url(...)` do CSS           | GET                       |
    | `fetch`/`import`/`register` | o `method:` da chamada, GET quando omitido |

    **Teto aceito, medido**: 16 dos 174 `fetch` do frontend recebem a URL numa
    VARIÁVEL (`const url = ...; fetch(url, {...})`) e ficam fora. Cobrir isso é
    seguir fluxo de dados, não casar padrão — e nos casos reais deste repositório a
    variável é composta em dois níveis (`const base = ...; fetch(`${base}/kpis`)`) e o
    método é ternário (`isEdit ? "PATCH" : "POST"`). A tentativa barata — conferir
    todo literal absoluto atribuído a variável — foi medida e descartada: acusava
    `/></svg>` de dentro de markup e as bases compostas, ou seja, trocava um ponto
    cego por ruído. O modo de falha do que ficou de fora é um 404 não detectado, não
    um verde falso sobre outra coisa.
    """
    rotas = _rotas()

    # Data URI carrega `url(...)` DENTRO dele (`url(%23n)` de filtro SVG). Sem tirar
    # antes, o casamento pega o de dentro e inventa referência quebrada — foram 4
    # falsos positivos na primeira medição.
    sem_data_uri = re.compile(r'(?:"|\')data:[^"\']*(?:"|\')')

    # Crase incluída de propósito: `fetch(`${API}/auth/validate`)` é como o frontend
    # chama a API — 108 chamadas que a versão anterior não enxergava, porque só lia
    # aspas e porque `${API}` no começo não parecia caminho absoluto.
    NAVEGA = re.compile(r"""(?:src|href|poster|data-src)\s*=\s*["\']([^"\'>]+)["\']""")
    CSS_URL = re.compile(r"""url\(\s*["\']?([^"\')]+)["\']?\s*\)""")
    CHAMADA = re.compile(r"""(?:fetch|import|register)\(\s*["\'`]([^"\'`]+)["\'`]""")
    METODO = re.compile(r"""method\s*:\s*["\']([A-Za-z]+)["\']""")
    # As opções nem sempre vêm como `{` logo depois da vírgula: este repositório
    # escreve `fetch(url, authOptions({ method: "POST", ... }))`. Exigir a chave
    # colada dava GET a uma chamada POST e inventava referência quebrada. A janela
    # vai até a PRÓXIMA chamada (ou 500 caracteres), para não pegar o método de outra.
    PROXIMA = re.compile(r"(?:fetch|import|register)\(")

    # `${API}` e irmãos valem a origem do site: `const API = BASE_HTTP`. O que vem
    # depois é caminho absoluto, e é o que interessa aqui.
    ORIGEM_NO_COMECO = re.compile(r"^\$\{[A-Za-z_$][\w$]*\}(?=/)")

    def resolve(u: str, metodo: str, concatenavel: bool) -> bool:
        u = ORIGEM_NO_COMECO.sub("", u).split("?")[0].split("#")[0]
        if not u.startswith("/") or u.startswith("//"):
            return True
        # Barra no fim só indica concatenação em CHAMADA — `fetch("/x/" + id)`. Num
        # atributo, o valor É a URL inteira: `<script src="/fonts/">` é 404, e tratar
        # a barra como base fazia ele casar `/fonts/{name}` e passar.
        base = concatenavel and u.endswith("/") and len(u) > 1
        alvo = u.rstrip("/") if base else u
        for caminho, metodos, regex in rotas:
            if metodo is not None and metodo not in metodos:
                continue
            if "${" in alvo:
                if _casa_com_interpolacao(alvo, caminho):
                    return True
            elif regex.match(alvo):
                return True
            # Base de concatenação: `"/auth/google/pending/" + token` para a rota
            # `/auth/google/pending/{token}`. Um segmento a mais, e só isso —
            # `/cards/123/typo/` continua reprovando.
            if base and (
                regex.match(alvo + "/x")
                if "${" not in alvo
                else _casa_com_interpolacao(alvo + "/x", caminho)
            ):
                return True
        return False

    quebradas = []
    for p in sorted(FRONTEND.rglob("*")):
        if not p.is_file() or _ignorado(p) or p.suffix.lower() not in {".html", ".css", ".js"}:
            continue
        texto = sem_data_uri.sub('""', p.read_text(errors="replace"))
        achados = [(m.group(1), "GET", False) for m in NAVEGA.finditer(texto)]
        achados += [(m.group(1), "GET", False) for m in CSS_URL.finditer(texto)]
        for m in CHAMADA.finditer(texto):
            fim = PROXIMA.search(texto, m.end())
            janela = texto[m.end():min(fim.start() if fim else len(texto), m.end() + 500)]
            met = METODO.search(janela)
            achados.append((m.group(1), met.group(1).upper() if met else "GET", True))
        for u, metodo, concatenavel in achados:
            u = u.strip()
            if not resolve(u, metodo, concatenavel):
                quebradas.append(f"{u} [{metodo or 'qualquer'}] (em {p.relative_to(RAIZ)})")
    assert not quebradas, (
        "referência absoluta que nenhuma rota atende NAQUELE MÉTODO — isto é 404 ou "
        f"405 em produção, e o arquivo existir não muda nada sem StaticFiles: "
        f"{sorted(set(quebradas))}"
    )
