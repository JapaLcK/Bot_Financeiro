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

## Faixa

Antes de chamar qualquer agente, classifique a mudança pela tabela de faixas do
`CLAUDE.md` §0 e diga ao usuário qual escolheu. Na faixa **Completo** rode a
sequência inteira. Na **Leve**, pule o Arquiteto (passo 1 e 2: o plano é o próprio
pedido, em poucas linhas), faça **uma** passada do Tester e um Manager curto. Na
**Direto** este fluxo não se aplica.

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
5. **Loop Coder ↔ Tester** (só na faixa **Completo**), **com teto de 2 rodadas**:
   se o Tester achou algo real (severidade que bloqueia), volte ao Coder só com
   os achados novos, depois rode o Tester de novo só no que mudou. Se ainda
   houver achado bloqueante na 2ª rodada, pare e leve ao usuário a escolha entre
   consertar e declarar como limite — não abra a 3ª sozinho.
   - **Na faixa Leve não há segunda passada do Tester.** Achado bloqueante volta
     ao Coder uma vez e o Manager confere a correção. Se o achado cair numa área
     da faixa Completo, a tarefa sobe de faixa e segue o loop acima.
   - **Achado improvável vira limite declarado, não rodada nova — só fora das
     áreas Completo.** Se o caso exige condição rara (dois toques no mesmo
     quadro, recriação de tela, falha dupla de hardware) e não toca **nenhuma**
     área da faixa Completo do `CLAUDE.md` §0, o Tester reporta, o orquestrador
     registra no relato/PR e segue. Em área Completo, raro não dispensa conserto:
     leve ao usuário.
   - **Agente novo com resumo, não retomada.** Retomar um agente carrega o
     contexto inteiro dele de novo a cada chamada. Para uma rodada nova, chame
     um agente novo com o plano, o diff atual e os achados em aberto.
   - **Mutação só no que mudou.** O Tester ataca e prova com mutação os
     trechos alterados na rodada; não refaz a bateria inteira das anteriores.
6. **Manager**: chame por último, passando o plano do Arquiteto, o diff final
   do Coder e todos os achados do Tester (inclusive os já corrigidos). Ele
   audita consistência entre os três, não repete achados do Tester.
7. Se o Manager reprovar algo, volte para o agente específico que ele
   apontou (não necessariamente o Coder) com o apontamento exato, e repita a
   partir do passo relevante. **Na faixa Leve**, a correção pedida pelo Manager
   volta direto ao Manager depois do Coder, sem nova passada do Tester — a
   menos que o apontamento caia numa área Completo, e aí a tarefa sobe de faixa.

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
  28/28 → 26/28; remeça, não reuse). O gatilho é qualquer diff que MANTENHA o arquivo, typo em
  comentário incluso; o gate compara a base com o PR inteiro
  (`git diff --quiet HEAD^1 HEAD -- "$SW"`, `.github/workflows/tests.yml:309`), então
  bump em commit posterior do mesmo PR passa. PR que APAGA ou renomeia o arquivo sai
  antes disso (`tests.yml:297-300`, "não existe no head") — não há constante a bumpar. A suíte local participa pela metade, e o
  step só roda em `pull_request`: quem ESQUECE o bump passa local e só o CI pega; quem
  bumpa SEM o par fica vermelho local. Por que o bump importa: `docs/armadilhas.md`,
  § "Service worker e PWA".

## Regras do orquestrador

- Dentro da faixa escolhida, nunca pule uma etapa para economizar tempo — o
  valor do time é justamente ter um papel adversarial (Tester) e um auditor
  (Manager) que não confiam no agente anterior. Economizar é escolher a faixa
  certa e respeitar o teto de rodadas, não cortar o Tester.
- Nunca aja como se fosse um dos agentes — sempre delegue via Agent tool,
  mesmo quando a resposta parecer óbvia.
- No fim, resuma para o usuário: o que foi implementado, o veredito do
  Manager, e o que ficou explicitamente fora do escopo verificado (ex: "só
  testado localmente", "não verificado em produção").
- Ações arriscadas (commit, push, merge, deploy) continuam exigindo
  confirmação explícita do usuário, mesmo com o Manager aprovando — aprovação
  do time deixa o trabalho pronto, não autoriza a ação.
