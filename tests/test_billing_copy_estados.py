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
# Dito sem rodeio, porque o leitor supõe o contrário: **a troca de `is_pro` por
# `get_plan_tier` neste caminho de cobrança NÃO TEM EFEITO MEDIDO.** Remedido
# 2026-09-11 com a injeção do roteamento antigo e mais nada: 43 passed, 0
# failed. O predicado novo é o correto — `is_pro` é `tier >= plus` e mente sobre
# o Essencial, e o fall-through que hoje o salva é acidente de duas metades se
# cobrindo, não contrato — e ninguém pede reverter. Mas nenhum teste deste
# arquivo, nem de outro, cai se ele voltar sozinho. Quem mexer aqui e quiser um
# discriminador do roteamento precisa de um estado onde as duas metades NÃO se
# cubram; ele não existe hoje e não foi inventado só para fechar a declaração.
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


# ── O FREIO DE EMERGÊNCIA não pode fazer a copy mentir ───────────────────────
#
# `estado_sem_plano_pago` deduzia a carência de `has_app_access`, com o argumento
# de que — descartado o plano pago vigente — "ainda tem acesso" só podia vir do
# relógio. O argumento vale só enquanto `has_app_access` chega ao OR. Ele tem
# TRÊS curtos-circuitos que devolvem True sem olhar relógio nenhum:
# `ACCESS_GATE_ENABLED=0`, `PLANS_V2_ENABLED=0` e exceção. Nos três, o cortado
# ouvia "a cobrança da sua assinatura não passou… seu acesso continua por
# enquanto" e `cancelar plano` entregava o portal da Stripe de uma assinatura
# que não existe.
#
# Isto é o pior lugar possível para um bug de copy: o freio é a alavanca que se
# puxa às 3 da manhã se o corte der errado, e era exatamente nesse estado que a
# mentira alcançava a base INTEIRA. O predicado certo é `carencia_aberta`, que
# responde só o relógio e não tem freio.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — em
# `core.services.billing_copy.estado_sem_plano_pago`, troque o bloco do
# `carencia_aberta` pelo gate de novo. Troca de PREDICADO, nada apagado, e o
# `is not None` TEM de vir junto::
#
#     from core.services.plan_service import has_app_access
#     aberta = (has_app_access(user_id, user=user) if user is not None
#               else has_app_access(user_id))
#
# **Sem o `is not None` a injeção mede outra coisa** — medido: `user=None` cru é
# VEREDITO ("não existe conta") para o `has_app_access`, e aí o POSITIVO
# (`test_carencia_manda_cancelar_e_recebe_o_portal`) cai junto, 3 vermelhos em
# vez de 2. Injeção que derruba o positivo inverte a leitura de quem a segue
# (`docs/controles_declarados.md`): muda UM termo por vez.
#
# VERMELHOS (medido 2026-09-11):
#   `test_freio_do_corte_nao_transforma_cortado_em_carencia`
#   `test_freio_da_escada_nao_transforma_cortado_em_carencia`
# Direção: falso POSITIVO de assinatura — o bot afirma a quem não tem nada que
# existe uma cobrança em retentativa, e manda ao portal da Stripe.
#
# Positivo do PAR, VERDE sob a injeção (é o que o torna positivo):
#   `test_carencia_manda_cancelar_e_recebe_o_portal` — a carência DE VERDADE
#   continua sendo carência; o conserto restringe, e não recusa tudo.


def _cortado_sem_relogio() -> int:
    """Cortada de verdade: plano vencido, `canceled`, `past_due_since` NULL."""
    return _com_tier("pro", expira_em_dias=-1, status="canceled")


def test_freio_do_corte_nao_transforma_cortado_em_carencia(monkeypatch):
    """`ACCESS_GATE_ENABLED=0`: o freio devolve ACESSO, não uma assinatura."""
    uid = _cortado_sem_relogio()
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "0")
    from core.services.plan_service import has_app_access
    assert has_app_access(uid) is True, "pré-condição: o freio tem de abrir o acesso"

    assert _diga(uid, "cancelar plano") == _copy_do_cortado()
    baixa = _diga(uid, "plano").lower()
    assert "não passou" not in baixa and "nao passou" not in baixa, baixa


def test_freio_da_escada_nao_transforma_cortado_em_carencia(monkeypatch):
    """`PLANS_V2_ENABLED=0` com `PAYWALL_ENABLED=0`: o binário legado também
    devolve True cru de `has_app_access`, pelo `paywall_enabled()`."""
    uid = _cortado_sem_relogio()
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    from core.services.plan_service import has_app_access
    assert has_app_access(uid) is True, "pré-condição: o freio legado abre o acesso"

    assert _diga(uid, "cancelar plano") == _copy_do_cortado()
