# "Seu dia" no app nativo (aba Gastos)

Rascunho de plano, aberto em 2026-10-16. **Só plano: nenhum código do app, do painel web ou do servidor muda neste PR.**
Parte do `docs/plano-etapa5-gastos.md` §4.4 (o card "Seu dia" do `/painel`, servidor em #1024 e cliente web em #1029).
Cada PR de código abaixo confere se o que está aqui ainda vale (a `main` anda; o #1031 já entrou e reescreveu a área, §7).

**Faixa: Leve.** É só cliente do app (`app/`) lendo uma rota que já existe. O servidor não muda, então não há dinheiro novo,
sessão nem schema. Se algum PR passar a tocar o servidor (por exemplo, devolver a média pronta), aquele PR vira **Completo**.

## 1. O pedido

O dono pediu, para o web, o **total do dia como alvo principal**, um título atraente e o card mostrando os detalhes do dia.
O web entregou (#1029): setas de dia, data longa, total grande, pílula contra a média do mês, "média do mês: R$ Y/dia",
fatos (Compras N, Maior compra, Mais em categoria), barra de divisão por categoria, lista de compras do dia e "Ver em Lançamentos".
Este plano leva a **mesma ideia** ao app, na tradução que as decisões do dono para o app exigem:

- **visual, mínimo de texto, ideal ZERO** (2026-10-14; já valia desde o #870): dado em forma, número, ícone, cor. Texto só para
  data do dado, sentido do número, consentimento e instrução. Paridade com o texto do web **não** justifica copiar prosa;
- **quanto mais nativo, melhor** (2026-09-29);
- a **identidade do PDF** `docs/identidade-visual-painel.pdf`: botão `#FF2D8E` com texto `#0F172A`, toque de 44pt, claro e escuro
  seguindo o iPhone.

## 2. O que o app tem × o que falta (varredura por grep, 2026-10-16, `main` 2505b340)

### 2.1 O que já existe e se reaproveita

| O quê | Onde | Observação |
|---|---|---|
| A rota `GET /api/app/gastos?mes=` | `api/v2/app.py:36` (`criar_app` monta `gastos`); `api/nativo/app.py:30-34` troca a dependência por `usuario_do_app` | **Já está no ar** e já traz `dias[].quantidade` e `dias[].categorias[{chave, valor}]` (#1024). Sem chave beta do v2 (a do `/painel`); exige o onboarding do Open Finance, como as outras rotas do app. Matriz de senha: `tests/test_rotas_senha_obrigatoria.py:148`. |
| Quem lê hoje | `rota()` em `app/src/features/painel/provider.tsx:36-39` | **Nenhum recurso `gastos`.** `grep -rn "gastos" app/src` só acha nome de aba/tela (`TelaGastos`, `telas.tsx`). O app **não consome** `/api/app/gastos`. |
| O dia no calendário nativo | `Calendario`, `gastos.tsx:26-37`; `detalhes` = `/api/app/mes-detalhes` | `dias: [{dia, entrou, saiu}]` (`painel.ts:18`; `api/nativo/mes_detalhes.py` `Dia`). **Sem quantidade, sem categoria, sem os maiores.** Uma linha por dia, mais recente primeiro, 7 + "Mostrar todos". Toque → `/extrato?dia=` (`gastos.tsx:33`). No chat (bloco), a linha expande `LancamentosDe` e nunca navega. |
| Folha | `Folha`, `base.tsx:98` | `Modal` com `presentationStyle="pageSheet"` (nativo do iOS). **Decisão do dono (#889): a Folha continua Modal pageSheet, sem alça.** O `Sheet` do expo-router (`ui/componentes/Sheet.tsx`) só serve à rota `_ds`. |
| Dinheiro | `Valor` (`base.tsx:27`), `Money`, `decimalParaCentavos` (`decimal.ts`), `somarCentavos` | Tudo em **centavos inteiros**; `oculto` (modo privacidade) já é tratado em `Valor`, `LinhaCategoria` e `Anel`. |
| Barra de divisão | `BarraComposicao` (`ui/componentes/BarraComposicao.tsx`) | Barra empilhada de 10pt, largura proporcional ao `valor` (> 0), decorativa (some do VoiceOver). Serve como está. |
| Cores de categoria | `CORES_CATEGORIA` (`tokens.ts:141`) | Decorativas, seguem o PDF à risca (não se ajusta por contraste: o Manager já reprovou isso). |
| Estados e avisos | `RecursoDados`, `Vazio`, `Avisos`, `Acao`, `Linha` | Carregando, erro com "Tentar novamente", negado. |
| Padrão de leitura sob demanda | `useLancamentos`, `extrato.tsx:18-31` | Hook com `p.operacao()`, `lote`, abort no cleanup, reinício por `versao`. É o molde para a leitura do mês. |
| Ícones | `Icone.tsx` | Já há `ArrowUp`, `ArrowDown`, `CaretRight`, `CalendarBlank`, `ListBullets`, `X`. **Não há `CaretLeft` nem `Receipt`** (a seta "voltar" gira o `CaretRight` 180°, como o `CaretDown` já faz). O #1031 acrescenta `CreditCard`, `Bank` e `ClockCounterClockwise`. |
| Acessibilidade | `AccessibilityInfo.announceForAccessibility` em `Banner`, `Toast`, `Input` | O anúncio da troca de dia usa o mesmo mecanismo. |
| Teste | `painel_numeros_gastos.test.tsx` (`renderRouter` + `painel.fixture.json`); `app.yml` roda `lint`, `typecheck` e `jest` só quando `app/**` muda | O molde do teste de tela. |

### 2.2 O que falta

1. **Os campos `quantidade` e `categorias[]` do dia não chegam ao app** (o app lê `/mes-detalhes`, que não os tem).
2. **A regra do dia do web** (`webapp/src/dashboard/lib/gastos-dia.ts`: `mediaDoPeriodo`, `diaAbrivel`, `vizinho`,
   `contraMedia`) não existe no app.
3. **Os dois calendários não têm a mesma grade.** `mes_detalhes.ler` soma `dias` por qualquer `dia` das linhas válidas, **inclusive
   fora do mês** (a compra parcelada conserva o dia original; o comentário de `gastos.tsx:29` e a largura de 68 para "outro ano"
   existem por isso). `/gastos` põe essa saída em `fora_da_grade` (`db/gastos.py::_calendario`) e só devolve dias da grade
   `[max(dia 1, corte), fim do mês)`. **Há linha no calendário nativo que não tem dia em `/gastos`.** O desenho trata isso (§3.3, estados).
4. **Um recurso de leitura do mês em `/api/app/gastos`**, com zod, fixture e teste de contrato.

### 2.3 Por qual caminho os campos novos chegam

| | Como | Custo | Risco para o app instalado | Veredito |
|---|---|---|---|---|
| **A. Recurso `gastos` no provider** | acrescenta `gastos` a `schemasRecurso` e à `rota()` | **+1 requisição em toda carga completa** (hoje 15 recursos), mesmo sem ninguém abrir a folha; muda o `Dados`/`Resultados` do provider que o app inteiro usa | nenhum (servidor igual) | descartado: paga em toda carga por um dado de um toque |
| **B. Leitura sob demanda** (`useGastosMes(mes)`), ao abrir a folha pela primeira vez | hook no molde do `useLancamentos`, cache por `[usuário, versao, mês]`, abort no cleanup | uma requisição por mês aberto; spinner na 1ª abertura (latência **não medida**) | nenhum (servidor igual) | **recomendado**: nada muda para quem não abre a folha |
| **C. Estender `/api/app/mes-detalhes`** com `quantidade`/`categorias` por dia | muda `db/mes_detalhes.py` e o modelo `Dia` | **Completo** (dinheiro, e `ler` é lido pelo WhatsApp, pela IA e pelo chat); duplicaria a regra de grade que `/gastos` já tem | acréscimo de campo é seguro (`z.object` sem `.strict()` em todo `painel.ts`: 0 ocorrências de `.strict()`), mas o app instalado não ganha nada | descartado |
| **D. Embutir `gastos` no bloco do chat** | muda `core/services/ai_chat/cards.py` | Leve/Completo no servidor | nenhum | fora do escopo (§3.4) |

**Regra do app instalado.** App instalado **não atualiza com o deploy do backend** (e o backend deploya sozinho no merge). Aqui o
servidor não muda e nada que o app instalado lê muda: a rota `/api/app/gastos` já existe e ele não a chama. A leitura nova só existe
no build novo. O risco inverso (o servidor mudar um campo que o build novo lê) é do build novo, e é coberto pelo teste de contrato do PR 1
(§6). O schema do app lê **só o que a folha usa** e nada além (§4.1).

## 3. O desenho

### 3.1 Três apresentações

**A. Folha inferior ao tocar no dia (recomendada).**
```
 ─────────────────────────────
  ‹  qui, 16 out  ›         ✕
         R$ 142,30     ↓12%
  ▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰
  🍔 🛒 🚌                 ☰ 5
  Padaria Pão         R$ 48,00
  Mercado X           R$ 41,30
  Uber                R$ 22,90
  [ Ver extrato do dia      › ]
```
O toque que hoje navega para o extrato passa a abrir a `Folha` (já existente) com o dia. O extrato continua a um toque, dentro da folha.
Prós: reaproveita a `Folha` (decisão do dono), o toque no dia **já existe** (troca só o destino), sem rota nova, sem mexer em
`native-intent`/portão, o dia fica no contexto da lista de dias. Contras: um modal sobre a aba; dentro do Resumo o `Calendario` já mora
numa `Folha` (§8, risco 2).

**B. Tela de dia** (rota nova, p. ex. `/gastos/dia`, com cabeçalho nativo e voltar). Prós: navegação "nativa" de pilha; dá para deslizar
entre dias. Contras: rota nova no `expo-router` (portão do painel, `native-intent`, testes de deep link), mais código e mais superfície
de regressão para um card que tem 8 elementos; perde o contexto da lista.

**C. Card fixo "Seu dia" no topo da aba Gastos** (hoje por padrão, troca o dia ao tocar na lista). Prós: o total de hoje já está na
tela sem toque. Contras: a lista de dias fica abaixo de `NumerosMes`, "Para onde vai" e do próprio card, então o dia tocado fica longe
do card que muda; a aba já é longa; o card ocupa a tela inteira o tempo todo, para um dado que se consulta de vez em quando.

**Recomendação: A.** Menor mudança que entrega o pedido; B pode vir depois sobre a mesma base de dados (o PR 1 não depende de A).

### 3.2 O que do card web vira o quê no app (alternativa A)

| Elemento do web | No app | Por quê |
|---|---|---|
| Título "Seu dia" | **corta**; o título da `Folha` é a **data curta** ("qui, 16 out") | a data é o dado; o rótulo é texto de enfeite |
| Setas dia anterior/próximo | **mantém**, 44pt, só ícone; "‹" é o `CaretRight` girado 180°; `disabled` quando `diaAbrivel` for falso | navegar entre dias é o uso principal; sem texto |
| Data longa | só no `accessibilityLabel` | a curta basta à vista |
| **Total do dia, grande, em rosa** | **mantém**, `Valor destaque`; cor `ink` (texto rosa só com `brandInk`, nunca `brand`, que é "ícone/gráfico", `tokens.ts:85`) | o alvo principal do pedido |
| Pílula contra a média ("↓ N% abaixo da média") | **traduz** para `ArrowDown`/`ArrowUp` + "N%" (só o sentido e o número); tom `positive` (≤ −5), `warning` (≥ +15), `inkMuted` no meio; "na média" (0) vira "0%" sem seta | sentido do número é permitido; as palavras "abaixo/acima da média" vão para o `accessibilityLabel` |
| "média do mês: R$ Y/dia" | **corta** da vista (vai no `accessibilityLabel` da pílula) | é a referência da pílula, mas é prosa + número; aprovado pelo dono (§9) |
| Fato "Compras N" | **traduz** para `ListBullets` + N | N é o dado |
| Fato "Maior compra" | **corta** | é a 1ª linha dos maiores |
| Fato "Mais em \<categoria\>" | **corta** | é a 1ª fatia da barra, com o emoji dela |
| Barra de divisão + legenda "emoji nome · R$" | **traduz**: `BarraComposicao` + **só os emojis** das fatias (até 4; "+N" se houver mais); nome e valor só no `accessibilityLabel` | forma e ícone no lugar de texto; só com ≥ 2 fatias > 0 |
| "Compras do dia" (lista completa do `/lancamentos`) | **corta**; fica **os 3 maiores** (`maiores`, já no payload), descrição + valor | a lista completa é o extrato; zero requisição extra |
| "Ver em Lançamentos" | **traduz** para "Ver extrato do dia" → `/extrato?dia=` (rota existente; **o `Extrato` passa a mandar `dia` na consulta**, §5) | a instrução indispensável; o app chama a tela de **Extrato** |
| Região `role="status"` ao trocar o dia | `AccessibilityInfo.announceForAccessibility` (data + total; **só a data com `oculto`**: o anúncio não passa pela máscara do `Money`) | equivalente nativo |
| Tag "Só \<categoria\>" / modo categoria | **fora**: o app não tem o filtro de categoria no calendário nem o modo categoria | não existe no app hoje |

Texto visível na folha: **a data (título), o total, "N%", N, 3 descrições e 1 botão**. Nenhuma frase.

### 3.3 Estados

| Estado | O que a folha mostra |
|---|---|
| carregando (1ª abertura do mês) | `ActivityIndicator` com `accessibilityLabel` (padrão do `RecursoDados`); a data e o ✕ já aparecem |
| erro (rede, 5xx, contrato) | `mensagemRecurso` + "Tentar novamente" (padrão do `RecursoDados`). **402 e 403 `open_finance_onboarding_required` caem no portão, mas não de graça:** `p.falhou` (`provider.tsx:73-76`) só trata sessão expirada e 402; o 403 do onboarding é tratado **dentro** do `carregar` do provider (`provider.tsx:131`). O `useGastosMes` precisa do mesmo tratamento (achado do Codex no #1050; o `useLancamentos` tem a mesma lacuna hoje), §5 |
| `disponivel: false` (mês todo antes do corte) | **não abre a folha**: o toque navega para o extrato, como hoje (o calendário nativo também não terá linha útil) |
| dia **fora da grade** (a linha existe em `detalhes` mas não em `/gastos.dias`, §2.2.3) | **não abre a folha**: o toque navega para o extrato, como hoje. Nunca mostra "R$ 0,00" para uma linha que tem valor |
| dia da grade **sem saída** (só entrada) | data, setas, `Valor` R$ 0,00, ✕ e o botão do extrato; sem pílula, contador nem barra |
| **hoje** | total sem pílula (dia em curso não compara com a média) |
| **agendado** (dia futuro com lançamento) | `CalendarBlank` ao lado do total, sem pílula; o dia só se abre se tem gasto agendado |
| fora do histórico (antes de `historico_desde`) | seta desabilitada; nunca "R$ 0,00" |
| privacidade (`oculto`) | total e valores mascarados (`Valor`), **sem** pílula, **sem** barra, descrições "Lançamento" (igual a `LinhaLancamento`); o `accessibilityLabel` não carrega valor |
| valor longo ("R$ 1.234.567,89") | **não medido**: `Money` display + `numberOfLines`/`adjustsFontSizeToFit` (já usado em `CurvaAcumulada`); medir no simulador |
| fonte grande (`ampliado`, `fontScale > 1.1`) | coluna no lugar de linha (padrão do `Linha`); sem barra se `ampliado`, como o `Calendario` já faz com as barras; setas e botão continuam 44pt |
| VoiceOver | o total é o 1º elemento focado; pílula com label completo ("12% abaixo da média do mês, R$ X por dia"); barra decorativa escondida e substituída por um elemento com o texto das fatias; setas com `accessibilityLabel` "Dia anterior/seguinte" e estado `disabled`; anúncio da troca |
| tema escuro | só tokens (`useTema().cores`), nunca hex; `positive`/`warning` já têm par nos dois temas; contraste conferido com `ui/contraste.ts` se algum par novo aparecer |

### 3.4 Fora (de propósito)

- Deslizar entre dias com o dedo (gesto horizontal dentro do pageSheet): v2, depois de ver o uso. As setas cobrem.
- O card do **chat** (`bloco`): `/gastos` não vem embutido no bloco, então o `Calendario` do chat segue expandindo `LancamentosDe`.
  Levar o "Seu dia" ao chat é o item D da §2.3 (servidor) e pertence ao plano do chat (E2/E3), não a este.
- Modo categoria, filtro por categoria, lista completa de compras na folha, editar/apagar.
- Pílula com a média em texto (decisão do dono, §9; ver a pergunta aberta 2 da §10).

## 4. Regras de dinheiro

### 4.1 Contrato lido e schema
- O zod do app lê **só**: `hoje`, `disponivel`, `historico_desde`, `dias[{dia, total, quantidade, maiores[{descricao, valor, categoria}], categorias[{chave, valor}]}]`,
  `categorias[{chave, emoji, cor?}]` (para a posição/cor e o emoji da fatia) e `motivos`. Dinheiro com o `dinheiro` do `painel.ts`
  (texto decimal → **centavos inteiros** por `decimalParaCentavos`, HALF_UP) **só para exibir**. Para a média e o `pct` (§4.2) o schema guarda também o **texto decimal bruto** de
  `dias[].total` (o servidor devolve `Decimal` sem arredondar, "1.137" existe): arredondar para centavos antes de calcular muda o resultado (§4.2); a conta do §4.2 lê esse texto com `Number(raw)`, como o web. Campo a mais é ignorado (sem `.strict()`).
- `total` do dia, o que a folha mostra, vem de `dias[].total`. **O app não soma saída nenhuma** além da média do §4.2.

### 4.2 A única conta nova no cliente: a média do calendário
É a mesma conta de `lib/gastos-dia.ts`: média = (Σ `total` dos dias com `total > 0`, de `max(dia 1, historico_desde)` até `min(hoje, fim do mês)`) ÷ nº de dias
desse intervalo (dia agendado e dia antes do corte **não entram, nem como zero**). `pct = round((total/média − 1) × 100)`, tom por −5/+15,
sem comparação com média ≤ 0, nula ou total ilegível. Mesmas regras de `diaAbrivel`/`vizinho`.

**Fonte de verdade (§0.7).** O app não importa o web (projetos npm separados; é o precedente do `metasTextos.ts`). Duas implementações, **uma tabela**:
- `app/src/features/painel/gastosDia.ts` reimplementa `mediaDoPeriodo`, `diaAbrivel`, `vizinho` e `contraMedia` **emulando a aritmética do web expressão a expressão**
  (achados do Codex no #1050): `Number(raw)` de cada `total`, a mesma ordem das operações e `Math.round` em `double`. **A autoridade é o web** (já entregue, #1029), e o tom da
  pílula que ele mostra hoje é o comportamento observável a reproduzir. Duas alternativas foram descartadas: (a) centavos primeiro, que muda o tom em casos sub-centavo
  (`"1.040"` e `"1.137"`: web −4% neutro, centavos −5% positivo); (b) decimal exato, que erra nos empates do meio porque o `double` do web cai do outro lado (`"0.191"` e `"0.209"`:
  `(0.191/0.2 − 1)·100 = −4.5000000000000036`, então o web dá −5% e o decimal exato daria −4%). O `float` fica restrito a este cálculo de razão, mostrado como inteiro; **todo valor exibido
  em reais** (o total do dia, as maiores compras) continua em centavos inteiros pelo `decimalParaCentavos`, e a média em R$ do `accessibilityLabel` é arredondada a centavos. Mudar o web para
  decimal exato seria outro PR (web) e está fora deste plano;
- **tabela dourada** `tests/fixtures/gastos_dia_casos.json` (entrada: `mes`, `hoje`, `historico_desde`, `dias` com `total` em texto, **inclusive com 3 ou mais casas**;
  saída: `media`, `nDias`, `pct`, `tom`, `diaAbrivel` por dia, `vizinho`) lida pelo `jest` do app **e** pelo teste web existente
  `tests/frontend/dashboard_v2_gastos_dia_topo.test.mjs` (que já importa o `.ts` direto, linha 18; só ganha um caso que lê a tabela: não toca no código do painel).
  A tabela entra em `paths` do `.github/workflows/app.yml` (precedente: `tests/fixtures/ai_chat_eventos.json`). A mesma ideia já vive no
  #1031 (`tests/fixtures/percentual_categorias.json`, Python × TS).
- **Casos que a tabela precisa ter:** `"1.040"` e `"1.137"` (primeiro dia −4% neutro; é o controle negativo de "centavos primeiro", que dá −5%); `"0.191"` e `"0.209"` (−5% positivo no web por erro de
  `double`; é o controle negativo de "decimal exato", que dá −4%); `"91.010"` × `"1.005"` (escalas diferentes no mesmo mês); total com 6 casas; empates exatos no meio onde o `double` cai para cada lado.
  Os valores esperados são **os que o web produz** (a tabela é gerada rodando `lib/gastos-dia.ts`), e os dois lados leem a mesma tabela: se o app divergir, o jest do app fica vermelho.
- **Não verificado:** se o `jest` do app consegue importar o `.ts` do web direto (`jest.config.js` não define `roots`; não testei). Se der, o teste de paridade importa o web em vez de usar tabela.

### 4.3 O resto
- **Fatia ≤ 0 é descartada** antes da barra (estorno ou zero inseridos direto no banco podem vir do servidor); a barra só desenha com ≥ 2 fatias > 0.
  `BarraComposicao` já exige `valor > 0`; o filtro fica no chamador.
- **Positividade e proporção da barra usam o decimal bruto, não os centavos (achado do Codex no #1050).** O schema guarda o texto de `categorias[].valor` (e de `maiores[].valor`) como guarda o de `total`;
  o filtro "> 0" e o `valor` de cada `Segmento` saem de `Number(raw)`, como o `flex-grow` do web. Com `"0.004"` e `"0.006"` a barra é 40/60 (em centavos seria 0 e 1, a 1ª fatia sumiria e a barra
  também). Centavos só no que se **exibe** (o `accessibilityLabel`, as maiores compras). Regra geral: **toda decisão** (positivo, zero, ≤ 0, tom da pílula, "dia sem gasto") usa o bruto, como o web;
  centavos só para mostrar. Teste: categorias sub-centavo `"0.004"` + `"0.006"` desenham 2 segmentos 40/60; `"0.004"` sozinha não desenha barra.
- **Nunca comparar o texto de `total` com o de `categorias[].valor`** (a escala pode diferir, "91.010" × "1.005"). A folha nem mostra o valor por categoria
  (só no `accessibilityLabel`, em centavos), e a largura da fatia usa só o `valor` bruto da categoria.
- `hoje` vem de `/gastos` (o servidor, no fuso do app), **não** do relógio do aparelho. Em `Calendario`, `hojeSP()` segue valendo para o resto do painel.
- `arredondamento_por_grupo` em `motivos`: `Avisos` já cobre; a folha não repete.
- Total ilegível ou `dinheiro` inválido: o zod lança `ContratoInvalido` ("Recebemos dados incompatíveis"), vira erro recuperável da folha, nunca R$ 0,00.

## 5. Interação com o resto do app
- `Calendario` (provider): toque no dia → abre a folha **se** o dia está na grade de `/gastos` e o mês está `disponivel`; senão, o `navegar({pathname:"/extrato", params:{dia}})` de hoje.
  O destino existente do botão da folha é o mesmo.
- **Fechar toda folha antes de abrir o Extrato (achado do Codex no #1050).** `/extrato` é navegação entre abas irmãs: a tela de origem segue montada e o `Modal` do RN pode ficar por cima do
  destino. O `navegar` do painel só roda o `aoSair` do `TelaPainel` (`setFolha(null)`, `cabecalho.tsx`), que fecha **as folhas do cabeçalho** (perfil, mês, organizar, conta); ele não limpa o dia
  aberto dentro do `Calendario` **nem a folha de detalhe do `TelaResumo`** (`telas.tsx`, estado `detalhe` próprio) quando o calendário está dentro dela. (O toque no dia dentro dessa folha do Resumo, que
  hoje já navega, tem o mesmo risco: **não verificado** no simulador.) O PR 2 faz o botão "Ver extrato do dia" fechar a folha do dia **e** a folha que a contém antes de navegar:
  o contexto de navegação ganha um registro de "fechadores" (`ContextoNavegacaoPainel`/`useNavegarPainel`, onde o `aoSair` já vive), a folha do dia e o `TelaResumo` registram o seu, e o `navegar`
  roda todos antes do `router.push`. Teste: nas duas origens (aba Gastos e Resumo) nenhuma folha fica visível depois do toque e o Extrato está montado com o `dia` pedido.
- **Zerar os filtros do Extrato ao entrar pelo dia (achado do Codex no #1050).** O `Extrato` guarda `q`, `busca`, `origem` e `tipo` em `useState` local (`extrato.tsx:40`) e as abas do
  `expo-router` ficam montadas (provável; **não verificado**), então "Entradas" escolhido antes combinaria com o dia e a tela diria "vazio". Ao chegar com `dia` (a mudança de `params.dia`)
  o `Extrato` zera os quatro filtros; o PR 2 inclui isso. Teste: escolher "Entradas", voltar a Gastos, abrir um dia com saídas pelo botão: o extrato mostra as saídas do dia.
- **`Extrato` com `dia` na consulta (achado do Codex no #1050; já é um defeito de hoje).** `extrato.tsx:42-47` pede só `mes` e `limite=50` e filtra `params.dia` **no aparelho**
  sobre a 1ª página. Num mês com mais de 50 lançamentos, o dia que cai depois da 1ª página aparece vazio ("Nenhum resultado nesta página.") ou parcial, e o botão da folha
  prometeria o extrato completo do dia. `/api/app/lancamentos` já aceita `dia` (`api/v2/lancamentos.py:79`, sempre junto do `mes`, que é o da fatura), então a correção é **só do
  app**: o PR 2 acrescenta `dia` à `URLSearchParams` do `Extrato` e passa a usar o `dia` como parte da consulta (o filtro no aparelho deixa de ser necessário, e o `chave` extra do
  `useLancamentos` também). Servidor igual, app instalado não muda. Teste: mês com > 50 lançamentos e o dia na 2ª página (vermelho antes, verde depois).
- `Calendario` (chat, `bloco`): **inalterado**.
- **403 do onboarding na leitura sob demanda (achado do Codex no #1050).** Se o onboarding do Open Finance deixa de estar completo entre o foco e a leitura, `/api/app/gastos` devolve 403
  `open_finance_onboarding_required`. Hoje só o `carregar` do provider o leva ao portão (`cancelar(); setGate("negado"); setDados({})`, `provider.tsx:131`); `p.falhou` não. O PR 1 extrai essa
  checagem para um ponto só (o 403 com o corpo do onboarding tratado em `falhou`, ou um helper chamado pelo `carregar` e pelo hook) e o `useGastosMes` a usa; o `useLancamentos` passa a se beneficiar
  (§0.7: uma regra). Teste: 403 do onboarding na leitura do mês manda o painel para o portão, e **não** mostra "Tentar novamente"; 403 de outro motivo segue como "negado" do recurso.
- `mudarMes`/`atualizar`/`versao`: a leitura do mês reinicia por esses gatilhos (molde do `useLancamentos`), e fecha a folha se o mês muda.

## 6. Testes e verificação

**Jest (`app/`, roda no CI `app.yml`):**
- paridade com a tabela dourada (`gastosDia`), com **controle negativo**: trocar a regra de borda (−5/+15, dia agendado fora da média) tem de deixar vermelho;
- o schema contra a fixture; **controle negativo**: campo de dinheiro inválido → `ContratoInvalido`, não 0;
- a folha pelo `renderRouter` (molde de `painel_numeros_gastos.test.tsx`): toque abre; setas e limites (`disabled` no 1º/último dia aberto); estados da §3.3
  (carregando, erro com "Tentar novamente", dia sem saída, hoje, agendado, dia fora da grade navega, `disponivel:false` navega, `oculto` sem pílula/barra/valores no label **nem no anúncio da troca de dia**);
  fatia ≤ 0 fora da barra; `"91.010"` × `"1.005"` sem erro; `fontScale` ampliado; `accessibilityLabel` com data, total e sentido;
- fechamento: depois de "Ver extrato do dia" (aba Gastos e Resumo) nenhuma folha fica visível e o Extrato está montado; controle negativo: sem o registro de fechadores o `Modal` do Resumo continua visível;
- filtros: "Entradas" escolhido antes, voltar a Gastos e abrir um dia com saídas mostra as saídas do dia (hoje combinaria com o filtro e ficaria vazio);
- barra: categorias `"0.004"` + `"0.006"` dão 2 segmentos 40/60 e `"0.004"` sozinha não desenha barra;
- `Extrato` com `dia`: mês com > 50 lançamentos e o dia fora da 1ª página mostra as compras do dia (hoje mostra vazio); a consulta leva `mes` e `dia` juntos;
- o negativo do conserto de grade: com o fallback desligado, a linha fora da grade abre a folha com R$ 0,00 → o teste fica **vermelho**;
- **servidor, só teste** (`tests/test_api_app_gastos.py`, sem mudar o código do servidor): usuário **sem** chave beta lê `/api/app/gastos` (200) e a fixture do app valida contra o
  `Gastos` do Pydantic; onboarding do OF incompleto dá 403 `open_finance_onboarding_required`; outro usuário não vê dado alheio; e `dias[].total` de `/gastos` **bate com** `dias[].saiu`
  de `/mes-detalhes` para todo dia da grade (a prova de que a linha tocada e a folha dizem o mesmo número). Hoje só `/gastos/categoria` tem teste pelo `/api/app` (`test_api_v2_gastos_categoria.py:279`).

**Simulador (ferramentas do iOS Simulator):** abrir a folha com servidor falso (`servidor-falso.mjs` na 8787, memória do build 22; a fixture precisa do `/api/app/gastos`); claro e escuro;
Dynamic Type máximo; valor longo; `oculto`; setas; Resumo (folha dentro de folha, risco 2); screenshot de cada estado. Lição registrada: o simulador acha o que o jest não mede (gesto, quadro).

**Só no aparelho (ficam "não verificados" até lá):** VoiceOver real (foco, rotor, anúncio da troca); a tampa de privacidade e o `inactive` com a folha aberta (a tampa nativa cobre o `Modal`?); latência real da 1ª abertura; Dynamic Type no iPhone físico.

**Dados reais (leitura, produção, avisando antes):** conta do dono, um mês: `/api/app/gastos` × `/api/app/mes-detalhes`, dia a dia (`total` × `saiu`) e a média contra a do web no `/painel`. Banco real só em caso extremo; padrão é o `pigbank_ci_test`.

**O que foi medido neste PR:** só leitura estática (grep e leitura dos arquivos citados). **Nada foi executado**: nenhum jest, nenhum simulador, nenhuma chamada ao servidor.

## 7. Build e dependências
- Build novo do app pelo Xcode (`export` com `PATH=/usr/bin` primeiro; o app não tem projeto EAS). O `buildNumber: "15"` de `app.config.ts:66` é velho **de propósito**: o Xcode usa
  `manageAppVersionAndBuildNumber` e escolhe o próximo livre no App Store Connect. **O último número registrado é o 30 (2026-10-12)**; pode ter havido builds depois, então o próximo
  é "o que o ASC der" e quem confirma é o dono (a sessão não acessa o ASC).
- **Depende de outro PR?** Do **#1031 (E2, card de gastos do chat), já mergeado na `main` em 2026-10-17**: ele tocou `gastos.tsx`, `painel.ts`, `decimal.ts` e `Icone.tsx`, os mesmos arquivos dos PRs 1 e 2.
  Os números de linha acima foram conferidos na `main` depois do merge; **cada PR de código parte da `main` atual e reconfere as referências**. No chat, o `Calendario` com `bloco` agora expande os
  lançamentos do dia (`?dia=` do endpoint, `gastos.tsx:29`): isso fica como está, a folha do plano só vale fora do chat. **Não depende** de E1 (caixinhas, build 27, já feito) nem de metas/C4. Nenhum PR de servidor.
- O PR 1 e o 2 não dependem do web nem mexem nele (só um caso novo no teste web existente).

## 8. PRs (cada um verificável sozinho)

| # | Conteúdo | Faixa | Deixa pronto |
|---|---|---|---|
| **1** | **Base, sem UI.** `gastosSchema` + fixture de `/api/app/gastos`; `gastosDia.ts` (emula o `double` do web) + tabela dourada com casos sub-centavo e de empate + caso no teste web; `useGastosMes` (com o 403 do onboarding indo ao portão, §5); `tests/test_api_app_gastos.py` (só teste); `paths` do `app.yml`. | **Leve** | tudo que a folha lê, provado por teste e contrato; nenhuma mudança visível; o Manager confere a conta de centavos |
| **2** | **A folha do dia.** `Calendario` abre a `Folha` com data, setas, total, contador, estados, fallback para o extrato, privacidade, a11y e anúncio; **`Extrato` pede `dia` ao servidor** e as folhas se fecham antes de navegar e o `Extrato` zera os filtros ao entrar pelo dia (§5). Sem pílula nem barra. | **Leve** | o essencial do pedido ("o total do dia é o alvo"); simulador claro/escuro/fonte grande; `Resumo` (folha dentro de folha) resolvido |
| **3** | **O dia em contexto.** Pílula ↓↑ %, barra com emojis e os 3 maiores. | **Leve** | paridade visual com o web na versão sem texto; o jest dos tons e da barra |
| — | **Build** pelo Xcode (dono) e validação no iPhone. Não é PR. | — | fecha o que só o aparelho prova |

O dono escolheu a opção (a) (§9), então o 2 e o 3 ficam separados como na tabela.

### Riscos
1. **Grade diferente entre `detalhes` e `/gastos`** (§2.2.3): mitigado pelo fallback para o extrato, pelo teste `saiu == total` e pelo negativo que religa o bug.
2. **`Folha` dentro de `Folha` no Resumo** (o `Calendario` do Resumo já abre numa folha): não há precedente no repo; **não verificado**. Plano B: dentro da folha o toque segue navegando ao extrato; plano C: trocar o conteúdo da própria folha.
3. **Conflito com o #1031**: resolvido, ele já está na `main`. Resta o risco de a `main` andar de novo em `gastos.tsx`: cada PR de código rebaseia e reconfere as linhas citadas.
4. **Média e %: o app emula a aritmética `double` do web** (tom idêntico, inclusive nos empates do meio); tabela dourada com casos sub-centavo e de empate, gerada pelo próprio web.
5. **Leitura sob demanda:** a 1ª abertura de cada mês mostra o carregando; `versao` e troca de conta reiniciam (o `useLancamentos` já trata); resposta pequena (≤ 31 dias × 3 maiores + categorias).
6. **O servidor mudar um campo lido:** o build novo quebra com "dados incompatíveis"; o teste de contrato do PR 1 pega no CI do backend.
7. **Privacidade:** pílula e barra revelam proporção; somem em `oculto` (como `LinhaCategoria` já faz).
8. **Emoji da categoria vem do usuário** (o servidor já troca vazio/inválido pelo neutro); quebra de linha e fonte grande medidas só no simulador.
9. **Onde o dia mostrado "hoje" muda** (virada da meia-noite): `hoje` vem do servidor e só atualiza no próximo carregamento; mesmo comportamento do resto do painel.

## 9. Decisões do dono (2026-10-16)
1. **Apresentação: folha ao tocar no dia** (alternativa A da §3.1). Aprovada como recomendada; o Extrato do dia fica dentro da folha.
2. **Conteúdo: opção (a)**: data como título, total grande, seta + %, contador (ícone + N), barra com emojis e as 3 maiores compras; sem a média em texto,
   sem "Maior compra", sem "Mais em", sem lista completa (a tabela da §3.2 vale como está).

## 10. Perguntas que seguem abertas
1. *(menor, sem bloquear)* Deslizar entre dias com o dedo, depois das setas? Deixado fora da v1.
2. A média em reais só no leitor de tela ("R$ Y por dia") é aceita como suficiente para entender o "N%"? Se o dono sentir falta na tela, a alternativa sem texto é uma marca da média
   numa mini-barra do dia (forma no lugar de frase); não entra sem pedido.
3. Número do próximo build e se alguém mexeu no app depois da 30: só o dono vê no App Store Connect.
