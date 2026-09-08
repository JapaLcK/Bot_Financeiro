"""
tests/test_billing_dunning.py — a regra "está bloqueado por inadimplência?".

A tabela de `core.services.billing_dunning.bloqueado_por_inadimplencia`, contra
Postgres real (quantas linhas: `len(CASOS)` — o número que estava escrito aqui
dizia 17 e eram 18; §2). Cada linha é uma população que o contrato do dono
nomeia, e a razão de a tabela não ter três linhas é que a maioria delas existe
para provar que o plano `free` (e os vizinhos dele) NÃO são tocados.

Os controles negativos declarados do grupo, com o resultado MEDIDO de cada um
(não o previsto — os dois divergiram, e o que vale é o medido):

  • `sources=("pix","admin")` → `sources=()` em `grant_vigente`:
    ficam vermelhas as linhas 12 (Pix vigente) e 13 (admin vigente); as
    linhas 1-11, 14, 16 e 17 continuam verdes.
  • remover a guarda de status (passo 2 de `bloqueado_por_inadimplencia`):
    ficam vermelhas SÓ as linhas 16a/16b/16c — a INVARIANTE. As linhas 3, 4 e
    5 continuam VERDES porque o relógio delas é NULL e a guarda 3 já as
    protege; foi por isso que a invariante virou três linhas (um status cada)
    em vez de uma só: com `active` sozinha, o controle mediria uma instância
    e não a categoria.

O controle POSITIVO do grupo são as linhas que esperam "passa"
(`sum(1 for c in CASOS if not c[-1])` — o número que estava escrito aqui dizia
13 e eram 14; §2): sem elas o grupo ficaria verde num código que bloqueia todo
mundo, que é pior que o bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta as _conta
from core.services import plan_service
from core.services.billing_dunning import (
    DUNNING_GRACE_DAYS,
    bloqueado_por_inadimplencia,
    dunning_block_enabled,
)
from db.connection import get_conn
from db.plan_grants import upsert_grant

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


def _grant(uid: int, source: str, *, dias_ini: int, dias_fim: int) -> None:
    upsert_grant(uid, source, f"{source}:{uid}", "pro",
                 AGORA + timedelta(days=dias_ini),
                 AGORA + timedelta(days=dias_fim), 1, f"evt_{source}_{uid}")


# ──────────────────────────────────────────────────────────────────────────────
# A tabela de casos (contagem: `len(CASOS)`, nunca escrita aqui — §2)
# ──────────────────────────────────────────────────────────────────────────────
# (nome, plan, last_payment_status, delta do relógio, grants, esperado)
_GRANT_VIGENTE = ("pix", -10, 355)
_GRANT_EXPIRADO = ("admin", -400, -10)

CASOS = [
    # 1-6: status que NÃO é dunning — o `free` e os vizinhos dele, intactos.
    ("01_active",              "pro",  "active",         None, [("stripe", -10, 20)], False),
    ("02_trialing",            "pro",  "trialing",       None, [("stripe", -1, 14)],  False),
    ("03_sem_status",          "free", "",               None, [],                    False),
    ("04_canceled",            "free", "canceled",       None, [],                    False),
    ("05_grandfathered",       "pro",  "grandfathered",  None, [],                    False),
    ("06_free_admin_expirado", "free", "canceled",       None, [_GRANT_EXPIRADO],     False),
    # 7-9: a carência de 7 dias e as duas fronteiras dela.
    ("07_past_due_1d",         "pro",  "past_due",       timedelta(days=-1),                    [("stripe", -30, 1)], False),
    ("08_past_due_6d23h",      "pro",  "past_due",       -timedelta(days=6, hours=23),          [("stripe", -30, 1)], False),
    ("09_past_due_7d1min",     "pro",  "past_due",       -timedelta(days=7, minutes=1),         [("stripe", -30, 1)], True),
    ("10_unpaid_8d",           "pro",  "unpaid",         timedelta(days=-8),                    [("stripe", -30, 1)], True),
    # 11: dunning sem relógio — nunca foi carimbada.
    ("11_past_due_sem_relogio", "pro", "past_due",       None, [("stripe", -30, 1)], False),
    # 12-14: direito EFETIVO por outro caminho (decisão 3). `legacy` não resgata.
    ("12_pix_vigente",         "pro",  "past_due",       timedelta(days=-30), [_GRANT_VIGENTE],                    False),
    ("13_admin_vigente",       "pro",  "past_due",       timedelta(days=-30), [("admin", -10, 355)],               False),
    ("14_legacy_vigente",      "pro",  "past_due",       timedelta(days=-30), [("legacy", -10, 355)],              True),
    # 16: a INVARIANTE, nas três populações em que o relógio pode ficar órfão
    # (`clear_past_due_since` que não rodou). Uma linha por status porque a
    # guarda que as protege é a MESMA — e é essa guarda que o controle negativo
    # 4 desliga. `active` sozinha não fecharia a categoria.
    ("16a_relogio_orfao_em_active",   "pro",  "active",   timedelta(days=-30), [("stripe", -10, 20)], False),
    ("16b_relogio_orfao_em_canceled", "free", "canceled", timedelta(days=-30), [],                    False),
    ("16c_relogio_orfao_em_free",     "free", "",         timedelta(days=-30), [],                    False),
    # 17: o `incomplete` (decisão 9).
    ("17_incomplete_8d",       "pro",  "incomplete",     timedelta(days=-8),  [("stripe", -30, 1)],  True),
]


@pytest.mark.parametrize("nome,plan,status,relogio,grants,esperado",
                         CASOS, ids=[c[0] for c in CASOS])
def test_tabela_de_bloqueio(user_id, nome, plan, status, relogio, grants, esperado):
    _conta(user_id, plan, AGORA + timedelta(days=20), status)
    _relogio(user_id, relogio)
    for source, ini, fim in grants:
        _grant(user_id, source, dias_ini=ini, dias_fim=fim)

    assert bloqueado_por_inadimplencia(user_id, AGORA) is esperado, nome


def test_15_allowlist_nunca_bloqueia(user_id, monkeypatch):
    """Linha 15: uid na allowlist de admin/teste passa mesmo com 30 dias de
    atraso e grant só de cartão. Reuso de `plan_service._ACCESS_ALLOWLIST`, a
    mesma que `trial_downsell.py` e `open_finance_trial_expiry.py` consultam."""
    _conta(user_id, "pro", AGORA + timedelta(days=20), "past_due")
    _relogio(user_id, timedelta(days=-30))
    _grant(user_id, "stripe", dias_ini=-30, dias_fim=1)

    # Sem a allowlist, esta MESMA conta bloqueia — é o que torna a asserção
    # seguinte não-tautológica.
    assert bloqueado_por_inadimplencia(user_id, AGORA) is True

    monkeypatch.setattr(plan_service, "_ACCESS_ALLOWLIST", {int(user_id)})
    assert bloqueado_por_inadimplencia(user_id, AGORA) is False


def test_flag_default_desligada(monkeypatch):
    """A flag nasce DESLIGADA (deploy seguro) e é lida do ambiente a cada
    chamada — sem redeploy, igual `paywall_enabled`."""
    monkeypatch.delenv("DUNNING_BLOCK_ENABLED", raising=False)
    assert dunning_block_enabled() is False
    for ligado in ("1", "true", "yes", "ON"):
        monkeypatch.setenv("DUNNING_BLOCK_ENABLED", ligado)
        assert dunning_block_enabled() is True
    monkeypatch.setenv("DUNNING_BLOCK_ENABLED", "0")
    assert dunning_block_enabled() is False


def test_carencia_e_sete_dias():
    """A carência é constante de módulo, não env: quem mudar o número muda a
    regra de produto, e as linhas 8/9 da tabela medem exatamente esta fronteira."""
    assert DUNNING_GRACE_DAYS == 7


def test_lista_de_tres_tem_uma_fonte_so():
    """O admin importa a lista daqui — não existe uma quarta cópia (§0.7)."""
    from core import admin_dashboard
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES

    assert admin_dashboard._PAST_DUE_PAYMENT_STATUSES is PAST_DUE_PAYMENT_STATUSES
    assert PAST_DUE_PAYMENT_STATUSES == ("past_due", "unpaid", "incomplete")
    # E o espelho SQL do painel continua decidindo igual (o teste de paridade
    # completo vive em tests/test_admin_users_panel.py).
    for s in PAST_DUE_PAYMENT_STATUSES:
        assert f"'{s}'" in admin_dashboard._ACCOUNT_STATUS_SQL


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


def test_backfill_do_relogio_nao_recarimba_a_cada_boot(user_id):
    """O backfill de `db/schema.py` roda UMA VEZ, na criação da coluna.

    O par simétrico do `where past_due_since is null` que ele tinha: conta com
    relógio LIMPO e `last_payment_status` ainda na lista dos três era
    recarimbada a cada boot de cada processo — e esse estado é NORMAL, é o que o
    `clear_past_due_since` do `invoice.paid` produz quando o
    `Subscription.retrieve` ainda devolve `past_due`. Ou seja: quem acabou de
    pagar ganhava relógio novo no boot seguinte.

    Controle negativo declarado: devolva o `add column if not exists` + o UPDATE
    solto (sem o `do $$ ... information_schema ...`) e este teste fica VERMELHO;
    a tabela de casos acima continua verde, porque ela carimba o relógio à mão.
    """
    from db import get_auth_user, init_db

    _conta(user_id, "pro", AGORA + timedelta(days=20), "past_due")
    _relogio(user_id, None)
    assert get_auth_user(user_id)["past_due_since"] is None

    init_db()                       # o boot seguinte, com a coluna já existindo

    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    assert get_auth_user(user_id)["past_due_since"] is None, "recarimbou no boot"


def test_backfill_do_relogio_carimba_inadimplente_ao_criar_a_coluna(user_id):
    """CONTROLE POSITIVO do backfill: uma vez só não é nenhuma vez.

    A decisão do dono é que TODO inadimplente existente ganhe sete dias a
    partir do deploy — então o statement tem de carimbar quando a coluna nasce.
    Aqui a coluna é DERRUBADA para reproduzir o estado pré-deploy; o `init_db`
    a recria e o teste devolve o schema ao normal por construção.
    """
    from db import get_auth_user, init_db
    from db_support import invalidate_auth_user_cache

    _conta(user_id, "pro", AGORA + timedelta(days=20), "unpaid")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("alter table auth_accounts drop column past_due_since")
        conn.commit()

    init_db()

    invalidate_auth_user_cache(user_id)
    assert get_auth_user(user_id)["past_due_since"] is not None, "backfill não carimbou"
