---
description: Roda o time completo (Arquiteto → Coder → Tester → Manager) numa ideia
---

Ideia recebida do usuário: $ARGUMENTS

Você é o orquestrador do time de 4 agentes: `arquiteto`, `coder`, `tester`,
`manager`. Rode o fluxo abaixo usando o Agent tool com `subagent_type` igual
ao nome de cada um. Cada chamada de agente começa sem contexto — o prompt
precisa levar tudo que esse agente precisa saber.

Chame isso de **PACOTE**: **a ideia original do usuário + o plano aprovado do
Arquiteto + o diff atual + os achados/apontamentos até aqui + a BASELINE
(abaixo).**

**TODA chamada leva o PACOTE inteiro** — primeira rodada e rodada de correção,
sem exceção. A única exceção é o passo 1, e só porque plano e diff ainda não
existem. Por isso os passos abaixo NÃO repetem o que passar: cada lista
própria por passo é uma cópia que diverge, e as que existiam divergiram.

Não é formalidade — cada pedaço que falta cega um agente de um jeito
específico:

- **sem o plano**, o Coder para (o `coder.md` manda parar com plano incompleto)
  e o Tester perde o "Critério de pronto", que o `arquiteto.md` escreve
  literalmente endereçado a ele ("como o Tester vai saber que está correto");
- **sem a ideia original**, o Manager só consegue comparar plano × código, e
  nunca ideia × plano — então a pergunta que o `manager.md` manda ele fazer
  ("alguma decisão que devia ter virado pergunta ao usuário foi tomada por
  conta própria?") fica sem como ser respondida;
- **sem o diff**, o Tester ataca o repositório inteiro em vez do que mudou.

Antes de começar, se o diretório atual for um repo git com CLAUDE.md, leia-o
— as regras desse arquivo (fluxo de PR, como rodar testes, o que não fazer)
valem por cima deste fluxo genérico.

## Baseline — capturada antes de QUALQUER mutação

Estado inicial se registra antes de o Coder tocar em arquivo. Depois não dá
para reconstruir: `git status --porcelain` devolve caminho e letra de status,
**nunca hunks**, então trabalho pré-existente no mesmo arquivo que o Coder edita
fica indistinguível do dele.

1. **Árvore limpa é pré-requisito, não preferência.** Se `git status --porcelain`
   não vier vazio, **pare antes da primeira chamada**, liste os arquivos e peça
   decisão ao usuário. **Nunca use `git stash`**: ele é compartilhado entre os
   worktrees do repositório, e você estaria mexendo em trabalho de outra sessão.
   Commit WIP é a saída, e a decisão é do usuário.
2. **Registre `git rev-parse HEAD`.** "Diff atual", no PACOTE, é o que mudou em
   relação a esse commit — nunca `git diff` cru.
3. **Rode a suíte ANTES do passo 3** e guarde a **lista de NOMES** dos testes que
   já falhavam — nunca a contagem. É exigência da skill `baseline-testes`
   ("Tire a baseline ANTES de mexer. Falha que já existia não é regressão sua"),
   e contagem igual não prova ausência de regressão: um teste novo mascara um
   quebrado. Sem essa medição o Tester credita ao Coder falha que já existia, ou
   não vê um teste que passou a falhar — e ele só roda DEPOIS do Coder, quando o
   estado anterior já não existe.

   **Meça no momento.** Nunca reaproveite número de baseline escrito em documento.

## Paradas — valem no ciclo inteiro, nunca por passo

Checadas em **toda** rodada de correção, venha ela do Tester (passo 5) ou de
uma reentrada do Manager (passo 7). Ficam aqui e não dentro de um passo por um
motivo medido: regra escrita dentro de um passo já divergiu do passo irmão em
todas as rodadas de revisão deste arquivo.

**Subsistema é decidível aqui, não é impressão:** dois achados são do mesmo
subsistema quando apontam **o mesmo arquivo ou a mesma função/fluxo**. Por isso
o Tester e o Manager são obrigados a rotular cada achado com o subsistema e a
dizer se ele repete achado de rodada anterior — sem esses dois campos a tabela
abaixo não é avaliável, e a regra vira palpite.

**"Rodada" é rodada de CORREÇÃO.** O ataque inicial do Tester é a rodada 0: ele
estabelece o primeiro achado e não corrige nada. Logo a primeira correção não
tem rodada anterior com que comparar. E as linhas são avaliadas **em ordem, a
primeira que casar decide** — sem essa precedência, duas linhas casam com ações
diferentes já no primeiro caso aplicável.

**PRÉ-CONDIÇÃO, avaliada ANTES da tabela: teto de 3 rodadas de correção no ciclo
inteiro.** Contador único para as rodadas do Tester e as reentradas do Manager,
que **nunca zera** no meio — dois tetos separados não resolveriam, porque 3
rodadas por reentrada vezes N reentradas continua ilimitado. Estourou com achado
bloqueante de pé: **escale, e não consulte a tabela**. O teto não pode ser linha
dela: as linhas abaixo são exaustivas e ordenadas, então uma linha de teto no fim
nunca seria alcançada.

| a rodada atual… | ação |
|---|---|
| é a primeira correção do ciclo (não há rodada anterior) | remendo pontual |
| bate no **mesmo subsistema** da rodada imediatamente anterior — achados iguais **ou diferentes** | **recuperação §4** |
| traz achado bloqueante **repetido literalmente**, mesmo não consecutivo | **recuperação §4** (política nossa, ver abaixo) |
| bate em subsistema diferente, sem repetição | remendo pontual |

**Recuperação §4** (`CLAUDE.md:412-415`, que vale por cima daqui): pare de
remendar, **enumere estados × eventos por escrito** e feche a classe inteira num
commit. A enumeração é escrita, não código — faça-a sempre, mesmo sem rodada
sobrando; é ela que revela se a causa é alcançável neste ambiente. No histórico
do repo ela achou 2 bugs que o revisor não tinha apontado.

**O gatilho do §4 é "duas rodadas seguidas no mesmo subsistema", não "o mesmo
achado de novo".** É a diferença que fez a chave Pix consumir três rodadas com
três achados DIFERENTES no mesmo fluxo (§4). A linha do achado repetido
não-consecutivo é **política deste arquivo**, mais dura que o §4 de propósito —
não a atribua a ele.

**Escale ao usuário** quando: (a) a enumeração mostra causa fora deste ambiente
— aparelho e `reportlab` ausente (§6), deploy e WhatsApp real (§3); (b) o achado
sobrevive à rodada que implementou a enumeração; (c) o teto não deixa rodada
para implementá-la. Nos três, entregue a enumeração junto: ela vale com o ciclo
parado. Escalar **não substitui** a recuperação — vem depois dela.

E o critério de saída do loop é **nenhum achado bloqueante de pé** — nunca
"nada NOVO". Bloqueante repetido não é "nada novo", e ler assim manda o fluxo
para o Manager com o defeito em pé: avançar calado é pior que rodar demais.

## Sequência

1. **Arquiteto**: chame com a ideia completa do usuário. Ele pode fazer
   perguntas via AskUserQuestion — deixe ele perguntar, não responda por ele.
   Saída esperada: um plano escrito.
2. Mostre o plano ao usuário e confirme antes de avançar, a menos que o
   plano já tenha sido marcado como trivial (uma frase, escopo óbvio) pelo
   próprio Arquiteto.
3. **Coder**. Saída esperada: diff implementado + o que foi pulado
   deliberadamente (comentários `ponytail:`, a convenção de atalho com teto
   conhecido que o repo já usa — `git grep -l "ponytail:"`).
4. **Tester**. Saída esperada: lista de achados, cada um com severidade e se
   foi provado rodando ou é hipótese.
5. **Loop Coder ↔ Tester**: se o Tester achou algo real (severidade que
   bloqueia), volte ao Coder para corrigir e rode o Tester de novo, apontando
   o que mudou desde a rodada anterior. Repita até **não haver achado
   bloqueante de pé**, respeitando as **Paradas** acima.
6. **Manager**: chame por último. Ele audita a consistência entre plano,
   código e achados — inclusive os já corrigidos — e não repete achados do
   Tester.
7. Se o Manager reprovar algo, volte para o agente específico que ele apontou
   (não necessariamente o Coder) com o apontamento exato, e repita a partir do
   passo relevante. A reentrada é uma rodada de correção como qualquer outra:
   consome do contador e passa pelas **Paradas** acima.

   Se ela voltar ao **Arquiteto** e o plano mudar, **o passo 2 roda de novo**:
   o PACOTE exige plano *aprovado*, e plano reescrito depois da aprovação não
   é aprovado. Sem isso o ciclo seguiria sobre um escopo que o usuário nunca
   viu — o oposto do que o passo 2 existe para impedir.

## Regras do orquestrador

- Nunca pule uma etapa para economizar tempo — o valor do time é justamente
  ter um papel adversarial (Tester) e um auditor (Manager) que não confiam no
  agente anterior.
- Nunca aja como se fosse um dos agentes — sempre delegue via Agent tool,
  mesmo quando a resposta parecer óbvia.
- No fim, resuma para o usuário: o que foi implementado, o veredito do
  Manager, e o que ficou explicitamente fora do escopo verificado (ex: "só
  testado localmente", "não verificado em produção").
- Ações arriscadas (commit, push, merge, deploy) continuam exigindo
  confirmação explícita do usuário, mesmo com o Manager aprovando — aprovação
  do time deixa o trabalho pronto, não autoriza a ação.
