"""`GET /api/v2/categorias`: o catálogo do usuário, `{chave, nome}`.

`chave` é a de casamento (`cat_norm_sql`, a mesma do `categoria` de cada lançamento e do
filtro `categoria` de `/lancamentos`); `nome` é a grafia que vence entre as gêmeas
(`CAT_META_SQL`, o desempate do donut). Semeia as canônicas como o `/categories` do /app.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from db.categories import ensure_user_categories_seeded
from db.connection import CAT_META_SQL, get_conn

router = APIRouter()


class Categoria(BaseModel):
    chave: str
    nome: str


class Categorias(BaseModel):
    categorias: list[Categoria]


@router.get("/categorias", response_model=Categorias)
def categorias(uid: int = Depends(usuario_atual)) -> Categorias:
    ensure_user_categories_seeded(uid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select cat as chave, name as nome from ({CAT_META_SQL}) m order by name", (uid,))
        return Categorias(categorias=cur.fetchall())
