"""Isenções do gate de plano do bot (core.handle_incoming._paywall_gate).

Quem está barrado tem de conseguir DUAS coisas: assinar e pedir ajuda. É o mesmo
papel do `_GATE_EXEMPT_PREFIXES = ("/billing", "/auth", "/conta")` da web
(frontend/routes/shared.py). O veredito do gate em si mora no
`test_paywall_gate_bot.py`; aqui se mede o que ele deixa passar, e se a RESPOSTA
que o isento recebe é verdadeira pra quem está barrado (o bloco P2 no fim). Que
ele responde ele mesmo, em vez de deixar a mensagem seguir, é o
`test_paywall_gate_responde.py`.

A CLASSE de bug que este arquivo fecha (§4 do CLAUDE.md — enumerar, não
remendar): a isenção fazia string matching com `.strip().lower()` enquanto o
destino real (`intent_classifier._normalize`) tira pontuação, acento e "/" antes
de casar. Toda variante suja era roteada pra ajuda e barrada pelo gate. O Codex
apontou 4; a enumeração achou 9. A correção não foi imitar a normalização: é
perguntar ao MESMO oráculo que roteia a mensagem (`classify(allow_ai=False)`).

CONTROLE NEGATIVO DO GRUPO: no `_paywall_gate`, troque
`classify(texto, user_id=uid, allow_ai=False).intent` por um `"help" if texto
in HELP_TRIGGERS else "x"` — as linhas com pontuação/acento de
`test_isencao_cobre_toda_variante_que_o_roteamento_atende` ficam VERMELHAS, e
`menu`/`/menu` de `test_isencao_nao_alarga_para_o_que_nao_e_ajuda` também
(porque voltam a ser isentos sem irem pra ajuda nenhuma).
"""
from __future__ import annotations

import pytest

import db
from core.types import Attachment
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    barrado as _barrado,
    cadastro_novo as _cadastro_novo,
    com_plano as _com_plano,
    diga as _diga,
    v2_ligado,
)

# A enumeração virada em tabela. Cada grupo é uma coluna do "o que o roteamento
# faz com isto", medida antes de escrever a correção.
_VARIANTES_DE_AJUDA = [
    "ajuda", "AJUDA", "  ajuda  ", "/ajuda", "help", "/help",   # exatas
    "ajuda?", "ajuda!", "ajuda, por favor", "ajúda", "help!",   # pontuação/acento
    "ajuda ofx", "AJUDA OFX", "ajuda  ofx", "ajuda ofx?",       # com seção
    "/ajuda ofx", "help: ofx", "ajúda ofx", "ajuda, ofx",       # ídem, sujas
    "help investimentos", "ajuda gastei 50 no mercado",         # seção e payload
]

_VARIANTES_DE_BILLING = [
    "assinar", "ASSINAR", "/assinar", "assinar plano", "plano", "cancelar",
]

# Ausentes de propósito: "assinar?", "quero assinar!", "plano?", "assinar.".
# A enumeração foi até a perna de billing (é onde estaria o irmão do bug de
# normalização da ajuda) e o buraco EXISTE — billing_commands._normalize tira
# acento, caixa e espaço, mas NÃO tira pontuação:
#
#     is_billing_command("assinar")  -> True
#     is_billing_command("assinar?") -> False
#
# Não entra nesta correção, e o motivo é que não é regressão deste PR:
# handle_billing_command usa o MESMO is_billing_command, então "assinar?" nunca
# foi atendido por billing — caía em out_of_scope ("não entendi"). Com o gate
# ele passa a receber a mensagem do paywall, que traz o link do /precos: para
# quem está barrado a resposta MELHOROU. Consertar de verdade é mexer na
# normalização do billing, que muda o comportamento de todo mundo (inclusive de
# quem paga) — PR próprio. Fica como teto conhecido.

# O que NÃO pode ser isento: o classificador não manda nada disso pra ajuda.
_NAO_ISENTOS = [
    "menu", "/menu", "menu ofx",          # out_of_scope, não é rota de ajuda
    "gastei 50 no mercado", "saldo",
    "quero assinar um plano de saude",    # encosta em "assinar" e não é comando
]


@pytest.mark.parametrize("texto", _VARIANTES_DE_AJUDA + _VARIANTES_DE_BILLING)
def test_isencao_cobre_toda_variante_que_o_roteamento_atende(texto):
    """Para todo texto que o roteamento atende sem tocar em dinheiro, o gate
    isenta. As asserções de saldo/lançamento são a outra metade: a rota de ajuda
    só renderiza texto e a de billing só devolve link — nenhuma pode escrever."""
    uid = _cadastro_novo()

    resposta = _diga(uid, texto, plataforma="discord")

    assert not _barrado(resposta), f"o gate barrou {texto!r}: {resposta!r}"
    assert db.list_launches(uid) == [], f"{texto!r} registrou lançamento"
    assert db.get_balance(uid) == 0, f"{texto!r} mexeu no saldo"


@pytest.mark.parametrize("texto", _NAO_ISENTOS)
def test_isencao_nao_alarga_para_o_que_nao_e_ajuda(texto):
    """O par do teste acima: a isenção não pode ser mais LARGA que o roteamento.

    `menu` é o caso que mudou aqui — ele está em HELP_TRIGGERS (o desvio do
    WhatsApp usa isso ANTES do handle_incoming), mas o classificador manda `menu`
    pra out_of_scope, então isentá-lo abria bypass sem levar ninguém à ajuda. No
    WhatsApp ele nunca chega neste gate; no Discord ele já não respondia ajuda.
    """
    uid = _cadastro_novo()

    resposta = _diga(uid, texto, plataforma="discord")

    assert _barrado(resposta), f"{texto!r} escapou do gate: {resposta!r}"
    assert db.list_launches(uid) == []


@pytest.mark.parametrize("comando,esperado", [
    ("assinar", "assinar"),      # link de checkout
    ("/assinar", "assinar"),     # prefixo do Discord
    ("plano", "plano"),
    ("cancelar", "cancelar"),    # o trigger da ressalva do `ponytail:` no gate
    ("ajuda", "comece aqui"),
    # Ajuda COM seção. O texto esperado é o da seção "start" e não o da seção
    # pedida DE PROPÓSITO: intent_router passa só o argumento ("ofx") pro
    # resolve_section, que espera o texto inteiro ("ajuda ofx") e cai no
    # fallback "start". Defeito PRÉ-EXISTENTE, fora deste PR — o que se mede
    # aqui é o gate deixar passar, não a seção resolvida.
    ("ajuda ofx", "comece aqui"),
    ("help investimentos", "comece aqui"),
])
def test_discord_barrado_alcanca_billing_e_ajuda(comando, esperado):
    """No Discord o handle_incoming responde assinar/plano/ajuda ELE MESMO — o
    adapter (adapters/discord/discord_bot.py) só cai nos cogs quando a lista
    volta vazia. Sem as isenções, o gate sequestra esses comandos e o usuário
    barrado fica sem como assinar."""
    uid = _cadastro_novo()

    resposta = _diga(uid, comando, plataforma="discord")

    assert not _barrado(resposta), f"o gate sequestrou {comando!r}: {resposta!r}"
    assert esperado in resposta.lower(), f"{comando!r} respondeu: {resposta!r}"


def test_discord_mensagem_comum_continua_barrada():
    """A isenção é dos comandos, não da plataforma."""
    uid = _cadastro_novo()

    resposta = _diga(uid, "gastei 50 no mercado", plataforma="discord")

    assert _barrado(resposta), f"o Discord passou por cima do gate: {resposta!r}"
    assert db.list_launches(uid) == []


@pytest.mark.parametrize("nome,tipo", [
    ("extrato.ofx", "application/x-ofx"),
    ("extrato.csv", "text/csv"),
])
@pytest.mark.parametrize("legenda", ["ajuda", "assinar", "ajuda ofx",
                                     "ajuda?", "/ajuda ofx"])
def test_anexo_com_legenda_isenta_continua_barrado(nome, tipo, legenda):
    """A legenda do anexo vira msg.text (adapters/whatsapp/wa_parse.py), então
    sem o `not msg.attachments` um .ofx legendado "ajuda" entra pelo gate e cai
    direto na importação — o passo do anexo roda ANTES de qualquer outro.

    Hoje nada é gravado porque cada ramo de anexo tem gate de feature próprio
    (extrato Pro, image_ocr_enabled, audio_enabled); isso é defesa em
    profundidade, não este gate. Imagem e áudio percorrem o MESMO caminho
    (passos 2 e 3, depois do gate) e ficam de fora só porque exigem chave de API
    para rodar aqui.

    Controle negativo: tire o `not msg.attachments` e os casos ficam vermelhos.
    """
    uid = _cadastro_novo()

    resposta = _diga(uid, legenda, anexos=[
        Attachment(filename=nome, content_type=tipo, data=b"OFXHEADER:100\n"),
    ])

    assert _barrado(resposta), f"anexo passou com legenda {legenda!r}: {resposta!r}"
    assert db.list_launches(uid) == []


# ---------------------------------------------------------------------------
# P2: a resposta de billing conhece o estado "não escolheu plano".
#
# CONTROLE NEGATIVO: apague o `if _sem_plano_escolhido(...)` de `_handle_plano`
# e de `_handle_cancelar` (core/services/billing_commands.py) — os dois primeiros
# testes abaixo ficam vermelhos; o de `assinar` continua verde (ele é o controle
# positivo: a saída de emergência do barrado não pode ter mudado).
# ---------------------------------------------------------------------------

def test_barrado_pergunta_plano_e_nao_recebe_franquia_do_gratis():
    uid = _cadastro_novo()

    resposta = _diga(uid, "plano").lower()

    assert "30 lançamentos" not in resposta, resposta
    assert "plano: *grátis*" not in resposta, resposta
    assert "assinar plano" in resposta, resposta


def test_barrado_manda_cancelar_e_nao_ouve_que_esta_tudo_de_graca():
    uid = _cadastro_novo()

    resposta = _diga(uid, "cancelar").lower()

    assert "tudo de graça" not in resposta, resposta
    assert "assinar plano" in resposta, resposta


def test_barrado_manda_assinar_e_continua_recebendo_o_link():
    """A saída de emergência do barrado. Nada aqui pode ter mudado."""
    uid = _cadastro_novo()

    resposta = _diga(uid, "assinar")

    assert not _barrado(resposta), resposta
    assert "pigbank" in resposta.lower(), resposta
    assert "http" in resposta, resposta


def test_quem_escolheu_o_gratis_continua_vendo_a_franquia_do_gratis():
    """A copy do Grátis não pode ter sumido para quem NÃO está barrado — ela é
    verdadeira para quem já escolheu um plano e caiu no Grátis."""
    uid = _com_plano()

    resposta = _diga(uid, "plano")

    assert "30 lançamentos" in resposta, resposta
