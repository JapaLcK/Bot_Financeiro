"""A régua do que pode virar `/items/{id}` na Pluggy a partir do operador.

Arquivo próprio (CLAUDE.md §0.5): `of_itens_operador.py` tem o EFEITO; aqui mora
a decisão de sobre QUAL id ele pode agir. Restaurado de
`bda3ee7:scripts/adotar_items_lista.py` (`recusa_id` e `_dica_de_recusa`); a
lista de alvos (`alvos()`) não voltou — não existe mais escrita em lista.
"""
from __future__ import annotations

from core.services.pluggy import _ITEM_ID_OK


def recusa_id(item: str) -> str:
    """Por que este id NÃO pode ir para a Pluggy — string VAZIA quando pode.

    Duas réguas, e a segunda não é redundante:
      • `_ITEM_ID_OK` é a que JÁ existe para os montadores de `/items/{id}`
        (§0.1). Ela NÃO exige UUID, de propósito — `core/services/pluggy.py`
        diz por quê (sandbox e ambiente de teste);
      • só-dígitos pega o erro já cometido: `--item <user_id>`. A Pluggy EMITE
        UUID, e 200.000 `uuid4()` (medido 2026-09-10, com e sem hífen) deram 0
        só-dígitos — a régua não recusa id dela no formato de hoje.
    Vazio também é recusado: `--item "$VAR"` com a variável não setada.
    `isdigit` sem surpresa de unicode: o charset ASCII do `_ITEM_ID_OK` já passou.
    """
    if not _ITEM_ID_OK.match(item):
        return "não é id de item da Pluggy (caractere que muda a URL, vazio, ou > 64)"
    if item.isdigit():
        return "é só dígitos: isso é um user_id, não o id de item da Pluggy"
    return ""


def _dica_de_recusa() -> str:
    """O que fazer quando o id RECUSADO veio do registry, e não de um dedo errado.

    A ESCRITA do registry não tem régua (`provider_item_id` guarda o que vier no
    corpo do webhook) e a LEITURA tem, então o dry-run pode ver id que o
    `--item` recusa. A régua continua recusando: para o que nem vira URL é
    certeza (`_item_path` os barra dentro do cliente); para o só-dígitos é
    hipótese forte, e por isso o texto oferece o painel da Pluggy em vez de
    afirmar que o item não existe lá.
    """
    return ("\n  Id assim quase certamente não veio da Pluggy: o registry ACEITA na escrita"
            "\n  o que ela nunca emitiria — quem escreve é o corpo do webhook. O provável é"
            "\n  não haver nada a apagar lá. Se você tem certeza do contrário"
            "\n  (sandbox/homolog), o caminho é o painel da Pluggy: este script não manda"
            "\n  id que ela não emite.")
