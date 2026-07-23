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

<h2>Como criar e usar</h2>
<ul>
  <li>No dashboard, clique em <strong>+ Nova</strong> na área de caixinhas.</li>
  <li>Dê um nome e (se quiser) uma meta em reais.</li>
  <li>Pra guardar, faça um aporte — pelo dashboard ou pedindo pra Piggy:
      "guarda 200 na viagem".</li>
</ul>
<p>Suas caixinhas se organizam sozinhas por saldo, então a que tem mais dinheiro
guardado aparece primeiro.</p>

<h2>"Saldo" e "sobrou" não são a mesma coisa</h2>
<p>Vale entender essa diferença pra não se confundir:</p>
<ul>
  <li><strong>Saldo</strong> é o total que você tem na conta.</li>
  <li><strong>Sobrou</strong> é o que restou no mês depois de receitas menos
      despesas menos o que você guardou nas caixinhas.</li>
</ul>
<p>Ou seja: aportar numa caixinha não some com o seu dinheiro — ele continua seu,
só que reservado pra um objetivo.</p>

<h2>Uma regra importante</h2>
<p>Uma caixinha <strong>não pode ser apagada enquanto tiver saldo</strong>. É de
propósito: evita que você delete uma reserva e "perca" o dinheiro de vista. Quer
encerrar? Primeiro resgate o valor de volta pra conta, aí a caixinha some.</p>

<p>Comece com uma só — a clássica reserva de emergência — e sinta como é diferente
guardar quando cada real tem endereço.</p>
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

<h2>Lembretes pra não perder o vencimento</h2>
<p>Dá pra ligar lembretes por cartão, avisando alguns dias antes do vencimento.
Assim você paga em dia e não paga juros à toa.</p>

<h2>O resultado</h2>
<p>Quando você acompanha a fatura enquanto ela cresce, duas coisas mudam: some o
susto no fim do mês, e você começa a segurar a mão <em>antes</em> de estourar — que
é o que realmente protege o seu bolso.</p>
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

<h2>3. Padrão por dia da semana</h2>
<p>Você gasta mais na sexta? No fim de semana? Esse recorte mostra os dias em que a
mão pesa, o que ajuda a criar pequenas regras ("sexta é dia de segurar").</p>

<h2>4. Onde você mais compra</h2>
<p>Um ranking dos lugares/estabelecimentos que mais aparecem nos seus lançamentos.
Ótimo pra achar assinaturas esquecidas e gastos repetidos que passam batido.</p>

<h2>5. Números-chave do mês</h2>
<p>Um resumo direto: quanto entrou, quanto saiu, quanto <strong>sobrou</strong> e o
que você guardou. É o "como foi o mês" em uma olhada.</p>

<h2>Como usar de verdade</h2>
<p>Não precisa virar analista. Escolha <strong>uma</strong> visão por mês pra
investigar e uma atitude pra tomar em cima dela. Pequenos ajustes, repetidos,
mudam o ano inteiro.</p>
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

<h2>3. Transforme a meta numa caixinha</h2>
<p>Crie uma caixinha com o nome do objetivo e a meta em reais. Cada aporte te
mostra a barrinha andando — e ver o progresso é o que mantém você no jogo. Se
quiser, peça pra Piggy guardar assim que o salário cair.</p>

<h2>4. Comemore os marcos</h2>
<p>Bateu 25%, metade, 75%? Reconheça. Meta longa sem marco pelo caminho cansa;
com marcos, cada etapa vira uma pequena vitória.</p>

<h2>5. Reavalie sem culpa</h2>
<p>Mudou de vida, apertou o mês? Ajuste o valor ou o prazo. Uma meta é uma
ferramenta a seu favor, não uma prova. Melhor uma meta menor que você cumpre do
que uma enorme que você abandona.</p>
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
