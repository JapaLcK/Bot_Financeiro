"""`scripts/conferir_fotos_of.py` contra Postgres real.

Isolamento: as seções agregam o banco INTEIRO (é o que o script faz em produção),
e o banco da execução tem linhas de outros testes. Então o teste apaga as duas
tabelas DENTRO de uma transação, semeia, roda o `conferir` no mesmo cursor e dá
ROLLBACK — asserções exatas, e nada do que foi apagado ou semeado fica.
O `delete` sem `where` não trava ninguém: cada worker do xdist tem o próprio
database `pytest_*` (skill baseline-testes) e, dentro dele, os testes são sequenciais.
"Hoje" é o dia de SP calculado pelo próprio Postgres, como no script.

CONTROLES (CLAUDE.md §3), medidos:
  • seção 3 comparando TOTAIS de fotos × posições por conexão (a versão antiga)
    → vermelho: a foto órfã da conexão C cobre a posição sem foto e sai "ok";
  • `left join` → `join` na seção 3 → vermelho (some o "sem foto nenhuma");
  • trocar os filtros de `confirmadas`/`nao_confirmadas` na seção 2 → vermelho
    (hoje são 4 × 1, assimétrico de propósito);
  • tirar o `where dia_ant = observed_on - 1` da seção 7 → vermelho (a lacuna da
    inv-a2 e as linhas sem dia anterior entram na conta).
"""
from db.connection import get_conn
from scripts.conferir_fotos_of import conferir
from test_of_connection_state import _conexao

# inv-a1: 6 dias seguidos; o saldo sobe todo dia e o aplicado muda uma vez
# (+50 no aplicado, +1 no saldo). (dias atrás, saldo, aplicado)
A1 = [(5, 100, 100), (4, 101, 100), (3, 102, 150), (2, 103, 150), (1, 104, 150), (0, 105, 150)]


def _secao(saida: str, n: int) -> list[str]:
    bloco = next(b for b in saida.split("\n## ") if b.startswith(f"{n}."))
    return [linha.strip() for linha in bloco.splitlines()[1:] if linha.strip()]


def test_conferir_conta_o_que_foi_semeado(user_id, capsys):
    a = _conexao(user_id, f"item-{user_id}-cfa")["id"]   # ok
    b = _conexao(user_id, f"item-{user_id}-cfb")["id"]   # sem foto nenhuma
    c_ = _conexao(user_id, f"item-{user_id}-cfc")["id"]  # foto parcial, com foto órfã
    with get_conn() as c, c.cursor() as cur:
        try:
            cur.execute("delete from open_finance_investment_snapshots")
            cur.execute("delete from open_finance_investments")
            cur.execute("select (now() at time zone 'America/Sao_Paulo')::date as d")
            hoje = cur.fetchone()["d"]
            cur.executemany(
                "insert into open_finance_investments (connection_id, provider_investment_id, updated_at)"
                " values (%s, %s, now())",
                [(a, "inv-a1"), (a, "inv-a2"), (b, "inv-b1"),
                 (c_, "inv-c1"), (c_, "inv-c2"), (c_, "inv-c3")])
            foto = ("insert into open_finance_investment_snapshots (connection_id,"
                    " provider_investment_id, observed_on, observed_at, collection_confirmed,"
                    " status, balance, amount) values (%s, %s, %s::date - %s,"
                    " now() - make_interval(days => %s), %s, %s, %s, %s)")
            cur.executemany(foto, [(a, "inv-a1", hoje, k, k, True, "ACTIVE", s, ap)
                                   for k, s, ap in A1])
            cur.executemany(foto, [(a, "inv-a2", hoje, 3, 3, True, "ACTIVE", 10, 10),
                                   (a, "inv-a2", hoje, 0, 0, False, "TOTAL_WITHDRAWAL", 0, 0)])
            # C: 3 posições no espelho, 3 fotos hoje, mas inv-c3 sem foto e inv-c9 órfã.
            cur.executemany(foto, [(c_, pid, hoje, 0, 0, True, "ACTIVE", 1, 1)
                                   for pid in ("inv-c1", "inv-c2", "inv-c9")])
            capsys.readouterr()
            conferir(cur, 14)
            saida = capsys.readouterr().out
        finally:
            c.rollback()

    assert f"dia={hoje} | fotos=5 | conexoes=2 | confirmadas=4 | nao_confirmadas=1" in _secao(saida, 2)
    assert _secao(saida, 3) == ["situacao=foto parcial | conexoes=1", "situacao=ok | conexoes=1",
                                "situacao=sem foto nenhuma | conexoes=1"]
    assert _secao(saida, 4) == ["dia=bate | fotos=11"]
    assert _secao(saida, 6) == ["status=ACTIVE | fotos=4", "status=TOTAL_WITHDRAWAL | fotos=1"]
    assert _secao(saida, 7) == [
        "pares=5 | nada_mudou=0 | so_saldo_mudou=4 | aplicado_e_saldo_mesmo_delta=0"
        " | aplicado_mudou_delta_diferente=1 | taxa_banco_mudou=0"]
