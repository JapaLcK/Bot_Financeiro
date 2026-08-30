---
description: Roda o time completo (Arquiteto → Coder → Tester → Manager) numa ideia
---

Ideia recebida do usuário: $ARGUMENTS

Você é o orquestrador do time de 4 agentes: `arquiteto`, `coder`, `tester`,
`manager`. Rode o fluxo abaixo usando o Agent tool com `subagent_type` igual
ao nome de cada um. Cada chamada de agente começa sem contexto — o prompt
precisa levar tudo que esse agente precisa saber.

Chame isso de **PACOTE**: **a ideia original do usuário + o plano aprovado do
Arquiteto + o diff atual + os achados/apontamentos até aqui.**

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

## Paradas — valem no ciclo inteiro, nunca por passo

Checadas em **toda** rodada de correção, venha ela do Tester (passo 5) ou de
uma reentrada do Manager (passo 7). Ficam aqui e não dentro de um passo por um
motivo medido: regra escrita dentro de um passo já divergiu do passo irmão em
todas as rodadas de revisão deste arquivo.

1. **Achado bloqueante repetido → pare AGORA.** Se um achado bloqueante já
   apontado numa rodada anterior reaparece, escale para o usuário sem gastar as
   rodadas que sobraram. É a regra do `CLAUDE.md` §4 ("duas rodadas seguidas
   batem no mesmo subsistema, pare de remendar"). Achado que sobrevive a uma
   correção honesta quase sempre é o que este ambiente não resolve — aparelho,
   deploy, WhatsApp real, `reportlab` ausente (§6) — e aí quem decide é o
   usuário, não mais uma rodada.
2. **Teto de 3 rodadas de correção no ciclo inteiro.** Contador único para as
   rodadas do Tester e as reentradas do Manager, que **nunca zera** no meio.
   Dois tetos separados não resolveriam: 3 rodadas por reentrada, vezes N
   reentradas, continua ilimitado. Estourou com bloqueante de pé: pare e escale.

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
