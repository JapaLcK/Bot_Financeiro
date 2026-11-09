"""Filtro de FORMA para um código vindo de terceiro: o que pode ir a log e a coluna persistida.

Saiu de `core/services/asaas.py` para que quem não fala com o Pix (`core/pg_text.py`) o use sem
importar o cliente do Asaas, que `tests/test_pix_inerte.py` restringe a uma allowlist.
"""

from __future__ import annotations

import re
from typing import Any


# Os separadores que o filtro de forma ACEITA — e que a normalização remove
# antes de procurar corrida de dígito. Os dois usos leem daqui de propósito:
# separador aceito e não normalizado é o defeito que voltou três vezes (§2).
_SEPARADORES = "_-."


def _codigo_seguro(valor: Any, fallback: str = "") -> str:
    """Letras, dígitos, `_`, `-` e `.`, até 60 chars, sem corrida com forma de
    documento. `fallback` é o que sai quando a forma NÃO passa.

    **NÃO é a mesma regra do `safe_code` do Pluggy**, e esta docstring dizia que
    era: medido em 2026-09-09 (remedir antes de reusar), divergem nos DOIS
    sentidos — `"42"` (aqui recusado por `isdigit()`, lá aceito) e `"x"*60` (aqui
    aceito, lá não: o `_CODE_FORMAT` para em 20). Coincidem no que importa,
    CPF/CNPJ/agência-conta. `_erro_seguro` (`db/webhook_outbox.py`) era CÓPIA
    disto e virou import no 1b-B; os 29 casos que a guardavam migraram para
    `tests/test_asaas_codigo_seguro.py`.

    A descrição do erro do Asaas vem no MESMO objeto que o `code`
    (`{"errors": [{"code": …, "description": "O CPF/CNPJ 123… é inválido"}]}`),
    e a descrição carrega o dado do titular. Filtrar por FORMA é o que impede
    alguém de ampliar isto para "só o campo description, que é curtinho".
    """
    texto = str(valor or "")
    if not texto or len(texto) > 60:
        return fallback
    if not all(c.isalnum() or c in _SEPARADORES for c in texto):
        return fallback
    nu = texto.translate(str.maketrans("", "", _SEPARADORES))
    # Só-dígitos é recusado: a forma de um CPF ("12345678901") é exatamente a de
    # um código curto, e o código do Asaas é sempre nominal
    # ("invalid_cpfCnpj"). Recusar identificador puramente numérico custa nada —
    # não existe `code` só-dígitos na API — e fecha o caminho por onde um
    # documento entraria numa string persistida.
    if nu.isdigit():
        return fallback
    # …e só-dígitos NÃO basta. A CATEGORIA é "corrida com forma de documento,
    # em QUALQUER grafia que o filtro permita": prefixo (`CPF12345678901`),
    # separador (`123.456.789-01`) ou os dois. Por isso a normalização acima
    # remove TODO separador de `_SEPARADORES` — normalizar só parte deles fecha
    # uma grafia e deixa as outras, que foi como este mesmo defeito voltou em
    # três rodadas: só-dígitos, depois prefixo alfabético, depois `_`. Cada
    # rodada tratou o caso achado como se fosse a categoria (§2).
    # 11+ dígitos porque CPF tem 11 e CNPJ 14; código legítimo com número é
    # curto (`error_400`, `HTTP_502`, `v1.2.3`), então não colide.
    if re.search(r"\d{11,}", nu):
        return fallback
    return texto
