# Rascunhos e ferramentas de QA — não é código de produção

Nada aqui está na `main`. São artefatos de uma sessão longa (2026-08-22 a 26) que
produziu os PRs #128 e #133 e as issues #130, #134 e #136.

## Patches guardados

### `varredura-consumidores-pendencia.patch` (722 linhas) → issue #134
Consumidores de pendência chamam `clear_pending_action(uid)`, que apaga **o que
estiver lá**, não o que foi lido. Se outra tarefa instalar uma pendência nova no
meio, ela é apagada e o usuário fica com uma pergunta cuja resposta não resolve nada.

Varredura completa: **57 chamadas em 8 arquivos**, 54 se qualificam, 2 ficam de
propósito (`credit.py:1057,1073`), e a própria **leitura** apagava a linha vencida
sem condição. Seis lugares limpavam *depois* do trabalho — ali o consumo tem de ir
para antes, senão a proteção não protege.

Medido: **1406 passed** (baseline `origin/main` = 1312). Controle negativo com os
dois lados: 3 testes de corrida falham sem o conserto, 2 positivos passam nas duas
versões. O teste novo está em `test_pending_consume_race.py.novo`.

**Não foi mergeado porque alcança fluxos destrutivos** (apagar lançamento, caixinha,
investimento, cartão) e ninguém o atacou. Aplicar num branch, rodar Tester e Manager,
depois PR.

### `fila-de-perguntas.patch` (108 KB) → o multi-lançamento
`gastei 30 no uber e no ifood` vira um lançamento de R$30 em alimentação: some o uber
e erra a categoria. O patch troca adivinhação por pergunta ("Quanto foi gasto no
uber?").

Contém a **tabela de estados × eventos** da fila — a parte cara, que fechou 6 bugs de
uma vez.

**Não foi mergeado após 4 rodadas de bloqueio.** A última: o guarda `_other_target_named`
descartava a conversa em `foi 30`, `uns 30`, `30 pila`, `uber 30` — 13 de 20 respostas
humanas, contra 3 na `main`. Ainda aberto no patch: lançamento novo engolido com fila
viva; destino `99` (app de corrida) trava a fila até o TTL; `quick_entry` multiplica
lançamentos sem teto de plano e passa dos 4096 chars do WhatsApp.

## Ferramentas (essas valem por si)

- `scripts/category_qa_harness.py` — 123 casos de categorização em ~2s, Postgres
  descartável, sem rede e sem IA. Três oráculos por caso. Trava de total esperado.
  Estado medido na `main`: 50/50 nas frases canônicas, ~11/18 no input real.
- `scripts/splitter_sweep.py` — 363.972 frases sobre `CATEGORY_KEYWORDS`.
  **Rodar sempre em duas colunas (`main` × branch)**: foi assim que se descobriu que
  uma tabela de "X → 0" comparava o branch com ele mesmo.

## Lições que custaram caro (as três que mais geraram retrabalho)

1. **Consertar a classe, não a instância.** Quatro achados do #133 nasceram de eu
   consertar um site e deixar o irmão ao lado.
2. **Todo teste precisa de controle negativo E positivo.** Desligue o conserto: o teste
   tem de falhar. Mantenha um controle que prove que o caminho legítimo ainda funciona —
   senão o teste passa num código que recusa tudo.
3. **A conversa real acha o que o diff não mostra.** Os dois piores defeitos do #133 só
   apareceram rodando o produto com estado de outro fluxo no banco.
