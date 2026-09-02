# Armadilhas conhecidas do PigBank

Corpo do **§5 do `CLAUDE.md` da raiz**. Ficou aqui porque é referência: só se lê
ao mexer na área, não a cada tarefa. O §5 continua existindo lá como ponteiro —
as citações `CLAUDE.md §5` espalhadas pelo código e pelos testes seguem válidas.

### Frontend: o app é o mesmo site

O app iOS (Capacitor, `mobile/`) carrega `https://pigbankai.com` num WKWebView com
`allowNavigation` para o domínio inteiro. Consequências que já causaram bug:

- **O ajuste nativo está DESLIGADO — o CSS é a única proteção.**
  `mobile/capacitor.config.json` traz `"contentInset": "never"`, o que desliga o
  ajuste do `UIScrollView` nos quatro lados, em todas as rotas do domínio. Antes do
  #56 era `"automatic"` e o WebView reservava a área segura sozinho. Saiba em qual
  dos dois mundos você está antes de somar `padding`: com `automatic`, inset em CSS
  **duplica** o espaçamento; com `never`, a falta dele deixa conteúdo sob o notch.
- **Quem carrega o CSS ≠ quem é alcançável.** Das páginas em `frontend/`
  (`ls frontend/*.html | wc -l`; a soma das linhas abaixo tem de bater com ela):

  | quantas | o que carregam | quem |
  |---|---|---|
  | 6 | `app-mode.css`/`app-mode.js` | `login`, `cadastro`, `home`, `dashboard`, `comandos-app`, `settings` — mais a `changelog`, que carrega **os dois** (`changelog.html:15` shim, `:16-17` app-mode) e está contada na linha de baixo |
  | 16 | o shim `frontend/safe-area.js` | as estáticas: `index`, `precos`, `termos`, `privacy`, `completar-cadastro`, `comecar`, `suporte`, `agents`, `changelog`, `comandos`, `como-funciona`, `funcionalidades`, `blog-article`, `reset-password`, `whatsapp` — mais a `error`, que **não é rota**: é template servido pelo `error_page_response` (`frontend/routes/shared.py`) em quase toda URL que dá erro. Quase: as **4** exceções são `/webhook` e `/wa/webhook` (403 `text/plain` "forbidden", `adapters/whatsapp/wa_app.py:224,241,255`) e `/fonts/{name}` e `/brand/{path}` (404 de corpo vazio, `static_pages.py:536,559,564,567`) — endpoints de máquina e de subrecurso, que de propósito não gastam 1,4 KB de HTML |
  | 4 | nada, de propósito | `admin-login`, `admin-dashboard`, `preview_agentes`, e o `ddf99f17-…` — o `_dash_mockup` saiu no PR #209, e o `tests/test_frontend_assets_e_rotas.py` agora reprova página sem rota |

  As duas páginas geradas em Python (bullet seguinte) também carregam o shim.
  `env(safe-area-inset-*)` aparece em **seis** arquivos de `frontend/`
  (`git grep -l safe-area-inset -- frontend/`): os dois que implementam o tratamento
  — `app-mode.css` e `safe-area.js` — e mais quatro que trazem o próprio inset inline:
  `home.html`, `precos.html`, `admin-login.html` e `admin-dashboard.html`. Os dois
  últimos são páginas que, de propósito, não carregam nem o app-mode nem o shim.

  Isso importa porque **qualquer rota do domínio abre no app**: o "Ver planos" do
  dashboard leva ao `/precos`, que está na linha do shim — coberta por ele, não pelo
  app-mode. Se alguma da linha "nada, de propósito" virar rota alcançável pelo app,
  precisa entrar no shim; ela não herda nada.

  As duas contagens que este parágrafo trazia **divergiam da tabela acima**: diziam
  14 onde ela diz 16, e 5 depois de o `_dash_mockup` sair. Número repetido fora da
  sua fonte envelhece sozinho (§0.7 e §2), então o texto passou a citar a LINHA —
  quem quiser o número lê a tabela.
- **Existe HTML gerado em Python**, fora de qualquer template:
  `frontend/finance_bot_websocket_custom.py` devolve duas páginas standalone
  (link expirado do `/d/{code}`, descadastro). O `AppDelegate.swift` carrega o
  `/d/{code}` **direto no WebView**. Toda mudança global de frontend esquece essas
  duas — já esqueceu.
- **O modo app é `html.pb-app`**, inerte na web. A classe da página é posta em **dois
  lugares**, e os dois importam:

  | elemento | classe | onde | para quê |
  |---|---|---|---|
  | `<body>` | `pb-page-*` | `app-mode.js:151` | escopar o CSS por página |
  | `<html>` | `pb-root-*` | `app-mode.js:82` (`<head>`, 1º paint) e `:154` | pintar o **canvas** |

  (mais `pb-app` em `:46` e `pb-no-tabs` em `:150`, quando a rota não tem tab bar.)

  O fundo que o elástico revela é o do canvas, e o canvas vem do `<html>` — por isso
  a cor por tela está em `html.pb-app.pb-root-*` (`app-mode.css:54-71`), não no
  `<body>`. Pintar só o `<body>` deixa a faixa do overscroll na cor errada. O modo
  claro do dashboard depende da mesma regra (`app-mode.css:77`).
- **`position: fixed` não herda o padding do `body`.** Todo fixo ancorado numa borda
  precisa reservar a área segura por conta própria.
- **Paisagem está habilitada no iPhone** (`mobile/ios/App/App/Info.plist`), então os
  insets laterais contam. Não trate área segura como assunto de topo.
- **O cache-buster `?v=N` de CSS/JS é reescrito no serve-time — não bumpe à mão.**
  `stamp_asset_versions` (`frontend/routes/shared.py`) troca o `?v=N` de qualquer
  `*.css`/`*.js` de `frontend/` por um hash do conteúdo do arquivo, em toda saída de
  HTML — os **6** call sites: o funil `html_file`, `/suporte`, `/blog/{slug}`, as duas
  páginas geradas em Python e a **página de erro** (`error_page_response`, onde o stamp
  é aplicado no cache do template, não por requisição: trocar um asset sem reiniciar o
  processo deixa só ESSA página com o hash velho até o restart). A página de erro **não**
  passa pelo `html_file` — aquele funil injeta o Meta Pixel, e ela fica fora do rastreio
  de propósito; se você "reusar o html_file" pra ganhar o stamp, reintroduz o pixel
  calado. O número hardcoded no `<head>` (ex.: `app-mode.css?v=29`) é **ignorado** —
  está lá só como resquício; mexer nele não faz nada. A invalidação passou a ser
  automática (muda o arquivo → muda o hash). Antes cada asset era bumpado à mão em
  ~8 HTMLs por PR e `v=31` vs `v=33` na mesma linha dava merge conflict a cada duas
  PRs paralelas. Asset servido de fora de `frontend/` fica intacto.

### Frontend: como a navegação funciona hoje

Não há build, não há bundler, não há framework: o `package.json` da raiz existe **só**
para o harness de testes de frontend (`node --test tests/frontend/*.test.mjs`, com
Playwright). O HTML é escrito à mão e servido pelo FastAPI; templating de servidor
existe em dois lugares só, por `str.replace("{{X}}")` (`/blog/{slug}` e o `{{FAQ}}` do
`/suporte`, ambos em `frontend/routes/static_pages.py`).

Três mundos convivem, e confundi-los produz bug:

| | como navega | quem |
|---|---|---|
| **Páginas públicas** | MPA clássico, request por clique | `index`, `precos`, `funcionalidades`, `termos`, `privacy`, `suporte`, `blog/*`, `whatsapp`, … |
| **Área logada** | MPA clássico **também**, com muito JS por página | `dashboard`, `settings`, `home`, `comecar` (servida em `/onboarding`) |
| **POC de SPA** | `fetch` + troca de DOM, **desligado por padrão** | só `/home` e `/comandos-app`, só no app |

**O POC de SPA é o `frontend/pb-nav.js`, e ele NÃO está ligado em produção.** Ler o
cabeçalho do arquivo antes de tocar em navegação. O que ele é, exatamente:

- Ativa só quando as **três** condições valem: modo app (`html.pb-app`) **e** a flag
  `pbspa` em `sessionStorage` (ligada por `?pbspa=1` dentro do app, desligada por
  padrão, morre com a sessão) **e** a rota estar em `ROUTES` — que hoje tem **duas**
  entradas: `/home` e `/comandos-app` (`pb-nav.js:93`).
- Quando ativo: `fetch` da próxima página, swap dos nós dentro de
  `document.startViewTransition`, `history.pushState`, cache por página (nós, estilos,
  título, scroll) e re-execução dos inits. Existe para matar a piscada preta do
  WKWebView entre documentos, não para virar SPA.
- **Contrato por página convertida**: script inline embrulhado em
  `(PBPages.<key> ||= {inits:[]}).inits.push(fn)`, e o último script marcado
  `data-pb-boot` chamando `PBNav.boot("<key>")`. Quebrar esse contrato quebra a página
  no app e não quebra nada no navegador — o pior tipo de bug daqui.
- **Fallback sempre-navega**: qualquer erro (fetch, timeout de 5s, redirect de auth,
  rota não convertida) cai em `location.href`. O pior caso é o comportamento de hoje.

Ao escrever qualquer coisa nova, o default é **MPA**. Não descreva nem trate a área
logada como SPA, e não presuma que uma migração para framework aconteceu: ela não
aconteceu, nem está decidida.

### `dashboard.js`: o arquivo grande demais

É o maior passivo de organização do repositório e o exemplo vivo da §0.5 do `CLAUDE.md`: milhares
de linhas, centenas de funções e de `const/let` **no escopo global**, dezenas de
seções demarcadas por comentário. Nasceu de um `<script>` inline extraído do
`dashboard.html` ("refactor Fase 1: CSP script-src").

Se for decidir com base no tamanho, remeça — não confie no número que estiver
escrito aqui: `wc -l frontend/dashboard.js`.

Três fatos que decidem qualquer mexida ali:

- **`dashboard.html` tem 139 handlers inline** (`onclick="abrirX()"`) chamando **86
  nomes** distintos. Eles funcionam porque o arquivo é script clássico e tudo no topo
  é global. `settings.html` tem 61 handlers inline e `home.html` tem 11.
- **Trocar por `<script type="module">` sem mais nada quebra os 139** — módulo tem
  escopo próprio, os nomes somem do `window`, o botão para de funcionar **sem erro
  visível**. Enquanto os handlers inline existirem, qualquer divisão precisa devolver
  os 86 nomes ao `window` e ter teste que compare a lista com o HTML.
- **Esses handlers inline são também o que segura o `'unsafe-inline'`** no
  `script-src` da CSP. A Fase 1 já tirou o `<script>` inline; matar os handlers é o
  que falta para fechar o `script-src`.

Ao adicionar funcionalidade nova de dashboard, **prefira arquivo novo** a mais uma
seção nesse arquivo — e ele precisa de rota própria em `static_pages.py` (ver abaixo).

### Assets: não há `StaticFiles` mount

Cada CSS/JS servido tem **uma rota `@router.get` escrita à mão** em
`frontend/routes/static_pages.py`. Arquivo novo em `frontend/` **não é servido
sozinho** — sem a rota, ele dá 404 e o sintoma aparece só no navegador.

Cuidado ao mexer em cache aqui: o `stamp_asset_versions` reescreve `?v=` **no HTML**.
Um `import "./x.js"` dentro de um JS **nunca é carimbado** — módulo ES importado por
outro módulo não pode receber `immutable` enquanto não tiver hash no nome.

### Service worker e PWA

`frontend/service-worker.js` (`CACHE_NAME` versionado à mão — o valor atual sai de
`grep CACHE_NAME frontend/service-worker.js`):
HTML e auth nunca são cacheados, assets são network-first com fallback de cache,
API e WebSocket passam direto. O `manifest.json` tem `start_url: "/login"` — e a
`index.html` tem um guard no topo que manda a PWA instalada para `/login`, com saída
por `?site=1`. Mexeu no `frontend/service-worker.js` — QUALQUER diff, typo em comentário
incluso? Bumpe o `CACHE_NAME` **e o `VERSAO_ATUAL` de
`tests/frontend/sw_cache_privado.test.mjs`**, para o mesmo N — um sem o outro fica
vermelho em `node --test tests/frontend/sw_cache_privado.test.mjs`. O CI normalmente reprova quem
esquecer, desde o #197.

**Não é para entregar o código novo.** Isso já acontece sozinho: a rota serve o arquivo
com `Cache-Control: no-cache` (`frontend/routes/static_pages.py`), o `install` encadeia
`skipWaiting()` depois do `addAll` e o `activate` chama `clients.claim()`. Desde que o
`install` complete, o worker novo assume com ou sem bump.

O que o bump faz é apagar o **conteúdo**: o `activate` só apaga cache de nome DIFERENTE
do atual. Sem bumpar, a regra nova só alcança gravação NOVA — e quem decide o que entra é
a allowlist do `podeCachear`, não o `PRECACHE`: item tirado do precache que a allowlist
ainda aceita volta a ser cacheado em runtime, e é o caso do Chart.js. O que a versão
anterior já guardou — inclusive dado privado — continua no aparelho.

### `pending_actions`: uma linha por usuário, ~100 lugares mexendo nela

A tabela de pendências guarda **uma linha por usuário**. Toda escrita compete com todas
as outras: ~48 pontos gravam e 57 consomem. Escrever ou apagar sem condição atropela a
pendência que outra tarefa acabou de pôr lá — e o usuário responde uma pergunta que o bot
fez e leva "não entendi", ou pior, o valor dele vai para a pergunta errada.

**A disciplina é gravar e apagar só se ainda for o que você leu:**

- `db.claim_pending_action` para gravar (aplica a ordem de prioridade);
- `db.advance_pending_action(uid, tipo_lido, payload_lido, None)` para consumir;
- `db.create_pending_action_if_absent` quando só se cria se não houver nada.

`set_pending_action` e `clear_pending_action` crus só quando você **não leu nada antes** —
e diga por quê num comentário.

**A ordem de prioridade** (`db/pending.py`): PERGUNTA nunca é desalojada; OFERTA DE
CONVENIÊNCIA cede; a mesma pergunta por outra porta substitui. O teste de qual é qual é
observável, não é opinião: **oferta de conveniência é a que o
`_send_reply_with_optional_buttons` consome no mesmo turno** (`wa_runtime.py`). Classificar
sem olhar isso já custou caro — `confirm_recurring_offer` foi posta como oferta, e o "sim"
do usuário passou a matar duas pendências de uma vez.

> Escrita incondicional apareceu **cinco vezes** no mesmo PR — reivindicar, devolver,
> abandonar, gravar e consumir — corrigida uma por rodada porque ninguém varreu os irmãos.
> Antes de fechar qualquer conserto aqui, `grep` os outros pontos.

**Uma tabela, três perguntas diferentes.** O `_REGISTRO` (`db/pending.py`) tem uma
linha por tipo de pendência e três colunas. As perguntas continuam sendo três — elas
divergem de propósito — mas se respondem no mesmo lugar:

| coluna | pertencer significa | pergunta nova… |
|---|---|---|
| `oferta` | **pode ser desalojada** por uma pergunta | fica **False** — True, o `claim_pending_action` a apaga |
| `suprime_ia` | **suprime o fallback da IA** enquanto está de pé | True **só se** a resposta chegar pelo `handle_incoming` |
| `sobrevive_audio` | **não é sobrescrita** por `undo_audio` | True se um áudio no meio dela puder atropelá-la |

O critério de cada uma é observável, não é gosto:

- **é oferta de conveniência?** só se o `_send_reply_with_optional_buttons`
  (`wa_runtime.py`) a consome no mesmo turno. Se ela espera resposta do usuário, é
  pergunta — mesmo tendo "offer" no nome.
- **precisa suprimir a IA?** só se a resposta natural do usuário chega até o
  `handle_incoming` E o classificador não a reconhece. `bill_pay_amount` está **False**
  de propósito: o runtime do WhatsApp a consome antes, e ligá-la suprimiria a IA sem
  motivo. Quem é respondida com "sim"/"não" também fica False — o classificador
  devolve `confirm.yes`/`confirm.no` com confiança alta.
- **sobrevive a áudio?** só se perdê-la custa trabalho já feito. As confirmações
  destrutivas ficam **False** de propósito: perdê-las é fail-safe, e protegê-las
  reintroduz o footgun "apagar #285" → [áudio] → "sim" (o guard anti-órfão do
  `intent_router` só dispara com comando de TEXTO).

Marcar errado causa bug silencioso nos dois sentidos: pergunta com `oferta=True` perde o
estado sem aviso; oferta com `oferta=False` bloqueia a linha por 10 min; tipo já
consumido pelo runtime com `suprime_ia=True` tira do usuário Pro a IA que ele paga.

**Tipo ausente da tabela = as três colunas False** — o comportamento de hoje para um
tipo não listado, então nada muda em silêncio. O `tests/test_pending_registry.py` varre
o código com `ast` atrás de todo tipo GRAVADO e reprova o que não estiver na tabela;
reprova também a linha órfã que nenhum código grava.

### Validação de entrada: o critério é o dano, não a boa digitação

A pergunta não é "o usuário digitou certo?", é **"o erro dele vira dinheiro errado?"**.

- `1.23.456` é recusado: pagaria R$ 123.456 quando o provável era R$ 1.234,56.
- `1.23,45` passa: paga R$ 123,45, que é o que a pessoa quis dizer. Recusar seria pedir
  para redigitar algo que o bot entendeu.

Sem esse critério a validação apara forma por forma até recusar entrada válida — que é
pior que o bug. E recusar tem de **manter a pergunta viva**: descartar a pendência joga o
usuário no fallback genérico e ele recomeça o fluxo.

`parse_money` aceita quase qualquer coisa e devolve um número (`132 50` → 13250,
`132,50.` → 13250, `1"*400` → `inf`). É a raiz de três apontamentos seguidos e é chamado
por dezenas de fluxos: valide **antes** de entregar a ele, não mexa nele sem PR próprio.

### Componentes fragmentados

Três overlays (`.overlay`, `.pig-modal-overlay`, `.mfa-overlay`) e quatro modais
(`.modal`, `.modal.wide`, `.mfa-modal`, `.pig-modal`) fazem o mesmo trabalho com
contratos diferentes. Qualquer regra transversal precisa ser escrita para os três/quatro
— e conferindo o que cada um já declara. Enquanto isso não for unificado, é imposto
fixo de toda mudança de layout.

### Backend

- **`db/` é um pacote** com ~30 módulos por domínio, não um `db.py` único. O DDL de
  todas as tabelas vive em `db/schema.py::init_db()` — é a fonte de verdade do schema
  (§0.7 do `CLAUDE.md`), e o `docs/CLAUDE.md` aponta para lá em vez de repetir a lista.
- **O monólito ainda existe e ainda cresce.**
  `frontend/finance_bot_websocket_custom.py` é o maior arquivo do backend e ainda
  cresce; concentra auth, MFA, billing, WebSocket, dashboard e o `ConnectionManager`.
  Antes de decidir com base no tamanho, remeça:
  `wc -l frontend/finance_bot_websocket_custom.py`. Parte das rotas já saiu
  para `frontend/routes/` (`static_pages`, `settings`, `pockets`, `cards`,
  `analytics`, `open_finance`, `push`, `agents`, `affiliates`, `shared`), registradas
  por `include_router`. **Rota nova vai para um router de `frontend/routes/`** — não
  para o monólito. O plano completo está em `docs/refactor_plan.md`.
- **Isolamento por usuário é regra dura.** A formulação da regra mora no §0 do
  `CLAUDE.md`, que é auto-carregado — instrução de segurança não pode depender de
  alguém abrir este arquivo. Aqui fica só o lembrete de que ela vale em todo `db/`.
- **`launch.py` sobe dois processos**: o uvicorn (que atende o `$PORT` do Railway) e o
  `bot.py` do Discord. Um `web` no Procfile, dois processos filhos.
- **Tarefas de fundo sobem no startup do app** quando `RUN_BACKGROUND_TASKS != "0"`
  (agendadores de investimento, Open Finance, engajamento, cobrança recorrente…).
  Em teste e no `dashboard_dev.py` isso é desligado — se você ligar sem querer num
  ambiente com banco real, elas escrevem.
