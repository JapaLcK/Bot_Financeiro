"""Estimativa do gasto variável (o "dia a dia") na previsão de saldo.

A fonte é a MESMA linha do "Para onde vai" (`db.lancamentos.listar` + `mes_detalhes._em_reais`).
Por categoria, a mediana de até 3 janelas de 30 dias que terminam ontem (hoje fica de fora: já está
no saldo); a soma das medianas é o gasto mensal, descontado dia a dia a partir de amanhã (`mensal/30`).
Só entra o que a previsão NÃO soma por outro caminho (nada contado duas vezes): fica fora parcela,
pagamento de boleto/conta, o legado do cobrador antigo, a cadeia recorrente do banco (que não é de
cartão) e o fixo manual pago por fora (nome parecido, ou mesmo valor no dia perto).
Chamado por `cashflow_snapshot.ler`, que é quem define o `hoje` (calendário do app, nunca UTC cru).
ponytail: transferência entre contas próprias sem marca de interna conta como gasto (igual ao
"Para onde vai"); conciliação pendente conta em dobro (igual ao mês).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from core.services.cashflow_contract import FONTE_VARIAVEL, Ocorrencia, centavos, dinheiro
from core.services.previsao_recorrencias import _perto
from db.connection import CAT_META_SQL, CAT_VAZIA_LABEL
from db.lancamentos import PAGA_CONTA_SQL, listar
from db.mes_detalhes import _em_reais
from db.open_finance import RECON_AMOUNT_TOL, merchant_contido
from utils_date import _tz

JANELA = 30
MAX_JANELAS = 3
PISO_DIAS = 7  # abaixo disso não há estimativa (decisão do dono, 2026-10-20)
FOLGA_FUTURA = 45  # a compra na fatura aberta tem `period_end` no futuro


def mediana_por_categoria(linhas, hoje: date, dias: int) -> tuple[dict, int] | None:
    """`({categoria: gasto mensal em centavos}, janelas completas)`, ou `None` com menos de `PISO_DIAS`.
    `linhas`: `(dia, categoria, valor)` já filtradas. Janela j (1..k) = `[hoje-30j, hoje-30(j-1)-1]`;
    sem janela completa (< 30 dias de histórico) o total dos dias que existem é levado a 30 dias."""
    dias = min(MAX_JANELAS * JANELA, dias)
    if dias < PISO_DIAS:
        return None
    k = min(MAX_JANELAS, dias // JANELA)
    por_cat = defaultdict(lambda: [Decimal(0)] * max(k, 1))
    for dia, cat, valor in linhas:
        atras = (hoje - dia).days  # 1 = ontem
        if 1 <= atras <= (JANELA * k or dias):
            por_cat[cat][(atras - 1) // JANELA if k else 0] += valor
    mensal = {c: centavos(median(v) if k else v[0] * JANELA / dias) for c, v in por_cat.items()}
    return {c: v for c, v in mensal.items() if v}, k


def ocorrencias(hoje: date, until: date, mensal: Decimal) -> list[Ocorrencia]:
    """Uma saída por dia, de amanhã até `until`, de `mensal/30` sem arredondar: 30 dias somam o mensal."""
    valor = mensal / JANELA
    if valor <= 0:
        return []
    return [Ocorrencia(FONTE_VARIAVEL, 'total', d.isoformat(), d, 'gasto_variavel', 'Gastos do dia a dia',
                       valor, 'saida', 'estimado', 'presumida', 'prevista', (), True)
            for d in (hoje + timedelta(days=i) for i in range(1, (until - hoje).days + 1))]


def repartir(est: dict, chaves, valor: Decimal) -> dict[str, Decimal]:
    """`{chave: parte de valor}`: `valor` dividido entre `chaves` (de `est`) proporcional à `despesa_mensal`, em
    centavos. A parte nunca passa da despesa da categoria: o resíduo vai para a 1ª, na ordem da estimativa, que
    ainda tem folga, e a soma fecha exata (`valor` <= soma das despesas). Categoria que ficaria com 0,00 fica fora.
    ponytail: o resíduo vai ao início da lista, não é espalhado; tem o teto de poucos centavos."""
    cats = [c for c in est['categorias'] if c['chave'] in chaves]
    desp = [int(c['despesa_mensal'] * 100) for c in cats]
    alvo = int(valor * 100)
    partes = [alvo * d // sum(desp) for d in desp]
    resto = alvo - sum(partes)
    for i, d in enumerate(desp):
        extra = min(resto, d - partes[i])
        partes[i] += extra
        resto -= extra
    return {c['chave']: Decimal(p) / 100 for c, p in zip(cats, partes) if p}


def cortar(est: dict, cortes: dict[str, int], valores: dict[str, Decimal] | None = None) -> tuple[list[dict], Decimal]:
    """`([{chave, nome, despesa_mensal, percentual, economia_mensal, valor_pedido}], economia total)` das categorias
    de `est` que estão em `cortes` (`{chave: percentual}`) ou em `valores` (`{chave: reais por mês}`, já validados
    contra a despesa), na ordem da estimativa. Em reais, a economia é o valor pedido e o percentual é o efetivo
    (arredondado, 0..100); em %, a economia é arredondada por categoria (`centavos`). A soma bate com o total."""
    valores = valores or {}
    linhas = []
    for c in est['categorias']:
        k = c['chave']
        if k in valores:
            eco = centavos(valores[k])  # 2 casas, como o resto do dinheiro do contrato
            pct = min(100, max(0, int((eco * 100 / c['despesa_mensal']).quantize(1, ROUND_HALF_UP))))
        elif k in cortes:
            pct, eco = cortes[k], centavos(c['despesa_mensal'] * cortes[k] / 100)
        else:
            continue
        linhas.append({**c, 'percentual': pct, 'economia_mensal': eco, 'valor_pedido': eco if k in valores else None})
    return linhas, sum((l['economia_mensal'] for l in linhas), Decimal(0))


def _inicio_do_historico(cur, uid: int) -> date | None:
    cur.execute("""select least(
        (select min(coalesce(posted_at, (criado_em at time zone %s)::date)) from launches where user_id = %s),
        (select min(purchased_at) from credit_transactions where user_id = %s)) as d""",
                (_tz().key, uid, uid))
    return cur.fetchone()['d']


def _ja_contados(cur, uid: int, ids_l: list, ids_c: list) -> tuple[set, set]:
    """Ids de `launches` (pagamento de conta, legado do cobrador) e de `credit_transactions` (legado)
    que a previsão já soma por outro caminho. Tudo por `user_id` do dono da linha."""
    l, c = set(), set()
    if ids_l:
        cur.execute(f"""select id from launches where user_id = %s and id = any(%s) and {PAGA_CONTA_SQL}
                        union select launch_id from recurring_charges where user_id = %s and launch_id = any(%s)""",
                    (uid, ids_l, uid, ids_l))
        l = {r['id'] for r in cur.fetchall()}
    if ids_c:
        cur.execute("select credit_tx_id as id from recurring_charges where user_id = %s and credit_tx_id = any(%s)",
                    (uid, ids_c))
        c = {r['id'] for r in cur.fetchall()}
    return l, c


def _fixo_pago_por_fora(fixos: list, r: dict) -> bool:
    """O manual ativo (não de cartão, não `once`) que parece esta linha: nome contido no outro, ou valor a
    `RECON_AMOUNT_TOL` e, se mensal/anual, dia perto."""
    for f in fixos:
        if merchant_contido(f['name'], r['descricao']) or merchant_contido(f['name'], r['mensagem']):
            return True
        v = dinheiro(f.get('amount'))
        if v is not None and abs(v - r['valor']) <= RECON_AMOUNT_TOL and (
                f.get('frequency') not in ('monthly', 'annual')
                or isinstance(f.get('due_day'), int) and _perto(r['dia'].day, f['due_day'])):
            return True
    return False


def estimar(cur, uid: int, hoje: date, recs: list, cadeias: list, contas_na_base: set,
            bancos_permitidos: bool) -> dict | None:
    """O objeto `estimativa_variavel` do contrato (dinheiro em Decimal), ou `None` sem histórico suficiente."""
    inicio = _inicio_do_historico(cur, uid)
    if inicio is None:
        return None
    dias = min(MAX_JANELAS * JANELA, (hoje - inicio).days)
    if dias < PISO_DIAS:
        return None
    k = min(MAX_JANELAS, dias // JANELA)
    ini = hoje - timedelta(days=JANELA * k or dias)
    # Janela larga: o `criado_em` é cortado no fuso da sessão do Postgres e a compra na fatura aberta tem
    # `period_end` no futuro. O corte de verdade é pelo `dia`, em Python.
    linhas, _ = listar(cur, uid, ini - timedelta(days=1), hoje + timedelta(days=FOLGA_FUTURA), limite=None)
    candidatas = [r for r in _em_reais(linhas, set())[0]
                  if r['tipo'] == 'saida' and r['valor'] > 0 and ini <= r['dia'] < hoje and (r['parcela_total'] or 0) <= 1
                  and (r['origem'] != 'banco' or bancos_permitidos and r['conta_id'] in contas_na_base)]
    l_pagos, c_pagos = _ja_contados(cur, uid, [r['id'] for r in candidatas if r['tabela'] == 'l'],
                                    [r['id'] for r in candidatas if r['tabela'] == 'c'])
    da_cadeia = set().union(*(c['launches'] for c in cadeias if c['direcao'] == 'saida' and not c['cartao']))
    fixos = [f for f in recs if f['is_active'] and f.get('payment_type') != 'credit_card' and f.get('frequency') != 'once']
    validas = [(r['dia'], r['categoria'] or CAT_VAZIA_LABEL, r['valor']) for r in candidatas
               if r['id'] not in (l_pagos if r['tabela'] == 'l' else c_pagos)
               and not (r['tabela'] == 'l' and r['id'] in da_cadeia) and not _fixo_pago_por_fora(fixos, r)]
    mensal, janelas = mediana_por_categoria(validas, hoje, dias)
    cur.execute(CAT_META_SQL, (uid,))  # só lê: nada de `ensure_user_categories_seeded`
    nomes = {r['cat']: r['name'] for r in cur.fetchall()}
    total = sum(mensal.values(), Decimal(0))
    if total <= 0:  # estimativa de R$ 0 = não há estimativa (o motivo e a premissa antigos valem)
        return None
    return {'despesa_mensal': total,
            'janela': {'inicio': ini.isoformat(), 'fim': (hoje - timedelta(days=1)).isoformat()},
            'dias_de_historico': dias, 'janelas_completas': janelas,
            'categorias': [{'chave': c, 'nome': nomes.get(c, c), 'despesa_mensal': v}
                           for c, v in sorted(mensal.items(), key=lambda kv: (-kv[1], kv[0]))]}
