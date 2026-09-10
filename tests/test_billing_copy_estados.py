"""
tests/test_billing_copy_estados.py — a copy de `plano` e `cancelar` nos estados
que NÃO são "plano pago vigente".

Arquivo próprio por assunto (e porque `test_paywall_gate_isencoes.py` bateu no
teto de 350 linhas): lá mora **o que o gate deixa passar**, aqui **o que a
pessoa lê quando passa**. São três estados e nenhum serve pelos outros — a
tabela está em `core/services/billing_copy.py`.

Os dois casos que bloqueavam o push vinham daqui: o assinante **Essencial** e a
conta em **CARÊNCIA** recebiam, literalmente, a mensagem do cortado — "sem plano
ativo… nem assinatura a cancelar… pra voltar a usar". Na carência a assinatura
está VIVA na Stripe, em retentativa, e o `_handle_cancelar` também não entregava
o link do portal (roteava por `is_pro`, False ali): beco sem saída de até 7 dias
para quem quer parar a cobrança.
"""
from __future__ import annotations

import db
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    cadastro_novo as _cadastro_novo,
    diga as _diga,
    v2_ligado,
)


# ── B1: a copy do CLIENTE PAGANTE e a da CARÊNCIA ───────────────────────────
#
# Os dois estados abaixo recebiam LITERALMENTE a mensagem do cortado — "sem
# plano ativo… nem assinatura a cancelar… pra voltar a usar" — e nos dois ela é
# falsa. O roteamento era `is_pro` (`tier >= plus`), que não vê Essencial nem
# carência.
#
# CONTROLES DECLARADOS (`docs/controles_declarados.md`), e são DOIS porque o
# conserto tem duas metades independentes. Medido, e a primeira redação estava
# errada: injetar só o roteamento antigo dá **ZERO vermelhos**.
#
# **(a) a carência deixa de cair no portal** — em `_handle_cancelar`, troque
# `if estado == "sem_acesso":` por `if estado != "sem_plano":` (troca de valor,
# nada apagado). VERMELHO:
#   `test_carencia_manda_cancelar_e_recebe_o_portal`
#
# **(b) o estado ANTERIOR AO PR, que precisa das DUAS metades** — (a) mais
# trocar o `try/except get_plan_tier` por
# `tier = "plus" if is_pro(user_id) else "free"`. VERMELHOS:
#   `test_essencial_vigente_recebe_o_portal_e_nao_a_copy_do_cortado`
#   `test_carencia_manda_cancelar_e_recebe_o_portal`
#
# **Por que o roteamento sozinho não discrimina, e isto é a medição falando:**
# com `is_pro`, o assinante ESSENCIAL vira `tier == "free"` e entra no bloco de
# estados — mas lá `estado_sem_plano_pago` devolve `"carencia"` (tem acesso, não
# precisa escolher plano) e ele CAI NO MESMO PORTAL pelo fall-through. As duas
# metades se cobrem, o que é bom para o cliente e ruim para um controle: o caso
# do Essencial é uma CERCA DE RESULTADO ("o pagante não recebe a copy do
# cortado"), não um discriminador do predicado de roteamento. Fica declarado
# assim em vez de fingir que mede o que não mede.
#
# Direção das duas: falso NEGATIVO de assinatura — o bot afirma a cliente
# pagante que não há assinatura a cancelar, e some com o link que a encerra.
#
# Positivo do grupo, VERDE nas duas injeções (é o que o torna positivo):
#   `test_cortado_de_verdade_continua_recebendo_a_copy_do_corte`


def _copy_do_cortado() -> str:
    """A mensagem do CORTADO, renderizada da própria constante (§0.7).

    Comparar contra o objeto e não contra um literal: se alguém reescrever a
    copy, estes testes continuam medindo "não recebe a mensagem do cortado" em
    vez de virarem verde por o literal ter parado de casar."""
    from core.services import billing_copy
    return billing_copy.SEM_ACESSO.format(assinar="*assinar plano*")


def _com_tier(tier_armazenado, *, expira_em_dias=30, past_due_since=None, status="active"):
    """Conta com um tier armazenado específico. `plan='essencial'` é o caso que
    o `is_pro` não enxerga; a carência é `plan` pago VENCIDO + relógio aberto."""
    from datetime import datetime, timedelta, timezone
    from db.connection import get_conn
    from db_support import invalidate_auth_user_cache

    uid = _cadastro_novo()
    db.mark_plan_selected(uid)
    agora = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan=%s, plan_expires_at=%s,"
                "       past_due_since=%s, last_payment_status=%s where user_id=%s",
                (tier_armazenado, agora + timedelta(days=expira_em_dias),
                 past_due_since, status, uid),
            )
        conn.commit()
    invalidate_auth_user_cache(uid)
    return uid


def test_essencial_vigente_recebe_o_portal_e_nao_a_copy_do_cortado():
    """Assinante ESSENCIAL paga e `is_pro` é False para ele — era a rota direta
    para "sua conta está sem plano ativo… nem assinatura a cancelar"."""
    uid = _com_tier("essencial")
    from core.services.plan_service import get_plan_tier, has_app_access
    assert get_plan_tier(uid) == "essencial", "pré-condição: o tier tem de ser Essencial"
    assert has_app_access(uid) is True

    resposta = _diga(uid, "cancelar plano")

    assert resposta != _copy_do_cortado(), "o pagante recebeu a copy do cortado"
    assert "nem assinatura a cancelar" not in resposta.lower(), resposta
    assert "http" in resposta, "o pagante ficou sem o link do portal"


def test_carencia_manda_cancelar_e_recebe_o_portal():
    """Carência: `plan_expires_at` vencido (tier `free`) + relógio aberto. A
    assinatura está VIVA na Stripe, em retentativa — dizer que não há o que
    cancelar tranca a pessoa por até 7 dias querendo parar a cobrança."""
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone.utc)
    uid = _com_tier("pro", expira_em_dias=-1,
                    past_due_since=agora - timedelta(days=2), status="past_due")
    from core.services.plan_service import get_plan_tier, has_app_access
    assert get_plan_tier(uid) == "free", "pré-condição: a carência tem tier free"
    assert has_app_access(uid) is True, "pré-condição: a carência concede acesso"

    resposta = _diga(uid, "cancelar plano")

    assert resposta != _copy_do_cortado(), "a carência recebeu a copy do cortado"
    assert "nem assinatura a cancelar" not in resposta.lower(), resposta
    assert "http" in resposta, "quem quer parar a retentativa ficou sem o portal"


def test_carencia_pergunta_plano_e_ouve_a_verdade_da_cobranca():
    """`plano` na carência não pode dizer "sem plano ativo": há assinatura, e o
    que existe é uma cobrança em atraso."""
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone.utc)
    uid = _com_tier("pro", expira_em_dias=-1,
                    past_due_since=agora - timedelta(days=2), status="past_due")

    resposta = _diga(uid, "plano")

    baixa = resposta.lower()
    assert "sem plano ativo" not in baixa, resposta
    assert "não passou" in baixa or "nao passou" in baixa, resposta
    assert "cancelar plano" in baixa, "a copy da carência não oferece a saída"


def test_cortado_de_verdade_continua_recebendo_a_copy_do_corte():
    """POSITIVO do grupo: quem foi cortado MESMO (sem direito, sem carência)
    continua ouvindo que não há assinatura a cancelar — que ali é verdade."""
    uid = _com_tier("pro", expira_em_dias=-1, status="canceled")
    from core.services.plan_service import has_app_access
    assert has_app_access(uid) is False, "pré-condição: a conta tem de estar cortada"

    assert _diga(uid, "cancelar plano") == _copy_do_cortado()
