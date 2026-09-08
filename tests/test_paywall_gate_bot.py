"""Gate de escolha de plano no BOT (core.handle_incoming._paywall_gate).

O gate rodava `if not paywall_enabled(): return None` e depois `has_app_access`,
que devolve True INCONDICIONALMENTE com o v2 ligado (plan_service.has_app_access)
— ou seja, era inerte em produção: quem se cadastrava na web e ignorava a /precos
usava o WhatsApp de graça. Agora ele espelha o veredito do gate do WS e do
_post_login_url: `needs_plan_selection(uid) or not has_app_access(uid)`.

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

import pytest

import db
import core.handle_incoming as hi
from core.types import Attachment, IncomingMessage


@pytest.fixture(autouse=True)
def _v2_ligado(monkeypatch):
    """O conftest roda a suíte com PLANS_V2_ENABLED=0. Sem este setenv,
    needs_plan_selection devolve False sempre e o arquivo inteiro é teatro."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PAYWALL_ENABLED", "0")  # a perna legada fica fora do teste


def _cadastro_novo() -> int:
    """Conta recém-criada pela web: plan_selected_at NULL → gate fechado.

    O user_id canônico é <= 2e9 (db/users.get_or_create_canonical_user), então
    `_normalize_user_id` não comprime e o uid do teste é o uid do handler."""
    user = db.register_auth_user(f"gatebot-{uuid.uuid4().hex[:12]}@t.com", "senha-forte-123")
    return int(user["user_id"])


def _diga(uid: int, texto: str, plataforma: str = "whatsapp", anexos=None) -> str:
    out = hi.handle_incoming(IncomingMessage(
        platform=plataforma, user_id=uid, text=texto,
        message_id=uuid.uuid4().hex, attachments=anexos or [], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


def _barrado(resposta: str) -> bool:
    return "sua conta precisa estar ativa" in resposta.lower()


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


@pytest.mark.parametrize("comando,esperado", [
    ("assinar", "assinar"),      # link de checkout
    ("/assinar", "assinar"),     # prefixo do Discord
    ("plano", "plano"),
    ("cancelar", "cancelar"),    # o trigger da ressalva do `ponytail:` no gate
    ("ajuda", "comece aqui"),
    # Ajuda COM seção (HELP_SECTION_RE). O texto esperado é o da seção "start"
    # e não o da seção pedida DE PROPÓSITO: intent_router passa só o argumento
    # ("ofx") pro resolve_section, que espera o texto inteiro ("ajuda ofx") e
    # cai no fallback "start". Defeito PRÉ-EXISTENTE, fora deste PR — o que se
    # mede aqui é o gate deixar passar, não a seção resolvida.
    ("ajuda ofx", "comece aqui"),
    ("help investimentos", "comece aqui"),
])
def test_discord_barrado_alcanca_billing_e_ajuda(comando, esperado):
    """No Discord o handle_incoming responde assinar/plano/ajuda ELE MESMO — o
    adapter (adapters/discord/discord_bot.py) só cai nos cogs quando a lista
    volta vazia. Sem as isenções, o gate sequestra esses comandos e o usuário
    barrado fica sem como assinar: é o `_GATE_EXEMPT_PREFIXES` da web
    (frontend/routes/shared.py) faltando aqui.

    Controle negativo: apague o bloco de isenção do `_paywall_gate` e os quatro
    casos ficam VERMELHOS.
    """
    uid = _cadastro_novo()

    resposta = _diga(uid, comando, plataforma="discord")

    assert not _barrado(resposta), f"o gate sequestrou {comando!r}: {resposta!r}"
    assert esperado in resposta.lower(), f"{comando!r} respondeu: {resposta!r}"


@pytest.mark.parametrize("nome,tipo", [
    ("extrato.ofx", "application/x-ofx"),
    ("extrato.csv", "text/csv"),
])
@pytest.mark.parametrize("legenda", ["ajuda", "assinar", "ajuda ofx"])
def test_anexo_com_legenda_isenta_continua_barrado(nome, tipo, legenda):
    """A legenda do anexo vira msg.text (adapters/whatsapp/wa_parse.py), então
    sem o `not msg.attachments` um .ofx legendado "ajuda" entra pelo gate e cai
    direto na importação — o passo do anexo roda ANTES de qualquer outro.

    Hoje nada é gravado porque cada ramo de anexo tem gate de feature próprio
    (extrato Pro, image_ocr_enabled, audio_enabled); isso é defesa em
    profundidade, não este gate. Imagem e áudio percorrem o MESMO caminho
    (passos 2 e 3, depois do gate) e ficam de fora só porque exigem chave de API
    para rodar aqui.

    Controle negativo: tire o `not msg.attachments` e os quatro casos ficam
    vermelhos.
    """
    uid = _cadastro_novo()

    resposta = _diga(uid, legenda, anexos=[
        Attachment(filename=nome, content_type=tipo, data=b"OFXHEADER:100\n"),
    ])

    assert _barrado(resposta), f"anexo passou com legenda {legenda!r}: {resposta!r}"
    assert db.list_launches(uid) == []


def test_ajuda_com_secao_passa_e_o_payload_nao_registra_nada():
    """`ajuda ofx` / `help investimentos` são ajuda documentada (help_text
    resolve a seção por alias). A isenção por texto EXATO barrava as duas —
    achado do Codex no PR #308.

    As duas outras metades são o freio: o payload de `ajuda <qualquer coisa>`
    não pode virar porta de entrada (medido: o classificador manda tudo isso pra
    `help`, nunca pra despesa), e `menu <algo>` NÃO é isento, porque o
    classificador manda `menu ofx` pra out_of_scope — isentá-lo abriria bypass
    sem levar ninguém à ajuda.

    Controle negativo: tire o `HELP_SECTION_RE.match(texto)` do `pede_ajuda` e
    a primeira asserção fica vermelha.
    """
    uid = _cadastro_novo()

    secao = _diga(uid, "ajuda ofx")
    assert not _barrado(secao), f"o gate barrou 'ajuda ofx': {secao!r}"
    # "comece aqui" e não "ofx": o `ofx` da resposta viria do texto da seção
    # "start" (ela cita `.ofx`), então casar por "ofx" passaria sem provar nada.
    # Que "ajuda ofx" caia em "start" é defeito pré-existente do resolve_section
    # (recebe só o argumento do intent_router) — aqui só se mede o gate.
    assert "comece aqui" in secao.lower(), f"não veio ajuda nenhuma: {secao!r}"

    payload = _diga(uid, "ajuda gastei 50 no mercado")
    assert not _barrado(payload)
    assert db.list_launches(uid) == [], "a isenção deixou registrar um gasto"
    assert db.get_balance(uid) == 0, "a isenção deixou debitar o saldo"

    assert _barrado(_diga(uid, "menu ofx")), \
        "`menu <algo>` ficou isento e não vai pra ajuda — bypass de graça"


def test_discord_mensagem_comum_continua_barrada():
    """Par do teste acima: a isenção é só dos comandos, não da plataforma."""
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado", plataforma="discord")

    assert _barrado(resposta), f"o Discord passou por cima do gate: {resposta!r}"
    assert db.list_launches(uid) == []


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
