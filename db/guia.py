"""db/guia.py — o progresso do guia do /painel (`guia_painel`, #728).

O roteiro mora em `api/v2/guia.py` (`PASSOS`); aqui só o estado gravado e o motivo de o
passo 1 (o Saiu real) não estar disponível. Os carimbos só gravam uma vez: o 1º
`feito` de cada passo, `oferecido_em`, `dispensado_em` e `concluido_em` nunca são
reescritos ("reabrir" zera `dispensado_em` e, sem feitos, carimba a oferta).
"""
from __future__ import annotations

from .connection import get_conn

# A ação do POST → o SET dela. `%(passo)s` só entra no `feito`. O `visto` só carimba
# antes do 1º `feito` (o convite só existe sem nenhum): `oferecido_em`, quando existe, é
# anterior ou igual a todo carimbo de `feitos`, e as medianas da medição não ficam negativas.
# O `reabrir` (a Ajuda) carimba a oferta com a mesma guarda: quem abre pela Ajuda sem nunca
# ter visto o convite não o recebe depois.
# `clock_timestamp()`, não `now()`: `now()` é o início da transação, e um `feito` que começou
# antes de um `visto` concorrente comitar gravaria um carimbo anterior a `oferecido_em`. O
# UPDATE que esperou a trava reavalia o SET sobre a linha nova (READ COMMITTED), então o
# relógio sai depois do commit do outro e a guarda `feitos = '{}'` vê o `feito` já gravado.
_SET = {
    "visto": "oferecido_em = coalesce(oferecido_em, case when feitos = '{}'::jsonb then clock_timestamp() end)",
    "dispensar": "dispensado_em = coalesce(dispensado_em, clock_timestamp())",
    "reabrir": ("dispensado_em = null, oferecido_em = coalesce(oferecido_em,"
                " case when feitos = '{}'::jsonb then clock_timestamp() end)"),
    "feito": ("feitos = case when feitos ? %(passo)s::text then feitos"
              " else feitos || jsonb_build_object(%(passo)s::text, clock_timestamp()) end"),
}

_ERRO = {"error_recoverable", "needs_user_action", "item_missing"}

_LER = "select oferecido_em, dispensado_em, concluido_em, feitos from guia_painel where user_id = %s"


def ler(user_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_LER, (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def registrar(user_id: int, acao: str, passo: str | None, ids: list[str]) -> dict:
    """Aplica `acao` e conclui quando todos os `ids` estão feitos, numa transação.
    O UPDATE trava a linha: dois `feito` simultâneos não perdem carimbo."""
    p = {"uid": user_id, "passo": passo, "ids": ids}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into guia_painel (user_id) values (%(uid)s) on conflict do nothing", p)
        cur.execute(f"update guia_painel set {_SET[acao]} where user_id = %(uid)s", p)
        cur.execute("update guia_painel set concluido_em = clock_timestamp() where user_id = %(uid)s"
                    " and concluido_em is null and feitos ?& %(ids)s::text[]", p)
        cur.execute(_LER, (user_id,))
        return dict(cur.fetchone())


def motivo_resumo(user_id: int) -> str | None:
    """`None` = o passo 1 está disponível: Saiu > 0 no mês corrente ou no anterior,
    pela regra do Resumo (`resumo_do_mes`, com a janela do plano: o número que a tela
    mostra). Senão, por quê: alguma conexão sincronizando, alguma com erro, ou nada."""
    from utils_date import now_tz

    from .patrimonio import ler_conexoes
    from .resumo_mes import resumo_do_mes

    agora = now_tz()
    r = resumo_do_mes(user_id, agora.year, agora.month, agora)
    if r["saiu"] > 0 or (r["anterior"] and r["anterior"]["saiu"] > 0):
        return None
    # ponytail: lê as conexões de novo (o resumo_do_mes já leu, via contas_hoje); uma
    # consulta a mais por GET só quando falta dado. Medir se o GET pesar no SSE.
    with get_conn() as conn, conn.cursor() as cur:
        estados = set(ler_conexoes(cur, user_id)[1].values())
    if "updating" in estados:
        return "sincronizando"
    if estados & _ERRO:
        return "conexao_com_erro"
    return "sem_dados"
