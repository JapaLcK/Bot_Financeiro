# Controles declarados: o que envelhece e o que não

Um **controle declarado** é a instrução que um arquivo de teste carrega dizendo
como desligar o conserto que ele cobre, e quais testes ficam vermelhos quando
você desliga. É o §3 do `CLAUDE.md` da raiz virado em texto executável: sem ela,
"o grupo tem controle negativo" é afirmação que ninguém consegue conferir.

Este arquivo existe porque a lição foi **paga com medição**, não deduzida. Na
rodada 12 de revisão de um PR de cobrança, o Tester reexecutou **31 controles
declarados** dos arquivos daquele PR. O resultado separou com nitidez o que
sobrevive do que apodrece.

## O que a medição mostrou

| parte da instrução | resultado |
|---|---|
| **nome do teste VERMELHO** | **31 de 31 corretos** |
| lista de testes VERDES | **4 de 4 afirmações falsas** |
| contagem `N failed, N passed` | **5 de 5 erradas** |

Nenhum controle estava morto — todas as injeções ainda produziam vermelho no
teste certo. O que apodreceu foi tudo o que estava **em volta** do vermelho.

### Por que, e a razão é estrutural

O nome do vermelho afirma algo sobre **o caminho de código injetado**. Esse
caminho só muda se alguém desfizer o conserto — e aí o controle apontar para
ele é exatamente o que se quer.

A lista verde e a contagem afirmam algo sobre **o conjunto inteiro de arquivos**,
que cresce a cada rodada. Todo teste novo pode entrar na lista verde ou mudar a
contagem, e nenhum autor de teste novo vai procurar todos os controles
declarados para atualizá-los. Envelhecer não é descuido: é a única coisa que
podia acontecer.

### O segundo eixo: o que a instrução CITA

| a instrução cita | destino |
|---|---|
| identificador Python local (`enviado`, `email = decrypt_pii_optional(...)`) | **morre** na primeira refatoração |
| forma de bloco ou indentação ("desidente as duas linhas") | **morre** |
| **texto de predicado SQL** (`and past_due_since is null`, `= any(%s)`, `and coalesce(engagement_opt_out, false) = false`) | **sobrevive** |
| **constante nomeada** (`PAYMENT_REMINDER_DEDUPE_DAYS = 2.0`) | **sobrevive** |

Das 4 instruções velhas encontradas, todas citavam nome local ou forma de
bloco. As intactas citavam predicado ou constante. Faz sentido: o predicado é a
coisa que o conserto **é**, e o nome da variável é só onde ele mora hoje.

## A regra

> **Cite o predicado ou a constante. Nomeie só os testes VERMELHOS. Nunca
> escreva `N passed`.**

E um corolário, quando a mesma injeção tem mais de uma grafia: **se duas
variantes produzem o MESMO vermelho, fique com uma** (é a patologia
"degenerado", abaixo); **se produzem vermelhos DIFERENTES, elas são injeções
diferentes — nomeie qual delas a instrução manda aplicar.** O segundo caso não
é teórico: um controle desta família oferecia "descarte o retorno do gate" e
"apague o bloco do gate" como equivalentes, e um teste novo de fail-closed
passou a distinguir as duas (2 vermelhos × 3), porque apagar o bloco leva o
`except` junto. Instrução que não diz QUAL variante deixa o leitor conferir
outra coisa.

E uma exceção útil: **afirmação de verde vale quando é sobre o caminho
injetado**, não sobre o conjunto de arquivos. "Esta injeção NÃO reprova o caso
legítimo X" é o controle positivo, é o que separa "o guard discrimina" de "o
guard recusa tudo", e essa afirmação envelhece com o conserto, não com a suíte.
O que não vale é enumerar arquivos irmãos.

## Duas patologias além de envelhecer

**Controle degenerado** — duas injeções diferentes que produzem o mesmo conjunto
de falhas. Encontrada uma: um bloco oferecia "troque a leitura fresca por um
valor fixo" *ou* "reponha o parâmetro alimentado pelo snapshot" como se fossem
equivalentes. A primeira produzia exatamente as falhas de outro controle do
mesmo arquivo — era a mesma injeção com outro nome, e nenhuma das duas separava
"o gate existe" de "o gate lê fresco". Se duas variantes dão o mesmo vermelho,
uma delas não mede nada: apague-a.

**Instrução impossível** — manda fazer algo que já está feito. Encontrada uma:
"volte o `return` para dentro do `try` de fora", quando o `return` **já estava**
lá e nunca havia saído. Pior, o comentário de produção correspondente dizia que
ele "vivia" lá no passado, sugerindo uma mudança que não houve. Instrução
impossível é sinal de que a narrativa do conserto está errada, não só o texto do
controle — conserte os dois.

**Injeção que APAGA TEXTO, num gate fail-closed** — a pior das três, porque
inverte a leitura em vez de só envelhecer. Apagar um fragmento de expressão
(um pedaço de string SQL, uma linha de concatenação) pode deixar a expressão
inválida em vez de deixá-la mais fraca. Se o trecho injetado mora dentro de um
`try` cujo `except` é **fail-closed**, a exceção é engolida, o guard passa a
RECUSAR TUDO, **o teste que a instrução declara VERMELHO fica verde** e os
positivos ficam vermelhos — quem seguiu a instrução conclui o oposto do que ela
afirma. Esta família pagou o erro DUAS vezes com a mesma instrução: apagar só o
texto do predicado deixou `1 placeholders but 2 parameters were passed`; apagar
a linha inteira encostou a string na tupla seguinte e deu `'str' object is not
callable`. Nas duas, até 11 vermelhos e o declarado passando.

O remédio não é redigir com mais cuidado, é **trocar o eixo da injeção: ALARGUE
em vez de apagar.** Onde a instrução mandava apagar o termo de status do `where`,
ela passou a mandar trocar `list(PAST_DUE_PAYMENT_STATUSES)` por
`list(PAST_DUE_PAYMENT_STATUSES) + ["active"]` — o termo continua lá e deixa de
discriminar, nada é removido, **não há expressão para quebrar sob nenhuma
leitura**, e as duas grafias plausíveis foram medidas dando o mesmo vermelho.
Regra prática: se o guard é fail-closed, prefira injeção que muda um VALOR à
que apaga um PEDAÇO DE CÓDIGO.

## Como conferir

Reexecutar um controle é aplicar a injeção que ele descreve e rodar os testes
que ele nomeia. Não copie o resultado de outra pessoa: se você mexeu no
conserto, o conjunto de vermelhos pode ter mudado.

```bash
# 1. guarde o arquivo original
cp <arquivo> /tmp/orig.bak
# 2. aplique a injeção exatamente como o controle descreve
# 3. rode os testes que ele nomeia
# 4. restaure SEMPRE
cp /tmp/orig.bak <arquivo>
```

Se o vermelho não aparecer, o controle morreu e o grupo pode não estar medindo
nada — é o caso do §3 ("se o resultado sai igual com e sem o conserto, o grupo
não mede nada").
