---
version: 1
slug: "app-home-nativa"
primary_target: "app/app/(app)/(painel)/resumo.tsx"
related_targets:
  - "app/src/features/painel/"
  - "app/app/(app)/(painel)/_layout.tsx"
---

# Surface brief: Home nativa PigBank

Extensão iOS do mundo PigBank existente, modo **Operate**: compreender o mês e abrir a próxima ação. Registro da implementação em 2026-10-07; não substitui `PRODUCT.md`, `DESIGN.md`, `.impeccable/design.json` nem a superfície V2 web. A direção e a composição do mockup foram aprovadas; **a revisão visual independente liberou o recorte normal, fonte ampliada ao vivo e abertura com fonte ampliada em 25 capturas de simulador**.

## Direction contract

**Autoridade:** direção registrada em `/private/tmp/home-nativa-direcao.md`, mockup em `/Users/lucaskuramoti/.codex/visualizations/2026/10/05/01a10a07-f075-79b2-ac3f-719ca2b4c7f7/mockup-home-v2` e screenshot fornecido pelo usuário em `/var/folders/w2/rxp22qds1vj4rv0tt_b2jlq40000gn/T/codex-clipboard-39d815c7-643b-4e87-ba94-8551bcb08617.png`. São referências da composição aprovada, não evidência de aprovação do app executado.

**Primeira vista:** marca à esquerda, privacidade e conta à direita; saudação, título mensal e seletor de mês; perfil e Organizar; convite à conversa com Piggy; Contas antes dos demais blocos no preset. Base preta/grafite, Inter, rosa na ação e seleção. Conteúdo em rolagem vertical, cinco abas inferiores: Resumo, Gastos, Piggy, Metas, Extrato. Fonte: `app/src/features/painel/cabecalho.tsx`, `catalogo.ts`, `telas.tsx` e `app/app/(app)/(painel)/_layout.tsx`.

**Comportamento:** puxar para atualizar, abrir detalhes em folha nativa, entrar em categoria/dia/lançamento, conversar com Piggy e alcançar Bancos conectados, Configurações e Segurança. Produto em pt-BR/BRL; datas civis em dd/mm/aaaa. Mês inicial calculado no fuso America/Sao_Paulo, sem deslocar datas civis pelo fuso do aparelho (`catalogo.ts`).

## Colors

Fonte normativa nativa: `app/src/ui/tokens.ts`, paleta `escuro`. O painel força tema escuro em seu próprio layout. Fundo `bg` (#0E0E10), conteúdo `surface` (#17171A), superfície elevada `surfaceRaised` (#202024), contorno `border` (#2A2A2F); texto `ink` (#F2F2F3) e `inkMuted` (#9B9BA6). A marca original é #FF2D8E; o rosa legível do tema (`brand`, `brandInk`, `acao`) é #FF4FA0. Entradas usam `positive` (#47CD89) com sinal; saídas usam texto com menos, sem vermelho financeiro (`Money.tsx`). `warning` informa cobertura e precisão; `danger` indica erros.

## Typography

Inter local em quatro TTFs: Regular, Medium, SemiBold, Bold (`app/assets/fonts/`, carregamento em `app/app/_layout.tsx`). Escala nativa em pontos: display 40/44, título 28/34, seção 20/26, corpo 16/22, rótulo 14/18 e legenda 12/16. A família codifica o peso; números usam `tabular-nums` (`tokens.ts`, `Texto.tsx`, `Money.tsx`).

`TipografiaPainelProvider` acompanha `fontScale` por `useWindowDimensions`; acima de 1,1, as composições afetadas empilham conteúdo ou reduzem colunas. `TextoPainel`, `ButtonPainel` e `Valor` invalidam apenas a apresentação medida ao mudar a escala; formulário, seleção e dados permanecem no pai (`tipografia.tsx`, `base.tsx`). O componente global `Texto` limita a ampliação a 1,3. O valor display usa uma linha e ajuste mínimo de escala 0,6. Estes limites são fatos da implementação, não prova de suporte irrestrito a todos os tamanhos de acessibilidade.

## Layout

iPhone em retrato, `supportsTablet: false` (`app/app.config.ts`). `Screen` aplica safe areas nos quatro lados e margem horizontal de 16pt; cabeçalho e blocos usam intervalo de 20pt. A coluna se adapta ao conteúdo. `Acao`, privacidade, conta e abertura de detalhes têm alvo de pelo menos 44pt; conferir outros controles no acabamento. O navegador mantém cinco abas e só as exibe com acesso liberado, app ativo e desbloqueado.

## Elevation & Depth

Elevação por diferença de tom. O `Card` comum não tem sombra no tema escuro; folhas usam apresentação do sistema. Não transportar as sombras, o arraste ou as container queries do dashboard web para a Home (`Card.tsx`, `base.tsx`).

## Shapes

Grade nativa de 4pt; raios globais 8/12/20pt. Blocos usam raio 12pt e padding 16pt; avatar circular de 44pt; convite ao Piggy com raio e padding 12pt (`tokens.ts`, `Card.tsx`, `cabecalho.tsx`).

## Components

**Perfis e organização.** Seis perfis: Padrão, Economizar, Investir, Controlar gastos, Sair das dívidas e Autônomo. Seleção em `/api/app/perfil` GET/PUT; erro de gravação restaura o anterior. Ordem e visibilidade são locais, por usuário e perfil, em SecureStore (`pigbank.painel.v1.u{uid}.{perfil}`). Organizar oferece subir, descer, esconder, adicionar e restaurar o preset. Todos começam com Contas; o usuário pode mover ou ocultar esse bloco (`catalogo.ts`, `provider.tsx`, `cabecalho.tsx`).

**Catálogo.** 16 blocos: Contas; Saldo previsto; Resumo do mês; Para onde vai; Dia a dia; Simulador; Próximos 30 dias; Piggy notou; Metas e caixinhas; Patrimônio; Fatura do cartão; Onde está o dinheiro; Renda mês a mês; Rendimento contratado; Parcelas futuras; Assinaturas. O título de compromissos é estático, mas seu conteúdo informa o horizonte efetivo do servidor (`catalogo.ts`, `widgets.tsx`, `futuro.tsx`).

**Privacidade e estados.** Valores, curvas, distribuição, progresso de metas, taxa, mensagens e detalhes sensíveis respeitam a ocultação. A conversa fica desabilitada nesse estado. Ausente permanece ausente; vazio, carregamento, erro, limite e recurso negado têm texto próprio e retry quando aplicável. Nenhum fixture de teste deve ser importado pela aplicação (`base.tsx`, `patrimonio.tsx`, `extrato.tsx`, `conversa.tsx`).

**Movimento.** Pressão nativa de 150ms, escala 0,97, curva bezier (0,23, 1, 0,32, 1), `useNativeDriver`. Reduce Motion mantém feedback por opacidade 0,85. Folhas são `Modal` com `presentationStyle="pageSheet"`, slide normal e sem animação com Reduce Motion. Tokens gerais de 250/400ms não definem a duração do slide do sistema (`app/src/ui/motion.ts`, `base.tsx`).

## Contratos e verdade financeira

| Informação/ação | Fonte e significado obrigatório |
| --- | --- |
| Contas | `/api/app/contas`: disponibilidade atual, carteira manual, bancos, cobertura e atualização. Não representa saldo do mês. |
| Resumo e distribuição | `/api/app/resumo-do-mes?mes=AAAA-MM` e `/api/app/mes-detalhes?mes=AAAA-MM`: saldo mensal = Entrou − Saiu; guardado líquido = aportes − saques registrados. Categorias/dias excluem outras moedas e movimentos internos e podem divergir do total legado. Compra no cartão pode ter dia anterior ao mês da fatura. |
| Previsão | `/api/app/previsao`: horizonte permitido, marcos, trajetória quando existente, premissas e direção de incerteza. Parte de hoje, mesmo com outro mês selecionado; gastos variáveis só entram quando a cobertura declara. |
| Patrimônio | `/api/app/patrimonio`: cálculo oficial e fotos reais; não duplica caixinhas espelhadas. Sem fotos, histórico vazio. |
| Rendimento | `/api/app/rendimento`: taxa contratada, unidade e data informadas pelo banco; nunca lucro recebido. Taxa ausente permanece null. |
| Simulador | Exclusivo Pro no cliente, sobre categorias reais do mês. Cortes de 0–100%, em passos de 10; economia potencial reversível, sem alterar registros ou inventar trajetória (`gastos.tsx`, `widgets.tsx`). |
| Extrato | `/api/app/lancamentos`: mês, categoria, busca, origem, tipo, cursor; filtro de dia sobre páginas carregadas. Mostrar existência de mais páginas e abrir detalhes reais (`extrato.tsx`). |
| Piggy e demais recursos | `/ai/messages`, `/ai/chat`, `/insights/{uid}/current`, `/goals/{uid}/status`, `/cards/{uid}/summary`, `/installments/{uid}/list`, `/analytics/{uid}/evolution?months=6` e `/api/app/assinaturas`; preservar limites e contratos (`provider.tsx`, `services/painel.ts`). |

Dinheiro Decimal vira centavos por texto com HALF_UP; soma e porcentagem usam operações inteiras verificadas (`decimal.ts`, `api/schemas/painel.ts`). Diferenças de arredondamento por grupo são explicadas por `motivos`, sem redistribuir centavos. Cobertura, saldos ausentes e dados desatualizados devem continuar visíveis. Contratos completos: `docs/api-app-painel.md`.

## Acesso

Direção de produto: acesso pago; free sem app. O runtime confirma `app_access === true` no servidor, sem inferir direito apenas do nome do plano. A conexão inicial exige marco persistente de sincronização; o marco continua válido após remover o último banco. Sem confirmação de acesso ou de marco, nenhum conteúdo financeiro monta (`features/openFinance/acesso.ts`, `provider.tsx`).

`/api/app` é namespace nativo independente da coorte beta web, com dono obtido da sessão, assinatura e onboarding bancário no servidor (`api/nativo/app.py`, `api/v2/sessao.py`). Prefixo, User-Agent e headers não concedem acesso. Cliente Bearer usa `credentials: "omit"`; escrita com cookies mantém CSRF. Features e janela de histórico vêm do servidor. Foco/foreground revalidam acesso; cancelamento e geração descartam respostas antigas ou de outro usuário. SSE permanece web (`api/client.ts`, `provider.tsx`, `docs/api-app-painel.md`).

## Drift e limites do aceite

- `PRODUCT.md` ainda descreve web/protótipo, interação do simulador indecisa e ausência de free. A Home nativa e os gates atuais ampliam esse escopo; documento global preservado.
- `DESIGN.md` e `.impeccable/design.json` descrevem tokens, componentes HTML/CSS, quatro colunas, arraste, tamanhos e motion web. O app usa a fonte nativa acima; não há paridade literal declarada nem sidecar nativo gerado.
- A direção pede símbolo e wordmark oficiais. O código usa o símbolo oficial, porém desenha “PigBank” como texto Inter no cabeçalho. Categorias no anel seguem um ciclo de quatro cores, não as oito cores fixas por entidade do sistema web; curvas são sólidas de 3pt, e o calendário é lista de dias, sem heatmap (`cabecalho.tsx`, `base.tsx`, `gastos.tsx`). Registrar e avaliar na revisão, sem reparar neste documento.
- Revisão visual independente: `ship`, F1 resolvido, em 25 capturas do simulador iOS 27 (402×874): matriz normal, mudança ao vivo para accessibility-large e abertura com accessibility-large. O parecer confirma validade das capturas, fidelidade normal preservada e reflow dos textos e valores mostrados. Não confirma aparelho físico, VoiceOver, teclado/gestos, desempenho, todos os estados de erro/vazio, Reduce Motion em execução ou banco/LLM externos. Dados sintéticos para captura permanecem fora do código de produção e estão identificados na evidência.
