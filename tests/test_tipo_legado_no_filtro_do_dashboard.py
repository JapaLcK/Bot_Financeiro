"""O filtro do dashboard lê as duas formas, e o COUNT bate com a LISTA.

Partido de `tests/test_tipo_legado_no_dashboard.py` (§0.5) — lá ficam os números
do mês e os helpers de seed; em `..._na_projecao_do_dashboard.py`, o rótulo que a
query 4 devolve. Aqui: `_dashboard_launch_filter_sql` e as DUAS queries que o
interpolam.

O filtro roda no WHERE da perna de DENTRO, contra a coluna CRUA, e por isso lê
`saida` junto de `despesa` e `entrada` junto de `receita` — a canonização entrou
no SELECT de FORA da query 4 e não pode ter sido levada junto, senão um dos dois
filtros volta vazio (é dinheiro sumindo da tela sem erro nenhum).

E os dois números do card "Lançamentos" saem de queries DIFERENTES: o `total` da
query 3 (o COUNT) e as linhas da query 4. Eles só concordam porque as pernas de
DENTRO interpolam as MESMAS CINCO coisas — o `WHERE user_id = %s`, a janela
`AND criado_em >= %s AND criado_em < %s`, o `{launch_filter_sql}`, o
`AND tipo NOT IN ('criar_caixinha', ...)` e o `{credit_union_sql}`. Só as TRÊS
primeiras têm controle abaixo: as outras duas estão na lista de classes CEGAS no
fim deste cabeçalho, e é por elas que a cobertura deste par NÃO está fechada.

Controles do grupo. As mutações foram RODADAS nesta árvore e cada uma nomeia o(s)
VERMELHO(s) que produz; se o resultado for outro, o conserto mudou de lugar e o
grupo parou de medir o que diz medir.

  1. POSITIVO do filtro. Tire a forma legada das DUAS pernas de
     `_dashboard_launch_filter_sql` (frontend/finance_bot_websocket_custom.py),
     deixando `tipo IN ('despesa')` e `tipo IN ('receita')` → ficam VERMELHOS
     `test_filtro_continua_achando_as_duas_formas` (`assert [] == [100.0]`) e
     `test_contagem_sob_filtro_bate_com_a_lista_pagina_a_pagina`
     (`assert (0, 1) == (60, 3)`).
     NÃO confunda com tirar a forma legada de `TIPO_DESPESA_SQL`/
     `TIPO_RECEITA_SQL` (db/connection.py): é injeção DIFERENTE, e deixa este
     arquivo INTEIRO verde enquanto reprova em outros — medido. O filtro do
     dashboard tem literal PRÓPRIO, e é só ele que este caso positivo guarda.

  2. As três dimensões do par query 3 × query 4. Todas as injeções são SÓ na
     query 3 (query 4 intacta) e todas ALARGAM em vez de apagar
     (docs/controles_declarados.md): nada é removido, não há expressão para
     quebrar sob nenhuma leitura, e nenhum `%s` muda de lugar. Cada uma deixa
     VERMELHO só `test_contagem_sob_filtro_bate_com_a_lista_pagina_a_pagina`:
       a. FILTRO — troque a linha `{launch_filter_sql}` da query 3 por
          `{launch_filter_sql.replace("AND (", "AND (true OR ", 1)}`:
          `assert (115, 5) == (60, 3)` (conta o mês inteiro, lista filtrada).
       b. DATA — troque a janela da query 3 por
          `AND criado_em >= %s - interval '1 year' AND criado_em < %s + interval '1 year'`:
          `assert (63, 3) == (60, 3)`. É por causa desta dimensão que o seed
          semeia mês passado; sem essas 3 linhas a injeção fica VERDE — medido.
       c. ISOLAMENTO (§0 da raiz) — troque `WHERE user_id = %s` da query 3 por
          `WHERE (user_id = %s OR 1=1)`: `assert (61, 3) == (60, 3)`. É a medição
          que justifica o vizinho que o caso semeia. A query 4 tem o espelho
          exato desta injeção, e ele também fica vermelho.

  3. ORDEM TOTAL da query 4. Tire o `, id ASC` do
     `ORDER BY criado_em DESC, id ASC` → o `assert not repetidos` de
     `test_contagem_sob_filtro_bate_com_a_lista_pagina_a_pagina` fica VERMELHO:
     o mesmo `id` volta em duas páginas e, como o número de linhas por página não
     muda, cada repetição é um lançamento que o usuário NUNCA vê.
     Esta injeção só discrimina porque o seed tem EMPATE de `criado_em` em volume
     e roda `ANALYZE launches`: com o plano `Sort Method: top-N heapsort` a ordem
     dos empatados muda com o `LIMIT+OFFSET` de cada página. Sem o `ANALYZE` o
     planner escolhe o índice, que é determinístico, e a injeção fica VERDE —
     medido, e é o motivo de o seed antigo (27+26 linhas, sem empate construído)
     não medir identidade nenhuma.

     ponytail: o teto é do CONTROLE, não do teste — e a diferença importa antes
     de alguém "estabilizar" um caso que já é estável.
     O TESTE é verde por garantia ESTRUTURAL: `id` é PK, logo `(criado_em, id)`
     é único e `ORDER BY criado_em DESC, id ASC` é ordem TOTAL — não existe
     plano que produza sobreposição entre páginas. Medido: 10 repetições mais
     cinco combinações de `PGOPTIONS` (`enable_indexscan=off`, `enable_sort=off`,
     `enable_seqscan=off` e pares), todas VERDES. Não há vermelho de CI a temer
     por escolha de planner.
     Quem depende do planner é só a INJEÇÃO ficar vermelha: ela exige que o
     `Sort Method: top-N heapsort` continue ganhando do index scan JÁ ORDENADO.
     Qualquer configuração de custo que inverta essa balança deixa a injeção
     VERDE sem nada ter quebrado — medido com a injeção aplicada: VERDE sob
     `enable_sort=off` e sob `random_page_cost=0.5` (plausível em Postgres
     gerenciado com NVMe, e o tipo de coisa que difere entre uma máquina e o
     CI); VERMELHA sob `random_page_cost=1.0` e `1.1`, `seq_page_cost=4`,
     `cpu_operator_cost=0.01`, `work_mem=64kB` e `effective_cache_size=8GB`.
     É ISSO que se confere quando a injeção aparecer verde: o plano, não o seed.
     Como conferir, já que a query 4 é f-string montada dentro de
     `get_financial_data` e não há `EXPLAIN` para o leitor rodar: com a injeção
     aplicada, rode este arquivo sob `PGOPTIONS='-c random_page_cost=1.0'`. Se
     ficar VERMELHA ali e verde no default, é o planner — não o conserto.
     O padrão de empate NÃO é o eixo: a injeção é vermelha de `i % 2` a `i % 7`,
     não só no `i % 3` que o caso usa. QUAIS ids se repetem não vai escrito aqui:
     o conjunto muda com a ordem dos testes e com o plano sem o dado mudar, e o
     `assert` para no primeiro filtro — o observável estável é ele ficar VERMELHO.
     Um controle que não dependesse do planner exigiria embaralhar a ordem
     FÍSICA das linhas contra a ordem dos `id`, que é bem mais máquina do que
     este caso justifica.

O que este arquivo NÃO cobre, de propósito (classes registradas no corpo do PR).
Esta é A lista, e é uma só (§0.7): a aba `all` com `credit_union_sql`, o `query=`
da busca livre, os ramos `investimento`/`interno` do filtro, a costura
servidor → navegador, mais duas que não são óbvias:

  - o `AND tipo NOT IN ('criar_caixinha', ...)` que as duas pernas de dentro
    compartilham. É dimensão de divergência REAL, igual às três com controle
    (tirar um pseudo-tipo da lista de UMA das queries desalinha COUNT e LISTA),
    e este grupo é cego a ela POR CONSTRUÇÃO: sob `filter_type="despesa"` ou
    `"receita"` o `NOT IN` não discrimina, porque o filtro já excluiu aqueles
    tipos. Medido nesta árvore com `criar_caixinha` fora do `NOT IN` da query 3:
    a família INTEIRA fica verde, e num usuário com 5 despesas mais 1
    `criar_caixinha` o card sai `total=6` para lista de 5 SEM filtro — e 5 para
    5 sob `filter_type="despesa"`, que é exatamente onde os casos daqui olham.
  - a TERCEIRA cópia dos aliases (é PR próprio, §0.7): o literal PRÓPRIO de
    `_dashboard_launch_filter_sql` fica fora de
    `test_o_sql_e_o_python_falam_dos_mesmos_aliases`, que compara só
    `_TIPO_ALIASES` com `TIPO_DESPESA_SQL`/`TIPO_RECEITA_SQL` — alias novo nas
    duas cópias comparadas deixa a família INTEIRA verde, medido.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import db
from utils_date import today_tz
import frontend.finance_bot_websocket_custom as dashboard

from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado, _hoje_as


def test_filtro_continua_achando_as_duas_formas(pro_user_id):
    """P3 — controle POSITIVO do caminho que RESTRINGE.

    O filtro (`_dashboard_launch_filter_sql`) roda no WHERE da perna de DENTRO,
    contra a coluna CRUA, e lê os dois pares. A canonização entrou no SELECT de
    FORA, então "Despesas" tem de continuar trazendo a linha 'saida' e
    "Receitas" a 'entrada' — se o filtro tivesse sido levado junto, um dos dois
    voltaria vazio.
    """
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")
    _grava_tipo_legado(pro_user_id, "entrada", 300, "salario")
    hoje = today_tz()

    def _filtrado(ft):
        return asyncio.run(dashboard.get_financial_data(
            pro_user_id, year=hoje.year, month=hoje.month, filter_type=ft,
        ))["recent_launches"]

    # Só o VALOR, de propósito: o tipo devolvido é o que os dois testes de
    # projeção do arquivo irmão medem. Aqui o observável é QUAIS linhas o WHERE
    # trouxe — é o que mantém este caso verde com e sem o conserto, que é o que
    # um controle positivo tem de fazer.
    assert [float(r["valor"]) for r in _filtrado("despesa")] == [100.0]
    assert [float(r["valor"]) for r in _filtrado("receita")] == [300.0]


def test_contagem_sob_filtro_bate_com_a_lista_pagina_a_pagina(pro_user_id):
    """P4 — o "N de M" do card "Lançamentos" e as linhas que ele numera.

    O `total` sai da query 3 (o COUNT) e as linhas da query 4: concordam
    enquanto as pernas de DENTRO interpolarem as mesmas cinco coisas (a
    enumeração e o que fica CEGO estão no cabeçalho). `renderLaunchesPagination`
    (frontend/dashboard.js:7840) imprime "Mostrando 1 – 25 de N" a partir do
    `total`, sem olhar a lista, e some quando `total_pages` é 1
    (dashboard.js:7841) — por isso o seed passa do `limit`.

    A asserção de IDENTIDADE (`repetidos`) é o que separa este caso de uma
    conferência de QUANTIDADE: contar certo e devolver a mesma linha em duas
    páginas é o bug que o usuário vê (some um lançamento, outro aparece três
    vezes). Molde emprestado de tests/test_admin_users_panel.py:513-515.
    """
    # `limite` é o `limit` que o front manda hoje (`LAUNCHES_LIMIT`,
    # frontend/dashboard.js:273); o teste passa o SEU e assere a relação, não um
    # literal — divergir do front não quebra o caso ENQUANTO o `limite` daqui
    # couber no grampo da produção, `limit = max(min(int(limit or 25), 100), 1)`.
    # Acima de 100 a relação vira mentira: com `limite=150` a produção pagina de
    # 100 e sai `assert 100 == 150` (medido). Não alcançável pelo front hoje.
    limite, despesas, receitas = 25, 60, 55
    # O seed é ACOPLADO à asserção, e o acoplamento fica explícito aqui em vez de
    # virar literal cravado lá embaixo: mais de uma página, e a ÚLTIMA parcial
    # nos dois filtros (10 e 5 linhas). O número de páginas é derivado.
    assert despesas > limite and receitas > limite
    assert despesas % limite and receitas % limite

    # Três horários por filtro: o EMPATE de `criado_em` é o que faz a ordem sem
    # `id ASC` variar de uma página para a outra (ver controle 3 do cabeçalho).
    for tipo, n in (("saida", despesas), ("entrada", receitas)):
        for i in range(n):
            _grava_tipo_legado(pro_user_id, tipo, 10, "mercado",
                               criado_em=_hoje_as(9 + i % 3))

    # Dimensão DATA: fora da janela do mês. O caso semeia um usuário Pro, cujo
    # `history_earliest_date` não corta nada, então `query_start` é o 1º do mês e
    # estas 3 linhas TÊM de ficar fora do `total` e da lista.
    mes_passado = _hoje_as(9).replace(day=1) - timedelta(days=1)
    for _ in range(3):
        _grava_tipo_legado(pro_user_id, "saida", 10, "mercado", criado_em=mes_passado)

    # §0: sem vizinho na base, um `total` que perdesse o `user_id = %s` daria o
    # número certo assim mesmo. `_auto_cleanup_orphan_users` (conftest) o apaga.
    vizinho = pro_user_id + 1
    db.ensure_user(vizinho)
    for tipo in ("saida", "entrada"):
        _grava_tipo_legado(vizinho, tipo, 99, "mercado")

    # Sem estatística o planner escolhe `idx_launches_user_time`, que ordena os
    # empatados sempre igual e esconde a falta do `id ASC` (controle 3).
    # Ele VAZA, e é o único `ANALYZE` da suíte: o `_cleanup_user` do conftest
    # apaga as linhas do seed DEPOIS, então `pg_statistic` segue descrevendo uma
    # `launches` que não existe mais, para todos os testes seguintes, até o
    # próximo autovacuum. Teto NÃO medido: quem ordena por `criado_em desc` SEM
    # desempate (`get_uncategorized_launches` em db/categories.py,
    # `list_user_categories` em db/budgets.py) pode trocar de linha empatada
    # quando o plano muda.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("ANALYZE launches")
        conn.commit()

    hoje = today_tz()
    for ft, esperado in (("despesa", despesas), ("receita", receitas)):
        paginas = -(-esperado // limite)
        vistos = []
        for page in range(1, paginas + 1):  # a última é PARCIAL nos dois filtros
            d = asyncio.run(dashboard.get_financial_data(
                pro_user_id, year=hoje.year, month=hoje.month,
                page=page, limit=limite, filter_type=ft,
            ))
            meta, linhas = d["launches_pagination"], d["recent_launches"]
            assert (meta["total"], meta["total_pages"]) == (esperado, paginas), (ft, page, meta)
            assert len(linhas) == min(limite, max(0, meta["total"] - (page - 1) * limite)), \
                (ft, page, meta, len(linhas))
            vistos += [r["id"] for r in linhas]

        repetidos = sorted({i for i in vistos if vistos.count(i) > 1})
        assert not repetidos and len(vistos) == esperado, (ft, repetidos, len(vistos))
