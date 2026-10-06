"""Contrato interno da previsão: Decimal, identidade e qualidade das ocorrências."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Literal
from math import isfinite

Direction = Literal['so_melhora', 'so_piora', 'ambos']


def dinheiro(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return d if d.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def centavos(value) -> Decimal | None:
    d = dinheiro(value)
    if d is None or not isfinite(float(d)):
        return None
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, d.adjusted() + 4)
        d = d.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
    return abs(d) if d == 0 else d


def somar(valores):
    """Soma Decimal sem perder centavos por diferença de magnitude, antes de apresentar."""
    values = [v for v in valores if v != 0]
    if not values:
        return Decimal(0)
    if any(not v.is_finite() for v in values):
        return sum(values, Decimal(0))
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, max(v.adjusted() for v in values)
                       - min(v.as_tuple().exponent for v in values) + len(str(len(values))) + 1)
        return sum(values, Decimal(0))


def legado(value):
    """Única conversão para number, depois da aritmética Decimal."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: legado(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [legado(v) for v in value]
    return value


@dataclass(frozen=True)
class Motivo:
    codigo: str
    direcao_do_erro: Direction
    origem_id: int | str | None = None
    ciclo: str | None = None
    efeito_quantificado: Decimal | None = None


@dataclass(frozen=True)
class Ocorrencia:
    fonte: str
    origem_id: int | str
    ciclo: str
    data: date | None
    tipo: str
    nome: str
    valor: Decimal | None
    direcao: Literal['entrada', 'saida']
    qualidade_valor: str = 'conhecido'
    qualidade_data: str = 'conhecida'
    realizacao: str = 'prevista'
    motivos: tuple[Motivo, ...] = ()
    incluida: bool = True

    @property
    def chave(self):
        return f'{self.fonte}:{self.origem_id}:{self.ciclo}'

    @property
    def assinado(self):
        if not self.incluida or self.realizacao == 'realizada' or self.valor is None:
            return Decimal(0)
        return self.valor if self.direcao == 'entrada' else self.valor.copy_negate()

    def detalhe(self):
        return {'chave': self.chave, 'fonte': self.fonte, 'origem_id': self.origem_id,
                'ciclo': self.ciclo, 'date': self.data.isoformat() if self.data else None,
                'tipo': self.tipo, 'nome': self.nome, 'valor': centavos(self.valor),
                'direcao': self.direcao, 'qualidade_valor': self.qualidade_valor,
                'qualidade_data': self.qualidade_data, 'realizacao': self.realizacao,
                'incluida': self.incluida, 'motivos': [asdict(m) for m in self.motivos]}


@dataclass
class Snapshot:
    hoje: date
    calculado_em: datetime
    base: dict
    ocorrencias: list[Ocorrencia] = field(default_factory=list)
    motivos: list[Motivo] = field(default_factory=list)
    premissas: list[str] = field(default_factory=lambda: [
        'Projeção condicional; receitas previstas não são garantidas.',
        'Não inclui estimativa de gastos variáveis futuros; não autoriza compra segura.',
        'Recorrências sem data de fim seguem ativas no horizonte consultado.',
        'Não reconstrói realização passada nem desloca datas por feriados.',
    ])
    valido_ate: datetime | None = None

    def qualidade(self):
        return {'estado': 'indisponivel' if self.base['saldo'] is None else
                'a_conferir' if self.motivos else 'condicional',
                'motivos': [asdict(m) for m in self.motivos], 'premissas': self.premissas,
                'calculado_em': self.calculado_em.isoformat(),
                'valido_ate': self.valido_ate.isoformat() if self.valido_ate else None,
                'cobertura': {'inclui_estimativa_variavel': False,
                             'janela_conferencia_inicio': None,
                             'fontes_incompletas': sorted({m.codigo for m in self.motivos})},
                'cabe_nas_premissas': False}


