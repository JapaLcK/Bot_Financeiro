"""
core/services/cashflow_forecast.py — previsão de saldo do plano Pro: horizontes
30/60/90 dias e trajetória diária com o pior dia.

Saldo e eventos vêm de `core/services/cashflow.py`, lidos uma vez por resposta.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date, timedelta
from typing import Any

from core.services import cashflow
from core.services.cashflow_snapshot import carregar, centavos, dinheiro, legado, somar, Ocorrencia

HORIZONS = (30, 60, 90)


def _horizons(today: date, sb: dict[str, Any], events: list[Ocorrencia],
              horizons: tuple[int, ...]) -> dict[str, Any]:
    hz = {str(n): cashflow._projection(today, sb, events, today + timedelta(days=n)) for n in horizons}
    # A origem do saldo é a mesma em todos os horizontes; sobe pro topo pra o
    # dashboard renderizar o aviso sem precisar abrir cada projeção.
    any_h = next(iter(hz.values()), {})
    return {
        "today": today.isoformat(),
        "balance_source": any_h.get("balance_source", "manual"),
        "of_bank_count": any_h.get("of_bank_count", 0),
        "banks_excluded": any_h.get("banks_excluded", False),
        "horizons": hz,
    }


def forecast_horizons(user_id: int, horizons: tuple[int, ...] = HORIZONS) -> dict[str, Any]:
    """Previsão de saldo em vários horizontes (default 30/60/90 dias).

    Feature paga (Pro+): a projeção de `project` em hoje+N pra cada N, sobre UMA
    leitura de saldo e eventos, e devolve
    ``{"today": ..., "horizons": {"30": <projeção>, "60": ..., "90": ...}}`` —
    formato pensado pro card do dashboard e pra tool de IA. Cada projeção mantém
    a mesma semântica de `project` (saldo + receitas fixas − gastos fixos −
    boletos até a data); ver docstring de `core/services/cashflow.py`."""
    today = date.today()
    snapshot = carregar(user_id, today, today + timedelta(days=max(horizons, default=0)))
    return legado({**_horizons(today, snapshot.base, snapshot.ocorrencias, horizons),
                   **snapshot.qualidade()})


def _trajectory(today: date, sb: dict[str, Any], events: list[Ocorrencia],
                days: int, threshold: float) -> dict[str, Any]:
    """Parte pura de `forecast_with_trajectory`: trajetória dia a dia, pior dia,
    vencidos e vencem hoje sobre saldo e eventos já lidos. Aceita tipo de evento
    que `_projection` não conhece (o simulador de decisão acrescenta os dele)."""
    days = max(0, int(days))
    horizon_end = today + timedelta(days=days)
    threshold = centavos(threshold)
    if threshold is None:
        raise ValueError("limite deve ser um número finito")
    # Parcelas do saldo até o dia corrente; o saldo do dia é a soma exata delas,
    # a mesma conta de `project`.
    parcelas = [dinheiro(sb["saldo"])]
    available = parcelas[0] is not None
    if not available:
        parcelas = []
    vencidos: list[dict] = []
    vencem_hoje: list[dict] = []
    eventos_por_dia: dict[date, list[dict]] = {}
    valores_por_dia: dict[date, list[Decimal]] = {}
    for event in events:
        d = event.data
        compromisso = event.detalhe()
        if d is None or d > horizon_end or not event.incluida:
            continue
        valor = event.assinado
        if d <= today:
            # Boleto/fatura já vencido (ou vencendo hoje): `project()` o soma em
            # qualquer horizonte, então ele pesa no saldo de partida — e é listado
            # em `vencidos` (ou `vencem_hoje`) pra não sumir da resposta. Recorrente
            # de hoje também pode cair aqui, sob hipótese de não realização.
            parcelas.append(valor)
            (vencidos if d < today else vencem_hoje).append({"date": d.isoformat(), **compromisso})
            continue
        eventos_por_dia.setdefault(d, []).append(compromisso)
        valores_por_dia.setdefault(d, []).append(valor)
    vencidos.sort(key=lambda v: v["date"])  # mais antigo primeiro; estável no empate

    saldo_projetado = centavos(somar(parcelas)) if available else None
    saldos = [saldo_projetado]  # posição N = saldo no fim do dia N; 0 = partida
    worst: dict[str, Any] | None = None
    worst_i = 0
    trajectory: list[dict] = []
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        if d in valores_por_dia:  # dia sem evento repete o saldo, sem refazer a soma
            parcelas.extend(valores_por_dia[d])
            saldo_projetado = centavos(somar(parcelas)) if available else None
        item = {
            "date": d.isoformat(),
            "saldo_projetado": saldo_projetado,
            "abaixo_do_limite": saldo_projetado is not None and saldo_projetado < threshold,
            "compromissos": eventos_por_dia.get(d, []),
        }
        trajectory.append(item)
        saldos.append(saldo_projetado)
        if saldo_projetado is not None and (worst is None or saldo_projetado < worst["saldo_projetado"]):
            worst, worst_i = item, i

    worst_day = None
    if worst is not None:
        # Causas = saídas desde o último pico: o maior saldo em [dia 0, pior dia),
        # o mais recente em empate (`topo`). Num patamar (mesmo saldo vários dias
        # seguidos), `desde` é o dia em que o saldo CHEGOU lá, mas as causas só
        # contam depois do último dia parado nele: uma saída compensada no mesmo
        # dia por uma receita, no meio do patamar, não derrubou nada.
        topo = max(range(worst_i), key=lambda n: (saldos[n], n))
        if worst["saldo_projetado"] >= saldos[topo]:
            # Não houve queda (só possível com o pior dia no dia 1, sem cair em
            # relação à partida): nada a explicar, então sem causas e sem `desde`.
            causas, desde = [], None
        else:
            pico = topo
            while pico > 0 and saldos[pico - 1] == saldos[pico]:
                pico -= 1
            causas = [
                {"date": it["date"], **c}
                for it in trajectory[topo:worst_i]  # dias topo+1 .. pior dia
                for c in it["compromissos"]
                if c["tipo"] != "receita"
            ]
            desde = (today + timedelta(days=pico)).isoformat()
        # Dict novo: `worst` é o próprio item de `trajectory`, e não pode ganhar chave.
        worst_day = {**worst, "causas": causas, "desde": desde}

    return {
        "period": {"start": (today + timedelta(days=1)).isoformat(), "end": horizon_end.isoformat()} if days > 0 else None,
        "threshold": threshold,
        "trajectory": trajectory,
        "worst_day": worst_day,
        "vencidos": vencidos,
        "vencem_hoje": vencem_hoje,
    }


def forecast_with_trajectory(user_id: int, days: int = 90, threshold: float = 0.0) -> dict[str, Any]:
    """Trajetória diária de saldo projetado (default 90 dias) e o "pior dia" no
    caminho, com os compromissos que levaram até ele, mais os horizontes de
    `forecast_horizons` — tudo da mesma leitura. Mesmos eventos de `project`
    (snapshot), distribuídos dia a dia em vez de somados no horizonte —
    pensada pra achar aperto de saldo que os marcos não mostram. Feature Pro+."""
    today = date.today()
    snapshot = carregar(user_id, today, today + timedelta(days=max(int(days), *HORIZONS)))
    return legado({
        **_horizons(today, snapshot.base, snapshot.ocorrencias, HORIZONS),
        **_trajectory(today, snapshot.base, snapshot.ocorrencias, days, threshold),
        **snapshot.qualidade(),
        'compromissos': [e.detalhe() for e in snapshot.ocorrencias],
        'premises': 'Projeção condicional das obrigações conhecidas; valores ou realizações desconhecidos permanecem a conferir. Não inclui estimativa variável.',
    })



__all__ = ["forecast_horizons", "forecast_with_trajectory"]
