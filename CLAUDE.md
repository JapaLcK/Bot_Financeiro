# PigBank — como trabalhar neste repositório

> **Contexto de domínio** (tabelas, endpoints, fluxos de cadastro, e-mail, roadmap):
> `docs/CLAUDE.md`. Guia de desenvolvimento: `docs/dev_guide.md`.
>
> **Este arquivo é sobre o processo**: o que fazer antes de escrever, como validar,
> e as armadilhas deste código que já custaram caro.

---

## 1. Antes de escrever a primeira linha

**Entenda a queixa antes de diagnosticar.** Relato de usuário quase nunca é
diagnóstico. Se a descrição admite mais de uma causa, pergunte pelo estado em que
elas se separam — não escolha uma e comece a codar.

> Custou um branch inteiro: "aparecem barras pretas ao puxar a tela" tinha duas
> leituras (o preto *durante* o arrasto elástico e uma faixa *permanente* sob o
> status bar). Foi tratada a errada. A pergunta que resolvia era uma só:
> "como fica com a tela parada, sem tocar?"

**Planeje antes, e por escrito.** Para qualquer coisa além de uma correção local:
liste o que vai mudar, onde, e o que pode quebrar — antes de editar. Se o plano
tem mais de três passos ou toca em mais de um arquivo, mostre-o e confirme.

**Pergunte o que a mudança torna verdade no resto do sistema.** A pergunta certa
nunca é só "isso resolve?". Uma flag de configuração de 8 caracteres pode transferir
responsabilidade para todo o produto.

> `contentInset: "never"` no `capacitor.config.json` é uma linha. Ela desliga o
> ajuste nativo de área segura nos **quatro** lados, em **todas** as rotas do
> domínio, em todas as orientações. Virou 21 arquivos e 15 apontamentos de revisão.

---

## 2. Medir o alcance — o inventário

Mudança transversal (CSS global, config nativa, middleware, schema, helper usado em
muitos lugares) **começa por uma varredura, não por uma edição**. O inventário se faz
com `grep`, nunca de memória.

Perguntas que se respondem com busca, não com raciocínio:

- **Quem mais está nesta categoria?** Corrigiu um elemento — o que mais é da mesma
  classe? (`position: fixed`, `100vw`/`100vh`, `bottom:` fixo, `z-index` alto…)
- **Com o que isto faz par?** Elementos costumam vir em pares que precisam mudar
  juntos: botão e painel, overlay e conteúdo, escrita e leitura, migração e query.
- **Quem carrega isto × quem é alcançável?** No frontend as duas listas são
  diferentes, e é aí que mora o buraco (§5).
- **Existe versão gerada fora do template?** Sim, existe (§5).

> O padrão que se repetiu 15 vezes num único PR foi sempre o mesmo erro: tratar
> "achei um caso" como "resolvi a categoria". Corrigiu o FAB e não o painel que é
> par dele; corrigiu os lados do painel e não a altura; varreu os `.html` e esqueceu
> o HTML gerado em Python.

**Seletor parecido não é contrato igual.** Antes de aplicar uma regra a N seletores,
leia o que cada um já declara. `.modal`, `.modal.wide`, `.mfa-modal` e `.pig-modal`
parecem intercambiáveis e não são — um tem largura própria, outro não tem `overflow`.
Isso gerou dois apontamentos separados.

---

## 3. Testar e validar antes de empurrar

**Nunca empurrar sem rodar a suíte.** Ela precisa de Postgres no ar e das variáveis
de ambiente — sem isso o pytest morre com `INTERNALERROR` no import, o que **não** é
falha de teste e não deve ser lido como tal:

```bash
export DATABASE_URL="postgresql://…"     # Postgres acessível
export JWT_SECRET="…"                    # 32+ bytes
export PII_ENCRYPTION_KEY="…" PII_HASH_PEPPER="…"
export PII_AUDIT_DISABLED=1 RUN_BACKGROUND_TASKS=0

PYTHONPATH=. python3 -m pytest -q                 # tudo
PYTHONPATH=. python3 -m pytest tests/test_x.py -q # durante o desenvolvimento
```

Use `python3 -m pytest`, não `pytest` solto — o binário no PATH pode ser de outro
interpretador, sem as dependências instaladas.

O CI (`.github/workflows/tests.yml`) sobe seu próprio Postgres 16 e roda `pytest`
(bloqueante) e `audit` de CVEs (não-bloqueante) em todo push e PR. Não use o CI como
primeiro teste — ele é a confirmação, não a descoberta.

**Antes de afirmar que algo "não existe", confirme contra qual árvore.** Um branch
atrasado em relação à `main` mente com toda a confiança do mundo: o `grep` não acha o
arquivo, o `find` não acha nada, e a conclusão sai redonda e errada. Foi o que
aconteceu neste próprio arquivo — um branch **um** commit atrás da `main` não tinha o
`frontend/safe-area.js` nem as classes `pb-root-*`, ambos criados pelo #56, e as duas
coisas foram documentadas como inexistentes. Antes de escrever "não existe":

```bash
git rev-list --count HEAD..main    # 0 = em dia; qualquer outro número é um aviso
git grep -l "<o que voce procura>" main -- <caminho>
```

`git grep <ref>` consulta a árvore daquele commit sem mexer no working tree — dá para
conferir `main` sem trocar de branch. Vale o mesmo para revisores automáticos: o Codex
lê a árvore **do branch**, então um achado de "isso não existe" num branch atrasado
pode ser artefato do atraso, não um defeito. Cheque antes de aceitar.

**Compare com a baseline, não com zero.** Rode a suíte **antes** de mexer e guarde o
número. Falha que já existia não é regressão sua; falha nova é. Sem a baseline não dá
para distinguir as duas, e sobra "os testes estão vermelhos" sem conclusão.

**A baseline local oscila — compare por nome de teste, não por contagem.** Duas
execuções idênticas, seguidas, deram resultados diferentes:

```
execução A:   9 failed, 984 passed
execução B:  12 failed, 981 passed
```

O núcleo estável são os **7** de `tests/test_statement_import.py`, e eles **não têm uma
causa só** — são dois grupos de dependência ausente (§6). Os demais —
`test_export_email`, `test_nlp_and_pending_flow`,
`test_security_alerts` — aparecem e somem entre execuções: há dependência de ordem ou
de estado compartilhado no banco de teste. **Consequência prática:** um número de
falhas maior que o da sua baseline não prova regressão, e um igual não prova ausência
dela. Compare a **lista de nomes**, e na dúvida rode o arquivo suspeito isolado
(`pytest tests/test_x.py -q`). Isso corta a interferência **dos outros arquivos**, e só
isso: os testes de dentro do arquivo continuam no mesmo processo, na ordem de definição,
compartilhando estado global e as mesmas linhas de banco. Se a falha persistir isolada,
ainda pode ser ordem interna. Para descartar, desça mais um nível: rode o teste
**sozinho** (`pytest "tests/test_x.py::test_y" -q`) e depois varie a ordem passando os
node IDs explicitamente na linha de comando — o pytest executa na ordem em que você os
lista (não há plugin de ordenação instalado aqui; o único plugin é o `anyio`):

```bash
pytest -v "tests/test_x.py::test_b" "tests/test_x.py::test_a"
```

"Passou isolado" nunca é prova de que não há efeito de ordem.

**Frontend também se testa.** Há Chromium com Playwright disponível
(`/opt/pw-browsers/`, use `NODE_PATH=$(npm root -g)`). Mudou layout, mediu; não
"pareceu certo". E as duas perguntas que separam medição de teatro:

1. **Quanto isso daria se a correção não fizesse nada?** Se a resposta for "o mesmo
   número", a medição não mede nada — corrija o teste antes de reportar o resultado.
2. **Que classe de bug esta verificação nunca pegaria?** Se existe uma classe cega,
   ela precisa de outro método (enumeração, revisão linha a linha), porque testar
   mais do mesmo não vai alcançá-la.

> As duas perguntas vêm de erros reais: uma medição de largura de modal que dava o
> mesmo valor com e sem a correção (a CSS do componente nem estava na página), e uma
> bateria inteira de testes de área segura que era estruturalmente cega ao erro que
> estava sendo cometido, porque `env(safe-area-inset-*)` vale 0 no navegador headless.

**Verifique que não quebrou nada em volta.** Toda regra nova de CSS global, todo
helper alterado, toda mudança de schema: confira o caminho vizinho, não só o que você
consertou. `git diff` antes do commit, lido de ponta a ponta.

---

## 4. PR, Codex e threads

**Toda mudança vai por PR.** Nunca empurre direto na `main`.

**Espere o Codex.** Ele revisa automaticamente ao abrir o PR e responde a
`@codex review` num comentário. Não merje antes do parecer dele — mesmo com o CI
verde, mesmo parecendo trivial.

**Responda cada thread, e peça revisão de novo.** O ciclo completo:

1. o Codex aponta;
2. corrija — ou explique por que o apontamento não procede, com evidência;
3. **responda na própria thread**, citando o commit da correção e o que foi medido;
4. comente `@codex review` para ele reavaliar o head novo;
5. repita até ele aprovar ("Didn't find any major issues").

Nunca deixe thread sem resposta e nunca afirme que threads estão respondidas sem
abrir e conferir. **Se você pediu revisão, é sua obrigação ir ler o parecer** — não
espere alguém perguntar.

**Merge só com autorização explícita do dono do repositório.** Aprovação do Codex e
CI verde deixam o PR *pronto*; não autorizam o merge. Avise e pergunte.

**Separe por verificabilidade, não por tamanho.** Se parte do trabalho pode ser
comprovada agora e parte só depois (build de app, deploy, acesso a produção), abra
dois PRs. A metade verificável sobe sem ficar refém da outra.

---

## 5. Armadilhas conhecidas deste repositório

### Frontend: o app é o mesmo site

O app iOS (Capacitor, `mobile/`) carrega `https://pigbankai.com` num WKWebView com
`allowNavigation` para o domínio inteiro. Consequências que já causaram bug:

- **O ajuste nativo está DESLIGADO — o CSS é a única proteção.**
  `mobile/capacitor.config.json` traz `"contentInset": "never"`, o que desliga o
  ajuste do `UIScrollView` nos quatro lados, em todas as rotas do domínio. Antes do
  #56 era `"automatic"` e o WebView reservava a área segura sozinho. Saiba em qual
  dos dois mundos você está antes de somar `padding`: com `automatic`, inset em CSS
  **duplica** o espaçamento; com `never`, a falta dele deixa conteúdo sob o notch.
- **Quem carrega o CSS ≠ quem é alcançável.** Das **25** páginas em `frontend/`:

  | quantas | o que carregam | quem |
  |---|---|---|
  | 6 | `app-mode.css`/`app-mode.js` | `login`, `cadastro`, `home`, `dashboard`, `comandos-app`, `settings` |
  | 14 | o shim `frontend/safe-area.js` | as estáticas: `index`, `precos`, `termos`, `privacy`, `onboarding`, `suporte`, `agents`, `changelog`, `comandos`, `como-funciona`, `funcionalidades`, `blog-article`, `reset-password`, `whatsapp` |
  | 5 | nada, de propósito | `admin-login`, `admin-dashboard`, `_dash_mockup`, `preview_agentes`, e o `ddf99f17-…` |

  As duas páginas geradas em Python (bullet seguinte) também carregam o shim.
  `env(safe-area-inset-*)` mora em exatamente dois arquivos: `frontend/app-mode.css`
  e `frontend/safe-area.js` (`grep -rl safe-area-inset frontend/`).

  Isso importa porque **qualquer rota do domínio abre no app**: o "Ver planos" do
  dashboard leva ao `/precos`, que é uma das 14 — coberta pelo shim, não pelo
  app-mode. Se alguma das **5** virar rota alcançável pelo app, precisa entrar no
  shim; ela não herda nada.
- **Existe HTML gerado em Python**, fora de qualquer template:
  `frontend/finance_bot_websocket_custom.py` devolve duas páginas standalone
  (link expirado do `/d/{code}`, descadastro). O `AppDelegate.swift` carrega o
  `/d/{code}` **direto no WebView**. Toda mudança global de frontend esquece essas
  duas — já esqueceu.
- **O modo app é `html.pb-app`**, inerte na web. A classe da página é posta em **dois
  lugares**, e os dois importam:

  | elemento | classe | onde | para quê |
  |---|---|---|---|
  | `<body>` | `pb-page-*` | `app-mode.js:85` | escopar o CSS por página |
  | `<html>` | `pb-root-*` | `app-mode.js:88` | pintar o **canvas** |

  (mais `pb-app` em `:30` e `pb-no-tabs` em `:84`, quando a rota não tem tab bar.)

  O fundo que o elástico revela é o do canvas, e o canvas vem do `<html>` — por isso
  a cor por tela está em `html.pb-app.pb-root-*` (`app-mode.css:33-36`), não no
  `<body>`. Pintar só o `<body>` deixa a faixa do overscroll na cor errada. O modo
  claro do dashboard depende da mesma regra (`app-mode.css:42`).
- **`position: fixed` não herda o padding do `body`.** Todo fixo ancorado numa borda
  precisa reservar a área segura por conta própria.
- **Paisagem está habilitada no iPhone** (`mobile/ios/App/App/Info.plist`), então os
  insets laterais contam. Não trate área segura como assunto de topo.

### Componentes fragmentados

Três overlays (`.overlay`, `.pig-modal-overlay`, `.mfa-overlay`) e quatro modais
(`.modal`, `.modal.wide`, `.mfa-modal`, `.pig-modal`) fazem o mesmo trabalho com
contratos diferentes. Qualquer regra transversal precisa ser escrita para os três/quatro
— e conferindo o que cada um já declara. Enquanto isso não for unificado, é imposto
fixo de toda mudança de layout.

### Backend

- **`db/` é um pacote**, não o `db.py` único que o `docs/CLAUDE.md` descreve — aquele
  arquivo está desatualizado nesse ponto. As rotas também estão divididas em
  `frontend/routes/`.
- **Isolamento por usuário é regra dura**: toda query com `WHERE user_id = %s`.
  Nunca vazar dado entre usuários.

---

## 6. Limites deste ambiente (não são bugs, não tente contornar)

- **A produção não é acessível.** O proxy bloqueia `pigbankai.com` (403). Não dá para
  conferir o comportamento real do site daqui.
- **CDNs são bloqueados.** Chart.js e afins falham; `applyTheme` do dashboard, por
  exemplo, lança no meio por causa disso. Saiba disso ao interpretar um teste.
- **`env(safe-area-inset-*)` vale sempre 0** no Chromium headless. Regras de área
  segura são inertes aqui; só dá para verificar a aritmética substituindo valores fixos.
- **Comportamento nativo do WKWebView não é reproduzível.** `contentInset`, elástico,
  teclado: só no aparelho, depois do build.
- **Faltam pacotes de `requirements.txt` aqui** — pelo menos `ofxparse`, `reportlab` e
  `pypdf` (o `pip install` falha pelo proxy). A ausência do `ofxparse` não
  faz "alguns testes falharem": são **9 erros de coleta**, e o pytest **interrompe a
  suíte inteira** antes de rodar qualquer teste. Sem tratar isso você não tem sinal
  nenhum — nem verde, nem vermelho. Para obter baseline local:

  A lista é **fixa** — são estes 9, e só estes:

  ```bash
  IGNORADOS=(
    tests/test_audio_clarification.py
    tests/test_audio_multi_launch_ask_value.py
    tests/test_full_handler_smoke.py
    tests/test_handle_incoming_routing.py
    tests/test_recurring_value.py
    tests/test_split_audio_transactions.py
    tests/test_whatsapp_confirmations.py
    tests/test_whatsapp_daily_report.py
    tests/test_whatsapp_simulation.py
  )

  ESPERADO="No module named 'ofxparse'"

  # Guarda: valida CAMINHO **e** CAUSA. Pareia cada "ERROR collecting <arquivo>"
  # com a linha "E <causa>" seguinte — checar só o caminho deixa passar um
  # SyntaxError novo dentro de um dos 9 (o nome do arquivo não muda).
  ERROS=$(PYTHONPATH=. python3 -m pytest -q --collect-only 2>&1 \
    | awk '/ERROR collecting /{f=$0; sub(/.*ERROR collecting /,"",f); sub(/ _*$/,"",f); next}
           f && /^E /{sub(/^E +/,""); print f" | "$0; f=""}')

  INESPERADOS=$(echo "$ERROS" | grep -vF "$ESPERADO" \
    | cat - <(echo "$ERROS" | grep -F "$ESPERADO" | cut -d' ' -f1 \
        | grep -vxF "$(printf '%s\n' "${IGNORADOS[@]}")") | grep .)

  if [ -n "$INESPERADOS" ]; then
    echo "COLETA FORA DO ESPERADO — corrija antes de tirar baseline:"; echo "$INESPERADOS"
  else
    PYTHONPATH=. python3 -m pytest -q "${IGNORADOS[@]/#/--ignore=}"
  fi
  ```

  **Não gere essa lista com `grep ERROR | sed s/^ERROR/--ignore=/`.** Esse atalho
  ignora *qualquer* erro de coleta, inclusive um que a sua própria mudança acabou de
  introduzir: um `ImportError` ou erro de sintaxe num arquivo de teste vira mais um
  `--ignore`, a rodada seguinte passa verde e o arquivo inteiro nunca roda. Testado:
  com um arquivo de sintaxe quebrada em `tests/`, o pipeline montou **10** `--ignore`
  em vez de 9 e engoliu o arquivo quebrado sem uma linha de aviso.

  **E não basta conferir o caminho.** Um `SyntaxError` novo *dentro* de um dos 9 sai
  com o mesmo nome de arquivo, então uma guarda que só compara caminhos não vê nada e
  o arquivo inteiro deixa de rodar. Medido: com um `def quebrado(` no fim de
  `tests/test_recurring_value.py`, a guarda por caminho imprimiu nada e a guarda por
  causa acusou `tests/test_recurring_value.py | File "...", line 137`. Por isso o
  bloco acima pareia arquivo **e** causa.

  Com esses 9 fora, a suíte roda em ~70s: **981–984 passam**, e as falhas restantes
  incluem sempre os **7** de `tests/test_statement_import.py`. Esses 7 **não vêm todos
  do `ofxparse`** — são dois grupos, e vale saber qual é qual, porque só o primeiro
  desaparece se o `ofxparse` voltar:

  | testes | dependência que falta | onde estoura |
  |---|---|---|
  | `test_attachment_detection`, `test_import_statement_bytes_csv_idempotente`, `test_import_statement_bytes_vazio_ou_grande` | `ofxparse` (import indireto, não pego pelo `--ignore` acima) | `core/handle_incoming.py` → `core/services/ofx_service.py`; e o import tardio em `statement_import.py:583` → `ofx_import.py:8` |
  | `test_parse_pdf_sicoob_like`, `test_parse_pdf_valor_e_saldo_na_mesma_linha`, `test_parse_pdf_sufixo_c_nao_engole_palavra`, `test_parse_pdf_sem_transacoes` | `reportlab` (helper `_make_pdf` do teste) e `pypdf` (`_extract_pdf_text`, `statement_import.py:463`) | o `_make_pdf` estoura primeiro; qualquer um dos dois ausente derruba os mesmos 4 |

  Os outros dois testes de PDF do arquivo (`test_parse_pdf_nubank_extrato` e
  `test_parse_pdf_nubank_sem_secao_usa_palavra_chave`) passam mesmo sem esses pacotes:
  operam sobre texto puro, sem gerar nem ler PDF.

  **O estrago não para nos testes.** `reportlab` também é importado em produção, no
  `_render_pdf` de `frontend/finance_bot_websocket_custom.py:1193`, chamado pelo
  `build_pdf` (`:1188`) da rota de exportação. Sem o pacote, exportar PDF estoura com
  `ModuleNotFoundError` — então **este ambiente não consegue exercitar esse fluxo**.
  Mexeu na exportação de PDF? Ela não foi validada aqui; diga isso no relato em vez de
  chamar de testada. São três arquivos ao todo — este, o `statement_import.py` e o
  teste:

  ```bash
  grep -rln "reportlab\|pypdf" --include="*.py" --exclude-dir=.venv --exclude-dir=.claude .
  ```

  As exclusões não são decoração: sem elas o `grep` traz também as cópias em
  `.claude/worktrees/`, e você conta o mesmo arquivo duas vezes.

  **Não trate os 7 como um bloco.** Se você mexer no parser de PDF e um desses 4 mudar
  de mensagem — de `ImportError` para uma falha de asserção, ou vice-versa — isso é
  sinal, não baseline.

  No CI os três pacotes estão presentes (`requirements.txt:56–57,66`), então lá tudo
  isso roda normalmente — a exclusão é só local.

Nunca desligue verificação de TLS nem tire o `HTTPS_PROXY` para contornar bloqueio.

---

## 7. Como reportar o que foi feito

- **Diga o que mediu e o que não mediu.** Se algo não pôde ser verificado, diga com
  todas as letras, e diga o que *falta* para verificar ("só no aparelho, depois do
  build"). Silêncio sobre o não-verificado lê-se como verificado.
- **Números, não adjetivos.** "O botão fica dentro do card em 844×390" vale; "parece
  ok" não vale.
- **Erro admitido em uma frase, e segue.** Sem autoflagelação e sem esconder.
- **Não afirme estado sem consultar.** Antes de dizer "o CI está verde", "as threads
  estão respondidas" ou "o Codex aprovou" — abra e confira.
