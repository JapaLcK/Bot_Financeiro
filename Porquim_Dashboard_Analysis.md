# Porquim IA Dashboard — Análise Detalhada para LLM

## Objetivo

Este documento descreve com precisão a interface visual e funcional do dashboard financeiro `dash.oporquim.com.br`, para que uma LLM possa compreender os padrões de UI/UX e aplicar melhorias equivalentes em outro dashboard.

---

## 1. Tema Visual Geral

O dashboard utiliza um tema **claro (light mode)**. Fundo predominantemente **branco puro** (`#ffffff`), com elementos de suporte em cinza muito claro. Não há modo escuro ativo. O contraste é feito com texto escuro sobre fundo branco, seguindo padrão corporativo clean e minimalista.

- **Fundo da página**: branco (`#ffffff`)
- **Fundo dos cards/painéis**: branco, com sombra leve (`box-shadow: 0 1px 3px rgba(0,0,0,0.08)`) e borda cinza clara (`#e5e7eb`)
- **Sidebar**: fundo branco ou cinza muito claro (`#f9fafb`), com texto escuro
- **Texto principal**: preto/cinza escuro (`#111827` ou `#1f2937`)
- **Texto secundário/labels**: cinza médio (`#6b7280`)
- **Linhas divisórias**: cinza claro (`#e5e7eb`)

---

## 2. Estrutura de Layout

### Sidebar (menu lateral esquerdo)
- Fundo **branco/cinza muito claro**, ocupando toda a altura da tela
- Largura fixa (aprox. 220–240px)
- Logo "Porquim" no topo
- Itens de menu com ícones à esquerda e label de texto à direita
- Item ativo destacado com fundo levemente colorido ou sublinhado
- Texto dos itens: escuro, sem maiúsculas forçadas
- Separadores entre grupos de menu com linha fina cinza

### Área de conteúdo principal
- Ocupa o restante da largura após a sidebar
- Padding interno consistente (aprox. 24–32px)
- Header superior com título da seção atual + ações contextuais (ex: botão "Registrar Gasto")
- Grade de cards abaixo do header

---

## 3. Botões

### Botão primário — "Registrar Gasto"
- Cor de fundo: **vermelho coral/salmão** (`#ef4444` ou `#f87171`)
- Texto: branco
- Border-radius arredondado (aprox. 6–8px)
- Tamanho médio, padding generoso
- Posicionado no canto superior direito da área de conteúdo
- Sem outline, sem sombra excessiva

### Botões secundários
- Fundo branco com borda cinza
- Texto escuro
- Mesmo border-radius do botão primário

---

## 4. Cards de Métricas (KPI Cards)

Cada card exibe um indicador financeiro resumido. Estrutura interna:

- **Fundo**: branco com sombra sutil
- **Borda**: cinza claro, 1px
- **Border-radius**: 8–12px
- **Ícone**: pequeno, colorido (cada métrica tem uma cor própria — ex: verde para receita, vermelho para despesa, azul para saldo)
- **Label**: texto pequeno, cinza médio, acima do valor
- **Valor**: fonte grande e bold, cor escura (preto/cinza escuro); valores negativos em **vermelho** (`#ef4444`)
- **Variação percentual**: exibida abaixo ou ao lado do valor, com seta (↑ verde para positivo, ↓ vermelho para negativo)
- **Período de referência**: label pequeno em cinza, ex: "Este mês", "Últimos 30 dias"

---

## 5. Tabelas e Listas de Transações

- Fundo branco
- Header da tabela: fundo cinza muito claro (`#f3f4f6`), texto cinza escuro, letra pequena maiúscula (ou normal bold)
- Linhas alternadas ou separadas por linha `<hr>` cinza claro
- Colunas típicas: Data, Descrição, Categoria, Valor
- Valores negativos (despesas): texto **vermelho** (`#ef4444`)
- Valores positivos (receitas): texto **verde** (`#16a34a` ou `#22c55e`)
- Ícones de categoria: pequenos, coloridos, à esquerda da descrição
- Hover na linha: fundo cinza muito claro (`#f9fafb`)

---

## 6. Gráficos e Visualizações

- Fundo dos containers: branco, com borda e sombra como os outros cards
- Gráfico de linha ou barra para evolução mensal de receitas/despesas
- Cores usadas: azul para receita, vermelho/coral para despesa, cinza para neutro
- Eixos com texto cinza claro
- Tooltip com fundo branco, borda leve, texto escuro
- Legenda abaixo ou ao lado do gráfico, com pequenos círculos coloridos

---

## 7. Filtros e Seletores de Período

- Dropdown ou botões de período (Semana, Mês, Ano, Personalizado)
- Fundo branco, borda cinza, texto escuro
- Item selecionado: fundo levemente colorido (cinza claro ou cor primária suave)
- Calendário de data: modal centralizado, fundo branco, dias em grid, dia selecionado em vermelho coral

---

## 8. Tipografia

- Fonte principal: sans-serif moderna (provavelmente Inter, Poppins, ou similar)
- Hierarquia:
  - Títulos de seção: 18–22px, bold, `#111827`
  - Labels de KPI: 12–13px, regular, `#6b7280`
  - Valores numéricos: 24–28px, bold, `#111827`
  - Texto de tabela: 13–14px, regular, `#374151`
  - Texto secundário: 12px, `#9ca3af`

---

## 9. Paleta de Cores Resumida

| Elemento                  | Cor                        | Hex aproximado  |
|---------------------------|----------------------------|-----------------|
| Fundo da página           | Branco                     | `#ffffff`       |
| Fundo dos cards           | Branco com sombra          | `#ffffff`       |
| Sidebar                   | Branco / cinza muito claro | `#f9fafb`       |
| Texto principal           | Preto/cinza escuro         | `#111827`       |
| Texto secundário          | Cinza médio                | `#6b7280`       |
| Bordas                    | Cinza claro                | `#e5e7eb`       |
| Botão primário            | Vermelho coral/salmão      | `#ef4444`       |
| Valores negativos         | Vermelho                   | `#ef4444`       |
| Valores positivos         | Verde                      | `#22c55e`       |
| Indicadores de receita    | Azul                       | `#3b82f6`       |
| Hover em linha de tabela  | Cinza muito claro          | `#f9fafb`       |

---

## 10. Padrões de Interação

- Sidebar colapsável em telas menores
- Tooltips ao hover nos gráficos
- Modal para registro de novo gasto (formulário com campos: valor, categoria, data, descrição)
- Feedback visual imediato ao salvar (toast/notificação no canto superior direito)
- Filtros de período atualizam todos os cards e gráficos simultaneamente (sem recarregar a página)

---

## 11. Observações para Aplicar Melhorias

Ao replicar ou inspirar-se neste dashboard, priorizar:

1. **Consistência do tema claro**: nunca misturar elementos escuros sem propósito claro
2. **Hierarquia visual nos KPIs**: ícone + label pequeno + valor grande + variação percentual
3. **Cor como semântica financeira**: vermelho = despesa/negativo, verde = receita/positivo
4. **Cards com sombra leve**, não com borda pesada — transmite leveza
5. **Botão de ação principal em destaque** (coral/vermelho) para criar novo registro
6. **Tabelas sem zebra striping pesado**, apenas hover sutil
7. **Gráficos com fundo branco** integrados aos cards, sem container separado de cor diferente
