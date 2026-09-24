"""Exclusão de conta: o que some, e o resíduo que sobrava sem função.

`plan_trials` sobrevive de propósito — é keyed por `phone_hash` e segura a
regra de 15 dias de teste por telefone, na vida. Apagar devolveria um trial
novo a cada conta recriada com o mesmo número.

O `user_id` da linha, porém, era resíduo: só é escrito no insert
(`db/plans.py`) e nunca lido — as duas consultas de lá casam por `phone_hash`.
Deixá-lo mantinha um identificador de conta apagada sem função nenhuma, contra
o que a `/privacy` diz sobre eliminar os dados vinculados à conta.

Quem desvincula é o BANCO, por uma FK `on delete set null`
(`db/schema_repairs.py::ensure_plan_trials_user_fk`), e não um UPDATE no job de
exclusão: o UPDATE perde a corrida com um `claim_trial_for_user` (webhook do
checkout) que insira depois dele e commite depois, e a varredura pós-commit
nunca revisita `plan_trials`.

Controle negativo (medido): trocando a FK para CASCADE — que é o que o
`repair_user_fk_cascades` faria se `plan_trials` não estivesse na lista de
SET NULL — o teste da trava fica vermelho. Trocando para `no action`, o do
desvínculo fica vermelho. Um controle para cada metade.
"""
import uuid

import db
from db.connection import get_conn
from db.privacy import delete_user_data


def _trial_para(user_id: int) -> str:
    """Registra a queima do trial de um telefone, como o webhook faz."""
    phone_hash = f"ph_test_{uuid.uuid4().hex}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into plan_trials (phone_hash, user_id, started_at, model_version) "
                "values (%s, %s, now(), 2)",
                (phone_hash, int(user_id)),
            )
        conn.commit()
    return phone_hash


def _linha(phone_hash: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select phone_hash, user_id, started_at from plan_trials where phone_hash = %s",
                (phone_hash,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _limpa(phone_hash: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from plan_trials where phone_hash = %s", (phone_hash,))
        conn.commit()


def test_exclusao_desvincula_o_trial_da_conta_apagada(user_id):
    phone_hash = _trial_para(user_id)
    try:
        assert _linha(phone_hash)["user_id"] == user_id   # pré-condição

        delete_user_data(user_id)

        row = _linha(phone_hash)
        assert row is not None, "a trava do trial não podia sumir"
        assert row["user_id"] is None, (
            "o user_id de uma conta apagada continuou na linha — identificador "
            "residual, sem função, que a /privacy diz eliminar"
        )
    finally:
        _limpa(phone_hash)


def test_exclusao_preserva_a_trava_de_um_trial_por_telefone(user_id):
    """Positivo: sem ele, apagar a linha inteira passaria neste grupo — e daria
    um teste de 15 dias novo a cada conta recriada com o mesmo número."""
    phone_hash = _trial_para(user_id)
    try:
        antes = _linha(phone_hash)["started_at"]

        delete_user_data(user_id)

        depois = _linha(phone_hash)
        assert depois is not None, "a linha sumiu: o telefone ganharia um trial novo"
        assert depois["started_at"] == antes, "a data de início do teste foi reescrita"
    finally:
        _limpa(phone_hash)


def test_fk_da_trava_e_set_null_e_nao_cascade():
    """A FK existe e é SET NULL.

    CASCADE aqui apagaria a linha na exclusão da conta e devolveria 15 dias de
    teste a cada conta recriada com o mesmo telefone. É o erro que o
    `repair_user_fk_cascades` cometeria sozinho se `plan_trials` não estivesse
    em `_USER_FK_SET_NULL_TABLES` — este teste é o que segura as duas pontas
    juntas (a FK criada e a lista que a preserva).
    """
    from db.schema_repairs import _USER_FK_SET_NULL_TABLES

    assert "plan_trials" in _USER_FK_SET_NULL_TABLES

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select confdeltype from pg_constraint
                 where contype = 'f'
                   and conrelid = 'plan_trials'::regclass
                   and confrelid = 'users'::regclass
                """
            )
            row = cur.fetchone()
        conn.commit()

    assert row is not None, "a FK de plan_trials.user_id não foi criada"
    assert row["confdeltype"] == "n", (
        f"on delete é {row['confdeltype']!r}, esperado 'n' (SET NULL) — "
        "'c' (CASCADE) apagaria a trava do trial"
    )


def test_criar_a_fk_e_idempotente():
    """A segunda chamada não tenta o ALTER de novo.

    É a metade da corrida entre instâncias que dá para medir aqui: o
    `duplicate_object` que abortaria o init da segunda instância só aparece se a
    checagem de catálogo não vir a constraint já criada. A outra metade — as
    duas passando pela checagem ao mesmo tempo — é fechada pelo
    `pg_advisory_lock` em `db/schema.py`, e não por este teste.
    """
    from db.schema_repairs import ensure_plan_trials_user_fk

    with get_conn() as conn:
        with conn.cursor() as cur:
            assert ensure_plan_trials_user_fk(cur) is False, (
                "a FK já existe (o init_db criou) — a função tentou criar de novo"
            )
        conn.commit()


def test_fk_da_trava_esta_validada_e_indexada():
    """Duas coisas que o `create constraint` sozinho não dá.

    - VALIDADA: a constraint nasce NOT VALID (para fechar o vão entre a limpeza
      dos órfãos e o ALTER, com o container velho ainda atendendo webhooks) e
      só depois é validada. Parar no NOT VALID protegeria escrita nova mas
      deixaria órfão antigo passando pela verificação.
    - INDEXADA: o Postgres NÃO cria índice no lado que REFERENCIA. Sem ele, todo
      `delete from users` varre a plan_trials inteira — que cresce para sempre.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select convalidated from pg_constraint
                 where contype = 'f'
                   and conrelid = 'plan_trials'::regclass
                   and confrelid = 'users'::regclass
                """
            )
            fk = cur.fetchone()
            cur.execute(
                """
                select indexdef from pg_indexes
                 where tablename = 'plan_trials'
                   and indexdef ilike '%%(user_id)%%'
                """
            )
            idx = cur.fetchall()
        conn.commit()

    assert fk is not None and fk["convalidated"], "a FK ficou NOT VALID"
    assert idx, "sem índice em plan_trials(user_id): cada exclusão varre a tabela inteira"


def test_exclusao_apaga_pockets_e_com_eles_a_lapide_do_vinculo_of(user_id):
    """LGPD: a lápide do vínculo OF (`of_tombstone_connection_id` /
    `of_tombstone_provider_id`, db/schema.py) guarda o id do investimento do
    usuário no PROVEDOR. Ela é COLUNA de `pockets`, então morre com a linha — mas
    isso só vale enquanto `pockets` estiver na lista de exclusão
    (`db/privacy.py`), e nenhum teste media essa linha da lista.

    O caminho do `reset_user_data` já é medido por `tests/test_account_reset.py`
    (`_TABELAS_SIMPLES` inclui `pockets`); este cobre o da exclusão de conta."""
    _, pocket_id, _ = db.create_pocket(user_id, "Viagem", interest_enabled=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pockets set of_tombstone_provider_id='inv-do-provedor' "
                "where id=%s and user_id=%s",
                (pocket_id, user_id),
            )
        conn.commit()

    delete_user_data(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from pockets where user_id=%s", (user_id,))
            assert cur.fetchone()["n"] == 0, "a caixinha (e a lápide dentro dela) tinha que sumir"


def _tetos(identifiers: list[str]) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select identifier from auth_rate_limits where identifier = any(%s)", (identifiers,)
        )
        return {r["identifier"] for r in cur.fetchall()}


def test_exclusao_apaga_o_teto_por_conta_e_so_o_dela(user_id):
    """O `mfa-verify` grava `user:<id>` em auth_rate_limits, sem TTL: sobraria
    para sempre. O vizinho `user:<id>0` prova casamento exato (nem LIKE, nem
    outra conta)."""
    minha, outra = f"user:{user_id}", f"user:{user_id}0"
    with get_conn() as conn, conn.cursor() as cur:
        for ident in (minha, outra):
            cur.execute(
                "insert into auth_rate_limits (bucket, identifier, window_started_at, attempts, updated_at) "
                "values ('mfa-verify', %s, now(), 1, now())",
                (ident,),
            )
        conn.commit()
    try:
        delete_user_data(user_id)
        assert _tetos([minha, outra]) == {outra}
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from auth_rate_limits where identifier = any(%s)", ([minha, outra],))
            conn.commit()
