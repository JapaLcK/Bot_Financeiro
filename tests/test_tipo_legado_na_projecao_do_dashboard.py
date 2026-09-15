"""PR 3 (#287): a PROJEÇÃO de `recent_launches` devolve a forma canônica.

Partido de `tests/test_tipo_legado_no_dashboard.py` (§0.5) — lá ficam os números
do mês e os helpers de seed; aqui, só o rótulo que a query 4 devolve para fora.

Query 4 de `get_financial_data` devolvia o `tipo` CRU para fora. Quem consome
`recent_launches` decide rótulo, cor, sinal e ícone com igualdade estrita:
home.html:944 imprime o cru ("Última atividade: saida"), :1094 não conta a
linha legada no onboarding, :1147 desenha a receita legada como DESPESA
(vermelho, sinal de menos, ícone de queda), e no dashboard.js o cru CAI como
rótulo pelo `||` de três fallbacks: `TYPE_LABELS[l.tipo] || l.tipo` em
`renderLaunches` (a linha do Histórico, que lê `lastData.recent_launches`),
`LAUNCH_TYPE_LABELS[l.tipo] || String(l.tipo)` em `_renderLaunchDetail`
("Tipo: saida") e `const prefix = ... : launch.tipo` em `openEditLaunchModal`
(resumo do editor). Predicado e nome de função, não offset — offset é o que
envelhece em silêncio (docs/controles_declarados.md). O conserto é
`TIPO_CANON_SQL AS tipo` na projeção de FORA — mesma decisão de
db/analytics.py:784-791.

NÃO fecha o modal de detalhe inteiro: ele tem um SEGUNDO alimentador
(`_catLaunchesRows`, de `list_launches_by_category`), que segue cru — issue
296, e o comentário da query 4 diz por que ficou fora.

Incidência: ZERO linhas legadas em 4.964 `launches` na produção, medido pelo
dono em 04/09/2026 com

    select count(*) filter (where tipo = 'saida')   as saida,
           count(*) filter (where tipo = 'entrada') as entrada,
           count(*)                                 as total_launches
      from launches;

REMEDIR antes de reusar este número: ele envelhece a cada import. É fechamento
PREVENTIVO de classe, não conserto de incêndio — nenhum número de usuário muda
hoje.

Controles do grupo. As mutações abaixo foram RODADAS, e cada uma diz quais casos
deste arquivo ficam VERMELHOS — se o resultado for outro, o conserto mudou de
lugar e o grupo parou de medir o que diz medir:

  1. NEGATIVO. Troque a projeção de FORA da query 4 por `tipo` cru
     (`SELECT id, tipo, valor, ...`, frontend/finance_bot_websocket_custom.py, a
     linha do `TIPO_CANON_SQL AS tipo` na query 4) → os dois `test_projecao_*`
     ficam VERMELHOS.
  2. POSITIVO do `ELSE`. Troque o `ELSE tipo` de `TIPO_CANON_SQL` por
     `ELSE 'despesa'` → `test_canonizacao_nao_toca_nos_outros_tipos` fica
     VERMELHO. `TIPO_CANON_SQL` mora em db/connection.py e é lido por vários
     read paths, então esta injeção também reprova FORA deste arquivo. Não
     enumeramos os irmãos de propósito: lista de irmãos envelhece em silêncio, e
     esta já envelheceu uma vez — a versão anterior dizia "só
     `test_canonizacao_nao_toca_nos_outros_tipos` cai" e eram dois
     (docs/controles_declarados.md).

O que NÃO discrimina, e já enganou uma leitura deste arquivo: injetar
`TIPO_CANON_SQL` na perna de DENTRO. O WHERE avalia a tabela base, não a
projeção da subquery — o arquivo inteiro passa. É no-op funcional, não controle.

O controle POSITIVO do caminho que RESTRINGE (o filtro continuar achando as duas
formas) mora em `tests/test_tipo_legado_no_filtro_do_dashboard.py`, junto com o
resto do que interpola `launch_filter_sql`.
"""
from __future__ import annotations

import db
from utils_date import today_tz

from tests.test_tipo_legado_no_dashboard import _dados, _grava_tipo_legado, _hoje_as


def test_projecao_nao_devolve_forma_legada_nenhuma(pro_user_id):
    """N1 — com as duas formas legadas na base, nenhuma sai pela projeção."""
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")
    _grava_tipo_legado(pro_user_id, "entrada", 300, "salario")

    tipos = [r["tipo"] for r in _dados(pro_user_id)["recent_launches"]]
    assert tipos, "as duas linhas legadas têm de aparecer na lista"
    assert not ({"saida", "entrada"} & set(tipos)), tipos


def test_projecao_colapsa_saida_em_despesa_sem_tocar_no_valor(pro_user_id):
    """N2 — a linha 'saida' 100 chega como 'despesa', com o valor intacto."""
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")

    linhas = _dados(pro_user_id)["recent_launches"]
    assert len(linhas) == 1, linhas
    assert linhas[0]["tipo"] == "despesa", linhas[0]
    assert float(linhas[0]["valor"]) == 100.0, linhas[0]


def test_canonizacao_nao_toca_nos_outros_tipos(pro_user_id):
    """P1 — controle POSITIVO, o que reprova canonizar DEMAIS.

    `TIPO_CANON_SQL` tem `ELSE tipo`: só os dois pares colapsam. Todo o resto
    (moderno, interno, investimento) sai IDÊNTICO ao gravado. `criar_caixinha`
    é o par oposto: a perna de DENTRO o exclui pela coluna crua, e ele tem de
    continuar fora — é o que prova que a canonização não vazou para o WHERE.
    """
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 80, "freela", None,
        categoria="rendimentos", criado_em=_hoje_as(11),
    )
    # `_grava_tipo_legado` é o inserter SQL genérico do arquivo irmão (§0.1): os
    # tipos internos entram por ele porque o que se mede aqui é a PROJEÇÃO, não o
    # caminho de escrita de caixinha/investimento.
    for tipo in ("deposito_caixinha", "aporte_investimento", "criar_caixinha"):
        _grava_tipo_legado(pro_user_id, tipo, 20, None, interno=True)

    card_id = db.create_card(pro_user_id, "Nubank", closing_day=31, due_day=10)
    db.add_credit_purchase(pro_user_id, card_id, 70, "mercado", "pão", today_tz())

    tipos = sorted(r["tipo"] for r in _dados(pro_user_id)["recent_launches"])
    assert tipos == ["aporte_investimento", "credito", "deposito_caixinha",
                     "despesa", "receita"], tipos


def test_lista_e_contagem_nao_mudam_de_tamanho(pro_user_id):
    """P2 — canonizar o RÓTULO não pode criar, sumir nem duplicar linha."""
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")
    _grava_tipo_legado(pro_user_id, "entrada", 300, "salario")

    d = _dados(pro_user_id)
    assert d["launches_pagination"]["total"] == 2, d["launches_pagination"]
    assert len(d["recent_launches"]) == 2, d["recent_launches"]
