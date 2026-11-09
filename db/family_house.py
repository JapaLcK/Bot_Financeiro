"""db/family_house.py — Plano Família, PR 5: a casa (metas e orçamentos que pertencem ao GRUPO).

ÚNICO lugar que lê ou escreve family_goals/family_budgets (tests/test_casa_so_na_porta.py; exceção: a contagem da
prévia de dissolução). Qualquer membro ativo, titular incluso, lê e edita: o vínculo ativo, reconferido sob o lock do
grupo, é a fronteira de autorização. Toda query de item filtra `id = %s AND group_id = <grupo do chamador>`; ref de
outro grupo, inexistente ou malformado é o mesmo `item_not_found`. Nada aqui toca lançamento, conta ou caixinha.
Erros (FamilyError): not_member, coverage_required (o titular do grupo sem cobertura Pro: a casa fecha, o vínculo fica), item_not_found, edit_conflict, category_taken, insufficient, prazo_passado.
"""
from psycopg import errors as pgerr

from utils_date import today_tz

from .connection import get_conn
from .family import FamilyError, _lock_member_group
from .family_views import _REF, _primeiro_nome

# gasto do mês corrente (o primeiro lançamento num mês novo zera e soma)
_GASTO = "(case when gasto_mes = %(mes)s then gasto_cents else 0 end)"


META, ORC = "m", "o"  # prefixo do ref: metas e orçamentos têm sequências próprias, o ref sem tipo apagaria o item errado


def _id(ref: str, tipo: str) -> int:
    if ref[:1] != tipo or not _REF.fullmatch(ref[1:]):
        raise FamilyError("item_not_found")
    return int(ref[1:])


def _nomeia(dados: dict, ativos: set) -> dict:
    """`autor` = primeiro nome se quem criou é membro ATIVO deste grupo; senão None (saiu, removido, conta apagada).
    Roda DEPOIS de devolver a conexão: o nome pode pedir outra ao pool (get_auth_user) e o lock do grupo já caiu."""
    nomes: dict = {}
    for r in dados["metas"] + dados["orcamentos"]:
        uid = r.pop("created_by_user_id")
        if uid in ativos and uid not in nomes:
            nomes[uid] = _primeiro_nome(uid)
        r["autor"] = nomes[uid] if uid in ativos else None
    return dados


def _le(cur, gid: int) -> tuple[dict, set]:
    """(casa sem `autor`, user_ids dos membros ativos) sob o mesmo cursor; `_nomeia` fecha depois do commit."""
    mes = today_tz().replace(day=1)
    cur.execute("select id, nome, alvo_cents, guardado_cents, prazo, versao, created_by_user_id from family_goals "
                "where group_id = %s order by created_at, id", (gid,))
    metas = cur.fetchall()
    cur.execute(f"select id, categoria, limite_cents, {_GASTO} as gasto_cents, versao, created_by_user_id "
                "from family_budgets where group_id = %(g)s order by created_at, id", {"g": gid, "mes": mes})
    orcamentos = cur.fetchall()
    cur.execute("select user_id from family_members where group_id = %s and left_at is null", (gid,))
    return {"mes": mes, "metas": metas, "orcamentos": orcamentos}, {r["user_id"] for r in cur.fetchall()}


def _exige_cobertura(cur, owner_id: int) -> None:
    """A casa é do plano Pro do titular: sem ele, leitura e escrita (dele e dos membros) fecham; nada é apagado."""
    from core.services.family_access import titular_pro_no_lock  # lazy: family_access importa db.family
    if not titular_pro_no_lock(cur, owner_id):
        raise FamilyError("coverage_required")


def casa(uid: int) -> dict:
    """{"mes", "metas", "orcamentos"} (centavos) do grupo de `uid`, ou FamilyError("not_member")."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")  # vínculo e leituras: um snapshot só
        cur.execute("select m.group_id, g.owner_user_id from family_members m join family_groups g on g.id = m.group_id"
                    " where m.user_id = %s and m.left_at is null", (uid,))
        m = cur.fetchone()
        if not m:
            raise FamilyError("not_member")
        _exige_cobertura(cur, m["owner_user_id"])
        lido = _le(cur, m["group_id"])
    return _nomeia(*lido)


def _escreve(uid: int, fazer) -> dict:
    """Transação própria: lock do grupo como membro ativo (titular incluso), depois `fazer(cur, group_id)`.
    Lê a casa na MESMA transação, antes do commit; os nomes saem depois dele (`_primeiro_nome` não levanta),
    então a resposta não falha com a escrita gravada e nenhuma 2a conexão é pedida com o lock na mão."""
    with get_conn() as conn, conn.cursor() as cur:
        gid, owner_id = _lock_member_group(cur, uid, com_titular=True)
        _exige_cobertura(cur, owner_id)
        try:
            fazer(cur, gid)
        except pgerr.UniqueViolation:  # a corrida em si o lock serializa; o índice é a rede
            raise FamilyError("category_taken") from None
        lido = _le(cur, gid)
        conn.commit()
    return _nomeia(*lido)


def _um(cur, tabela: str, campos: str, item_id: int, gid: int) -> dict:
    cur.execute(f"select {campos} from {tabela} where id = %s and group_id = %s", (item_id, gid))
    row = cur.fetchone()
    if not row:
        raise FamilyError("item_not_found")
    return row


def _confere_versao(item: dict, versao: int) -> None:
    if item["versao"] != versao:
        raise FamilyError("edit_conflict")


def _prazo_ok(prazo, atual=None) -> None:
    if prazo is not None and prazo != atual and prazo < today_tz():
        raise FamilyError("prazo_passado")


def criar_meta(uid: int, nome: str, alvo_cents: int, prazo) -> dict:
    def fazer(cur, gid):
        _prazo_ok(prazo)
        cur.execute("insert into family_goals (group_id, created_by_user_id, nome, alvo_cents, prazo) "
                    "values (%s, %s, %s, %s, %s)", (gid, uid, nome, alvo_cents, prazo))
    return _escreve(uid, fazer)


def editar_meta(uid: int, ref: str, nome: str, alvo_cents: int, prazo, versao: int) -> dict:
    def fazer(cur, gid):
        i = _id(ref, META)
        meta = _um(cur, "family_goals", "versao, prazo", i, gid)
        _confere_versao(meta, versao)
        _prazo_ok(prazo, meta["prazo"])  # meta antiga com prazo vencido continua editável no nome e no alvo
        cur.execute("update family_goals set nome = %s, alvo_cents = %s, prazo = %s, versao = versao + 1 "
                    "where id = %s and group_id = %s", (nome, alvo_cents, prazo, i, gid))
    return _escreve(uid, fazer)


def apagar_meta(uid: int, ref: str) -> dict:
    def fazer(cur, gid):
        i = _id(ref, META)
        cur.execute("delete from family_goals where id = %s and group_id = %s", (i, gid))
        if cur.rowcount == 0:
            raise FamilyError("item_not_found")
    return _escreve(uid, fazer)


def mover_meta(uid: int, ref: str, delta_cents: int) -> dict:
    """Soma (ou subtrai, delta < 0) ao guardado, atômico; nunca deixa negativo (insufficient) e não mexe na versão."""
    def fazer(cur, gid):
        i = _id(ref, META)
        cur.execute("update family_goals set guardado_cents = guardado_cents + %s "
                    "where id = %s and group_id = %s and guardado_cents + %s >= 0",
                    (delta_cents, i, gid, delta_cents))
        if cur.rowcount == 0:
            _um(cur, "family_goals", "id", i, gid)  # não existe -> item_not_found
            raise FamilyError("insufficient")
    return _escreve(uid, fazer)


def criar_orcamento(uid: int, categoria: str, limite_cents: int) -> dict:
    def fazer(cur, gid):
        cur.execute("insert into family_budgets (group_id, created_by_user_id, categoria, limite_cents) "
                    "values (%s, %s, %s, %s)", (gid, uid, categoria, limite_cents))
    return _escreve(uid, fazer)


def editar_orcamento(uid: int, ref: str, categoria: str, limite_cents: int, versao: int) -> dict:
    def fazer(cur, gid):
        i = _id(ref, ORC)
        _confere_versao(_um(cur, "family_budgets", "versao", i, gid), versao)
        cur.execute("update family_budgets set categoria = %s, limite_cents = %s, versao = versao + 1 "
                    "where id = %s and group_id = %s", (categoria, limite_cents, i, gid))
    return _escreve(uid, fazer)


def apagar_orcamento(uid: int, ref: str) -> dict:
    def fazer(cur, gid):
        i = _id(ref, ORC)
        cur.execute("delete from family_budgets where id = %s and group_id = %s", (i, gid))
        if cur.rowcount == 0:
            raise FamilyError("item_not_found")
    return _escreve(uid, fazer)


def mover_orcamento(uid: int, ref: str, delta_cents: int) -> dict:
    """Gasto do mês: soma/subtrai no mês corrente (mês novo parte de 0); subtrair além do gasto é insufficient."""
    def fazer(cur, gid):
        i = _id(ref, ORC)
        cur.execute(f"update family_budgets set gasto_cents = {_GASTO} + %(d)s, gasto_mes = %(mes)s "
                    f"where id = %(id)s and group_id = %(g)s and {_GASTO} + %(d)s >= 0",
                    {"d": delta_cents, "mes": today_tz().replace(day=1), "id": i, "g": gid})
        if cur.rowcount == 0:
            _um(cur, "family_budgets", "id", i, gid)
            raise FamilyError("insufficient")
    return _escreve(uid, fazer)
