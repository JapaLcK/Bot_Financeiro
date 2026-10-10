"""Leitura de `/bills` da Pluggy (faturas que o banco fechou), paginada.

Módulo próprio porque `core/services/pluggy.py` está no teto de linhas (ver
tests/test_max_lines_python.py). Só LÊ:
quem grava é `db/of_card_bills.py`. Como a gravação nunca apaga, uma leitura que
termina antes da hora só deixa de atualizar — por isso metadata incoerente, `total` que
não bate com o lido, id repetido entre páginas, página vazia sem prova e estouro do teto LEVANTAM
(`PluggyApiError`) em vez de devolver lista parcial. Limite: paginação por deslocamento sem snapshot pode
pular uma fatura que muda de página entre requisições, sem sinal; aqui nada é apagado, então o efeito é a
fatura não atualizar naquele sync (e a V1 contar a menos), não perda de dado.
"""
from __future__ import annotations

from collections.abc import Callable

from core.services.pluggy import PluggyApiError, _pluggy_get
from core.services.pluggy_investments import _inv_int


def list_pluggy_bills(account_id: str, api_key: str, *, max_pages: int = 20,
                      on_page: Callable[[], None] | None = None) -> list[dict]:
    """Todas as faturas da conta (`accountId` da Pluggy), página a página (1-based).
    `on_page` é o heartbeat do sync, chamado antes de CADA página (como em
    `list_pluggy_transactions`); falha dele nunca interrompe a leitura."""
    out: list[dict] = []
    total = contagem = None
    vistos: set[str] = set()
    for pagina in range(1, max_pages + 1):
        if on_page is not None:
            try:
                on_page()
            except Exception:
                pass
        data = _pluggy_get("/bills", api_key, {"accountId": account_id, "page": pagina})
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise PluggyApiError("Leitura de /bills incompleta: resposta sem results.")
        paginas = _inv_int(data.get("totalPages"))
        if (paginas is None or paginas < 0 or (total is not None and paginas != total)
                or ("page" in data and _inv_int(data["page"]) != pagina)
                or (pagina > paginas and data["results"])):
            raise PluggyApiError("Leitura de /bills incompleta: metadata de paginação incoerente.")
        total = paginas
        if "total" in data:                 # fixado pela 1ª página; as outras têm de repetir
            n = _inv_int(data["total"])
            if n is None or n < 0 or (pagina > 1 and n != contagem):
                raise PluggyApiError("Leitura de /bills incompleta: total incoerente.")
            contagem = n
        if not data["results"] and paginas != 0 and contagem != 0:   # `contagem` já é a desta página
            raise PluggyApiError("Leitura de /bills incompleta: página vazia sem prova.")
        for item in data["results"]:        # item sem id usável: a gravação o pula (db/of_card_bills.py)
            bid = item.get("id") if isinstance(item, dict) else None
            if isinstance(bid, str) and bid.strip():
                if bid.strip() in vistos:   # janela deslizante: o que ficou no fim nunca foi lido
                    raise PluggyApiError("Leitura de /bills incompleta: id repetido.")
                vistos.add(bid.strip())
        out += data["results"]
        if pagina >= total:
            if contagem is not None and len(out) != contagem:
                raise PluggyApiError("Leitura de /bills incompleta: total não bate.")
            return out
    raise PluggyApiError(f"Leitura de /bills passou do teto de {max_pages} páginas.")
