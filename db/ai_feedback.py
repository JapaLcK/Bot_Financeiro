"""db/ai_feedback.py — nota 👍/👎 do usuário numa resposta do chat IA (`ai_messages.feedback`),
com o motivo do 👎 (`feedback_motivo`) e o comentário do "Outro" (`feedback_texto`)."""
from __future__ import annotations

from typing import Optional

from .connection import get_conn

# Fonte única dos motivos (chave -> rótulo). A borda (`FeedbackBody`) deriva o Literal daqui.
MOTIVOS = {
    "numero_errado": "Número errado",
    "nao_entendeu": "Não entendeu",
    "faltou_card": "Faltou card",
    "outro": "Outro",
}


def set_feedback(user_id: int, message_id: int, nota: Optional[int],
                 motivo: Optional[str] = None, texto: Optional[str] = None) -> bool:
    """Grava o ESTADO INTEIRO do feedback numa resposta DO USUÁRIO (nota, motivo e
    texto juntos): gravar 👍, None ou 👎 sem motivo zera motivo e texto, então quem
    chama só precisa passar o que quer ver gravado. False quando a linha não
    existe, é de outro usuário ou não é `assistant`: o chamador não distingue os três."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ai_messages set feedback = %s, feedback_motivo = %s, feedback_texto = %s"
            " where id = %s and user_id = %s and role = 'assistant'",
            (nota, motivo, texto, int(message_id), int(user_id)),
        )
        ok = cur.rowcount == 1
        conn.commit()
        return ok


# As DUAS funções abaixo são a ÚNICA leitura entre usuários deste módulo (CLAUDE.md §0:
# toda query filtra por `user_id`). Existem só para a tela do dono (`/admin/feedback`),
# que lê a nota de todo mundo para melhorar o Piggy. O único `user_id` que cruza a
# fronteira é o de `listar_negativos`, para a auditoria; a rota o remove antes de responder.
_BRT = "America/Sao_Paulo"


def resumo_semanal(semanas: int = 12) -> list[dict]:
    """Por semana (segunda a domingo em BRT, a mais nova primeiro): 👍, 👎, 👎 por motivo
    e 👎 sem motivo (`negativos == sum(por_motivo) + outros_motivos + sem_motivo`).
    A janela é de `semanas` semanas de CALENDÁRIO (a corrente mais as `semanas - 1` anteriores),
    não as `semanas` últimas com nota: semana sem nenhuma nota é OMITIDA, não vem zerada."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local statement_timeout = '5s'")
        cur.execute(
            f"""
            select date_trunc('week', created_at at time zone '{_BRT}')::date as semana,
                   feedback, feedback_motivo as motivo, count(*) as n
            from ai_messages
            where role = 'assistant' and feedback is not null
              and (created_at at time zone '{_BRT}')
                  >= date_trunc('week', now() at time zone '{_BRT}') - make_interval(weeks => %s)
            group by 1, 2, 3
            """,
            (int(semanas) - 1,),
        )
        rows = cur.fetchall() or []
        conn.commit()
    por_semana: dict = {}
    for r in rows:
        s = por_semana.setdefault(r["semana"], {"semana": r["semana"], "positivos": 0,
                                                "negativos": 0, "sem_motivo": 0, "outros_motivos": 0,
                                                "por_motivo": {}})
        if r["feedback"] == 1:
            s["positivos"] += r["n"]
        elif r["feedback"] == -1:
            s["negativos"] += r["n"]
            if r["motivo"] is None:
                s["sem_motivo"] += r["n"]
            elif r["motivo"] in MOTIVOS:
                s["por_motivo"][r["motivo"]] = s["por_motivo"].get(r["motivo"], 0) + r["n"]
            else:  # sem CHECK no banco: chave fora de MOTIVOS não some, vira `outros_motivos`
                s["outros_motivos"] += r["n"]
    return sorted(por_semana.values(), key=lambda s: s["semana"], reverse=True)


def listar_negativos(limit: int, offset: int) -> dict:
    """Os 👎, do mais novo ao mais antigo: `{"total": n, "itens": [...]}`. Cada item traz
    id, created_at, pergunta, resposta, motivo, rotulo, texto e o `user_id` INTERNO (a rota
    o tira). A pergunta é a última mensagem `user` DO MESMO usuário antes da resposta."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local statement_timeout = '5s'")
        cur.execute("select count(*) as n from ai_messages where role = 'assistant' and feedback = -1")
        total = int(cur.fetchone()["n"])
        cur.execute(
            """
            select m.id, m.user_id, m.created_at, m.content as resposta,
                   m.feedback_motivo as motivo, m.feedback_texto as texto,
                   (select u.content from ai_messages u
                     where u.user_id = m.user_id and u.role = 'user'
                       and (u.created_at, u.id) < (m.created_at, m.id)
                     order by u.created_at desc, u.id desc limit 1) as pergunta
            from ai_messages m
            where m.role = 'assistant' and m.feedback = -1
            order by m.created_at desc, m.id desc
            limit %s offset %s
            """,
            (int(limit), int(offset)),
        )
        itens = [dict(r) for r in cur.fetchall() or []]
        conn.commit()
    for it in itens:
        it["rotulo"] = MOTIVOS.get(it["motivo"])
    return {"total": total, "itens": itens}
