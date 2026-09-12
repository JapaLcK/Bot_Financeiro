"""
tests/test_ajuda_do_cortado_copy.py — a COPY da ajuda de quem não tem acesso.

Arquivo próprio porque `tests/test_tutorial_no_corte.py` passou de 350 linhas e
porque o assunto é outro: lá são as PORTAS (por onde o cortado chegava ao
tutorial), aqui é o TEXTO que ele recebe quando chega.

**DUAS formas, e a distinção é a mesma que o `_paywall_gate` faz** entre
`estado is None` e a linha existente. A seção nasceu com uma forma só e voltou a
cometer a mentira que o gate evita há três rodadas: dizia "Seus dados continuam
guardados" e "pigbankai.com/settings continua aberto" para quem NUNCA fez
cadastro web — não há dados dele guardados nem painel para ele abrir. Medido,
mesmo uid, mesma sessão::

    "qualquer coisa" → "Oi! Que bom te ver por aqui…"            (forma certa)
    "ajuda"          → "…pigbankai.com/settings continua aberto."  (forma errada)

**E ela vive FORA do `HELP_SECTIONS`**, o que fecha outra classe: enquanto era
uma chave lá, `resolve_section("ajuda sem_acesso")` a devolvia pelo fallback
`if arg in HELP_SECTIONS` e um PAGANTE lia "sua conta está sem plano ativo".

CONTROLE DECLARADO (`docs/controles_declarados.md`) — em `core/help_text.py`,
troque o corpo de `_AJUDA_SEM_ACESSO["sem_cadastro"]` pelo de `["web"]` (as duas
formas viram uma; nada é apagado). VERMELHOS:
  `test_a_forma_sem_cadastro_nao_promete_dados_guardados`
  `test_a_forma_sem_cadastro_nao_manda_pro_settings`
Direção: promete painel e dados a quem nunca teve conta web — a mesma mentira
que o gate gastou três parágrafos evitando na população só-WhatsApp.
"""
from __future__ import annotations

import pytest

from core.help_text import HELP_SECTIONS, render_ajuda_sem_acesso, resolve_section


@pytest.mark.parametrize("tem_cadastro_web", [True, False])
def test_a_ajuda_do_cortado_nao_manda_tentar_comando(tem_cadastro_web):
    """Vale para as DUAS formas: o comando seguinte seria recusado pelo gate, e
    convidar antes de recusar é a pior ordem possível das duas mensagens."""
    texto = render_ajuda_sem_acesso("whatsapp", tem_cadastro_web).lower()
    for instrucao in ("gastei ", "recebi ", "tente", "experimenta"):
        assert instrucao not in texto, f"a ajuda do cortado manda tentar: {texto}"


@pytest.mark.parametrize("tem_cadastro_web", [True, False])
def test_a_ajuda_do_cortado_nao_aponta_pro_tutorial(tem_cadastro_web):
    """E não manda digitar a palavra que devolve o paywall."""
    assert "tutorial" not in render_ajuda_sem_acesso("whatsapp", tem_cadastro_web).lower()


@pytest.mark.parametrize("tem_cadastro_web", [True, False])
def test_a_ajuda_do_cortado_diz_o_que_importa(tem_cadastro_web):
    """POSITIVO: ela não pode ter virado uma parede. As duas formas têm de dizer
    por que parou e para onde ir."""
    texto = render_ajuda_sem_acesso("whatsapp", tem_cadastro_web).lower()
    assert "plano ativo" in texto, texto
    assert "precos" in texto, texto


def test_a_forma_sem_cadastro_nao_promete_dados_guardados():
    """Quem nunca fez cadastro web não tem dados nossos para "continuarem
    guardados" — dizer que tem é supor um cadastro que não existe."""
    assert "guardados" not in render_ajuda_sem_acesso("whatsapp", False).lower()


def test_a_forma_sem_cadastro_nao_manda_pro_settings():
    """Nem painel para ele abrir. A saída de emergência do `/settings` é real
    para quem TEM conta — e é isso que a outra forma diz."""
    assert "settings" not in render_ajuda_sem_acesso("whatsapp", False).lower()
    assert "settings" in render_ajuda_sem_acesso("whatsapp", True).lower()


def test_a_ajuda_do_cortado_nao_vaza_pra_quem_paga():
    """Fora do `HELP_SECTIONS` de propósito: sem chave, não há fallback,
    `render_full`, dropdown do Discord nem `_TOPIC_MAP` que a alcance."""
    assert "sem_acesso" not in HELP_SECTIONS
    assert resolve_section("ajuda sem_acesso") == "start"
    assert resolve_section("ajuda sem acesso") == "start"
