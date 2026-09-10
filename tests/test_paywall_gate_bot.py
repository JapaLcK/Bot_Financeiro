"""Gate de escolha de plano no BOT (core.handle_incoming._paywall_gate).

O gate rodava `if not paywall_enabled(): return None` e depois `has_app_access`,
que devolvia True INCONDICIONALMENTE com o v2 ligado — ou seja, era inerte em
produção: quem se cadastrava na web e ignorava a /precos usava o WhatsApp de
graça. Ele passou a espelhar o veredito do gate do WS e do _post_login_url:
`needs_plan_selection(uid) or not has_app_access(uid)`.

DESDE O CORTE DO GRÁTIS as DUAS pernas mordem, e a segunda mudou quem é barrado:
`has_app_access` consulta `tem_direito_hoje`, então quem escolheu plano e ficou
no Grátis passa a ser barrado, e a população só-WhatsApp (sem linha em
`auth_accounts`) também — decisão registrada do dono. A mensagem do gate ganhou
DUAS formas por causa disso, e elas têm teste próprio abaixo.

Este arquivo mede o VEREDITO (quem é barrado, e o que barrar escreve ou não).
O que o gate deixa passar mora no `test_paywall_gate_isencoes.py`.

CONTROLE NEGATIVO DO GRUPO (§3 do CLAUDE.md), o da perna do DIREITO: em
`core/services/plan_service.has_app_access`, troque `return tem_direito_hoje(user)`
por `return True`. VERMELHOS:
  `test_ex_assinante_e_barrado_e_nao_registra_nada`
  `test_so_whatsapp_e_barrado_e_recebe_a_copy_sem_painel`
  `test_conversa_dois_assuntos_bloqueado_nao_escreve_nada`
Direção: falso positivo de acesso — o bot volta a registrar o gasto de quem não
paga, e é a asserção do SALDO que discrimina (um gate que responde a mensagem
certa mas registra o lançamento assim mesmo passaria sem ela).

CONTROLE NEGATIVO da perna da ESCOLHA: troque
`sem_plano = estado is not None and needs_plan_selection(uid, estado)` por
`sem_plano = False`. VERMELHO: `test_cadastro_novo_e_barrado_e_nao_registra_nada`.

CONTROLE POSITIVO: `test_pagante_registra_normal` — sem ele o grupo inteiro
passaria num bot que recusa todo mundo, que é pior que o bug. Ele fica VERDE nas
duas injeções acima, e é isso que o torna um positivo e não uma quarta cópia.

As mensagens entram pelo `handle_incoming` (não pelo `_paywall_gate` isolado) e
o banco é real: o que quebra aqui é o estado que o turno anterior deixou.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import db
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    barrado as _barrado,
    cadastro_novo as _cadastro_novo,
    com_plano as _com_plano,
    diga as _diga,
    so_whatsapp as _so_whatsapp,
    v2_ligado,
)


def _copy_da_precos_promete_trial() -> str:
    """A frase do trial que a /precos mostra ao VISITANTE, lida do HTML.

    Serve à direção OPOSTA da de antes. Este arquivo comparava a copy do bot com
    ela para exigir PARIDADE, e a paridade era o defeito: a mensagem do gate
    prometia "15 dias grátis (um teste por número)" a quem o corte manda para
    lá, e o trial é um por telefone NA VIDA — o ex-assinante que já o queimou
    lia uma promessa que o checkout não cumpre. Hoje ela existe para provar que
    o bot NÃO repete essa frase."""
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    m = re.search(r"15 dias grátis pra testar</strong>\s*\(([^)]+)\)", html)
    assert m, "não achei a frase do trial na precos.html"
    return m.group(1)


def test_cadastro_novo_e_barrado_e_nao_registra_nada():
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"o bot atendeu quem não escolheu plano: {resposta!r}"
    assert "/precos" in resposta, "mensagem do gate sem o link da /precos"
    # O que discrimina: barrar é responder E não mexer no dinheiro.
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    assert db.get_balance(uid) == 0, "o gate respondeu mas debitou o saldo"


def test_pagante_registra_normal():
    """Controle positivo: o gate não recusa todo mundo.

    A conta é PAGANTE e não só "escolheu plano" — desde o corte, escolher sem
    pagar é justamente o estado barrado, e usá-lo aqui mediria o gate contra si
    mesmo. Ver a docstring de `_paywall_gate_helpers.com_plano`."""
    uid = _com_plano()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert not _barrado(resposta), f"barrou um pagante: {resposta!r}"
    assert db.list_launches(uid), "o pagante não teve o gasto registrado"


def test_ex_assinante_e_barrado_e_nao_registra_nada():
    """Escolheu plano um dia e hoje não tem direito vigente: BARRADO.

    É o caso novo do corte, e o oposto exato do que valia antes — `plan_selected_at`
    preenchido fechava o gate inteiro, porque a outra perna devolvia True
    incondicional."""
    uid = _cadastro_novo()
    db.mark_plan_selected(uid)   # escolheu; o direito é que não existe mais

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"o bot atendeu quem não tem direito: {resposta!r}"
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    assert db.get_balance(uid) == 0, "o gate respondeu mas debitou o saldo"


def test_so_whatsapp_e_barrado_e_recebe_a_copy_sem_painel():
    """A população sem cadastro web é cortada (decisão do dono), e a mensagem do
    gate é a ÚNICA comunicação que ela recebe.

    As três asserções de conteúdo são requisito, não estilo: quem nunca viu o
    dashboard não pode ser mandado para "seu painel", e nada pode ser afirmado
    sobre o período grátis — `db.plans.is_trial_eligible_for_user` devolve False
    para TODA esta população (não há `phone_hash` porque não há linha), então
    reusar a frase do `assinar` diria "esse telefone já usou o período grátis",
    mentira na direção oposta."""
    uid = _so_whatsapp()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"a população só-WhatsApp não foi cortada: {resposta!r}"
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    baixa = resposta.lower()
    assert "painel" not in baixa, resposta
    assert "dashboard" not in baixa, resposta
    assert "grátis" not in baixa and "gratis" not in baixa, (
        "a copy da população sem cadastro afirmou algo sobre o período grátis: "
        f"{resposta!r}")


def test_conversa_dois_assuntos_bloqueado_nao_escreve_nada():
    """DUAS mensagens de assuntos diferentes, pelo `handle_incoming`, com estado
    real (§3: rode a conversa, não a função).

    A 1ª é dinheiro e devolve o gate sem escrever nada; a 2ª é billing, cai na
    ISENÇÃO do gate — e também não escreve."""
    uid = _cadastro_novo()
    db.mark_plan_selected(uid)

    primeira = _diga(uid, "gastei 50 no mercado")
    assert _barrado(primeira), primeira
    assert db.list_launches(uid) == [], "a 1ª mensagem registrou o lançamento"

    segunda = _diga(uid, "plano")
    assert not _barrado(segunda), f"o gate sequestrou o comando de billing: {segunda!r}"
    assert "assinar plano" in segunda.lower(), segunda
    assert db.list_launches(uid) == [], "a 2ª mensagem escreveu alguma coisa"
    assert db.get_balance(uid) == 0


def test_conversa_dois_assuntos_o_turno_barrado_nao_deixa_pendencia():
    uid = _cadastro_novo()

    assert _barrado(_diga(uid, "gastei 50 no mercado"))
    assert db.get_pending_action(uid) is None, "o turno barrado deixou pendência"

    # PAGAR, não só escolher: desde o corte é o direito que abre o gate.
    db.mark_plan_selected(uid)
    db.update_user_plan(uid, "pro", datetime.now(timezone.utc) + timedelta(days=30))
    resposta = _diga(uid, "saldo")

    assert not _barrado(resposta)
    assert "conta corrente" in resposta.lower(), \
        f"'saldo' não foi respondido como saldo: {resposta!r}"
    assert db.list_launches(uid) == [], "o gasto do turno barrado voltou do além"


def test_o_veredito_vem_do_estado_e_nao_do_texto():
    """As duas metades mandam a MESMA mensagem e recebem respostas opostas.

    **Este teste INVERTEU uma das metades, e a inversão é a decisão do dono.**
    Ele afirmava que quem NÃO tem linha em `auth_accounts` (a população
    só-WhatsApp) não via o gate, porque "não há cadastro web para ter escolhido
    plano". Isso valia enquanto a única perna era a da ESCOLHA. Com a perna do
    DIREITO, `tem_direito_hoje(None)` é False e essa população é cortada — sem
    aviso prévio, por decisão explícita (docstring de `tem_direito_hoje`). O
    corte dela tem teste próprio, o `test_so_whatsapp_e_barrado_*`.

    Como as duas metades de antes viraram o mesmo veredito, o par que ainda
    DISCRIMINA é pagante × não-pagante. Consequência conhecida e aceita: um
    número já vinculado a uma conta sem direito é barrado ao mandar `link
    <código>` de outra conta; ele continua alcançando `assinar` e `ajuda`.
    """
    pagante = _com_plano()
    sem_direito = _cadastro_novo()

    assert not _barrado(_diga(pagante, "link 123456")), \
        "barrou um pagante"
    assert _barrado(_diga(sem_direito, "link 123456")), \
        "o mesmo texto passou para uma conta sem direito — o teste não mede estado"


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
    resposta = _diga(_so_whatsapp(), "gastei 50 no mercado")

    assert _barrado(resposta), "pré-condição: a mensagem medida é a do gate"
    assert "sem cobrança agora" not in resposta.lower()
    assert "checkout" in resposta.lower(), "a copy não diz quem confirma a cobrança"
    # A comparação com a /precos mudou de DIREÇÃO: antes exigia paridade, hoje
    # exige o contrário. A frase do visitante ("um teste por número") é falsa
    # para quem o gate manda para lá, e o bot não pode repeti-la. Continua lida
    # do HTML de verdade (§0.7) para não virar uma terceira cópia do literal.
    frase_de_visitante = _copy_da_precos_promete_trial()
    assert frase_de_visitante.lower() not in resposta.lower(), (
        f"o bot repetiu a promessa de visitante da /precos ({frase_de_visitante!r}) "
        "para quem está sendo cortado")
