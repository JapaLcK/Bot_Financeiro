# Open Finance: estados da conexão, fontes e eventos

Referência do que a tela ("Ajustes > Open Finance"), o toast do Atualizar e o
aviso proativo dizem sobre uma conexão Pluggy, de onde cada informação vem e o
que cada evento faz com ela. Criado na Onda 5 (PR-A). **Cada PR da Onda 5
atualiza as células que corrige**: esta tabela é o aceite "estados documentados",
num lugar que teste nenhum precisa ler.

Código de referência:

- `core/services/pluggy_health.py`: `resolve_connection_state` (DECIDE o par
  `status` + `status_reason` a partir de uma observação, para o sync, o job de
  saúde e o 404; quem grava é `mark_sync_result`) e `connection_ui_state` (única que decide
  o estado exibido, um dos de `_LABELS`). A máquina de estados por escrito está no
  topo desse módulo.
- Também GRAVAM `status` e/ou `status_reason` sem passar pelo resolvedor: o
  webhook (`update_pluggy_open_finance_item_status`), a reconexão
  (`save_pluggy_open_finance_item`, linha G), `_sync_item_contido` (`read_failed`
  com `status` intocado) e a pausa (`pause_open_finance_connection`, `PAUSED`).
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
   do job de saúde ou do sync. Webhook não limpa. *Vigente.*
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
   o que houve. *Hoje não tem limite* (R1, PR-B1).
9. **Aviso proativo = função do mesmo estado da tela** (`connection_ui_state`), não
   um classificador paralelo por `status`. *Proposto (D4, PR-D).*
10. **`health` e `status` local discordando** (`status=ERROR` do webhook com
    `health.item_status=UPDATED`): hoje `ERROR` vence. *Proposto:* não comparar
    relógios; a observação imediata do item 5 resolve. Até ela terminar, `ERROR`
    continua vencendo, que é o lado conservador.

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
| Atualizando, 1ª conexão sem sync | E2 com sync levantando (5xx/429/rede) | Atualizando… para sempre | não | ✗ R1 (PR-B1) |
| Atualizando, 1ª conexão sem sync | processo reinicia no meio do sync | Atualizando… para sempre | não | ✗ família R1 (PR-B1) |
| Atualizando, 1ª conexão sem sync | E8 | Atualizando… ("Ainda não sincronizou") | não | ✗ R1: o tique mede e não recupera (PR-B1, PR-B2) |
| Atualizando, 1ª conexão sem sync | E12 (horas) | Atualizando… | não | ✗ sem limite (D1, PR-B1) |
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

Testes das células do PR-A: `tests/test_of_leitura_incompleta.py`.

**Conserto de classe previsto para as corridas do job (PR-C):** o CAS do PR-A
compara só `status_reason`, que é o dado de que a decisão do job depende, e
por isso só cobre o caso em que o motivo muda. As células ✗ de corrida acima
são escritas concorrentes que não mudam o motivo. O conserto previsto é um CAS
pela versão da linha (`updated_at` lido na listagem) no `observar_item` que o
PR-C extrai de `run_of_health_check`, usado também pelo caminho do 404.

---

## 3. Decisões do dono (2026-09-27) e quem as implementa

Todas decididas. Nenhuma está implementada no PR-A, que também não altera o
texto que a D3 vai tornar verdade ("Tentaremos de novo automaticamente").

| decisão | escolha | PR |
|---|---|---|
| D1: por quanto tempo "Atualizando…" é honesto sem sync | 30 min desde a autorização atual; depois pílula âmbar, mesma "Atualizando…", detalhe "Está demorando mais que o normal — toque em Atualizar" | PR-B1 |
| D2: o que a tela diz com dado antigo | âmbar só com prova (Pluggy com dado mais novo que o nosso); sem limite de idade absoluta até medir o auto-update da Pluggy | sem PR atribuído no plano da Onda 5 (a atribuir) |
| D3: alguém tenta de novo sozinho quando nosso dado está atrás | sim: o tique de saúde agenda sync para conexões com dado atrás (motivo de leitura pendente, coleta vencida, Pluggy à frente), teto K por tique, só GET. "Tentaremos de novo automaticamente" passa a ser verdade | PR-B2 |
| D4: quais estados geram o aviso "reconecte" | só `needs_user_action` sem instrução de dispositivo e `item_missing`; a mesma função da tela | PR-D |
| D5: prazo da instrução de device/QR com `health` medido | a mesma `JANELA_DEVICE_AUTH_MIN` (`core/services/pluggy_health.py`), ancorada na autorização atual, nos dois ramos | PR-D |
| D6: como os Ajustes acompanham a coleta | relê o snapshot em 5/10/20/40 s e depois a cada 60 s, para no estado final ou em 30 min, pausa com a aba oculta, relê no `visibilitychange` | PR-E |
| D7: "Última sync" mostra a data de quê | mantém "Última sync" e acrescenta "· dados de dd/mm" quando a data do banco difere mais de 1 dia | sem PR atribuído no plano da Onda 5 (a atribuir) |

Texto novo do PR-A, visível ao usuário: o detalhe
"Investimentos não vieram nesta atualização" (pílula "Parcial").

## 4. Achados registrados, fora do escopo da Onda 5

- **Painel admin cego para falha de leitura nossa.** `of_health_counters`
  (`db/open_finance_state.py`) conta `ativas` por `status` e `parciais` só por
  `health.stale_products`. Conexão "Parcial" por `investments_read_failed` e
  conexão com `read_failed` têm `status=ACTIVE` e entram em `ativas`: o painel não
  as distingue das saudáveis. Sem PR atribuído.
