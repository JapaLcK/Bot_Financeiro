PigBank é um assistente financeiro que mora no WhatsApp: o usuário fala com o Piggy durante o dia e volta ao app e ao `/painel` para entender o que essa conversa soma. Público: 18 a 24 anos, cuidando do próprio dinheiro pela primeira vez, quase sempre no celular. O sistema tem três superfícies com a mesma identidade:

- **App** (Expo, claro e escuro): a fonte dos tokens, `app/src/ui/tokens.ts`. É o que este sistema espelha.
- **/painel** (web, escuro "grafite"): usa os mesmos valores do tema `dark`; ver `DESIGN.md` do repositório.
- **Marketing** (landing, e-mail, VSL): mais expressivo; usa também `neon`, `pink-support`, `black`, `paper` e `shadow-brand`, que **não entram no produto**.

## Fundamentos de conteúdo

- Idioma **pt-BR**, moeda BRL, data dd/mm. Sentence case em tudo: nenhum texto em caixa alta, nenhum rótulo com espaçamento largo.
- Tom: inteligente, jovem, amigável, um pouco irreverente e confiável. Divertido, não infantil. Frase curta, verbo no imperativo no botão ("Pagar fatura", "Tentar de novo", "Ver 90 dias").
- Fale como o usuário fala: ele manda "gastei 50 no mercado" e "paguei a luz". Evite jargão financeiro e frases de panfleto ("revolucione sua vida financeira").
- O **Piggy é masculino**: "pro Piggy", "o Piggy categoriza".
- Erro e estado vazio são desenhados, nunca disfarçados de sucesso, e dizem o que fazer: "Não deu para atualizar seu banco. Tente de novo."
- Dado de demonstração é sempre rotulado ("Dados de demonstração"). Nunca invente depoimento, número ou funcionalidade.
- Mínimo de texto no app: dado em gráfico ou forma; palavra só onde ela é o essencial.

## Fundamentos visuais

**Cor.** Base em branco, preto e neutros (`bg`, `surface`, `surface-control`, `surface-raised`, `ink`, `ink-muted`, `ink-faint`). Só há uma cor de marca, o rosa `brand` `#FF2D8E`, e ele marca **decisão e agora**: o botão primário (`action` com texto `on-action`), foco, seleção, o marcador de hoje. Não pinte superfícies inteiras de rosa. Texto rosa usa `brand-ink`, nunca `brand`. O verde `positive` significa **dinheiro ganho** e nada além, sempre com `+` ou palavra. Gasto é `ink` com sinal `−`: nunca rosa nem vermelho. `warning` marca gasto acima do ritmo; `danger`, erro. As cores `cat-*`, `serie-*` e `heat-*` são marcas de dado e não entram em botão, navegação nem fundo de painel.

**Tipografia.** Produto: Inter, uma família só (variável, self-hosted). A hierarquia vem de tamanho, peso (400, 500, 600) e espaçamento que aperta com o tamanho: `display` 40px com −0,035em, `title` 24px, `section` 20px, `body` 14px, `label` 13px, `caption` 12px. Todo número que muda ou se compara usa algarismos tabulares (`font-variant-numeric: tabular-nums`). Texto pequeno nunca tem tracking negativo. Marketing tem escala própria (grupo Marketing: `hero` 64px Black até `tiny` 12px).

**Espaço e forma.** Grade de 4pt (`space-xs` a `space-huge`). Botão, chip e controle segmentado são pílulas (`radius-pill`); card e banner usam `radius-block` (16px); input e lista agrupada, `radius-control`. Dentro de um card não há outro card: separe com hairline de `border`.

**Profundidade.** Sem sombra em repouso. Profundidade é o tom do plano (`surface`, `surface-control`, `surface-raised`) mais 1px de `border`. `shadow-raised` só existe no claro; `shadow-brand` só em marketing.

**Movimento.** Funcional e curto: 160ms para toque, 240ms para transição, 400ms para abrir folha, sempre `cubic-bezier(0.23, 1, 0.32, 1)` (nunca ease-in). Escala 0,97 ao pressionar. Só indicador ao vivo repete em loop. Respeite sempre `prefers-reduced-motion`.

**Estados e acessibilidade.** Alvo de toque mínimo 44px. Foco visível de 2px em `brand`, afastado 2px. Texto 4,5:1 nos dois temas (a tabela está nas notas de cada token). Cor nunca é o único sinal: ganho e perda levam sinal e palavra.

**Imagens.** Só arte oficial: o símbolo, o Piggy e os agentes. A arte aparece em boas-vindas, estados vazios, chat e marketing; no dashboard o número lê primeiro.

## Iconografia

Ícones **Phosphor regular** (MIT), sempre no peso regular, sem emoji decorativo. A cor é um tom de texto (`ink`, `ink-muted`, `ink-faint`) ou de estado (`positive`, `warning`, `danger`), nunca `brand` solto nem dentro de quadrado colorido.

- **App e componentes deste sistema:** `Icon`, com os ícones que o app usa (o tipo `NomeIcone` de `app/src/ui/componentes/Icone.tsx`), desenhados em SVG (24px padrão, 20px em botão e campo). A lista está em `PigBank.iconNames`.
- **Site e /painel:** a fonte `fonts/Phosphor.woff2` (`--font-icons`), um subset gerado a partir do que o frontend usa, a 15–18px. O `bundle.css` traz só três classes de exemplo (`pb-ph-wallet`, `pb-ph-piggy-bank`, `pb-ph-chat-circle`); o resto está no `frontend/phosphor.css` do repositório. Ícone fora do subset fica invisível: regere com `scripts/build_phosphor_subset.py`. Nem todo ícone do app está no subset web (em 2026-10-18 faltavam, entre outros, `CaretLeft`, `LinkBreak`, `Copy` e `GoogleLogo`; confira com `grep "ph-<nome>:before" frontend/phosphor.css` antes de usar).

## Faixas de uso dos componentes

- **Base:** `Text` (os seis estilos de tipo) e `Icon`.
- **Ação:** `Button` (um primário por tela), `Chip`, `SegmentedControl`.
- **Entrada:** `Input`, `AmountInput` (valor em centavos, digitado da direita).
- **Superfície e aviso:** `Card`, `InsightCard`, `Banner` (aviso que fica), `Toast` (aviso que passa), `EmptyState`, `ConnectionStatus` (Open Finance).
- **Lista:** `ListRow` dentro de `ListGroup`; `TransactionRow` para lançamento.
- **Dado:** `Money`, `Avatar`, `ProgressBar`, `Skeleton`, `CompositionBar`, `MonthlyBars`, `CumulativeCurve`.
- **Estrutura:** `Screen` e `Sheet`.

Valor sempre via `Money` (ou `AmountInput`) a partir de centavos inteiros, nunca float. Gráfico é escondido do leitor de tela: o número que ele mostra também aparece escrito.

As telas de acesso (login e cadastro) usam no app uma paleta própria, levemente azulada, para casar com a tampa biométrica nativa; ela não está neste sistema.
