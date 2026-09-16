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

# DOIS CONSUMIDORES, DUAS ENTRADAS DIFERENTES — e é por isso que estes conjuntos
# misturam campos da Pluggy de propósito. Não "limpe" a mistura sem ler isto:
#
#   • `resolve_connection_state` compara com `health["item_status"]`, que sai de
#     `item["status"]` (`derive_item_health`). Aí só cabem status de Item.
#   • `connection_ui_state` (ramo sem `health`) compara com o `status` LOCAL da
#     conexão — e esse pode receber um `executionStatus`, porque o upsert faz
#     `item.get("status") or item.get("executionStatus")` (`db/open_finance.py`).
#
# Daí `INVALID_CREDENTIALS` e `CREATED` estarem aqui: eles são `executionStatus`,
# nunca chegam pelo primeiro caminho, e são LOAD-BEARING no segundo. Medido: com
# eles fora, um `status` local `INVALID_CREDENTIALS` deixa de virar "Ação
# necessária". `tests/test_of_health.py` prende os dois.
#
# O que NÃO fazer: acrescentar `executionStatus` novo "por precaução". Os dois
# que estão aqui têm caminho medido; um terceiro sem caminho é adivinhação.

# Os dois estados de "autorize o DISPOSITIVO / leia o QR no app do banco" — a
# explicação completa (campos diferentes, páginas da doc) está no bloco do
# `_DETALHE_POR_STATUS`, abaixo. Ficam aqui em cima porque `_NEEDS_USER` precisa
# do primeiro: repetir a string crua nos dois lugares é a §0.7 sendo violada no
# mesmo arquivo que a invoca.
ITEM_STATUS_AUTORIZA_DISPOSITIVO = "WAITING_USER_ACTION"
EXEC_STATUS_AUTORIZA_DISPOSITIVO = "USER_AUTHORIZATION_PENDING"

# Por quanto tempo o `executionStatus` do `raw` (o payload cru da Pluggy, gravado
# pelo upsert) ainda descreve a autorização ATUAL. Mora aqui, ao lado dos dois
# nomes acima e pelo mesmo motivo: quem o consome é SQL (`db/open_finance_state.py`,
# e por ele o snapshot e o aviso proativo), e a regra de device/QR é deste módulo.
# O `raw` é congelado — `mark_sync_result` não o toca —, então sem prazo a
# supressão do aviso e a instrução de dispositivo durariam para sempre.
#
# 60 minutos. O PISO é ESTIMATIVA, e é preciso dizer de onde ela vem antes de
# derivar qualquer coisa dela:
#   • NÃO HÁ FONTE NA ÁRVORE PARA OS 30 MIN DA JANELA DO QR. O único registro
#     anterior a este PR é prosa num comentário vizinho — "a janela do QR
#     (~30 min)", `db/open_finance.py`, no `where` de
#     `list_connections_needing_reconnect` —, com til e sem citar página de doc.
#     O bloco do `_DETALHE_POR_STATUS`, abaixo, diz só "um `userAction.expiresAt`
#     CURTO": sem número. Escrever aqui "a doc registrada anota 30 min" foi
#     promover um `~` de um comentário irmão a fato documentado, que é a §0.7
#     começando ("foi copiando que a regra chegou a ter três versões com três
#     precisões"). Fica como o que é: número herdado, não medido e não citável.
#   • PISO: cortar antes da janela do QR tiraria a instrução CERTA de quem ainda
#     está dentro dela. Como a janela é estimada em ~30 min, o piso tem de
#     cobrir pelo menos isso.
#   • FOLGA: 60 é o DOBRO dessa estimativa — margem escolhida sobre um número
#     incerto, não derivação de um número documentado. Ela paga o fato de o
#     carimbo ser a ESCRITA do item (`reconnected_at`/`created_at`), não o
#     `userAction.expiresAt` da Pluggy — campo que NÃO existe nesta árvore
#     (`grep userAction` acha só um comentário em `frontend/settings.html`), então
#     ancorar nele seria adivinhação. Quem um dia achar a janela real na doc (ou
#     o `expiresAt` chegar ao `raw`) troca este número pelo medido: é isso que
#     fecha a estimativa, não outra rodada de aritmética sobre ela.
#   • TETO: quem reescreveria `health` é o tique de `OF_REFRESH_INTERVAL_SEC`,
#     default 6 h (`frontend/finance_bot_websocket_custom.py`). 60 min vence muito
#     antes: quem encerra a supressão é o PRAZO, não uma corrida com o tique.
#   • DESVIO DE RELÓGIO: estes 60 min cobrem UM SENTIDO SÓ — o do app ATRASADO,
#     que carimba no passado e come janela —, e o cobrem só ATÉ 30 MIN DE
#     ATRASO. Com o app atrasado em L minutos a janela efetiva é `60 - L`, então
#     em L = 30 ela empata com a estimativa de ~30 min do QR e, para L > 30, a
#     instrução certa morre com o QR AINDA ABERTO (medido: carimbo em
#     `now() - 65 min` já devolve "Reautorize o banco" com o aviso proativo
#     ligado). Com L = 45: o carimbo tem idade 60 EXATOS 15 min depois de
#     conectar e o predicado é `>` estrito, então a instrução morre aos 15 min,
#     com os outros ~15 da estimativa ainda abertos. Estes dois 15 são
#     ARITMÉTICA sobre um número estimado, não medição — e `60 - L` também.
#     O sentido oposto (app adiantado, carimbo no FUTURO) esta constante não
#     cobre e não pode cobrir: ela é o piso do intervalo. Quem o cobre é o TETO
#     do `SQL_RAW_AINDA_VALE` (`db/open_finance_state.py`), e é ele que impede o
#     carimbo no futuro de tornar a supressão permanente.
#   • A TOLERÂNCIA A RELÓGIO É ASSIMÉTRICA, 6×: 5 min para o app adiantado (o
#     teto) contra 30 min para o atrasado (os `60 - 30` de folga do piso). Não é
#     decisão tomada — é o que cai das duas pontas terem motivos diferentes (o
#     teto vem do desvio NORMAL entre app e banco, o piso da janela ESTIMADA do
#     QR). Fica escrito porque a assimetria não estava em lugar nenhum — e o
#     `6×` herda a incerteza do piso: ele é `30 / 5`, com o 30 estimado.
#   • DIREÇÃO DO ERRO: vencido o prazo, o detalhe volta a
#     `_FIXED_DETAIL["needs_user_action"]` ("Reautorize o banco"), que é a ação
#     correta depois que a janela fechou. Errar curto custa uma instrução
#     conservadora; errar longo manda a pessoa esperar um QR morto.
#
# 60 NÃO é a janela máxima: somado ao teto de 5 min do `SQL_RAW_AINDA_VALE`, o
# intervalo aceito tem 65 min de largura para um carimbo 5 min adiantado (medido:
# `now() - 60 min` FORA, `now() - 59 min` DENTRO, `now() + 5 min` DENTRO,
# `now() + 5 min 1 s` FORA). Quem lê só esta constante infere 60.
JANELA_DEVICE_AUTH_MIN = 60

# Status do item que significam "a Pluggy ainda está buscando".
_UPDATING = {"UPDATING", "CREATED"}
# Status que só o usuário resolve (reautorizar / responder MFA no banco, ou
# autorizar o dispositivo / ler o QR — ver `_DETALHE_POR_STATUS` abaixo).
_NEEDS_USER = {"LOGIN_ERROR", "WAITING_USER_INPUT", "INVALID_CREDENTIALS",
               "OUTDATED", ITEM_STATUS_AUTORIZA_DISPOSITIVO}

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

# Detalhe POR STATUS, quando o do estado manda a ação errada. `_NEEDS_USER` é um
# balde só ("Ação necessária"), mas a ação não é a mesma para todo mundo:
# autorizar o DISPOSITIVO / ler o QR no app do banco é agir AGORA, numa janela
# curta — e o detalhe fixo do estado, "reautorize o acesso", manda refazer a
# autorização daqui, que é justamente deixar a janela expirar.
#
# São DOIS valores em DOIS CAMPOS diferentes, e é por isso que não viram um
# conjunto só. Varredura das 183 páginas da doc (`docs.pluggy.ai/llms.txt`), não
# de uma tabela:
#
#   • `WAITING_USER_ACTION` é `status` de Item — Safra e Banco Inter PF
#     (`docs/connect-an-account`) e o "QR Login" do `docs/sandbox`;
#   • `USER_AUTHORIZATION_PENDING` é `executionStatus` e NUNCA `status`: o Item
#     vem com `"status": "OUTDATED"` ao lado dele (Caixa PF/PJ, autorização de
#     dispositivo com 30' de espera). Aparece em SEIS páginas —
#     `connect-an-account`, `errors-validations`, `item-lifecycle`, `webhooks`,
#     `sandbox` e `environments-and-configurations`. Como `OUTDATED` já está em
#     `_NEEDS_USER`, o BALDE sempre acertou; era só o DETALHE que errava, e por
#     isso acrescentá-lo a `_NEEDS_USER` seria o `executionStatus` "por
#     precaução" que o bloco lá em cima proíbe. Codex do @hiago no #166.
#
# Os dois nomes são exportados porque `list_connections_needing_reconnect`
# (`db/open_finance.py`) precisa PULAR estas conexões: o aviso proativo manda
# "reconecte seu banco", o único caminho que faz PERDER a janela.
# SÓ É LIDO no ramo `needs_user_action` (abaixo, nos dois caminhos), via
# `_detalhe_de_acao`. As DUAS chaves são load-bearing, e por motivos diferentes:
# a de `item_status` porque está em `_NEEDS_USER`; a de `execution_status`
# porque é consultada como SEGUNDO campo, e de propósito NÃO está em
# `_NEEDS_USER` (`tests/test_of_health.py` prende a ausência). Uma versão
# anterior deste comentário dizia que chave fora de `_NEEDS_USER` era código
# morto — o que mandava apagar exatamente a chave que o conserto acabara de
# acrescentar. Não é: o que é código morto aqui é chave que nenhum dos dois
# campos pode assumir.
_DETALHE_POR_STATUS = {
    ITEM_STATUS_AUTORIZA_DISPOSITIVO: "Autorize o acesso no app do banco",
    EXEC_STATUS_AUTORIZA_DISPOSITIVO: "Autorize o acesso no app do banco",
}


def _detalhe_de_acao(item_status: str, execution_status: str = "") -> str | None:
    """A instrução específica quando "Ação necessária" sozinha mandaria a errada.

    Consulta os DOIS campos porque os dois estados de device/QR chegam por
    campos diferentes (ver acima).

    OS DOIS RAMOS CONVERGEM, e o de baixo levou um PR para chegar aqui. Sem
    `health` não existe `execution_status` na linha, então o ramo sem health do
    `connection_ui_state` só tinha o primeiro campo — e ali a Caixa (`status`
    local `OUTDATED`, porque o upsert grava `item['status'] or
    item['executionStatus']`) ouvia o detalhe fixo "Reautorize o banco": mesmo
    estado, mesmo rótulo, instrução OPOSTA à do ramo com health. Não era sobra de
    linha antiga — é o caminho de TODA conexão recém-gravada (o upsert zera
    `health`, e o `POST /pluggy-item` monta o snapshot antes de o sync de fundo
    escrever saúde).

    De onde vem o segundo campo agora: de um DERIVADO calculado em SQL
    (`db/open_finance_state.py`), lido do `raw` que o upsert já persiste e válido
    só enquanto `health is null` E o carimbo da autorização atual couber em
    `JANELA_DEVICE_AUTH_MIN`. Esta função continua PURA: recebe `execution_status`
    na linha, não consulta banco nem relógio. O `raw` inteiro NÃO viaja — só o
    escalar — porque ele carrega `clientUserId`.

    Precedência: o `item_status` ganha. As duas diagonais fora do par medido,
    enumeradas porque enumerar só a inofensiva foi apontado:

      • `_NEEDS_USER` (ex.: `LOGIN_ERROR`) + `execution_status` de device/QR →
        mostra a instrução de dispositivo. Benigna: o BALDE continua certo, só o
        detalhe é discutível;
      • `_UPDATING` (`UPDATING`/`CREATED`) + `execution_status` de device/QR →
        esta função NÃO É CHAMADA em nenhum dos dois lados. Sem sync,
        `connection_ui_state` testa `_UPDATING and sem_sync` antes e devolve
        "Atualizando…" direto; COM sync ele desce para os ramos de dado, que
        também não consultam o detalhe de ação. A instrução de dispositivo
        continua não aparecendo — o que o `and sem_sync` mudou foi só o rótulo
        do lado já sincronizado, que deixou de ser a mentira de fiapo girando.
        Ainda assim vale dizer o que sustenta não fechar a diagonal.

    O que sustenta: a varredura das 183 páginas (`docs.pluggy.ai/llms.txt`) achou
    `USER_AUTHORIZATION_PENDING` em seis páginas, e em TODAS o `status` ao lado é
    `OUTDATED` — nenhuma o pareia com `UPDATING` ou `CREATED`. Fechar a diagonal
    exigiria consultar `execution_status` antes do teste de `_UPDATING`, que é
    mudar a ordem da máquina de estados por um par que a doc não produz — o
    "`executionStatus` por precaução" que o bloco do módulo proíbe. Se algum dia
    aparecer, o conserto é ESTE, e ele vai bater no teste que prende a ausência
    em `_NEEDS_USER`/`_UPDATING`: isso é sinal, não regressão.
    """
    return (_DETALHE_POR_STATUS.get(item_status)
            or _DETALHE_POR_STATUS.get(execution_status))

_FIXED_DETAIL = {
    "error_recoverable": "Tentaremos de novo automaticamente",
    # DUAS superfícies leem esta frase: a linha da conexão ("Ação necessária" +
    # esta linha) e o toast do refresh ("Ação necessária no Nubank: reautorize o
    # acesso."). Era "Reautorize o banco", que no toast virava a instrução mais
    # fraca do caso MAJORITÁRIO — quem cai aqui é `_NEEDS_USER` menos os casos de
    # device/QR, que o `_detalhe_de_acao` desvia antes. Atenção: `OUTDATED` cai
    # nos DOIS lados — sozinho é reautorização, acompanhado de
    # `execution_status = USER_AUTHORIZATION_PENDING` é dispositivo.
    # Uma frase só nas duas superfícies: não duplique instrução no JS.
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

    # ONDA 3 — a lacuna da Onda 1 foi REAVALIADA com doc oficial, e o resultado
    # tem duas metades. A tabela "Item Status" de
    # https://docs.pluggy.ai/docs/item-lifecycle lista cinco valores — UPDATED /
    # UPDATING / WAITING_USER_INPUT / LOGIN_ERROR / OUTDATED — e os cinco estão
    # cobertos e presos por teste (`tests/test_of_health.py`).
    #
    # A outra metade: **aquela tabela não é a enumeração fechada do campo.** Um
    # SEXTO valor aparece em payloads de Item completos em duas páginas vigentes
    # da mesma doc — `docs/connect-an-account` (Safra e Banco Inter PF) e
    # `docs/sandbox` (fluxo "QR Login"):
    #
    #     "status": "WAITING_USER_ACTION", "executionStatus": "WAITING_USER_ACTION"
    #
    # Ele significa "o usuário precisa autorizar o dispositivo / ler o QR no app
    # do banco", com um `userAction.expiresAt` curto. Estava em `_UPDATING`, e
    # por isso a tela dizia "Atualizando…" numa conexão que só anda se a pessoa
    # AGIR — a mesma mentira de fiapo girando que a Onda 1 existiu para matar.
    # Movido para `_NEEDS_USER`, junto do irmão `WAITING_USER_INPUT`, que já
    # estava lá. É a única mudança de comportamento desta onda.
    #
    # A lição fica: a lista de cinco veio de UMA tabela, e a tabela não é a
    # fonte. O OpenAPI de `reference/items-retrieve` declara `status` como string
    # SEM `enum`, então nenhuma leitura de doc fecha esse campo.
    #
    # `item_status` aqui tem DUAS origens, e só duas: `item["status"]` (em
    # `derive_item_health`) e o `MISSING` que nós mesmos sintetizamos em
    # `_HEALTH_MISSING`
    # (`pluggy_sync.py`) — este último sempre pareado com
    # `status_reason='item_missing'`, que `connection_ui_state` trata antes de
    # olhar o `item_status`. `MERGE_ERROR` e companhia são `executionStatus` e
    # não chegam por nenhuma das duas (ver o bloco em cima de `_UPDATING`, que
    # explica por que os conjuntos ainda assim têm membros daquele campo).
    #
    # Duas limitações sobram, e são de revisão, não de código:
    #   • status de Item fora dos SEIS conhecidos — seja publicado depois desta
    #     leitura, seja já existente numa página que ninguém varreu — cai
    #     adiante como saudável. Foi assim que o sexto passou despercebido;
    #   • um `executionStatus` de erro que caia no `status` LOCAL pela via do
    #     upsert e não esteja nos conjuntos lê como saudável ali. Medido:
    #     `MERGE_ERROR` no status local devolve "Atualizado" — MAS só com
    #     `last_sync_at` preenchido E `reconnected_at` NULL, combinação que o
    #     upsert de hoje não produz (o ramo de conflito sempre carimba
    #     `reconnected_at`; o de insert nasce com `last_sync_at` NULL). É
    #     armadilha latente, não sangramento.

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

    # "Sincronizou" tem que significar "sincronizou DEPOIS da autorização atual".
    # O upsert preserva o `last_sync_at` velho numa reconexão de propósito
    # (reconectar não é sincronizar), então só olhar se ele existe deixava o
    # espelho ANTERIOR à reconexão voltar à tela como "Atualizado" assim que o
    # job de saúde media o item novo como saudável — apontado pelo Codex no #162.
    ultimo, religado = row.get("last_sync_at"), row.get("reconnected_at")
    sem_sync = ultimo is None or (religado is not None and ultimo < religado)

    # Item em coleta cujo health não traz informação de produto NENHUMA:
    # `derive_item_health` pula o produto cujo `statusDetail` não veio
    # (`if not isinstance(detail, dict): continue`), então `products` vazio não é
    # "nada atrasado", é "não sei". E o job de saúde sobrescreve `health` sem
    # condição — um "Parcial — Cartão desatualizado desde 12/08" vira health sem
    # `statusDetail` assim que o item entra em `UPDATING`, e o aviso do cartão
    # sumiria com a tela dizendo "Atualizado". Só o VERDE é interceptado (no
    # `out()`, depois do motivo pendente): "Atualizando…" é o que a base dizia
    # e é a resposta honesta para o que não se mediu.
    # LIMITE CONHECIDO, e é o mesmo cenário por outra porta: a guarda pega
    # "nenhuma informação de produto", não "informação a menos". Se a foto nova
    # trouxer só `accounts`, o cartão que estava atrasado na foto ANTERIOR some
    # dela, `stale_products` fica vazio e o card vira "Atualizado". Fechar isso
    # exige comparar com a foto anterior — que o job de saúde sobrescreve — e
    # ficou para a issue #444.
    coletando_sem_info = (str((health or {}).get("item_status") or "").upper() in _UPDATING
                          and not (health or {}).get("products"))

    def out(state: str, detail: str | None = None) -> dict:
        # DEFAULT SEGURO: estado desconhecido nunca é verde. Um `status_reason`
        # pendente que este arquivo não conhece (gravado por um caminho novo)
        # não pode virar "Atualizado" — foi exatamente assim que `no_accounts`
        # (item vivo que não espelhou nada) chegou à tela como "Tudo em dia!".
        if state == "updated" and reason not in _REASONS_OK:
            state = reason if reason in _LABELS else "error_recoverable"
            detail = _FIXED_DETAIL.get(state)
        # ONDA 2: "Atualizado" exige SYNC REAL, não só item saudável. O job de
        # saúde grava `health` com `ok=None` (só faz GET /items, nunca toca em
        # last_sync_at), então um banco recém-conectado/reconectado caía no ramo
        # do health e ficava verde antes de qualquer sync — com "Última sync:
        # pendente" logo abaixo, na mesma linha da tela.
        # Vem DEPOIS do default seguro, e a ordem foi medida: com ela na frente,
        # `no_accounts` e `read_failed` de uma conexão nova (que também têm
        # last_sync_at NULL, porque `mark_sync_result(ok=False)` não carimba)
        # perdiam o motivo e viravam "Atualizando…" — "Sem dados" sumia da tela e
        # a pílula do `read_failed` descia de vermelho para âmbar. Só o verde SEM
        # motivo é que vira "Ainda não sincronizou".
        elif state == "updated" and sem_sync:
            state, detail = "updating", "Ainda não sincronizou"
        # Mesma família da linha de cima, e no mesmo lugar de propósito: DEPOIS
        # do motivo pendente (`no_accounts`/`read_failed`/desconhecido continuam
        # falando primeiro) e só contra o verde. Sem `detail`: é o "Atualizando…"
        # seco da base, não o "Ainda não sincronizou" — aqui já se sincronizou.
        elif state == "updated" and coletando_sem_info:
            state, detail = "updating", None
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
            return out("needs_user_action", _detalhe_de_acao(
                item_status, str(health.get("execution_status") or "").upper()))
        # `and sem_sync`: coleta de banco real demora MUITO mais que o sync, então
        # o item fica em `UPDATING` depois de o espelho já estar escrito — e o card
        # dizia "Atualizando…" para sempre em cima de dado importado e de um
        # `last_sync_at` carimbado. Com sync posterior à autorização atual, quem
        # fala é o ESTADO DO DADO (ramos abaixo: "Atualizado"/"Parcial"/"Sem
        # dados"/"Erro temporário"). Sem sync — 1ª conexão ou reconexão ainda não
        # espelhada — "Atualizando…" é verdade e continua sendo a guarda que
        # impede o card de dizer "tudo em dia" com espelho vazio.
        if item_status in _UPDATING and sem_sync:
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
        # O status LOCAL também pode trazer `WAITING_USER_ACTION` (o upsert grava
        # `item.get("status") or item.get("executionStatus")`), então o detalhe
        # específico vale nos dois ramos.
        # O SEGUNDO campo é o derivado do `raw` (`db/open_finance_state.py`), que
        # só existe enquanto `health` é NULL e a autorização atual está dentro de
        # `JANELA_DEVICE_AUTH_MIN`. É o que faz a Caixa (`status='OUTDATED'` +
        # `executionStatus='USER_AUTHORIZATION_PENDING'`) dizer "Autorize o acesso
        # no app do banco" aqui também, e não o oposto do ramo com health.
        # Ausente (linha de outra query, prazo vencido, `raw` sem o campo) → "",
        # que é o default do parâmetro: o detalhe cai em "Reautorize o banco".
        return out("needs_user_action", _detalhe_de_acao(
            status, str(row.get("execution_status") or "").upper()))
    if status == "ERROR":
        # Mesma classe do ramo com health: o motivo explica melhor que "Erro
        # temporário" (linha legada gravada antes desta onda também cai aqui).
        return out("no_accounts" if reason == "no_accounts" else "error_recoverable")
    if ultimo is None:
        # `ultimo is None`, NÃO `sem_sync`: este early-return pula o default
        # seguro do `out()` — é assim na base, e por isso ele só pode valer no
        # escopo EXATO que a base lhe dava ("nunca sincronizou"). Alargá-lo para
        # `sem_sync` fez o caso da reconexão descartar `no_accounts`,
        # `read_failed` e motivo desconhecido: medido, 60 combinações com perda,
        # e a pílula do `read_failed` caindo de vermelho para âmbar logo depois
        # de o usuário reconectar e o sync falhar. Quem reconectou desce pelo
        # `out("updated")` abaixo, onde o motivo fala primeiro e o `sem_sync` só
        # decide o que restar de verde.
        return out("updating", "Ainda não sincronizou")
    return out("updated")
