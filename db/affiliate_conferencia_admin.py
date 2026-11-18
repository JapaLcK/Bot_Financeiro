"""
db/affiliate_conferencia_admin.py — comissões em conferência na Stripe (A5), vistas e decididas pelo admin (#1538).

Nada do indicado sai daqui para a tela: nem fatura, nem pessoa. `acoes` é a regra única de quem pode o quê: a
lista leva o que ela devolve, e cada decisão a repete sob a trava da comissão. Toda decisão compara a versão que
o admin viu (instante da marca, motivo e valor): marca nova do webhook, volta do worker, revisão parcial, outro
admin ou duplo clique dão `ConferenciaMudou`, e nada é gravado. "Continua marcada" é a única guarda da comissão que
nasce marcada: a versão dela é (None, None) antes e depois de a Stripe liberar. O evento sai na mesma transação do efeito: uma decisão, um evento.
"""
from .affiliate_eventos import registrar_evento
from .affiliate_reviews import PENDENCIA_ABERTA_SQL, SEM_MARCA, PendenciaMudou
from .affiliate_reviews_manutencao import MARCADA_DESDE_SQL
from .affiliates import _estornar
from .connection import get_conn

# O motivo só vale com tentativa: toda marca zera `tried_at` e não zera o motivo, que seria o da marca anterior.
_COLUNAS = f"""
c.id, a.code, c.amount_cents, c.status, c.payout_id, p.status as payout_status,
       c.payment_check_pending as marcada,
       c.payment_check_marked_at as marked_at,
       {MARCADA_DESDE_SQL.format(c="c")} as marked_on,
       c.payment_check_tried_at as tried_at,
       case when c.payment_check_tried_at is not null then c.payment_check_reason end as reason,
       {PENDENCIA_ABERTA_SQL.format(c="c")} as in_review,
       coalesce(left(c.stripe_invoice_id, 4) <> 'pix:', false) as has_stripe_invoice,
       exists (select 1 from affiliate_refund_reviews v
                where v.commission_id = c.id and v.status = 'recovery') as in_recovery
"""
_DE = """
  from affiliate_commissions c
  join affiliates a on a.id = c.affiliate_id
  left join affiliate_payouts p on p.id = c.payout_id
"""
_LINHA = f"select {_COLUNAS} {_DE}"
# Lista fechada: motivo novo, ou disputa (o worker resolve quando ela fecha), não conclui.
_CONCLUIVEIS = {"sem_cobranca", "forma_inesperada", "stripe_400", "stripe_404"}
_MUDOU = "A comissão mudou: recarregue antes de decidir."


class ConferenciaMudou(PendenciaMudou):
    """A comissão não está como o admin a viu, ou a ação não vale para ela: a rota responde 409."""


def _porque_nao_decide(linha: dict) -> str | None:
    """Concluir e Liberar decidem só com a conferência tentada e um motivo da lista. None = pode."""
    if linha["tried_at"] is None:
        return "Aguarde a conferência na Stripe antes de decidir."
    if linha["reason"] == "disputa_aberta":
        return "Disputa aberta: a conferência fecha sozinha quando a disputa terminar."
    if linha["reason"] == "stripe_403":
        return "A chave da Stripe não tem permissão para conferir: corrija a chave, o worker reconfere."
    if linha["reason"] not in _CONCLUIVEIS:
        return "Motivo sem regra de decisão: confira na Stripe."
    return None


def acoes(linha: dict) -> dict[str, str | None]:
    """As ações da linha, na ordem da tela. None = pode; texto = por que não. Ação ausente não se aplica."""
    if linha["status"] == "paid":
        return {"conclude": _porque_nao_decide(linha),
                "reverse": "Comissão já paga: reembolso confirmado vira recuperação."}
    if linha["status"] != "pending":
        return {}
    no_saque = (f"A comissão está no saque {linha['payout_id']}, ainda aberto. Rejeite o saque se o Pix não saiu (ela"
                " volta marcada e pode ser estornada) ou registre \"Pagamento já realizado\" se saiu.")
    return {"release": (no_saque if linha["payout_id"] is not None
                        else "Em revisão de reembolso: decida a pendência antes." if linha["in_review"]
                        else _porque_nao_decide(linha)),
            "reverse": None if linha["payout_id"] is None else no_saque}


def listar_conferencia(limite: int = 100) -> list[dict]:
    """As marcadas, a marca mais antiga primeiro. O total sai de `resumo_da_conferencia`, mesmo filtro."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_LINHA + " where c.payment_check_pending"
                    f" order by {MARCADA_DESDE_SQL.format(c='c')}, c.id limit %s", (int(limite),))
        return [{**linha, "actions": acoes(linha)} for linha in cur.fetchall()]


def _decidir(cur, commission_id: int, acao: str, marcada_em, motivo, valor_cents, trava: bool) -> dict | None:
    """A linha, se a decisão vale contra a versão vista; None = comissão inexistente.

    Trava antes, leitura depois, num comando novo: a pendência, a recuperação e o saque que o estado anterior grava
    são os do commit que a trava esperou, e não os do instante em que o pedido chegou."""
    if trava:
        cur.execute("select id from affiliate_commissions where id = %s for update", (int(commission_id),))
    cur.execute(f"select c.stripe_invoice_id as fatura, {_COLUNAS} {_DE} where c.id = %s", (int(commission_id),))
    linha = cur.fetchone()
    if linha is None:
        return None
    if (not linha["marcada"] or linha["marked_at"] != marcada_em or linha["reason"] != motivo
            or linha["amount_cents"] != valor_cents):
        raise ConferenciaMudou(_MUDOU)
    porque = acoes(linha).get(acao, "Esta ação não vale para esta comissão.")
    if porque:
        raise ConferenciaMudou(porque)
    return linha


def conferir_decisao(commission_id: int, acao: str, marcada_em, motivo, valor_cents) -> dict | None:
    """A mesma guarda sem trava, antes de consultar a Stripe. Leva a fatura: só para a consulta, nunca para a tela."""
    with get_conn() as conn, conn.cursor() as cur:
        return _decidir(cur, commission_id, acao, marcada_em, motivo, valor_cents, trava=False)


def _estado(linha: dict) -> dict:
    """Chaves fixas e sem nada do indicado; datas em texto, como o jsonb guarda."""
    def data(v):
        return v and v.isoformat()

    return {"status": linha["status"], "payout_id": linha["payout_id"], "payout_status": linha["payout_status"],
            "marcada": linha["marcada"], "motivo": linha["reason"], "marcada_em": data(linha["marked_at"]),
            "tentada_em": data(linha["tried_at"]), "valor_cents": linha["amount_cents"],
            "em_revisao": linha["in_review"], "recuperacao_aberta": linha["in_recovery"]}


def _registrar(cur, antes: dict, acao: str, revisor: str, justificativa: str, evidencia: str, depois_cents: int):
    cur.execute(_LINHA + " where c.id = %s", (antes["id"],))
    registrar_evento(cur, None, acao, revisor, antes=antes["amount_cents"], depois=depois_cents,
                     referencia=evidencia, justificativa=justificativa, comissao=antes["id"], codigo=antes["code"],
                     estado_antes=_estado(antes), estado_depois=_estado(cur.fetchone()))


def estornar_a_mao(commission_id: int, revisor: str, *, justificativa: str, evidencia: str, marcada_em,
                   motivo, valor_cents: int) -> bool:
    """Estornar (P2). Sem Stripe. No saque aberto recusa e não muda nada: a marca segura o saque (D11)."""
    with get_conn() as conn, conn.cursor() as cur:
        linha = _decidir(cur, commission_id, "reverse", marcada_em, motivo, valor_cents, trava=True)
        if linha is None:
            return False
        if not _estornar(cur, commission_id, revisor):  # a trava acima já viu pendente e solta
            raise ConferenciaMudou(_MUDOU)
        _registrar(cur, linha, "check_reversed", revisor, justificativa, evidencia, 0)
        conn.commit()
    return True


def concluir_a_mao(commission_id: int, revisor: str, *, justificativa: str, evidencia: str, marcada_em,
                   motivo, valor_cents: int) -> bool:
    """Concluir conferência da comissão já paga (P6): só tira a marca. Saldo, saque, status, valor e
    recuperação ficam como estão."""
    with get_conn() as conn, conn.cursor() as cur:
        linha = _decidir(cur, commission_id, "conclude", marcada_em, motivo, valor_cents, trava=True)
        if linha is None:
            return False
        cur.execute(f"update affiliate_commissions set {SEM_MARCA} where id = %s and status = 'paid'",
                    (int(commission_id),))
        if cur.rowcount != 1:
            raise ConferenciaMudou(_MUDOU)
        _registrar(cur, linha, "check_concluded", revisor, justificativa, evidencia, linha["amount_cents"])
        conn.commit()
    return True


def liberar_a_mao(commission_id: int, revisor: str, *, justificativa: str, evidencia: str, marcada_em,
                  motivo, valor_cents: int) -> bool:
    """Liberar (#1538): a pendente em conferência entra no saldo disponível. Tira a marca; valor e status não mudam."""
    with get_conn() as conn, conn.cursor() as cur:
        linha = _decidir(cur, commission_id, "release", marcada_em, motivo, valor_cents, trava=True)
        if linha is None:
            return False
        cur.execute(f"update affiliate_commissions set {SEM_MARCA} where id = %s and status = 'pending'"
                    " and payout_id is null", (int(commission_id),))
        if cur.rowcount != 1:
            raise ConferenciaMudou(_MUDOU)
        _registrar(cur, linha, "check_released", revisor, justificativa, evidencia, linha["amount_cents"])
        conn.commit()
    return True


def fatura_da_comissao(commission_id: int) -> str | None:
    """O id da fatura Stripe, só para o redirecionamento do admin. Comissão Pix (`pix:`) ou sem fatura: None."""
    with get_conn() as conn:
        row = conn.execute("select stripe_invoice_id as f from affiliate_commissions where id = %s",
                           (int(commission_id),)).fetchone()
    f = row and row["f"]
    return f if f and not f.startswith("pix:") else None


def comissoes_liberadas(ids: list[int]) -> set[int]:
    """As que o admin liberou à mão: a conferência sem confirmação da Stripe não as marca de novo."""
    with get_conn() as conn:
        rows = conn.execute("select distinct commission_id from affiliate_refund_review_events"
                            " where action = 'check_released' and commission_id = any(%s)",
                            ([int(i) for i in ids],)).fetchall()
    return {r["commission_id"] for r in rows}
