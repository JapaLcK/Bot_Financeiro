"""Reexporta o patrimônio de `api/v2/patrimonio.py` (o card do chat importa `patrimonio` daqui).

A rota do app sai do `criar_app("/api/app")`, não de um router deste módulo."""
from api.v2.patrimonio import Patrimonio, patrimonio  # noqa: F401
