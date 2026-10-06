"""Projeção condicional de caixa: motor Decimal único; dados desconhecidos ficam explícitos."""
from __future__ import annotations

import calendar
from decimal import Decimal
import math
from datetime import date, timedelta
from typing import Any
from core.services.cashflow_snapshot import (Ocorrencia, Motivo, Snapshot, carregar, centavos, dinheiro, legado, somar)


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _recurring_occurrence_dates(day: Any, freq: str, month: Any, start: date | None,
                                after: date, until: date) -> list[date]:
    """Datas de ocorrência de um recorrente em (after, until]. Mensal/anual caem
    em `day` (clampado ao fim do mês); único/diário/semanal ancoram em `start`
    (único = start; diário = todo dia desde start; semanal = start + 7k)."""
    if until <= after:
        return []
    if freq in ("once", "daily", "weekly"):
        if start is None:
            return []
        if freq == "once":
            return [start] if after < start <= until else []
        passo = 1 if freq == "daily" else 7
        first = max(start, after + timedelta(days=1))
        d = start + timedelta(days=passo * math.ceil((first - start).days / passo))
        dates = []
        while d <= until:
            dates.append(d)
            d += timedelta(days=passo)
        return dates
    try:
        day = int(day or 1)
    except (TypeError, ValueError):
        day = 1
    mnum = None
    if freq == "annual":
        try:
            mnum = int(month)
        except (TypeError, ValueError):
            return []
    dates: list[date] = []
    y, m = after.year, after.month
    while (y, m) <= (until.year, until.month):
        if not (freq == "annual" and mnum and m != mnum):
            dim = calendar.monthrange(y, m)[1]
            d = date(y, m, min(day, dim))
            if after < d <= until and (start is None or d >= start):
                dates.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return dates


def validar_extra(value):
    if value is None:
        return Decimal(0)
    amount = dinheiro(value)
    if (amount is None or not math.isfinite(float(amount))
            or amount != 0 and float(amount) == 0):
        raise ValueError("valor adicional deve ser finito e representável no contrato numérico")
    return amount


def _projection(today: date, sb: dict, events: list[Ocorrencia],
                target_date: date, extra_amount=Decimal(0)) -> dict:
    """Única soma financeira; valores completos até quantizar o resultado."""
    extra = validar_extra(extra_amount)
    values = {t: [] for t in ('receita', 'gasto_fixo', 'boleto', 'fatura_cartao')}
    for event in events:
        if event.data is not None and event.data <= target_date and event.incluida:
            values.setdefault(event.tipo, []).append(event.assinado)
    saldo = dinheiro(sb['saldo'])
    sums = {t: somar(vs) for t, vs in values.items()}
    projected = centavos(somar((saldo, *sums.values(), extra.copy_negate()))) if saldo is not None else None
    past = target_date < today
    if past:
        projected = None
    return {
        'today': today.isoformat(), 'target': target_date.isoformat(),
        'saldo_atual': centavos(saldo), 'balance_source': sb['balance_source'],
        'of_bank_count': sb['of_bank_count'], 'banks_excluded': sb['banks_excluded'],
        'receitas_previstas': centavos(sums['receita']),
        'gastos_fixos_previstos': centavos(sums['gasto_fixo'].copy_negate()),
        'boletos_ate': centavos(sums['boleto'].copy_negate()), 'n_boletos': len(values['boleto']),
        'faturas_cartao': centavos(sums['fatura_cartao'].copy_negate()), 'boleto_novo': centavos(extra),
        'projetado': projected, 'tranquilo': projected is not None and projected >= 0,
    }


def _risco(snapshot: Snapshot, target: date, extra=Decimal(0)) -> str:
    """Aplica direções ao mesmo cálculo: risco precisa sobreviver às dúvidas."""
    if snapshot.base['saldo'] is None or target < snapshot.hoje:
        return 'abster'
    if any(m.direcao_do_erro == 'ambos' or
           m.direcao_do_erro == 'so_piora' and m.efeito_quantificado is None
           for m in snapshot.motivos):
        return 'abster'
    base = dict(snapshot.base)
    # Só ajustes da BASE, não ocorrências (saídas duvidosas saem abaixo).
    base['saldo'] = somar((base['saldo'], *(max(m.efeito_quantificado, Decimal(0))
                         for m in snapshot.motivos
                         if m.codigo in ('conciliacao_a_conferir', 'declaracao_bancaria_a_conferir')
                         and m.efeito_quantificado is not None)))
    events = [e for e in snapshot.ocorrencias if not (e.direcao == 'saida'
              and (e.realizacao == 'a_conferir' or
                   any(m.direcao_do_erro in ('so_piora', 'ambos') for m in e.motivos)))]
    projected = _projection(snapshot.hoje, base, events, target, extra)['projetado']
    return 'risco' if projected is not None and projected < 0 else 'abster'


def project(user_id: int, target_date: date, extra_amount=0, *, percurso=False) -> dict:
    today = date.today()
    extra = validar_extra(extra_amount)
    snapshot = carregar(user_id, today, max(today, target_date))
    result = {**_projection(today, snapshot.base, snapshot.ocorrencias, target_date, extra),
              **snapshot.qualidade(), 'orientacao': _risco(snapshot, target_date, extra)}
    if target_date < today:
        result.update(estado='indisponivel', motivos=[*result['motivos'],
                      {'codigo': 'previsao_historica_indisponivel', 'direcao_do_erro': 'ambos'}])
    if percurso:
        from core.services.cashflow_forecast import _trajectory
        trajectory = _trajectory(today, snapshot.base, snapshot.ocorrencias,
                                 max(0, (target_date - today).days), Decimal(0))
        wd = trajectory['worst_day']
        initial = _projection(today, snapshot.base, snapshot.ocorrencias, today)['projetado']
        vals = [v for v in (initial, wd['saldo_projetado'] if wd else None,
                           result['projetado']) if v is not None]
        result['minimo_percurso'] = min(vals) if vals else None
    return legado(result)


__all__ = ['project']
