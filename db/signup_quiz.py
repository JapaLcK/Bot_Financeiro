"""
db/signup_quiz.py — resultado do quiz de venda gravado na conta nova.

Fluxo: anúncio → XQuiz → /q#p=<perfil>&r=<respostas> → `quiz-resultado.js` grava o
cookie `quiz_result` (`v1.<perfil>[.<letras>]`) → cadastro → na criação da conta o
`_apply_quiz_attribution` (monólito) revalida aqui e grava em `auth_accounts`.

Perfil e respostas são DADO PESSOAL FINANCEIRO: nunca em query, log ou analytics.
O cookie chega do navegador, então é entrada não confiável — `parse_quiz_cookie`
aceita só o formato exato, e o CHECK de `dashboard_profile` (db/schema.py) é a
segunda barreira.
"""
from psycopg.types.json import Jsonb

from .connection import get_conn

QUIZ_COOKIE = "quiz_result"

# Fonte única dos perfis no Python. O JS (`frontend/quiz-resultado.js`) e o painel v2
# (`webapp/src/dashboard/lib/profiles.js`) repetem a lista; tests/test_signup_quiz.py
# compara as três.
PERFIS = ("economizar", "investir", "controlar", "dividas", "autonomo")

# Perguntas em ordem; a letra N da resposta é a opção N (a=0, b=1…).
QUIZ_V1 = (
    ("renda", ("salario", "freela", "mesada", "sem_renda")),
    ("fim_do_mes", ("guarda", "some", "zero_a_zero", "falta")),
    ("cartao", ("nao_tem", "paga_inteira", "minimo_ou_parcela", "perdeu_a_conta")),
    ("mil_reais", ("quita_divida", "reserva", "investe", "compra")),
    ("objetivo", ("juntar", "render", "controlar_gastos", "sair_das_dividas", "organizar_renda")),
)


def parse_quiz_cookie(valor: str) -> tuple[str, dict | None] | None:
    """`v1.<perfil>` ou `v1.<perfil>.<letras>` → (perfil, respostas|None); resto → None.

    Perfil inválido → None (nada é gravado). Perfil válido com letras ausentes ou
    inválidas → (perfil, None): grava o perfil, sem as respostas.
    """
    if not valor or len(valor) > 40:
        return None
    partes = valor.split(".")
    # Igualdade exata com PERFIS: barra maiúscula, NUL e unicode parecido.
    if partes[0] != "v1" or len(partes) not in (2, 3) or partes[1] not in PERFIS:
        return None
    letras = partes[2] if len(partes) == 3 else ""
    if len(letras) != len(QUIZ_V1):
        return partes[1], None
    respostas = {}
    for letra, (pergunta, opcoes) in zip(letras, QUIZ_V1):
        i = "abcde".find(letra)
        if i not in range(len(opcoes)):
            return partes[1], None
        respostas[pergunta] = opcoes[i]
    return partes[1], respostas


def record_signup_quiz(user_id: int, perfil: str, respostas: dict | None) -> bool:
    """Grava perfil e respostas na conta, só se ela ainda não tem nenhum dos dois.

    Parcial grava `{"versao": 1, "respostas": null}` e não NULL: `signup_quiz is not
    null` é o que marca quem veio do quiz.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update auth_accounts
               set dashboard_profile = %s, signup_quiz = %s
             where user_id = %s and dashboard_profile is null and signup_quiz is null
            """,
            (perfil, Jsonb({"versao": 1, "respostas": respostas}), int(user_id)),
        )
        conn.commit()
        return cur.rowcount > 0
