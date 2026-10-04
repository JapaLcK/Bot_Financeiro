"""Teto de tamanho (413) e prazo de leitura (408) do corpo de toda requisição HTTP.

O uvicorn não tem nenhum dos dois, e o FastAPI lê o corpo tipado ANTES da
autenticação: sem isto, um anônimo enche a memória por qualquer rota com corpo.
Plano, números e fontes: `docs/plano-limite-corpo.md`.

ASGI puro de propósito: levantar `HTTPException` dentro do `receive` sai como
`ExceptionGroup` do `BaseHTTPMiddleware` (medido), então o middleware responde
ele mesmo pelo `send` original.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

MiB = 1024 * 1024

# Fonte única do teto do OFX (§0.7); a rota em finance_bot_websocket_custom.py importa daqui.
MAX_OFX_BYTES = 8 * MiB

# (teto em bytes, prazo em segundos) por faixa — fontes na tabela "Tetos" do plano.
# AgentChatBody no pior caso ≈ 972.040 B; 1 MiB a 750 kbit/s = 11,2 s, 30 s = 2,7× de margem.
PADRAO = (1 * MiB, 30)
# Meta: "Webhook payloads can be up to 3 MB"; Pluggy: sem 2XX em 10 s conta falha.
WEBHOOK = (3 * MiB, 10)
# MAX_OFX_BYTES + folga do multipart; 8,06 MiB a 750 kbit/s = 90 s, 2× = 180 s (< 300 s do Railway).
OFX = (MAX_OFX_BYTES + 64 * 1024, 180)

# Caminhos exatos: o inventário "Corpo lido à mão" do plano.
CAMINHOS_WEBHOOK = frozenset({
    "/wa/webhook",
    "/webhook",
    "/open-finance/pluggy/webhook",
    "/billing/webhook",
    "/billing/asaas/webhook",
    "/xquiz/webhook",
})
PREFIXO_OFX = "/ofx/import/"


def limites_da_rota(path: str) -> tuple[int, float]:
    if path in CAMINHOS_WEBHOOK:
        return WEBHOOK
    if path.startswith(PREFIXO_OFX):
        return OFX
    return PADRAO


def _content_length(scope) -> int | None:
    """Só o caminho rápido usa: inválido (`abc`, `-1`) vira None e a contagem
    decide. `len <= 18` evita o ValueError do `int()` acima de 4300 dígitos."""
    for nome, valor in scope.get("headers") or ():
        if nome == b"content-length" and valor.isdigit() and len(valor) <= 18:
            return int(valor)
    return None


class LimiteCorpoMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        teto, prazo = limites_da_rota(scope["path"])
        loop = asyncio.get_running_loop()
        lidos = 0
        inicio = None
        corpo_completo = False
        app_comecou = False
        respondeu = False

        async def recusar(status: int, motivo: str, detalhe: str) -> dict:
            nonlocal respondeu
            respondeu = True
            logger.info("limite_corpo %s %s %s bytes=%d", motivo, scope.get("method"), scope["path"], lidos)
            # Resposta já começada pelo app não aceita um segundo start; o app só
            # vê o disconnect. Nenhuma rota lê o corpo depois de responder hoje.
            if not app_comecou:
                corpo = json.dumps({"detail": detalhe}).encode()
                await send({"type": "http.response.start", "status": status, "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(corpo)).encode()),
                    (b"cache-control", b"no-store"),
                    (b"connection", b"close"),
                ]})
                await send({"type": "http.response.body", "body": corpo})
            return {"type": "http.disconnect"}

        async def receive_limitado():
            nonlocal lidos, inicio, corpo_completo
            if respondeu:
                return {"type": "http.disconnect"}
            if corpo_completo:
                # Sem prazo: quem espera o disconnect depois do corpo (BaseHTTPMiddleware,
                # StreamingResponse) não pode tomar 408.
                return await receive()
            if inicio is None:
                inicio = loop.time()
                declarado = _content_length(scope)
                if declarado is not None and declarado > teto:
                    return await recusar(413, "content_length", "Corpo da requisição grande demais.")
            try:
                async with asyncio.timeout(inicio + prazo - loop.time()):
                    mensagem = await receive()
            except TimeoutError:
                return await recusar(408, "prazo", "Tempo esgotado ao receber o corpo da requisição.")
            if mensagem["type"] == "http.request":
                lidos += len(mensagem.get("body", b""))
                if lidos > teto:
                    return await recusar(413, "tamanho", "Corpo da requisição grande demais.")
                if not mensagem.get("more_body", False):
                    corpo_completo = True
            return mensagem

        async def send_filtrado(mensagem):
            nonlocal app_comecou
            if respondeu and mensagem["type"].startswith("http.response."):
                return
            if mensagem["type"] == "http.response.start":
                app_comecou = True
            await send(mensagem)

        try:
            await self.app(scope, receive_limitado, send_filtrado)
        except Exception:
            # O app reage ao disconnect com exceção (ClientDisconnect, o 400 do
            # `except Exception` do Asaas...); já respondemos, então ela não é 500.
            if not respondeu:
                raise
