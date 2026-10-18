"""db/lancamentos_leitura.py — as leituras de `db/lancamentos.listar` com conexão própria.

`pagina` é o `GET /api/v2/lancamentos`; `um_item` é UM item do usuário pelo id `l<id>`/`c<id>`
(a escrita da IA relê por ele, para dizer o que vai apagar com o que o banco tem, não com o que
o modelo falou); `recentes` são os "últimos lançamentos" da IA. Moram aqui porque
`db/lancamentos.py` está no teto de linhas.
"""
from __future__ import annotations

from datetime import date, datetime

from utils_date import add_months, day_tz

from .analytics import termos_busca
from .connection import get_conn
from .lancamentos import _item, ler_cursor, cursor_de, listar
from .resumo_mes import MOTIVOS, mes_de


def pagina(user_id: int, ano: int, mes: int, agora: datetime, *, cursor: str | None = None,
           limite: int = 50, q: str | None = None, **filtros) -> dict:
    """O `GET /api/v2/lancamentos`. O mês pedido, cortado pela janela do plano como
    `resumo_do_mes`; com busca, a janela inteira do plano. `motivos` da lista: os do bloco
    de contas (situação de hoje) e `inicio_do_historico` quando a janela cortou o pedido.
    `cursor` vem cru do cliente: `CursorInvalido` sobe para quem chama."""
    from core.services.plan_service import history_earliest_date

    apos = ler_cursor(cursor) if cursor else None
    corte = history_earliest_date(user_id, agora)
    inicio, fim = (date.min, date.max) if termos_busca(q) else mes_de(date(ano, mes, 1))
    cortou = bool(corte and corte > inicio)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, mais = listar(cur, user_id, max(inicio, corte) if corte else inicio, fim,
                              q=q, apos=apos, limite=limite, **filtros)
        motivos = _motivos(cur, user_id, cortou)
        conn.rollback()
    return {"mes": f"{ano:04d}-{mes:02d}", "itens": [_item(r) for r in linhas],
            "proximo": cursor_de(linhas[-1]) if mais else None, "motivos": motivos}


def _motivos(cur, user_id: int, cortou: bool) -> list[str]:
    """Os `motivos` de uma lista: os do bloco de contas (situação de hoje) e `inicio_do_historico`
    quando a janela do plano cortou o pedido."""
    from .contas_hoje import listar as contas

    vistos = set(contas(cur, user_id)["motivos"])
    if cortou:
        vistos.add("inicio_do_historico")
    return [m for m in MOTIVOS if m in vistos]


def recentes(user_id: int, agora: datetime, limite: int, *, sem_categoria: bool = False) -> dict:
    """Os "últimos lançamentos" da IA: o `listar` do Extrato, mais novos primeiro, com 3 diferenças: o guardado
    entra (a IA o lista); a janela de 12 meses vai até o fim do mês SEGUINTE (a compra no cartão feita depois do
    fechamento entra pela fatura do mês que vem, `MES_CARTAO_SQL`, e no Extrato só aparece lá); e o parcelamento
    vem em UMA linha por data, a da menor parcela (o manual grava a data da compra em todas, então vira uma linha; no
    Open Finance cada parcela é uma cobrança com a sua data e cada uma aparece; recategorizar o `c<id>` já propaga ao grupo). Cada `l` leva o `user_seq` (o `#N` que o usuário vê) no
    campo `numero` do item (a tool o entrega ao modelo como `user_seq`) e a categoria como foi gravada em
    `categoria_nome` (o `_item` só tem a chave, sem acento nem caixa), ambos fora do `_item` (o contrato da v2 não os tem).
    `sem_categoria`: só as saídas não internas sem categoria ou em "outros" (o cartão também)."""
    from core.services.plan_service import history_earliest_date

    hoje = day_tz(agora)
    primeiro = hoje.replace(day=1)
    corte = history_earliest_date(user_id, agora)
    inicio = date(*add_months(primeiro.year, primeiro.month, -11), 1)  # M-11
    fim = mes_de(mes_de(hoje)[1])[1]  # dia 1 do mês depois do seguinte
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, _ = listar(cur, user_id, max(inicio, corte) if corte else inicio, fim,
                           limite=limite, com_guardado=True, sem_categoria=sem_categoria, uma_por_compra=True)
        ids = [r["id"] for r in linhas if r["tabela"] == "l"]
        cur.execute("select id, user_seq from launches where user_id = %s and id = any(%s)", (user_id, ids))
        seq = {r["id"]: r["user_seq"] for r in cur.fetchall()}
        motivos = _motivos(cur, user_id, bool(corte and corte > inicio))
        conn.rollback()
    return {"itens": [_item(r) | {"numero": seq.get(r["id"]) if r["tabela"] == "l" else None, "categoria_nome": r["categoria_cru"]}
                      for r in linhas],
            "motivos": motivos}


def um_item(user_id: int, tabela: str, n: int) -> dict | None:
    """O `_item` do Extrato de UMA linha do usuário (`tabela` "l"|"c", id `n`), ou None. Sem corte de
    janela nem de guardado: a escrita da v2 também não corta, e o guardado volta com `pode=[]`.
    Limite: linha de `launches` de tipo que o Extrato não lista e estorno de cartão viram None."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, _ = listar(cur, user_id, date.min, date.max, ident=(tabela, n), limite=1, com_guardado=True)
        conn.rollback()
    return _item(linhas[0]) if linhas else None
