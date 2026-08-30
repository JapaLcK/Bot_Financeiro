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
  boletos.js                             // grande demais para caber num arquivo
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

**E não vira número escrito aqui.** Contagem que um comando responde — linhas de um
arquivo, funções num escopo, quantos testes passam, qual é a versão de uma constante
— **não é fato de documentação**: ela envelhece em silêncio a cada commit e passa a
mentir com cara de medição. A preferência é **remover** a contagem e deixar o comando
no lugar dela. Se um número precisar mesmo ser documentado, ele vem com **a data em
que foi medido, o comando que o produziu e o aviso de remedir antes de reusar** — e
quem lê remede. Esta regra nasceu de quatro números deste próprio arquivo que ficaram
errados sem ninguém notar, um deles por mais de 2×.

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

O conteúdo desta seção mora em **`docs/armadilhas.md`** — é referência, e carregá-la
em toda tarefa custa contexto sem retorno. **Leia antes de mexer na área.**

A seção continua existindo aqui de propósito: as citações `CLAUDE.md §5`
no código, nos testes e no `docs/CLAUDE.md` continuam resolvendo.

---

## 6. Limites do ambiente (não são bugs, não tente contornar)

O conteúdo desta seção mora em **`docs/ambiente.md`** — é referência, e carregá-la
em toda tarefa custa contexto sem retorno. **Leia antes de rodar a suíte, de
interpretar uma falha, ou de afirmar que algo não é reproduzível aqui.**

A seção continua existindo aqui de propósito: as citações `CLAUDE.md §6`
no código, nos testes e no `docs/CLAUDE.md` continuam resolvendo.

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
