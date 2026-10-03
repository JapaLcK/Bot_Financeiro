"""Um pedido HTTP pelo app ASGI direto, para ler um stream SSE aos pedaços.

O `TestClient` espera a resposta inteira, e com stream infinito trava. Aqui o
`send` cai numa fila e o `receive` só devolve `http.disconnect` quando o teste
fecha — o mesmo caminho do cliente que some. Roda dentro de `asyncio.run`.
"""
import asyncio


class Pedido:
    def __init__(self, app, path, *, cookies=None, query=""):
        cabecalhos = [(b"host", b"testserver")]
        if cookies:
            cabecalhos.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
        self.scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": query.encode(), "headers": cabecalhos,
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        self.app = app
        self.fim = False
        self._fila = asyncio.Queue()
        self._sumiu = asyncio.Event()
        self._leu_corpo = False

    async def _receive(self):
        if not self._leu_corpo:
            self._leu_corpo = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._sumiu.wait()
        return {"type": "http.disconnect"}

    async def abrir(self):
        """Manda o pedido e espera o começo da resposta (`status` e `headers`)."""
        self.tarefa = asyncio.create_task(self.app(self.scope, self._receive, self._fila.put))
        inicio = await asyncio.wait_for(self._fila.get(), 10)
        assert inicio["type"] == "http.response.start", inicio
        self.status = inicio["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in inicio["headers"]}
        return self

    async def ler(self, prazo=5.0) -> bytes:
        """O próximo pedaço do corpo; `TimeoutError` se nada chegar no prazo."""
        msg = await asyncio.wait_for(self._fila.get(), prazo)
        self.fim = not msg.get("more_body", False)
        return msg["body"]

    async def ate_o_fim(self, prazo=5.0) -> bytes:
        """O resto do corpo, até a resposta terminar sozinha."""
        corpo = b""
        while not self.fim:
            corpo += await self.ler(prazo)
        await asyncio.wait_for(self.tarefa, prazo)
        return corpo

    async def fechar(self):
        """O cliente some. A tarefa do app tem de terminar sem levantar nada."""
        self._sumiu.set()
        await asyncio.wait_for(self.tarefa, 5)
