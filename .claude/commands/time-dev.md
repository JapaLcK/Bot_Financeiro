---
description: Roda o time completo (Arquiteto → Coder → Tester → Manager) numa ideia
---

Ideia recebida do usuário: $ARGUMENTS

Você é o orquestrador do time de 4 agentes: `arquiteto`, `coder`, `tester`,
`manager`. Rode o fluxo abaixo usando o Agent tool com `subagent_type` igual
ao nome de cada um. Cada chamada de agente começa sem contexto — o prompt
precisa levar tudo que esse agente precisa saber.

Chame isso de **PACOTE**, porque ele vale em TODA chamada, inclusive nas de
correção: **a ideia original do usuário + o plano aprovado do Arquiteto + o
diff atual + os achados/apontamentos desta rodada.** Uma rodada de correção
que leve só os achados novos deixa o Coder sem o plano — e o `coder.md` manda
ele PARAR quando o plano está incompleto, então a primeira correção trava, ou
pior, conserta sem saber o escopo aprovado.

Antes de começar, se o diretório atual for um repo git com CLAUDE.md, leia-o
— as regras desse arquivo (fluxo de PR, como rodar testes, o que não fazer)
valem por cima deste fluxo genérico.

## Sequência

1. **Arquiteto**: chame com a ideia completa do usuário. Ele pode fazer
   perguntas via AskUserQuestion — deixe ele perguntar, não responda por ele.
   Saída esperada: um plano escrito.
2. Mostre o plano ao usuário e confirme antes de avançar, a menos que o
   plano já tenha sido marcado como trivial (uma frase, escopo óbvio) pelo
   próprio Arquiteto.
3. **Coder**: chame com o plano completo do Arquiteto. Saída esperada: diff
   implementado + o que foi pulado deliberadamente (comentários `ponytail:`,
   a convenção de atalho com teto conhecido que 8 arquivos do repo já usam).
4. **Tester**: chame com o diff/arquivos que o Coder tocou. Saída esperada:
   lista de achados, cada um com severidade e se foi provado rodando ou é
   hipótese.
5. **Loop Coder ↔ Tester**: se o Tester achou algo real (severidade que
   bloqueia), volte ao Coder **com o PACOTE inteiro** — ideia, plano aprovado,
   diff atual e os achados novos. Nunca só os achados. Depois rode o Tester de
   novo, com o PACOTE, apontando o que mudou desde a rodada anterior. Repita
   até o Tester não achar nada novo que bloqueie, ou até 3 rodadas — se ainda
   houver achado bloqueante na 3ª rodada, pare e escale para o usuário em vez
   de insistir sozinho.
6. **Manager**: chame por último, passando o plano do Arquiteto, o diff final
   do Coder e todos os achados do Tester (inclusive os já corrigidos). Ele
   audita consistência entre os três, não repete achados do Tester.
7. Se o Manager reprovar algo, volte para o agente específico que ele
   apontou (não necessariamente o Coder) **com o PACOTE inteiro mais o
   apontamento exato**, e repita a partir do passo relevante. Vale o mesmo do
   passo 5: mandar só o apontamento é a mesma falha por outra porta.

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
