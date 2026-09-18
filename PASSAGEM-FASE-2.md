# Passagem — Fase 2 (design system do app nativo)

Escrito em 2026-09-17, no fim da execução da Fase 2 do PigBank Mobile 2.0.
Quem pegar a Fase 3 começa lendo este arquivo e o `docs/` citado no fim.

## O que ficou pronto

A Fase 2 saiu em **quatro PRs empilhados**, mergeados nesta ordem em
2026-09-17. Todos estão na `main`:

| PR | O que é | Faixa (CLAUDE.md §0) | Merge |
|---|---|---|---|
| A — #472 | Fundação: tokens, contraste, tema, `Texto`, motion, haptics, Inter, rota `/_ds` | Leve | `203292e` |
| B — #475 | `Money` e `AmountInput` | Completo (dinheiro) | `ea9c0aa` |
| C1 — #477 | 11 primitivas + snapshot do `Texto` | Leve | `342be4a` |
| C2 — #482 | Composição, stickers e telas-modelo | Leve | `3b2e2e4` |

Cada um foi aprovado pelo Codex e mergeado travado no SHA aprovado. Na `main`
(`3b2e2e4`): 37 suítes, 420 testes e 42 snapshots verdes, com App, Tests e
Smoke (produção) verdes no CI.

Nenhuma tela de produto entrou. A tela de entrada da Fase 1 continua provisória
e só mudou de cor de erro (`negative` virou `danger`) e de família de fonte.

### Critério da fase, item a item

- **"Cada componente tem teste de render nos dois temas e snapshot"**: cumprido
  para os 18 componentes (`Texto`, `Money`, `AmountInput`, as 11 primitivas do
  C1 e os 7 da composição). O `Texto` ficou sem snapshot no PR A e ganhou o
  dele no C1 — está registrado no PR.
- **"Contraste AA medido nos tokens, por script"**: `app/src/ui/contraste.ts`
  calcula a razão WCAG e `contraste.test.ts` roda a tabela inteira nos dois
  temas (texto ≥ 4,5; não-texto ≥ 3). Os pares decorativos que ficam de fora
  estão nomeados em comentário, com valor e data.
- **"Dynamic Type 130% sem overflow nas telas-modelo"**: conferido no
  simulador, em `extra-extra-extra-large` (≈135%). O saldo em display encolhe
  para caber, textos quebram linha e os valores continuam dentro do card.

## O que foi cortado, e por quê

- **Os 4 gráficos da seção H** (barras, donut, linha, ritmo). A seção K não os
  lista no escopo da Fase 2, e eles só ganham sentido junto das telas que os
  usam (Fases 5, 7 e 8). Não há código de gráfico no app.
- **A paleta de categorias** (as 15 cores-semente de `db/categories.py`). Nada
  na Fase 2 a usa, e a cor da categoria deve vir do servidor, que é a fonte.
  Copiá-la agora criaria uma segunda verdade.
- **`@gorhom/bottom-sheet`, reanimated, gesture-handler e worklets.** A seção G
  do plano os previa; você decidiu pelo Sheet nativo do expo-router, e nenhum
  deles entrou. O `motion.ts` usa o `Animated` do próprio RN.
- **`expo-image`**: o `Image` do RN lê webp, que é o formato dos stickers.

## Decisões suas, tomadas durante a fase

1. **Sheet nativo do expo-router** (`presentation: "formSheet"`), sem
   dependência nova.
2. **Botão primário**: `#C7186B` com texto branco no claro, `#FF4FA0` com
   texto escuro no escuro. Branco sobre `#FF2D8E` dá 3,49:1 e reprova o AA.
3. **AmountInput preenche pela direita**, só dígito ASCII, teto de
   R$ 99.999.999,99.
4. **Valor oculto é "R$ ••••"**, sem nenhum dígito na árvore nem no leitor de
   tela.
5. **Colar substitui** o valor do campo.
6. **Só substitui se o trecho colado tiver símbolo.** Trecho só com dígitos
   acumula, o que protege a digitação rápida que chega em lote. Custo aceito:
   colar "50" puro sobre R$ 1,23 dá R$ 123,50.
7. **Entrada e saída usam o módulo com o sinal do tipo**; negativo é
   "−R$ 12,30" (U+2212), divergindo do "R$ -12,30" do site.
8. **Centavos da entrada em display ficam todos em verde.**

## Quais skills de design orientaram o quê

- **`pigbank-frontend`** (obrigatória, e citada em cada chamada de Coder):
  rosa só onde há decisão — por isso o `Banner` usa `ink`/`warning`/`danger` e
  nunca `brand`, o `Avatar` é neutro e o `ghost` leva rosa só no texto; verde
  só para entrada e sucesso; sem ícone dentro de quadrado colorido.
- **`impeccable`** (modo Operate, referência de iOS): toque de 44 pt em todo
  interativo, os estados default/pressed/disabled/loading/error do `Button`,
  elevação declarada uma vez só no `Card`, skeleton com a forma real, estado
  vazio que ensina. Conflito conhecido e resolvido a favor do seu brief: o
  `ios.md` dela pede SF Pro e SF Symbols; o plano fixou Inter e Phosphor.
- **`emil-design-eng`**: press scale 0,97 em 150 ms com ease-out, nada nascendo
  de `scale(0)`, só transform e opacity, toast entrando e saindo pelo mesmo
  eixo com saída mais rápida, e "reduzir movimento" tirando o deslocamento mas
  mantendo a opacidade.
- **`imagegen-frontend-mobile`**: **não** foi usada como gerador. Esta sessão
  não tem ferramenta de geração de imagem, então ela entrou só como direção
  escrita. Se a Fase 3 tiver o gerador, vale usá-la antes de desenhar as telas
  de autenticação.

## O que depende de você

1. **Conferir no seu aparelho.** Tudo que eu verifiquei foi no simulador com o
   Expo Go. Ficaram sem prova: haptic real, VoiceOver lendo os rótulos,
   Android inteiro, "reduzir movimento" de verdade e o build nativo — que não
   compila nesta máquina com o Xcode 26.3 (`expo-modules-jsi` 57.x e
   `SWIFT_RETURNS_RETAINED`).
2. **Decidir sobre o `SegmentedControl` com rótulos repetidos.** Hoje o rótulo
   é a identidade da opção, e dois rótulos iguais deixam o segundo
   inalcançável. Está documentado na prop. Se algum dia a Fase 5 precisar de
   rótulos repetidos, a API muda para índice — e aí é churn em todos os
   chamadores.

## Como conferir no aparelho

Hoje **não há caminho pronto para o iPhone**. O Expo Go no iPhone pela rede
local foi descartado pelo dono, e o build nativo (`npx expo run:ios`) **não
compila nesta máquina** com o Xcode 26.3. As saídas reais são o EAS Build na
nuvem ou um Xcode 16.x instalado ao lado, com o `xcode-select` apontando para
ele.

O caminho que funciona é o **Expo Go no simulador iOS**, usado em toda a
verificação visual desta fase:

```bash
cd app && npx expo start --go --port 8081
```

```bash
xcrun simctl openurl booted "exp://127.0.0.1:8081/--/_ds"
```

O catálogo só existe em desenvolvimento, que é exatamente este modo. As
telas-modelo estão em `/_ds/inicio`, `/_ds/lista` e `/_ds/formulario`. O
`xcrun simctl` precisa rodar fora do sandbox do agente, senão dá
`CoreSimulatorService connection became invalid`.

O simulador **não prova** nenhum dos itens abaixo. O que **só o aparelho**
prova, e que eu não pude verificar:

1. **Haptic.** Toque nos chips e no SegmentedControl da seção "Controles":
   deve haver o toque leve de seleção. Em `/_ds/formulario`, deixe o campo
   "Descrição" vazio e tire o foco dele: o erro aparece e deve vir com a
   vibração de aviso. Navegar entre telas **não** pode vibrar.
2. **VoiceOver** (Ajustes → Acessibilidade → VoiceOver). Em `/_ds/inicio`,
   cada linha de lançamento deve ser lida como uma coisa só, por exemplo
   "Mercado São Luiz, Mercado, hoje 14:32, menos 189 reais e 90 centavos". No
   valor oculto do catálogo, o leitor deve dizer "Valor oculto" e **nenhum
   dígito**. No card de conexão, ele não pode repetir "Atualizando" duas vezes.
3. **Reduzir movimento** (Ajustes → Acessibilidade → Movimento). Com ele
   ligado, o toast deve aparecer sem deslizar, só surgindo; o toque nos botões
   deve mudar só a opacidade, sem encolher; e o pulso do Skeleton deve parar.
4. **Android.** Nada foi rodado em Android nesta fase. O que mais merece olho:
   o peso da Inter (família custom com `fontWeight` cai na fonte do sistema),
   a cor do indicador de puxar-para-atualizar (lá quem vale é `colors`, não
   `tintColor`) e o teclado numérico do `AmountInput`, que no Android manda
   vírgula e ponto.
5. **Campo de valor com o teclado do aparelho.** Em `/_ds/formulario`, digite
   rápido e cole um valor copiado de outra conversa: digitar acumula pela
   direita, colar com símbolo substitui, e colar texto sem dígito não muda
   nada.

## Armadilhas novas desta máquina (custaram tempo aqui)

1. **`expo start` com `CI=1` desliga o watch e o reload.** O app fica com o
   bundle antigo e você "conserta" o que já estava certo. Suba o Metro assim:
   `cd app && REACT_NATIVE_PACKAGER_HOSTNAME=127.0.0.1 npx expo start --go --port 8081`.
2. **`expo start` reescreve `app/.gitignore`** acrescentando um bloco do
   expo-cli e gera `app/expo-env.d.ts`. Reverta antes de commitar — aconteceu
   duas vezes nesta sessão.
3. **`node_modules/` com barra no `.gitignore` não barra symlink** com esse
   nome. Um symlink meu entrou num commit e só o Codex pegou. O `app/.gitignore`
   agora tem `node_modules` sem barra.
4. **O display do simulador congela.** Capturas ficam idênticas e o toque não
   responde, e dá para perder muito tempo antes de notar. `xcrun simctl
   shutdown` seguido de `boot` resolve. O relógio da barra de status fica
   parado de propósito, então ele não serve de sinal.
5. **Captura do `simctl` leva alguns segundos.** Qualquer coisa que dura 3 s
   (o toast) passa entre duas capturas. Para provar, aumente a duração numa
   sonda temporária e remova depois — foi assim que o toast ficou provado.
6. **`renderNosDoisTemas` + `fireEvent` não dispara nada** e não acusa erro: a
   RNTL só entrega evento para a última árvore montada, e o helper monta duas.
   Use `renderInterativo` em teste que interage.
7. **O `.stop()` do `Animated` do RN chama o callback** com `{finished:false}`,
   de forma síncrona. Um dublê que só descarta a chamada deixa teste verde
   sobre código que quebraria no aparelho.
8. **Iniciar animação dentro de um updater de `setState`** não funciona com
   driver nativo: a view ainda não existe, e o valor fica preso. Dispare no
   efeito, depois do commit.
9. **Cópias com sufixo " 2" aparecem e o git não as mostra.** Alguma
   ferramenta (ou um `cp` de agente) criou 24 duplicatas — stickers, telas,
   testes e snapshots. O `.gitignore` do template do Expo tem os padrões
   `* [0-9]` e `* [0-9].*`, então elas somem do `git status` e só quebram o
   lint e o teste que lê o diretório. Varra com
   `find . -path ./node_modules -prune -o -name "* [0-9].*" -print`.
10. **Postgres local não sobe no sandbox.** O teste Python do espelho roda com
   `--noconftest` (é teste de filesystem) ou fora do sandbox.

## Onde a Fase 3 começa

A Fase 3 é **autenticação e sessão**: telas 1–4 do plano, Google nativo, MFA
com códigos de backup, biometria, sessões, refresh silencioso, "sessão
expirada" e logout com limpeza total. Dependências: Fases 1 e 2. Pronto quando
os seis caminhos passarem em Maestro no staging.

O que a Fase 2 deixa pronto para ela:

- `Screen`, `Card`, `Button`, `Input`, `Banner`, `Toast`, `EmptyState` e
  `ConnectionStatus` já resolvem a casca das telas de entrada e de erro.
- O `AmountInput` **não** serve para código de MFA: ele é campo de dinheiro.
  Um campo de código é componente novo, e o `Input` é a base dele.
- A tela provisória `app/app/index.tsx` é a primeira coisa a sair quando a tela
  de entrada de verdade nascer. A lógica dela (`src/ui/inicio.ts`) tem a fila
  de ações e as corridas já resolvidas: vale ler antes de reescrever.
- Continua aberta a issue #458 (logout esperando a revogação com tempo limite),
  que é da Fase 3 por natureza.

Os PRs da Fase 2 já estão todos na `main`. Antes de começar, confirme com o
dono que a Fase 3 abre; a conferência no aparelho continua pendente e depende
de um build que rode no iPhone (seção "Como conferir no aparelho").
