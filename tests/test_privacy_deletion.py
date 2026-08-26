"""Exclusão de conta: o que some, e o resíduo que sobrava sem função.

`plan_trials` sobrevive de propósito — é keyed por `phone_hash` e segura a
regra de 30 dias de teste por telefone, na vida. Apagar devolveria um trial
novo a cada conta recriada com o mesmo número.

O `user_id` da linha, porém, era resíduo: só é escrito no insert
(`db/plans.py`) e nunca lido — as duas consultas de lá casam por `phone_hash`.
Deixá-lo mantinha um identificador de conta apagada sem função nenhuma, contra
o que a `/privacy` diz sobre eliminar os dados vinculados à conta.

Controle negativo (medido): tirando o `update plan_trials set user_id = null`
de `db/privacy.py`, o primeiro teste fica vermelho. O segundo é o positivo — a
trava do trial tem que continuar de pé, senão "consertar" viraria apagar a
linha e devolver trial infinito.
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
    um teste de 30 dias novo a cada conta recriada com o mesmo número."""
    phone_hash = _trial_para(user_id)
    try:
        antes = _linha(phone_hash)["started_at"]

        delete_user_data(user_id)

        depois = _linha(phone_hash)
        assert depois is not None, "a linha sumiu: o telefone ganharia um trial novo"
        assert depois["started_at"] == antes, "a data de início do teste foi reescrita"
    finally:
        _limpa(phone_hash)
