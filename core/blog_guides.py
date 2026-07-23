"""
core/blog_guides.py — Conteúdo dos guias/dicas evergreen do blog (/blog/{slug}).

Fonte única (mesma ideia do commands_catalog): a lista GUIDES alimenta tanto os
cards da página /changelog quanto a página de cada artigo. Conteúdo ORIGINAL do
PigBank (não é notícia de terceiros) — por isso é hospedado aqui, sem link-out.

Cada guia:
  slug        — parte da URL /blog/<slug> (kebab-case, estável; não renomear)
  title       — título do artigo
  category    — etiqueta curta (Guia, Dica, Cartões, ...)
  emoji       — thumb do card
  read_time   — "4 min"
  description — meta description (SEO) e subtítulo
  body        — corpo em HTML (h2/p/ul) — conteúdo confiável, escrito por nós
"""
from __future__ import annotations

GUIDES: list[dict] = [
    {
        "slug": "registrar-gastos-whatsapp",
        "title": "Como registrar gastos falando com a Piggy no WhatsApp",
        "category": "Guia",
        "emoji": "💬",
        "sticker": "hello",
        "read_time": "4 min",
        "description": "Registrar um gasto no PigBank é só mandar mensagem pra Piggy no WhatsApp, do jeito que sair na cabeça. Veja como.",
        "body": """
<p>No PigBank você não preenche formulário nem abre planilha pra anotar um gasto.
Você <strong>conversa com a Piggy no WhatsApp</strong>, do jeito que a frase sair na
cabeça — ela entende, categoriza e guarda pra você.</p>

<h2>Mande a mensagem do seu jeito</h2>
<p>Não existe fórmula certa. Todas essas funcionam:</p>
<ul>
  <li>"gastei 30 no almoço"</li>
  <li>"45,90 uber ontem"</li>
  <li>"paguei 120 de farmácia"</li>
  <li>"recebi 2000 de salário"</li>
</ul>
<p>A Piggy identifica o valor, se é receita ou despesa, e chuta a categoria
(alimentação, transporte, saúde…). Se ela errar a categoria, é só corrigir — e
ela <strong>aprende</strong> a sua preferência pra da próxima vez já acertar.</p>

<h2>Ela confirma antes de salvar</h2>
<p>Pra você nunca registrar algo errado sem querer, a Piggy mostra o que entendeu
e espera seu ok antes de gravar. Anotou errado? Responde ali mesmo que ela ajusta.</p>

<h2>Tirou foto do cupom? Manda também</h2>
<p>Recebeu um comprovante de Pix, uma nota fiscal ou a foto de um cupom? Manda a
imagem que a Piggy lê os dados e monta o lançamento pra você conferir. (Leitura de
imagem faz parte do PigBank+.)</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Anote na hora que gasta — ainda no caixa ou dentro do Uber. Deixar pra depois é a receita pra esquecer, e o que não é anotado não aparece nos seus relatórios.</p></div></aside>

<h2>Comandos rápidos que vale conhecer</h2>
<ul>
  <li><strong>saldo</strong> — quanto você tem na conta agora</li>
  <li><strong>listar</strong> — seus últimos lançamentos</li>
  <li><strong>apagar</strong> — remove o último lançamento (ou um específico)</li>
</ul>
<p>E se quiser só conversar — "onde eu mais gastei esse mês?" — pode perguntar
normal. A Piggy responde com base nos seus próprios números.</p>

<p>A ideia é essa: quanto menos fricção pra anotar, mais você anota. E quanto mais
você anota, mais o PigBank te mostra pra onde seu dinheiro está indo.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>Registrar é só mandar mensagem pra Piggy, do jeito que a frase sair na cabeça.</span></div>
<div class="g-tk"><span class="c">2</span><span>Ela confirma antes de salvar — anotou errado, corrige ali mesmo.</span></div>
<div class="g-tk"><span class="c">3</span><span>Foto de cupom ou comprovante também vira lançamento (recurso do PigBank+).</span></div>
</div>
""",
    },
    {
        "slug": "caixinhas-guardar-dinheiro",
        "title": "Caixinhas: o método simples pra guardar dinheiro com objetivo",
        "category": "Dica",
        "emoji": "🐷",
        "sticker": "income",
        "read_time": "5 min",
        "description": "Guardar dinheiro fica mais fácil quando cada real tem um destino. É pra isso que servem as caixinhas do PigBank.",
        "body": """
<p>Guardar dinheiro "no geral" é difícil porque não tem cara. Guardar pra
<strong>uma viagem</strong>, pra <strong>reserva de emergência</strong> ou pro
<strong>notebook novo</strong> é bem mais fácil — porque você vê o objetivo
chegando. As caixinhas do PigBank existem justamente pra isso.</p>

<h2>O que é uma caixinha</h2>
<p>Pense num envelope com nome e meta. Você cria uma caixinha "Viagem", define
quanto quer juntar, e vai <strong>aportando</strong> aos poucos. O PigBank separa
esse valor e mostra o quanto você já caminhou até a meta.</p>

<div class="g-cardshot">
  <div class="g-cardshot-top"><span class="g-cardshot-ico">🐷</span><span class="g-cardshot-name">Reserva de emergência</span></div>
  <div class="g-cardshot-val">R$ 4.500,00</div>
  <div class="g-track"><div class="g-track-f" style="width:45%"></div></div>
  <div class="g-cardshot-sub">45% de R$ 10.000,00</div>
</div>

<h2>Como criar e usar</h2>
<ul>
  <li>No dashboard, clique em <strong>+ Nova</strong> na área de caixinhas.</li>
  <li>Dê um nome e (se quiser) uma meta em reais.</li>
  <li>Pra guardar, faça um aporte — pelo dashboard ou pedindo pra Piggy:
      "guarda 200 na viagem".</li>
</ul>
<p>Suas caixinhas se organizam sozinhas por saldo, então a que tem mais dinheiro
guardado aparece primeiro.</p>

<figure class="g-chart">
  <div class="g-chart-t">Guardando R$ 500 por mês</div>
  <div class="g-chart-s">Exemplo · saldo da caixinha ao longo de 12 meses</div>
  <svg viewBox="0 0 600 220" width="100%" role="img" aria-label="Reserva crescendo de zero a R$ 6.740 em 12 meses">
    <defs><linearGradient id="gc1" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#FF2D8E" stop-opacity=".42"/><stop offset="1" stop-color="#FF2D8E" stop-opacity="0"/></linearGradient></defs>
    <g stroke="rgba(255,255,255,.07)" stroke-width="1"><line x1="48" y1="30" x2="576" y2="30"/><line x1="48" y1="85" x2="576" y2="85"/><line x1="48" y1="140" x2="576" y2="140"/><line x1="48" y1="196" x2="576" y2="196"/></g>
    <path class="g-chart-area" d="M48,196 L136,171 L224,145 L312,118 L400,90 L488,61 L576,30 L576,196 Z" fill="url(#gc1)"/>
    <polyline class="g-chart-line" points="48,196 136,171 224,145 312,118 400,90 488,61 576,30" fill="none" stroke="#FF2D8E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    <circle class="g-chart-dot" cx="576" cy="30" r="5" fill="#C6F11A" stroke="#0c0c0d" stroke-width="2"/>
    <text class="g-chart-endlabel" x="560" y="22" text-anchor="end" fill="#C6F11A" font-size="13" font-weight="700">R$ 6.740</text>
    <g fill="rgba(255,255,255,.42)" font-size="11" text-anchor="middle"><text x="48" y="212">Mês 1</text><text x="312" y="212">Mês 6</text><text x="576" y="212">Mês 12</text></g>
  </svg>
  <figcaption>Cada aporte te aproxima da meta — e ver a linha subir é o que segura a disciplina.</figcaption>
</figure>

<h2>"Saldo" e "sobrou" não são a mesma coisa</h2>
<p>Vale entender essa diferença pra não se confundir:</p>
<ul>
  <li><strong>Saldo</strong> é o total que você tem na conta.</li>
  <li><strong>Sobrou</strong> é o que restou no mês depois de receitas menos
      despesas menos o que você guardou nas caixinhas.</li>
</ul>
<p>Ou seja: aportar numa caixinha não some com o seu dinheiro — ele continua seu,
só que reservado pra um objetivo.</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Guarde <strong>assim que o dinheiro entra</strong>, não com o que sobra no fim do mês. O que sobra pra guardar é sempre menor do que a gente imagina.</p></div></aside>

<h2>Uma regra importante</h2>
<p>Uma caixinha <strong>não pode ser apagada enquanto tiver saldo</strong>. É de
propósito: evita que você delete uma reserva e "perca" o dinheiro de vista. Quer
encerrar? Primeiro resgate o valor de volta pra conta, aí a caixinha some.</p>

<p>Comece com uma só — a clássica reserva de emergência — e sinta como é diferente
guardar quando cada real tem endereço.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>Cada caixinha é um objetivo com nome e meta — dá cara pro dinheiro.</span></div>
<div class="g-tk"><span class="c">2</span><span>Guarde assim que o dinheiro entra, não com o que sobra.</span></div>
<div class="g-tk"><span class="c">3</span><span>Uma caixinha não some enquanto tem saldo — primeiro resgata, depois encerra.</span></div>
</div>
""",
    },
    {
        "slug": "fatura-cartao-sem-surpresa",
        "title": "Nunca mais seja pego de surpresa pela fatura do cartão",
        "category": "Cartões",
        "emoji": "💳",
        "sticker": "expense-alert",
        "read_time": "6 min",
        "description": "A fatura do cartão só assusta quem não acompanha. Veja como o PigBank te mostra o valor crescendo em tempo real.",
        "body": """
<p>Aquele susto quando a fatura fecha acontece por um motivo só: a gente perde a
conta das comprinhas no meio do mês. No PigBank a fatura <strong>cresce à vista</strong>,
então quando ela fecha não tem surpresa — você já sabia o número.</p>

<h2>Cadastre seu cartão uma vez</h2>
<p>Informe o nome do cartão, o <strong>dia de fechamento</strong> e o <strong>dia
de vencimento</strong>. Pronto: o PigBank passa a montar a fatura de cada período
automaticamente.</p>

<h2>Toda compra entra na fatura aberta</h2>
<p>Registrou uma compra no crédito — pelo WhatsApp ou pelo dashboard — e ela cai na
fatura do período atual. A qualquer momento você vê o <strong>total parcial</strong>
da fatura que ainda vai fechar. Nada de esperar o e-mail do banco pra descobrir.</p>

<h2>Parcelou? O PigBank entende</h2>
<p>Comprou em "12x de 79,90"? É só dizer isso. O PigBank distribui as parcelas nas
faturas dos próximos meses, com a identificação de qual parcela é qual (1/12,
2/12…), pra você saber exatamente quanto do cartão já está comprometido lá na frente.</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Pagou só o mínimo? O resto vira <strong>crédito rotativo</strong> — os juros mais caros que existem. Acompanhar a fatura durante o mês é o que te ajuda a pagar tudo e fugir dessa bola de neve.</p></div></aside>

<h2>Lembretes pra não perder o vencimento</h2>
<p>Dá pra ligar lembretes por cartão, avisando alguns dias antes do vencimento.
Assim você paga em dia e não paga juros à toa.</p>

<div class="g-vs">
  <div class="g-vs-col bad"><div class="g-vs-h">😬 Sem acompanhar</div>
    <div class="g-vs-li"><span class="m">✕</span>Fatura vira surpresa no fim do mês</div>
    <div class="g-vs-li"><span class="m">✕</span>Descobre o valor tarde demais</div>
    <div class="g-vs-li"><span class="m">✕</span>Parcela some no meio das outras</div></div>
  <div class="g-vs-col good"><div class="g-vs-h">🐷 Com o PigBank</div>
    <div class="g-vs-li"><span class="m">✓</span>Vê a fatura crescendo em tempo real</div>
    <div class="g-vs-li"><span class="m">✓</span>Lembrete antes do vencimento</div>
    <div class="g-vs-li"><span class="m">✓</span>Sabe quanto já está comprometido</div></div>
</div>

<h2>O resultado</h2>
<p>Quando você acompanha a fatura enquanto ela cresce, duas coisas mudam: some o
susto no fim do mês, e você começa a segurar a mão <em>antes</em> de estourar — que
é o que realmente protege o seu bolso.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>A fatura cresce à vista no PigBank — sem susto quando ela fecha.</span></div>
<div class="g-tk"><span class="c">2</span><span>Parcelamento entra distribuído nos próximos meses, cada parcela identificada.</span></div>
<div class="g-tk"><span class="c">3</span><span>Lembrete antes do vencimento pra você nunca pagar juros à toa.</span></div>
</div>
""",
    },
    {
        "slug": "relatorios-dashboard",
        "title": "5 relatórios do dashboard que mostram pra onde seu dinheiro vai",
        "category": "Organização",
        "emoji": "📊",
        "sticker": "report",
        "read_time": "5 min",
        "description": "Registrar é metade do caminho. A outra metade é olhar. Conheça 5 visões do dashboard que revelam seus padrões.",
        "body": """
<p>Anotar os gastos é metade do trabalho. A outra metade é <strong>olhar</strong> —
e é aí que o PigBank vira um raio-x do seu dinheiro. Na área de Análises do
dashboard, essas cinco visões contam o essencial:</p>

<h2>1. Evolução no tempo</h2>
<p>Suas receitas e despesas mês a mês (ou nos últimos dias). Serve pra enxergar
tendência: você está gastando mais do que ganha? Melhorando ou piorando?</p>

<h2>2. Gastos por categoria</h2>
<p>Onde o dinheiro realmente vai: alimentação, transporte, assinaturas, lazer… É a
visão que mais surpreende — quase sempre tem uma categoria maior do que você
imaginava.</p>

<div class="g-chart-t" style="margin:24px 0 16px">Gasto médio de um mês (exemplo)</div>
<div class="g-bars">
  <div class="g-bar top"><span class="g-bar-l">Moradia</span><span class="g-bar-track"><span class="g-bar-f" style="width:28%"></span></span><span class="g-bar-v">28%</span></div>
  <div class="g-bar"><span class="g-bar-l">Alimentação</span><span class="g-bar-track"><span class="g-bar-f" style="width:22%"></span></span><span class="g-bar-v">22%</span></div>
  <div class="g-bar"><span class="g-bar-l">Transporte</span><span class="g-bar-track"><span class="g-bar-f" style="width:14%"></span></span><span class="g-bar-v">14%</span></div>
  <div class="g-bar"><span class="g-bar-l">Lazer</span><span class="g-bar-track"><span class="g-bar-f" style="width:11%"></span></span><span class="g-bar-v">11%</span></div>
  <div class="g-bar"><span class="g-bar-l">Assinaturas</span><span class="g-bar-track"><span class="g-bar-f" style="width:8%"></span></span><span class="g-bar-v">8%</span></div>
  <div class="g-bar"><span class="g-bar-l">Outros</span><span class="g-bar-track"><span class="g-bar-f" style="width:17%"></span></span><span class="g-bar-v">17%</span></div>
</div>

<h2>3. Padrão por dia da semana</h2>
<p>Você gasta mais na sexta? No fim de semana? Esse recorte mostra os dias em que a
mão pesa, o que ajuda a criar pequenas regras ("sexta é dia de segurar").</p>

<h2>4. Onde você mais compra</h2>
<p>Um ranking dos lugares/estabelecimentos que mais aparecem nos seus lançamentos.
Ótimo pra achar assinaturas esquecidas e gastos repetidos que passam batido.</p>

<h2>5. Números-chave do mês</h2>
<p>Um resumo direto: quanto entrou, quanto saiu, quanto <strong>sobrou</strong> e o
que você guardou. É o "como foi o mês" em uma olhada.</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Olhou o relatório e uma categoria te surpreendeu? Esse é o ouro. <strong>Ataque a surpresa</strong> — quase sempre é onde tem gordura pra cortar sem dor.</p></div></aside>

<h2>Como usar de verdade</h2>
<p>Não precisa virar analista. Escolha <strong>uma</strong> visão por mês pra
investigar e uma atitude pra tomar em cima dela. Pequenos ajustes, repetidos,
mudam o ano inteiro.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>Os relatórios transformam lançamentos em padrões que dá pra agir.</span></div>
<div class="g-tk"><span class="c">2</span><span>A categoria que mais surpreende costuma ser a maior alavanca.</span></div>
<div class="g-tk"><span class="c">3</span><span>Uma visão pra investigar + uma atitude por mês já muda o ano.</span></div>
</div>
""",
    },
    {
        "slug": "metas-realistas",
        "title": "Como definir metas realistas e realmente alcançá-las",
        "category": "Metas",
        "emoji": "🎯",
        "sticker": "goal",
        "read_time": "4 min",
        "description": "Meta que não cabe no orçamento vira frustração. Veja como definir objetivos que você de fato consegue cumprir.",
        "body": """
<p>A maioria das metas falha não por falta de vontade, mas por serem grandes demais
pra realidade do mês. A boa notícia: dá pra ajustar isso do jeito certo desde o
começo.</p>

<h2>1. Parta do que sobra, não do sonho</h2>
<p>Olhe no dashboard quanto costuma <strong>sobrar</strong> no fim do mês (receitas
menos despesas menos o que você já guarda). A sua meta mensal precisa caber
confortavelmente aí dentro. Meta que só funciona "num mês perfeito" quebra no
primeiro imprevisto.</p>

<h2>2. Quebre o grande em mensal</h2>
<p>Quer juntar R$ 6.000 numa reserva em um ano? Isso é R$ 500 por mês. Pensar na
parcela mensal deixa o objetivo tangível — e mostra na hora se ele é realista ou
se precisa de mais prazo.</p>

<div class="g-stats">
  <div class="g-stat"><span class="n">R$ 500</span><span class="l">guardados por mês</span></div>
  <div class="g-arrow">→</div>
  <div class="g-stat"><span class="n">R$ 6 mil</span><span class="l">em 1 ano</span></div>
  <div class="g-arrow">→</div>
  <div class="g-stat win"><span class="n">R$ 34 mil</span><span class="l">em 5 anos rendendo</span></div>
</div>

<h2>3. Transforme a meta numa caixinha</h2>
<p>Crie uma caixinha com o nome do objetivo e a meta em reais. Cada aporte te
mostra a barrinha andando — e ver o progresso é o que mantém você no jogo. Se
quiser, peça pra Piggy guardar assim que o salário cair.</p>

<div class="g-goal">
  <div class="g-goal-top"><span class="g-goal-name">✈️ Viagem pro Chile</span><span class="g-goal-pct">45%</span></div>
  <div class="g-track"><div class="g-track-f" style="width:45%"></div></div>
  <div class="g-goal-nums"><span>Guardado <b>R$ 2.700</b></span><span>Meta <b>R$ 6.000</b></span></div>
</div>

<h2>4. Comemore os marcos</h2>
<p>Bateu 25%, metade, 75%? Reconheça. Meta longa sem marco pelo caminho cansa;
com marcos, cada etapa vira uma pequena vitória.</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Meta que só funciona "num mês perfeito" quebra no primeiro imprevisto. Melhor uma <strong>meta menor que você cumpre</strong> do que uma enorme que você abandona.</p></div></aside>

<h2>5. Reavalie sem culpa</h2>
<p>Mudou de vida, apertou o mês? Ajuste o valor ou o prazo. Uma meta é uma
ferramenta a seu favor, não uma prova — reavaliar no meio do caminho não é
fracassar.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>A meta mensal precisa caber no que sobra — não no sonho.</span></div>
<div class="g-tk"><span class="c">2</span><span>Quebre o objetivo grande em parcela mensal; vira tangível.</span></div>
<div class="g-tk"><span class="c">3</span><span>Meta longa sem marco cansa — comemore 25%, 50%, 75%.</span></div>
</div>
""",
    },
    {
        "slug": "seguranca-dados",
        "title": "Como o PigBank protege seus dados financeiros",
        "category": "Segurança",
        "emoji": "🔒",
        "sticker": "approved",
        "read_time": "3 min",
        "description": "Seu dinheiro é assunto sério. Veja as camadas de segurança que protegem seus dados no PigBank.",
        "body": """
<p>Falar de dinheiro exige confiança. Por isso a segurança dos seus dados é levada
a sério no PigBank, em várias camadas — não como enfeite, mas por padrão.</p>

<h2>Seus dados sensíveis são cifrados</h2>
<p>Informações pessoais como e-mail e telefone ficam <strong>criptografadas</strong>
no banco de dados. Mesmo internamente, esses dados não trafegam em texto puro —
seguem a lógica de minimizar quem consegue enxergar o quê, em linha com a LGPD.</p>

<aside class="g-callout"><div class="g-callout-ico">🐷</div><div><span class="g-callout-k">Dica da Piggy</span><p>Ative a <strong>verificação em duas etapas</strong> hoje — leva 2 minutos e é a barreira que segura sua conta mesmo se sua senha vazar em algum outro site.</p></div></aside>

<h2>Verificação em duas etapas (2FA)</h2>
<p>Você pode ativar a autenticação em dois fatores na sua conta. Com ela ligada,
além da senha é preciso um código temporário do seu aplicativo autenticador pra
entrar — uma barreira e tanto contra acesso indevido.</p>

<h2>Acompanhe a atividade da conta</h2>
<p>Nas configurações de segurança você vê os últimos acessos: quando, de onde. E se
um login vier de um lugar novo, o PigBank te avisa por e-mail — pra você reagir na
hora se não tiver sido você.</p>

<h2>Os dados são seus</h2>
<p>Você pode <strong>exportar</strong> tudo o que registrou e também
<strong>excluir</strong> sua conta e seus dados quando quiser. Sem pegadinha:
transparência faz parte da proposta.</p>

<div class="g-takeaways"><h4>🐷 Resumindo</h4>
<div class="g-tk"><span class="c">1</span><span>Dados sensíveis (e-mail, telefone) ficam criptografados no banco.</span></div>
<div class="g-tk"><span class="c">2</span><span>Ative o 2FA — a proteção que sobrevive a um vazamento de senha.</span></div>
<div class="g-tk"><span class="c">3</span><span>Os dados são seus: dá pra exportar tudo e excluir a conta quando quiser.</span></div>
</div>

<p>Ficou com alguma dúvida de segurança? Fale com a gente em
<strong>suporte@pigbankai.com</strong>.</p>
""",
    },
]

_BY_SLUG = {g["slug"]: g for g in GUIDES}


def list_guides() -> list[dict]:
    """Todos os guias, na ordem de exibição."""
    return GUIDES


def get_guide(slug: str) -> dict | None:
    """Um guia pelo slug, ou None se não existir."""
    return _BY_SLUG.get(slug)


def other_guides(slug: str) -> list[dict]:
    """Os demais guias (pra seção 'Continue lendo')."""
    return [g for g in GUIDES if g["slug"] != slug]
