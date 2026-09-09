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
