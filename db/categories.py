"""
db/categories.py — Regras de categorização automática + metadata visual (Sprint 3).

Duas tabelas distintas:
- `user_category_rules`: keyword → category (memorização automática). Funções
  abaixo de `list_category_rules`, `add_category_rule` etc.
- `user_categories`: nome + emoji + cor por usuário (metadata visual da
  Sprint 3). Não tem FK em launches — `launches.categoria` continua string
  livre. Rename emite UPDATE em cascata nas 5 tabelas que referenciam o
  texto da categoria.
"""
import logging
import unicodedata

from .connection import get_conn
from .users import ensure_user
from utils_text import normalize_text

log = logging.getLogger(__name__)


# ─── Seed das 15 categorias canônicas (Sprint 3) ─────────────────────────────
# Mesma lista de `ai_router.py:ALLOWED_CATEGORIES`. Emoji/cor escolhidos pra
# bater com a paleta do dashboard (gradient roxo→azul) e contraste visual.
SYSTEM_CATEGORIES_SEED: list[tuple[str, str, str]] = [
    ("alimentação",         "🍔", "#f59e0b"),
    ("mercado",             "🛒", "#84cc16"),
    ("transporte",          "🚗", "#3b82f6"),
    ("saúde",               "💊", "#ec4899"),
    ("moradia",             "🏠", "#8b5cf6"),
    ("lazer",               "🎬", "#10b981"),
    ("educação",            "📚", "#06b6d4"),
    ("assinaturas",         "📺", "#6366f1"),
    ("pets",                "🐾", "#f97316"),
    ("compras online",      "📦", "#a855f7"),
    ("beleza",              "💄", "#f43f5e"),
    ("investimento_aporte", "📈", "#22c55e"),
    ("criptomoedas",        "₿",  "#eab308"),
    ("rendimentos",         "💰", "#14b8a6"),
    ("outros",              "🏷️", "#64748b"),
]


def list_category_rules(user_id: int) -> list[tuple[str, str]]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select keyword, category from user_category_rules "
                "where user_id=%s order by length(keyword) desc",
                (user_id,),
            )
            rows = cur.fetchall()
    return [(r["keyword"], r["category"]) for r in rows]


def add_category_rule(user_id: int, keyword: str, category: str) -> None:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    category = (category or "").strip()
    if not keyword:
        raise ValueError("keyword vazio")
    if not category:
        raise ValueError("category vazia")
    _grava_regra(user_id, keyword, category)


def delete_category_rule(user_id: int, keyword: str) -> int:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    if not keyword:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_category_rules WHERE user_id=%s AND keyword=%s",
                (user_id, keyword),
            )
            n = cur.rowcount
        conn.commit()
    return n


def delete_category_rules_by_category(user_id: int, category: str) -> int:
    ensure_user(user_id)
    category = (category or "").strip()
    if not category:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_category_rules WHERE user_id=%s AND lower(category)=lower(%s)",
                (user_id, category),
            )
            n = cur.rowcount
        conn.commit()
    return n


def list_categories(user_id: int) -> list[str]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT category FROM user_category_rules "
                "WHERE user_id=%s ORDER BY category",
                (user_id,),
            )
            rows = cur.fetchall()
    return [r["category"] if isinstance(r, dict) else r[0] for r in rows]


def get_memorized_category(user_id: int, memo: str) -> str | None:
    """
    Retorna categoria memorizada se alguma keyword bater com o texto.
    """
    achado = get_memorized_rule(user_id, memo)
    return None if achado is None else achado[1]


def get_memorized_rule(user_id: int, memo: str):
    """A regra memorizada que bate com o texto: `(keyword, categoria, criada_em)`.

    Existe porque quem consome precisa do KEYWORD, não só do destino: é ele que
    diz se a regra ficou obsoleta depois que o usuário criou uma categoria que
    passou a ser dona daquele termo (`infer_category`, passo B). O
    `get_memorized_category` delega para cá — a busca é uma só (§0.7).
    """
    from utils_text import normalize_text, contains_word  # import local pra evitar loop circular

    ensure_user(user_id)
    memo_norm = normalize_text(memo or "")
    if not memo_norm:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, category, created_at FROM user_category_rules "
                "WHERE user_id = %s ORDER BY LENGTH(keyword) DESC",
                (user_id,),
            )
            rows = cur.fetchall()

    for r in rows:
        keyword = r.get("keyword") if isinstance(r, dict) else r[0]
        category = r.get("category") if isinstance(r, dict) else r[1]
        kw_norm = normalize_text(keyword or "")
        if not kw_norm:
            continue
        if contains_word(memo_norm, kw_norm) or (kw_norm in memo_norm):
            destino = (category or "").strip()
            criada_em = r.get("created_at") if isinstance(r, dict) else r[2]
            return (keyword, destino, criada_em) if destino else None

    return None


def _grava_regra(user_id: int, keyword: str, category: str) -> None:
    """O único INSERT em `user_category_rules`.

    Existia escrito duas vezes — em `add_category_rule` e em
    `upsert_category_rule`, que diferem só no tratamento do keyword (uma valida
    e chama `ensure_user`, a outra faz `lower()`). As duas cópias custaram caro:
    a renovação do `created_at` foi aplicada numa e a outra continuou como
    estava, e o defeito só apareceu porque um teste mediu o timestamp.

    `created_at` renovado no conflito é necessário, não cosmético: ele é o
    critério que separa regra OBSOLETA de regra DELIBERADA
    (`_regra_ficou_obsoleta`). Sem renovar, quem manda "aprender cafe como
    lazer" DEPOIS de criar a categoria Café recebe a confirmação e continua
    sendo classificado como Café — a regra nova herdaria a data velha e seria
    descartada por antiga.

    O caminho automático não chega aqui quando há conflito: o guard do #123
    (`learn_from_signals`, `guard_local_conflict`) recusa aprender token que já
    pertence a categoria custom. Então renovar só afeta ação deliberada.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_category_rules (user_id, keyword, category) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, keyword) DO UPDATE "
                "   SET category = EXCLUDED.category, created_at = now()",
                (user_id, keyword, category),
            )
        conn.commit()


def upsert_category_rule(user_id: int, keyword: str, category: str) -> None:
    _grava_regra(user_id, (keyword or "").strip().lower(), category)


def list_user_category_rules(user_id: int) -> list[tuple[str, str]]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, category FROM user_category_rules "
                "WHERE user_id = %s ORDER BY LENGTH(keyword) DESC",
                (user_id,),
            )
            rows = cur.fetchall() or []

    out: list[tuple[str, str]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r.get("keyword") or "", r.get("category") or ""))
        else:
            out.append((r[0] or "", r[1] or ""))
    return out


def get_uncategorized_launches(user_id: int, limit: int = 20) -> list[dict]:
    """
    Lançamentos em 'outros' ou sem categoria — candidatos a virar regra.
    Útil pra IA sugerir criar regras de categorização.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, alvo, nota, categoria, criado_em
                from launches
                where user_id = %s
                  and tipo = 'despesa'
                  and is_internal_movement = false
                  and (categoria is null or lower(categoria) = 'outros')
                order by criado_em desc
                limit %s
                """,
                (user_id, int(limit)),
            )
            rows = cur.fetchall() or []

    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append({
                "id": r.get("id"),
                "tipo": r.get("tipo"),
                "valor": float(r.get("valor") or 0),
                "alvo": r.get("alvo"),
                "nota": r.get("nota"),
                "categoria": r.get("categoria"),
                "criado_em": r.get("criado_em"),
            })
        else:
            out.append({
                "id": r[0], "tipo": r[1], "valor": float(r[2] or 0),
                "alvo": r[3], "nota": r[4], "categoria": r[5], "criado_em": r[6],
            })
    return out


# ─── user_categories (metadata visual — Sprint 3) ───────────────────────────


def _normalize_category_name(name: str) -> str:
    """Normaliza nome de categoria pro storage (lowercase, trim, espaços únicos).

    Controles (Cc) e formatadores invisíveis (Cf) viram espaço antes do
    collapse. Cc porque o Postgres RECUSA o NUL em texto (`psycopg.DataError`),
    e um PATCH com NUL na categoria virava 500 — a `main` respondia 200 porque
    passava pelo `normalize_text`, que já filtrava. Cf porque zero-width
    (U+200B) e override bidi (U+202E) são invisíveis: "cafe" e "cafe"+U+200B
    ficariam como duas fatias idênticas na tela, que é exatamente o bug que
    esta normalização existe pra matar. `str.split()` só quebra em whitespace —
    nenhuma das duas classes passa por ela."""
    limpo = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf") else c
        for c in (name or "")
    )
    return " ".join(limpo.lower().split())


def ensure_user_categories_seeded(user_id: int) -> None:
    """Seed lazy: popula as canônicas com is_system=true.

    O insert das canônicas roda SEMPRE (on conflict do nothing), pra que
    categorias novas adicionadas ao seed — como "mercado" — cheguem também
    aos usuários que já tinham sido semeados antes.
    Adicional: importa categorias customizadas que o user JÁ TEM em launches
    (lower distinto) — assim a tela começa povoada com a realidade dele. Essa
    parte só roda na primeira vez.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from user_categories where user_id=%s and is_system=true limit 1",
                (user_id,),
            )
            already_seeded = cur.fetchone() is not None

            for name, emoji, color in SYSTEM_CATEGORIES_SEED:
                cur.execute(
                    """
                    insert into user_categories (user_id, name, emoji, color, is_system)
                    values (%s, %s, %s, %s, true)
                    on conflict (user_id, name) do nothing
                    """,
                    (user_id, name, emoji, color),
                )

            if already_seeded:
                return

            # Importa categorias customizadas já presentes em launches.
            cur.execute(
                """
                insert into user_categories (user_id, name, emoji, color, is_system)
                select %s, lower(trim(categoria)), '🏷️', '#7c3aed', false
                from (
                    select distinct categoria from launches
                    where user_id=%s and categoria is not null
                    union
                    select distinct categoria from credit_transactions
                    where user_id=%s and categoria is not null
                ) src
                where trim(coalesce(categoria,'')) <> ''
                on conflict (user_id, name) do nothing
                """,
                (user_id, user_id, user_id),
            )
        conn.commit()


def list_user_categories_full(
    user_id: int, include_archived: bool = True
) -> list[dict]:
    """Lista categorias da tabela `user_categories` com contagem de uso.

    Retorna dicts com: id, name, emoji, color, is_archived, is_system,
    usage_count (qtd de launches+credit_transactions com esse texto).
    Ordenação: não arquivadas primeiro, depois alfabético.
    """
    ensure_user(user_id)
    ensure_user_categories_seeded(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with usage as (
                  select lower(categoria) as name, count(*) as n
                  from launches
                  where user_id=%s and categoria is not null
                  group by lower(categoria)
                  union all
                  select lower(categoria) as name, count(*) as n
                  from credit_transactions
                  where user_id=%s and categoria is not null
                  group by lower(categoria)
                )
                select
                  uc.id, uc.name, uc.emoji, uc.color,
                  uc.is_archived, uc.is_system,
                  coalesce((select sum(n) from usage where usage.name = uc.name), 0)::int as usage_count
                from user_categories uc
                where uc.user_id=%s
                  and (%s::boolean = true or uc.is_archived = false)
                order by uc.is_archived asc, uc.name asc
                """,
                (user_id, user_id, user_id, include_archived),
            )
            rows = cur.fetchall() or []
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "emoji": r["emoji"],
            "color": r["color"],
            "is_archived": bool(r["is_archived"]),
            "is_system": bool(r["is_system"]),
            "usage_count": int(r["usage_count"] or 0),
        })
    return out


def categoria_criada_depois_de(user_id: int, name: str, quando) -> bool:
    """A categoria custom `name` nasceu depois de `quando`?

    É o que separa regra OBSOLETA de regra DELIBERADA: quem linkou uma keyword
    tendo a categoria já na tela fez uma escolha, e ela continua valendo. Quem
    tinha a regra aprendida antes de a categoria existir tem uma regra que
    envelheceu — o `user_category_rules` não guarda de onde a regra veio, mas
    guarda QUANDO, e a ordem responde a pergunta sem inventar coluna nova.

    Sem `quando` (regra antiga de antes do default de created_at) devolve True:
    o caso conhecido é justamente a regra velha, e o fallback erra para o lado
    de respeitar a categoria que o usuário criou na tela.
    """
    if quando is None:
        return True
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from user_categories "
                " where user_id=%s and lower(name)=lower(%s) and created_at > %s limit 1",
                (user_id, (name or "").strip(), quando),
            )
            return cur.fetchone() is not None


def list_custom_category_names(user_id: int) -> list[str]:
    """Nomes das categorias CUSTOMIZADAS (is_system=false) e não arquivadas.

    Usada pela inferência de categoria (`infer_category`) pra reconhecer, num
    lançamento, uma categoria que o usuário criou na tela mas ainda não tem
    regra de keyword. Sem isso, "gastei 400 com minha namorada" caía em "outros"
    mesmo o usuário tendo criado a categoria "gastos com minha namorada".
    """
    # Caminho quente da inferência: não faz seed lazy aqui. A tela de categorias
    # e os writes continuam semeando; a leitura precisa ser barata e read-only.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select name from user_categories "
                "where user_id=%s and is_system=false and is_archived=false "
                "order by length(name) desc",
                (user_id,),
            )
            rows = cur.fetchall() or []
    return [(r["name"] if isinstance(r, dict) else r[0]) for r in rows]


def get_user_category(user_id: int, cat_id: int) -> dict | None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, emoji, color, is_archived, is_system "
                "from user_categories where user_id=%s and id=%s",
                (user_id, int(cat_id)),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"], "name": r["name"], "emoji": r["emoji"],
                "color": r["color"], "is_archived": bool(r["is_archived"]),
                "is_system": bool(r["is_system"]),
            }


def create_user_category(
    user_id: int, name: str, emoji: str | None = None, color: str | None = None
) -> dict:
    """Cria categoria custom. Levanta ValueError("CATEGORIA_DUPLICADA") se já existe."""
    ensure_user(user_id)
    ensure_user_categories_seeded(user_id)
    norm = _normalize_category_name(name)
    if not norm:
        raise ValueError("CATEGORIA_INVALIDA")
    emoji = (emoji or "🏷️").strip() or "🏷️"
    color = (color or "#7c3aed").strip() or "#7c3aed"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from user_categories where user_id=%s and name=%s",
                (user_id, norm),
            )
            if cur.fetchone():
                raise ValueError("CATEGORIA_DUPLICADA")
            cur.execute(
                """
                insert into user_categories (user_id, name, emoji, color, is_system)
                values (%s, %s, %s, %s, false)
                returning id
                """,
                (user_id, norm, emoji, color),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()

    # A categoria nova pode ser a dona legítima de keywords que o bot aprendeu
    # ANTES de ela existir — o caso "gastei com cafe" por meses e só depois
    # criar a categoria Café. Sem isto, a regra velha vence no passo B de
    # `infer_category` e a categoria recém-criada nunca é usada. Import local
    # porque `category_service` importa deste módulo.
    # BEST-EFFORT de propósito: a categoria já foi commitada acima, e falhar
    # aqui devolveria 500 num POST que deu certo — o usuário veria erro e a
    # retentativa daria CATEGORIA_DUPLICADA. A correção do comportamento não
    # depende disto: quem garante que a regra obsoleta perde é o guard de
    # LEITURA em `infer_category`. Isto é só higiene do dado.
    try:
        from core.services.category_service import reconciliar_regras_com_categoria

        reconciliar_regras_com_categoria(user_id, norm)
    except Exception:
        log.warning("reconciliacao de regras falhou para user=%s categoria=%r",
                    user_id, norm, exc_info=True)
    return get_user_category(user_id, new_id)


def update_user_category(
    user_id: int,
    cat_id: int,
    *,
    new_name: str | None = None,
    emoji: str | None = None,
    color: str | None = None,
) -> dict:
    """PATCH em uma categoria. Se `new_name` muda, faz UPDATE em cascata.

    Levanta:
      ValueError("CATEGORIA_NAO_ENCONTRADA")
      ValueError("CATEGORIA_DUPLICADA") — novo nome já existe pra outra row
    """
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")

    next_emoji = (emoji or current["emoji"]).strip() or current["emoji"]
    next_color = (color or current["color"]).strip() or current["color"]
    next_name = current["name"]

    if new_name is not None:
        norm = _normalize_category_name(new_name)
        if not norm:
            raise ValueError("CATEGORIA_INVALIDA")
        next_name = norm

    rename = next_name != current["name"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            if rename:
                cur.execute(
                    "select id from user_categories "
                    "where user_id=%s and name=%s and id<>%s",
                    (user_id, next_name, int(cat_id)),
                )
                if cur.fetchone():
                    raise ValueError("CATEGORIA_DUPLICADA")

                old_name = current["name"]
                # Cascata em todas as tabelas que armazenam o texto.
                cur.execute(
                    "update launches set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s)",
                    (next_name, user_id, old_name),
                )
                cur.execute(
                    "update credit_transactions set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s)",
                    (next_name, user_id, old_name),
                )
                cur.execute(
                    "update category_budgets set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s) "
                    "and not exists ("
                    "  select 1 from category_budgets cb2 "
                    "  where cb2.user_id=%s and lower(cb2.categoria)=lower(%s) and cb2.id<>category_budgets.id"
                    ")",
                    (next_name, user_id, old_name, user_id, next_name),
                )
                cur.execute(
                    "update budget_alert_sent set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s) "
                    "and not exists ("
                    "  select 1 from budget_alert_sent bs2 "
                    "  where bs2.user_id=%s and lower(bs2.categoria)=lower(%s) "
                    "    and bs2.ym=budget_alert_sent.ym and bs2.threshold=budget_alert_sent.threshold"
                    ")",
                    (next_name, user_id, old_name, user_id, next_name),
                )
                cur.execute(
                    "update user_category_rules set category=%s "
                    "where user_id=%s and lower(category)=lower(%s)",
                    (next_name, user_id, old_name),
                )

            cur.execute(
                """
                update user_categories
                   set name=%s, emoji=%s, color=%s
                 where user_id=%s and id=%s
                """,
                (next_name, next_emoji, next_color, user_id, int(cat_id)),
            )
        conn.commit()
    return get_user_category(user_id, cat_id)


def set_user_category_archived(user_id: int, cat_id: int, archived: bool) -> dict:
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update user_categories set is_archived=%s where user_id=%s and id=%s",
                (bool(archived), user_id, int(cat_id)),
            )
        conn.commit()
    return get_user_category(user_id, cat_id)


def delete_user_category(user_id: int, cat_id: int) -> None:
    """Deleta categoria. Levanta ValueError se tem lançamentos vinculados.

    Categorias system (is_system=true) só podem ser arquivadas, nunca deletadas
    — manter a lista canônica disponível pro user reverter um arquivamento.
    """
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")
    if current["is_system"]:
        raise ValueError("CATEGORIA_SISTEMA_INDELETAVEL")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  (select count(*) from launches
                   where user_id=%s and lower(categoria)=lower(%s)) +
                  (select count(*) from credit_transactions
                   where user_id=%s and lower(categoria)=lower(%s)) as total
                """,
                (user_id, current["name"], user_id, current["name"]),
            )
            total = int(cur.fetchone()["total"] or 0)
            if total > 0:
                raise ValueError("CATEGORIA_COM_LANCAMENTOS")
            cur.execute(
                "delete from user_categories where user_id=%s and id=%s",
                (user_id, int(cat_id)),
            )
            # Na MESMA transação, antes do commit: se a limpeza falhasse depois,
            # a categoria já estaria apagada, o endpoint devolveria erro e a
            # retentativa daria CATEGORIA_NAO_ENCONTRADA — não sobraria caminho
            # de API para limpar as regras órfãs. Sem isto elas apontam para um
            # nome que não existe mais e o passo B de `infer_category` devolve
            # categoria fantasma. O rename já era tratado; faltava o delete.
            cur.execute(
                "delete from user_category_rules "
                " where user_id=%s and lower(category)=lower(%s)",
                (user_id, current["name"]),
            )
        conn.commit()


def resolve_category_rule_target(user_id: int, target: str) -> tuple[str, str, int]:
    """
    Resolve um alvo de remoção informado pelo usuário.

    Retorna:
      ("keyword", keyword_original, 1)
      ("category", category_original, qtd_regras)
      ("", "", 0) se não encontrar
    """
    target_norm = normalize_text(target or "")
    if not target_norm:
        return ("", "", 0)

    rules = list_user_category_rules(user_id)
    if not rules:
        return ("", "", 0)

    for keyword, _category in rules:
        if normalize_text(keyword) == target_norm:
            return ("keyword", keyword, 1)

    category_matches: dict[str, int] = {}
    for _keyword, category in rules:
        if normalize_text(category) == target_norm:
            category_matches[category] = category_matches.get(category, 0) + 1

    if category_matches:
        category, count = max(category_matches.items(), key=lambda item: item[1])
        return ("category", category, count)

    return ("", "", 0)


# ─── Texto livre do usuário → nome de categoria ──────────────────────────────


# Teto do nome de categoria vindo de texto livre. Uma fonte só — as portas
# (PATCH /launches, /credit-transactions, /installments, tool da IA, WhatsApp)
# citam esta constante em vez de repetir o número.
CATEGORY_NAME_MAX_LEN = 80


def user_category_display_map(user_id: int, *, strict: bool = False) -> dict[str, str]:
    """`normalize_text(name)` → `name` como está gravado em `user_categories`.

    Pré-carregado uma vez pelos importadores de extrato (evita N+1: uma query
    por transação). Inclui arquivadas — o nome digitado tem de reencontrar a
    categoria existente em vez de criar uma gêmea.

    READ-ONLY, como o resto do caminho quente da inferência (ver
    `list_custom_category_names`): NÃO semeia. O seed não muda o resultado —
    os 15 nomes canônicos são resolvidos no passo 1 de `resolve_category_input`,
    antes de o mapa ser consultado — e semear aqui punha 15 INSERT em toda
    inferência que bate em regra do usuário. O desempate é determinístico pela
    própria ordenação, sem depender do seed.

    Falha de banco devolve mapa vazio em vez de estourar — nas 3 chamadas de
    importação de extrato uma exceção aqui abortaria o arquivo inteiro por
    causa de um enfeite de grafia, e o import não dependia desta tabela antes
    deste PR.

    `strict=True` faz a falha SUBIR, e é o que as portas de correção usam:
    lá a resposta vira DADO. Mapa vazio numa falha transitória não acha a
    categoria que o usuário já tem, o nome digitado é aceito como novo e
    `ensure_user_category` grava a gêmea — exatamente o que este módulo
    existe pra impedir. Medido: usuário com "Café da Manhã" digitando
    "Cafe da Manha" ficava com as duas linhas no catálogo.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Desempate determinístico e independente de id: nomes
                # distintos podem colapsar no mesmo normalizado ("mcdonald s" e
                # "mcdonald's"). Vence o do seed (is_system) — é a grafia
                # oficial — e, entre iguais, o menor nome em ordem alfabética.
                # Ordenar por id deixava o resultado dependente de quem foi
                # criado/apagado antes.
                cur.execute(
                    "select name from user_categories where user_id=%s "
                    "order by is_system asc, name desc",
                    (user_id,),
                )
                rows = cur.fetchall() or []
    except Exception:
        if strict:
            raise
        log.warning("user_category_display_map falhou user=%s", user_id, exc_info=True)
        return {}
    return {normalize_text(r["name"]): r["name"] for r in rows if (r["name"] or "").strip()}


def resolve_category_input(
    user_id: int,
    raw: str,
    *,
    create: bool = False,
    display_map: dict[str, str] | None = None,
) -> str | None:
    """Texto livre → nome de categoria pra gravar em `launches.categoria`.

    Normalizado é chave, forma de exibição é valor: devolve o nome que já
    existe em `user_categories` sempre que houver um, pra não criar fatias
    gêmeas no dashboard. NÃO escreve no banco: `create=True` só significa
    "aceito nome novo" e devolve o nome já no formato de storage — quem grava
    a linha é `ensure_user_category`, DEPOIS de o UPDATE ter dado certo (criar
    antes deixava categoria órfã quando o lançamento não existia).
    """
    from utils_text import CATEGORY_LABELS, canonicalize_category_label

    norm = normalize_text(raw or "")
    if not norm:
        return None

    # 1) rótulo do sistema: "Alimentação"/"alimentacao"/"ALIMENTACAO" → "alimentação"
    if norm in CATEGORY_LABELS or norm.replace(" ", "_") in CATEGORY_LABELS:
        return canonicalize_category_label(norm)

    # 2) categoria que o usuário já tem, na grafia dele
    if display_map is None:
        display_map = user_category_display_map(user_id, strict=create)
    existing = display_map.get(norm)
    if existing:
        return existing

    if not create:
        return None

    # 3) nome novo. Preservar a grafia só vale pra quem VAI ganhar a linha em
    #    `user_categories` — é o catálogo que de-duplica as grafias seguintes.
    #    Sem plano de categoria custom não há catálogo, e preservar faria
    #    "Padaria do Zé" e "Padaria do Ze" virarem duas fatias no dashboard,
    #    onde a `main` colapsava as duas. Aí a de-duplicação é o próprio
    #    `normalize_text`, que é o que a `main` grava.
    # Mede o que VAI SER GRAVADO, não o normalizado: `normalize_text` tira
    # acento/emoji/pontuação e ENCOLHE a entrada, então medir por ele deixava
    # passar nome de 5000 caracteres (emoji + uma letra normaliza pra 1 char).
    stored = _normalize_category_name(raw) if _custom_categories_allowed(user_id) else norm
    if not stored or len(stored) > CATEGORY_NAME_MAX_LEN:
        return None
    return stored


def _custom_categories_allowed(user_id: int) -> bool:
    """Fonte única do gate de categoria custom fora do HTTP. Mesma feature do
    `POST /categories` (`require_pro_feature("custom_categories")`)."""
    from core.services.plan_service import plan_gate_ok  # local: db <-> plan_service

    return plan_gate_ok(user_id, "custom_categories")


def ensure_user_category(user_id: int, name: str) -> None:
    """Garante a linha em `user_categories` do nome que acabou de ser gravado.

    Idempotente e best-effort: roda DEPOIS do UPDATE (senão sobra categoria
    órfã quando o alvo não existe) e nunca derruba a resposta — a correção já
    está no banco. No-op pra rótulo do sistema, pra nome que já existe e pra
    quem não tem plano com categoria custom (aí o texto fica só no lançamento,
    como era antes da normalização).
    """
    from utils_text import CATEGORY_LABELS

    norm = normalize_text(name or "")
    if not norm or norm in CATEGORY_LABELS or norm.replace(" ", "_") in CATEGORY_LABELS:
        return
    try:
        if norm in user_category_display_map(user_id):
            return
        # ponytail: 2ª leitura de plano no mesmo PATCH (a 1ª é o
        # `resolve_category_input`). Fica porque esta função é pública e GRAVA;
        # se pesar, passa o veredito como argumento.
        if not _custom_categories_allowed(user_id):
            return
        create_user_category(user_id, name)
    except ValueError:
        pass  # CATEGORIA_DUPLICADA/INVALIDA: corrida ou nome vazio
    except Exception:
        log.warning("ensure_user_category falhou user=%s name=%r", user_id, name, exc_info=True)
