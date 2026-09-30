"""`scripts/conferir_fotos_of.py` contra Postgres real.

Isolamento: as seções agregam o banco INTEIRO (é o que o script faz em produção),
e o banco da execução tem linhas de outros testes. Então o teste apaga as duas
tabelas DENTRO de uma transação, semeia, roda o `conferir` no mesmo cursor e dá
ROLLBACK — asserções exatas, e nada do que foi apagado ou semeado fica.
O `delete` sem `where` não trava ninguém: cada worker do xdist tem o próprio
database `pytest_*` (skill baseline-testes) e, dentro dele, os testes são sequenciais.
"Hoje" é o dia de SP calculado pelo próprio Postgres, como no script.
`now()` é o início da transação, então espelho e foto de hoje semeados com
`now()` têm `updated_at = observed_at`: é o "mesmo sync" que a seção 3 exige.
D e E têm o espelho 1 µs depois da foto (sync posterior do mesmo dia).

CONTROLES (CLAUDE.md §3), medidos:
  • seção 3 comparando TOTAIS de fotos × posições por conexão (a versão antiga)
    → vermelho: a foto órfã da conexão C cobre a posição sem foto e sai "ok";
  • `left join` → `join` na seção 3 → vermelho (some o "sem foto nenhuma");
  • seção 3 casando só pelo dia, sem `observed_at = updated_at` → vermelho
    (D e E saem "ok");
  • trocar os filtros de `confirmadas`/`nao_confirmadas` na seção 2 → vermelho
    (hoje são 5 × 2, assimétrico de propósito);
  • tirar o `where dia_ant = observed_on - 1` da seção 7 → vermelho (a lacuna da
    inv-a2 e as linhas sem dia anterior entram na conta);
  • tirar a janela da seção 7 → vermelho (com dias=2 contaria os 5 pares);
  • tirar a janela da seção 4 → vermelho (a foto antiga fora do dia de SP, em
    hoje-20, apareceria como `NAO BATE` com dias=14).
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
    d = _conexao(user_id, f"item-{user_id}-cfd")["id"]   # ambígua: confirmada de sync anterior
    e = _conexao(user_id, f"item-{user_id}-cfe")["id"]   # não confirmada de sync anterior = falha
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
            cur.executemany(
                "insert into open_finance_investments (connection_id, provider_investment_id, updated_at)"
                " values (%s, %s, now() + interval '1 microsecond')", [(d, "inv-d1"), (e, "inv-e1")])
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
            cur.executemany(foto, [(d, "inv-d1", hoje, 0, 0, True, "ACTIVE", 1, 1),
                                   (e, "inv-e1", hoje, 0, 0, False, "ACTIVE", 1, 1)])
            # Descompasso de fuso antigo: foto de hoje-20 gravada no instante de hoje-19.
            cur.execute(foto, (a, "inv-a0", hoje, 20, 19, True, "ACTIVE", 1, 1))
            capsys.readouterr()
            conferir(cur, 14)
            saida = capsys.readouterr().out
            conferir(cur, 2)
            saida2 = capsys.readouterr().out
            conferir(cur, 30)
            saida30 = capsys.readouterr().out
        finally:
            c.rollback()

    assert f"dia={hoje} | fotos=7 | conexoes=4 | confirmadas=5 | nao_confirmadas=2" in _secao(saida, 2)
    assert _secao(saida, 3) == ["situacao=ambigua: foto confirmada de sync anterior | conexoes=1",
                                "situacao=foto parcial | conexoes=2", "situacao=ok | conexoes=1",
                                "situacao=sem foto nenhuma | conexoes=1"]
    assert _secao(saida, 4) == ["dia=bate | fotos=13"]
    # sorted: a ordem de 'NAO BATE' × 'bate' depende da collation do banco.
    assert sorted(_secao(saida30, 4)) == ["dia=NAO BATE | fotos=1", "dia=bate | fotos=13"]
    assert _secao(saida, 6) == ["status=ACTIVE | fotos=6", "status=TOTAL_WITHDRAWAL | fotos=1"]
    assert _secao(saida, 7) == [
        "pares=5 | nada_mudou=0 | so_saldo_mudou=4 | aplicado_e_saldo_mesmo_delta=0"
        " | aplicado_mudou_delta_diferente=1 | taxa_banco_mudou=0"]
    # dias=2: só os pares que terminam em hoje-2, hoje-1 e hoje (o aporte, em hoje-3, fica fora).
    assert _secao(saida2, 7) == [
        "pares=3 | nada_mudou=0 | so_saldo_mudou=3 | aplicado_e_saldo_mesmo_delta=0"
        " | aplicado_mudou_delta_diferente=0 | taxa_banco_mudou=0"]
