"""
db/signup_quiz.py — resultado do quiz de venda gravado na conta nova.

Fluxo: anúncio → XQuiz → /q#p=<perfil>&r=<respostas> → `quiz-resultado.js` grava o
cookie `quiz_result` (`v1.<perfil>[.<letras>]`) → cadastro → na criação da conta o
`_apply_quiz_attribution` (monólito) revalida aqui e grava em `auth_accounts`.

Perfil e respostas são DADO PESSOAL FINANCEIRO: nunca em query, log ou analytics.
O cookie chega do navegador, então é entrada não confiável — `parse_quiz_cookie`
aceita só o formato exato, e o CHECK de `dashboard_profile` (db/schema.py) é a
segunda barreira.

`criar_conta_sem_codigo` é a conta da /assinar (funil v3): nasce sem senha e sem
código, pela rota `POST /auth/quiz/conta` (frontend/routes/quiz_signup.py), que só
chama `boas_vindas_da_conta` depois de a sessão sair.
"""
import logging

from psycopg.types.json import Jsonb

import db_support
from core.crypto import hash_pii_optional

from .connection import get_conn
from .users import create_link_code, get_or_create_canonical_user

QUIZ_COOKIE = "quiz_result"

# Fonte única dos perfis no Python. O JS (`frontend/quiz-resultado.js`) e o painel v2
# (`webapp/src/dashboard/lib/profiles.js`) repetem a lista; tests/test_signup_quiz.py
# compara as três.
PERFIS = ("economizar", "investir", "controlar", "dividas", "autonomo")
# O painel padrão, escolhido no v2 (`PUT /api/v2/perfil`); o quiz nunca o grava.
# NULL na coluna = nunca escolheu.
PERFIL_PADRAO = "padrao"

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


def ler_perfil(user_id: int) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select dashboard_profile from auth_accounts where user_id = %s order by id limit 1",
                    (int(user_id),))
        linha = cur.fetchone()
    return linha["dashboard_profile"] if linha else None


def gravar_perfil(user_id: int, perfil: str) -> int:
    """Grava o perfil escolhido no painel; devolve o rowcount (0 = sem conta)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set dashboard_profile = %s where user_id = %s",
                    (perfil, int(user_id)))
        conn.commit()
        return cur.rowcount


def criar_conta_sem_codigo(email: str, telefone: str | None, nome: str | None, source: str) -> dict:
    """{"estado": "criada", "user_id"} | {"estado": "tem_conta", "user_id"}
    | {"estado": "cadastro_pendente"} | {"estado": "ocupado"} (outro pedido do mesmo
    e-mail está com a trava: não espera). Só "criada" pode ganhar sessão.

    NÃO passa por `email_verification_codes`: o `create_email_verification` invalida
    todo código vivo do e-mail, inclusive o de quem está no meio do /auth/register,
    e o `confirm` fundia. Aqui, sob a trava do e-mail (a mesma que o register toma
    para gravar o código), numa transação só: conta existente → `tem_conta`; código
    de register vivo (com senha) → `cadastro_pendente`, sem tocar nele; senão o
    INSERT que só cria (`inserir_conta_nova`). Se a sessão falhar depois, a rota
    desfaz a conta com `desfazer_conta_sem_codigo`.
    """
    email = email.strip().lower()
    email_hash = hash_pii_optional(email, kind="email")
    # ANTES da conexão da trava (a conexão dele já voltou ao pool): este pedido
    # nunca segura duas conexões ao mesmo tempo. O id é determinístico pelo e-mail
    # e o register o reaproveita; em tem_conta/pendente sobra no máximo a linha
    # em `users`/`user_identities` do e-mail que o register vai usar.
    user_id = get_or_create_canonical_user("email", email)
    resultado: dict = {}

    def _gravar(telefone):
        resultado.clear()
        with conn.cursor() as cur:
            if not db_support.trava_email(cur, email_hash, esperar=False):
                resultado.update(estado="ocupado")
                return
            cur.execute("select user_id from auth_accounts where email_hash = %s", (email_hash,))
            conta = cur.fetchone()
            if conta:
                resultado.update(estado="tem_conta", user_id=int(conta["user_id"]))
                return
            cur.execute(
                """
                select 1 from email_verification_codes
                where email_hash = %s and used_at is null and expires_at > now()
                  and password_hash is not null
                """,
                (email_hash,),
            )
            if cur.fetchone():
                resultado.update(estado="cadastro_pendente")
                return
            criou = db_support.inserir_conta_nova(
                cur, user_id=user_id, email=email, password_hash=None,
                phone_e164=db_support.telefone_livre(cur, telefone), display_name=nome, source=source,
            )
            resultado.update(estado="criada" if criou else "tem_conta", user_id=user_id)
            if criou:  # a PK da linha nova: é ela que `desfazer_conta_sem_codigo` apaga
                cur.execute("select id from auth_accounts where email_hash = %s", (email_hash,))
                resultado["conta_id"] = int(cur.fetchone()["id"])

    with get_conn() as conn:
        db_support.gravar_descartando_telefone_disputado(conn, _gravar, telefone)
        conn.commit()
    if resultado["estado"] == "criada":
        db_support.invalidate_auth_user_cache(resultado["user_id"])
    return resultado


def boas_vindas_da_conta(email: str, user_id: int) -> None:
    """link_code + e-mail de boas-vindas da conta de `criar_conta_sem_codigo`. A rota
    chama só DEPOIS da sessão: se ela falhar, a conta é desfeita e o e-mail de "conta
    criada" não pode ter saído. Falha aqui não vira 503: a conta já está logada."""
    try:
        db_support.boas_vindas(create_link_code, email, user_id)
    except Exception as exc:  # só o tipo: a mensagem do psycopg pode trazer e-mail/telefone
        logging.getLogger(__name__).warning("boas_vindas falhou user=%s: %s", user_id, type(exc).__name__)


def desfazer_conta_sem_codigo(user_id: int, conta_id: int) -> bool:
    """Apaga a conta que `criar_conta_sem_codigo` acabou de criar (a linha `conta_id`
    dela, não qualquer uma do `user_id`: `auth_accounts` não tem unique em `user_id`)
    e a sessão já emitida desde então, quando a sessão falha no meio: sem isto o retry
    cai em `tem_conta`. Sem corte de tempo (a falha pode vir depois de minutos na fila
    do pool); a guarda é a linha seguir sem senha e sem plano. As sessões só saem se
    nenhuma outra linha do `user_id` sobrou: as desta requisição são inertes (nada saiu
    do servidor), e as da outra linha podem ser um login legítimo feito na janela.
    `users` e `user_identities` ficam: o id é o mesmo na recriação."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            delete from auth_accounts
             where id = %s and user_id = %s and password_hash is null and plan = 'free'
               and plan_expires_at is null
            returning created_at
            """,
            (int(conta_id), int(user_id)),
        )
        conta = cur.fetchone()
        sozinha = "and not exists (select 1 from auth_accounts where user_id = %s)"
        if conta:
            cur.execute(f"delete from auth_refresh_tokens where user_id = %s and issued_at >= %s {sozinha}",
                        (int(user_id), conta["created_at"], int(user_id)))
            cur.execute(f"delete from auth_sessions where user_id = %s and created_at >= %s {sozinha}",
                        (int(user_id), conta["created_at"], int(user_id)))
        conn.commit()
    if conta:
        db_support.invalidate_auth_user_cache(user_id)
    return bool(conta)
