"""O SEGUNDO alimentador do modal de detalhe: a lista de UMA categoria (#296).

O irmão `test_tipo_legado_no_dashboard.py` fechou a projeção de
`recent_launches` (query 4 de `get_financial_data`, PR #299). O modal de
detalhe do dashboard tem DOIS alimentadores, e o outro seguia cru:
`_catLaunchesRows` (frontend/dashboard.js) vem de `list_launches_by_category`
(db/accounts.py), que projetava `select tipo, ...` direto da coluna.

Caminho do usuário: dashboard → barra de categoria → clique na linha →
`_renderLaunchDetail` (dashboard.js) escrevia **"Tipo: saida"**, e
`openEditLaunchModal` abria o editor com o mesmo rótulo legado.

A MESMA query alimenta o texto do WhatsApp (`_listar_categoria`,
core/handlers/launches.py) — foi por isso que o #299 deixou esta fora. E lá a
mensagem se CONTRADIZIA: a linha saía `#12 • saida • R$ 100,00` enquanto o
rodapé da mesma resposta já somava aquele valor em `💸 Gastos: R$ 100,00`,
porque o `resumo` sempre usou `TIPO_DESPESA_SQL`. Com o conserto a linha
concorda com o próprio total.

Conserto: `{TIPO_CANON_SQL} as tipo` na projeção de FORA (a SELECT list dos
window aggregates), UMA linha de produção. Ela fecha as três superfícies —
modal, editor e WhatsApp — sem tocar em `core/handlers/launches.py`,
`frontend/dashboard.js` nem `frontend/routes/categories.py`.

Incidência: ZERO linhas legadas na produção (medição do dono em 04/09/2026,
registrada no cabeçalho do irmão do dashboard — REMEDIR antes de reusar). É
fechamento PREVENTIVO de classe: nenhum número de usuário muda hoje, só o
rótulo de uma linha que não existe na base de hoje.

Controles do grupo. As mutações abaixo foram RODADAS, e cada uma diz quais
casos ficam vermelhos — se o resultado for outro, o conserto mudou de lugar e o
grupo parou de medir o que diz medir:

  1. NEGATIVO. Volte a projeção de FORA para `tipo` cru (`select tipo, valor,
     categoria, ...`, db/accounts.py) → vermelhos:
     `test_projecao_da_categoria_nao_devolve_forma_legada`,
     `test_linha_legada_no_whatsapp_imprime_a_forma_canonica` e
     `test_filtro_por_tipo_continua_achando_a_legada` (este último também
     assere o RÓTULO da linha filtrada, não só que ela veio).
  2. POSITIVO do `ELSE`. Troque o `ELSE tipo` de `TIPO_CANON_SQL`
     (db/connection.py) por `ELSE 'despesa'` → vermelho: SÓ
     `test_ELSE_preserva_tipo_interno`. Sem este caso o conserto poderia
     passar a chamar pagamento de fatura de "despesa", que seria a regressão
     real: `LAUNCH_TYPE_LABELS` (dashboard.js) rotula 'pagamento_fatura' como
     "pgto. fatura", e o WhatsApp imprime o tipo cru na linha.
  3. POSITIVO dos agregados. Troque o `{TIPO_DESPESA_SQL}` do `tot_despesa`
     por `tipo = 'despesa'` → vermelhos, e o que interessa é o número:
     `test_resumo_e_contagem_nao_mudam_com_o_canon` devolve
     `{'n_total': 2, 'despesa': 0.0, 'receita': 300.0}` (medido). ZERO, não
     100 — é a prova, contra Postgres de verdade, de que o agregado da mesma
     SELECT list continua lendo `agg.tipo` CRU ('saida') e nunca o alias de
     saída recém-criado. Se o alias vazasse para lá, este literal casaria a
     linha canonizada e daria 100.0. Os outros dois vermelhos são os que
     também leem `resumo["despesa"]`/"💸 Gastos".
  4. POSITIVO do filtro. Tire `"saida"` de `_TIPO_ALIASES["despesa"]`
     (db/accounts.py) → vermelho: SÓ
     `test_filtro_por_tipo_continua_achando_a_legada`. O filtro roda na perna
     de DENTRO, contra a coluna base, e tem de continuar achando a forma
     legada depois da canonização.

O que este grupo NÃO alcança: `tests/test_tipo_legado_na_cauda.py` tem o
fixture `fonte_ampliada`, que faz `monkeypatch.setattr(CONN, "TIPO_CANON_SQL",
...)` para pegar DERIVA (literal copiado no lugar da fonte única). Ele não
cobre o sítio novo — `db/accounts.py` importa o nome POR VALOR na carga, então
o módulo guarda a sua própria cópia. É lacuna PRÉ-EXISTENTE, igual para os dois
sítios de `TIPO_CANON_SQL` que já moravam neste arquivo; alargar o fixture
seria escopo de outro PR (§0.2/§0.3).

Rodar o grupo:
  pytest tests/test_tipo_legado_na_categoria.py -q
"""
from __future__ import annotations

import db

# §0.1: o INSERT da linha legada, o horário e o helper de CONVERSA já existem
# nos irmãos — importa, não recria. `uid_wa` é o usuário de id curto que o
# caminho do WhatsApp exige (fixture, usada por nome nos testes abaixo).
from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado, _hoje_as
from tests.test_tipo_legado_na_cauda import _diga, uid_wa  # noqa: F401


def _base_legada(uid) -> None:
    """As duas formas legadas na MESMA categoria: 'saida' 100 e 'entrada' 300.

    Uma categoria só porque é assim que o usuário abre a lista — clicando na
    barra do dashboard ou pedindo "liste os lancamentos em mercado". As duas
    formas juntas cobrem as duas pernas de `TIPO_CANON_SQL` de uma vez.
    """
    _grava_tipo_legado(uid, "saida", 100, "mercado", criado_em=_hoje_as(10))
    _grava_tipo_legado(uid, "entrada", 300, "mercado", criado_em=_hoje_as(9))


# ── NEGATIVOS: hoje vermelhos, verdes com o conserto ────────────────────────

def test_projecao_da_categoria_nao_devolve_forma_legada(uid_wa):
    """A porta do DASHBOARD. `_catLaunchesRows` repassa estas rows ao modal, e
    `_renderLaunchDetail` imprime o `tipo` como rótulo: com o cru, "Tipo: saida".

    `len(rows) == 2` junto no mesmo caso porque "nenhuma forma legada" passa
    trivialmente numa lista VAZIA — sem a contagem, o teste ficaria verde se a
    canonização tivesse vazado para o WHERE e sumido com as duas linhas.
    """
    _base_legada(uid_wa)

    rows, _ = db.list_launches_by_category(uid_wa, "mercado")
    tipos = [r["tipo"] for r in rows]
    assert len(rows) == 2, rows
    assert not ({"saida", "entrada"} & set(tipos)), tipos
    assert set(tipos) == {"despesa", "receita"}, tipos


def test_linha_legada_no_whatsapp_imprime_a_forma_canonica(uid_wa):
    """A porta do WHATSAPP, pela CONVERSA (`handle_incoming`, não o handler).

    A MESMA query monta `#N • {tipo} • {valor}` em `_listar_categoria`. Antes,
    a linha dizia "saida" e o rodapé da mesma resposta já a somava em
    "💸 Gastos" — a mensagem se contradizia sozinha.
    """
    _base_legada(uid_wa)

    resposta = _diga(uid_wa, "liste os lancamentos em mercado")
    assert "• saida •" not in resposta, resposta
    assert "• entrada •" not in resposta, resposta
    assert "• despesa •" in resposta, resposta
    assert "• receita •" in resposta, resposta
    # a linha concorda com o próprio rodapé, que nunca mudou
    assert "💸 Gastos: R$ 100,00" in resposta, resposta
    assert "💰 Receitas: R$ 300,00" in resposta, resposta


# ── POSITIVOS: verdes hoje E depois ─────────────────────────────────────────

def test_ELSE_preserva_tipo_interno(uid_wa):
    """O que reprova canonizar DEMAIS, pelas DUAS portas.

    `TIPO_CANON_SQL` tem `ELSE tipo`: só os dois pares colapsam. Um
    `tipo='pagamento_fatura'` tem de sair IDÊNTICO — `LAUNCH_TYPE_LABELS`
    (dashboard.js) o rotula como "pgto. fatura" e o WhatsApp imprime o tipo cru
    na linha.

    Controle NEGATIVO deste caso: `ELSE tipo` → `ELSE 'despesa'`. Só ele cai.
    """
    _grava_tipo_legado(uid_wa, "pagamento_fatura", 88, "mercado",
                       criado_em=_hoje_as(10))

    # WhatsApp ANTES das rows: sob a mutação `ELSE 'despesa'` as duas portas
    # quebram, e a de rows abortaria o caso antes desta linha rodar.
    resposta = _diga(uid_wa, "liste os lancamentos em mercado")
    assert "• pagamento_fatura •" in resposta, resposta

    rows, _ = db.list_launches_by_category(uid_wa, "mercado")
    assert [r["tipo"] for r in rows] == ["pagamento_fatura"], rows


def test_resumo_e_contagem_nao_mudam_com_o_canon(uid_wa):
    """Os window aggregates da MESMA SELECT list não enxergam o alias novo.

    `tot_despesa`/`tot_receita` usam `TIPO_DESPESA_SQL`/`TIPO_RECEITA_SQL` na
    mesma SELECT list onde agora mora `{TIPO_CANON_SQL} as tipo`. O Postgres
    não resolve alias de coluna de saída dentro de expressões da própria SELECT
    list (só em `GROUP BY`/`ORDER BY`), então o `tipo` deles continua sendo o
    de `agg`, CRU — e os números do rodapé não se mexem.

    Controle NEGATIVO deste caso: `{TIPO_DESPESA_SQL}` → `tipo = 'despesa'`,
    que devolve `despesa == 0.0`. Discrimina de verdade.
    """
    _base_legada(uid_wa)

    _, resumo = db.list_launches_by_category(uid_wa, "mercado")
    assert resumo["n_total"] == 2, resumo
    assert resumo["despesa"] == 100.0, resumo
    assert resumo["receita"] == 300.0, resumo


def test_filtro_por_tipo_continua_achando_a_legada(uid_wa):
    """O FILTRO roda na perna de DENTRO, contra a coluna base.

    Canonizar a projeção de fora não pode ter mexido nele: pedir
    `tipo="despesa"` tem de continuar devolvendo a linha gravada como 'saida'
    — invisível E fora do total é a pior classe de erro num caminho de
    dinheiro, e é o que `_TIPO_ALIASES` existe para evitar.

    Controle NEGATIVO deste caso: tire `"saida"` de
    `_TIPO_ALIASES["despesa"]`. Só ele cai.
    """
    _base_legada(uid_wa)

    rows, resumo = db.list_launches_by_category(uid_wa, "mercado", tipo="despesa")
    assert len(rows) == 1, rows
    assert rows[0]["tipo"] == "despesa", rows[0]
    assert float(rows[0]["valor"]) == 100.0, rows[0]
    assert resumo["despesa"] == 100.0, resumo
