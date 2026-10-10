"""Anomalia de gasto do Xerife: a regra, a conta que a justifica e o texto do alerta.

O Xerife compara UM lançamento com a média POR LANÇAMENTO da mesma categoria nos 90 dias
anteriores às últimas 24h. A unidade (lançamento x lançamento, mesma categoria, mesmo tipo,
sem movimento interno, sem lançamentos marcados como "esperados") e a janela são fixas e
completas; por isso não há período parcial a alinhar por calendário.

Só `launches` entra: compra no cartão de crédito (`credit_transactions`) fica de fora.

Este módulo não toca banco nem relógio (`agora` entra por parâmetro); a leitura é de
`db/anomalias.py`. Dinheiro é Decimal; o payload só carrega número JSON (float de 2 casas).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from core.observability import get_logger

logger = get_logger(__name__)

JANELA_DIAS = 90
CONVITE_ESPERADO = "Era esperado? Marque no painel que eu deixo de contar esse gasto."   # decisão D3 do dono
_CENTAVO = Decimal("0.01")
_DECIMO = Decimal("0.1")


@dataclass(frozen=True)
class AmostraMinima:
    lancamentos: int   # lançamentos NÃO esperados da categoria na janela
    dias: int          # dias de histórico do usuário; 0 = não exige


# Fonte única da regra de amostra. Hoje só por ocorrências (comportamento anterior ao PL-04);
# exigir 4 semanas de histórico é trocar `dias=0` por `dias=28`.
AMOSTRA_MINIMA = AmostraMinima(lancamentos=5, dias=0)


@dataclass(frozen=True)
class Referencia:
    media: Decimal          # média por lançamento, valor cheio (a decisão usa este)
    n: int
    primeira: date
    ultima: date
    esperados_fora: int     # esperados da categoria na janela, deixados de fora da conta


@dataclass(frozen=True)
class Candidato:
    launch_id: int
    valor: Decimal
    categoria: str
    descricao: str
    data: date


@dataclass(frozen=True)
class Avaliacao:
    status: str             # anomalia | sem_referencia | valor_pequeno | abaixo_do_limiar | amostra_insuficiente
    historico_incompleto: bool


def avaliar_lancamento(valor: Decimal, ref: Referencia | None, dias_historico: int, *,
                       multiplicador: Decimal, minimo: Decimal,
                       amostra: AmostraMinima | None = None) -> Avaliacao:
    """Decisão pura. `amostra_insuficiente` só quando o gasto TERIA alertado: é o que o piloto
    precisa contar, e não os gastos pequenos ou normais de quem tem pouco histórico."""
    amostra = amostra or AMOSTRA_MINIMA
    incompleto = dias_historico < JANELA_DIAS
    if ref is None or ref.media <= 0:
        status = "sem_referencia"
    elif valor < minimo:
        status = "valor_pequeno"
    elif valor <= multiplicador * ref.media:
        status = "abaixo_do_limiar"
    elif ref.n < amostra.lancamentos or dias_historico < amostra.dias:
        status = "amostra_insuficiente"
    else:
        status = "anomalia"
    return Avaliacao(status, incompleto)


def limiar(bruto: Any, padrao: float) -> Decimal:
    """Limiar vindo da `config` do agente (chega crua do cliente). Ausente, não finito (nan, inf)
    ou <= 0 cai no padrão: Decimal('NaN') estourava em toda comparação. Lixo não numérico
    ("abc") levanta ValueError, que o runner isola do bloco de limites."""
    v = float(bruto or padrao)
    return Decimal(str(v if math.isfinite(v) and v > 0 else padrao))


def _2casas(v: Decimal) -> Decimal:
    return v.quantize(_CENTAVO, ROUND_HALF_UP)


def _pct(parte: Decimal, base: Decimal, casas: int) -> Decimal:
    """Arredonda UMA vez, do valor cheio, direto à casa pedida (11,46 -> 11,5 nunca vira 12)."""
    return (parte / base * 100).quantize(Decimal(1).scaleb(-casas), ROUND_HALF_UP)


def montar_explicacao(c: Candidato, ref: Referencia, av: Avaliacao, dias_historico: int, *,
                      multiplicador: Decimal, agora: datetime) -> dict[str, Any]:
    amostra = AMOSTRA_MINIMA
    media = _2casas(ref.media)
    return {
        "versao": 1,
        "detector": "xerife.lancamento_vs_media_categoria",
        "calculado_em": agora.isoformat(timespec="seconds"),
        "fonte": {"base": "lancamentos", "cartao_incluido": False},
        "referencia": {
            "estatistica": "media_por_lancamento",
            "categoria": c.categoria.lower(),
            "valor": float(media),
            "janela": {"dias": JANELA_DIAS, "primeira": ref.primeira.isoformat(),
                       "ultima": ref.ultima.isoformat()},
            "limiar_multiplicador": float(multiplicador),
        },
        "atual": {"lancamento_id": c.launch_id, "valor": float(_2casas(c.valor)),
                  "data": c.data.isoformat()},
        # A diferença sai das duas parcelas que o usuário lê (valor - média de 2 casas), para a
        # conta bater no papel; razão e percentual saem do valor cheio, arredondados uma vez.
        "diferenca": {"absoluta": float(_2casas(c.valor) - media),
                      "percentual": float(_pct(c.valor - ref.media, ref.media, 1)),
                      "razao": float((c.valor / ref.media).quantize(_DECIMO, ROUND_HALF_UP))},
        "amostra": {"lancamentos": ref.n, "minimo_lancamentos": amostra.lancamentos,
                    "minimo_dias": amostra.dias, "suficiente": av.status == "anomalia",
                    "dias_historico": dias_historico,
                    "historico_incompleto": av.historico_incompleto,
                    "esperados_fora": ref.esperados_fora},
    }


def _linha(texto: str, limite: int = 40) -> str:
    # Texto do usuário em mensagem de uma linha (sem quebra, marcação ou controle invisível).
    from core.reports.weekly import _categoria_em_uma_linha
    return _categoria_em_uma_linha(texto, limite)


def titulo(categoria: str) -> str:
    return f"Gasto fora do padrão em {_linha(categoria)}"


def montar_mensagem(c: Candidato, ref: Referencia, av: Avaliacao, dias_historico: int) -> str:
    from core.reports.formatting import _fmt_brl, _fmt_pct
    media, dif = _2casas(ref.media), _2casas(c.valor) - _2casas(ref.media)
    razao = f"{(c.valor / ref.media).quantize(_DECIMO, ROUND_HALF_UP)}".replace(".", ",")
    desc = f" ({_linha(c.descricao, 60)})" if (c.descricao or "").strip() else ""
    n = f"{ref.n} lançamento{'s' if ref.n != 1 else ''}"
    partes = [
        f"{_fmt_brl(_2casas(c.valor))} em {_linha(c.categoria)}{desc} — {razao}x a sua média por "
        f"lançamento nessa categoria ({_fmt_brl(media)}; diferença de {_fmt_brl(dif)}, "
        f"{_fmt_pct(_pct(c.valor - ref.media, ref.media, 0))}), com base em {n} de "
        f"{ref.primeira:%d/%m} a {ref.ultima:%d/%m}.",
    ]
    if av.historico_incompleto:
        partes.append(f"Histórico ainda curto ({dias_historico} de {JANELA_DIAS} dias): "
                      "a comparação pode não refletir o seu padrão.")
    partes.append(CONVITE_ESPERADO)
    return " ".join(partes)


def montar_payload(c: Candidato, ref: Referencia, av: Avaliacao, dias_historico: int, *,
                   multiplicador: Decimal, agora: datetime) -> dict[str, Any]:
    """Payload do evento. Os campos legados saem SEMPRE; a explicação e o texto novo são
    enriquecimento: se falharem, o alerta essencial sai com o texto simples (e o log leva só o
    nome da classe da exceção, nunca o texto do usuário)."""
    payload: dict[str, Any] = {
        "tipo": "anomalia", "launch_id": c.launch_id, "categoria": c.categoria,
        "descricao": (c.descricao or "")[:120], "valor": float(c.valor),
        "media": round(float(ref.media), 2),   # float como a main gravava; Decimal.quantize estoura em 1e26
    }
    try:
        payload["explicacao"] = montar_explicacao(
            c, ref, av, dias_historico, multiplicador=multiplicador, agora=agora)
        payload["mensagem"] = montar_mensagem(c, ref, av, dias_historico)
        payload["titulo"] = titulo(c.categoria)
    except Exception as exc:
        logger.warning("anomalia: explicação não montada (%s)", type(exc).__name__)
        payload.pop("explicacao", None)
        payload["titulo"] = f"Gasto fora do padrão em {c.categoria}"
        v, m = (f"{payload[k]:.2f}".replace(".", ",") for k in ("valor", "media"))
        payload["mensagem"] = (f"R$ {v} em {c.categoria}: bem acima do seu normal (R$ {m}). "
                               f"{CONVITE_ESPERADO}")
    return payload


def avaliar_candidatos(rows: list[dict[str, Any]], agora: datetime, *,
                       multiplicador: Decimal, minimo: Decimal) -> tuple[list[dict[str, Any]], int]:
    """Linhas de `db.anomalias.listar_candidatos_xerife` -> (payloads dos alertas, quantos
    candidatos o piso de amostra suprimiu). Sem PII no segundo número."""
    from utils_date import day_tz
    payloads: list[dict[str, Any]] = []
    suprimidos = 0
    for r in rows:
        ref = None
        if r["n"]:
            ref = Referencia(Decimal(r["media"]), int(r["n"]), day_tz(r["primeira"]),
                             day_tz(r["ultima"]), int(r["esperados_fora"] or 0))
        dias = min(JANELA_DIAS, max(0, (agora - r["primeira_usuario"]).days))
        valor = Decimal(r["valor"])
        av = avaliar_lancamento(valor, ref, dias, multiplicador=multiplicador, minimo=minimo)
        if av.status == "amostra_insuficiente":
            suprimidos += 1
        elif av.status == "anomalia":
            c = Candidato(int(r["id"]), valor, r["categoria"], r["descricao"] or "",
                          day_tz(r["criado_em"]))
            payloads.append(montar_payload(c, ref, av, dias, multiplicador=multiplicador,
                                           agora=agora))
    return payloads, suprimidos
