# PigBank — como trabalhar neste repositório

> **Contexto de domínio** (tabelas, endpoints, fluxos de cadastro, e-mail, roadmap):
> `docs/CLAUDE.md`. Guia de desenvolvimento: `docs/dev_guide.md`.
>
> **Este arquivo é sobre o processo**: o que fazer antes de escrever, como validar,
> e as armadilhas deste código que já custaram caro.

---

## 0. Regras permanentes — leia antes de escrever qualquer linha

**Estas regras valem para toda alteração de código deste repositório**, sem exceção:
implementação, correção, refatoração, arquivo novo. Não são um checklist de abertura
— elas valem durante o trabalho inteiro, e de novo a cada nova tarefa da mesma sessão.

**Antes de começar, leia o `CLAUDE.md` relevante.** Este arquivo para processo;
`docs/CLAUDE.md` para domínio (tabelas, endpoints, integrações). Se existir um
`CLAUDE.md` mais específico dentro da área que você vai mexer, ele também vale — e o
mais específico ganha quando os dois falarem do mesmo assunto.

### 0.1 Procure antes de criar

Antes de escrever **qualquer** função, classe, helper, utilitário, componente,
módulo, endpoint, serviço, arquivo ou abstração: **procure se já existe**. Este
repositório tem 300+ arquivos Python e 15 anos-pessoa de código acumulado em
`dashboard.js` — a chance de o que você precisa já existir é alta, e a de você
não achar sem procurar é ainda maior.

```bash
grep -rn "nome_provavel\|conceito" --include="*.py" --exclude-dir=.venv --exclude-dir=.claude .
git grep -n "conceito" -- frontend/
```

Nunca crie função nova porque criar é mais rápido que procurar. Ordem de preferência,
nesta ordem:

1. **reutilizar** uma função existente;
2. **estender** uma existente, quando fizer sentido para os dois chamadores;
3. **extrair** o comportamento comum, quando houver reutilização real (não especulada);
4. só então **criar** algo novo.

Não copie-e-cole lógica para outro lugar. Duas funções com nomes diferentes fazendo
essencialmente a mesma coisa são um bug esperando o dia em que só uma for corrigida —
já aconteceu aqui (`handlers/credit.py` × `core/handlers/credit.py`, ver
`docs/refactor_plan.md`).

### 0.2 Escreva o mínimo que resolve

A solução preferida é a mais simples que resolve **corretamente** o problema. Evite:
abstração prematura, wrapper desnecessário, helper de uso único, código defensivo
exagerado, sistema genérico para problema específico, camada nova de arquitetura,
comentário explicando o óbvio, duplicação, boilerplate, e funcionalidade que ninguém
pediu.

Não "melhore" área não relacionada. Não transforme mudança pequena em refatoração
grande. Isso **não** vale para o que nunca se simplifica: validação em fronteira de
confiança, tratamento de erro que evita perda de dado, segurança, acessibilidade, e
o que foi explicitamente pedido.

### 0.3 Mudanças cirúrgicas

Para bug e feature pequena: mudança pequena e localizada. Não altere arquivo que não
precisa mudar, não renomeie sem necessidade, não reformate arquivo inteiro para trocar
três linhas, não misture refatoração não relacionada com feature. **O diff deve ser o
menor possível sem comprometer a solução** — e o revisor mede o PR pelo que ele tem de
ler, não pelo que você achou bonito de arrumar no caminho.

### 0.4 Entenda o fluxo antes de mexer

Nunca altere funcionalidade importante olhando só o trecho isolado. Antes de tocar em
**autenticação, MFA, pagamentos, Open Finance, WebSocket, navegação, estado global,
service worker ou cache**, levante o fluxo inteiro: quem chama, quem escuta, quais
callbacks, quais eventos, quais imports, quais endpoints relacionados, quais efeitos
colaterais. É o mesmo inventário do §2, e a §5 lista onde ele já foi pago caro.

### 0.5 Organização de arquivos é prioridade

Arquivo grande demais não é tradição a ser respeitada. Se um arquivo acumula
responsabilidades diferentes, divida por assunto:

```text
dashboard/                 em vez de     dashboard.js
  boletos.js                             // 10.587 linhas, 414 funções
  cartoes.js
  investimentos.js
```

Ao criar arquivo novo: uma responsabilidade clara, nome que a reflita, diretório
apropriado. Nada de `utils.js`/`helpers.js`/`common.js`/`misc.js` gigantes onde coisa
não relacionada é despejada. Ao mexer num arquivo já enorme, pergunte se o novo código
não cabe melhor num módulo separado.

O oposto também é erro: não estilhace em dezenas de arquivos minúsculos sem ganho.
O alvo é **coesão alta, acoplamento baixo, estrutura fácil de navegar**.

### 0.6 Respeite a arquitetura atual

Não introduza framework, biblioteca ou padrão arquitetural novo porque seria a forma
mais moderna. Antes de adicionar dependência, veja se o projeto ou a própria
plataforma já resolvem (o navegador tem módulos ES, `<dialog>`, `Intl`, View
Transitions; o Postgres tem constraint). Alteração local segue o padrão existente,
salvo decisão explícita de migração.

### 0.7 Uma fonte de verdade

Antes de adicionar constante, configuração, status, string, regra de negócio, lista ou
mapeamento: veja se já existe a fonte oficial daquilo. Não mantenha duas versões da
mesma regra em lugares diferentes. Quando a duplicação for inevitável (ex.: HTML
estático que não consegue importar Python), **um teste tem que comparar as duas** —
é o que já se faz com o subset de ícones (`tests/test_phosphor_subset.py`).

### 0.8 Otimize para quem vem depois

Entre duas soluções equivalentes, prefira a que tem menos código, menos estado, menos
dependências, reutiliza o que existe, deixa claro de quem é cada responsabilidade e
vai ser mais fácil de mudar. Não otimize só para a implementação de hoje funcionar.

---

## 1. Antes de escrever a primeira linha

**Entenda a queixa antes de diagnosticar.** Relato de usuário quase nunca é
diagnóstico. Se a descrição admite mais de uma causa, pergunte pelo estado em que
elas se separam — não escolha uma e comece a codar.

> Custou um branch inteiro: "aparecem barras pretas ao puxar a tela" tinha duas
> leituras (o preto *durante* o arrasto elástico e uma faixa *permanente* sob o
> status bar). Foi tratada a errada. A pergunta que resolvia era uma só:
> "como fica com a tela parada, sem tocar?"

**Se a decisão parecer escolha entre dois males, procure a terceira opção.** Quase
sempre falta um passo, não falta coragem para escolher o mal menor.

> `mark_bill_paid` debitava e só então marcava a conta como paga: falha no meio →
> retentativa **debita duas vezes**. Inverti a ordem e chamei de "erro conservador":
> falha no meio → conta paga sem débito, **sem retentativa possível**, o gasto some para
> sempre. Não era escolha entre os dois: faltava desfazer a reserva quando o débito
> falha. A versão "conservadora" era a pior das três.

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

PY=/Users/<user>/Desktop/bot/bot_wa/.venv/bin/python   # na máquina local; ver §6a
PYTHONPATH=. $PY -m pytest -q                 # tudo
PYTHONPATH=. $PY -m pytest tests/test_x.py -q # durante o desenvolvimento
```

Use `<interpretador> -m pytest`, nunca `pytest` solto — o binário no PATH costuma ser
de outro interpretador, sem as dependências. **Na máquina local o `python3` do PATH
(Homebrew) não tem nem `fastapi` nem `pytest`**: use o do `.venv` da raiz, que tem
tudo (§6a). No sandbox da web o interpretador é o `python3` mesmo, e aí valem as
exclusões do §6b.

**Frontend:** `npm run test:frontend` (`node --test tests/frontend/*.test.mjs`, com
Playwright). O `package.json` da raiz existe só para isso — não há build de JS.

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

**Teste de regressão escrito para provar um conserto precisa dos DOIS controles.**
Vale para o **grupo** de testes daquele conserto, não para cada teste isolado:

- **negativo** — desligue o conserto e rode o grupo: pelo menos um caso tem de falhar.
  Injete a falha **onde ela discrimina**: num caso que estava verde, nunca num que já
  estava vermelho. Se o resultado sai igual com e sem o conserto, o grupo não mede nada.
- **positivo** — um caso no grupo provando que o caminho legítimo continua funcionando,
  quando o conserto *restringe* algo (validação, guarda, recusa). Sem ele, o grupo passa
  num código que recusa tudo — que é pior que o bug.

**Isto não vale para teste unitário comum**: caminho de erro, invariante, função pura,
tabela de entrada/saída. Ali não há "conserto para desligar" nem "caminho legítimo" a
provar, e cobrar mutação vira cerimônia.

> Quatro testes de uma sessão só não mediam nada: dois liam o *texto do arquivo* com
> `read_text()` + `index()` procurando o nome de uma função; um chamava a função nova
> direto, sem passar pelo caminho alterado; e um controle negativo foi injetado num caso
> que já falhava. Os três consertos que eles "cobriam" podiam ser desligados sem uma
> linha vermelha.

**Rode a conversa, não a função.** Teste que chama o handler isolado, com `db` mockado,
é cego para a classe de bug que mais aparece aqui: o estado que **outro fluxo** deixou no
banco. Um teste vale quando manda **duas mensagens de assuntos diferentes** pelo
`handle_incoming`, com estado real.

> No PR #133, 11 dos 13 testes eram mockados e o único ponta a ponta pulava o
> `handle_incoming`. Os dois piores defeitos só apareceram na conversa: `paguei a luz`
> logo depois de `gastei 50 no mercado` reproduzia o bug original inteiro — sequencial,
> um usuário, sem corrida nenhuma.

**Varredura combinatória se roda em DUAS COLUNAS** (`main` × branch). Uma tabela de
"122.014 erros → 652" parecia um conserto enorme; medida contra a `main`, os grupos já
valiam zero lá — a varredura comparava o branch com uma versão anterior dele mesmo.

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

**Verde no teste não é "funciona no WhatsApp".** Já se repetiu: implementei, os testes
passaram, e no aparelho nada funcionava — ou porque o teste não media nada, ou porque
não cobria o que o usuário faz de verdade. Três regras, cada uma contra uma causa:

1. **Prove que o teste falha sem o fix.** Antes de dizer "pronto": reverta o fix, rode
   o teste, veja **vermelho**; reponha o fix, veja **verde**. Se ele passa com e sem a
   correção, é teste tautológico — escrito junto com o código, afirmando o que o código
   faz, verde por construção. Conserte o teste antes de reportar o resultado. (É a 1ª
   das duas perguntas acima, virada em rotina obrigatória.)
2. **Separe "verificado aqui" de "só no aparelho/deploy", explícito no relato.** O app
   carrega o site ao vivo, então mudança de frontend só aparece **depois do deploy**
   (§5), e vários fluxos — WhatsApp real, envio, `ofxparse`/`reportlab` ausentes (§6) —
   este ambiente não exercita. Diga em qual dos dois mundos a mudança foi provada;
   silêncio sobre o não-verificado lê-se como verificado (§7).
3. **Cubra o input que o usuário digita, não o que você projetou.** Um teste com
   `"gastei 50 no mercado"` bonitinho passa e não prova nada sobre acento, áudio, duas
   transações na mesma frase, gíria, ordem trocada — a classe de bug que mais quebra no
   WhatsApp. É a 2ª pergunta acima aplicada à entrada: que mensagem real este teste
   nunca veria? Lembre que os testes de WhatsApp (`test_whatsapp_simulation.py` e
   irmãos) mockam o LLM/NLP e o envio — um bug na interpretação real ou no roteamento
   real passa batido, então o teste verde só cobre a lógica intermediária, não o comando
   ponta a ponta.

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

**Apontamento do Codex não é ordem de mudança — é hipótese a verificar.** Ele não
tem contexto de negócio, lê a árvore do branch (§3) e erra. Antes de tocar no código,
para cada apontamento:

1. **localize o trecho citado** e confirme que o problema existe mesmo ali;
2. **leia o contexto inteiro** e o comportamento anterior — não só as linhas do diff;
3. **verifique se o caso é alcançável em produção** (quem chama, com que entrada);
4. **avalie o risco da correção**: ela pode introduzir regressão pior que o achado?
5. **procure proteção que já exista** — validação, guarda ou função que já resolve (§0.1);
6. **decida**: corrigir, simplificar, manter como está, ou apenas documentar.

Só implemente com evidência de que a correção é necessária e melhora o sistema. Se o
apontamento estiver errado, for só teórico, ou custar mais complexidade que benefício,
**explique na thread e não altere o código** — resposta com evidência fecha a thread
tão bem quanto um commit. Vale sempre a menor mudança segura: nada de abstração,
tratamento ou teste criado só para satisfazer o revisor.

**Ataque antes de empurrar. O parecer do Codex tem de ser confirmação, não descoberta.**
Se ele está achando coisa, o ataque não foi feito. Rode o Tester (e o Manager) **antes**
do push, não depois do apontamento — é a diferença entre revisar e terceirizar a revisão.

> No PR #133 o Codex apontou 9 vezes. Quando o time passou a atacar antes do push, o
> Tester achou **11 defeitos que o Codex não tinha pegado**, e os dois piores estavam em
> código já aprovado: o PR não consertava o bug no caminho comum, e o pagamento perdia
> dinheiro de forma permanente.

E **nunca sugira pular a revisão**. Parece economia de tempo; o efeito real é parar de
olhar para nada ser encontrado.

**Merge só com autorização explícita do dono do repositório.** Aprovação do Codex e
CI verde deixam o PR *pronto*; não autorizam o merge. Avise e pergunte.

**O ciclo de correção também precisa de inventário — senão vira gerador de bug.**
Registro do PR #60 (puxar pra atualizar): 14 rodadas de revisão, 21 apontamentos,
e **quase metade nasceu das próprias correções**, não do código original. Os dois
mecanismos que produziram isso, para nunca repetir:

1. **Corrigir a instância e não a classe.** O gate do `PBRefresh` foi consertado
   na Início sem perguntar "quem mais registra isso cedo demais?" — o dashboard
   tinha o bug idêntico e virou a rodada seguinte. A proteção de digitação foi
   posta nas telas de reload sem olhar as de refresh mole — a chave Pix virou a
   rodada seguinte. A regra já estava neste arquivo (§2: "achei um caso" ≠
   "resolvi a categoria") e vale DOBRADO para apontamento de revisor: antes de
   responder a thread, nomear a classe e varrer os irmãos com grep. Quando a
   varredura foi feita (rodada 3, os 12 branches do `PBRefresh`), ela achou bug
   que o revisor não tinha visto.

2. **Remendar transição em vez de enumerar a máquina.** Da rodada 7 em diante os
   apontamentos eram todos transições de uma máquina de estados (gesto ×
   refresh) construída um remendo por vez, reagindo ao revisor. Um único campo
   (a chave Pix) consumiu TRÊS rodadas: sucesso, falha, edição durante o fetch —
   três caminhos que uma tabela de estados teria mostrado de uma vez. **Se duas
   rodadas seguidas batem no mesmo subsistema, pare de remendar: enumere
   estados × eventos por escrito e feche tudo num commit.** A enumeração feita
   ao final achou 2 bugs que o revisor ainda não tinha apontado.

O custo disso não foi abstrato. Erros meus que chegaram a existir no branch e
seriam produção sem o ciclo de revisão: reload por cima dos códigos de backup do
MFA (que aparecem uma vez); totais financeiros zerados renderizados com cara de
sucesso; cache bom sobrescrito com histórico vazio; pedido velho sobrescrevendo
resposta mais nova; PATCH de preferências abortado no meio. Cada um entrou num
commit que "corrigia" outra coisa.

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
- **Quem carrega o CSS ≠ quem é alcançável.** Das **27** páginas em `frontend/`:

  | quantas | o que carregam | quem |
  |---|---|---|
  | 6 | `app-mode.css`/`app-mode.js` | `login`, `cadastro`, `home`, `dashboard`, `comandos-app`, `settings` — mais a `changelog`, que carrega **os dois** (`changelog.html:15` shim, `:16-17` app-mode) e está contada na linha de baixo |
  | 16 | o shim `frontend/safe-area.js` | as estáticas: `index`, `precos`, `termos`, `privacy`, `completar-cadastro`, `comecar`, `suporte`, `agents`, `changelog`, `comandos`, `como-funciona`, `funcionalidades`, `blog-article`, `reset-password`, `whatsapp` — mais a `error`, que **não é rota**: é template servido pelo `error_page_response` (`frontend/routes/shared.py`) em quase toda URL que dá erro. Quase: as **4** exceções são `/webhook` e `/wa/webhook` (403 `text/plain` "forbidden", `adapters/whatsapp/wa_app.py:224,241,255`) e `/fonts/{name}` e `/brand/{path}` (404 de corpo vazio, `static_pages.py:536,559,564,567`) — endpoints de máquina e de subrecurso, que de propósito não gastam 1,4 KB de HTML |
  | 5 | nada, de propósito | `admin-login`, `admin-dashboard`, `_dash_mockup`, `preview_agentes`, e o `ddf99f17-…` |

  As duas páginas geradas em Python (bullet seguinte) também carregam o shim.
  `env(safe-area-inset-*)` aparece em **seis** arquivos de `frontend/`
  (`git grep -l safe-area-inset -- frontend/`): os dois que implementam o tratamento
  — `app-mode.css` e `safe-area.js` — e mais quatro que trazem o próprio inset inline:
  `home.html`, `precos.html`, `admin-login.html` e `admin-dashboard.html`. Os dois
  últimos são páginas que, de propósito, não carregam nem o app-mode nem o shim.

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

### `dashboard.js`: 10.587 linhas num arquivo só

É o maior passivo de organização do repositório e o exemplo vivo da §0.5: **414
funções e 159 `const/let` no escopo global**, 35 seções demarcadas por comentário,
491 KB. Nasceu de um `<script>` inline extraído do `dashboard.html` ("refactor Fase 1:
CSP script-src").

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

`frontend/service-worker.js` (`CACHE_NAME` versionado à mão, hoje `pigbank-v7`):
HTML e auth nunca são cacheados, assets são network-first com fallback de cache,
API e WebSocket passam direto. O `manifest.json` tem `start_url: "/login"` — e a
`index.html` tem um guard no topo que manda a PWA instalada para `/login`, com saída
por `?site=1`. Mexeu na estratégia de cache? Bumpe o `CACHE_NAME`, senão o aparelho
que já instalou continua com o SW velho.

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
  (§0.7), e o `docs/CLAUDE.md` aponta para lá em vez de repetir a lista.
- **O monólito ainda existe e ainda cresce.**
  `frontend/finance_bot_websocket_custom.py` tem ~14,5 mil linhas e concentra auth,
  MFA, billing, WebSocket, dashboard e o `ConnectionManager`. Parte das rotas já saiu
  para `frontend/routes/` (`static_pages`, `settings`, `pockets`, `cards`,
  `analytics`, `open_finance`, `push`, `agents`, `affiliates`, `shared`), registradas
  por `include_router`. **Rota nova vai para um router de `frontend/routes/`** — não
  para o monólito. O plano completo está em `docs/refactor_plan.md`.
- **Isolamento por usuário é regra dura**: toda query com `WHERE user_id = %s`.
  Nunca vazar dado entre usuários.
- **`launch.py` sobe dois processos**: o uvicorn (que atende o `$PORT` do Railway) e o
  `bot.py` do Discord. Um `web` no Procfile, dois processos filhos.
- **Tarefas de fundo sobem no startup do app** quando `RUN_BACKGROUND_TASKS != "0"`
  (agendadores de investimento, Open Finance, engajamento, cobrança recorrente…).
  Em teste e no `dashboard_dev.py` isso é desligado — se você ligar sem querer num
  ambiente com banco real, elas escrevem.

---

## 6. Limites do ambiente (não são bugs, não tente contornar)

**Antes de tudo: descubra em QUAL ambiente você está.** São dois, com limites
diferentes, e confundi-los já produziu documentação errada neste próprio arquivo.

```bash
ls -d /Users/<user>/Desktop/bot/bot_wa/.venv   # existe → máquina local
python3 -c "import fastapi" 2>&1 | tail -1     # ModuleNotFoundError → não é o venv
```

### 6a. Máquina local (macOS)

- **Existe um `.venv` na raiz do repositório com TODOS os pacotes**, inclusive
  `ofxparse`, `reportlab` e `pypdf`. Nada do §6b abaixo se aplica aqui: não há
  `--ignore`, não há os 9 erros de coleta, e os 7 testes de `test_statement_import.py`
  não têm por que falhar por dependência.
- **O `python3` do PATH (Homebrew) NÃO tem os pacotes** — nem `fastapi`, nem
  `psycopg`, nem `pytest`. Rodar `python3 -m pytest` dá `ModuleNotFoundError` e isso
  **não é falha de teste**. Use o interpretador do venv, com caminho absoluto:

  ```bash
  PYTHONPATH=. /Users/<user>/Desktop/bot/bot_wa/.venv/bin/python -m pytest -q
  ```

  Vale também a partir de um worktree de `.claude/worktrees/` — o venv da raiz serve
  os dois.
- **As variáveis de ambiente continuam obrigatórias** (§3). Sem elas o import de
  `frontend/finance_bot_websocket_custom.py` chama `sys.exit(1)` na linha 277
  (`DATABASE_URL`) e o pytest morre com `INTERNALERROR` — de novo, não é falha de
  teste.
- **A produção é acessível** por HTTP a partir daqui (medido: `GET https://pigbankai.com/`
  → 200, assets idem). `HEAD` responde 405 com `Allow: GET`, e isso é o roteamento
  sendo estrito, não uma falha. Antes de afirmar que algo está no ar, busque.

### 6b. Sandbox do Claude Code na web

- **A produção não é acessível**: o proxy bloqueia `pigbankai.com` (403).
- **CDNs são bloqueados.** Chart.js e afins falham; `applyTheme` do dashboard, por
  exemplo, lança no meio por causa disso. Saiba disso ao interpretar um teste.
- **Faltam pacotes de `requirements.txt`** — pelo menos `ofxparse`, `reportlab` e
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
  isso roda normalmente — a exclusão é só do sandbox.

### 6c. Vale nos dois ambientes

- **`env(safe-area-inset-*)` vale sempre 0** no Chromium headless. Regras de área
  segura são inertes ali; só dá para verificar a aritmética substituindo valores fixos.
- **Comportamento nativo do WKWebView não é reproduzível.** `contentInset`, elástico,
  teclado: só no aparelho, depois do build.
- **Exportar PDF depende de `reportlab`** (`_render_pdf`,
  `frontend/finance_bot_websocket_custom.py`). Onde o pacote falta, esse fluxo não é
  exercitável — diga isso no relato em vez de chamar de testado.

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
