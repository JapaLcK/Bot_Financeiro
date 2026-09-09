"""Gate de escolha de plano no BOT (core.handle_incoming._paywall_gate).

O gate rodava `if not paywall_enabled(): return None` e depois `has_app_access`,
que devolve True INCONDICIONALMENTE com o v2 ligado (plan_service.has_app_access)
— ou seja, era inerte em produção: quem se cadastrava na web e ignorava a /precos
usava o WhatsApp de graça. Agora ele espelha o veredito do gate do WS e do
_post_login_url: `needs_plan_selection(uid) or not has_app_access(uid)`.

Este arquivo mede o VEREDITO (quem é barrado, e o que barrar escreve ou não).
O que o gate deixa passar mora no `test_paywall_gate_isencoes.py`.

CONTROLE NEGATIVO DO GRUPO (§3 do CLAUDE.md): no `_paywall_gate`, troque
`sem_plano = estado is not None and needs_plan_selection(uid, estado)` por
`sem_plano = False` — ou reponha o `if not paywall_enabled(): return None` no
topo dele — e `test_cadastro_novo_e_barrado_e_nao_registra_nada` volta VERDE (o
bot registra o gasto). É um caso que está verde hoje, e é a asserção do SALDO
que discrimina: um gate que responde a mensagem certa mas registra o lançamento
assim mesmo passaria sem ela.

CONTROLE POSITIVO: `test_depois_de_escolher_plano_registra_normal` — sem ele o
grupo inteiro passaria num bot que recusa todo mundo, que é pior que o bug.

As mensagens entram pelo `handle_incoming` (não pelo `_paywall_gate` isolado) e
o banco é real: o que quebra aqui é o estado que o turno anterior deixou.
"""
from __future__ import annotations

import pathlib
import re
import uuid

import db
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    barrado as _barrado,
    cadastro_novo as _cadastro_novo,
    diga as _diga,
    v2_ligado,
)


def _ressalva_do_trial_na_precos() -> str:
    """A ressalva entre parênteses que a /precos põe no trial, lida do HTML."""
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    m = re.search(r"15 dias grátis pra testar</strong>\s*\(([^)]+)\)", html)
    assert m, ("não achei a ressalva do trial na precos.html — se a página mudou "
               "a frase, a copy do bot precisa acompanhar")
    return m.group(1)


def test_cadastro_novo_e_barrado_e_nao_registra_nada():
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"o bot atendeu quem não escolheu plano: {resposta!r}"
    assert "/precos" in resposta, "mensagem do gate sem o link da /precos"
    # O que discrimina: barrar é responder E não mexer no dinheiro.
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    assert db.get_balance(uid) == 0, "o gate respondeu mas debitou o saldo"


def test_depois_de_escolher_plano_registra_normal():
    """Controle positivo: o gate não recusa todo mundo."""
    uid = _cadastro_novo()
    db.mark_plan_selected(uid)

    resposta = _diga(uid, "gastei 50 no mercado")

    assert not _barrado(resposta), f"barrou quem já escolheu plano: {resposta!r}"
    assert db.list_launches(uid), "quem escolheu plano não teve o gasto registrado"


def test_conversa_dois_assuntos_o_turno_barrado_nao_deixa_pendencia():
    uid = _cadastro_novo()

    assert _barrado(_diga(uid, "gastei 50 no mercado"))
    assert db.get_pending_action(uid) is None, "o turno barrado deixou pendência"

    db.mark_plan_selected(uid)
    resposta = _diga(uid, "saldo")

    assert not _barrado(resposta)
    assert "conta corrente" in resposta.lower(), \
        f"'saldo' não foi respondido como saldo: {resposta!r}"
    assert db.list_launches(uid) == [], "o gasto do turno barrado voltou do além"


def test_uid_sem_cadastro_web_nao_ve_o_gate_e_uid_com_cadastro_ve():
    """O veredito vem do ESTADO DA CONTA, não do texto — as duas metades mandam
    a MESMA mensagem e recebem respostas opostas.

    Sem linha em auth_accounts não há cadastro web para ter escolhido plano:
    barrar aí trocaria o convite de cadastro/vínculo pela tela de planos, para
    um número que nem conta tem.

    Consequência conhecida e aceita (não é o que este teste mede): um número já
    auto-vinculado a uma conta SEM plano é barrado ao mandar `link <código>` de
    outra conta. Ele continua alcançando `assinar` e `ajuda` (isenções do gate).
    """
    sem_conta = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(sem_conta)
    com_conta_sem_plano = _cadastro_novo()

    assert not _barrado(_diga(sem_conta, "link 123456")), \
        "barrou quem ainda não tem cadastro web"
    assert _barrado(_diga(com_conta_sem_plano, "link 123456")), \
        "o mesmo texto passou para uma conta sem plano — o teste não mede estado"


def test_freio_de_emergencia_desliga_o_gate(monkeypatch):
    """PLANS_V2_ENABLED=0 (freio de emergência) devolve o comportamento legado:
    ninguém é barrado por não ter escolhido plano."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert not _barrado(resposta), f"o freio não desligou o gate: {resposta!r}"


def test_barrar_nao_deixa_rastro_no_banco(monkeypatch):
    """Barrar é responder — não é escrever.

    Duas escritas que o gate chegou a fazer: uma linha em `dashboard_sessions`
    por mensagem (link autenticado que ninguém consome e que nada expira — não
    há varredor) e linhas em `pii_access_log` (get_auth_user decifra e audita).
    Ambas por MENSAGEM, no caminho mais quente do bot.

    Controle negativo: volte o `build_dashboard_link` e troque o
    `db.get_plan_gate_state` por `get_auth_user` — cada contagem sai do zero.
    """
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")  # o conftest desliga por padrão
    from db.connection import get_conn
    import db_support

    uid = _cadastro_novo()
    db_support.invalidate_auth_user_cache(uid)  # cache quente esconderia o decrypt

    assert _barrado(_diga(uid, "gastei 50 no mercado"))
    assert _barrado(_diga(uid, "gastei 20 na padaria"))

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from dashboard_sessions where user_id=%s", (uid,))
        sessoes = cur.fetchone()["n"]
        cur.execute("select count(*) as n from pii_access_log where subject_user_id=%s", (uid,))
        auditoria = cur.fetchone()["n"]

    assert sessoes == 0, f"{sessoes} linha(s) em dashboard_sessions por mensagem barrada"
    assert auditoria == 0, f"{auditoria} linha(s) de auditoria de PII por mensagem barrada"


def test_copy_do_gate_nao_promete_isencao_de_cobranca():
    """O trial é 1 por telefone NA VIDA (db/plans.py): quem recria a conta com o
    mesmo número é cobrado na hora (trial_days=0). A copy não pode afirmar 'sem
    cobrança agora' — a moldura é a do send_welcome_email: o checkout confirma
    antes de cobrar."""
    resposta = _diga(_cadastro_novo(), "gastei 50 no mercado")

    assert _barrado(resposta), "pré-condição: a mensagem medida é a do gate"
    assert "sem cobrança agora" not in resposta.lower()
    assert "checkout" in resposta.lower(), "a copy não diz quem confirma a cobrança"
    # Paridade com a /precos, comparada contra o HTML de verdade (§0.7, mesmo
    # padrão do tests/test_phosphor_subset.py): um literal aqui seria uma
    # TERCEIRA cópia da ressalva e ficaria verde se a página mudasse.
    ressalva = _ressalva_do_trial_na_precos()
    assert ressalva.lower() in resposta.lower(), (
        f"a /precos ressalva o trial com {ressalva!r} e o bot não — o bot está "
        "prometendo mais que a página"
    )
