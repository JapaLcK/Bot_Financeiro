"""Os RESUMOS que o modelo vê no lugar da tabela inteira quando o card está na tela (o modelo que a vê inteira a copia).

Funções puras sobre o dict da visão. Moram fora de `cards.py` (teto de 350 linhas); `montar` fica lá, por causa
dos monkeypatches dos testes. O dinheiro vai em float sob chave `total_*`/`valor*` (o `_CHAVE_MONETARIA_RE` do `ai_guard`).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from db.connection import CAT_VAZIA_LABEL, cat_key
from utils_text import normalize_text


def _dec(x) -> Decimal:
    return Decimal(str(x))  # o float da visão vem do texto do bloco (`_casa`): o `str` dele volta o mesmo texto


def _extremos(linhas) -> dict:
    """`maior_mes`/`menor_mes` por `saiu` entre as (mês, entrou, saiu) COM movimento (o `compute_evolution` preenche
    com zero, como na `tendencia`); None se nenhuma."""
    com = [l for l in linhas if l[1] or l[2]]
    um = lambda l: l and {"mes": l[0], "saiu": l[2]}  # noqa: E731
    return {"maior_mes": um(max(com, key=lambda l: l[2], default=None)),
            "menor_mes": um(min(com, key=lambda l: l[2], default=None))}


def _top3(v: dict, categoria: str | None) -> dict:
    """As 3 maiores categorias (o `montar` as corta para 1 por aba ao comparar meses), o total delas e, se o usuário
    citou uma, o valor dela (`categoria_pedida`). Casa pela MESMA chave do `get_category_spend` (`cat_key`: caixa e
    acento, vazio = `CAT_VAZIA_LABEL`); sem ela, pelo
    `normalize_text` (pontuação fora: "e commerce" = "e-commerce") só se der um candidato único. Achou:
    `{categoria, valor}`; não (ou ambíguo): `encontrada: False`, sem número."""
    cats = v["categorias"]  # o leitor já as ordena por valor, da maior (`db/mes_detalhes.py`)
    out = {**v, "categorias": cats[:3], "categorias_total": len(cats)}
    if categoria:
        achadas = [c for c in cats if cat_key(c["categoria"]) == cat_key(categoria)]
        if not achadas and (n := normalize_text(categoria)):  # só texto (emoji e símbolo viram "" e não casam)
            achadas = [c for c in cats if normalize_text(c["categoria"]) == n]
        achada = achadas[0] if len(achadas) == 1 else None
        out["categoria_pedida"] = ({"categoria": achada["categoria"] or CAT_VAZIA_LABEL, "valor": achada["valor"]}
                                   if achada else {"categoria": categoria, "encontrada": False})
    return out


def _resumo_periodo(v: dict, categoria: str | None) -> dict:
    out = _top3({k: x for k, x in v.items() if k != "meses"}, categoria)
    return out | _extremos([(m["mes"], m["entrou"], m["saiu"]) for m in v["meses"]])


def _resumo_categorias(v: dict, categoria: str | None) -> dict:
    return _top3(v, categoria) | {"saldo": float(_dec(v["entrou"]) - _dec(v["saiu"]))}


def _resumo_renda(v: dict, categoria: str | None) -> dict:
    ev = v["evolution"]
    entrou, saiu = (sum((_dec(m[k]) for m in ev), Decimal(0)) for k in ("income", "expense"))
    return {"months": v["months"], "tendencia": v["tendencia"], "entrou": float(entrou), "saiu": float(saiu),
            "saldo": float(entrou - saiu),
            "por_mes": [{"mes": m["month"], "receita": float(_dec(m["income"])), "despesa": float(_dec(m["expense"]))} for m in ev],  # `receita`/`despesa`: chaves que o `ai_guard` reconhece como dinheiro
            } | _extremos([(m["month"], m["income"], m["expense"]) for m in ev])


def _resumo_calendario(v: dict, categoria: str | None) -> dict:
    com_gasto = [d for d in v["dias"] if d["saiu"] > 0]
    maior = max(com_gasto, key=lambda d: d["saiu"], default=None)
    return {**{k: x for k, x in v.items() if k != "dias"}, "maior_dia": maior and {"dia": maior["dia"], "saiu": maior["saiu"]}}


_RESUMOS = {"periodo": _resumo_periodo, "categorias": _resumo_categorias, "renda": _resumo_renda,
            "calendario": _resumo_calendario}


def resumo_maiores(r: dict[str, Any]) -> dict[str, Any]:
    """O que o MODELO vê quando o card `maiores_gastos` está na tela: a lista está lá, e o modelo que a vê inteira a copia
    (como `_RESUMOS`). Dinheiro em float sob chave `total_*`/`valor*` (o `_CHAVE_MONETARIA_RE` do `ai_guard` casa `^total_`)."""
    rows = r["expenses"]
    return {
        **{k: v for k, v in r.items() if k != "expenses"},
        # soma só dos `count` itens listados, NÃO o gasto do período (chave `total_*`: o `ai_guard` a reconhece como dinheiro)
        "total_dos_itens": float(sum((Decimal(str(x["valor"])) for x in rows), Decimal(0))),
        "maior": max(rows, key=lambda x: x["valor"]),
        "categorias": list(dict.fromkeys(x["categoria"] for x in rows if x.get("categoria")))[:3],
        "card_na_tela": True,
    }


def resumo_comparacao(j: dict[str, Any]) -> dict[str, Any]:
    """O que o MODELO vê quando o card `comparacao` está na tela: as categorias estão lá (fica só a que mais mudou, para
    o comentário; `categorias_total` diz quantas há, como no `_top3`), com `escolha`, `nota` e `motivos`, que o card não
    mostra. Os números seguem sob `total_*` (o `ai_guard`)."""
    out = {k: v for k, v in j.items() if k != "categorias_omitidas"}
    return out | {"categorias": j["categorias"][:1], "categorias_total": len(j["categorias"]) + j["categorias_omitidas"],
                  "card_na_tela": True}
