"""
db/categorias_condominio.py — catálogo de categorias da conta de CONDOMÍNIO.

A conta do síndico (`users.tipo_conta = 'condominio'`) usa o plano de contas da
administradora no lugar das categorias pessoais. Cada categoria carrega o
`grupo` (`user_categories.grupo`); conta pessoal fica com `grupo` nulo.

Fonte única da lista: o relatório gerencial por centro da administradora
(Cond. Residencial Japuíra). O código contábil mora só aqui, não no banco.
"""
from utils_text import normalize_text

from .categories import SYSTEM_CATEGORIES_SEED

GRUPOS_CONDOMINIO = ("receitas", "pessoal", "funcionais", "manutencao", "tributarias", "outras")

# Cor por grupo (as categorias de um grupo se leem juntas na tela).
_COR = {
    "receitas": "#22c55e", "pessoal": "#3b82f6", "funcionais": "#f59e0b",
    "manutencao": "#8b5cf6", "tributarias": "#ef4444", "outras": "#64748b",
}

# (nome, grupo, emoji, código contábil). Nomes minúsculos e sem colisão entre si nem com as
# canônicas pessoais ("água" ≠ "receb. água"; "alimentação funcionários" ≠ "alimentação").
# Na planilha as quatro receitas aparecem todas com o código 4001; aqui vai como está lá.
_CONTAS: list[tuple[str, str, str, str]] = [
    ("mensalidades", "receitas", "💵", "4001"),
    ("receb. água", "receitas", "🚰", "4001"),
    ("antecipação de receitas", "receitas", "⏩", "4001"),
    ("amortiz. antecip. de receitas", "receitas", "⏪", "4001"),
    ("salários", "pessoal", "👷", "3101"),
    ("irrf", "pessoal", "🧾", "3104"),
    ("pis", "pessoal", "🧾", "3105"),
    ("inss", "pessoal", "🏛️", "3106"),
    ("fgts", "pessoal", "🏦", "3107"),
    ("férias", "pessoal", "🏖️", "3108"),
    ("13º salário", "pessoal", "🎄", "3109"),
    ("alimentação funcionários", "pessoal", "🍽️", "3111"),
    ("vale transporte", "pessoal", "🚌", "3112"),
    ("assist. médica e social", "pessoal", "🩺", "3113"),
    ("treinamentos", "pessoal", "🎓", "3115"),
    ("uniformes e epis", "pessoal", "🦺", "3116"),
    ("indenizações trabalhistas", "pessoal", "⚖️", "3117"),
    ("grrf/fgts", "pessoal", "🏦", "3118"),
    ("rescisões", "pessoal", "📄", "3119"),
    ("serviços pf", "funcionais", "🧑‍🔧", "3130"),
    ("serviços pj", "funcionais", "🏢", "3132"),
    ("honorários contábeis", "funcionais", "🧮", "3134"),
    ("segurança terceirizada", "funcionais", "🛡️", "3138"),
    ("honorários advocatícios", "funcionais", "⚖️", "3141"),
    ("brindes e comemorações", "funcionais", "🎉", "3145"),
    ("bens permanentes", "funcionais", "🪑", "3149"),
    ("impostos e taxas", "funcionais", "🧾", "3150"),
    ("reparos e manutenção", "funcionais", "🔧", "3153"),
    ("combustíveis", "funcionais", "⛽", "3160"),
    ("manut. veículos", "funcionais", "🚙", "3161"),
    ("telefone e internet", "funcionais", "📞", "3183"),
    ("água", "funcionais", "💧", "3184"),
    ("energia elétrica", "funcionais", "💡", "3185"),
    ("material de escritório", "funcionais", "📎", "3186"),
    ("despesas de copa", "funcionais", "☕", "3190"),
    ("material de limpeza", "funcionais", "🧹", "3191"),
    ("material de consumo", "funcionais", "📦", "3192"),
    ("material esportivo", "funcionais", "⚽", "3193"),
    ("manut. máquinas", "manutencao", "⚙️", "3171"),
    ("manut. móveis", "manutencao", "🪑", "3172"),
    ("manut. edifícios", "manutencao", "🏢", "3173"),
    ("manut. jardins", "manutencao", "🌳", "3174"),
    ("juros de mora e multas", "tributarias", "⏰", "3416"),
    ("impostos federais", "tributarias", "🏛️", "3417"),
    ("tarifas bancárias", "outras", "🏦", "3501"),
]
CATEGORIAS_CONDOMINIO = [(n, g, e, _COR[g], c) for n, g, e, c in _CONTAS]

# keyword → categoria. A busca (`get_memorized_rules`) casa por PALAVRA **ou por substring** do texto
# normalizado, então só entra palavra que não aparece dentro de outra. Descartadas por isso: "pis"
# (ex.: "pista"), "vt", "luz" (ex.: "luzia"), "folha" (casa "folhagem" do jardim; usa-se
# "folha de pagamento") e "claro" (casa "declaro"/"esclarecimento"). Empate de tamanho: "grrf" e
# "fgts" têm 4 letras, então "grrf fgts" cai na que o banco devolver primeiro.
REGRAS_CONDOMINIO: dict[str, str] = {
    "folha de pagamento": "salários", "salário": "salários", "inss": "inss", "gps": "inss",
    "fgts": "fgts", "grrf": "grrf/fgts", "irrf": "irrf", "férias": "férias",
    "13º salário": "13º salário", "13 salário": "13º salário", "décimo terceiro": "13º salário",
    "vale transporte": "vale transporte", "sabesp": "água",
    "enel": "energia elétrica", "eletropaulo": "energia elétrica",
    "vivo": "telefone e internet", "internet": "telefone e internet",
    "tarifa bancária": "tarifas bancárias", "cesta de serviços": "tarifas bancárias",
    "contador": "honorários contábeis", "contabilidade": "honorários contábeis",
    "advogado": "honorários advocatícios", "jardinagem": "manut. jardins", "rescisão": "rescisões",
    "iptu": "impostos e taxas", "juros de mora": "juros de mora e multas", "multa": "juros de mora e multas",
    "segurança": "segurança terceirizada", "vigilância": "segurança terceirizada",
    "mensalidade": "mensalidades", "taxa condominial": "mensalidades",
}

_CANONICAS = [n for n, _, _ in SYSTEM_CATEGORIES_SEED]


def semear_condominio(cur, user_id: int) -> dict[str, int]:
    """Semeia o plano de contas do condomínio para `user_id`, na transação de `cur`. Idempotente.

    Pré-requisito de quem chama: `ensure_user_categories_seeded(user_id)` já rodou (ele abre conexão
    própria, por isso não roda aqui) — as canônicas existem e podem ser arquivadas.
    - categorias: `on conflict … do update set grupo … where grupo is null` — não sobrescreve grupo já definido;
    - canônicas pessoais sem uso (lançamento ou cartão) DESTE usuário são arquivadas, nunca apagadas;
    - regras: só a keyword que o usuário ainda não tem (comparada sem acento/caixa), nunca sobrescreve.
    """
    for nome, grupo, emoji, cor, _codigo in CATEGORIAS_CONDOMINIO:
        cur.execute(
            """
            insert into user_categories (user_id, name, emoji, color, is_system, grupo)
            values (%s, %s, %s, %s, false, %s)
            on conflict (user_id, name) do update set grupo = excluded.grupo
              where user_categories.grupo is null
            """,
            (user_id, nome, emoji, cor, grupo),
        )
    cur.execute(
        """
        update user_categories uc set is_archived = true
         where uc.user_id = %s and uc.is_system and not uc.is_archived and uc.name = any(%s)
           and not exists (select 1 from launches l where l.user_id = uc.user_id and lower(l.categoria) = uc.name)
           and not exists (select 1 from credit_transactions t where t.user_id = uc.user_id and lower(t.categoria) = uc.name)
        """,
        (user_id, _CANONICAS),
    )
    arquivadas = cur.rowcount
    cur.execute("select keyword from user_category_rules where user_id = %s", (user_id,))
    tem = {normalize_text(r["keyword"]) for r in cur.fetchall()}
    novas = 0
    for kw, cat in REGRAS_CONDOMINIO.items():
        if normalize_text(kw) in tem:
            continue
        # clock_timestamp(): regra POSTERIOR à categoria (a obsolescência compara as datas; ver `_regra_ficou_obsoleta`).
        cur.execute(
            "insert into user_category_rules (user_id, keyword, category, created_at) "
            "values (%s, %s, %s, clock_timestamp()) on conflict (user_id, keyword) do nothing",
            (user_id, kw, cat),
        )
        novas += cur.rowcount
    return {"categorias": len(CATEGORIAS_CONDOMINIO), "arquivadas": arquivadas, "regras_novas": novas}
