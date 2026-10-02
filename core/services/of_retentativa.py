"""Retentativa do tique de saúde (Onda 5, PR-B2): quem entra e o que um desfecho
conta para o disjuntor. Funções PURAS (sem banco, sem rede).

Quem lista é `list_connections_para_retentar` (`db/open_finance_state.py`, com os
filtros baratos: terminal, cooldown, dono único); quem executa é
`retentar_leituras` (`frontend/routes/of_retentativa.py`). A tabela célula por
célula está em `docs/open_finance_estados.md` §2.2.

A decisão é pelo MESMO estado da tela (`connection_ui_state`), mais o motivo e a
Pluggy à frente. A âncora da "Pluggy à frente" é `last_attempt_at`, não
`last_sync_at`: `no_accounts` é veredito ("li e veio vazio"), e só vale ler de
novo se a Pluggy coletou depois da nossa última tentativa.
"""

from __future__ import annotations

from core.services.pluggy_health import (
    _DETALHE_COLETA_ESTOURADA, _MOTIVOS_DE_LEITURA, INVESTMENTS_READ_FAILED, _REASONS_OK,
    connection_ui_state, pluggy_tem_dado_depois_de)

# Desfechos que não dizem nada sobre a saúde do provedor: não contam como falha
# nem como sucesso no disjuntor. O coalescido (item já em voo) chega como `None`.
_NEUTROS = frozenset({"sync_in_progress", "stale_authorization", "connection_not_found",
                      "connection_paused", "connection_deleted"})


def classe_de_retentativa(row: dict) -> str | None:
    """`leitura`, `coleta`, `pluggy_a_frente`, `pista_de_erro` ou None (não retenta).

    A classe só serve para log e contagem; a ordem é da listagem (tentativa mais
    antiga primeiro), sem prioridade por classe, e o orquestrador passa para o fim
    quem ele já tentou (`_TENTADOS`, memória). Quem falha sempre atrasa os saudáveis
    atrás dele em `N // 3 + 1` tiques (o disjuntor de 3 falhas seguidas): nenhum
    caso deixa o saudável sem vez (`docs/open_finance_estados.md` §2.2).
    """
    health = row.get("health") if isinstance(row.get("health"), dict) else None
    # E13 (DECISÃO 1 = B): a execução falhou na Pluggy. GET não muda o item em
    # ERROR; só uma coleta nova (PATCH) ou o auto-update dela.
    if str((health or {}).get("item_status") or "").upper() == "ERROR":
        return None
    ui = connection_ui_state(row)
    estado = ui["state"]
    # O teto (Fase 4, PR 2) é a coleta vencida com outro rótulo: E5, E7 e E8
    # mantêm a classe, e a coleta estourada de quem já sincronizou entra (E25).
    if estado == "error_recoverable" and ui["detail"] == _DETALHE_COLETA_ESTOURADA:
        estado = "updating"
    motivo = str(row.get("status_reason") or "").lower()

    if estado == "error_recoverable":
        if motivo in _MOTIVOS_DE_LEITURA:
            return "leitura"                      # E1
        # E12: `ERROR` local do webhook, sem motivo. Motivo desconhecido (E23) não.
        return "pista_de_erro" if motivo in _REASONS_OK else None
    if estado == "partial" and motivo == INVESTMENTS_READ_FAILED:
        return "leitura"                          # E2
    if estado == "updating":
        if not (row.get("coleta_vencida") or row.get("coleta_estourada")):
            return None                           # E6, E24, E25b: coleta legítima
        if motivo != "no_accounts":
            return "coleta"                       # E5, E25
        # E7: `no_accounts` é veredito; cai na Pluggy à frente, abaixo (E8)
    elif estado not in ("updated", "partial", "no_accounts"):
        return None                               # E3, E4, E10, E11, E14
    if pluggy_tem_dado_depois_de(health, row.get("last_attempt_at")):
        return "pluggy_a_frente"                  # E8, E16
    return None                                   # E9, E15, E17


def elegiveis(rows: list[dict]) -> list[tuple[dict, str]]:
    """As linhas que entram, na ordem recebida, com a classe de cada uma."""
    return [(r, c) for r in rows if (c := classe_de_retentativa(r))]


def tipo_de_desfecho(desfecho: dict | None) -> str:
    """O que um desfecho de `_run_pluggy_sync_bg` conta para o disjuntor:
    `429` (para o tique), `falha` (exceção: 5xx, timeout, rede), `neutro` ou `ok`.

    `ok` é "a Pluggy respondeu": inclui `no_accounts` e `item_missing`, que são
    respostas dela. Converge como o disjuntor do job de saúde: quem respondeu
    prova que o provedor está de pé.
    """
    if desfecho is None or desfecho.get("reason") in _NEUTROS:
        return "neutro"
    if desfecho.get("status_code") == 429:
        return "429"
    if desfecho.get("ok") or desfecho.get("reason") != "excecao":
        return "ok"
    return "falha"
