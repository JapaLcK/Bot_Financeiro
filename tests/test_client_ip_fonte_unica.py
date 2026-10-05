"""Portão da #766: o IP do cliente só sai de `core/client_ip.py`.

Ler o peer (`request.client.host`, o `get_remote_address` do slowapi) ou um
cabeçalho de IP direto devolve, em produção, o IP do proxy do Railway ou um
valor forjável — foi o defeito da #766. Fora de `core/client_ip.py` só ficam
`client_ip` e `rate_limit_key`. O mesmo vale para o segredo da Cloudflare
(cabeçalho e env): ler em outro lugar é abrir uma segunda fonte de confiança.

Varre todo arquivo versionado E todo arquivo novo ainda não versionado que o
`.gitignore` não exclui (um router novo, antes do `git add`, também cai aqui),
texto e código (comentário incluído: é mais barato reescrever o comentário que
distinguir os dois). Fora da varredura: o próprio módulo, `.env.example`,
`docs/`, `.claude/`, `harness_tests/` e os testes que precisam montar esses
cabeçalhos.

O portão NÃO enxerga: o nome montado em pedaços (`"x-forwarded" + "-for"`),
`getattr(request, "client")` (e `getattr(scope, ...)`), arquivo ignorado pelo
`.gitignore`, e acesso ao peer por outro nome de variável (`r.client.port`
passa; `.client.host` não).
"""
import re
import subprocess

PADRAO = re.compile(
    r"get_remote_address|\.client\.host|\b(?:request|ws|websocket)\.client\b"
    r"|x-forwarded-for|x-real-ip|cf-connecting-ip|(?:\[|\.get\()\s*['\"]client['\"]"
    r"|x-pigbank-cf-secret|cloudflare_origin_secret",
    re.IGNORECASE,
)
FORA = ("core/client_ip.py", ".env.example", "tests/test_audit.py")
PASTAS_FORA = ("docs/", ".claude/", "harness_tests/", "tests/test_client_ip")


def _achados() -> list[str]:
    arquivos = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                              capture_output=True, text=True, check=True).stdout.splitlines()
    achados = []
    for path in arquivos:
        if path in FORA or path.startswith(PASTAS_FORA):
            continue
        try:
            with open(path, "rb") as f:
                bruto = f.read()
        except (FileNotFoundError, IsADirectoryError):  # apagado no working tree / submódulo
            continue
        if b"\0" in bruto:
            continue
        for n, linha in enumerate(bruto.decode("utf-8", "replace").splitlines(), 1):
            if PADRAO.search(linha):
                achados.append(f"{path}:{n}: {linha.strip()[:120]}")
    return achados


def test_ip_do_cliente_so_sai_de_core_client_ip():
    achados = _achados()
    assert not achados, "use client_ip/rate_limit_key de core/client_ip.py:\n" + "\n".join(achados)


def test_o_casador_do_portao():
    for proibido in (
        "ip = get_remote_address(request)",
        "limiter = Limiter(key_func=get_remote_address)",
        'ip = (request.client.host if request.client else "")',
        "host = websocket.client",
        "peer = ws.client and ws.client.host",
        'request.headers.get("X-Forwarded-For")',
        "request.headers['x-real-ip']",
        'headers.getlist("CF-Connecting-IP")',
        "scope['client']",
        'scope[ "client" ]',
        'peer = scope.get("client")',
        "scope.get( 'client', None)",
        'request.scope.get("client")',
        'request.headers.get("x-pigbank-cf-secret")',
        'os.getenv("CLOUDFLARE_ORIGIN_SECRET")',
    ):
        assert PADRAO.search(proibido), proibido
    for permitido in (
        "ip = client_ip(request)",
        "f\"ip:{rate_limit_key(request)}\"",
        "from core.client_ip import client_ip as ip_cliente, rate_limit_key",
        "request.client_id",
        "self.client.get('/x')",
        "httpx_client.host",
        "x_forwarded = 1",
        '"cliente": 1',
        'scope.get("client_id")',
        'scope.get("clientes")',
        'scope.get("type")',
    ):
        assert not PADRAO.search(permitido), permitido
