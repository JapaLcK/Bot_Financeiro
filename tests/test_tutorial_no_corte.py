"""
tests/test_tutorial_no_corte.py — o tutorial não convida quem foi cortado a
fazer o que a próxima mensagem recusa.

Sem o prefixo `wa_` de propósito: metade do conserto mora no `_paywall_gate`,
que é dos DOIS canais, e o caso do Discord está aqui embaixo.

Arquivo próprio porque `tests/test_wa_botao_velho_no_corte.py` passou de 350
linhas (`tests/test_max_lines_python.py`) e porque o assunto é outro: lá é o
BOTÃO VELHO que escreve no banco, aqui é ORIENTAÇÃO que instrui uma ação
impossível. A bancada (`_conta`, `bancada`, `_clique`, `_texto`) vem por IMPORT
do irmão — uma fonte só (§0.7).

**São DUAS portas e elas foram consertadas em camadas diferentes**, e isso é o
ponto do arquivo:

  · o BOTÃO do tutorial no WhatsApp não passa pelo `handle_incoming` — gate
    próprio em `adapters/whatsapp/wa_runtime.py`;
  · `tutorial` DIGITADO chega ao `_paywall_gate`, que o isentava junto com a
    ajuda (`ajuda in ("help", "help.tutorial")`). O token saiu de lá, o que
    conserta o WhatsApp **e o Discord** de uma vez.

A ajuda genérica continua isenta nos dois lugares: ela EXPLICA, o tutorial MANDA
TENTAR. Medido 2026-09-11: `classify("tutorial")` → `help.tutorial`,
`classify("ajuda")` → `help`.
"""
from __future__ import annotations

# `_gate_ligado` é autouse e vem JUNTO de propósito: fixture autouse só vale no
# módulo que a define, e sem ela o `conftest` roda com `PLANS_V2_ENABLED=0` — o
# gate se auto-desliga, nada é barrado e os dois negativos daqui ficariam verdes
# medindo NADA. Custou dois vermelhos antes de eu notar.
from _paywall_gate_helpers import diga as _diga
from test_wa_botao_velho_no_corte import (  # noqa: F401  (fixtures por import)
    _clique, _conta, _gate_ligado, _texto, bancada,
)


# ── O TUTORIAL convida a fazer o que a próxima mensagem recusa ───────────────
#
# Seis dos dez passos de `adapters/whatsapp/wa_tutorial.py` mandam tentar um
# comando ("gastei 50 no mercado", "Tente: gastei 10 no café") e o `tut_skip`
# responde "✅ Pode usar à vontade!". Para quem foi cortado isso não é leitura:
# é instrução para fazer algo que a mensagem seguinte recusa.
#
# **Gatear a ENTRADA, e não reescrever os passos.** Copy própria de bloqueado
# exigiria ramo em seis lugares de `wa_tutorial.py`, módulo que hoje não conhece
# plano nenhum, contra duas linhas reusando `_bloqueado_pelo_corte`; e a
# mensagem do `_paywall_gate` já entrega o próximo passo certo (o link). A ajuda
# genérica continua alcançável — é a mesma razão do opt-out e do `/settings`.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — troque os dois
# `_bloqueado_pelo_corte(...)` dos ramos do tutorial por `False` (troca de
# valor; os blocos continuam lá). VERMELHOS (medido 2026-09-11):
#   `test_cortado_tocando_o_botao_do_tutorial_nao_e_convidado_a_tentar`
#   `test_cortado_digitando_tutorial_tambem_e_barrado`
# Direção: o produto convida quem não tem acesso a registrar um gasto, e o
# registro seguinte é recusado — a pior ordem possível das duas mensagens.
#
# Positivos do grupo, VERDES sob a injeção:
#   `test_cortado_ainda_alcanca_o_menu_de_ajuda`
#   `test_pagante_continua_vendo_o_tutorial`


def test_cortado_tocando_o_botao_do_tutorial_nao_e_convidado_a_tentar(monkeypatch, bancada):
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "handle_tutorial_button", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("o cortado entrou no tutorial")))
    monkeypatch.setattr(wr, "get_tutorial_button_id", lambda raw: "tut_skip")

    _clique(uid, monkeypatch, "tut_skip")

    assert respostas, "o cortado tocou o tutorial e não recebeu nada"
    assert "plano" in respostas[0].lower(), respostas
    baixa = " ".join(respostas).lower()
    assert "gastei" not in baixa, f"o cortado foi convidado a registrar: {respostas}"


def test_cortado_digitando_tutorial_tambem_e_barrado(monkeypatch, bancada):
    """O gêmeo digitado: gatear um e não o outro abriria a porta por uma palavra."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "send_welcome", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("o cortado abriu o tutorial digitando")))

    _texto(uid, monkeypatch, "tutorial")

    assert respostas, "o cortado digitou `tutorial` e não recebeu nada"
    assert "plano" in respostas[0].lower(), respostas


def test_cortado_continua_recebendo_AJUDA_mas_a_dele(monkeypatch, bancada):
    """POSITIVO, e a versão anterior deste caso PROVAVA O BUG.

    Ele afirmava que o ramo do menu de ajuda era isento, e escolhia
    `help_gastos` como id — com `help_tutorial` a mesma asserção teria mostrado
    o tutorial saindo para conta cortada. Positivo que fixa o comportamento
    errado é pior que positivo nenhum.

    O que se mede agora é o certo: o cortado NÃO fica sem resposta (a ajuda é
    saída de emergência, como o opt-out e o `/settings`) e o que ele recebe é a
    seção `sem_acesso` — que explica sem mandar tentar."""
    respostas, _ = bancada
    uid = _conta(cortada=True)
    secoes: list[str] = []

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_tutorial_button_id", lambda raw: None)
    monkeypatch.setattr(wr, "get_help_menu_id", lambda raw: "help_gastos")
    monkeypatch.setattr(wr, "send_help_section",
                        lambda to, hid: secoes.append(hid))

    _clique(uid, monkeypatch, "help_gastos")

    assert secoes == [], f"o cortado recebeu a seção normal do menu: {secoes}"
    assert respostas, "o cortado pediu ajuda e não recebeu nada"
    assert "sem plano ativo" in respostas[0].lower(), respostas
    assert "gastei" not in " ".join(respostas).lower(), respostas


# ── As TRÊS portas que sobraram do conserto anterior ─────────────────────────
#
# Gatear o BOTÃO do tutorial e o texto `tutorial` fechou duas entradas e deixou
# três, todas medidas com conta cortada real:
#
#   (a) o item "🚀 Tutorial" do MENU de ajuda — o menu é uma LISTA, não
#       `button_reply`, então `get_tutorial_button_id` devolve `None` e a
#       mensagem cai no ramo do menu, que estava isento;
#   (b) `ajuda tutorial` / `help tutorial` / `ajuda guia` — `classify` os chama
#       de `help` (regra `^(ajuda|help)\s+\w+`) e `resolve_section` devolve
#       `"tutorial"`. Decidir pelo CLASSIFICADOR era a pergunta errada;
#   (c) a SAUDAÇÃO depois do auto-link, acima de tudo no arquivo.
#
# O conserto não fecha as três portas: move a decisão para o DESTINO. Se vai
# renderizar ajuda, o cortado recebe a seção `sem_acesso` — venha ele por onde
# vier, nos dois canais.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — em `core/help_text.py`,
# troque o corpo da seção `sem_acesso` pelo da `start` (troca de VALOR; a seção
# continua existindo e o gate continua roteando para ela). VERMELHOS:
#   `test_a_ajuda_do_cortado_nao_manda_tentar_comando`
#   `test_a_ajuda_do_cortado_nao_aponta_pro_tutorial`
# Direção: o cortado é mandado a tentar um comando que o gate recusa — e a
# apontar para a palavra que devolve o paywall. A pior ordem das duas mensagens.


def test_cortado_tocando_TUTORIAL_no_menu_de_ajuda_nao_recebe_o_tour(monkeypatch, bancada):
    """Porta (a): o item do menu é LISTA, não botão — escapava do gate do botão."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_tutorial_button_id", lambda raw: None)
    monkeypatch.setattr(wr, "get_help_menu_id", lambda raw: "help_tutorial")
    monkeypatch.setattr(wr, "send_help_section", lambda to, hid: (
        _ for _ in ()).throw(AssertionError("o cortado recebeu a seção do menu")))

    _clique(uid, monkeypatch, "help_tutorial")

    assert respostas and "sem plano ativo" in respostas[0].lower(), respostas


def test_cortado_dizendo_oi_depois_do_autolink_nao_recebe_o_tour(monkeypatch, bancada):
    """Porta (c): a saudação, no topo do arquivo, antes de qualquer gate."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "send_welcome", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("o cortado recebeu o welcome")))
    monkeypatch.setattr(wr, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "linked",
                                                             "user_id": uid})
    monkeypatch.setattr(wr, "get_or_create_canonical_user",
                        lambda provider, external_id: uid)
    from adapters.whatsapp.wa_parse import InboundMessage
    import uuid as _uuid
    wr.process_message(InboundMessage(
        wa_id="5511999990000", text="oi", timestamp="1", attachments=[],
        raw={"id": f"wamid.{_uuid.uuid4().hex[:10]}", "type": "text"}))

    assert respostas and "sem plano ativo" in respostas[0].lower(), respostas


def test_a_ajuda_do_cortado_nao_manda_tentar_comando():
    """Porta (d), a que o próprio conserto anterior abriu: a ajuda GENÉRICA que
    ficou isenta dizia "• `gastei 50 mercado`". Convidar a tentar e recusar em
    seguida é a pior ordem possível das duas mensagens."""
    from core.help_text import render_help
    texto = render_help("sem_acesso", "whatsapp").lower()
    for instrucao in ("gastei ", "recebi ", "tente", "experimenta"):
        assert instrucao not in texto, f"a ajuda do cortado manda tentar: {texto}"


def test_a_ajuda_do_cortado_nao_aponta_pro_tutorial():
    """E não manda digitar a palavra que devolve o paywall."""
    from core.help_text import render_help
    assert "tutorial" not in render_help("sem_acesso", "whatsapp").lower()


def test_a_ajuda_do_cortado_diz_o_que_importa():
    """POSITIVO do par: ela não pode ter virado uma parede.

    As três coisas que quem foi cortado precisa: por que parou, que os dados
    estão guardados, e para onde ir."""
    from core.help_text import render_help
    texto = render_help("sem_acesso", "whatsapp").lower()
    assert "sem plano ativo" in texto, texto
    assert "guardados" in texto, texto
    assert "precos" in texto, texto


def test_pagante_continua_vendo_o_tutorial(monkeypatch, bancada):
    """POSITIVO: o gate DISCRIMINA. Quem paga continua entrando no tutorial."""
    respostas, _ = bancada
    uid = _conta(cortada=False)
    tocados: list[str] = []

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_tutorial_button_id", lambda raw: "tut_skip")
    monkeypatch.setattr(wr, "handle_tutorial_button",
                        lambda wa_id, bid: tocados.append(bid))

    _clique(uid, monkeypatch, "tut_skip")

    assert tocados == ["tut_skip"], f"o pagante foi barrado no tutorial: {respostas}"


def test_cortado_digitando_tutorial_no_DISCORD_tambem_e_barrado():
    """A prova de que o conserto do `help.tutorial` vale para os DOIS canais.

    Isto estava DEDUZIDO ("os dois entram no mesmo `_paywall_gate`") e virou
    medição, porque o Discord tem ordenação própria: `discord_bot.py:180` chama
    `core_handle_incoming(incoming)` ANTES dos cogs e, se ele responder, os cogs
    não rodam. A dedução estava certa — mas a razão de escrever o caso é que uma
    ordenação diferente a teria invalidado sem nenhum teste ficar vermelho.

    Vai pelo `handle_incoming` com `platform="discord"`, que é o mesmo objeto
    que o adapter monta (§3: rode a conversa, não a função). Não sobe o bot: o
    que muda entre os canais é o despacho, e o despacho está lido acima.
    """
    uid = _conta(cortada=True)

    resposta = _diga(uid, "tutorial", plataforma="discord")

    baixa = resposta.lower()
    assert "plano" in baixa, f"o cortado leu o tutorial no Discord: {resposta!r}"
    assert "gastei" not in baixa, (
        f"o cortado foi convidado a registrar um gasto no Discord: {resposta!r}")


def test_pagante_continua_lendo_o_tutorial_no_DISCORD():
    """POSITIVO do par acima: o conserto tirou o tutorial de quem NÃO tem
    acesso, não de todo mundo. Sem ele, um gate que recusasse tudo passaria."""
    uid = _conta(cortada=False)

    resposta = _diga(uid, "tutorial", plataforma="discord")

    assert "gastei" in resposta.lower(), (
        f"o pagante perdeu o tutorial no Discord: {resposta!r}")
