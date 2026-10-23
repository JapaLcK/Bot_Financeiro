"""Issue #1243: `candidatos_carteira` agrupa os lançamentos por centavos UMA vez (O(P+L)), em vez de varrer a lista
inteira por declaração (O(P×L)). Nada observável muda: mesmos ids, mesma ordem, por declaração.

Controles (CLAUDE.md §3; rodados numa CÓPIA, nunca no worktree):
- agrupar por reais inteiros (`int(_money(...))` na chave E `int(centavos)` na busca) -> VERMELHO
  `test_equivale_ao_algoritmo_antigo` (49,99 e 50,01 entram na lista de 50,00).
- alargar a janela (um dia a mais nos dois lados) -> VERMELHO `test_equivale_ao_algoritmo_antigo` (os lançamentos a 4 e
  5 dias entram); deslocá-la de um lado só (inclui -4 e mantém +3; ou o espelho) -> VERMELHO
  `test_bordas_da_janela_decidem_sem_o_corte` (o corte em 5 candidatos mascara as bordas em quase todo o outro teste).
- voltar ao laço antigo (varrer todos os lançamentos por declaração), com ou sem os centavos guardados uma vez por
  lançamento -> VERMELHO `test_trabalho_cresce_com_p_mais_l` (conta linhas executadas, não chamadas de `_money`).
- ler o dia da declaração em UTC (`declared_at.date()` no lugar de `_dia(d)`) ou o do lançamento em UTC
  (`criado_em.date()` no lugar de `launch_day`) -> VERMELHO `test_dia_vem_do_fuso_do_app_nao_do_utc`.
Positivo: `test_equivale_...` (listas cheias, vazias e cortadas em MAX_CANDIDATOS), `test_bordas_...` (-3 e +3 entram) e os H1-H16.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

import db
from conftest import _cleanup_user, promote_to_pro
from db import bank_dinheiro
from db.accounts import _LIGADO_SQL
from db.bank_declaracoes import CARTEIRA_SQL, _dia, e_fluxo, tem_evidencia
from db.connection import TIPO_CANON_SQL, get_conn
from db.open_finance_cash import VINCULADO_SQL
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import em
from tests.test_declaracao_a2b2_dinheiro import _especie, lanca
from utils_date import _tz, launch_day


def antigo(cur, user_id, declarations):
    """Referência: `candidatos_carteira` ANTES da #1243 (varre todos os lançamentos por declaração)."""
    from db.bank_movements import _money
    from db.open_finance import RECON_DATE_WINDOW
    pendentes = [d for d in declarations if e_fluxo(d) and not d["matched_transaction_id"]]
    if not pendentes:
        return {}
    dias, w = [_dia(d) for d in pendentes], timedelta(days=RECON_DATE_WINDOW + 1)
    cur.execute(
        f"""select id, alvo, categoria, {TIPO_CANON_SQL} as tipo, valor, criado_em from launches
             where user_id = %s and {TIPO_CANON_SQL} in ('despesa', 'receita') and {CARTEIRA_SQL}
               and not is_internal_movement and not (efeitos ? '{bank_dinheiro.MARCADOR}')
               and not {_LIGADO_SQL} and not {VINCULADO_SQL}
               and round(valor::numeric, 2) = any(%s) and criado_em >= %s and criado_em < %s""",
        (user_id, [abs(_money(d["amount"])) for d in pendentes],
         datetime.combine(min(dias) - w, time.min, tzinfo=_tz()),
         datetime.combine(max(dias) + w + timedelta(days=1), time.min, tzinfo=_tz())))
    lancamentos = [dict(r, dia=launch_day(r["criado_em"])) for r in cur.fetchall()]
    saida = {}
    for d in pendentes:
        certos = [c for c in lancamentos if c["tipo"] == d["tipo"] and _money(c["valor"]) == abs(_money(d["amount"]))
                  and abs((c["dia"] - _dia(d)).days) <= RECON_DATE_WINDOW]
        certos.sort(key=lambda c: (not tem_evidencia(d, {"description": c["alvo"]}),
                                   abs((c["dia"] - _dia(d)).days), c["id"]))
        saida[d["launch_id"]] = [{"id": c["id"], "name": c["alvo"], "category": c["categoria"],
                                  "amount": float(c["valor"]), "date": c["dia"].isoformat()}
                                 for c in certos[:bank_dinheiro.MAX_CANDIDATOS]]
    return saida


def decl_falsa(n, valor, dia, tipo="despesa", alvo="mercado", casada=None, account_id=None):
    """A forma que `_declarations` devolve, só com o que `candidatos_carteira` lê (`amount` já é assinado)."""
    return {"launch_id": n, "tipo": tipo, "account_id": account_id, "matched_transaction_id": casada, "alvo": alvo,
            "amount": Decimal(str(valor)) * (1 if tipo == "receita" else -1), "declared_at": em(dia)}


@pytest.fixture
def outro():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    yield uid
    _cleanup_user(uid)


def _roda(fn, uid, declaracoes):
    with get_conn() as conn, conn.cursor() as cur:
        return fn(cur, uid, declaracoes)


def test_equivale_ao_algoritmo_antigo(user_id, outro):
    promote_to_pro(user_id)
    alvos = ["mercado", "padaria", "mercado extra"]
    for valor in (50, 50.01, 49.99, 20):  # mesmos centavos e vizinhos de 1 centavo
        for dia in range(-5, 6):  # ±3 dentro da janela, ±4 e ±5 fora
            for tipo in ("despesa", "receita"):
                lanca(user_id, valor, dia, alvos[(dia + 5) % 3], tipo=tipo)
    for i in range(3):  # empate de dia e de evidência: desempata o id
        lanca(user_id, 50, 1, "mercado")
    legado = [lanca(user_id, 20, 0, "mercado", tipo="receita"), lanca(user_id, 50, 2, "mercado", tipo="receita")]
    q("update launches set tipo='entrada' where id = any(%s) returning 1", (legado,))  # tipo canônico legado
    fora = [lanca(user_id, 50, 0), lanca(user_id, 50, 1)]
    q("update launches set efeitos = efeitos || '{\"foi_em_dinheiro\": {}}'::jsonb where id=%s returning 1", (fora[0],))
    q("update launches set is_internal_movement=true where id=%s returning 1", (fora[1],))
    _especie(user_id, None, None, None)
    lanca(outro, 50, 0)  # de outro usuário: jamais entra
    declaracoes = [decl_falsa(1, 50, 0), decl_falsa(2, 50, 1, alvo="padaria"), decl_falsa(3, 50, -2, alvo=None),
                   decl_falsa(4, 50.01, 3), decl_falsa(5, 49.99, -3), decl_falsa(6, 20, 0, "receita"),
                   decl_falsa(7, 20, 0, "despesa"), decl_falsa(8, 50, 0, "receita", "freela"),
                   decl_falsa(9, 77, 0),  # sem nenhum lançamento
                   decl_falsa(10, 50, 0, casada=99), decl_falsa(11, 50, 0, account_id=7)]  # não são "sem par"/fluxo
    novo = _roda(bank_dinheiro.candidatos_carteira, user_id, declaracoes)
    assert novo == _roda(antigo, user_id, declaracoes)  # mesmos ids, mesma ordem, por declaração
    tam = {k: len(v) for k, v in novo.items()}
    assert tam[1] == bank_dinheiro.MAX_CANDIDATOS and tam[9] == 0 and tam[6] > 0 and 10 not in tam and 11 not in tam
    assert all(len(set(c["id"] for c in v)) == len(v) for v in novo.values())


def test_bordas_da_janela_decidem_sem_o_corte(user_id):
    """Cada declaração tem UM candidato, num só dia da borda: com o corte em MAX_CANDIDATOS fora de jogo, a janela
    deslocada de um lado só muda o resultado (-3 e +3 entram, -4 e +4 não)."""
    declaracoes = []
    for i, dia in enumerate((-4, -3, 3, 4)):
        declaracoes.append(decl_falsa(i + 1, 70 + i, 0))
        lanca(user_id, 70 + i, dia, "mercado")
    novo = _roda(bank_dinheiro.candidatos_carteira, user_id, declaracoes)
    assert novo == _roda(antigo, user_id, declaracoes)
    assert [len(novo[k]) for k in (1, 2, 3, 4)] == [0, 1, 1, 0]


def test_dia_vem_do_fuso_do_app_nao_do_utc(user_id):
    """23h30 locais já são o dia SEGUINTE em UTC. Duas leituras do dia, ambas na borda +3 da janela:
    a declaração (`_dia`, não `declared_at.date()`) e o lançamento (`launch_day`, não `criado_em.date()`, com a sessão
    do Postgres em UTC como a produção já teve)."""
    lanca(user_id, 70, 0)
    tarde = em(0).replace(hour=23, minute=30)  # dia 0 local; dia +1 em UTC
    lanca_tarde = db.add_launch_and_update_balance(user_id, "despesa", 71, "mercado", "feira", "alimentacao", tarde)[0]
    declaracoes = [decl_falsa(1, 70, 3), decl_falsa(2, 71, -3)]
    declaracoes[0]["declared_at"] = em(3).replace(hour=23, minute=30).astimezone(timezone.utc)  # dia +3 local, +4 UTC

    def utc(cur, uid, decls):
        cur.execute("select set_config('TimeZone', 'UTC', true)")  # só nesta transação
        return bank_dinheiro.candidatos_carteira(cur, uid, decls)

    novo = _roda(utc, user_id, declaracoes)
    assert novo == _roda(antigo, user_id, declaracoes)
    assert [len(novo[1]), [c["id"] for c in novo[2]]] == [1, [lanca_tarde]]


def _cenario(uid, p, por_valor):
    """`p` declarações de centavos distintos, cada uma com `por_valor` lançamentos do mesmo valor na janela."""
    declaracoes = []
    for k in range(p):
        valor = 100 + k  # centavos distintos por declaração
        declaracoes.append(decl_falsa(k + 1, valor, 0))
        for j in range(por_valor):
            lanca(uid, valor, j % 4 - 1, "mercado")
    return declaracoes, p * por_valor


def _eventos(fn, uid, declaracoes):
    """`(resultado, nº de linhas executadas no arquivo de `fn`)`: a medida do trabalho do laço, qualquer que seja a
    operação dentro dele (não depende de `_money` nem de nenhum nome do produto)."""
    arquivo, n = fn.__code__.co_filename, [0]

    def local(frame, evento, arg):
        n[0] += evento == "line"
        return local

    anterior = sys.gettrace()  # depurador/coverage ativos: devolve o tracer deles, não o apaga
    sys.settrace(lambda frame, evento, arg: local if frame.f_code.co_filename == arquivo else None)
    try:
        return _roda(fn, uid, declaracoes), n[0]
    finally:
        sys.settrace(anterior)


@pytest.mark.parametrize("p", [40, 80])
def test_trabalho_cresce_com_p_mais_l(user_id, p):
    """Conta linhas executadas, não segundos: o antigo faz ~P×L, o novo ~L+P. Orçamento de 20 por unidade; o código
    real mede ~10 (Python 3.13), então refatoração inócua cabe e qualquer varredura total por declaração estoura."""
    declaracoes, n_lanc = _cenario(user_id, p, 10)
    novo, n_novo = _eventos(bank_dinheiro.candidatos_carteira, user_id, declaracoes)
    velho, n_antigo = _eventos(antigo, user_id, declaracoes)
    assert novo == velho
    assert all(len(v) == bank_dinheiro.MAX_CANDIDATOS for v in novo.values())
    assert n_novo <= 20 * (p + n_lanc)  # linear em P+L (o real faz ~10 por unidade; o laço sobre tudo, ~P×L)
    assert n_antigo >= p * n_lanc  # a referência é quadrática: o contador mede o que importa
