"""db/lancamentos_leitura.py — as leituras de `db/lancamentos.listar` com conexão própria.

`pagina` é o `GET /api/v2/lancamentos`; `um_item` é UM item do usuário pelo id `l<id>`/`c<id>`
(a escrita da IA relê por ele, para dizer o que vai apagar com o que o banco tem, não com o que
o modelo falou). Moram aqui porque `db/lancamentos.py` está no teto de linhas.
"""
from __future__ import annotations

from datetime import date, datetime

from .analytics import termos_busca
from .connection import get_conn
from .lancamentos import _item, ler_cursor, cursor_de, listar


def pagina(user_id: int, ano: int, mes: int, agora: datetime, *, cursor: str | None = None,
           limite: int = 50, q: str | None = None, **filtros) -> dict:
    """O `GET /api/v2/lancamentos`. O mês pedido, cortado pela janela do plano como
    `resumo_do_mes`; com busca, a janela inteira do plano. `motivos` da lista: os do bloco
    de contas (situação de hoje) e `inicio_do_historico` quando a janela cortou o pedido.
    `cursor` vem cru do cliente: `CursorInvalido` sobe para quem chama."""
    from core.services.plan_service import history_earliest_date

    from .contas_hoje import listar as contas
    from .resumo_mes import MOTIVOS, mes_de

    apos = ler_cursor(cursor) if cursor else None
    corte = history_earliest_date(user_id, agora)
    inicio, fim = (date.min, date.max) if termos_busca(q) else mes_de(date(ano, mes, 1))
    cortou = bool(corte and corte > inicio)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, mais = listar(cur, user_id, max(inicio, corte) if corte else inicio, fim,
                              q=q, apos=apos, limite=limite, **filtros)
        vistos = set(contas(cur, user_id)["motivos"])
        conn.rollback()
    if cortou:
        vistos.add("inicio_do_historico")
    return {"mes": f"{ano:04d}-{mes:02d}", "itens": [_item(r) for r in linhas],
            "proximo": cursor_de(linhas[-1]) if mais else None,
            "motivos": [m for m in MOTIVOS if m in vistos]}


def um_item(user_id: int, tabela: str, n: int) -> dict | None:
    """O `_item` do Extrato de UMA linha do usuário (`tabela` "l"|"c", id `n`), ou None. Sem corte de
    janela nem de guardado: a escrita da v2 também não corta, e o guardado volta com `pode=[]`.
    Limite: linha de `launches` de tipo que o Extrato não lista e estorno de cartão viram None."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, _ = listar(cur, user_id, date.min, date.max, ident=(tabela, n), limite=1, com_guardado=True)
        conn.rollback()
    return _item(linhas[0]) if linhas else None
