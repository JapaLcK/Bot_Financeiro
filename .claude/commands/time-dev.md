---
description: Roda o time completo (Arquiteto → Coder → Tester → Manager) numa ideia
---

Ideia recebida do usuário: $ARGUMENTS

Você é o orquestrador do time de 4 agentes: `arquiteto`, `coder`, `tester`,
`manager`. Rode o fluxo abaixo usando o Agent tool com `subagent_type` igual
ao nome de cada um. Cada chamada de agente começa sem contexto — o prompt
precisa levar tudo que esse agente precisa saber (a ideia original, o plano,
o diff, os achados anteriores).

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
   implementado + o que foi pulado deliberadamente (ponytail).
4. **Tester**: chame com o diff/arquivos que o Coder tocou. Saída esperada:
   lista de achados, cada um com severidade e se foi provado rodando ou é
   hipótese.
5. **Loop Coder ↔ Tester**: se o Tester achou algo real (severidade que
   bloqueia), volte ao Coder só com os achados novos para corrigir, depois
   rode o Tester de novo só no que mudou. Repita até o Tester não achar nada
   novo que bloqueie, ou até 3 rodadas — se ainda houver achado bloqueante na
   3ª rodada, pare e escale para o usuário em vez de insistir sozinho.
6. **Manager**: chame por último, passando o plano do Arquiteto, o diff final
   do Coder e todos os achados do Tester (inclusive os já corrigidos). Ele
   audita consistência entre os três, não repete achados do Tester.
7. Se o Manager reprovar algo, volte para o agente específico que ele
   apontou (não necessariamente o Coder) com o apontamento exato, e repita a
   partir do passo relevante.

## Gates deste repositório

- **Verde local não é verde no CI: o venv local não é o `requirements.txt`.** Em
  2026-09-02, `fastapi` 0.115.6 aqui × 0.141.1 no `requirements.txt`; remeça, não reuse
  (na raiz do repo, que é onde mora o `.venv` — worktree não tem o seu):
  `.venv/bin/python -c "import fastapi; print(fastapi.__version__)"` × `grep -i '^fastapi' requirements.txt`.
  Teste que toca a superfície de API passa aqui e falha lá. Mande o **Tester** comparar
  as duas versões, com o interpretador que a skill `baseline-testes` manda usar.
- **Diff toca `frontend/service-worker.js` → mande o Coder bumpar o `CACHE_NAME` E o par
  dele**: `VERSAO_ATUAL`, em `tests/frontend/sw_cache_privado.test.mjs`, para o mesmo N.
  Os dois números são um só — bumpar o `CACHE_NAME` sozinho derruba 2 de 28 em
  `node --test tests/frontend/sw_cache_privado.test.mjs` (medido em 2026-09-02, v9→v10:
  28/28 → 26/28; remeça, não reuse). O gatilho é QUALQUER diff no arquivo, typo em
  comentário incluso; o gate compara a base com o PR inteiro
  (`git diff --quiet HEAD^1 HEAD -- "$SW"`, `.github/workflows/tests.yml:309`), então
  bump em commit posterior do mesmo PR passa. A suíte local participa pela metade, e o
  step só roda em `pull_request`: quem ESQUECE o bump passa local e só o CI pega; quem
  bumpa SEM o par fica vermelho local. Por que o bump importa: `docs/armadilhas.md`,
  § "Service worker e PWA".

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
