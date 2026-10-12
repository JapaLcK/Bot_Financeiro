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
| P | sync: contas lidas, `/investments` falhou             | ACTIVE  | investments_|
|   | (espelho cheio; a tela diz "Parcial")                 |         | read_failed |
| F | sync com leitura COMPLETA e espelho cheio             | ACTIVE  | ""          |
| H | job de saúde: item vivo (não leu `/accounts`, então   | ACTIVE  | mantém D/P  |
|   | não INVENTA nem APAGA falha de leitura)               |         | sempre, E só|
|   |                                                       |         | c/ espelho  |
|   |                                                       |         | vazio; senão|
|   |                                                       |         | ""          |
| G | reconexão pelo widget (`save_pluggy_open_finance_item`)| remoto | "" + health |
|   |                                                       |         | zerado      |
| O | sync: `GET /items` ok e o run falhou DEPOIS (D com a  | ACTIVE  | read_failed |
|   | foto 1; B/C se a foto diz). Grava a foto, e só se     | (ERROR) | ("")        |
|   | ninguém observou o item desde o começo do run         |         |             |

B e C devolvem motivo vazio de propósito: quem conta a história ali é o
`health`, e o motivo velho (`item_missing` de ontem) só atrapalharia.

E ≠ D: "li e veio vazio" não é "não consegui ler". Só o primeiro autoriza
`no_accounts` — foi confundir os dois que fez um 429 em `/investments` descartar
contas já lidas. P é D com contas: antes caía em F e a tela dizia "Atualizado"
sem os investimentos (Onda 5, R4). O job de saúde (H) não limpa D nem P
(Onda 5, R5): quem limpa é uma leitura completa (E/F), ou B/C/G.

H é o que tira o caráter pegajoso de `no_accounts`: `has_data` é OBSERVAÇÃO (o
job pergunta ao espelho em `list_connections_for_health_check`, não à memória),
então o motivo cai sozinho no instante em que existir dado — e o job nunca cria
um motivo sobre uma leitura que ele não fez.

G é o único evento que zera o `health`: consentimento novo torna a medição velha
sem sentido (com ela, um `item_status: MISSING` de antes ainda pintava a tela).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from utils_date import _tz, day_tz

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

# Por quanto tempo a instrução de dispositivo/QR ainda descreve a autorização
# ATUAL — com ou sem `health` (Onda 5, D5). Mora aqui, ao lado dos dois nomes
# acima e pelo mesmo motivo: quem o consome é SQL (`db/open_finance_state.py`:
# `SQL_EXECUTION_STATUS` e `SQL_DEVICE_NA_JANELA`, lidos pelo snapshot e, por ele,
# pelo aviso proativo), e a regra de device/QR é deste módulo. Nem o `raw` nem o
# `health` trazem prazo (o `raw` é congelado; o job regrava o `health` com o mesmo
# `execution_status`), então sem prazo a supressão do aviso e a instrução de
# dispositivo durariam para sempre.
#
# 60 minutos. O PISO é ESTIMATIVA, e é preciso dizer de onde ela vem antes de
# derivar qualquer coisa dela:
#   • NÃO HÁ FONTE NA ÁRVORE PARA OS 30 MIN DA JANELA DO QR. O único registro
#     anterior a este PR é prosa num comentário vizinho — "a janela do QR
#     (~30 min)", `db/open_finance.py`, no `where` de
#     `list_connections_needing_reconnect` (removido com o SQL no PR-D da Onda 5) —,
#     com til e sem citar página de doc.
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
#     do `SQL_JANELA_DEVICE` (`db/open_finance_state.py`; vale para o
#     `execution_status` e para o `device_na_janela`), e é ele que impede o
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
# 60 NÃO é a janela máxima: somado ao teto de 5 min do `SQL_JANELA_DEVICE`, o
# intervalo aceito tem 65 min de largura para um carimbo 5 min adiantado (medido:
# `now() - 60 min` FORA, `now() - 59 min` DENTRO, `now() + 5 min` DENTRO,
# `now() + 5 min 1 s` FORA). Quem lê só esta constante infere 60.
JANELA_DEVICE_AUTH_MIN = 60

# Status do item que significam "a Pluggy ainda está buscando".
_UPDATING = {"UPDATING", "CREATED"}
# Lista de permissão de `mesclar_health_em_coleta`: só uma foto ANTERIOR nestes
# estados pode doar produto para a coleta nova — status desconhecido não mescla.
_MESCLA_PERMITE_ANTERIOR = _UPDATING | {"UPDATED"}
# Status que só o usuário resolve (reautorizar / responder MFA no banco, ou
# autorizar o dispositivo / ler o QR — ver `_DETALHE_POR_STATUS` abaixo).
_NEEDS_USER = {"LOGIN_ERROR", "WAITING_USER_INPUT", "INVALID_CREDENTIALS",
               "OUTDATED", ITEM_STATUS_AUTORIZA_DISPOSITIVO}

# LISTA DE PERMISSÃO DA FRONTEIRA (#539, A1): o que um payload da Pluggy pode
# virar na coluna `status` de `open_finance_connections`. Quem aplica é
# `save_pluggy_open_finance_item` (`db/open_finance.py`), o ÚNICO ponto da árvore
# que grava status REMOTO — os outros escritores da coluna gravam valor NOSSO:
# `pause_open_finance_connection` (PAUSED), o mapa literal do webhook
# (`update_pluggy_open_finance_item_status`: só DELETED desde o PR-C2), o
# `resolve_connection_state` via `mark_sync_result` (ACTIVE/ERROR) e o mock
# (ACTIVE, provider `mock_pluggy`).
#
# Existe porque `PAUSED` e `DELETED` são sentinelas LOCAIS e TERMINAIS
# (`db/open_finance_state._TERMINAL`), e `PAUSED` quer dizer "o item já foi
# deletado na Pluggy no vencimento do trial": é ele que TIRA o item do DELETE
# remoto da exclusão de conta (`db/open_finance_state.pluggy_items_a_deletar`) e
# da enumeração (`list_pluggy_item_ids`). Sem a lista, um `{"status": "PAUSED"}`
# do provedor deixava o item VIVO e pago na Pluggy depois de uma exclusão LGPD —
# e as duas guardas dessa exclusão são cegas juntas, porque leem o mesmo filtro.
#
# É lista de PERMISSÃO e não de bloqueio das duas sentinelas: `ACTIVE` e elas são
# vocabulário NOSSO, e nenhum payload pode reivindicar nenhum dos três. O
# conteúdo é a união dos conjuntos acima — os status que este módulo já trata por
# nome —, mais `ERROR` (o que o resolvedor e a pista do webhook gravam, e o que `of_health_counters` conta)
# e o `executionStatus` de dispositivo, que a fronteira também aceita pelo
# `item['status'] or item['executionStatus']`. Não acrescente status "por
# precaução": vale aqui o mesmo veto do bloco de `_NEEDS_USER`.
#
# POR QUE ESTA LEITURA DIVERGE DA DE `derive_item_health`, e a divergência é
# DELIBERADA (#539, ressalva 6 do Manager). O upsert lê `item["status"] or
# item["executionStatus"]`; `derive_item_health` lê `item["status"]` e SÓ ele, e
# isso está preso em
# `tests/test_of_health.py::test_execution_status_de_erro_nao_e_status_de_item`.
# Não é a mesma pergunta respondida de dois jeitos:
#   • `derive_item_health` tem DOIS campos de saída (`item_status` e
#     `execution_status`) e consumidores que os leem separados — misturar LÁ
#     corromperia um campo que tem casa própria;
#   • a coluna `status` tem UM slot só. Sem o `or`, o `executionStatus` de um
#     payload SEM `status` não vai a lugar nenhum.
# E o `or` NÃO amplia o vocabulário da coluna: depois desta lista, dos
# `executionStatus` documentados só entram os que ela já aceita — os que este
# módulo sabe pintar (`CREATED` → "Atualizando…", `INVALID_CREDENTIALS` → "Ação
# necessária", `USER_AUTHORIZATION_PENDING` → "Autorize o acesso no app do
# banco"). `PARTIAL_SUCCESS`, `MERGE_ERROR`, `SITE_NOT_AVAILABLE`,
# `INVALID_CREDENTIALS_MFA` e `USER_AUTHORIZATION_NOT_GRANTED` viram `UPDATING` —
# exatamente o que virariam SEM o `or`, porque quem os deixa sem estado é o
# `status` AUSENTE, não o fallback. O preço (a tela dizendo "Atualizando…" para um
# item cuja última execução falhou) é o MESMO nas duas opções e só existe em
# payload sem `status`; tirar o `or` só perderia os três de cima.
#
# `DELETED` fica de FORA de propósito, e a consequência é conhecida: um
# `status: "DELETED"` REMOTO vira `UPDATING` e o agendador deixa de ver terminal
# (`db/open_finance_state._TERMINAL`), continuando a pedir sync. Aceita-se porque:
#   • item apagado na Pluggy não chega aqui como `"DELETED"` — o `GET /items/{id}`
#     responde 404, que esta árvore já trata (linha A da tabela de estados:
#     `item_missing` → ERROR). Quem grava DELETED de verdade é LOCAL e não passa
#     por esta fronteira: o mapa do webhook `item/deleted`
#     (`frontend/routes/open_finance.py:1959` →
#     `update_pluggy_open_finance_item_status`);
#   • o oposto custa mais: TERMINAL é irreversível para quem escreve depois
#     (`mark_sync_result` tem `where ... not in _TERMINAL`), então um payload que
#     pudesse gravar `DELETED` congelaria uma conexão VIVA;
#   • o custo do nosso lado é um ciclo de sync a mais, que termina em 404 →
#     `item_missing`. Nenhum item fica vivo e pago na Pluggy por isso: quem decide
#     o DELETE remoto (`pluggy_items_a_deletar`) exclui `PAUSED`, não `DELETED`.
STATUS_REMOTOS_ACEITOS = frozenset(
    _MESCLA_PERMITE_ANTERIOR | _NEEDS_USER | {"ERROR", EXEC_STATUS_AUTORIZA_DISPOSITIVO}
)

_LABELS = {
    "updated": "Atualizado",
    "partial": "Dados parciais",
    "updating": "Atualizando…",
    "error_recoverable": "Erro temporário",
    "needs_user_action": "Ação necessária",
    "item_missing": "Conexão perdida",
    "paused": "Pausado",
    "removed": "Removido",
    "no_accounts": "Sem dados",
}

_CONTEXTO_DADOS_PARCIAIS = (
    "Banco conectado. Fechar o app ou bloquear a tela não cancela a autorização."
)

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
# O aviso proativo ("reconecte seu banco", o único caminho que faz PERDER a
# janela) PULA estas conexões dentro do prazo pelo DETALHE, não por estes nomes:
# `avisa_reconectar`, abaixo, compara com `_AUTORIZE_NO_APP`.
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

    O PRAZO dos DOIS campos (D5) não é daqui: `connection_ui_state` só a chama com
    `device_na_janela` verdadeiro, nos dois ramos. Fora da janela o detalhe é o
    fixo, "Reautorize o banco".

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


def avisa_reconectar(ui: dict) -> bool:
    """A regra do aviso "reconecte" (Onda 5, D4): a TELA decide, aqui só se filtra.

    Avisa `item_missing` e `needs_user_action` cujo detalhe NÃO é o de
    dispositivo/QR (mandar reconectar faz perder a janela, Codex #166). Erro
    transitório, "Sem dados" (DP1 = A) e o resto não avisam.
    """
    return ui.get("state") == "item_missing" or (
        ui.get("state") == "needs_user_action" and ui.get("detail") != _AUTORIZE_NO_APP)


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

# Código do warning → POR QUE o produto não veio, em frase NOSSA.
#
# A mensagem da Pluggy (`message`/`providerMessage`) cita nome, conta e documento
# do titular e por isso é descartada no `_warning_codes` — decisão que não muda.
# Só que aí o motivo virava um código que nenhuma superfície mostra. Relato do
# dono (não medido — o SELECT em produção foi negado): investimentos com 30
# warnings de código relatado como `004`, prefixo não confirmado, e ninguém soube
# por que não vieram. Traduzir o código é a saída que não carrega PII.
#
# Agrupado por AÇÃO do usuário, não por produto: o que muda a frase é o que ele
# pode fazer, e o mesmo motivo cai em conta, cartão e investimento.
#
# Fonte: docs.pluggy.ai/docs/warnings-status-codes (relido em 16/09/2026; remeça
# antes de reusar). SÓ conector Open Finance entra: os códigos de conector
# DIRETO são nus (`001`, `002`, `003`) e significam coisa diferente em cada
# conector — o `001` do Itaú PJ não é o do Santander PJ —, então mapeá-los seria
# chutar. Eles caem no fallback do `_frase_do_codigo`, que é honesto.
# `LOAN_*` e `ID_*` ficam de fora porque `_PRODUCT_KEYS` não lê esses produtos:
# entrada para código inalcançável é o código morto que este módulo já proíbe.
#
# A LINHA QUE DECIDE QUEM ENTRA: a frase fala do PRODUTO ("o cartão não veio"),
# então só entra código cuja linha da doc também fala do produto ou do recurso
# inteiro — ou tem frase ESTREITA o bastante para dizer só o que a doc diz
# (`INV_003`/`INV_005`). Código que fala de um SUB-DADO que a frase não nomeia —
# `ACCT_005` (limites de cheque especial), `CC_005` (faturas), `CC_006`
# (transações do cartão), `CC_007` (o limite do cartão) — fica FORA e cai no
# fallback, junto de `ACCT_006` (corte acima de 260 contas), `TXN_002` ("no
# accounts available") e `TXN_005` ("accounts step had errors"), que não têm
# ação de usuário nenhuma.
# Motivo: dizer "não adianta tentar de novo" por causa do `CC_007` condena um
# cartão cuja fatura e cujas transações vêm normalmente. Frase que mente é pior
# que código cru — o código cru pelo menos é honesto sobre a ignorância.

# Mesma instrução que `_DETALHE_POR_STATUS` já dá para `WAITING_USER_ACTION` —
# `ACCT_002`/`CC_002` são a mesma família ("awaiting user authorization at the
# financial institution"). Derivada, não copiada: duas cópias da instrução são
# duas chances de uma envelhecer (§0.7). Minúscula porque aqui ela entra no meio
# da frase, igual ao que o `ofInstrucao` do settings.html faz no toast.
_AUTORIZE_NO_APP = _DETALHE_POR_STATUS[ITEM_STATUS_AUTORIZA_DISPOSITIVO]

# A ORDEM DOS GRUPOS É A PRIORIDADE quando um produto (ou o sujeito) traz mais de
# um código: vence a instrução que ainda RECUPERA dado. Esconder "autorize" atrás
# de "não adianta" faz perder um cartão que viria. Por isso: agir agora >
# reconectar > esperar > desistir — com UMA exceção dentro do mesmo produto, que
# mora no `_motivo_do_warning` porque reconectar ali não é "um passo a mais": gasta
# a cota do mês (ver `_RECONECTE`).
_RECONECTE = "você não liberou esse dado ao conectar o banco, reconecte para liberar"
_LIMITE_DE_CONSULTAS = ("o banco bateu o limite de consultas do Open Finance, volta "
                        "sozinho na virada do período")

_MOTIVO_POR_WARNING = {
    code: frase
    for frase, codes in (
        # "Resource Status" tem TRÊS estados, e ler dois deles como um só foi o
        # defeito da rodada 1: "Pending Authorization: awaiting user
        # authorization at the financial institution" manda AGIR no app do
        # banco; "Temporarily Unavailable (e.g. maintenance)" manda ESPERAR;
        # "Unavailable: permanently unavailable" manda desistir. Instruções
        # opostas — colapsá-las deixa o usuário esperando para sempre.
        (_AUTORIZE_NO_APP[0].lower() + _AUTORIZE_NO_APP[1:],
         ("ACCT_002", "CC_002")),
        # "User hasn't granted permission to collect <produto> (ACCOUNTS_ALL /
        # CREDIT_CARDS_ALL / ACCOUNTS_TRANSACTIONS / INVESTMENTS_ALL)" e
        # `INV_002` "Investment product permission has not been granted" — é
        # permissão, e reconectar é o caminho que a repara.
        # `INV_002` entra e `CC_005`/`CC_006`/`ACCT_005` não. O que é da doc: os
        # três nomeiam a permissão de um DADO ANEXO a um registro que vem pela
        # `_ALL` (CREDIT_CARDS_BILLS, CREDIT_CARDS_TRANSACTIONS, ACCOUNTS_LIMITS),
        # então não explicam o cartão/conta atrasado; `INV_002` não nomeia dado
        # anexo nenhum, só "investment product". O que é LEITURA nossa, não da
        # doc: que esse "product" é o tipo da própria posição de investimento, e
        # por isso sem a permissão é o dado do produto que falta.
        (_RECONECTE,
         ("ACCT_001", "CC_001", "TXN_001", "TXN_004", "INV_001", "INV_002")),
        # INV_004 é "Open Finance monthly rate limit reached"; TXN_003/TXN_006 a
        # doc chama só de "rate limit reached" — daí "período", e não "mês".
        (_LIMITE_DE_CONSULTAS,
         ("INV_004", "TXN_003", "TXN_006")),
        ("o banco não liberou esse dado agora, deve voltar sozinho em algumas horas",
         ("ACCT_003", "CC_003")),
        ("o banco não envia esse dado por aqui, não adianta tentar de novo",
         ("ACCT_004", "CC_004")),
        # "Investment product not supported by the financial institution" /
        # "Specific investment product type not supported": nunca vem, mas é um
        # TIPO de investimento — a frase de cima condenaria os investimentos todos.
        ("o banco não oferece esse tipo de investimento por aqui",
         ("INV_003", "INV_005")),
    )
    for code in codes
}
_ORDEM_DAS_FRASES = list(dict.fromkeys(_MOTIVO_POR_WARNING.values()))

# A FORMA documentada de um código, e ela decide o que vai pra TELA — não o que
# se armazena, que continua sendo assunto do `safe_code`. Duas regras de
# propósito: `safe_code` aceita "1234-5" (6 caracteres, 5 dígitos), que é a cara
# de um número de conta. Dormente enquanto o código só existia num JSON que
# ninguém lia; imprimi-lo promove isso a vazamento visível.
# `[0-9]` e não `\d`: `\d` casa dígito Unicode, e este repo já pagou por isso
# (`isdigit()` não-ASCII virando 500, issue #365). Hoje o `safe_code` é ASCII, o
# que torna o caso inalcançável; a diferença é de um caractere.
_CODE_EXIBIVEL = re.compile(r"[A-Z]{2,5}_[0-9]{3}|[0-9]{3}")


def _frase_do_codigo(code: Any) -> tuple[int, str] | None:
    """`(prioridade, frase)` de UM código — menor vence —, ou None.

    O código CRU tem a pior prioridade de todas: ele só aparece quando não há
    motivo que a gente saiba explicar. E a escolha é por prioridade, não pela
    posição: o resultado não pode depender da ORDEM em que a Pluggy manda a lista
    — com "o primeiro que achar", `[004, CC_001]` e `[CC_001, 004]` davam frases
    diferentes para o mesmo produto.
    """
    if not isinstance(code, str):
        return None
    frase = _MOTIVO_POR_WARNING.get(code)
    if frase:
        return _ORDEM_DAS_FRASES.index(frase), frase
    # Código desconhecido aparece CRU: foi exatamente o que faltou no caso do
    # dono. Sem diagnóstico inventado, e só se tiver forma de código.
    if _CODE_EXIBIVEL.fullmatch(code):
        return len(_ORDEM_DAS_FRASES), f"o banco avisou com o código {code}, sem explicar o motivo"
    return None


def _motivo_do_warning(health: dict | None, produtos) -> str:
    """Cláusula " — <por que não veio>" para uma frase cujo sujeito é `produtos`.

    O SUJEITO E O MOTIVO TÊM DE CASAR. `_stale_detail` monta um sujeito plural
    ("Cartão e Investimentos desatualizados"), e grudar ali a frase do PRIMEIRO
    código atribuía o motivo de um produto ao outro: com `CC_001` no cartão e
    `INV_003` (não suportado pela instituição) nos investimentos, a linha mandava
    reconectar para liberar um dado que nunca ia vir. Por isso:

      • motivo igual para TODOS os produtos do sujeito → cláusula sem nome;
      • qualquer outro caso (motivos diferentes, ou produto sem warning nenhum)
        → a cláusula NOMEIA o produto de quem ela é.

    Sem warning a frase sai IDÊNTICA à de hoje — é o controle positivo, e
    `tests/test_of_refresh_response.py` já o prende com `==`.

    Defensivo de propósito: esta função lê a coluna `health` (jsonb), e os
    chamadores de `connection_ui_state` não têm `try` (`db/open_finance.py`,
    `pluggy_sync.py`). É a mesma classe que o `_warning_codes` já documenta —
    `{"warnings": 7}` estourando `TypeError` e MATANDO o sync.
    """
    products = health.get("products") if isinstance(health, dict) else None
    if not isinstance(products, dict):
        return ""

    achados = []
    for produto in produtos:
        detalhe = products.get(produto)
        if detalhe is None:
            # Produto que a Pluggy nem reportou não tem motivo a dar — e contá-lo
            # como "sem motivo" forçava o nome do produto na cláusula de todo
            # banco sem investimentos.
            continue
        codes = detalhe.get("warnings") if isinstance(detalhe, dict) else None
        candidatos = [c for c in map(_frase_do_codigo, codes) if c] \
            if isinstance(codes, list) else []
        # EXCEÇÃO, só dentro do mesmo produto: limite vence "reconecte". A cota é
        # por CPF + instituição + produto, CRIAR ITEM a consome, e a doc
        # (docs.pluggy.ai/en/docs/open-finance/rate-limits) diz que conectar o
        # mesmo CPF à mesma instituição com vários itens "you will reach the
        # limitation of Open Finance faster". Com o produto parado pelo limite,
        # reconectar não o traz de volta e gasta a cota. Entre produtos NÃO vale
        # (decisão do dono): o outro produto precisa mesmo da permissão, e a frase
        # já sai nomeada.
        if any(frase == _LIMITE_DE_CONSULTAS for _, frase in candidatos):
            candidatos = [c for c in candidatos if c[1] != _RECONECTE]
        # `min`, não "o primeiro": a ordem da lista é da Pluggy, a prioridade é nossa.
        melhor = min(candidatos, default=None)
        achados.append((produto, melhor))

    frases = {m[1] for _, m in achados if m}
    if not frases:
        return ""
    if len(frases) == 1 and all(m for _, m in achados):
        return f" — {frases.pop()}"
    # ponytail: UM motivo por linha, o de maior prioridade entre os produtos — com
    # dois a frase vira parágrafo, e ela entra no meio do toast. O que NÃO é
    # aceitável é atribuí-lo a quem não o produziu, e é isso que o nome resolve.
    # Se aparecer par recorrente, virar lista.
    produto, (_, frase) = min(((p, m) for p, m in achados if m), key=lambda a: a[1][0])
    return f" — {_PRODUCT_PT[produto]}: {frase}"


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
        # Data da última sincronização do ITEM (sempre presente na Pluggy, ao
        # contrário de `products[*].last_updated_at`, que só vem com statusDetail).
        "last_updated_at": _iso(item.get("lastUpdatedAt")),
        "execution_status": (str(item.get("executionStatus") or "").upper() or None),
        "products": products,
        "stale_products": stale,
    }


# Relógio da Pluggy adiantado em relação ao nosso: até aqui a data conta; além,
# é lixo (relógio errado, `lastUpdatedAt` = fim previsto) e ficaria "à frente"
# para sempre, porque o nosso sync nunca a alcança.
_TOLERANCIA_RELOGIO = timedelta(minutes=5)


def data_da_pluggy(health: Any, agora: datetime | None = None) -> datetime | None:
    """Data mais nova que a Pluggy declara ter coletado (Onda 5, PR-B3).

    A maior data LEGÍVEL e COM FUSO entre `products[*].last_updated_at` e o
    `last_updated_at` do item. `derive_item_health` grava as duas CRUAS: o parse
    é aqui, em Python, protegido. Um `::timestamptz` em SQL sobre string do
    provedor derrubaria a query inteira (a armadilha do `SQL_EXECUTION_STATUS`).
    Data ilegível, sem fuso, ausente, FORA DO INTERVALO (zero-time "0001-01-01Z":
    `astimezone` estoura `OverflowError`, e `connection_ui_state` não tem try) ou
    no futuro além de `_TOLERANCIA_RELOGIO` é ignorada: sem prova, não há data.
    `agora` é opcional (default: o relógio) para quem precisar de pureza.
    """
    if not isinstance(health, dict):
        return None
    produtos = health.get("products")
    brutas = [health.get("last_updated_at")]
    if isinstance(produtos, dict):
        brutas += [d.get("last_updated_at") if isinstance(d, dict) else None
                   for d in produtos.values()]
    limite = (agora or datetime.now(timezone.utc)) + _TOLERANCIA_RELOGIO
    datas = []
    for bruta in brutas:
        try:
            data = datetime.fromisoformat(bruta)
            if data.tzinfo is None:
                continue
            day_tz(data)  # a conversão que a tela fará (D2/D7): valida o intervalo aqui
        except (TypeError, ValueError, OverflowError):
            continue
        if data <= limite:
            datas.append(data)
    return max(datas, default=None)


def pluggy_tem_dado_depois_de(health: Any, instante: Any) -> bool:
    """A Pluggy coletou algo DEPOIS de `instante`? (Onda 5, PR-B2/B3)

    Uma regra, a âncora como parâmetro: a retentativa pergunta contra
    `last_attempt_at` (a Pluggy coletou depois da nossa última leitura?); a tela
    (D2, `connection_ui_state`) chama esta mesma função com `last_sync_at`. Ajuste
    de tolerância de relógio (`_TOLERANCIA_RELOGIO` só descarta o futuro) mora aqui.
    `instante` sem fuso devolve False, em vez de estourar a comparação.
    """
    data = data_da_pluggy(health)
    return (data is not None and getattr(instante, "tzinfo", None) is not None
            and data > instante)


def mesclar_health_em_coleta(anterior: Any, novo: Any) -> Any:
    """Preserva os produtos da foto ANTERIOR que a foto NOVA omite, quando a nova
    é de uma coleta em andamento (issue #444) — o último estado conhecido de cada um.

    O job de saúde e o sync sobrescrevem `health` inteiro a cada observação. Uma
    foto tirada com o item em `_UPDATING` pode trazer só um subconjunto de
    produtos — ou nenhum —, e sem mesclar um produto que já estava atrasado
    (ex.: CREDIT parado desde 12/08) some da tela assim que o item volta a
    "buscar", mesmo sem ter sido resolvido.

    Só mescla quando a foto nova está em coleta E existe foto anterior com
    produtos — do contrário devolve `novo` sem tocar. Uma foto FINAL
    (item_status fora de `_UPDATING`) é sempre a verdade corrente e nunca é
    mesclada: ela é o que ZERA a coleta.

    A foto ANTERIOR também precisa estar numa lista de permissão
    (`_UPDATING`/`UPDATED`): sem isto, produtos de uma foto anterior doente
    (LOGIN_ERROR/ERROR/WAITING_USER_ACTION/MISSING) vazavam para a coleta nova
    e o card podia virar verde a partir de uma medição feita com o item em erro.

    `coletando_desde` (a âncora do teto, `aplica_teto_por_health`): toda foto em
    coleta sai com ele. Herdado da anterior também em coleta (ou o `observed_at`
    dela, foto de antes deste campo); senão, o `observed_at` da nova. Foto final
    não ganha a chave. Vale mesmo quando não há produto a mesclar.

    Pura: não muta `anterior` nem `novo`.
    """
    if not isinstance(novo, dict):
        return novo
    if str(novo.get("item_status") or "").upper() not in _UPDATING:
        return novo
    em_coleta = (isinstance(anterior, dict)
                 and str(anterior.get("item_status") or "").upper() in _UPDATING)
    novo = {**novo, "coletando_desde": (
        anterior.get("coletando_desde") or anterior.get("observed_at") if em_coleta
        else novo.get("observed_at"))}
    if not isinstance(anterior, dict):
        return novo
    if str(anterior.get("item_status") or "").upper() not in _MESCLA_PERMITE_ANTERIOR:
        return novo
    produtos_anteriores = anterior.get("products")
    if not isinstance(produtos_anteriores, dict):
        return novo

    produtos_novos = novo.get("products") if isinstance(novo.get("products"), dict) else {}
    products = {k: v for k, v in produtos_anteriores.items() if isinstance(v, dict)}
    products.update({k: v for k, v in produtos_novos.items() if isinstance(v, dict)})
    stale = [name for name in _PRODUCT_KEYS if name in products and not products[name].get("updated")]

    merged = dict(novo)
    merged["products"] = products
    merged["stale_products"] = stale
    return merged


# Motivo de quem leu pela metade. Não está em `_LABELS` de propósito: o `out()`
# do `connection_ui_state` manda motivo desconhecido para `error_recoverable`
# ("Erro temporário / Tentaremos de novo automaticamente"), que é a verdade.
READ_FAILED = "read_failed"

# As contas vieram, `/investments` não (429, paginação incoerente, falha ao
# gravar). Não é `READ_FAILED`: o espelho das contas É desta leitura. A tela diz
# "Parcial" (`connection_ui_state`), nunca "Atualizado".
INVESTMENTS_READ_FAILED = "investments_read_failed"
_DETALHE_INVESTIMENTOS_FALTANDO = "Investimentos não vieram nesta atualização"
# "Atualizando…" de conexão sem sync desta autorização (`sem_sync`).
_DETALHE_SEM_SYNC = "Ainda não sincronizou"

# "Atualizando…" passado o prazo da coleta (Onda 5, D1): mesma pílula, âmbar.
# Quem decide o prazo é o derivado `coleta_vencida` (`SQL_COLETA_VENCIDA`, em
# `db/open_finance_state.py`), lido na linha; esta função não tem relógio.
_DETALHE_COLETA_VENCIDA = "Está demorando mais que o normal — atualize de novo"
# Passado o teto (`TETO_ATUALIZANDO_MIN`, derivado `coleta_estourada`), o
# "Atualizando…" vira "Erro temporário" com este detalhe (Fase 4 do app, PR 2).
# Só a leitura muda: o par status/motivo gravado é o mesmo. A retentativa lê este
# detalhe como coleta vencida (`core/services/of_retentativa.py`).
_DETALHE_COLETA_ESTOURADA = "O banco está demorando — atualize de novo mais tarde"

# Item em `ERROR` na Pluggy (E13): a execução falhou do lado do banco. Só o
# "Erro temporário" com este detalhe; o `_FIXED_DETAIL` dele ("Tentaremos de novo
# automaticamente") continua valendo para os outros, que a retentativa relê.
_DETALHE_ITEM_EM_ERRO = "O banco teve um erro — atualize de novo mais tarde"

# D2 (Onda 5, PR-B3): a Pluggy já tem dado mais novo que o nosso último sync.
# Instrução, não promessa: a retentativa de fundo tem interruptores (doc §2.2).
_DETALHE_PLUGGY_A_FRENTE = "O banco já tem dados de {} — atualize para trazer"

# Falha de leitura NOSSA. Só um sync com leitura completa a limpa: o job de
# saúde (`leitura_completa=None`) não leu nada, então não pode apagá-la.
_MOTIVOS_DE_LEITURA = (READ_FAILED, INVESTMENTS_READ_FAILED)

# Motivos que uma falha de sync pode TROCAR por `read_failed` (Onda 5, PR-B1):
# CAS por espécie no `mark_sync_result(motivos_substituiveis=...)`, lista de
# permissão. Veredito de observação (`no_accounts`, `item_missing`) fica de fora:
# a falha sem observação (`marcar_leitura_falhou`) nunca o troca, e motivo
# desconhecido também não (o default seguro do `out()` já o pinta não-verde).
MOTIVOS_QUE_A_FALHA_SUBSTITUI = _REASONS_OK + _MOTIVOS_DE_LEITURA
# A foto do item VIVO tirada pelo próprio run que falhou depois dela prova que
# o item existe e tira o `item_missing` (contrato §1 item 2). Não leu contas,
# então não tem autoridade sobre `no_accounts`.
MOTIVOS_QUE_A_FOTO_VIVA_SUBSTITUI = MOTIVOS_QUE_A_FALHA_SUBSTITUI + ("item_missing",)


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
                         `GET /items`). Com `None` nenhum motivo é INVENTADO:
                         falha de leitura (`read_failed`,
                         `investments_read_failed`) é mantida sempre, e
                         `no_accounts` só com o espelho ainda vazio.
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

    # H: sem leitura não se inventa motivo, e também não se apaga falha de
    # leitura — era o `if has_data:` daqui que devolvia verde a um espelho velho
    # (R5). `no_accounts` só se mantém com o espelho ainda vazio.
    if leitura_completa is None:
        motivo = str(reason_atual or "").lower()
        if motivo in _MOTIVOS_DE_LEITURA or (motivo == "no_accounts" and not has_data):
            return "ACTIVE", motivo
        return "ACTIVE", ""

    if has_data:
        return "ACTIVE", ("" if leitura_completa else INVESTMENTS_READ_FAILED)

    # Espelho vazio NÃO é erro do ITEM — ele respondeu, e respondeu saudável
    # (qualquer outra coisa já saiu acima). O `status` é a saúde do item; quem
    # explica o espelho vazio é o `status_reason`.
    # Devolver None aqui ("não mexe") deixava um ERROR anterior grudado: com
    # `OF_REFRESH_ENABLED` off (o default) não há PATCH → não há webhook → não há
    # sync completo, e ERROR virava TERMINAL na prática — medido, 404 no tick 0 e
    # item vivo nos 5 seguintes continuava "Erro temporário".
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
    base = f"{texto} desde {datas[0]}" if datas else texto
    return base + _motivo_do_warning(health, stale)


def connection_ui_state(connection_row: dict) -> dict:
    """Estado + textos de UMA conexão. Única fonte dos estados de `_LABELS`.

    Lê `status`, `status_reason`, `health` e `last_sync_at` da linha de
    `open_finance_connections`. `health` NULL significa "saúde ainda não medida"
    — NÃO "nunca sincronizou": linha legada tem last_sync_at e nenhum health.
    Os derivados SQL (`execution_status`, `device_na_janela`, `coleta_*`) vêm do
    select (`db/open_finance_state.py`); chave AUSENTE = janela do dispositivo
    fechada: quem não seleciona `device_na_janela` nunca vê "Autorize o acesso no
    app do banco", só "Reautorize o banco".
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
    # A mescla com a foto anterior (issue #444: cartão atrasado que some quando a
    # foto nova traz só `accounts`) mora na ESCRITA, não aqui — `mark_sync_result`
    # (`db/open_finance_state.py`) chama `mesclar_health_em_coleta` antes de
    # gravar, então o `health` que esta função lê já vem mesclado quando a coleta
    # está em andamento. Esta guarda continua valendo para o caso que a mescla
    # não cobre: quando NÃO HÁ foto anterior com produto (1ª coleta) ou ela não
    # está na lista de permissão (item em erro), não há o que mesclar, e
    # `products` vazio ainda é "não sei", não "nada atrasado".
    #
    # DECISÃO DO DONO (2026-09-16): durante a coleta, com foto anterior
    # UPDATED/UPDATING, o card mostra o último estado conhecido — inclusive
    # "Atualizado" quando a foto anterior estava toda em dia.
    # Âncora da D2/D7: o último sync, só se valeu (depois da autorização atual) e
    # tem fuso. Sem health ou sem âncora, `data_pluggy`/`ancora` None = sem D2/D7.
    data_pluggy = data_da_pluggy(health)
    ancora = ultimo if (not sem_sync and getattr(ultimo, "tzinfo", None)) else None
    pluggy_a_frente = pluggy_tem_dado_depois_de(health, ancora)  # a única regra de "à frente"
    dados_de = (day_tz(data_pluggy).strftime("%d/%m") if ancora and data_pluggy and not pluggy_a_frente
                and ancora - data_pluggy > timedelta(days=1) else None)
    coletando_sem_info = (str((health or {}).get("item_status") or "").upper() in _UPDATING
                          and not (health or {}).get("products"))

    def out(state: str, detail: str | None = None) -> dict:
        # DEFAULT SEGURO: estado desconhecido nunca é verde. Um `status_reason`
        # pendente que este arquivo não conhece (gravado por um caminho novo)
        # não pode virar "Atualizado" — foi exatamente assim que `no_accounts`
        # (item vivo que não espelhou nada) chegou à tela como "Tudo em dia!".
        # Leitura parcial nossa é "Parcial", não "Erro temporário": as contas vieram.
        # Mas só se essa leitura é da autorização atual: com `sem_sync` o motivo
        # veio de um sync cujo carimbo a reconexão recusou, e ele não vale.
        if state == "updated" and reason == INVESTMENTS_READ_FAILED:
            state, detail = (("updating", _DETALHE_SEM_SYNC) if sem_sync
                             else ("partial", _DETALHE_INVESTIMENTOS_FALTANDO))
        elif state == "updated" and reason not in _REASONS_OK:
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
            state, detail = "updating", _DETALHE_SEM_SYNC
        # Mesma família da linha de cima, e no mesmo lugar de propósito: DEPOIS
        # do motivo pendente (`no_accounts`/`read_failed`/desconhecido continuam
        # falando primeiro) e só contra o verde. Sem `detail`: é o "Atualizando…"
        # seco da base, não o "Ainda não sincronizou" — aqui já se sincronizou.
        elif state == "updated" and coletando_sem_info:
            state, detail = "updating", None
        # D2 (PR-B3): fica DEPOIS do `sem_sync` e do `coletando_sem_info` (reconexão
        # sem sync continua "Atualizando…") e só contra o verde: os motivos de
        # leitura, o erro e o `partial` da Pluggy já falam por si.
        # `and data_pluggy`: o predicado e `data_da_pluggy` leem o relógio em separado.
        elif state == "updated" and pluggy_a_frente and data_pluggy:
            state, detail = "partial", _DETALHE_PLUGGY_A_FRENTE.format(day_tz(data_pluggy).strftime("%d/%m"))
        # Mesmo defeito do `partial`: item vivo, espelho vazio, e o porquê
        # (`ACCT_001` = ninguém liberou contas) preso no JSON. Sem health — o
        # ramo de baixo — a frase continua exatamente a de hoje.
        #
        # SÓ os dois produtos que a frase NOMEIA ("não devolveu contas nem
        # investimentos"). Varrer o health inteiro pegava warning de cartão e de
        # transações, que não explicam espelho vazio de conta. E não dá para
        # filtrar por `updated` aqui como o `partial` faz: a doc é explícita que
        # o warning também vem com `isUpdated: true` ("the product was retrieved
        # correctly, but it can be improved with some user action"), e o exemplo
        # dela é de TRANSAÇÕES devolvidas vazias com o aviso — o análogo do
        # `ACCT_001` numa conta, não uma citação sobre contas. Filtrar por
        # `updated` aqui apagaria o caso que este ramo existe para explicar.
        if state == "no_accounts":
            detail = (detail or _FIXED_DETAIL[state]) + _motivo_do_warning(
                health, ("BANK", "INVESTMENTS"))
        # Teto e D1, no FIM: depois de toda transformação que produz `updating`,
        # e só sobre ele (instrução de dispositivo, erro e o resto não mudam).
        # Teto: "Atualizando…" há `TETO_ATUALIZANDO_MIN` ou mais vira "Erro
        # temporário". D1: sem sync desde a autorização atual, passado o prazo,
        # só troca o detalhe — estado, rótulo e pílula continuam os mesmos.
        if state == "updating" and row.get("coleta_estourada"):
            state, detail = "error_recoverable", _DETALHE_COLETA_ESTOURADA
        elif state == "updating" and row.get("coleta_vencida"):
            detail = _DETALHE_COLETA_VENCIDA
        if state == "partial":
            detail = _CONTEXTO_DADOS_PARCIAIS + (f" {detail}" if detail else "")
        return {
            "state": state,
            "label": _LABELS[state],
            "detail": detail if detail is not None else _FIXED_DETAIL.get(state),
            "stale_products": stale,
            "dados_de": dados_de,  # D7: sufixo da linha "Última sync" (settings.html)
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
                item_status, str(health.get("execution_status") or "").upper())
                if row.get("device_na_janela") else None)
        # `and sem_sync`: coleta de banco real demora MUITO mais que o sync, então
        # o item fica em `UPDATING` depois de o espelho já estar escrito — e o card
        # dizia "Atualizando…" para sempre em cima de dado importado e de um
        # `last_sync_at` carimbado. Com sync posterior à autorização atual, quem
        # fala é o ESTADO DO DADO (ramos abaixo: "Atualizado"/"Parcial"/"Sem
        # dados"/"Erro temporário"). Sem sync — 1ª conexão ou reconexão ainda não
        # espelhada — "Atualizando…" é verdade e continua sendo a guarda que
        # impede o card de dizer "tudo em dia" com espelho vazio.
        # Falha de LEITURA nossa registrada fala aqui também (Onda 5, contrato
        # item 8): o `out("updated")` a entrega ao default seguro — `read_failed`
        # vira "Erro temporário" e `investments_read_failed` sem sync continua
        # "Atualizando…" (regra do PR-A). Só esses dois: `no_accounts` numa 1ª
        # coleta em curso é a Pluggy ainda sem contas, e continua "Atualizando…".
        if item_status in _UPDATING and sem_sync:
            return out("updated" if reason in _MOTIVOS_DE_LEITURA else "updating")
        # ERROR vem ANTES de "parcial": item em erro COM produto atrasado é erro,
        # e rotulá-lo de "Parcial" ("atualizei o que deu") subestima o estado.
        if item_status == "ERROR":
            # Item em ERROR de verdade continua "Erro temporário": motivo velho
            # não subestima erro, e este ramo nunca vira verde. Com detalhe PRÓPRIO
            # (Onda 5, PR-B2, E13): a execução falhou na Pluggy e reler (GET) não
            # tira o item de ERROR, então a retentativa não promete "Tentaremos de
            # novo automaticamente" aqui (`core/services/of_retentativa.py`).
            return out("error_recoverable", _DETALHE_ITEM_EM_ERRO)
        if status == "ERROR":
            # `status` local em ERROR com o ITEM vivo e o motivo dizendo por que o
            # espelho está vazio: quem explica é o motivo. O override do `out()` só
            # rodava no ramo verde, então ERROR/no_accounts nunca mostrava "Sem
            # dados" — o estado desta onda ficava invisível.
            return out("no_accounts" if reason == "no_accounts" else "error_recoverable")
        if stale or str(health.get("execution_status") or "").upper() == "PARTIAL_SUCCESS":
            # Motivo pendente fala antes do produto atrasado, pela MESMA regra do
            # verde: o default seguro do `out("updated")` decide (`read_failed` e
            # desconhecido → "Erro temporário", `no_accounts` → "Sem dados").
            # "Atualizei o que deu" sobre nada lido/espelhado seria falso. Como no
            # verde, sem olhar `sem_sync`: a reconexão zera o motivo — exceto um
            # run velho de `_sync_item_contido` gravando depois dela, corrida que
            # o PR-B1 da Onda 5 fecha (`geracao_vista`).
            if reason not in _REASONS_OK and reason != INVESTMENTS_READ_FAILED:
                return out("updated")
            detalhe = _stale_detail(health)
            # Parcial da Pluggy E leitura parcial nossa: as duas coisas faltam, e
            # o detalhe da Pluggy sozinho escondia os investimentos (Codex, #692).
            # Com `sem_sync` o motivo é de antes da autorização atual e não vale.
            # Com INVESTMENTS já atrasado na Pluggy, o detalhe dela já os nomeia.
            if (reason == INVESTMENTS_READ_FAILED and not sem_sync
                    and "INVESTMENTS" not in stale):
                detalhe += "; " + _DETALHE_INVESTIMENTOS_FALTANDO.lower()
            return out("partial", detalhe)
        return out("updated")

    # Sem health medido: cai no status local (comportamento de hoje).
    # R1b (Onda 5): falha registrada fala ANTES do "Atualizando…" deste ramo. O
    # `status` local `UPDATING` veio do webhook ou do upsert, não de uma
    # observação; com `read_failed` do sync de fundo ele girava para sempre. Com
    # motivo pendente os dois early-returns de "Atualizando…" abaixo (este e o
    # `ultimo is None`) cedem ao `out("updated")` do fim, cujo default seguro
    # decide. `_NEEDS_USER` (instrução de dispositivo) continua vindo antes dele.
    pendente = reason not in _REASONS_OK
    if status in _UPDATING and not pendente:
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
        # O `device_na_janela` (D5) põe o MESMO prazo no 1º campo: o `status`
        # local `WAITING_USER_ACTION` também vence.
        return out("needs_user_action", _detalhe_de_acao(
            status, str(row.get("execution_status") or "").upper())
            if row.get("device_na_janela") else None)
    if status == "ERROR":
        # Mesma classe do ramo com health: o motivo explica melhor que "Erro
        # temporário" (linha legada gravada antes desta onda também cai aqui).
        return out("no_accounts" if reason == "no_accounts" else "error_recoverable")
    if ultimo is None and not pendente:
        # `ultimo is None`, NÃO `sem_sync`: este early-return pula o default
        # seguro do `out()`, e por isso ele só pode valer no escopo EXATO que a
        # base lhe dava ("nunca sincronizou") e sem motivo pendente (o
        # `not pendente`, Onda 5 R1b: a 1ª conexão com `status` local `UPDATED`
        # cujo sync falhou caía aqui e girava para sempre). Alargá-lo para
        # `sem_sync` fez o caso da reconexão descartar `no_accounts`,
        # `read_failed` e motivo desconhecido: medido, 60 combinações com perda,
        # e a pílula do `read_failed` caindo de vermelho para âmbar logo depois
        # de o usuário reconectar e o sync falhar. Quem reconectou desce pelo
        # `out("updated")` abaixo, onde o motivo fala primeiro e o `sem_sync` só
        # decide o que restar de verde.
        return out("updating", _DETALHE_SEM_SYNC)
    return out("updated")
