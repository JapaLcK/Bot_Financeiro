# Open Finance — o que só se valida fora daqui

As Ondas 1, 2 e 3 fecharam o comportamento em teste automatizado. Duas frentes
**não** dá para fechar nesta máquina, e este arquivo existe para que elas não
virem "provavelmente funciona": o roteiro está pronto, falta o acesso.

Regra de leitura: o que está na seção **Já provado aqui** não precisa ser
refeito no aparelho. O que está em **A** e **B** nunca foi executado — nem uma
vez, nem parcialmente.

---

## Por que estão bloqueadas

| frente | o que falta | por quê não dá para contornar |
|---|---|---|
| A. app / PWA / WKWebView | um build do app e um aparelho | `env(safe-area-inset-*)` vale 0 no Chromium headless, `contentInset`/elástico/teclado são comportamento nativo, e `matchMedia("(display-mode: standalone)")` não é forçável nem por CDP — medido na Onda 2: `Emulation.setEmulatedMedia` devolve `matches:false` nas duas formas |
| B. Pluggy real / sandbox | credencial de um ambiente Pluggy | todo o comportamento remoto é mockado nos testes. Sem credencial, afirmar qualquer coisa sobre o provedor é inventar |

Enquanto B estiver bloqueada, vale a regra da Onda 3: **não inventar status, não
assumir taxonomia externa.** A única fonte usada é a doc oficial, e o que ela
permitiu concluir está em `core/services/pluggy_health.py`, no comentário do
`resolve_connection_state`.

E a lição de como ela quase deu errado: a tabela "Item Status" do
`docs/item-lifecycle` lista cinco valores, e ler só aquela tabela produziu a
conclusão falsa de que o campo estava fechado em cinco. Um SEXTO
(`WAITING_USER_ACTION`) aparece em payloads de Item completos em
`docs/connect-an-account` e `docs/sandbox`, e o OpenAPI de
`reference/items-retrieve` declara `status` como string **sem `enum`**. Uma
tabela numa página não é a enumeração de um campo — varra a doc inteira, ou
diga que não varreu.

---

## A. Roteiro de aparelho (depois do build)

Quatro cascas, e elas não se substituem — o gate é o mesmo código, mas cada
casca chega nele por um sinal diferente (`app-mode.js:39-42`):

| casca | sinal que liga o `html.pb-app` |
|---|---|
| app iOS (WKWebView) | `navigator.userAgent` contendo `PigBankApp` |
| PWA instalada (iOS) | `navigator.standalone === true` |
| PWA instalada (Android/desktop) | `matchMedia("(display-mode: standalone)")` |
| navegador comum | nenhum — o modo app não liga |

### A1. Botão "↻ Atualizar" (`#of-refresh-btn`, `settings.html:1290`)

A regra é `html.pb-app body.pb-page-settings #of-refresh-btn { display: none }`
(`app-mode.css:641`). Abra **Ajustes → Open Finance** em cada casca:

- [ ] app iOS: botão **escondido**
- [ ] PWA instalada (iOS, ícone na tela de início): botão **escondido**
- [ ] Safari mobile (aba normal, não instalada): botão **visível**
- [ ] Chrome Android (aba normal): botão **visível**
- [ ] desktop web: botão **visível**

Os dois ramos do gate **já têm teste**, e é bom saber disso antes de gastar
aparelho com eles: `of_refresh_ui.test.mjs` injeta `navigator.standalone = true`
(o sinal do iOS) e `app_mode_gate.test.mjs:138` cobre o ramo do `matchMedia` com
um `matchMedia` falso (`:77`). O que nenhum teste alcança é o sinal **real** do
Android/desktop — `matchMedia("(display-mode: standalone)")` não é forçável nem
por CDP (medido no Chromium 151: `Emulation.setEmulatedMedia` devolve
`matches: false`). Ou seja: a lógica está presa, o que falta provar é que a
casca de verdade emite o sinal. Se o botão aparecer numa PWA instalada, o gesto
e o botão convivem — não é catástrofe, é o sintoma de o gate ter caído para
só-UA.

### A2. Puxar a tela (pull-to-refresh)

Em **app iOS** e em **PWA instalada**, na aba Open Finance:

- [ ] o gesto responde (o indicador aparece e gira)
- [ ] ele chama refresh **de verdade**, não só releitura: o `PBRefresh` da aba
      `open-finance` chama `refreshOpenFinance({propagate:true, wait:8})`
      (`settings.html:3568-3590`), que faz PATCH na Pluggy + sync. A prova
      observável é a linha "Última sync" mudar de horário, e não só a tela
      repintar com o mesmo horário
- [ ] sucesso termina **verde**, não âmbar. O `wait=8` existe porque o watchdog
      do indicador é 12s (`app-mode.js:669`); se todo sucesso estiver saindo
      âmbar, os dois números saíram de sincronia
- [ ] falha (modo avião no meio do gesto) termina **âmbar com mensagem**, nunca
      verde
- [ ] sem flicker, sem botão fantasma piscando antes de sumir, sem indicador
      preso girando depois que a tela já atualizou

### A3. Pílula de estado

- [ ] conexão sem sync posterior à autorização atual mostra **âmbar** +
      "Ainda não sincronizou" (não "Tudo em dia!")
- [ ] a cor no aparelho corresponde à classe: `pending` = âmbar, `error` =
      vermelho, `active` = verde. Aqui só se mediu a **classe CSS**, nunca o
      pixel em iOS

### A4. Dashboard

- [ ] com o dashboard aberto, disparar um sync (pelo botão no desktop, ou
      esperando um webhook): o dashboard atualiza **sozinho**, sem F5
- [ ] uma rajada de webhooks (`item/updated` + `transactions/created` chegam com
      segundos de diferença) produz **um** refresh, não três — o debounce é de
      1500 ms (`dashboard.js:7201-7208`)

---

## B. Roteiro de Pluggy real ou sandbox

Cada caso abaixo tem um resultado observável na tela **e** uma linha no banco.
Confira os dois: a tela pode estar certa por acaso.

Consulta útil enquanto roda:

```sql
select status, status_reason, last_sync_at, reconnected_at, health->>'item_status'
  from open_finance_connections where provider_item_id = '<item>';
```

| # | cenário | esperado no banco | esperado na tela |
|---|---|---|---|
| B1 | item saudável saindo de `ERROR` | `status=ACTIVE`, `status_reason=''`, `last_sync_at` avança | "Atualizado" |
| B2 | item em `LOGIN_ERROR` | `status=ERROR`, `health.item_status=LOGIN_ERROR` | "Ação necessária" — **continua** em erro |
| B3 | item deletado na Pluggy (`GET /items/{id}` → 404) | `status=ERROR`, `status_reason=item_missing`, espelho **intacto** | "Conexão perdida" |
| B4 | B3 seguido de webhook `item/updated` atrasado | `item_missing` **preservado** | não volta a verde sozinho |
| B5 | corretora (0 contas, carteira em `/investments`) | `ACTIVE` + `no_accounts` só se `/investments` também veio vazio | "Sem dados" |
| B6 | 429 em `/investments` no meio do sync | `ACTIVE` + `read_failed`, e as contas já lidas **não** são descartadas | "Erro temporário" |
| B7 | sync real concluído | `last_sync_at` muda | "Atualizado" |
| B8 | reconexão pelo widget (upsert), **sem** sync depois | `last_sync_at` **não** muda, `reconnected_at` recebe agora, `health` e `status_reason` zerados | **não** diz "Atualizado" |
| B9 | B8 seguido de sync real | `last_sync_at > reconnected_at` | "Atualizado" |
| B10 | sync concluído com o dashboard aberto | — | `open_finance_synced` chega e o dashboard repinta sozinho |
| B11 | qualquer um dos acima | `settings.html` **não** abre WebSocket nenhum (hoje: zero ocorrências de `new WebSocket`) | — |
| B12 | reconectar **enquanto** um sync escreve | a reconexão espera o `pluggy_item_lock`; só devolve 503 se as duas tentativas falharem | erro só no 503, e o mesmo POST reaproveita |
| B13 | frequência real do 503 de B12 | contar `of_reconnect_lock_timeout` nos logs por uma semana | — |
| B14 | que status a Pluggy JÁ nos devolveu | `select distinct status from open_finance_connections where provider='"'"'pluggy'"'"'` — leitura pura | — |
| B15 | existe conector com QR / autorização por dispositivo na base? | `WAITING_USER_ACTION` no resultado de B14, ou nos logs | decide o tamanho real da mudança da Onda 3 |

**B14 é o que fecha a última faixa de dúvida da taxonomia.** Linha gravada
ANTES da Onda 1 tem exatamente `health` NULL, `last_sync_at` preenchido e
`reconnected_at` NULL — porque naquele código o upsert carimbava `last_sync_at` e
as outras duas colunas não existiam. O `status` dela é o que a Pluggy disse na
hora, sem filtro. Se algum dia veio um valor fora dos conjuntos conhecidos,
aquela linha mostra "Atualizado" até hoje. Nenhum caminho de escrita ATUAL
produz essa combinação (os quatro escritores foram varridos: upsert,
`mark_sync_result`, `update_pluggy_open_finance_item_status` com seu mapa fixo de
três, e a pausa) — mas o passado não passou por eles. É hipótese estrutural, não
achado, e uma query de leitura resolve.

**B8 é o caso central das Ondas 1–3.** Foi ele que produzia "Tudo em dia!" com
"Última sync: pendente" na linha logo abaixo e zero contas espelhadas.

---

## Já provado aqui (não refaça no aparelho)

Isto está coberto por teste automatizado e por medição; o aparelho não vai
acrescentar informação. Uma linha da tabela é exceção e está marcada: o
`scripts/of_corrida_dois_processos.py` **não roda em CI e ninguém o invoca** —
é reprodutor manual, então uma regressão na guarda de geração não fica vermelha
sozinha. Ele também **não** mede o `pluggy_item_lock`: medido, com o
`pg_advisory_lock` desligado ele ainda sai `OK`, porque o handshake entre os dois
workers os serializa e nunca há disputa. Quem prova o lock é o
`tests/test_of_concurrency.py`.

| o quê | onde |
|---|---|
| `PAUSED` aborta o sync; `DELETED` aborta e é terminal; `ERROR` é recuperável | `tests/test_of_connection_state.py` |
| `ERROR → ACTIVE` só depois de consultar o Item remoto, confirmá-lo saudável e concluir sync real | idem |
| espelho vazio com Item saudável não vira terminal; com `LOGIN_ERROR` continua em erro | `tests/test_of_health.py` |
| `item_missing` preservado; `status_reason` carregando `no_accounts`/`read_failed` | ambos |
| `last_sync_at` só muda depois de sync real, e só posterior à autorização atual | `tests/test_of_connection_state.py` (Onda 2) |
| reconexão/upsert não marca "Atualizado" | idem |
| os SEIS `status` de Item que a doc da Pluggy mostra, ponta a ponta | `tests/test_of_health.py` (Onda 3) |
| o ramo `matchMedia("(display-mode: standalone)")` do gate (com `matchMedia` falso) | `tests/frontend/app_mode_gate.test.mjs` |
| botão Atualizar escondido no `pb-app` e visível no mobile web | `tests/frontend/of_refresh_ui.test.mjs` |
| `PBRefresh` da aba OF chama o refresh real, e `settings.html` não abre WebSocket | idem |
| rajada de `open_finance_synced` vira um `get_month` só | idem |
| a guarda de geração (`stale_authorization`) valendo entre **dois processos reais** | `scripts/of_corrida_dois_processos.py` (Onda 3) — ver ressalva abaixo |
| reconexão esperando o `pluggy_item_lock`, e o 503 quando ele não vem | `tests/test_of_concurrency.py` |

O gate da PWA é o único item do frontend com cobertura parcial, e a parte que
falta é estreita: a LÓGICA dos dois sinais está coberta (`navigator.standalone`
de verdade, `matchMedia` com um duplo); o que não dá para exercitar aqui é o
sinal real do `matchMedia`, que nem por CDP se force.

---

## Duas limitações que a Onda 2 fechou depois desta lista nascer

Estão registradas porque a versão anterior deste arquivo as listava como
permanentes, e quem lembrar do texto velho vai procurá-las:

- **Janela residual da reconexão** — fechada em `df0da07`. A rota `/pluggy/item`
  passou a gravar **dentro** do `pluggy_item_lock`. O `8025a11` pegou o lock mas
  ainda gravava sem ele quando o teto estourava, só logando aviso — ou seja,
  anulava o conserto exatamente no caso em que ele importava; quem fecha de
  verdade é o `df0da07`. O motivo não era a tela: o
  run daquela fresta também rodava `import_open_finance_launches` e
  `import_open_finance_credit`, que criam lançamento e compra de cartão. O
  carimbo era recusado, mas nenhum sync posterior remove lançamento — sobrava
  transação fantasma de uma conta que o usuário tinha desmarcado no
  consentimento novo. Espelho velho é chato; isso é dinheiro errado na tela.
- **Sync em voo que não se autocurava** — fechado em `731d0c7`. O
  `sync_in_progress` voltava como dict, não exceção, e encerrava a tarefa de
  fundo em silêncio; agora é retentado com backoff. `stale_authorization`
  continua **não** sendo retentado de propósito: significa "alguém mais novo
  assumiu", e quem assumiu já agendou o próprio sync.

## Limitações que continuam

1. **Reconexão pode devolver 503.** Com o lock ocupado nas duas tentativas
   (`_RECONNECT_LOCK_ATTEMPTS`, cada uma esperando até `OF_SYNC_LOCK_WAIT_MS`),
   a rota recusa em vez de gravar. É recuperável de propósito — o item continua
   na Pluggy e o mesmo POST reaproveita — mas o usuário vê um erro. Quanto isso
   acontece na prática **não foi medido em produção**: entra no roteiro B.
2. **Relógio do Python, não do banco.** `reconnected_at` e `last_sync_at` vêm de
   `datetime.now()`. Processos com relógios diferentes podem inverter a
   comparação e deixar âmbar transitório.
3. **Status de Item fora dos seis conhecidos** cai adiante como saudável — seja
   um publicado depois desta leitura, seja um que já exista numa página que
   ninguém varreu (foi assim que o sexto passou despercebido). Os seis de hoje
   estão presos por teste; o campo não tem `enum` no OpenAPI.
4. **`executionStatus` de erro no `status` LOCAL lê como saudável.** O upsert
   grava `item.get("status") or item.get("executionStatus")`, então um
   `MERGE_ERROR` pode chegar à coluna `status`; `connection_ui_state` compara
   essa coluna com `_NEEDS_USER`/`_UPDATING`, e o que não está nelas passa.
   Medido: `MERGE_ERROR` no status local devolve "Atualizado" — **mas** só com
   `last_sync_at` preenchido E `reconnected_at` NULL, combinação que o upsert de
   hoje não produz. Armadilha latente, não sangramento.
5. **`SKIP_INIT_DB=1` num banco sem a coluna `reconnected_at`** faz **quatro**
   queries de caminho principal estourarem `UndefinedColumn` — medido num banco
   com a coluna derrubada: `save_pluggy_open_finance_item` (o upsert da rota),
   `get_connections_by_item_id` (webhook e sync), `get_open_finance_snapshot`
   (a aba OF) e `mark_sync_result` (fim do sync).
6. **Cor de pílula em iOS**: mediu-se a classe CSS, não o pixel.
