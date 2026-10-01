# Open Finance: estados da conexão, fontes e eventos

Referência do que a tela ("Ajustes > Open Finance"), o toast do Atualizar e o
aviso proativo dizem sobre uma conexão Pluggy, de onde cada informação vem e o
que cada evento faz com ela. Criado na Onda 5 (PR-A). **Cada PR da Onda 5
atualiza as células que corrige**: esta tabela é o aceite "estados documentados",
num lugar que teste nenhum precisa ler.

Código de referência:

- `core/services/pluggy_health.py`: `resolve_connection_state` (DECIDE o par
  `status` + `status_reason` a partir de uma observação, para o sync, o job de
  saúde, o 404 e a foto do sync que falhou depois do `GET /items`; quem grava é
  `mark_sync_result`) e `connection_ui_state` (única que decide
  o estado exibido, um dos de `_LABELS`). A máquina de estados por escrito está no
  topo desse módulo.
- Também GRAVAM `status` e/ou `status_reason` sem passar pelo resolvedor: o
  webhook (`update_pluggy_open_finance_item_status`), a reconexão
  (`save_pluggy_open_finance_item`, linha G), `marcar_leitura_falhou`
  (só `read_failed`, com `status` intocado, e só sobre motivo substituível,
  `MOTIVOS_QUE_A_FALHA_SUBSTITUI`; chamada pelo lote, `_sync_item_contido`, e
  desde o PR-B1 pela falha final do sync de fundo, `_run_pluggy_sync_bg`) e a pausa (`pause_open_finance_connection`, `PAUSED`).
- `core/services/pluggy_sync.py`: `sync_pluggy_item` (sync), `run_of_health_check`
  (job de saúde, só `GET /items`), `_refresh_items_report` (o que o toast lê).
- `frontend/settings.html`: `renderConnections` (pílula) e `refreshVerdict` (toast).
- `db/open_finance.py`: `list_connections_needing_reconnect` (aviso proativo).

Nenhum número aqui é medição. Para contar conexões, testes ou linhas, rode o
comando; não copie o resultado para cá (`CLAUDE.md` §2).

---

## 1. Contrato: fontes, precedência e validade

**Vigente** é o que o código faz hoje. **Proposto** tem PR atribuído na §3.

1. **Terminal local** (`DELETED` e `PAUSED`) vence tudo. *Vigente.*
2. **`item_missing`** (404 observado) só sai por uma **observação** do item vivo,
   do job de saúde ou do sync, inclusive a foto do sync que falhou depois do
   `GET /items`. Webhook não limpa. *Vigente.*
3. **Autorização atual** = `coalesce(reconnected_at, created_at)`. Observação e
   sync anteriores a ela não valem: `sem_sync`, `health` zerado na reconexão e
   guarda de geração do sync. *Vigente.*
4. **Observação do item** (`health`: `item_status`, `execution_status`,
   `products`). O `item_status` decide o balde; o `execution_status` só refina o
   detalhe (device/QR, `PARTIAL_SUCCESS`). *Vigente.* A observação vale até a
   próxima. *Proposto (D5, PR-D):* a instrução de device/QR ganha prazo também
   quando há `health`.
5. **Status vindo do webhook** é pista, não observação. *Proposto (PR-C):*
   continua gravando `ERROR` na hora e dispara uma observação imediata
   (`GET /items` e `resolve_connection_state`), que decide o par. `item/created`
   sobre conexão existente deixa de reescrever `status` e `status_reason`.
6. **Nossa leitura** (`last_attempt_at`, `last_sync_at` e os motivos de falha de
   leitura). **Vigente desde o PR-A:**
   - contas lidas e `/investments` falhando (429, paginação incoerente, falha ao
     gravar) grava `ACTIVE` + `investments_read_failed`, e a tela diz
     **"Parcial · Investimentos não vieram nesta atualização"**;
   - sem conta nenhuma espelhada e leitura incompleta continua `read_failed`
     ("Erro temporário");
   - **só um sync com leitura completa limpa esses dois motivos.** O job de saúde,
     que não lê contas, os mantém. `no_accounts` continua caindo no job assim que
     existe dado no espelho.
   - O job decide manter ou limpar sobre o motivo que leu ao listar a linha, e o
     `GET /items` dele roda sem lock. Por isso ele só grava se o motivo ainda for o
     que leu (`status_reason_visto` no `mark_sync_result`); se um sync o mudou no
     meio, o job não grava nada naquela linha. *Vigente desde o PR-A.*
   - Leitura parcial de um sync cujo carimbo a reconexão recusou
     (`last_sync_at` anterior ao `reconnected_at`) não vale: a tela diz
     "Atualizando… · Ainda não sincronizou", como no item 3.
   - Também limpam, como antes: reconexão pelo widget (autorização nova), item
     observado doente (`ERROR`/`_NEEDS_USER`, quando o `health` conta a história)
     e o webhook nos três eventos que gravam status — `item/created`, `item/error`
     e `item/deleted` (`update_pluggy_open_finance_item_status` zera o motivo, menos
     `item_missing`). `item/updated` e `transactions/*` não gravam status nem
     motivo: só agendam sync. `item/created` e `item/error` sobre falha de leitura
     são buracos do contrato, corrigidos no PR-C (R8 e observação imediata).
7. **Idade do dado**: o usuário vê o dado do banco, cuja data é
   `products[*].last_updated_at` da Pluggy, limitada pelo nosso `last_sync_at`.
   *Proposto:* D2 e D7.
8. **"Atualizando…"** só vale enquanto a coleta da autorização atual pode estar
   legitimamente em curso. Passado o prazo (D1) ou registrada uma falha, a tela diz
   o que houve. **Vigente desde o PR-B1:**
   - a falha de um sync grava `read_failed`: a foto do run (**O**, abaixo) a cada
     tentativa que falha depois do `GET /items`, e a marca (**F**) na falha final,
     que é a exceção final do sync de fundo OU o `sync_in_progress` devolvido nas
     três tentativas (lock do item ou semáforo do processo ocupado: célula 30).
     No sync de fundo, portanto, "Erro temporário" já aparece durante o backoff
     entre as tentativas (célula 29 da §2.1). A frase "Tentaremos de novo
     automaticamente" só passa a ser verdade com a retentativa do PR-B2: o B1
     sozinho marca e para. O motivo pendente fala
     antes do "Atualizando…" ("Erro temporário"): no ramo sem `health` qualquer
     motivo pendente; no ramo com `health` em coleta (`UPDATING`/`CREATED`) só
     falha de LEITURA (`read_failed`; `investments_read_failed` sem sync continua
     "Atualizando…", regra do PR-A), porque `no_accounts` numa 1ª coleta em curso é
     a Pluggy ainda sem contas;
   - a falha de um sync grava em até duas vezes, cada uma com a autoridade da
     sua espécie, e as duas vão pelo `id` lido quando o run começou e só gravam
     se o par `(reconnected_at, last_sync_at)` (`geracao_vista`) ainda for o
     lido (sync bom ou reconexão no meio → nada):
     - **F**, a falha sem observação (`marcar_leitura_falhou`, os dois chamadores:
       sync de fundo e lote do Atualizar): grava `read_failed` só se o motivo
       ATUAL está em `MOTIVOS_QUE_A_FALHA_SUBSTITUI` (vazio, `ok` e as falhas de
       leitura), no próprio `UPDATE` (CAS por espécie, `motivos_substituiveis`).
       A falha sem observação nunca troca um veredito (`no_accounts`,
       `item_missing`), nem o de antes do run nem um observado no meio;
     - **O**, a foto do `GET /items` do próprio run quando ele falha depois dela
       (`sync_pluggy_item`): grava o par do resolvedor (`ACTIVE`/`read_failed`,
       ou `ERROR`/`""` com o item em erro) e a foto, só se ninguém observou o
       item desde o começo do run (`observacao_vista`, o `health.observed_at`).
       Com o item vivo, só sobre `MOTIVOS_QUE_A_FOTO_VIVA_SUBSTITUI` (a lista da
       F mais `item_missing`: a foto do item vivo troca `item_missing`, item 2).
       Ela não leu contas, então não troca `no_accounts`. A O usa a linha que o
       próprio sync lê, não a captura do sync de fundo: com o banco fora só
       naquela captura e um dono só, a O roda e grava na linha do dono (a F é
       que fica sem linha). Item ligado a mais de um usuário (`AmbiguousItemError`,
       no início do run ou na releitura de posse dentro do lock) não grava em
       linha nenhuma: nem a O nem a F (a O não tem foto antes da 1ª leitura; a F
       recebe a exceção e sai). Sem exceção que prove a ambiguidade (um segundo
       dono que aparece depois de uma falha de leitura comum), a O e a F gravam
       na linha do run, que é a do próprio usuário;
   - "sem sync desde a autorização atual" é o MESMO predicado nos dois lados:
     `last_sync_at` nulo ou anterior a `reconnected_at` (nunca `created_at`, que é
     relógio do Postgres contra o `last_sync_at` do Python);
   - sem sync desde a autorização atual e com a âncora
     `coalesce(reconnected_at, created_at)` fora de `(now() - 30 min, now() + 5 min]`
     (`SQL_COLETA_VENCIDA`, derivado no Postgres), a pílula continua
     "Atualizando…" em âmbar e o detalhe vira **"Está demorando mais que o normal —
     atualize de novo"** (o app não tem botão Atualizar: lá se puxa a tela). Vale nos dois ramos (com e sem `health`), porque o job
     de saúde grava `health` sem sincronizar. A instrução de dispositivo dentro da
     janela continua vencendo (ela é `needs_user_action`, não "Atualizando…").
   - quem RELÊ sozinho depois do prazo ou da falha é o PR-B2 (D3).
9. **Aviso proativo = função do mesmo estado da tela** (`connection_ui_state`), não
   um classificador paralelo por `status`. *Proposto (D4, PR-D).*
10. **`health` e `status` local discordando** (`status=ERROR` do webhook com
    `health.item_status=UPDATED`): hoje `ERROR` vence. *Proposto:* não comparar
    relógios; a observação imediata do item 5 resolve. Até ela terminar, `ERROR`
    continua vencendo, que é o lado conservador, salvo quando um sync em voo
    grava a sua foto do item (o sucesso, ou a O do item 8 quando ele falha
    depois do `GET /items`): aí o `status` observado troca esse `ERROR`
    (célula 13b da §2.1).

Ações para dado antigo, em ordem de custo: (a) reler a Pluggy (sync, só GET, sem
cota de coleta); (b) pedir coleta nova (PATCH, gasta cota); (c) pedir ação do
usuário (reautorizar ou autorizar no app). A tela só manda para (c) quando a
observação exige, e nunca durante uma autorização de dispositivo ainda válida.

**Invariante do verde:** "Atualizado" exige a última leitura completa (contas e
investimentos) depois da autorização atual, sem motivo pendente. O default seguro
do `connection_ui_state` (motivo desconhecido nunca é verde) continua valendo.

---

## 2. Estados × eventos

Estados = saídas de `connection_ui_state`. Eventos:

| código | evento |
|---|---|
| E1 | webhook `item/created` |
| E2 | webhook `item/updated` ou `transactions/*` (disparam sync) |
| E3 | webhook `item/error` |
| E4 | webhook `item/deleted` |
| E5 | `item/waiting_user_input` e outros `item/*` não mapeados |
| E6 | Atualizar manual (PATCH, espera, sync) |
| E7 | tique periódico (`OF_REFRESH_ENABLED=1`, só PATCH) |
| E8 | tique de saúde (`GET /items`, com `health` mais velho que `OF_HEALTH_MAX_AGE_SEC`) |
| E9 | reconexão pelo widget |
| E10 | remoção (disconnect) |
| E11 | perda de rede ou watchdog no front |
| E12 | tempo passando sem evento |

✓ = correto e com teste. ✗ = errado hoje, com o PR que corrige. ? = hipótese ou
verificação externa pendente.

| estado de partida | evento | tela | aviso (se ligado) | veredito |
|---|---|---|---|---|
| Atualizando, 1ª conexão sem sync | E2 com sync ok | Atualizado (os Ajustes só repintam se o usuário agir) | não | ✓ backend; ✗ front (F1, PR-E) |
| Atualizando, 1ª conexão sem sync | E2 com sync levantando (5xx/429/rede) | **Erro temporário · Tentaremos de novo automaticamente** (a falha final grava `read_failed`) | não | ✓ **PR-B1 (R1, R1b)**; a retentativa que a frase promete é o PR-B2 |
| Atualizando, 1ª conexão sem sync, `health` do job com o item em `UPDATING` | E2 com sync levantando | **Erro temporário · Tentaremos de novo automaticamente** | não | ✓ **PR-B1** (rodada 2) |
| Atualizando, 1ª conexão sem sync, `health` em `UPDATING` | sync lê e não vem conta (`no_accounts`, Pluggy ainda coletando) | Atualizando… (e o detalhe do prazo depois de 30 min) | não | ✓ **PR-B1** |
| Atualizado (ou qualquer) | falha final de um sync de fundo que começou ANTES de um sync bom ou de uma reconexão | mantém o que o mais novo gravou (a falha velha não marca) | não | ✓ **PR-B1** (rodada 2) |
| qualquer | falha final de um sync que começou ANTES de um veredito mais novo (`no_accounts`, 404 → `item_missing`), inclusive o mesmo veredito observado de novo | mantém "Sem dados" / "Conexão perdida" | avisa conforme o estado | ✓ **PR-B1** (células 4, 5, 6, 11 e 20 de `tests/test_of_marca_de_falha.py`) |
| qualquer | falha final de um sync que começou ANTES de o job ver o item vivo e **limpar** o motivo (`item_missing`, ou `no_accounts` com espelho cheio) | **Erro temporário · Tentaremos de novo automaticamente** (antes: verde falso) | não | ✓ **PR-B1** (células 9, 9b e 9c) |
| Conexão perdida (`item_missing`) | falha final de um sync no próprio `GET /items` (5xx/429, sem foto) | mantém "Conexão perdida · Refaça a conexão" | avisa | ✓ **PR-B1** (célula 7) |
| Conexão perdida (`item_missing`) | o próprio sync vê o item vivo (`GET /items` 200) e falha depois (`/accounts` 5xx/429) | **Erro temporário · Tentaremos de novo automaticamente** (DECISÃO 2 = A) | para | ✓ **PR-B1** (célula 8) |
| Sem dados (`no_accounts`) | falha final de um sync, sem evento no meio (no `GET /items` ou depois) | mantém "Sem dados"; em L a foto do run não é gravada (DECISÃO 1 = A) | não | ✓ **PR-B1** (células 18 e 19) |
| Sem dados (`no_accounts`) | o sync vê o item em `LOGIN_ERROR` e falha depois | **Ação necessária · Reautorize o banco** (antes: Erro temporário, com a foto de ontem) | avisa | ✓ **PR-B1** (célula 22) |
| qualquer sem motivo | E3 no meio de um sync que falha depois do `GET /items` | Erro temporário; a foto (mais velha que a pista do webhook) troca `status` `ERROR` por `ACTIVE`, e o aviso proativo, que ainda lê `status`, é afetado até o PR-D | não | ✓ tela **PR-B1** (célula 13b); aviso registrado em `decisoes.md` |
| Atualizando, com o prazo vencido | E6 (Atualizar) com a Pluggy ainda coletando e sem conta | toast "{banco}: está demorando mais que o normal — atualize de novo. Toque em Atualizar de novo em instantes.", em tom de ERRO (o `reason` é `no_accounts`) | não | ✗ instrução repetida, "Toque em Atualizar" num app sem o botão e tom de erro: frontend (`refreshVerdict`), fica para o PR-E |
| Atualizando, 1ª conexão sem sync | processo reinicia no meio do sync | Atualizando… até 30 min da autorização; depois **Atualizando… · Está demorando mais que o normal — atualize de novo** (âmbar) | não | ✓ **PR-B1 (D1)**; recuperar sozinho: PR-B2 |
| Atualizando, 1ª conexão sem sync | E8 | Atualizando… ("Ainda não sincronizou") dentro do prazo, depois o detalhe do prazo; com `read_failed`, Erro temporário | não | ✓ **PR-B1**; o tique ainda não relê (PR-B2) |
| Atualizando, 1ª conexão sem sync | E12 (horas) | depois de 30 min: **Atualizando… · Está demorando mais que o normal — atualize de novo** | não | ✓ **PR-B1 (D1)** |
| Atualizando, 1ª conexão sem sync | E11 (Ajustes aberto) | card parado em Atualizando… | n/a | ✗ F1 (PR-E) |
| Autorize no app (device, `health` null, dentro de `JANELA_DEVICE_AUTH_MIN`) | E8 com GET falhando | mantém; vencida a janela, "Reautorize" | calado, depois avisa | ✓ (#428) |
| Autorize no app (device, `health` null, dentro de `JANELA_DEVICE_AUTH_MIN`) | E8 com GET ok e mesmo estado | "Autorize no app" para sempre | calado para sempre | ✗ R7 (D5, PR-D) |
| Autorize no app | E9 | reinicia a janela | calado | ✓ |
| Autorize no app | E5 | nada muda | – | ? H5 (depende do catálogo de eventos da Pluggy) |
| Ação necessária (reautorize) | E2 | continua "Ação necessária"; "Última sync" avança | avisa | tela ✓; "Última sync" ✗ C1 (D7) |
| Ação necessária (reautorize) | E9 e sync | Atualizado | para | ✓ |
| Atualizado | E2 com sync ok | Atualizado | não | ✓ |
| Atualizado | E3 transitório (item ok na Pluggy) | "Erro temporário · Tentaremos de novo automaticamente" até o próximo E8 | "reconecte" | ✗ R2, R3 (PR-C, PR-D) |
| Atualizado | E3 com item em `LOGIN_ERROR` | "Erro temporário" (deveria ser "Ação necessária · Reautorize o banco") até o E8 | avisa | ✗ (PR-C) |
| Atualizado | E4 | Removido (sem detalhe) | não | ? C7 (fora da Onda 5 salvo pedido) |
| Atualizado | E6 com `/investments` 429 e contas lidas | **Parcial · Investimentos não vieram nesta atualização**; toast "Atualizei o que deu no {banco}: investimentos não vieram nesta atualização." | não | ✓ **corrigido no PR-A (R4)** |
| Atualizado | E6 com `/accounts` 429 | Erro temporário (`read_failed`) | não | ✓ |
| Atualizado | E6 com o item sumido (404) | Conexão perdida | avisa | ✓ |
| Atualizado | E7 sem webhook de volta | Atualizado sobre espelho velho | não | ✗ R6 (D2/D3, PR-B2) |
| Atualizado, espelho velho e Pluggy em dia | E8 | Atualizado | não | ✗ R6 (D2/D3, PR-B2) |
| Atualizado | E11 no PTR | âmbar; o pedido segue e repinta quando assentar | n/a | ✓ |
| Atualizado | E11 no botão | toast de erro; o servidor pode ter concluído | n/a | ? H1 |
| Parcial (Pluggy) | E2 ou E6 com produto voltando | Atualizado | não | ✓ (#473) |
| Parcial (Pluggy) | foto nova em `UPDATING` | mantém o produto atrasado | não | ✓ (#473) |
| Parcial (Pluggy) | E6 com `/investments` 429 e contas lidas | Parcial · "Cartão desatualizado desde dd/mm; investimentos não vieram nesta atualização" (antes só o da Pluggy) | não | ✓ **PR-A** (Codex, #692) |
| Parcial (Pluggy) | E6 com `/accounts` 429 (`read_failed`, nada lido) | **Erro temporário** · "Tentaremos de novo automaticamente"; toast "Não consegui atualizar o {banco} agora…" (antes: Parcial e "Atualizei o que deu…" sem ter lido nada). Vale também depois de reconectar (`sem_sync`), como no default seguro | não | ✓ **PR-A** |
| Parcial (Pluggy) | E6 com leitura completa e zero espelhado (`no_accounts`) | **Sem dados** · "O banco não devolveu contas nem investimentos"; toast "{banco}: o banco não devolveu contas nem investimentos." (antes: Parcial e "Atualizei o que deu…" com nada espelhado) | não | ✓ **PR-A** |
| Parcial (Pluggy) | motivo que o código não conhece | Erro temporário (o mesmo default seguro do verde; antes: Parcial) | não | ✓ **PR-A** |
| Parcial (Pluggy) com `investments_read_failed` de antes da autorização atual (`sem_sync`) | leitura da tela | só o detalhe da Pluggy, sem "investimentos não vieram" | não | ✓ **PR-A** |
| Parcial (Pluggy) com INVESTMENTS já atrasado | E6 com `/investments` 429 | só o detalhe da Pluggy ("Investimentos desatualizado desde dd/mm"), sem repetir | não | ✓ **PR-A** |
| Erro temporário (`read_failed`) depois de reconectar | um sync VELHO (de antes da reconexão) falhando depois dela | a falha velha não grava (`geracao_vista`): a tela é a da autorização nova | não | ✓ **PR-B1** (`tests/test_of_marca_de_falha.py::test_c2_c3_lote_com_linha_velha_nao_desfaz_o_que_veio_depois[reconexao]`) |
| Parcial (`investments_read_failed`) | E8 | mantém Parcial | não | ✓ **PR-A (R5)** |
| Parcial (`investments_read_failed`) | E2 ou E6 com leitura completa | Atualizado | não | ✓ **PR-A** |
| Parcial (`investments_read_failed`) | E3 | "Erro temporário" com o motivo apagado; o E8 seguinte, com o item vivo, pinta Atualizado sem os investimentos terem sido lidos | avisa (classifica por `status`) | ✗ (PR-C) |
| Parcial (`investments_read_failed`) | E1 atrasado | motivo apagado: Atualizando… (sem health) ou Atualizado | não | ✗ família R8 (PR-C) |
| qualquer, com o motivo MUDANDO no meio | E8 com um sync terminando durante o `GET /items` do job | o que o sync gravou: o job não grava nada, porque o CAS (`status_reason_visto`) compara só o motivo | não | ✓ **PR-A**, só neste escopo |
| qualquer | E8 com um sync ok terminando no meio e o motivo IGUAL antes e depois | o job regrava `health` e `status` por cima da foto mais nova do sync | não | ✗ (PR-C) |
| qualquer | E9 para uma autorização de dispositivo no meio do `GET /items` do job (motivo `NULL` antes e depois) | o job grava o `health` e o `status` da autorização antiga por cima dos zerados: some "Autorize o acesso no app do banco" e a tela diz "Atualizando… · Ainda não sincronizou" | não (o `status` volta a `ACTIVE`) | ✗ (PR-C; B3 do Tester) |
| Atualizado | E8 com 404 transitório no `GET /items`, e um sync ok da mesma linha terminando no meio | "Conexão perdida" com o item vivo (o caminho do 404 não tem CAS) | avisa | ✗ (PR-C; B2 do Tester) |
| qualquer | E3 (webhook `item/error`) no meio do `GET /items` do job, com o motivo igual | o job grava `ACTIVE` por cima do `ERROR` do webhook | – | ✗ (PR-C) |
| Parcial (`investments_read_failed`) com `last_sync_at` anterior ao `reconnected_at` | leitura da tela | Atualizando… · Ainda não sincronizou | não | ✓ **PR-A** |
| Erro temporário (`read_failed`) | E8 com espelho cheio | **mantém Erro temporário** | não | ✓ **corrigido no PR-A (R5)** |
| Erro temporário (`read_failed`) | E8 com espelho vazio | mantém | não | ✓ |
| Erro temporário (`read_failed`) ou Parcial (`investments_read_failed`) | E12 sem ninguém tocar | mantém (correto), mas ninguém relê sozinho; no Erro temporário o detalhe "Tentaremos de novo automaticamente" promete o que ainda não existe | não | ✗ D3=A (PR-B2) |
| Erro temporário (`status=ERROR` do webhook) | E8 depois do `health` envelhecer | observa: ACTIVE ou "Ação necessária" | avisa até lá | ✓ tardio (R3, PR-C) |
| Sem dados (`no_accounts`) | E1 atrasado | Atualizando… (sem health), motivo apagado | – | ✗ R8 (PR-C) |
| Sem dados (`no_accounts`) | E8 com espelho cheio | Atualizado | não | ✓ |
| Conexão perdida | E3 atrasado | mantém | avisa | ✓ |
| Conexão perdida | E8 com item vivo | sai | para | ✓ |
| Pausado / Removido | qualquer webhook, E6, E7, E8 | mantém (terminal) | não | ✓ |
| qualquer | E9 | zera `health` e motivo: Atualizando… ou instrução de device | calado conforme o prazo | ✓ fora de corrida; ✗ com o job de saúde em voo (linha "E9 no meio do `GET /items`" acima, PR-C) |
| qualquer | E10 | linha apagada, marca `removed`; webhook tardio não ressuscita | – | ✓ (Onda 4) |

Testes das células do PR-A: `tests/test_of_leitura_incompleta.py`. Do PR-B1:
`tests/test_of_coleta_sem_fim.py` (prazo, R1, R1b, irmão com `health`) e
`tests/test_of_marca_de_falha.py` (a marca de falha, células da §2.1).

### 2.1 A marca de falha do PR-B1, célula por célula

Um sync falha; o que a linha e a tela dizem depende do motivo gravado quando o
run começou, do que aconteceu no meio e de onde ele falhou: **G** = no próprio
`GET /items` (sem foto: só a F); **L** = depois da foto (`/accounts`,
transações, escrita: a O e depois a F). As regras estão no item 8 do contrato.
Os testes ficam em `tests/test_of_marca_de_falha.py`, com o número da célula no
nome (`test_c9b_…`).

| # | motivo no início | evento durante o run | onde | resultado: motivo e tela | teste |
|---|---|---|---|---|---|
| 1 | nenhum | nenhum | G, L | `read_failed` (L: e a foto, `ACTIVE`). **Erro temporário** | `test_of_coleta_sem_fim.py::test_sync_de_fundo_que_falha_…` |
| 2 | qualquer | sync bom | G, L | a falha não grava (par mudou). **Atualizado** | `c2_…`, `c2_c3_lote_…[sync_bom]` |
| 3 | qualquer | reconexão | G, L | não grava (par mudou). **Atualizando…** | `c3_…`, `c3_L_…`, `c2_c3_lote_…[reconexao]` |
| 4 | nenhum | sync lê zero contas (`no_accounts`) | G | mantém. **Sem dados** | `c4_c5_…[c4_no_accounts]` |
| 5 | nenhum | sync vê 404 (`item_missing`) | G | mantém. **Conexão perdida** | `c4_c5_…[c5_item_missing]` |
| 6 | `no_accounts` | sync lê zero de novo | G, L | mantém. **Sem dados** | `c6_…` |
| 7 | `item_missing` | nenhum | G | mantém (sem foto). **Conexão perdida** | `c7_…` |
| 8 | `item_missing` | nenhum; o run vê o item vivo | L | `ACTIVE`/`read_failed` e a foto. **Erro temporário** (DECISÃO 2 = A) | `c8_…` |
| 9 | `item_missing` | job vê o item vivo e limpa | G | `read_failed`. **Erro temporário** | `c9_…` |
| 9b | `item_missing` | idem | L | a O recusa (`observed_at` do job); a F grava. **Erro temporário** | `c9b_…` |
| 9c | `no_accounts` com espelho cheio | job limpa (`has_data`) | G | `read_failed`. **Erro temporário** | `c9c_…` |
| 10 | nenhum | job mede e mantém sem motivo | G, L | `read_failed`. **Erro temporário** | `c10_…` |
| 11 | nenhum | job vê 404 | G, L | mantém `item_missing`. **Conexão perdida** | `c11_…` |
| 12 | `read_failed` | nenhum | G, L | regrava. **Erro temporário** | `c12_c17_…[c12-…]` |
| 13 | nenhum | webhook `item/error` | G | `read_failed`, `status` segue `ERROR`. **Erro temporário** | `c13_…[c13_G]` |
| 13b | nenhum | webhook `item/error` | L | a foto troca `ERROR` por `ACTIVE`; tela igual. O aviso proativo, que lê `status`, deixa de sair até o PR-D | `c13_…[c13b_L]` |
| 14 | qualquer | item readotado (linha nova) | G | a marca vai pelo `id` antigo: a linha nova fica intocada | `c14_…` |
| 15 | qualquer | `PAUSED` ou `DELETED` | G | não grava (terminal) | `c15_…` |
| 16 | nenhum | webhook `item/created` | G, L | `read_failed`. **Erro temporário** | `c16_…` |
| 17 | `investments_read_failed` | nenhum | G, L | `read_failed`. **Erro temporário** | `c12_c17_…[c17-…]` |
| 18 | `no_accounts` | nenhum | L | mantém, e a foto não é gravada. **Sem dados** (DECISÃO 1 = A) | `c18_c19_…[c18_L]` |
| 19 | `no_accounts` | nenhum | G | mantém. **Sem dados** (DECISÃO 1 = A) | `c18_c19_…[c19_G]` |
| 20 | `item_missing` | job vê 404 de novo depois da foto | L | a O recusa (`observed_at`); mantém. **Conexão perdida** | `c20_…` |
| 21 | `item_missing` | job vê o item vivo depois da foto | L | = 9b | `c9b_…` |
| 22 | `no_accounts` | nenhum; a foto diz `LOGIN_ERROR` | L | a O grava `ERROR` e a foto (sem lista); a F grava `read_failed`. **Ação necessária · Reautorize o banco** | `c22_foto_…`; com um `no_accounts` mais novo no meio, a O recusa: `c22_error_depois_…` |
| 23 | nenhum | sync bom Parcial (`investments_read_failed`) | G | não grava (par mudou). **Parcial** | `c23_…` |
| 24 | nenhum | sync lê zero contas com `/investments` falhando (`read_failed`) | G, L | regrava o mesmo valor. **Erro temporário** | = 12 |
| 25 | nenhum | outra falha concorrente | G, L | as duas gravam o mesmo valor | sem teste (idempotente) |
| 26 | nenhum | só os attempts do próprio run | L | grava (o attempt não mexe no par nem no `observed_at`) | `c26_…` |
| 27 | nenhum | nenhum, pelo lote do Atualizar | L | grava pela linha do snapshot. **Erro temporário** | `c27_…` |
| 28 | — | dois donos do mesmo item desde o início do run | G | nada gravado em nenhuma linha (a captura do sync de fundo e o sync levantam `AmbiguousItemError`; no lote, a F recebe a exceção e sai). Com um dono e o banco fora só na captura do sync de fundo, a O roda e grava na linha do dono | `c28_…`, `c28c_…` |
| 28b | — | um segundo dono aparece depois da leitura inicial e antes da releitura de posse | L (a releitura levanta `AmbiguousItemError`) | nada gravado em nenhuma linha: nem a O nem a F (Codex #718). Antes, a O gravava a foto e `read_failed` na linha do 1º dono | `c28b_…[sync, bg, lote]`; positivo `c28d_…` |
| 29 | nenhum | sync de fundo: 1ª tentativa falha em L, as outras em G | L, G | a O da 1ª grava; a F final regrava. **Erro temporário** já durante o backoff | `c29_…` |
| 30 | nenhum | `sync_in_progress` (devolvido, não levantado) nas 3 tentativas do sync de fundo, ninguém sincroniza | — | a F marca `read_failed` (antes: nada, e a 1ª conexão ficava **Atualizando…** até o prazo e depois "demorando mais que o normal"). **Erro temporário**; a promessa "Tentaremos de novo" é do PR-B2 | `c30_…` |
| 30b | nenhum | o mesmo, mas o sync que segurava o lock termina bem antes da marca | — | a F recusa (o par mudou). **Atualizado** | `c30b_…` |
| 30c | nenhum | `sync_in_progress` na 1ª tentativa e sucesso na 2ª | — | sem marca. **Atualizado** | `c30c_…` |

**Conserto de classe previsto para as corridas do job (PR-C):** o CAS do PR-A
compara só `status_reason`, que é o dado de que a decisão do job depende, e
por isso só cobre o caso em que o motivo muda. As células ✗ de corrida acima
são escritas concorrentes que não mudam o motivo. O conserto previsto é um CAS
pela versão da linha (`updated_at` lido na listagem) no `observar_item` que o
PR-C extrai de `run_of_health_check`, usado também pelo caminho do 404.

---

## 3. Decisões do dono (2026-09-27) e quem as implementa

Todas decididas. O PR-A não implementou nenhuma; o PR-B1 implementa a D1. Nenhum
dos dois altera o texto que a D3 vai tornar verdade ("Tentaremos de novo
automaticamente").

| decisão | escolha | PR |
|---|---|---|
| D1: por quanto tempo "Atualizando…" é honesto sem sync | 30 min desde a autorização atual; depois pílula âmbar, mesma "Atualizando…", detalhe "Está demorando mais que o normal — atualize de novo" (texto trocado pelo dono em 2026-09-30: o app não tem botão Atualizar) | PR-B1 (**implementada**) |
| D2: o que a tela diz com dado antigo | âmbar só com prova (Pluggy com dado mais novo que o nosso); sem limite de idade absoluta até medir o auto-update da Pluggy | sem PR atribuído no plano da Onda 5 (a atribuir) |
| D3: alguém tenta de novo sozinho quando nosso dado está atrás | sim: o tique de saúde agenda sync para conexões com dado atrás (motivo de leitura pendente, coleta vencida, Pluggy à frente), teto K por tique, só GET. "Tentaremos de novo automaticamente" passa a ser verdade | PR-B2 |
| D4: quais estados geram o aviso "reconecte" | só `needs_user_action` sem instrução de dispositivo e `item_missing`; a mesma função da tela | PR-D |
| D5: prazo da instrução de device/QR com `health` medido | a mesma `JANELA_DEVICE_AUTH_MIN` (`core/services/pluggy_health.py`), ancorada na autorização atual, nos dois ramos | PR-D |
| D6: como os Ajustes acompanham a coleta | relê o snapshot em 5/10/20/40 s e depois a cada 60 s, para no estado final ou em 30 min, pausa com a aba oculta, relê no `visibilitychange` | PR-E |
| D7: "Última sync" mostra a data de quê | mantém "Última sync" e acrescenta "· dados de dd/mm" quando a data do banco difere mais de 1 dia | sem PR atribuído no plano da Onda 5 (a atribuir) |

Texto novo do PR-B1, visível ao usuário: o detalhe "Está demorando mais que o
normal — atualize de novo" (pílula "Atualizando…", âmbar).

Texto novo do PR-A, visível ao usuário: o detalhe
"Investimentos não vieram nesta atualização" (pílula "Parcial").

## 4. Achados registrados, fora do escopo da Onda 5

- **Concordância do detalhe da Pluggy.** `_stale_detail` escreve "Investimentos
  desatualizado desde dd/mm" (e "Transações desatualizado") para produto de nome
  plural. Pré-existente, não corrigido.

- **Painel admin cego para falha de leitura nossa.** `of_health_counters`
  (`db/open_finance_state.py`) conta `ativas` por `status` e `parciais` só por
  `health.stale_products`. Conexão "Parcial" por `investments_read_failed` e
  conexão com `read_failed` têm `status=ACTIVE` e entram em `ativas`: o painel não
  as distingue das saudáveis. Sem PR atribuído.
