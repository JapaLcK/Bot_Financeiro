"""O nome que vai para o Asaas sai da chave que o BANCO devolve (§0.7).

`_titular` lia `conta.get("name")` — chave que `get_auth_user` **nunca escreve**:
a coluna é `display_name`, e é ela que volta no dict (`db_support.py:578` e
`:607`, este último decifrando `display_name_enc`). O `or` engolia o `None` em
silêncio e o **e-mail do cliente ia no campo `name` do `POST /v3/customers`** —
recibo e painel do Asaas com e-mail no lugar do nome, e a `privacy.html`
prometendo "recebe seu nome".

**Nada é mockado do lado do banco, de propósito.** A conta é escrita em
`auth_accounts` e lida pelo `get_auth_user` de verdade. Um teste que montasse
`{"name": "Maria"}` à mão afirmaria a chave que o CÓDIGO pede em vez da que o
banco devolve — e era exatamente por isso que a suíte inteira ficava verde com o
bug vivo em produção (nenhum teste chamava `_titular`; os do checkout passam
`nome=` pronto, `tests/_pix_checkout_helpers.py:105`).

CONTROLE NEGATIVO MEDIDO: volte `display_name` para `name` em
`frontend/routes/billing_pix.py:234` → `test_nome_do_cadastro_e_o_que_vai_para_o_asaas`
VERMELHO, com o e-mail no lugar do nome. Os outros dois seguem verdes de
propósito: são os degraus do fallback, que o bug não mudava — quem discrimina é
o primeiro.

POSITIVO do grupo: `test_conta_sem_nome_cai_no_email`. Sem ele, um `_titular`
que devolvesse sempre `"PigBank {id}"` passaria no negativo, e nome vazio/errado
faz o `POST /v3/customers` recusar a venda inteira.
"""
from __future__ import annotations

from _billing_grants_helpers import conta
from db.connection import get_conn
from db_support import invalidate_auth_user_cache
from frontend.routes.billing_pix import _titular


def _cadastrar_nome(uid: int, nome: str | None) -> None:
    """Escreve o nome na COLUNA, e limpa o `_enc` para o claro valer."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set display_name=%s,"
                        "       display_name_enc=null where user_id=%s", (nome, uid))
        conn.commit()
    invalidate_auth_user_cache(uid)


def test_nome_do_cadastro_e_o_que_vai_para_o_asaas(user_id):
    """DISCRIMINA. O nome do cadastro, e não o e-mail com cara de nome."""
    conta(user_id, "free", None)
    _cadastrar_nome(user_id, "Maria Silva")

    nome, email = _titular(user_id)

    assert nome == "Maria Silva", "o campo `name` do Asaas não recebeu o nome do cadastro"
    assert "@" not in nome, "o e-mail vazou para o campo `name` do Asaas"
    assert email == f"gr-{user_id}@t.local"


def test_conta_sem_nome_cai_no_email(user_id):
    """POSITIVO. Conta antiga (ou Google sem nome) — o fallback tem de sobreviver."""
    conta(user_id, "free", None)
    _cadastrar_nome(user_id, None)

    assert _titular(user_id) == (f"gr-{user_id}@t.local", f"gr-{user_id}@t.local")


def test_sem_auth_account_o_nome_nunca_sai_vazio(user_id):
    """Último recurso: `get_auth_user` devolve `None` e o nome não pode ser "" —
    nome vazio faz o `POST /v3/customers` recusar a venda inteira."""
    assert _titular(user_id) == (f"PigBank {user_id}", None)
