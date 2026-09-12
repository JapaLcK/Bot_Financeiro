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
  `test_copy_do_gate_nao_promete_isencao_de_cobranca`
  `test_copy_do_ex_assinante_nao_culpa_um_telefone_que_nao_existe`
  `test_copy_do_ex_assinante_com_telefone_queimado_nomeia_o_telefone`
  (as três últimas medem a MENSAGEM do gate, que deixa de existir quando
   ninguém é barrado — a lista anterior parava em 3 e a injeção dava 4)
Direção: falso positivo de acesso — o bot volta a registrar o gasto de quem não
paga, e é a asserção do SALDO que discrimina (um gate que responde a mensagem
certa mas registra o lançamento assim mesmo passaria sem ela).

CONTROLE NEGATIVO da perna da ESCOLHA: troque
`sem_plano = estado is not None and needs_plan_selection(uid, estado)` por
`sem_plano = False`. VERMELHO:
  `test_com_direito_e_sem_escolha_e_barrado_so_pela_perna_da_escolha`

**A instrução anterior nomeava `test_cadastro_novo_e_barrado_e_nao_registra_nada`
e estava MORTA — medido: a injeção literal dava ZERO vermelhos.** O motivo é o
§3 pela letra: `_cadastro_novo()` não tem escolha NEM direito, então desde o
corte a perna do DIREITO já o barra sozinha e a da ESCOLHA parou de
discriminar. O negativo tem de ser injetado ONDE ele discrimina — a única conta
em que a perna da escolha é a única coisa que barra é uma COM direito vigente e
SEM `plan_selected_at`, que é o que o grant de admin produz.

CONTROLE POSITIVO: `test_pagante_registra_normal` — sem ele o grupo inteiro
passaria num bot que recusa todo mundo, que é pior que o bug. Ele fica VERDE nas
duas injeções acima, e é isso que o torna um positivo e não uma quarta cópia.

As mensagens entram pelo `handle_incoming` (não pelo `_paywall_gate` isolado) e
o banco é real: o que quebra aqui é o estado que o turno anterior deixou.
"""
from __future__ import annotations

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


def test_cadastro_novo_e_barrado_e_nao_registra_nada():
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"o bot atendeu quem não escolheu plano: {resposta!r}"
    assert "/precos" in resposta, "mensagem do gate sem o link da /precos"
    # O que discrimina: barrar é responder E não mexer no dinheiro.
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    assert db.get_balance(uid) == 0, "o gate respondeu mas debitou o saldo"


def test_com_direito_e_sem_escolha_e_barrado_so_pela_perna_da_escolha():
    """A ÚNICA configuração em que a perna da ESCOLHA é o que barra — e achá-la
    exigiu enumerar, porque a primeira tentativa (plano pago vigente sem
    `plan_selected_at`) é IMPOSSÍVEL por construção.

    `needs_plan_selection` termina em `return not _tem_plano_pago_vigente(user)`
    — "assinante pago já escolheu implicitamente no checkout". Logo
    `needs_plan_selection is True` **implica** sem plano pago vigente, e desde o
    corte o único jeito de ainda ter acesso nesse estado é pelo lado DIREITO do
    OR de `tem_direito_hoje`: a carência de inadimplência.

    **Vale registrar o que a enumeração mostrou**: depois do corte, a perna da
    ESCOLHA é quase inteiramente subsumida pela do DIREITO. A brecha entre as
    duas é só esta — carência aberta + `plan_selected_at` NULL — e é um canto
    (o webhook do checkout carimba `mark_plan_selected` antes de a primeira
    cobrança poder falhar). O caso existe para o controle negativo daquela perna
    ter onde discriminar, não porque seja tráfego comum.
    """
    from datetime import datetime, timedelta, timezone
    from db.connection import get_conn
    from db_support import invalidate_auth_user_cache

    uid = _cadastro_novo()
    agora = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='pro', plan_expires_at=%s,"
                "       past_due_since=%s, last_payment_status='past_due',"
                "       plan_selected_at=null where user_id=%s",
                (agora - timedelta(days=1), agora - timedelta(days=2), uid),
            )
        conn.commit()
    invalidate_auth_user_cache(uid)

    from core.services.plan_service import has_app_access, needs_plan_selection
    assert has_app_access(uid) is True, "pré-condição: a perna do DIREITO deixa passar"
    assert needs_plan_selection(uid) is True, "pré-condição: a perna da ESCOLHA barra"

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"a perna da escolha parou de morder: {resposta!r}"
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"


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


