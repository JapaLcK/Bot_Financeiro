# utils_text.py
"""
Helpers de texto: normalização, parse de valores, regras locais e utilitários.
"""

import re
import unicodedata
from decimal import Decimal

def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))  # remove acentos
    # preserva _ pra rótulos canônicos como "investimento_aporte" sobreviverem
    # à normalização e baterem em CATEGORY_LABELS / INTERNAL_MOVEMENT_CATEGORIES.
    text = re.sub(r"[^a-z0-9_\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def contains_word(text: str, word: str) -> bool:
    # bate palavra inteira quando possível (evita falsos positivos)
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def merchant_key(text: str) -> str:
    """Chave de casamento de estabelecimento para detectar recorrência.

    Normaliza (lowercase, sem acento/pontuação) e remove sufixo de domínio no
    fim, pra "NETFLIX.COM" / "netflix.com.br" baterem com "Netflix". Vazio se
    não sobrar nada. Usada por `find_recurring_candidate` (mesma descrição +
    mesmo valor em meses distintos → sugere gasto fixo)."""
    norm = normalize_text(text)  # "netflix.com.br" → "netflix com br"
    if not norm:
        return ""
    # remove TLD/segundo nível no FINAL apenas (não mexe no meio do nome)
    norm = re.sub(r"\s+(com|net|org|io|app|br)(\s+br)?$", "", norm).strip()
    return norm

# Regras locais (baratas) — já cobrindo mercado/psicologo/petshop
LOCAL_RULES = [
    # ─── Movimentações internas têm prioridade ────────────────────────────────
    # Sem isso, frases como "mercado bitcoin" ou "fundo multimercado" caem em
    # alimentação porque "mercado" bate primeiro.
    #
    # Aportes de investimento — movimentação interna (não é despesa real)
    (["aporte", "aportei", "aportar", "aplicacao", "aplicação", "apliquei", "investi", "investir",
      "compra de acoes", "compra de ações", "compra de acao", "compra de ação",
      "acoes", "ações", "fii", "fiis", "cdb", "rdb", "lci", "lca", "cra", "cri",
      "tesouro direto", "tesouro selic", "tesouro ipca", "tesouro pre", "tesouro pré",
      "tesouro pos", "tesouro pós", "tesouro", "selic", "ipca", "etf",
      "previdencia", "previdência", "previdencia privada", "previdência privada",
      "poupanca", "poupança",
      "pgbl", "vgbl", "fia", "fim", "multimercado", "fundo multimercado", "coe",
      "debenture", "debênture",
      "dolar", "dólar", "euro", "ouro",
      "xp investimentos", "nuinvest", "btg", "btg pactual", "btg invest", "rico investimentos",
      "clear corretora", "ágora", "agora corretora", "modal mais", "warren",
      "binance", "mercado bitcoin", "mercado pago crypto", "coinbase",
      ], "investimento_aporte"),
    # Cripto — mantida separada porque o card "Aportes" usa essa categoria também
    (["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "cripto", "criptomoeda", "criptomoedas",
      "doge", "dogecoin", "shiba", "shib", "ada", "cardano", "usdt", "tether",
      "xrp", "ripple", "bnb", "polygon", "matic", "avax", "avalanche",
      # conceito
      "criptoativo", "criptoativos", "moeda digital", "moedas digitais",
      "altcoin", "stablecoin", "memecoin", "shitcoin", "blockchain", "web3",
      "satoshi", "sats", "hodl",
      # operação
      "staking", "stake", "mineracao", "mineração", "minerar", "minerador",
      "airdrop", "yield farming", "defi", "nft",
      # carteira
      "carteira cripto", "hardware wallet", "cold wallet", "metamask",
      "ledger", "trezor", "trust wallet",
      # exchanges
      "kucoin", "kraken", "bybit", "okx", "bitget", "gate io",
      "foxbit", "novadax", "bitpreco", "bitpreço", "bitso",
      ], "criptomoedas"),
    # Genérico "investimento" — ainda interno, captura quem digita "investi" sem produto
    (["investimento", "investimentos"], "investimentos"),
    # Resgates de investimento — movimentação interna (não é receita real)
    (["resgate", "retirada de investimento", "retirei do investimento"], "investimento_resgate"),
    # Rendimentos — receita real (lucro/juros/dividendos)
    # Receita real (lucro/juros/dividendos). "juros do cartão/cheque especial/
    # atraso" é despesa e é bloqueado (ver KEYWORD_BLOCKERS) pra não virar receita.
    (["rendimento", "rendimentos", "juros", "dividendo", "dividendos", "lucro investimento",
      # proventos (termos que NÃO colidem com nome de produto do bloco de aporte)
      "proventos", "provento", "jcp", "juros sobre capital",
      "juros sobre capital proprio", "juros sobre capital próprio",
      "juros recebidos", "juros da renda fixa", "rendimento do fundo",
      # genéricos de retorno realizado
      "rendeu", "rendendo", "lucrei", "ganho de capital",
      "retorno do investimento", "remuneracao do capital",
      "remuneração do capital", "correcao monetaria", "correção monetária",
      # NOTA: "juros do cdb", "rendimento do tesouro", "dividendos de ações",
      # "rendimento de fii", "staking" etc. NÃO entram aqui: o nome do produto
      # (cdb/tesouro/ações/fii/staking) é capturado antes pelo bloco de aporte
      # ou de cripto. Esses casos ficam a cargo da IA.
      ], "rendimentos"),

    # ─── Despesas reais ───────────────────────────────────────────────────────
    # E-commerce vem antes de mercado pra "mercado livre" não virar supermercado.
    # "amazon" tem blocker pra não roubar "amazon prime" de assinaturas.
    (["mercado livre", "mercado envios", "shopee", "aliexpress", "shein",
      "magalu", "magazine luiza", "americanas", "casas bahia", "amazon",
      "ebay", "temu", "compra online", "compras online",
      # ato de comprar online (genéricos)
      "comprei online", "pedido online", "loja virtual", "e-commerce",
      "ecommerce", "site de compras", "checkout"], "compras online"),
    # Categoria própria (compra de casa), separada de alimentação (comer fora).
    # É a primeira regra de despesa, então "material de limpeza" ganha de
    # "material"/educação e "escova de dente" ganha de "escova"/beleza.
    # Fica DEPOIS do bloco de investimentos pra "mercado bitcoin" seguir aporte.
    (["mercado", "supermercado", "mercadinho", "hipermercado", "mercearia",
      "atacadao", "atacadão", "atacarejo", "sacolao", "sacolão",
      "hortifruti", "quitanda", "armazem", "armazém",
      # compra da casa
      "compras do mes", "compras do mês", "compra do mes", "compra do mês",
      "lista de compras", "mantimentos", "compras da casa",
      # higiene e limpeza
      "papel higienico", "papel higiênico", "produtos de limpeza",
      "material de limpeza", "detergente", "amaciante", "sabao em po",
      "sabão em pó", "desinfetante", "agua sanitaria", "água sanitária",
      "escova de dente", "escova de dentes",
      # papelaria de compra corriqueira — ficam aqui, não em educação
      "caneta", "canetas", "lapis", "lápis", "caderno", "cadernos",
      # enxoval/utensílios — "jogo" sozinho é lazer, "jogo de cama" é mercado
      "jogo de cama", "jogo de panela", "jogo de panelas", "jogo de jantar",
      "jogo de toalhas", "jogo de lencois", "jogo de lençóis",
      # redes
      "carrefour", "assai", "assaí", "pao de acucar", "pão de açúcar",
      "sams club", "makro", "zaffari", "angeloni", "walmart", "costco",
      ], "mercado"),
    (["padaria", "cafe", "café", "cafeteria"], "alimentação"),
    (["aluguel", "condominio", "condomínio", "luz", "energia", "conta de luz", "agua", "água", "conta de agua",
      "conta de água", "gas", "gás", "internet", "wifi",
      # contas da casa
      "conta de energia", "conta de gas", "conta de gás", "conta de internet",
      "iptu", "taxa de lixo", "esgoto", "saneamento", "telefone fixo", "tv a cabo",
      # financiamento — só com qualificador, pra não pegar financiamento de carro
      "financiamento imobiliario", "financiamento imobiliário",
      "prestacao da casa", "prestação da casa", "parcela do apartamento",
      "hipoteca", "consorcio imobiliario", "consórcio imobiliário",
      "entrada do imovel", "entrada do imóvel",
      # a moradia em si — "casa" e "cama" só como palavra inteira (ver
      # EXACT_WORD_KEYWORDS e KEYWORD_BLOCKERS)
      "casa", "apartamento", "imovel", "imóvel", "moradia", "quarto",
      "republica", "república", "kitnet", "aluguel de temporada",
      # manutenção e reforma — "manutenção" sozinha fica de fora: o usuário
      # sempre escreve o complemento (da casa, do carro, …)
      "reforma", "obra", "pintura", "pedreiro", "encanador", "eletricista",
      "marceneiro", "chaveiro", "dedetizacao", "dedetização",
      "conserto", "reparo", "manutencao da casa", "manutenção da casa",
      "manutencao do apartamento", "manutenção do apartamento",
      "manutencao residencial", "manutenção residencial",
      # serviços domésticos
      "diarista", "faxina", "faxineira", "empregada", "jardineiro",
      "porteiro", "zelador", "lavanderia",
      # utilidades da casa
      "movel", "móvel", "moveis", "móveis", "sofa", "sofá", "cama",
      "colchao", "colchão", "geladeira", "fogao", "fogão",
      "maquina de lavar", "máquina de lavar", "ar condicionado",
      "ar-condicionado", "eletrodomestico", "eletrodoméstico",
      # seguro e taxas
      "seguro residencial", "seguro incendio", "seguro incêndio",
      "taxa de condominio", "taxa de condomínio", "fundo de reserva",
      ], "moradia"),
    # "plano de saúde" fica aqui em cima pra ganhar do bloco de assinaturas
    # (que tem "plano mensal", "plano de celular" etc.).
    # "vacina" pura é saúde; "vacina do gato/cachorro/pet" é bloqueada aqui e
    # capturada pela regra de pets logo abaixo.
    (["psicologo", "psicologa", "terapia", "terapeuta", "psiquiatra", "vacina",
      "plano de saude", "plano de saúde", "plano medico", "plano médico",
      "convenio medico", "convênio médico", "convenio de saude", "convênio de saúde"], "saúde"),
    # "banho" e "tosa" continuam em pets — não entram em beleza de propósito.
    # "cao"/"gato"/"gata"/"pet" só como palavra inteira (ver EXACT_WORD_KEYWORDS):
    # "cao" senão bateria em "educacao", "pet" em "tapete"/"carpete".
    (["petshop", "pet shop", "pet", "racao", "racao seca", "veterinario",
      "veterinaria", "veterinária", "vet", "banho", "tosa",
      # animal
      "cachorro", "cao", "cão", "cadela", "gato", "gata", "filhote",
      "animal de estimacao", "animal de estimação", "bicho de estimacao",
      "bicho de estimação", "hamster", "coelho", "passaro", "pássaro",
      # alimentação do pet
      "sache", "sachê", "petisco", "areia higienica", "areia higiênica",
      "areia para gato", "areia de gato",
      # saúde do pet — "vacina do gato" etc. resolvido pelo termo do animal;
      # "vacina" pura fica em saúde (ver regra de saúde + blocker)
      "vermifugo", "vermífugo", "antipulgas", "castracao", "castração",
      "consulta veterinaria", "consulta veterinária", "clinica veterinaria",
      "clínica veterinária", "plano pet", "hotelzinho", "creche pet",
      # higiene do pet
      "tosa higienica", "tosa higiênica", "banho e tosa", "escovacao do pet",
      "escovação do pet",
      # acessórios do pet
      "coleira", "focinheira", "casinha de cachorro", "arranhador",
      "comedouro", "bebedouro",
      # serviços
      "adestrador", "adestramento", "dog walker", "passeador", "pet sitter",
      ], "pets"),
    # Beleza — vem depois de pets e saúde, então "banho/tosa" seguem pet e
    # "terapia" segue saúde. Laser, botox e massagem são tratados como estética.
    (["beleza", "estetica", "estética", "autocuidado",
      "salao", "salão", "salao de beleza", "salão de beleza",
      "cabeleireiro", "cabeleireira", "barbearia", "barbeiro",
      "cabelo", "corte de cabelo", "progressiva", "luzes", "mechas",
      # "escova" sozinho pegaria "escova de dente" — só as formas de cabelo entram.
      "escova progressiva", "escova no cabelo", "escova modeladora",
      "coloracao", "coloração", "tintura", "hidratacao capilar", "hidratação capilar",
      "manicure", "pedicure", "unha", "unhas", "alongamento de unha", "esmalte",
      "depilacao", "depilação", "cera", "laser",
      "sobrancelha", "sobrancelhas", "design de sobrancelha", "henna",
      "cilios", "cílios", "extensao de cilios", "extensão de cílios",
      "limpeza de pele", "botox", "preenchimento", "peeling",
      "massagem", "drenagem", "spa",
      "maquiagem", "batom", "perfume", "cosmetico", "cosmético", "cosmeticos",
      "skincare", "protetor solar",
      # marcas
      "sephora", "boticario", "boticário", "avon", "quem disse berenice",
      ], "beleza"),
    (["ifood", "restaurante", "lanchonete"], "alimentação"),
    # Educação vem ANTES do bloco genérico de comida de propósito: assim
    # "curso de japonês" cai em educação, e "japonês" sozinho cai em alimentação.
    (["livro", "livros", "ebook", "curso", "cursos", "aula", "aulas", "material", "apostila", "faculdade", "escola",
      # instituição
      "universidade", "colegio", "colégio", "creche", "bercario", "berçário",
      "cursinho", "pre vestibular", "pré-vestibular", "pos graduacao",
      "pós-graduação", "posgraduacao", "mba", "mestrado", "doutorado",
      "ensino medio", "ensino médio",
      # pagamentos
      "matricula", "matrícula", "mensalidade", "semestralidade", "anuidade",
      "taxa escolar", "bolsa de estudos",
      # aulas e reforço
      "aula particular", "reforco escolar", "reforço escolar",
      "professor particular", "tutoria", "monitoria", "workshop",
      "treinamento", "palestra", "seminario", "seminário", "certificacao", "certificação",
      # idiomas — sempre com "curso"/"aula" na frente, pra "japonês" e "francês"
      # sozinhos continuarem em alimentação (restaurante).
      "curso de idiomas", "curso de ingles", "curso de inglês", "aula de ingles",
      "curso de espanhol", "curso de frances", "curso de francês",
      "curso de alemao", "curso de alemão", "curso de japones", "curso de japonês",
      "curso de italiano", "intercambio", "intercâmbio",
      # material escolar (caneta/lápis/caderno ficam em mercado, de propósito)
      "material escolar", "mochila escolar", "estojo", "papelaria",
      "uniforme escolar", "calculadora",
      # provas e inscrições
      "enem", "vestibular", "concurso", "inscricao de concurso",
      "inscrição de concurso", "prova", "simulado",
      # plataformas
      "alura", "hotmart", "domestika", "duolingo", "rocketseat",
      "khan academy", "edx", "skillshare", "udemy", "coursera",
      ], "educação"),
    # Termos genéricos de comida — "gastei 50 com comida" caía em "outros" antes.
    (["comida", "comidas", "alimentacao", "alimentação", "alimento", "alimentos",
      "almoco", "almoço", "almocei", "janta", "jantar", "jantei", "cafe da manha",
      "café da manhã", "lanche", "lanches", "lanchei", "marmita", "quentinha",
      "refeicao", "refeição", "refeicoes", "refeições",
      "delivery", "uber eats", "rappi", "zedelivery", "food truck",
      "pizza", "pizzaria", "hamburguer", "hambúrguer", "burger", "sanduiche",
      "sanduíche", "sushi", "japones", "japonês", "churrasco", "churrascaria",
      "acai", "açaí", "sorvete", "sorveteria", "doceria", "confeitaria",
      "acougue", "açougue", "feira", "feira livre",
      "bebida", "bebidas", "cerveja", "cervejas", "boteco", "botequim",
      # bar e noite ficam em alimentação ("night" não entra: cai em outros)
      "bar", "pub", "happy hour", "drinks", "chopp", "chope",
      "buffet", "bufe", "bufê", "self service", "por quilo", "self-service",
      "salgado", "salgados", "pastel", "esfiha", "coxinha",
      # "prime rib" é comida; "prime" sozinho é assinatura (regra mais abaixo).
      # Este bloco vem antes de assinaturas, então o prato ganha a disputa.
      "prime rib", "prime ribs"], "alimentação"),
    # ─── Lazer ────────────────────────────────────────────────────────────────
    # Fica depois de alimentação pra bar/boteco/chopp seguirem sendo comida.
    # "clube" e "jogo" entram só como palavra inteira: "clube de assinatura" e
    # "jogo de cama" são bloqueados/capturados antes e vão pra IA ou mercado.
    (["lazer", "passeio", "diversao", "diversão", "entretenimento", "role",
      # cinema e teatro
      "cinema", "filme", "ingresso", "ingresso online", "ticket de cinema",
      "ticket de show", "ticket de evento", "teatro", "peca de teatro",
      "peça de teatro", "espetaculo", "espetáculo", "musical", "opera", "ópera",
      "stand up", "comedia", "comédia",
      # música e eventos
      # "concerto" (com C) é música/lazer; "conserto" (com S) é moradia.
      "concerto", "concertos",
      "show", "festival", "evento", "festa", "balada", "camarote", "boate",
      "casa de show", "sympla", "eventbrite",
      # viagem e hospedagem
      "viagem", "passagem", "passagem aerea", "passagem aérea", "hospedagem",
      "hotel", "pousada", "resort", "hostel", "booking", "airbnb",
      "excursao", "excursão", "turismo", "aluguel de carro",
      # passeios e atividades
      "parque", "parque aquatico", "parque aquático", "zoologico", "zoológico",
      "museu", "exposicao", "exposição", "boliche", "kart", "paintball",
      "escalada", "trilha", "praia", "piscina", "clube",
      # jogos
      "jogo", "jogos", "game", "games", "videogame", "video game",
      "fliperama", "sinuca", "bilhar",
      ], "lazer"),
    # Transporte. Termos ambíguos (corrida, álcool, multa pura, revisão pura,
    # peças pura, bateria pura, bicicleta, patinete, marcas de posto) ficam de
    # fora: vão pra IA decidir.
    (["uber", "99", "taxi", "metro", "onibus", "gasolina", "combustivel",
      # apps e corridas
      "indriver", "cabify", "mototaxi", "moto taxi", "blablacar",
      # combustível e posto
      "etanol", "diesel", "abastecimento", "posto de gasolina",
      # transporte público — "trem"/"barca"/"balsa" só palavra inteira
      "passagem de onibus", "passagem de ônibus", "bilhete unico", "bilhete único",
      "cartao de transporte", "cartão de transporte", "vale transporte",
      "vale-transporte", "brt", "trem", "vlt", "barca", "balsa", "rodoviaria",
      "rodoviária",
      # carro e manutenção — formas ambíguas só com "do carro"
      "oficina", "mecanico", "mecânico", "troca de oleo", "troca de óleo",
      "pneu", "pneus", "alinhamento", "balanceamento", "funilaria",
      "lava rapido", "lava-rápido", "lavagem do carro", "pecas de carro",
      "peças de carro", "autopecas", "autopeças", "bateria do carro",
      "revisao do carro", "revisão do carro",
      # documentos e taxas
      "ipva", "licenciamento", "dpvat", "cnh", "detran", "pedagio", "pedágio",
      "seguro do carro", "seguro auto", "multa de transito", "multa de trânsito",
      # estacionamento
      "estacionamento", "zona azul", "valet",
      # bike (aluguel) — compra de bicicleta fica pra IA
      "aluguel de bike",
      ], "transporte"),
    # Saúde (parte 2) — fica DEPOIS de pets pra "clínica/consulta veterinária"
    # seguirem em pets. Termos ambíguos (vitamina, suplemento, receita, óculos,
    # aparelho, canal, lente) ficam de fora: vão pra IA decidir.
    (["academia", "remedio", "farmacia", "dentista", "consulta",
      # profissionais
      "medico", "médico", "clinico", "clínico", "cardiologista",
      "dermatologista", "ginecologista", "ortopedista", "pediatra",
      "oftalmologista", "nutricionista", "fisioterapeuta", "fonoaudiologo",
      "fonoaudiólogo", "ortodontista",
      # locais
      "hospital", "clinica", "clínica", "posto de saude", "posto de saúde",
      "upa", "laboratorio", "laboratório", "pronto socorro",
      # exames e procedimentos
      "exame", "exames", "raio x", "ultrassom", "ressonancia", "ressonância",
      "tomografia", "endoscopia", "check up", "check-up", "cirurgia",
      "internacao", "internação", "vacinacao", "vacinação",
      # odontologia
      "aparelho dental", "aparelho nos dentes", "tratamento de canal",
      "clareamento dental", "limpeza dental", "implante dentario",
      "implante dentário",
      # medicamentos
      "medicamento", "drogaria", "antibiotico", "antibiótico",
      # óptica
      "oculos de grau", "óculos de grau", "lente de contato", "optica", "óptica",
      # terapias e bem-estar
      "fisioterapia", "psicoterapia", "acupuntura", "quiropraxia", "nutricao",
      "nutrição",
      # convênio
      "consulta medica", "consulta médica", "coparticipacao", "coparticipação",
      ], "saúde"),
    # Assinaturas — genéricos + marcas. "prime" sozinho cai aqui (palavra inteira,
    # pra não bater em "primeiro"); "prime rib" já foi capturado por alimentação.
    (["netflix", "spotify", "youtube", "prime video", "amazon prime", "prime", "disney",
      # genéricos
      "assinatura", "assinaturas", "assinei", "plano mensal", "plano anual",
      "renovacao", "renovação", "renovei", "subscription", "recorrente",
      "plano de celular", "plano do celular", "plano celular",
      # streaming de vídeo
      # "star+" vira "star" na normalização e bateria em "starbucks" — por isso
      # só a forma escrita por extenso entra na lista.
      "streaming", "hbo", "globoplay", "paramount", "star plus", "apple tv",
      "crunchyroll", "telecine", "mubi",
      # música e áudio
      "deezer", "tidal", "apple music", "youtube music", "audible",
      # nuvem e armazenamento
      "icloud", "google one", "dropbox", "onedrive",
      # software e produtividade
      "office 365", "microsoft 365", "adobe", "photoshop", "canva", "notion",
      "figma", "chatgpt", "copilot", "licenca", "licença", "saas",
      # games
      "steam", "game pass", "xbox game pass", "playstation plus", "ps plus",
      "nintendo online",
      # conteúdo
      "jornal", "revista", "patreon", "newsletter",
      ], "assinaturas"),
]

# Keywords que devem ser matchadas como palavra inteira (sem substring), para
# evitar que "acoes" bata em "transações", "investi" em "investigar", etc.
# A lista é normalizada (sem acento, lowercase) e checada antes da heurística
# de substring em infer_category.
EXACT_WORD_KEYWORDS = {
    "acoes", "acao", "acoesp",
    "investi", "investir", "investimento", "investimentos",
    "aporte", "aportei", "aportar",
    "ouro", "euro", "dolar",
    "btg", "xp",
    # "feira" (alimentação) precisa ser palavra inteira pra não bater em "feirao"
    # de carros/móveis; o caso "segunda-feira" é tratado em KEYWORD_BLOCKERS.
    "feira",
    # "prime" (assinaturas) como palavra inteira pra não bater em "primeiro",
    # "primavera", "primo". "prime rib" é resolvido pela ordem das regras.
    "prime",
    # "plano" nunca entra sozinho, mas "steam"/"saas" precisam de palavra inteira
    # pra não pegar pedaços de outras palavras.
    "steam", "saas", "star",
    # Beleza: "unha" bateria em "punhal", "cera" em "ceramica", "avon" em
    # "pavonear". Só valem como palavra inteira.
    "unha", "cera", "avon",
    # Cripto: "defi" bateria em "definir"/"deficit", "sats" e "stake" em
    # palavras maiores. Só valem como palavra inteira.
    "defi", "sats", "stake",
    # Educação: "prova" bateria em "aprovado", "comprovante", "provador".
    "prova", "concurso",
    # Lazer: "role" (rolê) bateria em "controle", "festa" em "manifesta",
    # "opera" em "operacao". "clube" e "jogo" precisam ser palavra inteira pra
    # o blocker de contexto funcionar ("clube de assinatura", "jogo de cama").
    "role", "festa", "opera", "clube", "jogo", "kart",
    # "game" é substring de "pagamento" (pa-game-nto) — obrigatoriamente
    # palavra inteira, senão todo lançamento com "pagamento" viraria lazer.
    "game", "games",
    # Moradia: "casa" bateria em "casaco"/"casas bahia", "cama" em "camarote",
    # "obra" em "obrigado", "reparo" em "preparo", "movel/moveis" em
    # "automóvel/automóveis". Só valem como palavra inteira.
    "casa", "cama", "obra", "reparo", "movel", "moveis",
    # Pets: "cao" bateria em "educacao"/"aplicacao", "pet" em "tapete"/"carpete",
    # "gato"/"gata" em palavras maiores. Só valem como palavra inteira.
    "cao", "pet", "gato", "gata", "coelho",
    # Transporte: "trem" bateria em "tremendo"/"extremo", "valet" em "cavalete",
    # "balsa" em "bálsamo", "barca" em "embarcação". Só como palavra inteira.
    "trem", "valet", "balsa", "barca",
    # Compras online: "amazon" bateria em "amazonia" (região/nome de
    # estabelecimento). Só vale como palavra inteira; "amazon prime" continua
    # tratado pelo blocker (assinaturas) e "amazon.com" ainda casa (\b no ponto).
    "amazon",
}

# Contextos que INVALIDAM uma keyword mesmo quando ela aparece no texto.
# Chave = keyword normalizada; valor = regex que, se casar, descarta o match.
# Ex.: "feira" é alimentação, mas "sexta-feira" (normalizada pra "sexta feira")
# é só uma data e não deve virar despesa de comida.
KEYWORD_BLOCKERS = {
    "feira": re.compile(r"\b(segunda|terca|quarta|quinta|sexta)\s+feira\b"),
    # "amazon prime" é assinatura, não compra online.
    "amazon": re.compile(r"\bamazon\s+prime\b"),
    # "mercado pago" é meio de pagamento — não é compra de supermercado.
    "mercado": re.compile(r"\bmercado\s+pago\b"),
    # "clube" sozinho é lazer; qualquer "clube de/do/da ..." (assinatura, vinho,
    # livro) sai da regra local e vai pra IA decidir a categoria certa.
    "clube": re.compile(r"\bclube\s+d[eoa]\b"),
    # "jogo" sozinho é lazer; "jogo de cama/panelas" já foi capturado por
    # mercado antes — o blocker cobre os enxovais que não estão na lista.
    "jogo": re.compile(r"\bjogos?\s+de\s+(cama|panela|panelas|jantar|toalha|toalhas|lencol|lencois)\b"),
    # "game pass" é assinatura (regra avaliada depois de lazer) — o blocker
    # impede que a palavra "game" roube esse caso pra lazer.
    "game": re.compile(r"\bgame\s+pass\b"),
    # "casa" sozinha é moradia; "casa de show", "casa de festa", "casa do
    # carro" etc. saem da regra local pra IA decidir o destino certo.
    "casa": re.compile(r"\bcasa\s+d[eoa]\b"),
    # "quarto" sozinho é moradia; "quarto de hotel"/"de pousada" segue pra
    # regra de lazer, e qualquer outro complemento vai pra IA decidir.
    "quarto": re.compile(r"\bquartos?\s+d[eoa]\b"),
    # "vacina" pura é saúde; "vacina do gato/cachorro/pet/animal" sai da regra
    # de saúde pra ser capturada por pets (avaliada logo depois).
    "vacina": re.compile(r"\bvacina\w*\b.{0,20}\b(pet|cachorro|cao|gat[oa]|animal|bicho|filhote|veterinari)"),
    # "juros" é rendimento (receita); mas juros de dívida são DESPESA — esses
    # saem da regra de rendimentos e vão pra IA decidir a categoria certa.
    "juros": re.compile(r"\bjuros\s+d[eoa]\s+(cartao|cartão|cheque|rotativo|atraso|financiamento|emprestimo|empréstimo|mora|parcelamento)\b"),
    # "aluguel de carro/bike" é transporte/lazer, não moradia.
    "aluguel": re.compile(r"\baluguel\s+de\s+(carro|carros|veiculo|veiculos|bicicleta|bike|moto)\b"),
    # "passagem" é lazer (viagem); "passagem de ônibus/metrô/trem" é transporte
    # urbano — sai da regra de lazer pra ser capturada por transporte.
    "passagem": re.compile(r"\bpassagem\s+de\s+(onibus|metro|trem|barca|balsa)\b"),
}


def keyword_blocked(kw_norm: str, text_norm: str) -> bool:
    """True se a keyword aparece num contexto que a invalida (ver KEYWORD_BLOCKERS)."""
    blocker = KEYWORD_BLOCKERS.get(kw_norm)
    return bool(blocker and blocker.search(text_norm or ""))

# Categorias que representam movimentações internas (não entram em receita/despesa do dashboard)
INTERNAL_MOVEMENT_CATEGORIES = {
    "investimento_aporte",
    "investimento_resgate",
    "transferencia_interna",
    "pagamento_fatura",
    "ajuste_saldo",
}

CATEGORY_LABELS = {
    "alimentacao": "alimentação",
    "transporte": "transporte",
    "saude": "saúde",
    "moradia": "moradia",
    "lazer": "lazer",
    "educacao": "educação",
    "assinaturas": "assinaturas",
    "pets": "pets",
    "compras online": "compras online",
    "mercado": "mercado",
    "beleza": "beleza",
    "outros": "outros",
    "investimento_aporte": "investimento_aporte",
    "investimento_resgate": "investimento_resgate",
    "transferencia_interna": "transferencia_interna",
    "pagamento_fatura": "pagamento_fatura",
    "ajuste_saldo": "ajuste_saldo",
    "rendimentos": "rendimentos",
    "investimentos": "investimentos",
    "criptomoedas": "criptomoedas",
}

# Categorias onde "mesmo valor repetido" é ruído, não recorrência: compras
# pontuais (mercado/lazer/compras online), alimentação (valor variável) e
# movimentações internas/investimento. Nessas NÃO sugerimos gasto fixo
# automático. As elegíveis (moradia, assinaturas, educação, saúde, pets,
# beleza, transporte...) ficam por exclusão. Valores são rótulos canônicos
# (comparar via canonicalize_category_label). Decisão de design registrada.
RECURRING_SUGGESTION_BLOCKLIST = {
    "alimentação", "mercado", "lazer", "compras online",
    "rendimentos", "criptomoedas", "outros",
} | INTERNAL_MOVEMENT_CATEGORIES  # inclui investimento_aporte, ajuste_saldo, etc.

INVESTMENT_CATEGORY_HINTS = {
    "investimento", "investimentos",
    "criptomoeda", "criptomoedas", "cripto",
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "acao", "acoes", "fii", "fiis", "etf", "etfs",
    "tesouro", "cdb", "rdb", "lci", "lca",
}

def is_internal_category(categoria: str | None) -> bool:
    """Retorna True se a categoria indica movimentação interna."""
    if not categoria:
        return False
    norm = normalize_text(categoria)
    if norm in INTERNAL_MOVEMENT_CATEGORIES:
        return True
    if norm == "rendimentos":
        return False

    return any(contains_word(norm, hint) for hint in INVESTMENT_CATEGORY_HINTS)


def canonicalize_category_label(category: str | None) -> str:
    norm = normalize_text(category or "")
    if not norm:
        return ""
    # Aceita rótulos digitados com espaço ("investimento aporte") como
    # equivalentes à forma canônica com underscore ("investimento_aporte").
    if norm in CATEGORY_LABELS:
        return CATEGORY_LABELS[norm]
    underscored = norm.replace(" ", "_")
    if underscored in CATEGORY_LABELS:
        return CATEGORY_LABELS[underscored]
    return norm

STOPWORDS_PT = {
    "gastei","paguei","comprei","debitei","recebi","ganhei","salario","reembolso",
    "no","na","nos","nas","em","de","do","da","dos","das","pra","para","por",
    "um","uma","uns","umas","com","ao","aos","as","os","o","a"
}

MEMORY_NOISE_TOKENS = {
    "pix", "pagamento", "pag", "pgto", "transferencia", "transfer", "ted", "doc",
    "debito", "credito", "compra", "pagto", "recebimento", "receita", "despesa",
    "saldo", "ajuste", "tarifa", "taxa", "estorno", "deb", "cred", "cp", "comp",
    "ltda", "me", "epp", "sa", "eireli", "banco", "bank", "loja",
}

# Ruído de fala/transcrição do auto-aprendizado: a wake-word do bot, pronomes,
# "valor por extenso" (reais/centavos), conectores de fala e números por extenso.
# Nunca podem virar — nem compor — uma keyword de regra. Diferente de
# MEMORY_NOISE_TOKENS (jargão bancário de extrato), estes vêm de mensagem de
# texto/áudio: "pig eu mercado mais", "pig reais ifood", "reais centavos almoco".
MEMORY_STOP_TOKENS = {
    # wake-word / marca
    "pig", "piggy", "porquinho", "porquim",
    # pronomes / dêixis de fala
    "eu", "tu", "voce", "vc", "meu", "minha", "meus", "minhas",
    "teu", "tua", "seu", "sua", "nosso", "nossa", "isso", "isto", "aqui", "ai",
    # "valor por extenso" / moeda falada
    "reais", "real", "centavos", "centavo", "conto", "contos", "pila", "pilas",
    "mango", "mangos",
    # conectores / preenchimento de fala
    "mais", "menos", "tambem", "entao", "tipo", "mano", "cara", "acho",
    "acredita", "valor",
    # verbos/particípios de registro (já há variantes em STOPWORDS_PT)
    "recebido", "recebida", "registrada", "registrado", "registrei",
    "pelo", "pela", "dashboard",
    # números por extenso (acentos já caem em normalize_text: "três" -> "tres")
    "zero", "um", "uma", "dois", "duas", "tres", "quatro", "cinco", "seis",
    "sete", "oito", "nove", "dez", "onze", "doze", "treze", "quatorze",
    "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove",
    "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
    "oitenta", "noventa", "cem", "cento", "duzentos", "trezentos", "quinhentos",
    "mil", "milhao", "milhoes", "bilhao", "bilhoes",
}


def is_useful_memory_keyword(keyword: str | None) -> bool:
    kw = normalize_text(keyword or "")
    if not kw or len(kw) < 3:
        return False
    if len(kw) > 48:
        return False
    if not re.search(r"[a-z]", kw):
        return False

    tokens = [tok for tok in kw.split() if tok]
    if not tokens:
        return False
    # Cap de ~2 palavras: keyword de regra é um alvo curto (merchant/coisa),
    # não uma frase. Frases longas ("pao doce mano acredita") são ruído.
    if len(tokens) > 2:
        return False
    if all(
        tok in STOPWORDS_PT or tok in MEMORY_NOISE_TOKENS or tok in MEMORY_STOP_TOKENS
        for tok in tokens
    ):
        return False
    return True


def extract_memory_candidates(text: str | None, limit: int = 3) -> list[str]:
    norm = normalize_text(text or "")
    if not norm:
        return []

    raw_tokens = [tok for tok in norm.split() if tok]
    filtered = [
        tok for tok in raw_tokens
        if len(tok) >= 2
        and not tok.isdigit()
        and tok not in STOPWORDS_PT
        and tok not in MEMORY_NOISE_TOKENS
        and tok not in MEMORY_STOP_TOKENS
    ]

    candidates: list[str] = []

    if filtered:
        # No máx 2 palavras — o alvo limpo, sem os conectores/ruído já removidos.
        first_two = " ".join(filtered[:2])
        if is_useful_memory_keyword(first_two):
            candidates.append(first_two)

    keyword = extract_keyword_for_memory(norm)
    if is_useful_memory_keyword(keyword):
        candidates.append(normalize_text(keyword))

    deduped: list[str] = []
    for candidate in candidates:
        c = normalize_text(candidate)
        if c and c not in deduped:
            deduped.append(c)
        if len(deduped) >= limit:
            break
    return deduped

def extract_keyword_for_memory(text_norm: str) -> str:
    # 1) se bater em alguma keyword das regras locais, salva essa keyword
    for keywords, _cat in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm:
                if len(kw_norm) <= 3:
                    ok = contains_word(text_norm, kw_norm)
                else:
                    ok = contains_word(text_norm, kw_norm) or (kw_norm in text_norm)

                if ok and keyword_blocked(kw_norm, text_norm):
                    ok = False

                if ok:
                    return kw_norm

    # 2) fallback: pega o último "token útil" (exclui números e stopwords)
    tokens = [
        t for t in text_norm.split()
        if t
        and t not in STOPWORDS_PT
        and t not in MEMORY_NOISE_TOKENS
        and t not in MEMORY_STOP_TOKENS
        and len(t) >= 3
        and not t.replace(",", "").replace(".", "").isdigit()
    ]
    if not tokens:
        return ""
    return tokens[-1]

def fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_rate(rate, period: str | None) -> str:
    if rate is None or not period:
        return ""

    # rate pode vir como Decimal do Postgres
    if isinstance(rate, Decimal):
        rate = float(rate)
    else:
        rate = float(rate)

    # CDI é armazenado como multiplicador:
    # 1.16 => 116% CDI, 1.0 => 100% CDI
    if period == "cdi":
        display = rate * 100
    else:
        # Taxas comuns são armazenadas como fração:
        # 0.14 => 14%
        display = rate * 100 if rate <= 1 else rate

    # formatação limpa (sem 1.0000)
    if abs(display - round(display)) < 1e-12:
        pct = str(int(round(display)))
    else:
        pct = f"{display:.6f}".rstrip("0").rstrip(".")
    pct = pct.replace(".", ",")

    if period == "cdi":
        return f"{pct}% CDI"
    if period == "cdi_spread":
        return f"CDI + {pct}% a.a."
    if period == "ipca_spread":
        return f"IPCA + {pct}% a.a."
    if period == "selic_spread":
        return f"SELIC + {pct}% a.a."

    # Na listagem, mostrar só a taxa evita redundância como "14% anual".
    return f"{pct}%"

DEPOSIT_VERBS = [
    "transferi", "coloquei", "adicionei", "depositei", "pus", "botei",
    "mandei", "joguei", "colocar", "adicionar", "depositar", "por", "botar",
    # variações de "adicionar/somar" (áudio/imperativo) — mesma família aceita
    # no saldo; aqui habilita "adicione 300 na caixinha viagem".
    "adicione", "adiciona", "adicionou", "somar", "soma", "some", "somei",
    # imperativo/infinitivo anunciado no catálogo ("deposita 200 na caixinha X")
    # e "guardar/poupar/juntar" — verbo canônico pra guardar numa caixinha.
    "deposita", "deposite", "guardar", "guarda", "guarde", "guardei",
    "poupar", "poupa", "poupe", "poupei", "juntar", "junta", "junte", "juntei",
    "coloca", "coloque", "bota", "bote",
]

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

# Números por extenso até 999 (o que costuma preceder "mil"/"milhão"): cobre
# "dez mil", "cem mil", "dois mil", "cento e cinquenta mil". Cada palavra é um
# token aditivo distinto em português abaixo de 1000, então basta somar
# ("cento" 100 + "cinquenta" 50 = 150). Inclui variantes sem acento porque o
# usuário digita/fala dos dois jeitos.
_PT_NUM_WORDS: dict[str, int] = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4,
    "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14,
    "quinze": 15, "dezesseis": 16, "dezasseis": 16, "dezessete": 17,
    "dezassete": 17, "dezoito": 18, "dezenove": 19, "dezanove": 19,
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "cincoenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
    "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300, "quatrocentos": 400, "quatrocentas": 400,
    "quinhentos": 500, "quinhentas": 500, "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700, "oitocentos": 800, "oitocentas": 800,
    "novecentos": 900, "novecentas": 900,
}
# Alternância ordenada por tamanho (desc) pra o regex casar "dezessete" antes de
# "dez", "cento" antes de "cem" etc.
_PT_NUM_ALT = "|".join(sorted(map(re.escape, _PT_NUM_WORDS), key=len, reverse=True))
# Uma frase = palavra-número, opcionalmente encadeada com "e" ("vinte e cinco").
_PT_PHRASE = rf"(?:{_PT_NUM_ALT})(?:\s+e\s+(?:{_PT_NUM_ALT})|\s+(?:{_PT_NUM_ALT}))*"
_PT_SPELLED_MULT_RE = re.compile(
    rf"\b({_PT_PHRASE})\s+(mil|milh[oõ]es|milhao|milhão)\b"
    rf"(?:\s*e\s+({_PT_PHRASE}|\d+(?:[.,]\d+)?))?",
    re.IGNORECASE,
)

# Aliases públicos + padrões reutilizáveis pra outros módulos (ex.: o parser de
# parcelamento) montarem regex de contagem/valor aceitando número por extenso.
PT_NUM_ALT = _PT_NUM_ALT
PT_PHRASE = _PT_PHRASE
# Igual, mas sem "um"/"uma": seguro pra apagar de uma descrição sem risco de
# remover o artigo ("cama de casal um lugar" não perde o "um").
PT_NUM_ALT_NO_ARTICLE = "|".join(
    sorted((re.escape(w) for w in _PT_NUM_WORDS if w not in ("um", "uma")),
           key=len, reverse=True)
)
_PT_MULT = r"mil|milh[oõ]es|milhao|milhão"
# Um "valor" = dígitos OU número por extenso, com "mil/milhão" opcional e
# encadeamento por "e" ("cento e vinte", "dez mil", "cem mil e quinhentos").
PT_VALUE = (
    rf"(?:r\$\s*)?(?:\d[\d.,]*|{_PT_NUM_ALT}|{_PT_MULT})"
    rf"(?:\s+(?:e\s+)?(?:\d[\d.,]*|{_PT_NUM_ALT}|{_PT_MULT}))*"
)


def _pt_words_to_int(phrase: str) -> int | None:
    """Soma as palavras-número de uma frase ('cento e cinquenta' → 150).

    Retorna None se algum token não for número por extenso conhecido.
    """
    total = 0
    found = False
    for w in re.findall(r"[a-zãõáéíóúâêôç]+", (phrase or "").lower()):
        if w == "e":
            continue
        if w in _PT_NUM_WORDS:
            total += _PT_NUM_WORDS[w]
            found = True
        else:
            return None
    return total if found else None


def parse_money(text: str) -> float | None:
    # multiplicador por extenso composto: "dez mil" → 10000, "cento e cinquenta
    # mil" → 150000, "dois milhões" → 2000000. Vem ANTES da regra de dígito
    # (que agora aceita "mil" sozinho); senão "dez mil" casaria só o "mil" e
    # daria 1000. Suporta resto opcional: "dez mil e quinhentos"/"dez mil e 500".
    sm = _PT_SPELLED_MULT_RE.search(text or "")
    if sm:
        base = _pt_words_to_int(sm.group(1))
        if base is not None:
            mult = 1_000_000 if sm.group(2).lower().startswith("milh") else 1_000
            total = float(base * mult)
            resto = sm.group(3)
            if resto:
                if resto[0].isdigit():
                    total += float(resto.replace(".", "").replace(",", "."))
                else:
                    r = _pt_words_to_int(resto)
                    if r is not None:
                        total += r
            return total

    # multiplicador "mil"/"milhão": "10 mil" → 10000, "1 mil e 500" → 1500.
    # O dígito antes é OPCIONAL: "mil reais" (sem número) = 1000 e "um milhão" =
    # 1000000 — senão cairia no número contínuo abaixo e pegaria um valor errado
    # (ex.: "parcelei mil reais em 10 vezes" pegaria o "10" de "10 vezes").
    # Precisa vir antes do número contínuo.
    mm = re.search(
        r'\b(\d+(?:[.,]\d+)?\s*)?(mil|milh[oõ]es|milhao|milhão)\b(?:\s*e\s*(\d+))?',
        text or "", re.IGNORECASE,
    )
    if mm:
        base = float(mm.group(1).strip().replace(".", "").replace(",", ".")) if mm.group(1) else 1.0
        mult = 1_000_000 if mm.group(2).lower().startswith("milh") else 1_000
        total = base * mult
        if mm.group(3):
            total += float(mm.group(3))
        return total

    # pega o primeiro número contínuo (com possíveis separadores)
    m = re.search(r'(\d[\d.,\s]*)', text)
    if not m:
        return None

    raw = m.group(1).strip().replace(" ", "")

    # normaliza milhares/decimais
    # se tiver vírgula E ponto, decide o decimal pelo último
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            # BR: 1.000,50
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # US: 1,000.50
            raw = raw.replace(",", "")
    elif "," in raw:
        # BR: 1000,50 ou 1.000,50
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        # pode ser milhar (1.000) ou decimal (1000.50)
        parts = raw.split(".")
        if len(parts[-1]) == 3:   # milhar
            raw = raw.replace(".", "")
        # senão, é decimal (deixa como está)

    try:
        return float(raw)
    except ValueError:
        return None


def parse_pt_number(text: str) -> float | None:
    """Converte um número em valor, aceitando dígitos OU por extenso.

    '100' → 100, '79,90' → 79.9, 'cem' → 100, 'cento e vinte' → 120,
    'mil' → 1000, 'dez mil' → 10000, 'dois milhões' → 2000000.

    Diferente de `parse_money`, entende número por extenso PURO (< 1000) como
    'cem'/'doze' — por isso é restrito a contextos onde já se sabe que o trecho
    é um valor (ex.: o "de <valor>" de um parcelamento), evitando confundir o
    artigo "um" com o número 1 em texto livre.
    """
    if not text:
        return None
    t = str(text).strip().lower()
    if not t:
        return None
    # dígitos ou multiplicador mil/milhão → parse_money já cobre
    if re.search(r"\d", t) or re.search(r"mil|milh", t):
        return parse_money(t)
    # por extenso puro (< 1000): 'cem', 'cento e vinte', 'doze'
    t = re.sub(r"\b(reais|real|r\$)\b", " ", t)
    v = _pt_words_to_int(t)
    return float(v) if v is not None else None


# foca a IA para responder questoes so do bot e nao geral
def should_use_ai(text: str) -> bool:
    t = text.lower().strip()

    # 1) Não chama IA pra coisas muito curtas / aleatórias
    if len(t) < 4:
        return False

    # 2) Palavras-chave financeiras
    keywords = [
        "saldo", "lanc", "lanç", "recebi", "receita", "gastei", "despesa",
        "caixinha", "caixinhas", "invest", "investimento", "aporte", "resgate",
        "fatura", "cartao", "cartão", "parcel", "metas", "limite", "gastos",
        "extrato", "conta", "rendeu", "rendendo", "cdb", "tesouro", "cdi"
    ]

    if any(k in t for k in keywords):
        return True

    # 3) Se começa com comandos conhecidos
    commands = [
        "saldo", "listar lancamentos", "listar lançamentos", "desfazer",
        "criar caixinha", "listar caixinhas", "saldo caixinhas",
        "criar investimento", "saldo investimentos"
    ]

    if any(t.startswith(c) for c in commands):
        return True

    return False

# Categorias por palavras-chave (bem simples e eficaz).
# A ordem importa: guess_category retorna o primeiro match, então "educação"
# vem antes de "alimentação" pra "curso de japonês" não virar comida.
CATEGORY_KEYWORDS = {
    "educação": ["curso", "udemy", "coursera", "livro", "faculdade", "mensalidade",
                 "universidade", "colégio", "colegio", "creche", "berçário", "bercario",
                 "cursinho", "pré-vestibular", "pós-graduação", "mba", "mestrado", "doutorado",
                 "matrícula", "matricula", "semestralidade", "anuidade", "bolsa de estudos",
                 "aula particular", "reforço escolar", "professor particular", "tutoria",
                 "monitoria", "workshop", "treinamento", "palestra", "seminário", "certificação",
                 "curso de idiomas", "curso de inglês", "intercâmbio", "intercambio",
                 "material escolar", "mochila escolar", "estojo", "papelaria",
                 "uniforme escolar", "calculadora",
                 "enem", "vestibular", "concurso", "prova", "simulado",
                 "alura", "hotmart", "domestika", "duolingo", "rocketseat", "khan academy"],
    # mercado antes de alimentação: "compras do mês" é mercado, não refeição.
    "compras online": ["mercado livre", "shopee", "aliexpress", "shein", "magalu",
                       "magazine luiza", "americanas", "amazon", "temu", "compra online",
                       "comprei online", "pedido online", "loja virtual", "e-commerce",
                       "ecommerce", "site de compras", "checkout"],
    "mercado": ["mercado", "supermercado", "mercadinho", "hipermercado", "mercearia",
                "atacadão", "atacadao", "atacarejo", "sacolão", "sacolao", "hortifruti", "quitanda",
                "compras do mês", "compras do mes", "lista de compras", "mantimentos",
                "papel higiênico", "papel higienico", "produtos de limpeza", "material de limpeza",
                "detergente", "amaciante", "sabão em pó", "sabao em po", "desinfetante",
                "escova de dente", "escova de dentes",
                "caneta", "canetas", "lápis", "lapis", "caderno", "cadernos",
                "jogo de cama", "jogo de panela", "jogo de panelas", "jogo de jantar",
                "jogo de toalhas", "jogo de lençóis", "jogo de lencois",
                "carrefour", "assaí", "assai", "pão de açúcar", "pao de acucar",
                "walmart", "costco", "whole foods", "makro", "zaffari", "angeloni"],
    "alimentação": ["ifood", "uber eats", "rappi", "restaurante", "lanche", "pizza", "hamburguer", "cafe", "café", "cafeteria", "padaria",
                    "comida", "alimento", "almoço", "almoco", "janta", "jantar", "marmita", "refeição", "refeicao", "delivery", "açaí", "acai", "sushi", "churrasco",
                    "food truck", "self service", "buffet", "boteco", "botequim", "cerveja", "japonês", "japones", "feira livre", "prime rib",
                    "bar", "pub", "happy hour", "drinks", "chopp", "chope"],
    "transporte": ["uber", "lyft", "99", "metro", "trem", "ônibus", "gasolina", "combustível", "posto", "estacionamento", "parking",
                   "indriver", "cabify", "mototáxi", "blablacar", "etanol", "diesel", "abastecimento",
                   "posto de gasolina", "passagem de ônibus", "bilhete único", "vale-transporte",
                   "brt", "vlt", "barca", "balsa", "rodoviária", "oficina", "mecânico", "troca de óleo",
                   "pneu", "pneus", "alinhamento", "balanceamento", "funilaria", "lava-rápido",
                   "peças de carro", "autopeças", "bateria do carro", "revisão do carro",
                   "ipva", "licenciamento", "dpvat", "cnh", "detran", "pedágio", "seguro do carro",
                   "multa de trânsito", "zona azul", "valet", "aluguel de bike"],
    "moradia": ["aluguel", "rent", "condomínio", "luz", "energia", "água", "internet", "wifi", "gás",
                "conta de energia", "conta de gás", "conta de internet", "iptu", "taxa de lixo",
                "esgoto", "saneamento", "telefone fixo", "tv a cabo",
                "financiamento imobiliário", "prestação da casa", "parcela do apartamento",
                "hipoteca", "consórcio imobiliário", "entrada do imóvel",
                "casa", "apartamento", "imóvel", "moradia", "quarto", "república", "kitnet",
                "reforma", "obra", "pintura", "pedreiro", "encanador", "eletricista",
                "marceneiro", "chaveiro", "dedetização", "conserto", "reparo",
                "manutenção da casa", "manutenção do apartamento",
                "diarista", "faxina", "faxineira", "empregada", "jardineiro",
                "porteiro", "zelador", "lavanderia",
                "móvel", "móveis", "sofá", "cama", "colchão", "geladeira", "fogão",
                "máquina de lavar", "ar condicionado", "eletrodoméstico",
                "seguro residencial", "taxa de condomínio", "fundo de reserva"],
    "saúde": ["farmácia", "remédio", "medicina", "consulta", "dentista", "hospital",
              "plano de saúde", "plano de saude", "plano médico", "plano medico",
              "convênio médico", "convenio medico",
              "médico", "clínico", "cardiologista", "dermatologista", "ginecologista",
              "ortopedista", "pediatra", "oftalmologista", "nutricionista",
              "fisioterapeuta", "ortodontista", "clínica", "posto de saúde", "upa",
              "laboratório", "pronto socorro", "exame", "raio x", "ultrassom",
              "ressonância", "tomografia", "endoscopia", "check-up", "cirurgia",
              "internação", "aparelho dental", "tratamento de canal", "limpeza dental",
              "implante dentário", "medicamento", "drogaria", "antibiótico",
              "óculos de grau", "lente de contato", "óptica", "fisioterapia",
              "psicoterapia", "acupuntura", "quiropraxia", "consulta médica",
              "coparticipação"],
    "beleza": ["beleza", "estética", "estetica", "salão", "salao", "cabeleireiro", "barbearia", "barbeiro",
               "cabelo", "escova progressiva", "escova no cabelo", "progressiva", "manicure", "pedicure", "unha", "unhas", "esmalte",
               "depilação", "depilacao", "cera", "laser", "sobrancelha", "cílios", "cilios",
               "limpeza de pele", "botox", "preenchimento", "peeling", "massagem", "drenagem", "spa",
               "maquiagem", "batom", "perfume", "cosmético", "cosmetico", "skincare",
               "sephora", "boticário", "boticario", "avon"],
    "assinaturas": ["netflix", "spotify", "prime", "amazon prime", "hbo", "disney", "icloud", "google one",
                    "assinatura", "assinaturas", "renovação", "renovacao", "subscription", "recorrente",
                    "plano mensal", "plano anual", "plano de celular", "plano do celular",
                    "streaming", "globoplay", "paramount", "star plus", "apple tv", "crunchyroll",
                    "deezer", "tidal", "apple music", "youtube music", "audible",
                    "dropbox", "onedrive", "office 365", "microsoft 365", "adobe", "canva", "notion",
                    "figma", "chatgpt", "copilot", "steam", "game pass", "playstation plus", "ps plus"],
    "compras": ["amazon", "shopee", "aliexpress", "loja", "compra", "roupa", "tenis", "sapato"],
    "lazer": ["cinema", "show", "balada", "viagem", "hotel", "airbnb",
              "lazer", "passeio", "diversão", "diversao", "entretenimento",
              "filme", "ingresso", "ticket de cinema", "teatro", "peça de teatro",
              "espetáculo", "espetaculo", "musical", "stand up", "comédia",
              "festival", "evento", "festa", "camarote", "boate", "casa de show",
              "sympla", "eventbrite", "concerto",
              "passagem", "passagem aérea", "hospedagem", "pousada", "resort",
              "hostel", "booking", "excursão", "turismo", "aluguel de carro",
              "parque", "zoológico", "museu", "exposição", "boliche", "kart",
              "paintball", "escalada", "trilha", "praia", "piscina", "clube",
              "jogo", "game", "games", "videogame", "fliperama", "sinuca", "bilhar"],
    "outros": []
}

def guess_category(text: str) -> str:
    # Normaliza pra que EXACT_WORD_KEYWORDS/KEYWORD_BLOCKERS funcionem igual ao
    # infer_category (ex.: "prime" não pode bater em "primeiro").
    t = normalize_text(text)
    for cat, words in CATEGORY_KEYWORDS.items():
        for w in words:
            w_norm = normalize_text(w)
            if not w_norm:
                continue
            if len(w_norm) <= 3 or w_norm in EXACT_WORD_KEYWORDS:
                ok = contains_word(t, w_norm)
            else:
                ok = w_norm in t
            if ok and not keyword_blocked(w_norm, t):
                return cat
    return "outros"

def parse_note_after_amount(text: str, amount: float) -> str:
    """
    Pega uma "descrição" simples depois do valor.
    Ex: 'gastei 35 no ifood' -> 'ifood'
    """
    t = re.sub(r"\s+", " ", text.strip())
    # remove o valor (primeira ocorrência de número)
    t2 = re.sub(r"\d+[.,]?\d*", "", t, count=1).strip(" -–—:;")
    # remove palavras comuns
    t2 = re.sub(r"\b(gastei|gasto|paguei|pagar|recebi|ganhei|pix|no|na|em|pra|para|de|do|da)\b", "", t2, flags=re.I).strip()
    return t2.strip()[:60]  # limita

def parse_expense_income_natural(text: str):
    """
    Retorna dict com:
      kind: 'expense'|'income'
      amount: float
      note: str
      category: str
    ou None se não reconhecer.
    """
    raw = re.sub(r"\s+", " ", text.lower()).strip()
    amount = parse_money(raw)
    if amount is None:
        return None

    expense_verbs = ["gastei", "paguei", "comprei", "debitei", "cartão", "cartao"]
    income_verbs  = ["recebi", "ganhei", "caiu", "salário", "salario", "pix recebido", "reembolso"]

    is_expense = any(v in raw for v in expense_verbs)
    is_income  = any(v in raw for v in income_verbs)

    # se não tiver verbo claro, não assume
    if not (is_expense or is_income):
        return None

    kind = "expense" if is_expense and not is_income else "income" if is_income and not is_expense else None
    if kind is None:
        return None

    note = parse_note_after_amount(text, amount)
    category = guess_category(text)

    return {"kind": kind, "amount": amount, "note": note, "category": category}

def parse_pocket_deposit_natural(text: str):
    """
    Retorna (amount: float, pocket_name: str) ou (None, None)
    Entende frases como:
      - coloquei 300 na emergência
      - adicionei 50 na caixinha viagem
      - depositei 1200 em emergencia
      - transferi 200 pra caixinha emergencia
    """
    raw = normalize_spaces(text.lower())

    # precisa ter algum verbo de depósito
    if not any(v in raw for v in DEPOSIT_VERBS):
        return None, None

    amount = parse_money(raw)
    if amount is None:
        return None, None

    # tenta extrair o nome depois de "caixinha ..."
    if "caixinha" in raw:
        pocket = raw.split("caixinha", 1)[1].strip()
        pocket = re.sub(r"^(da|do|na|no|pra|para|em)\s+", "", pocket).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        if pocket:
            return amount, pocket

    # se não tem a palavra caixinha, tenta padrões "na/em/pra <nome>"
    m = re.search(r"\b(na|no|em|pra|para)\s+([a-z0-9_\-áàâãéèêíìîóòôõúùûç ]+)", raw)
    if m:
        pocket = m.group(2).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        # corta se tiver outras palavras típicas depois
        pocket = re.split(r"\b(saldo|investimento|apliquei|aplicar)\b", pocket)[0].strip()
        if pocket:
            return amount, pocket

    return None, None
