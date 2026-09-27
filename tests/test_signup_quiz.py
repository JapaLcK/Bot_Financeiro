"""Resultado do quiz de venda — parse do cookie, gravação na conta, CHECK e fonte única.

A conversa inteira (cookie → cadastro real → colunas) está em
`tests/test_signup_quiz_cadastro.py`; o reset, em `tests/test_account_reset.py`.
"""
import io
import json
import re
import zipfile
from pathlib import Path

import psycopg
import pytest

from db.connection import get_conn
from db.privacy import build_user_export_zip
from db.signup_quiz import PERFIS, QUIZ_V1, parse_quiz_cookie, record_signup_quiz

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("valor", [
    "v1.admin.acdbd", "v1.divid\x00as", "V1.DIVIDAS", "v2.dividas.acdbd",
    "v1.dividas.acdbd.x", pytest.param("v1.dividas." + "a" * 5000, id="longo"), "", None,
])
def test_parse_recusa_o_que_nao_e_o_formato(valor):
    assert parse_quiz_cookie(valor) is None


@pytest.mark.parametrize("valor", ["v1.dividas", "v1.dividas.eaaaa", "v1.dividas.aaaaf", "v1.dividas.abc"])
def test_parse_perfil_valido_com_letras_ruins_e_parcial(valor):
    assert parse_quiz_cookie(valor) == ("dividas", None)


def test_parse_completo():
    assert parse_quiz_cookie("v1.dividas.acdbd") == ("dividas", {
        "renda": "salario", "fim_do_mes": "zero_a_zero", "cartao": "perdeu_a_conta",
        "mil_reais": "reserva", "objetivo": "sair_das_dividas",
    })
    assert parse_quiz_cookie("v1.autonomo.dddde")[1]["objetivo"] == "organizar_renda"


def _conta(uid: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into auth_accounts (user_id, email, password_hash) values (%s, %s, 'x')",
                    (uid, f"quiz-{uid}@example.com"))
        conn.commit()


def _colunas(uid: int) -> tuple:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select dashboard_profile, signup_quiz from auth_accounts where user_id = %s", (uid,))
        linha = cur.fetchone()
        conn.commit()
    return linha["dashboard_profile"], linha["signup_quiz"]


def test_record_grava_uma_vez_e_isola_o_vizinho(user_id):
    import db
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    _conta(user_id)
    _conta(vizinho)
    respostas = parse_quiz_cookie("v1.dividas.acdbd")[1]

    assert record_signup_quiz(user_id, "dividas", respostas) is True
    assert _colunas(user_id) == ("dividas", {"versao": 1, "respostas": respostas})
    # 2ª gravação não sobrescreve (cookie sobrando, outra aba).
    assert record_signup_quiz(user_id, "investir", None) is False
    assert _colunas(user_id)[0] == "dividas"
    assert _colunas(vizinho) == (None, None)


def test_record_parcial_grava_respostas_null_e_nao_null(user_id):
    _conta(user_id)
    assert record_signup_quiz(user_id, "investir", None) is True
    assert _colunas(user_id) == ("investir", {"versao": 1, "respostas": None})


def test_check_do_banco_barra_perfil_fora_da_lista(user_id):
    _conta(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("update auth_accounts set dashboard_profile = 'admin' where user_id = %s", (user_id,))
        conn.rollback()
        cur.execute("update auth_accounts set dashboard_profile = 'dividas' where user_id = %s", (user_id,))
        conn.commit()
    assert _colunas(user_id)[0] == "dividas"


def test_init_db_de_novo_nao_levanta_e_mantem_o_check():
    from db.schema import init_db
    init_db()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from pg_constraint where conname = 'auth_accounts_dashboard_profile_valido'")
        assert cur.fetchone() is not None
        conn.commit()


def test_export_traz_as_duas_colunas(user_id):
    _conta(user_id)
    record_signup_quiz(user_id, "dividas", None)
    with zipfile.ZipFile(io.BytesIO(build_user_export_zip(user_id))) as zf:
        conta = json.loads(zf.read("dados.json"))["dados"]["conta_login"][0]
    assert conta["dashboard_profile"] == "dividas"
    assert conta["signup_quiz"] == {"versao": 1, "respostas": None}


# ── fonte única: o JS e o painel v2 repetem a lista ──────────────────────────

def test_js_do_quiz_bate_com_o_python():
    js = (RAIZ / "frontend" / "quiz-resultado.js").read_text(encoding="utf-8")
    assert tuple(json.loads(re.search(r"PERFIS = (\[.*?\]);", js).group(1))) == PERFIS
    esperado = "^" + "".join(f"[a-{'abcde'[len(opcoes) - 1]}]" for _, opcoes in QUIZ_V1) + "$"
    assert re.search(r"RESPOSTAS = /(.*?)/;", js).group(1) == esperado


def test_perfis_batem_com_o_painel_v2():
    js = (RAIZ / "webapp" / "src" / "dashboard" / "lib" / "profiles.js").read_text(encoding="utf-8")
    assert tuple(re.findall(r'\{ id: "(\w+)"', js)) == PERFIS
