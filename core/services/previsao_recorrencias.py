"""Recorrências do Open Finance na Previsão (Fase 1a): as cadeias mensais de
`of_recurring_payments`, na seleção da tela de Assinaturas, viram ocorrências
estimadas do motor. Chamado por `cashflow_snapshot.ler`, só com os bancos na base.

O fixo e a receita manuais continuam contando. Quando um deles e uma cadeia do
banco parecem a mesma coisa (casamento 1:1, nunca guloso), conta o manual e a do
banco fica fora do cálculo, visível. Sem casamento, a saída do banco entra
(pessimista e rotulada) e a entrada fica fora (receita em dobro seria otimista).
"""
from __future__ import annotations

from datetime import date, timedelta

from core.services.assinaturas import DIAS_ATIVA, _merchant, cadeias, dia_da_cadeia
from core.services.cashflow import _recurring_occurrence_dates
from core.services.cashflow_contract import Ocorrencia, dinheiro
from core.services.cashflow_snapshot import _data, _motivo
from db.of_recurring import ler_frescor, ler_marcas, ler_ocorrencias
from db.open_finance import RECON_AMOUNT_TOL, merchant_similarity
from db.patrimonio import BANCO_VELHO
from utils_date import add_months, clamp_day

MEIO_CICLO = 15  # cobrança a menos de 15 dias do dia esperado cobre aquele ciclo
TOLERANCIA_DIA = 5  # a da Pluggy (30±5 dias)
PREMISSA = ('Do banco, só entram cobranças e receitas que se repetem todo mês; '
            'contas anuais e boletos ainda não pagos não aparecem.')


def _esperada(ultima: date, dia: int) -> date:
    y, m = ultima.year, ultima.month
    while True:
        d = date(y, m, clamp_day(y, m, dia))
        if (d - ultima).days >= MEIO_CICLO:
            return d
        y, m = add_months(y, m, 1)


def _perto(a: int, b: int) -> bool:
    dist = abs(a - b)
    return min(dist, 31 - dist) <= TOLERANCIA_DIA


def _candidato(m: dict, c: dict) -> bool:
    entrada = c['direcao'] == 'entrada'
    dia = m.get('pay_day' if entrada else 'due_day')
    valor = dinheiro(m.get('amount'))
    meio_ok = entrada or (m.get('payment_type') == 'credit_card') == c['cartao']
    return (meio_ok and m.get('is_active') and m.get('frequency') == 'monthly'
            and isinstance(dia, int) and _perto(dia, c['dia'])
            and (merchant_similarity(m.get('name'), c['nome'])
                 or merchant_similarity(m.get('name'), c['descricao'])
                 or valor is not None and abs(valor - c['valor']) <= RECON_AMOUNT_TOL))


def _casar(manuais: list[dict], cs: list[dict]) -> set[int]:
    """Índices das cadeias casadas 1:1 com um manual; e marca o manual casado."""
    pares = [(i, j) for i, m in enumerate(manuais) for j, c in enumerate(cs) if _candidato(m, c)]
    casadas = set()
    for i, j in pares:
        if sum(p[0] == i for p in pares) == 1 and sum(p[1] == j for p in pares) == 1:
            casadas.add(j)
            manuais[i]['_casado'] = True
    return casadas


def anexar(cur, s, user_id: int, until: date, recs: list, receitas: list, instancias: list,
           contas_na_base: set) -> None:
    """`contas_na_base`: ids (`open_finance_accounts.id`) das contas cujo saldo está na base."""
    hoje = s.hoje
    conexoes = ler_frescor(cur, user_id)
    if not conexoes:
        return
    s.premissas.append(PREMISSA)
    velho = s.calculado_em - BANCO_VELHO
    if any(c['recurring_fetched_at'] is None or c['recurring_fetched_at'] < velho for c in conexoes):
        _motivo(s, 'recorrencias_banco_nao_lidas')
    for c in conexoes:  # a previsão em cache vence quando a lista vira velha
        if c['recurring_fetched_at'] is not None and c['recurring_fetched_at'] >= velho:
            s.valido_ate = min(s.valido_ate, c['recurring_fetched_at'] + BANCO_VELHO)

    linhas = ler_ocorrencias(cur, user_id, receitas=True)
    # Positivo no cartão é pagamento de fatura ou estorno, não receita.
    no_cartao = {r['rp_id'] for r in linhas if r['average_amount'] > 0 and r['account_type'] == 'CREDIT'}
    marcas = ler_marcas(cur, user_id)
    cs = []
    for chave, lista in cadeias([r for r in linhas if r['rp_id'] not in no_cartao]).items():
        for k, ls in enumerate(lista):
            ult = ls[-1]
            entrada = ult['average_amount'] > 0
            # A marca é por chave, sem direção, e só a tela de despesas marca.
            if not entrada and marcas.get(chave, (None,))[0] == 'ignorar':
                continue
            cs.append({'id': f'{chave}#{k}', 'direcao': 'entrada' if entrada else 'saida',
                       'nome': _merchant(ult).get('name') or ult['description'],
                       'descricao': ult['description'], 'valor': abs(ult['amount']),
                       'dia': dia_da_cadeia(ls), 'ultima': ult['transaction_date'],
                       'cartao': not entrada and ult['account_type'] == 'CREDIT',
                       'conta': ult['account_id']})

    fixos = [dict(r) for r in recs if r['is_active']]
    rendas = [dict(r) for r in receitas if r['is_active']]
    casadas = set()
    for direcao, manuais in (('saida', fixos), ('entrada', rendas)):
        idx = [j for j, c in enumerate(cs) if c['direcao'] == direcao]
        casadas |= {idx[j] for j in _casar(manuais, [cs[j] for j in idx])}
    sobra = {'saida': any(not m.get('_casado') for m in fixos) or any(
                 b['status'] == 'pending' and (_data(b['due_date']) is None or _data(b['due_date']) <= until)
                 for b in instancias),
             'entrada': any(not m.get('_casado') for m in rendas)}

    for j, c in enumerate(cs):
        saida = c['direcao'] == 'saida'
        esperada = _esperada(c['ultima'], c['dia'])
        interrompida = (hoje - c['ultima']).days > DIAS_ATIVA
        atrasada = not interrompida and esperada < hoje
        datas = _recurring_occurrence_dates(c['dia'], 'monthly', None, esperada,
                                            (esperada if atrasada else hoje) - timedelta(days=1), until)
        for d in datas:
            motivos = [_motivo(s, 'recorrencia_banco_estimada', 'ambos', c['id'])]
            incluida = True
            if not saida:
                motivos.append(_motivo(s, 'receita_nao_garantida', 'so_melhora', c['id'], efeito=c['valor']))
            if c['cartao']:
                incluida = False
                motivos.append(_motivo(s, 'incorporacao_cartao_nao_comprovada', 'so_melhora', c['id']))
            if atrasada and d < hoje:
                incluida = incluida and saida
                motivos.append(_motivo(s, 'recorrencia_banco_atrasada', 'so_piora', c['id']))
            if not c['cartao'] and c['conta'] not in contas_na_base:
                incluida = False  # o saldo dessa conta não está na base: seria conta pela metade
                motivos.append(_motivo(s, 'recorrencia_banco_conta_fora_da_base',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            if interrompida:
                incluida = False
                motivos.append(_motivo(s, 'recorrencia_banco_interrompida',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            if j in casadas:
                incluida = False
                motivos.append(_motivo(s, 'recorrencia_banco_igual_a_fixo_manual',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            elif sobra[c['direcao']]:
                incluida = incluida and saida
                motivos.append(_motivo(s, 'recorrencia_banco_pode_repetir_manual', 'so_piora', c['id']))
            s.ocorrencias.append(Ocorrencia(
                'recorrencia_banco', c['id'], d.isoformat(), d, 'gasto_fixo' if saida else 'receita',
                c['nome'], c['valor'], c['direcao'], 'estimado', 'presumida',
                'a_conferir' if d <= hoje else 'prevista', tuple(motivos), incluida))
