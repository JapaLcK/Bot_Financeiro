"""
tests/test_billing_dunning.py — a INVARIANTE do relógio de inadimplência e o
vocabulário compartilhado de `core.services.billing_dunning`.

Este PR não bloqueia nada, então não há tabela de "está bloqueado?" para
testar. O que sobra e precisa de teste é a mecânica:

  • a invariante — `auth_accounts.past_due_since` não nulo SÓ existe em conta
    cujo `last_payment_status` está em `PAST_DUE_PAYMENT_STATUSES`. Ela é
    mantida na ESCRITA, e são DOIS os writers da coluna de status:
    `db_support.set_payment_status_impl` e o SQL cru de
    `core.admin_dashboard.set_account_plan`. Fechar só o primeiro foi o defeito
    que reprovou a v1 (§2: "achei um caso" ≠ "resolvi a categoria");
  • a lista de três status ter uma fonte só (§0.7);
  • `grant_vigente`, o predicado extraído de `billing_access`.

CONTROLES do grupo da invariante:

**Estes controles seguem a regra que a rodada 12 pagou** (ver
`docs/controles_declarados.md`): citam o PREDICADO ou a constante a injetar,
nomeiam só os testes VERMELHOS, e não trazem contagem de `passed`. Nome de
vermelho não envelhece; lista verde e contagem envelhecem sempre.

  • NEGATIVO 1 — devolva o `cur.execute` de `db_support.set_payment_status_impl`
    ao UPDATE de UMA coluna (`set last_payment_status = %s where user_id = %s`,
    sem o `case`):
      VERMELHO: test_invariante_set_payment_status[active] e [canceled].
  • NEGATIVO 2 — no `past_due_since = case` de
    `core.admin_dashboard.set_account_plan`, troque `then null` por
    `then past_due_since`:
      VERMELHO: test_invariante_admin_nao_deixa_relogio_orfao.
    Os dois writers são categorias SEPARADAS: o NEGATIVO 1 não reprova este
    teste e o NEGATIVO 2 não reprova os de `set_payment_status`. É essa
    independência que os controles medem, não a contagem.
    APAGAR o bloco inteiro NÃO serve de controle: deixa o `end,` da coluna
    anterior sem sucessor e o UPDATE vira erro de sintaxe, que reprova os dois
    testes do admin por motivo errado (medido antes de trocar a injeção).
  • POSITIVO — `test_admin_preserva_relogio_de_quem_segue_em_atraso` e o caso
    [past_due] do parametrize: os dois consertos RESTRINGEM, então sem um caso
    provando que o relógio LEGÍTIMO sobrevive o grupo passaria num código que
    zera o relógio sempre — pior que o bug, porque aí ninguém recebe lembrete.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta as _conta
from core.services.billing_dunning import (
    DUNNING_GRACE_DAYS,
    PAST_DUE_PAYMENT_STATUSES,
)
from db.connection import get_conn

AGORA = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _relogio(uid: int, delta: timedelta | None) -> None:
    """Carimba (ou zera) `past_due_since` relativo a AGORA."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = %s where user_id = %s",
                (AGORA + delta if delta is not None else None, uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _ler_relogio(uid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select past_due_since, last_payment_status"
                        "  from auth_accounts where user_id = %s", (uid,))
            return cur.fetchone()


# ──────────────────────────────────────────────────────────────────────────────
# A invariante, nos DOIS writers de `last_payment_status`.
#
# A categoria não é "quem chama set_payment_status" — é "quem escreve a coluna",
# em qualquer forma, inclusive SQL cru. A varredura que a fecha:
#   grep -rn "last_payment_status" --include="*.py" --include="*.sql" \
#        --exclude-dir=.venv .
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("novo_status,relogio_sobrevive", [
    ("active", False),          # saiu da lista → zera
    ("canceled", False),        # saiu da lista → zera
    ("past_due", True),         # continua na lista → PRESERVA (positivo)
])
def test_invariante_set_payment_status(user_id, novo_status, relogio_sobrevive):
    """`set_payment_status` zera o relógio no MESMO UPDATE quando o status sai
    de `PAST_DUE_PAYMENT_STATUSES` — e só nesse caso."""
    from db import set_payment_status

    _conta(user_id, "pro", AGORA + timedelta(days=20), "unpaid")
    _relogio(user_id, timedelta(days=-3))
    assert _ler_relogio(user_id)["past_due_since"] is not None

    set_payment_status(user_id, novo_status)

    row = _ler_relogio(user_id)
    assert row["last_payment_status"] == novo_status
    assert (row["past_due_since"] is not None) is relogio_sobrevive, row


def test_invariante_admin_nao_deixa_relogio_orfao(user_id):
    """O SEGUNDO writer: `set_account_plan` (drill-down do painel e
    /admin/grant-pro) move 'unpaid' para 'inactive' em SQL cru, sem passar por
    `set_payment_status`. Sem a linha `past_due_since = case ...` dele, o
    relógio ficava órfão — medido pelo Manager: `{'past_due_since':
    datetime(...), 'last_payment_status': 'inactive'}`.

    Órfão não é dado morto: o `invoice.payment_failed` do ciclo seguinte devolve
    o status para a lista, o `claim_past_due_since` vê `rowcount 0` e o relógio
    fica preso na data velha — a conta nasce fora da janela do lembrete e o
    lembrete de pagamento daquele ciclo não sai.
    """
    from core.admin_dashboard import set_account_plan

    _conta(user_id, "pro", AGORA + timedelta(days=20), "unpaid")
    _relogio(user_id, timedelta(days=-3))

    assert set_account_plan("pro", 12, user_id=user_id) is not None

    row = _ler_relogio(user_id)
    assert row["last_payment_status"] == "inactive", row
    assert row["past_due_since"] is None, f"orfao: {row}"


def test_admin_preserva_relogio_de_quem_segue_em_atraso(user_id):
    """CONTROLE POSITIVO do writer do admin: 'past_due' NÃO é status terminal,
    o CASE do SQL não o toca, e o relógio tem de SOBREVIVER. Sem este caso o
    conserto acima passaria num `past_due_since = null` incondicional — que
    apagaria o relógio de todo mundo a cada ajuste de plano pelo painel."""
    from core.admin_dashboard import set_account_plan

    _conta(user_id, "pro", AGORA + timedelta(days=20), "past_due")
    _relogio(user_id, timedelta(days=-3))

    assert set_account_plan("pro", 12, user_id=user_id) is not None

    row = _ler_relogio(user_id)
    assert row["last_payment_status"] == "past_due", row
    assert row["past_due_since"] == AGORA + timedelta(days=-3), row


# ──────────────────────────────────────────────────────────────────────────────

def test_lista_de_tres_tem_uma_fonte_so():
    """O admin importa a lista daqui, e o Python não tem uma segunda cópia
    (§0.7).

    Cópias LITERAIS em SQL, que este teste NÃO consegue amarrar por identidade,
    e por isso são nomeadas aqui: o espelho `_ACCOUNT_STATUS_SQL` do painel (o
    teste de paridade completo vive em tests/test_admin_users_panel.py) e o
    `in ('canceled', 'incomplete_expired', 'unpaid')` de
    `admin_dashboard.set_account_plan`, que é uma lista DIFERENTE (terminais +
    'unpaid') e não uma cópia desta. `db/schema.py` não tem cópia nenhuma — a
    versão do PR que tinha um backfill com os três status literais saiu junto
    com o backfill.
    """
    from core import admin_dashboard

    assert admin_dashboard._PAST_DUE_PAYMENT_STATUSES is PAST_DUE_PAYMENT_STATUSES
    assert PAST_DUE_PAYMENT_STATUSES == ("past_due", "unpaid", "incomplete")
    for s in PAST_DUE_PAYMENT_STATUSES:
        assert f"'{s}'" in admin_dashboard._ACCOUNT_STATUS_SQL


def test_janela_e_de_sete_dias():
    """7 dias é regra de produto (constante de módulo, não env). Quem mudar o
    número muda o dia do lembrete de pagamento E a janela de dedupe do e-mail
    de falha no webhook."""
    assert DUNNING_GRACE_DAYS == 7


def test_invariante_janela_menor_que_dedupe():
    """A largura da janela de candidatos do lembrete tem de ser ESTRITAMENTE
    MENOR que a janela de dedupe do envio.

    É o teto da largura. A janela existe larga para sobreviver a um tick
    perdido (restart, deploy, falha operacional atrasam muito mais que os 24 h
    nominais do `run_engagement_loop`), mas largura ≥ dedupe devolve o outro
    modo de falha: a conta continua elegível depois de o e-mail ter saído e o
    tick seguinte manda o SEGUNDO lembrete do mesmo ciclo.

    Este teste é o único lugar onde os dois números se veem — quem mexer num
    deles é obrigado a olhar o outro (§0.7). A conferência de que o consumidor
    usa mesmo estas constantes está em tests/test_payment_reminder.py
    (`test_janela`, `test_tick_inteiro_perdido_ainda_entrega` e
    `test_dois_ticks_na_janela_larga_mandam_um_so`).
    """
    from core.services.billing_dunning import (
        PAYMENT_REMINDER_DEDUPE_DAYS,
        PAYMENT_REMINDER_WINDOW_DAYS,
    )

    assert PAYMENT_REMINDER_WINDOW_DAYS < PAYMENT_REMINDER_DEDUPE_DAYS, (
        PAYMENT_REMINDER_WINDOW_DAYS, PAYMENT_REMINDER_DEDUPE_DAYS)
    # E a janela do lembrete começa DENTRO da carência, não depois dela.
    assert 1 <= PAYMENT_REMINDER_WINDOW_DAYS
    assert DUNNING_GRACE_DAYS - 1 >= 1


# ──────────────────────────────────────────────────────────────────────────────
# grant_vigente — o predicado extraído de billing_access (comportamento zero
# alterado). Roda em todo webhook de cobrança e na passada de 60 s do loop.
# ──────────────────────────────────────────────────────────────────────────────

def test_grant_vigente_janela_semiaberta_e_filtro_de_source():
    from core.services.billing_access import grant_vigente

    def g(source, ini, fim, status="active"):
        return {"source": source, "status": status, "plan_stored": "pro",
                "starts_at": AGORA + ini, "ends_at": AGORA + fim}

    ativo = [g("pix", timedelta(days=-1), timedelta(days=1))]
    assert grant_vigente(ativo, AGORA) is True
    assert grant_vigente(ativo, AGORA, sources=("pix",)) is True
    assert grant_vigente(ativo, AGORA, sources=("admin",)) is False
    assert grant_vigente(ativo, AGORA, sources=("pix", "admin")) is True
    # `starts_at` inclusivo, `ends_at` EXCLUSIVO — igual ao inline original.
    borda = [g("pix", timedelta(0), timedelta(days=1))]
    assert grant_vigente(borda, AGORA) is True
    fim = [g("pix", timedelta(days=-1), timedelta(0))]
    assert grant_vigente(fim, AGORA) is False
    # Revogado não vale, mesmo dentro da janela.
    assert grant_vigente([g("pix", timedelta(days=-1), timedelta(days=1),
                            status="revoked")], AGORA) is False
    assert grant_vigente([], AGORA) is False
