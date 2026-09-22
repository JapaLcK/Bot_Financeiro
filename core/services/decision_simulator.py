"""
core/services/decision_simulator.py — simulador de decisão financeira (Pro):
"e se eu comprar isto à vista, parcelado ou financiado?".

Cada cenário vira eventos extras sobre a MESMA leitura de saldo e compromissos
de `core/services/cashflow.py` e passa pela trajetória diária de
`cashflow_forecast._trajectory`. Sem persistência e sem apontar vencedor.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.services import cashflow
from core.services.cashflow_forecast import _trajectory

# strict: `true` e "180000" não passam por número; int continua valendo em float.
_DINHEIRO = dict(ge=0, le=1_000_000_000, allow_inf_nan=False, strict=True)


def installments(principal: float, monthly_rate: float, n: int) -> list[float]:
    """Parcelas de `principal` em `n` meses. Taxa 0: principal/n. Com taxa: tabela
    Price. Em centavos: todas iguais ao valor arredondado, e o resíduo do
    arredondamento vai para a última, então a soma é o total exato."""
    if monthly_rate:
        # 1 − (1+r)^−n via expm1/log1p: com r minúsculo, `(1 + r) ** -n` vira 1.0
        # em float e a fórmula direta divide por zero ou perde todos os dígitos.
        total = principal * monthly_rate / -math.expm1(-n * math.log1p(monthly_rate)) * n
    else:
        total = principal
    total_c = round(total * 100)
    parcela_c = round(total_c / n)
    return [parcela_c / 100] * (n - 1) + [(total_c - parcela_c * (n - 1)) / 100]


def _add_months(d: date, k: int) -> date:
    """`d` + k meses, com o dia ajustado ao tamanho do mês (31/01 + 1 → 28/02 ou
    29/02). Conta sempre a partir da data original, então 31/01 + 2 é 31/03."""
    y, m = divmod(d.month - 1 + k, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")  # `juros_mensal` errado não vira 0 % calado

    @model_validator(mode="before")
    @classmethod
    def _null_vale_o_padrao(cls, data: Any) -> Any:
        # O LLM manda `null` em parâmetro opcional: some a chave CONHECIDA e vale o
        # default do Field. Obrigatório em null continua recusado (vira "faltando") e
        # chave desconhecida em null segue para o `extra="forbid"` recusar.
        if not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items() if v is not None or k not in cls.model_fields}


class Cenario(_Corpo):
    nome: str = Field(min_length=1, max_length=60)
    preco: float = Field(gt=0, le=1_000_000_000, allow_inf_nan=False, strict=True)
    entrada: float = Field(0.0, **_DINHEIRO)
    # `None` ou 1 = à vista (valor cheio na data da compra). Parcelar é `parcelas > 1`.
    parcelas: Optional[int] = Field(None, ge=1, le=420, strict=True)
    juros_mensal_pct: float = Field(0.0, ge=0, le=20, allow_inf_nan=False, strict=True)
    data_compra: Optional[date] = None
    custos_unicos: float = Field(0.0, **_DINHEIRO)
    despesa_mensal_nova: float = Field(0.0, **_DINHEIRO)

    @field_validator("parcelas", mode="before")
    @classmethod
    def _float_inteiro(cls, v: Any) -> Any:
        # 48.0 (JSON de LLM) vira 48; 1.49, true e "48" seguem para o strict recusar.
        return int(v) if isinstance(v, float) and v.is_integer() else v

    @property
    def a_vista(self) -> bool:
        """Sem `parcelas` (ou com 1): paga o valor cheio na data da compra."""
        return (self.parcelas or 1) == 1

    @property
    def financiado(self) -> float:
        return 0.0 if self.a_vista else self.preco - self.entrada

    @model_validator(mode="after")
    def _coerente(self) -> "Cenario":
        if self.entrada > self.preco:
            raise ValueError("entrada maior que o preço")
        if self.a_vista and 0 < self.entrada < self.preco:
            raise ValueError("entrada parcial sem parcelas: informe parcelas > 1 ou tire a entrada")
        if not self.a_vista and self.entrada == self.preco:
            raise ValueError("entrada igual ao preço com parcelas > 1: nada a financiar")
        if self.a_vista and self.juros_mensal_pct > 0:
            # `parcelas: 1` (ou ausente) é à vista, e à vista não rende juros: aceitar
            # a combinação jogaria a taxa no lixo sem o usuário saber.
            raise ValueError("juros sem parcelamento: pergunte ao usuário em quantas parcelas; "
                             "se for uma compra única com acréscimo, some o acréscimo ao preco")
        if self.data_compra is None:
            self.data_compra = date.today()
        if self.data_compra < date.today():
            raise ValueError("data_compra no passado")
        try:
            _add_months(self.data_compra, self.parcelas or 1)
        except (ValueError, OverflowError):
            raise ValueError("data_compra fora do calendário") from None
        if self.financiado > 0 and min(installments(self.financiado, self.juros_mensal_pct / 100, self.parcelas)) < 0.01:
            raise ValueError("valor financiado pequeno demais para esse número de parcelas")
        return self


class Simulacao(_Corpo):
    """Fonte única da validação: a rota e a tool de IA validam por aqui."""
    reserva_minima: float = Field(0.0, **_DINHEIRO)
    cenarios: list[Cenario] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _nomes_distintos(self) -> "Simulacao":
        # Normalizado: "A" e "a " são o mesmo rótulo para quem lê a comparação.
        nomes = [c.nome.strip().casefold() for c in self.cenarios]
        if len(set(nomes)) != len(nomes):
            raise ValueError("cenários com o mesmo nome: a comparação fica ambígua")
        return self


def _duas_casas(v: float) -> float:
    return round(v, 2) + 0.0  # `+ 0.0` troca −0,0 por 0,0 no JSON


def _decision_events(cen: Cenario, today: date,
                     until: date) -> tuple[list[tuple[date, str, str, float]], dict[str, Any]]:
    """Eventos do cenário até `until` (inclusive) e o resumo do contrato inteiro."""
    # `max`: a validação pode ter rodado ontem, num pedido que virou a meia-noite.
    # Sem isto a compra cairia no passado e o saldo de partida a absorveria.
    compra = max(cen.data_compra, today)
    financiado = cen.financiado
    valores = installments(financiado, cen.juros_mensal_pct / 100, cen.parcelas) if financiado > 0 else []
    datas = [_add_months(compra, k) for k in range(1, len(valores) + 1)]
    # À vista: o preço cheio sai na data da compra. Parcelado: sai a entrada.
    na_compra = cen.preco if cen.a_vista else cen.entrada
    # Tudo o que sai no dia da compra: o valor acima mais os custos únicos.
    pago_na_compra = na_compra + cen.custos_unicos

    events: list[tuple[date, str, str, float]] = []
    if compra <= until:
        if na_compra > 0:
            events.append((compra, "simulacao",
                           f"{cen.nome}: {'à vista' if cen.a_vista else 'entrada'}", -na_compra))
        if cen.custos_unicos > 0:
            events.append((compra, "simulacao", f"{cen.nome}: custos únicos", -cen.custos_unicos))
    events += [(d, "simulacao_parcela", f"{cen.nome}: parcela {k}/{len(valores)}", -v)
               for k, (d, v) in enumerate(zip(datas, valores), start=1) if d <= until]
    if cen.despesa_mensal_nova > 0:
        k = 1
        while (d := _add_months(compra, k)) <= until:
            events.append((d, "simulacao", f"{cen.nome}: despesa mensal nova", -cen.despesa_mensal_nova))
            k += 1

    fora = [v for d, v in zip(datas, valores) if d > until]
    soma_parcelas = math.fsum(valores)
    contrato = {
        "data_compra": compra.isoformat(),
        "a_vista": cen.a_vista,
        # À vista não tem entrada, mesmo quando o pedido veio como `entrada = preco`.
        "entrada": 0.0 if cen.a_vista else _duas_casas(cen.entrada),
        "pago_na_compra": _duas_casas(pago_na_compra),
        "valor_financiado": _duas_casas(financiado),
        "parcelas": cen.parcelas or 1,  # ecoa o pedido; à vista é 1 pagamento na data da compra
        "parcela": valores[0] if valores else _duas_casas(cen.preco),
        "primeira_parcela": (datas[0] if datas else compra).isoformat(),
        "ultima_parcela": (datas[-1] if datas else compra).isoformat(),
        # Custo do contrato inteiro: pago_na_compra (já com os custos únicos) + parcelas.
        # Fora dele: a despesa mensal nova, que não tem fim contratado.
        "total_pago": _duas_casas(pago_na_compra + soma_parcelas),
        "juros_totais": _duas_casas(soma_parcelas - financiado),
        "custos_unicos": _duas_casas(cen.custos_unicos),
        "despesa_mensal_nova": _duas_casas(cen.despesa_mensal_nova),
        "parcelas_fora_do_horizonte": {"quantidade": len(fora), "valor": _duas_casas(math.fsum(fora))},
    }
    return events, contrato


def _resumo(traj: dict[str, Any]) -> dict[str, Any]:
    dias = traj["trajectory"]
    abaixo = [it["date"] for it in dias if it["abaixo_do_limite"]]
    wd = traj["worst_day"]
    return {
        "saldo_final_90": dias[-1]["saldo_projetado"] + 0.0,
        "pior_dia": {"date": wd["date"], "saldo": wd["saldo_projetado"] + 0.0},
        "dias_abaixo_da_reserva": len(abaixo),
        "primeiro_dia_abaixo": abaixo[0] if abaixo else None,
    }


DIAS = 90  # janela do saldo diário; a chave `saldo_final_90` do resumo assume 90

PREMISSAS = (
    "Saldo de 90 dias: o mesmo da previsão de saldo (saldo + receitas fixas − gastos fixos "
    "automáticos − boletos − faturas em aberto), mais a compra simulada. O pior dia e os dias "
    "abaixo da reserva incluem hoje após os compromissos e a compra, até o dia 90 inclusive "
    "(91 datas). pago_na_compra é tudo "
    "o que sai na data da compra: o preço cheio (à vista) ou a entrada (parcelado), mais os "
    "custos únicos; no à vista, entrada é 0. A primeira parcela e a despesa mensal nova saem "
    "um mês depois, no mesmo dia (ajustado ao fim do mês). Parcelado com juros usa a tabela "
    "Price; o arredondamento em centavos vai para a última parcela. total_pago é pago_na_compra "
    "mais todas as parcelas, inclusive as que caem depois do dia 90, e NÃO inclui a despesa "
    "mensal nova, que não tem fim contratado. Receitas e gastos fixos só entram quando "
    "mensais ou anuais; recorrências semanais, diárias e únicas não são projetadas. "
    "Não inclui gastos avulsos futuros nem IOF, "
    "seguro ou tarifas não informados."
)


def simulate(user_id: int, simulacao: Simulacao) -> dict[str, Any]:
    """Compara o cenário atual com 1 a 3 cenários de compra, sobre UMA leitura das
    fontes. Devolve resumos (não a trajetória diária inteira)."""
    today = date.today()
    days = DIAS
    until = today + timedelta(days=days)
    reserva = simulacao.reserva_minima
    sb = cashflow._starting_balance(user_id)
    events = cashflow._cashflow_events(user_id, today, until)

    # O motor puro inicia no dia seguinte à âncora. Ancorar em ontem inclui HOJE
    # como primeiro ponto, sem reler saldo nem mover o fim (hoje + 90 dias).
    # Compra hoje pode violar a reserva e ser encoberta por um salário amanhã.
    anchor = today - timedelta(days=1)
    atual = _resumo(_trajectory(anchor, sb, events, days + 1, reserva))
    cenarios = []
    for cen in simulacao.cenarios:
        extra, contrato = _decision_events(cen, today, until)
        resumo = _resumo(_trajectory(anchor, sb, events + extra, days + 1, reserva))
        resumo["delta_vs_atual"] = _duas_casas(resumo["saldo_final_90"] - atual["saldo_final_90"])
        # Compra datada depois da janela: o saldo de 90 dias é igual ao atual, e sem
        # este marcador a resposta pareceria "não muda nada".
        resumo["compra_fora_da_janela"] = contrato["data_compra"] > until.isoformat()
        cenarios.append({"nome": cen.nome, "resumo": resumo, "contrato": contrato})

    return {
        "today": today.isoformat(),
        "balance_source": sb["balance_source"],
        "banks_excluded": sb["banks_excluded"],
        "reserva_minima": _duas_casas(reserva),
        "atual": atual,
        "cenarios": cenarios,
        "premissas": PREMISSAS,
    }


__all__ = ["DIAS", "Simulacao", "installments", "simulate"]
