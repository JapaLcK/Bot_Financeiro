"""Contrato público do Xerife. Regras são geridas pelas rotas próprias, nunca por config crua."""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

XERIFE_MULTIPLIER = 2.5
XERIFE_MIN_VALOR = 50.0
MAX_REGRAS = 50
Valor = Annotated[float, Field(strict=True, ge=0.01, le=1_000_000, allow_inf_nan=False)]
Texto = Annotated[str, Field(strict=True, min_length=1, max_length=200)]


def normalizar_texto(value: str) -> str:
    return " ".join(value.lower().split())


class XerifeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    multiplicador: Annotated[float, Field(ge=1, le=10, allow_inf_nan=False)] = XERIFE_MULTIPLIER
    minimo: Valor = XERIFE_MIN_VALOR
    limites: dict[Texto, Valor] = Field(default_factory=dict, max_length=100)
    email_enabled: StrictBool = True

    @field_validator("limites")
    @classmethod
    def categorias(cls, value):
        normalized = {normalizar_texto(k): v for k, v in value.items()}
        if "" in normalized or len(normalized) != len(value):
            raise ValueError("Categorias vazias ou repetidas.")
        return normalized


class RegraEsperado(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categoria: Texto
    descricao: Texto
    teto: Valor
    data_fim: date | None = None

    @field_validator("categoria", "descricao", mode="before")
    @classmethod
    def texto(cls, value):
        return normalizar_texto(value) if isinstance(value, str) else value

    @field_validator("data_fim", mode="before")
    @classmethod
    def data_iso(cls, value):
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        return value


def config_publica(config: dict | None) -> dict:
    """Config legada inválida não quebra a tela: recupera campos válidos, defaults no resto."""
    result = XerifeConfig().model_dump()
    for key, value in (config if isinstance(config, dict) else {}).items():
        if key in result:
            try:
                result[key] = XerifeConfig.model_validate({key: value}).model_dump()[key]
            except ValidationError:
                pass
    return result


class RegraPersistida(RegraEsperado):
    id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]


def regras_publicas(config: dict | None) -> list[dict]:
    """Antes do contrato tipado, config aceitava qualquer JSON; ignore regras legadas inválidas."""
    raw = config.get("regras_esperado") if isinstance(config, dict) else None
    if not isinstance(raw, list):
        return []
    result = []
    ids = set()
    for item in raw[:MAX_REGRAS]:
        try:
            regra = RegraPersistida.model_validate(item).model_dump(mode="json")
        except ValidationError:
            continue
        if regra["id"] not in ids:
            result.append(regra)
            ids.add(regra["id"])
    return result
