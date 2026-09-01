# Handler inline × escopo global — parsing pelo navegador

> **Plano, antes de implementar.** Substitui a análise estática do PR #213, que
> acumulou 29 apontamentos do Codex em 17 commits — **28 deles na mesma metade**,
> a que lê texto-fonte. A causa não é desatenção: aquela metade reimplementa um
> parser de HTML e de JavaScript, e cada rodada acha outra construção que o
> navegador trata e o scanner não.

## Por que o #213 não entra como está

A metade de runtime **não é independente de forma útil**. Medido, nomes verificados
por página, com os stubs `{}`:

| página | fonte | só DOM | perda |
|---|---|---|---|
| admin-dashboard | 17 | 2 | **88%** |
| dashboard | 150 | 84 | **44%** |
| settings | 31 | 29 | 6% |
| home | 8 | 7 | 13% |
| precos | 4 | 3 | 25% |
| cadastro / login | 4 / 4 | 4 / 4 | 0% |
| **total** | **218** | **133** | **39%** |

Os 66 que faltam no `dashboard` são os handlers **gerados a partir de dados**
(`payBill`, `openCardDeleteModal`, `saveGoal`, `openInstEditModal`…) — exatamente
os que nenhum outro teste cobre.

**Fixture não resolve barato:** com `{}` a página chama **um** endpoint
(`/api/affiliate/me`) e para. Fazer os 66 renderizarem exigiria fixture para
cartões, faturas, metas, orçamentos, categorias, recorrentes, investimentos,
caixinhas, afiliados e agentes — fixture por página, que apodrece.

## 1. O que passa a ser responsabilidade do navegador

**Todo parsing de HTML.** Some por construção a categoria que gerou a maioria dos
29 apontamentos:

- forma do valor de atributo (aspa dupla, simples, ausente, espaço em volta do `=`);
- referência de caractere (`&quot;` `&#39` `&grave;` … e a regra do `;` opcional);
- comentário HTML e comentário de JS confundidos com markup;
- `data-onclick` / `data-src` casando como se fossem `onclick` / `src`;
- descoberta de `<script src>` e quais o navegador realmente carrega.

Duas vias, as duas usando o parser do próprio navegador:

1. **Páginas** — navegação real; os handlers saem do DOM (`el.attributes`), já
   decodificados.
2. **Markup gerado** — os trechos de template literal do `.js` vão para um
   `innerHTML` num documento descartável, e os handlers saem do DOM daquele
   fragmento. É o mesmo parser, sem precisar de dado que faça a página renderizar.

## 2. O que continua sendo análise estática

Só duas coisas, as duas dentro de `.js`:

- **achar os template literals** que contêm markup;
- **neutralizar `${...}`**, porque o conteúdo da interpolação roda na geração, no
  escopo de quem gera, e cobrá-lo é falso positivo.

Não há parser de JS disponível (`acorn`, `espree`, `@babel/parser`: nenhum
instalado; `node_modules` local vazio) e o §0.6 desaconselha dependência nova para
o que dá para resolver sem ela. Então isto continua sendo um scanner — **mas o
domínio dele encolhe de "HTML + JS" para "achar crase e contar chave"**.

Resta também extrair identificadores do valor de um handler. A diferença é o
insumo: hoje é texto-fonte cru; passa a ser uma expressão **já decodificada pelo
navegador**, sem entidade, sem interpolação e sem questão de delimitador.

## 3. Como evitar falso negativo silencioso

O modo de falha que mais apareceu no #213 não foi nome inventado — foi **o gate
se desligando sem avisar** (`src` com aspa simples, com espaço, sem aspas;
`data-src`; script comentado). Verde medindo nada.

Três defesas:

1. **Piso por página, versionado.** Um arquivo com a contagem mínima de nomes por
   página. Cair abaixo é **vermelho**, com a mensagem dizendo qual página encolheu
   e de quanto. Subir pede atualização explícita do piso. É o que teria pegado
   todos os casos de "desligou em silêncio" na primeira rodada.
2. **Premissas que falham alto**, como já existem hoje: página que navegou para
   fora, página com zero script, extrator que devolveu menos páginas que o piso.
3. **Descartar em vez de adivinhar.** Trecho que o scanner não entende com certeza
   não vira exigência. Perder cobertura é o lado seguro; inventar nome reprova
   código correto — e o piso do item 1 é o que impede essa perda de ser silenciosa.

## 4. Quais controles antigos deixam de existir

Somem porque o que eles testavam deixa de ser código meu:

- os que injetavam forma de atributo (`src='…'`, `src = "…"`, `src=/x.js`,
  `onclick=semAspas`, `data-onclick`, `data-src`);
- os de referência de caractere (`&quot;` `&#34;` `&#x22;` `&apos;` `&#39;`
  `&#x27;` `&#96;` `&#x60;` `&grave;` `&QUOT;` `&DiacriticalGrave;`, com e sem `;`);
- o de comentário HTML/JS contendo `onclick=` ou `<script src>`;
- **o teste "o mapa de referências nomeadas casa com o navegador"** — o mapa
  deixa de existir, então o teste que o guardava também.

Continuam existindo, porque continuam sendo código meu: os de interpolação
(`${...}` com chave aninhada, com aspa dentro, com aspa escapada) e os de
extração de identificador (chamada aninhada, chamada guardada, argumento nu,
`window.X()`, string esvaziada antes da busca).

## 5. Quais regressões dos 29 apontamentos seguem cobertas

| classe | apontamentos | no desenho novo |
|---|---|---|
| forma de atributo HTML | 5 | **eliminada** — o navegador parseia |
| referência de caractere | 4 | **eliminada** — o navegador decodifica |
| comentário confundido com markup | 2 | **eliminada** — não se lê mais o `.js` como markup |
| limite de atributo (`data-*`) | 2 | **eliminada** — atributo vem do DOM |
| descoberta de `<script src>` | 3 | **eliminada** — o navegador diz o que carregou |
| interpolação `${...}` | 6 | **continua** — controle mantido |
| extração de identificador | 6 | **continua** — controle mantido |
| stub da página (metade runtime) | 1 | **continua** — controle mantido |

**Comparação é de comportamento, não de história.** O critério de aceite do PR novo
é o conjunto de nomes por página igual ou maior que o do `e92bf82`, com o diff nome
a nome publicado — não "os 17 commits foram reproduzidos".

## O que não vou fazer

Não carrego os 17 commits de remendo. O branch sai da `main`, e o teste é escrito
de novo com o desenho acima. O `#213` fecha quando este entrar.
