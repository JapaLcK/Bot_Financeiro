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

## Sete regras, tiradas de erros medidos — não de teoria

Vinte achados nos PRs #128 e #133. **Cerca de metade nasceu dos próprios consertos.**
Cada regra abaixo tem o erro que a gerou.

**1. Escreva a regra antes dos consertos.**
A tabela `pending_actions` tem **uma linha por usuário** e ~100 lugares que escrevem ou
consomem nela. A disciplina — gravar e apagar só se ainda for o que você leu — só foi
escrita na oitava rodada (`claim_pending_action`, com a ordem de prioridade). Se tivesse
sido escrita na primeira, a maioria das rodadas não teria existido.

**2. Antes de dizer "consertado", faça grep dos irmãos.**
A pergunta não é "consertei o que ele apontou?", é "quem mais faz isto?". Escrita
incondicional apareceu em reivindicar, devolver, abandonar, gravação inicial e consumir —
consertadas uma por rodada. O arredondamento foi corrigido em 1 lugar de 4 (um deles 50
linhas acima, no mesmo arquivo). O ponto final, em 1 porta de 2.

**3. Todo teste precisa dos DOIS controles.**
Desligue o conserto: o teste tem de falhar. E mantenha um caso provando que o caminho
legítimo ainda funciona — senão o teste passa num código que recusa tudo. Quatro testes
desta sessão não mediam nada: dois liam o *texto do arquivo* com `read_text()` + `index()`,
um chamava a função nova direto sem passar pelo caminho alterado, e um controle negativo
foi injetado num caso **que já estava vermelho** — o número saía igual com e sem o conserto.
Injete a falha onde ela discrimina: num caso verde.

**4. Nunca cite número que você não mediu nesta sessão, contra a baseline certa.**
Repeti "baseline 1348" várias vezes; a `main` tinha **1312** — o 1348 era estado
intermediário do próprio branch. Rode a suíte nas duas árvores, hoje. Número que sobe
sozinho parece ganho e não é. Vale igual para varreduras: **rode em duas colunas**, senão
você compara o branch com ele mesmo (aconteceu, com uma tabela de "122 mil erros → 652"
que na verdade era zero na `main`).

**5. Rode a conversa, não a função.**
Os dois piores defeitos só apareceram mandando **duas mensagens de assuntos diferentes**
pelo `handle_incoming`, com estado real de outro fluxo no banco. Teste que chama a função
isolada com mock é cego para essa classe inteira — e era o caso de 11 dos 13 testes que o
PR trazia. Um deles: `paguei a luz` logo depois de `gastei 50 no mercado` reproduzia o bug
original inteiro, sequencial, um usuário só.

**6. Ataque antes de empurrar.**
A revisão automática deve ser **confirmação, não descoberta**. Quando o time voltou a
rodar antes do push, o Tester achou **11 defeitos** que a revisão não tinha pegado, e os
dois piores estavam em código que parecia pronto. E não sugira pular a revisão: parece
economia, é parar de olhar para nada ser encontrado.

**7. Quando a decisão parecer escolha entre dois males, procure a terceira opção.**
Escolhi entre "debitar duas vezes" e "perder o débito", chamei a segunda de erro
conservador e segui. Não era escolha: faltava um passo — reservar, debitar, e **desfazer
a reserva** se o débito falhar. A versão que eu tinha chamado de conservadora perdia
dinheiro do usuário de forma permanente, sem retentativa possível.

## Duas coisas que valem como critério, não como regra

**Para validação de entrada:** o critério não é "o usuário digitou certo?", é **"o erro
dele vira dinheiro errado?"**. `1.23.456` é recusado porque pagaria 100× a mais;
`1.23,45` passa porque o bot acerta a intenção. Sem esse critério escrito, a validação
apara forma por forma até recusar entrada válida — que é pior que o bug.

**Para prioridade entre pendências:** pergunta que espera resposta nunca é desalojada;
oferta de conveniência cede. E o teste de qual é qual é observável: oferta de conveniência
é a que o `_send_reply_with_optional_buttons` consome no mesmo turno. Classifiquei
`confirm_recurring_offer` como oferta sem olhar isso, e o "sim" do usuário passou a matar
duas pendências de uma vez.
