"""Valida e agrega snapshots paginados antes de autorizar reconciliação."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation


def _number(data: dict, field: str, error_type: type[Exception]) -> int:
    value = data.get(field)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not float(value).is_integer() or value < 0):
        raise error_type("Resposta inválida ao consultar investimentos na Pluggy.")
    return int(value)


def _valid_balance(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, TypeError, ValueError):
        return False


def collect_investment_pages(
    fetch: Callable[[int], dict], error_type: type[Exception], *, max_pages: int
) -> list[dict]:
    """Só devolve dados quando todas as páginas formam um snapshot coerente."""
    out: list[dict] = []
    seen_ids: set[str] = set()
    expected_total: int | None = None

    for requested_page in range(1, max_pages + 1):
        data = fetch(requested_page)
        results = data.get("results")
        if not isinstance(results, list):
            raise error_type("Resposta inválida ao consultar investimentos na Pluggy.")

        total = _number(data, "total", error_type)
        total_pages = _number(data, "totalPages", error_type)
        page = _number(data, "page", error_type)
        if expected_total is None:
            expected_total = total
        if total != expected_total or total_pages > max_pages:
            raise error_type("Paginação inválida ao consultar investimentos na Pluggy.")
        if total_pages == 0:
            if total or results:
                raise error_type("Paginação inválida ao consultar investimentos na Pluggy.")
            return []
        if page != requested_page:
            raise error_type("Paginação inválida ao consultar investimentos na Pluggy.")

        for investment in results:
            provider_id = investment.get("id") if isinstance(investment, dict) else None
            if not str(provider_id or "").strip() or str(provider_id) in seen_ids:
                raise error_type("Investimento inválido na resposta da Pluggy.")
            if not _valid_balance(investment.get("balance")):
                raise error_type("Saldo de investimento inválido na resposta da Pluggy.")
            seen_ids.add(str(provider_id))
            out.append(investment)

        if requested_page >= total_pages:
            if len(out) != total:
                raise error_type("Paginação incompleta ao consultar investimentos na Pluggy.")
            return out

    raise error_type("Paginação excedeu o limite ao consultar investimentos na Pluggy.")
