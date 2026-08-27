"""Saúde do item Pluggy — funções PURAS (sem banco, sem rede).

Três responsabilidades, e só elas:

  • `derive_item_health(item)` traduz o `GET /items/{id}` (feito pelo servidor)
    num dicionário estável que vai pra coluna `open_finance_connections.health`;
  • `resolve_connection_state(...)` é o ÚNICO ponto que decide `status` E
    `status_reason` — os dois JUNTOS, a partir de uma observação;
  • `connection_ui_state(row)` é a ÚNICA função que decide em qual dos 9 estados
    (`_LABELS`) uma conexão está, e com que texto. Consumidores: snapshot da aba OF, resposta
    do /refresh, painel admin e o job de saúde.

Ter uma fonte só evita o que existia antes: um rótulo derivado do `status` no
`settings.html`, outro no backend, e nenhum dos dois sabendo de produto atrasado.

## A MÁQUINA DE ESTADOS, POR ESCRITO (não remende linha por linha)

Três rodadas de revisão bateram no mesmo lugar pelo mesmo motivo: cada caminho
(sync, job de saúde, reconexão, webhook) remendava `status` OU `status_reason`,
e sempre esquecia o outro. Resultado medido: `item_missing` limpo com o
`status='ERROR'` ficando ("Erro temporário" para sempre) e `no_accounts` que
nunca saía. Os dois campos são UM estado só — logo, uma decisão só.

O par é sempre `(status, status_reason)`. `status=None` significa "não mexe" e
hoje NENHUMA linha o usa: espelho vazio com item vivo é ACTIVE + motivo, porque
"não mexe" não tira um ERROR que já está lá — e sem sync periódico (o default)
nada mais tiraria, o que fazia de ERROR um estado terminal. PAUSED/DELETED
continuam protegidos pelo `where` do `mark_sync_result`. `status_reason=""`
APAGA o motivo — nunca se devolve None aqui, senão o motivo velho sobrevive à
observação nova, que é exatamente o bug.

| # | evento (observação)                                   | status  | reason      |
|---|-------------------------------------------------------|---------|-------------|
| A | `GET /items/{id}` → 404 (sync ou job de saúde)        | ERROR   | item_missing|
| B | item vivo, mas exige o usuário (LOGIN_ERROR, OUTDATED,| ERROR   | ""          |
|   | WAITING_USER_INPUT, INVALID_CREDENTIALS)              |         |             |
| C | item vivo em ERROR                                    | ERROR   | ""          |
| D | sync: leitura remota incompleta (ex.: 429 em          | ACTIVE  | read_failed |
|   | `/investments`) E espelho vazio                       |         |             |
| E | sync: leitura COMPLETA e espelho vazio                | ACTIVE  | no_accounts |
| F | item vivo com espelho cheio (sync ok ou só medido)    | ACTIVE  | ""          |
| H | job de saúde: item vivo, espelho vazio                | ACTIVE  | mantém D/E, |
|   | (não leu `/accounts`, então não INVENTA motivo)       |         | senão ""    |
| G | reconexão pelo widget (`save_pluggy_open_finance_item`)| remoto | "" + health |
|   |                                                       |         | zerado      |

B e C devolvem motivo vazio de propósito: quem conta a história ali é o
`health`, e o motivo velho (`item_missing` de ontem) só atrapalharia.

E ≠ D: "li e veio vazio" não é "não consegui ler". Só o primeiro autoriza
`no_accounts` — foi confundir os dois que fez um 429 em `/investments` descartar
contas já lidas.

H é o que tira o caráter pegajoso de `no_accounts`: `has_data` é OBSERVAÇÃO (o
job pergunta ao espelho em `list_connections_for_health_check`, não à memória),
então o motivo cai sozinho no instante em que existir dado — e o job nunca cria
um motivo sobre uma leitura que ele não fez.

G é o único evento que zera o `health`: consentimento novo torna a medição velha
sem sentido (com ela, um `item_status: MISSING` de antes ainda pintava a tela).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from utils_date import _tz

# Produto (nosso nome) → chave do `statusDetail` da Pluggy.
_PRODUCT_KEYS = {
    "BANK": "accounts",
    "CREDIT": "creditCards",
    "INVESTMENTS": "investments",
    "TRANSACTIONS": "transactions",
}

_PRODUCT_PT = {
    "BANK": "Conta",
    "CREDIT": "Cartão",
    "INVESTMENTS": "Investimentos",
    "TRANSACTIONS": "Transações",
}

# Status do item que significam "a Pluggy ainda está buscando".
_UPDATING = {"UPDATING", "CREATED", "WAITING_USER_ACTION"}
# Status que só o usuário resolve (reautorizar / responder MFA no banco).
_NEEDS_USER = {"LOGIN_ERROR", "WAITING_USER_INPUT", "INVALID_CREDENTIALS", "OUTDATED"}

_LABELS = {
    "updated": "Atualizado",
    "partial": "Parcial",
    "updating": "Atualizando…",
    "error_recoverable": "Erro temporário",
    "needs_user_action": "Ação necessária",
    "item_missing": "Conexão perdida",
    "paused": "Pausado",
    "removed": "Removido",
    "no_accounts": "Sem dados",
}

_FIXED_DETAIL = {
    "error_recoverable": "Tentaremos de novo automaticamente",
    "needs_user_action": "Reautorize o banco",
    "item_missing": "Refaça a conexão com o banco",
    "paused": "Reative seu plano para voltar a sincronizar",
    "no_accounts": "O banco não devolveu contas nem investimentos",
}

# `status_reason` que NÃO impede o estado verde. Qualquer outro motivo — inclusive
# um que este arquivo não conhece — derruba o "Atualizado" (ver `out`).
_REASONS_OK = ("", "ok")

# Código aceitável da Pluggy. A mensagem dela cita nome, conta e documento do
# titular; um `code` de verdade é curto e quase sem dígito ("004", "CC_001",
# "INV_005", "MFA_TIMEOUT"). O teto de DÍGITOS é o que separa código de PII:
# CPF ("123.456.789-01") e conta ("0001-12345-6") têm 11 dígitos cada e passavam
# pelo formato — medido. `_` estava faltando na classe, então "CC_001", que é
# código REAL de produção, era rejeitado.
_CODE_FORMAT = re.compile(r"[A-Za-z0-9._-]{1,20}")
_CODE_MAX_DIGITS = 6


def safe_code(value: Any) -> str:
    """O `code` da Pluggy, ou "" se ele não PARECER um código.

    Fronteira de confiança: usado pelos warnings do health E pela mensagem de
    erro da API (`core/services/pluggy.py`), que vira log persistido e painel
    admin. Uma regra só para os dois — duas seriam duas chances de errar.
    """
    text = str(value if value is not None else "").strip()
    if not _CODE_FORMAT.fullmatch(text):
        return ""
    return text if sum(c.isdigit() for c in text) <= _CODE_MAX_DIGITS else ""


def _warning_codes(detail: dict) -> list[str]:
    """Só o CÓDIGO do warning entra no health — e só se ele PARECER um código.

    A mensagem da Pluggy cita nome, conta e documento do titular, e o health é
    lido pelo painel admin, pelo log e pelo snapshot que desce pro navegador.
    Cortar em 64 caracteres NÃO impede vazamento: quando `warnings` vem como
    lista de STRINGS (a Pluggy manda os dois formatos), `code` virava os
    primeiros 64 caracteres da mensagem — medido, com CPF dentro.

    Só dict com `code` no formato de código passa. Qualquer outra coisa vira um
    sentinela fixo: o painel continua sabendo que houve warning, sem carregar
    texto livre junto.

    `warnings` também pode não ser lista: medido, `{"warnings": 7}` estourava
    `TypeError` aqui e MATAVA o sync inteiro (o chamador não tem try) — um campo
    inesperado da Pluggy não pode custar a sincronização.
    """
    warnings = detail.get("warnings")
    if not isinstance(warnings, list):
        return []
    out: list[str] = []
    for w in warnings:
        code = safe_code(w.get("code")) if isinstance(w, dict) else ""
        out.append(code or "invalid_warning")
    return out


def _iso(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def derive_item_health(item: dict, *, now: datetime | None = None) -> dict:
    """Saúde por produto a partir do item cru da Pluggy.

    `stale_products` é a lista de produtos que a Pluggy NÃO conseguiu atualizar
    nesta execução — é o que separa "atualizado" de "atualizei o que deu".
    """
    item = item if isinstance(item, dict) else {}
    status_detail = item.get("statusDetail") or {}
    products: dict[str, dict] = {}
    stale: list[str] = []

    for name, key in _PRODUCT_KEYS.items():
        detail = status_detail.get(key)
        if not isinstance(detail, dict):
            continue
        updated = bool(detail.get("isUpdated"))
        products[name] = {
            "updated": updated,
            "last_updated_at": _iso(detail.get("lastUpdatedAt")),
            "warnings": _warning_codes(detail),
        }
        if not updated:
            stale.append(name)

    return {
        "observed_at": (now or datetime.now(_tz())).isoformat(),
        "item_status": (str(item.get("status") or "").upper() or None),
        "execution_status": (str(item.get("executionStatus") or "").upper() or None),
        "products": products,
        "stale_products": stale,
    }


# Motivo de quem leu pela metade. Não está em `_LABELS` de propósito: o `out()`
# do `connection_ui_state` manda motivo desconhecido para `error_recoverable`
# ("Erro temporário / Tentaremos de novo automaticamente"), que é a verdade.
READ_FAILED = "read_failed"


# Motivos que só o espelho vazio explica — os únicos que uma observação SEM
# leitura do provedor (o job de saúde) pode manter de pé.
_MOTIVOS_DE_ESPELHO_VAZIO = ("no_accounts", READ_FAILED)


def resolve_connection_state(
    *,
    health: dict | None = None,
    missing: bool = False,
    has_data: bool = True,
    leitura_completa: bool | None = None,
    reason_atual: str = "",
) -> tuple[str | None, str]:
    """`(status, status_reason)` de UMA observação — a tabela do topo do módulo.

    Único ponto do sistema que decide os dois campos, e sempre os dois juntos.
    Chamado pelo sync, pelo job de saúde e pelo 404; a reconexão (linha G) é SQL
    e mora em `save_pluggy_open_finance_item`.

    `has_data`         — o espelho AGORA (contas ou investimentos), não o histórico.
    `leitura_completa` — `True` lemos contas E investimentos; `False` a leitura
                         caiu no meio (429 em `/investments`); `None` esta
                         observação NÃO leu o provedor (job de saúde: só
                         `GET /items`). Com `None` o motivo de espelho vazio não
                         é INVENTADO — só mantido se já estava lá.
    `reason_atual`     — o motivo gravado hoje, para a decisão de manter/limpar.
    """
    if missing:
        return "ERROR", "item_missing"

    item_status = str((health or {}).get("item_status") or "").upper()
    if item_status in _NEEDS_USER or item_status == "ERROR":
        return "ERROR", ""

    # LIMITAÇÃO CONHECIDA (Onda 1, não corrigida de propósito): um `item_status`
    # de erro que não esteja em `_NEEDS_USER` nem seja "ERROR" cai adiante como
    # item saudável. Não é regressão: com `has_data=True` o resolvedor SEMPRE
    # tratou status desconhecido como saudável; esta onda só estendeu isso ao
    # `has_data=False` para destravar o ERROR terminal. Não ampliamos a lista
    # porque a taxonomia da Pluggy não é observável daqui — nenhum status desse
    # tipo aparece no repositório e ninguém tem acesso ao provedor para
    # confirmá-lo. Adivinhar a lista é pior que a lacuna. Vira item de Onda 2
    # SÓ com evidência real de um status externo assim.

    if has_data:
        return "ACTIVE", ""

    # Espelho vazio NÃO é erro do ITEM — ele respondeu, e respondeu saudável
    # (qualquer outra coisa já saiu acima). O `status` é a saúde do item; quem
    # explica o espelho vazio é o `status_reason`.
    # Devolver None aqui ("não mexe") deixava um ERROR anterior grudado: com
    # `OF_REFRESH_ENABLED` off (o default) não há PATCH → não há webhook → não há
    # sync completo, e ERROR virava TERMINAL na prática — medido, 404 no tick 0 e
    # item vivo nos 5 seguintes continuava "Erro temporário".
    if leitura_completa is None:
        motivo = str(reason_atual or "").lower()
        return "ACTIVE", (motivo if motivo in _MOTIVOS_DE_ESPELHO_VAZIO else "")
    return "ACTIVE", ("no_accounts" if leitura_completa else READ_FAILED)


def _dm(iso_text: str | None) -> str | None:
    """'2026-08-12T…' → '12/08'. Devolve None se não der pra ler."""
    if not iso_text:
        return None
    try:
        return f"{iso_text[8:10]}/{iso_text[5:7]}" if iso_text[4] == "-" and iso_text[7] == "-" else None
    except IndexError:
        return None


def _stale_detail(health: dict) -> str:
    stale = [p for p in (health.get("stale_products") or []) if p in _PRODUCT_PT]
    if not stale:
        return "Parte dos dados ainda não veio"
    nomes = [_PRODUCT_PT[p] for p in stale]
    products = health.get("products") or {}
    datas = sorted(
        d for d in (_dm((products.get(p) or {}).get("last_updated_at")) for p in stale) if d
    )
    texto = " e ".join(nomes) + (" desatualizados" if len(nomes) > 1 else " desatualizado")
    return f"{texto} desde {datas[0]}" if datas else texto


def connection_ui_state(connection_row: dict) -> dict:
    """Estado + textos de UMA conexão. Única fonte dos estados de `_LABELS`.

    Lê `status`, `status_reason`, `health` e `last_sync_at` da linha de
    `open_finance_connections`. `health` NULL significa "saúde ainda não medida"
    — NÃO "nunca sincronizou": linha legada tem last_sync_at e nenhum health.
    """
    row = connection_row if isinstance(connection_row, dict) else {}
    status = str(row.get("status") or "").upper()
    reason = str(row.get("status_reason") or "").lower()
    health = row.get("health") if isinstance(row.get("health"), dict) else None
    stale = list((health or {}).get("stale_products") or [])

    def out(state: str, detail: str | None = None) -> dict:
        # DEFAULT SEGURO: estado desconhecido nunca é verde. Um `status_reason`
        # pendente que este arquivo não conhece (gravado por um caminho novo)
        # não pode virar "Atualizado" — foi exatamente assim que `no_accounts`
        # (item vivo que não espelhou nada) chegou à tela como "Tudo em dia!".
        if state == "updated" and reason not in _REASONS_OK:
            state = reason if reason in _LABELS else "error_recoverable"
            detail = _FIXED_DETAIL.get(state)
        return {
            "state": state,
            "label": _LABELS[state],
            "detail": detail if detail is not None else _FIXED_DETAIL.get(state),
            "stale_products": stale,
        }

    if status == "DELETED":
        return out("removed")
    if status == "PAUSED":
        return out("paused")
    if reason == "item_missing":
        return out("item_missing")

    if health:
        item_status = str(health.get("item_status") or "").upper()
        if item_status in _NEEDS_USER:
            return out("needs_user_action")
        if item_status in _UPDATING:
            return out("updating")
        # ERROR vem ANTES de "parcial": item em erro COM produto atrasado é erro,
        # e rotulá-lo de "Parcial" ("atualizei o que deu") subestima o estado.
        if status == "ERROR" or item_status == "ERROR":
            # ...mas `status` local em ERROR com o ITEM vivo e o motivo dizendo
            # por que o espelho está vazio: quem explica é o motivo. O override
            # do `out()` só rodava no ramo verde, então ERROR/no_accounts nunca
            # mostrava "Sem dados" — o estado desta onda ficava invisível.
            # Item em ERROR de verdade continua "Erro temporário": motivo velho
            # não subestima erro, e este ramo nunca vira verde.
            return out("no_accounts" if reason == "no_accounts" and item_status != "ERROR"
                       else "error_recoverable")
        if stale or str(health.get("execution_status") or "").upper() == "PARTIAL_SUCCESS":
            return out("partial", _stale_detail(health))
        return out("updated")

    # Sem health medido: cai no status local (comportamento de hoje).
    if status in _UPDATING:
        return out("updating")
    if status in _NEEDS_USER:
        return out("needs_user_action")
    if status == "ERROR":
        # Mesma classe do ramo com health: o motivo explica melhor que "Erro
        # temporário" (linha legada gravada antes desta onda também cai aqui).
        return out("no_accounts" if reason == "no_accounts" else "error_recoverable")
    if row.get("last_sync_at") is None:
        return out("updating", "Ainda não sincronizou")
    return out("updated")
