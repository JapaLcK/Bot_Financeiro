"""Recorrências do Open Finance na Previsão (Fase 1a): as cadeias mensais de
`of_recurring_payments`, na seleção da tela de Assinaturas, viram ocorrências
estimadas do motor (saídas). A receita vem SÓ do extrato (P7 a): `receitas_do_extrato`
acha o crédito que se repete todo mês, mas só como CANDIDATA: entra na conta a que o usuário
confirmou (`CONFIRMADA`, marca em `subscription_marks`); a recorrência de entrada da Pluggy não é lida.
Chamado por `cashflow_snapshot.ler`, só com os bancos na base.

O fixo manual continua contando. Quando ele e uma cadeia do banco parecem a mesma
coisa (casamento 1:1, nunca guloso), conta o manual e a do banco fica fora do
cálculo, visível, do 1º ciclo do manual em diante (antes dele, a do banco segue como
sem casamento). Sem casamento, a saída do banco entra (pessimista e rotulada).
A receita fixa manual só conta para quem não tem banco lido: com ele, o snapshot nem
a lê (a do extrato a substitui), então aqui não há casamento de entrada.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import median

from core.crypto import hash_pii
from core.services.assinaturas import DIAS_ATIVA, _interno, _merchant, cadeias, dia_da_cadeia
from core.services.cashflow import _recurring_occurrence_dates
from core.services.cashflow_contract import Ocorrencia, dinheiro
from core.services.cashflow_forecast import HORIZONS
from core.services.cashflow_snapshot import _data, _motivo
from db.of_recurring import ler_creditos, ler_frescor, ler_marcas, ler_ocorrencias
from db.open_finance import RECON_AMOUNT_TOL, merchant_similarity
from db.patrimonio import BANCO_VELHO
from utils_date import add_months, clamp_day

CENTAVO = Decimal('0.01')
MEIO_CICLO = 15  # cobrança a menos de 15 dias do dia esperado cobre aquele ciclo
TOLERANCIA_DIA = 5  # a da Pluggy (30±5 dias)
JANELA_EXTRATO = 180  # dias de extrato lidos para achar receita
MINIMO = 2  # créditos seguidos (30±5 dias, valores a ±20% da mediana) para valer como receita
MINIMO_PULANDO = 3  # cadeia que pula crédito avulso do mesmo pagador (decisão do dono, 2026-10-16)
TOLERANCIA_VALOR = Decimal('0.2')
CONFIRMADA, NEGADA = 'assinatura', 'ignorar'  # `subscription_marks.status` da receita do extrato (a chave é o id `extrato#…`): NÃO é assinatura
_RENDIMENTO = re.compile(r'\b(interests?|dividends?|dividendos?|rendimentos?)\b', re.I)  # palavra inteira: juro e dividendo mensais não são renda
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
    dia = m.get('due_day')
    valor = dinheiro(m.get('amount'))
    meio_ok = (m.get('payment_type') == 'credit_card') == c['cartao']
    return (meio_ok and m.get('is_active') and m.get('frequency') == 'monthly'
            and isinstance(dia, int) and _perto(dia, c['dia'])
            and (merchant_similarity(m.get('name'), c['nome'])
                 or merchant_similarity(m.get('name'), c['descricao'])
                 or valor is not None and abs(valor - c['valor']) <= RECON_AMOUNT_TOL))


def _inicio(m: dict) -> date | None:
    """1ª data em que o manual gera ocorrência no motor (o mesmo cálculo do `_datas`)."""
    start = _data(m.get('start_date'))
    if start is None:
        return None  # calendário desconhecido: o casamento cobre tudo, como antes
    return _recurring_occurrence_dates(m['due_day'], 'monthly', None, start,
                                       start - timedelta(days=1), start + timedelta(days=31))[0]


def _casar(manuais: list[dict], cs: list[dict]) -> dict[int, date | None]:
    """Cadeias casadas 1:1 com um manual → início do manual; e marca o manual casado."""
    pares = [(i, j) for i, m in enumerate(manuais) for j, c in enumerate(cs) if _candidato(m, c)]
    casadas = {}
    for i, j in pares:
        if sum(p[0] == i for p in pares) == 1 and sum(p[1] == j for p in pares) == 1:
            casadas[j] = _inicio(manuais[i])
            manuais[i]['_casado'] = True
    return casadas


def cadeias_do_banco(cur, user_id: int, marcas: dict | None = None) -> list[dict]:
    """As cadeias mensais de SAÍDA do banco, na seleção de Assinaturas (a mesma da Previsão e do
    custo da reserva): `ignorar` já aplicado, cartão incluído. `marcas`: as de `ler_marcas`, se já lidas."""
    marcas = ler_marcas(cur, user_id) if marcas is None else marcas
    cs = []
    for chave, lista in cadeias(ler_ocorrencias(cur, user_id)).items():
        for k, ls in enumerate(lista):
            ult = ls[-1]
            if marcas.get(chave, (None,))[0] == 'ignorar':
                continue
            cs.append({'id': f'{chave}#{k}', 'direcao': 'saida',
                       'nome': _merchant(ult).get('name') or ult['description'],
                       'descricao': ult['description'], 'valor': abs(ult['amount']),
                       'dia': dia_da_cadeia(ls), 'ultima': ult['transaction_date'],
                       'cartao': ult['account_type'] == 'CREDIT',
                       'conta': ult['account_id'],
                       'launches': {r['imported_launch_id'] for r in ls if r.get('imported_launch_id')}})
    return cs


def _doc(v: str | None) -> str:
    """Só documento de verdade serve de chave: 11 (CPF) ou 14 (CNPJ) dígitos e não todo zero. Curto,
    mascarado ("***.456.789-**") ou "0" viraria a mesma chave para pessoas diferentes."""
    d = re.sub(r'\D', '', v or '', flags=re.A)
    return d if len(d) in (11, 14) and d.strip('0') else ''


def _ciclo(a: dict, b: dict) -> bool:
    """`b` vem 30±`TOLERANCIA_DIA` dias depois de `a`."""
    return abs((b['transaction_date'] - a['transaction_date']).days - 30) <= TOLERANCIA_DIA


def _na_faixa(seq: list[dict]) -> bool:
    med = median(r['amount'] for r in seq)
    return not any(abs(r['amount'] - med) > med * TOLERANCIA_VALOR for r in seq)


def _pulando(ls: list[dict]):
    """Cadeias que pulam avulsos, da âncora mais recente para a mais antiga. Âncora: crédito até
    30+`TOLERANCIA_DIA` dias antes do último do documento e sem crédito 30±`TOLERANCIA_DIA` dias depois
    dela (esse seria o próximo mês com outro valor: aumento ou fim). Do elo atual, volta ao crédito
    30±`TOLERANCIA_DIA` dias antes, de valor a ±`TOLERANCIA_VALOR` da âncora, o mais perto de 30 dias
    (empate: o valor mais perto da âncora; depois, o mais antigo). Vale com `MINIMO_PULANDO` elos a ±`TOLERANCIA_VALOR`
    da mediana. Pagador frequente de valores variados fecha cadeia de 3 por acaso (simulação de 2026-10-16: 24% de 20
    Pix de R$ 50–300 em 6 meses): a defesa não é o valor, é a confirmação do usuário (candidata não entra na conta).
    ponytail: O(âncoras × elos × n), n ≤ 181 datas por documento (janela de 180 dias)."""
    for fim in range(len(ls) - 1, -1, -1):
        anc = ls[fim]
        if (ls[-1]['transaction_date'] - anc['transaction_date']).days > 30 + TOLERANCIA_DIA:
            break
        if any(_ciclo(anc, r) for r in ls[fim + 1:]):
            continue
        seq = [anc]
        while cands := [r for r in ls[:fim] if _ciclo(r, seq[0])
                        and abs(r['amount'] - anc['amount']) <= anc['amount'] * TOLERANCIA_VALOR]:
            seq.insert(0, min(cands, key=lambda r: (abs((seq[0]['transaction_date'] - r['transaction_date']).days - 30),
                                                    abs(r['amount'] - anc['amount']))))
        if len(seq) >= MINIMO_PULANDO and _na_faixa(seq):
            yield seq


def receitas_do_extrato(linhas: list[dict]) -> list[dict]:
    """Receitas mensais achadas nos créditos de `ler_creditos`, no formato de `cadeias_do_banco`.
    Chave = documento do pagador (`_doc`: 11 ou 14 dígitos): crédito sem ele, ou com documento
    inválido, fica fora (a descrição do Pix é genérica e uniria pessoas diferentes). Fica fora o
    movimento interno, o rendimento (as palavras interest(s)/dividend(s)/dividendo(s)/rendimento(s),
    por palavra inteira, na categoria OU na descrição) e o Pix para si (pagador = recebedor). Crédito idêntico (chave, data, valor)
    conta uma vez: pendente e efetivado, ou duas conexões do mesmo banco, dariam intervalo 0.
    Créditos do mesmo documento no mesmo dia SOMAM num só (salário + vale; senão o intervalo 0 mata a
    cadeia). Cada parcela somada leva o conjunto das contas das suas cópias idênticas; `grupos` são
    esses conjuntos no último crédito: a receita só entra se TODA parcela tem pelo menos UMA conta na
    base (parcelas diferentes somam, então todas precisam; cópias da mesma parcela, como a mesma conta
    em duas conexões, bastam por uma).
    Cadeia = de trás para frente enquanto o intervalo for 30±`TOLERANCIA_DIA`; vale com `MINIMO`
    créditos e todos a ±20% da mediana. Se ela não fecha, ou se uma que pula créditos avulsos do mesmo
    pagador (`_pulando`, também a ±20%) é mais longa, vale a mais longa, que exige `MINIMO_PULANDO`.
    Valor = o último. É só CANDIDATA: `anexar` conta apenas a que o usuário confirmou.
    O id é um hash com pepper: o documento nunca sai daqui.
    ponytail: com 2 créditos, avulso no meio ou depois ainda quebra; uma receita por pagador (a cadeia
    mais longa); aumento > 20% e receita sem documento do pagador não são detectados; quinzenal de valor igual com 3+
    meses vira receita MENSAL (um dos dois créditos do mês; o valor previsto subestima a renda); o avulso do mesmo
    pagador 25–35 dias depois do último elo apaga a receita até o próximo crédito (a regra antiga também); pendente em D × efetivado em D+1 quebra a cadeia; o
    mesmo documento com formato diferente entre meses (zeros à esquerda) vira chaves distintas; cashback
    com documento pode entrar."""
    por_doc = defaultdict(lambda: defaultdict(dict))
    for r in linhas:
        doc, eu = _doc(r['pagador_doc']), _doc(r['recebedor_doc'])
        if doc and doc != eu and not _interno(r) and not _RENDIMENTO.search(f"{r['category'] or ''} {r['description']}"):
            ja = por_doc[doc][r['transaction_date']].get(r['amount'])
            por_doc[doc][r['transaction_date']][r['amount']] = {
                **r, 'grupo': {r['account_id']} | (ja['grupo'] if ja else set())}
    cs = []
    for doc, por_data in por_doc.items():
        ls = [{**max(rs.values(), key=lambda r: r['amount']), 'amount': sum(rs),
               'grupos': [r['grupo'] for r in rs.values()]} for _, rs in sorted(por_data.items())]
        i = len(ls) - 1
        while i > 0 and _ciclo(ls[i - 1], ls[i]):
            i -= 1
        consecutiva = [ls[i:]] if len(ls) - i >= MINIMO and _na_faixa(ls[i:]) else []
        seq = max(consecutiva + list(_pulando(ls)), key=len, default=None)  # empate: a consecutiva, depois a âncora mais recente
        if seq is None:
            continue
        ult = seq[-1]
        cs.append({'id': 'extrato#' + hash_pii(doc)[:16], 'direcao': 'entrada',
                   'nome': ult['pagador_nome'] or ult['description'], 'descricao': ult['description'],
                   'valor': ult['amount'], 'dia': dia_da_cadeia(seq), 'ultima': ult['transaction_date'],
                   'cartao': False, 'grupos': ult['grupos']})
    return cs


def custo_mensal(cur, user_id: int, hoje: date) -> dict:
    """Custo mensal das saídas recorrentes do banco, cartão incluído, para a reserva de
    emergência: a soma do último valor de cada cadeia ATIVA (última cobrança há até
    `DIAS_ATIVA` dias). `valor` é `None` (nunca 0) sem banco vivo ou com a soma em 0 (sem cadeia
    ativa, ou só de cobrança 0) ou com valor não finito no espelho (`recorrencia_valor_invalido`): o
    `custo_desconhecido` da reserva, que já trata 0 assim.
    ponytail: duas conexões do mesmo banco dobram a cadeia (herdado da Previsão)."""
    conexoes = ler_frescor(cur, user_id)
    if not conexoes:
        return {'valor': None, 'recorrencias': 0, 'motivos': ['sem_banco_conectado']}
    velho = datetime.now(timezone.utc) - BANCO_VELHO
    motivos = []
    try:
        ativas = [c['valor'] for c in cadeias_do_banco(cur, user_id)
                  if (hoje - c['ultima']).days <= DIAS_ATIVA]
        total, invalido = sum(ativas, Decimal(0)).quantize(CENTAVO), False
    except InvalidOperation:  # `amount` do espelho é `numeric` e guarda NaN/±Infinity (ou estoura a precisão)
        ativas, total, invalido = [], Decimal(0), True  # custo desconhecido, nunca uma soma menor com cara de exata
    if invalido:
        motivos.append('recorrencia_valor_invalido')
    elif not total:
        motivos.append('sem_recorrencias')
    if any(c['recurring_fetched_at'] is None or c['recurring_fetched_at'] < velho for c in conexoes):
        motivos.append('recorrencias_banco_nao_lidas')
    return {'valor': total or None, 'recorrencias': len(ativas) if total else 0, 'motivos': motivos}


def _interrompida(c, hoje) -> bool:
    return (hoje - c['ultima']).days > DIAS_ATIVA


def _fora_da_base(c, contas_na_base: set) -> bool:
    """O saldo de alguma parcela não está na base: a conta ficaria pela metade."""
    return not c['cartao'] and not all(g & contas_na_base for g in c.get('grupos') or [{c['conta']}])


def _datas_banco(c, hoje, until) -> tuple[list[date], bool, bool]:
    """As datas que `anexar` gera para a cadeia `c` até `until`, e se ela está interrompida/atrasada."""
    esperada = _esperada(c['ultima'], c['dia'])
    interrompida = _interrompida(c, hoje)
    atrasada = not interrompida and esperada < hoje
    return _recurring_occurrence_dates(c['dia'], 'monthly', None, esperada,
                                       (esperada if atrasada else hoje) - timedelta(days=1), until), interrompida, atrasada


def _sim_muda_a_conta(r, hoje, contas_na_base) -> bool:
    """Receita confirmada teria pelo menos uma ocorrência INCLUÍDA até o menor horizonte do produto
    (`HORIZONS[0]`, o único do Plus): as mesmas datas e os mesmos cortes do laço de `anexar`."""
    datas, interrompida, atrasada = _datas_banco(r, hoje, hoje + timedelta(days=HORIZONS[0]))
    return not (interrompida or _fora_da_base(r, contas_na_base)) and any(not (atrasada and d < hoje) for d in datas)


def anexar(cur, s, user_id: int, until: date, recs: list, instancias: list, contas_na_base: set) -> list:
    """`contas_na_base`: ids (`open_finance_accounts.id`) das contas cujo saldo está na base.
    Devolve as cadeias lidas (a estimativa do dia a dia tira os lançamentos delas)."""
    hoje = s.hoje
    conexoes = ler_frescor(cur, user_id)
    if not conexoes:
        return []
    s.premissas.append(PREMISSA)
    velho = s.calculado_em - BANCO_VELHO
    if any(c['recurring_fetched_at'] is None or c['recurring_fetched_at'] < velho for c in conexoes):
        _motivo(s, 'recorrencias_banco_nao_lidas')
    for c in conexoes:  # a previsão em cache vence quando a lista vira velha
        if c['recurring_fetched_at'] is not None and c['recurring_fetched_at'] >= velho:
            s.valido_ate = min(s.valido_ate, c['recurring_fetched_at'] + BANCO_VELHO)

    marcas = ler_marcas(cur, user_id)
    receitas = receitas_do_extrato(ler_creditos(cur, user_id, hoje - timedelta(days=JANELA_EXTRATO)))
    status = {r['id']: {CONFIRMADA: 'confirmada', NEGADA: 'negada'}.get(marcas.get(r['id'], (None,))[0], 'pendente')
              for r in receitas}
    # Pendente só se o Sim incluiria uma ocorrência até 30 dias (`_sim_muda_a_conta`); confirmada/negada ficam, para desfazer.
    s.receitas_do_banco = sorted(
        ({'chave': r['id'], 'nome': r['nome'], 'valor': r['valor'], 'dia': r['dia'], 'status': status[r['id']]}
         for r in receitas if status[r['id']] != 'pendente'
         or _sim_muda_a_conta(r, hoje, contas_na_base)), key=lambda r: (r['status'] != 'pendente', -r['valor'], r['chave']))
    # Candidata fica fora da conta até o Sim (decisão do dono, 2026-10-16): a defesa contra receita falsa é a confirmação.
    cs = cadeias_do_banco(cur, user_id, marcas) + [r for r in receitas if status[r['id']] == 'confirmada']

    fixos = [dict(r) for r in recs if r['is_active']]
    idx = [j for j, c in enumerate(cs) if c['direcao'] == 'saida']
    casadas = {idx[j]: ini for j, ini in _casar(fixos, [cs[j] for j in idx]).items()}
    sobra = any(not m.get('_casado') for m in fixos) or any(
        b['status'] == 'pending' and (_data(b['due_date']) is None or _data(b['due_date']) <= until)
        for b in instancias)

    for j, c in enumerate(cs):
        saida = c['direcao'] == 'saida'
        datas, interrompida, atrasada = _datas_banco(c, hoje, until)
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
            if _fora_da_base(c, contas_na_base):
                incluida = False
                motivos.append(_motivo(s, 'recorrencia_banco_conta_fora_da_base',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            if interrompida:
                incluida = False
                motivos.append(_motivo(s, 'recorrencia_banco_interrompida',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            # Antes do 1º ciclo do manual (meio ciclo antes do início dele), o banco conta sozinho.
            if j in casadas and (casadas[j] is None or (casadas[j] - d).days < MEIO_CICLO):
                incluida = False
                motivos.append(_motivo(s, 'recorrencia_banco_igual_a_fixo_manual',
                                       'so_melhora' if saida else 'so_piora', c['id']))
            elif saida and sobra:
                motivos.append(_motivo(s, 'recorrencia_banco_pode_repetir_manual', 'so_piora', c['id']))
            s.ocorrencias.append(Ocorrencia(
                'recorrencia_banco', c['id'], d.isoformat(), d, 'gasto_fixo' if saida else 'receita',
                c['nome'], c['valor'], c['direcao'], 'estimado', 'presumida',
                'a_conferir' if d <= hoje else 'prevista', tuple(motivos), incluida))
    return cs
