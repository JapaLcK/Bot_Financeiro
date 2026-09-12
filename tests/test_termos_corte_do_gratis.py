"""
tests/test_termos_corte_do_gratis.py — os Termos de Uso e o fim do plano Grátis.

`frontend/termos.html` é documento CONTRATUAL, e descrevia o Grátis como o
destino da conta em três situações (fim do anual por Pix, falha de cobrança,
cancelamento) mais a ficha de limites do plano. O corte torna as quatro falsas.

Um teste pequeno de propósito: a redação é do dono, não deste arquivo. O que se
prende aqui é só o que uma mudança de código poderia tornar FALSO sem ninguém
notar — não o estilo, não a ordem, não o número de parágrafos.

**O caso do "7 dias" é §0.7 pela letra.** A carência está em
`billing_dunning.DUNNING_GRACE_DAYS` e passou a estar também, por extenso, num
documento contratual que não consegue importar Python. Quando a duplicação é
inevitável, um teste compara as duas — é o que já se faz com o subset de ícones
(`tests/test_phosphor_subset.py`). Sem ele, mudar a constante deixaria os
Termos prometendo uma janela que o produto não cumpre, que é a pior forma
possível deste bug.

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**(a) a promessa do Grátis de volta** — em `termos.html` §6, troque "o acesso ao
serviço é bloqueado — não há plano gratuito para onde a conta volte" por "a
conta volta automaticamente para o plano Grátis". VERMELHO:
  `test_os_termos_nao_prometem_plano_gratuito`

**(b) a constante e o documento divergem** — em
`core/services/billing_dunning.py`, troque `DUNNING_GRACE_DAYS = 7` por `= 10`.
VERMELHO:
  `test_a_carencia_dos_termos_bate_com_a_constante`
Direção: os Termos passam a prometer 7 dias de acesso e o produto a conceder
10 — ou, na direção cara, o contrário.

**(c) a garantia de dados some** — apague "Os dados são preservados (você não
perde lançamentos, histórico ou conta)". VERMELHO:
  `test_os_termos_continuam_garantindo_que_os_dados_ficam`
Direção: o documento deixa de afirmar a única coisa que tranquiliza quem for
bloqueado — e que é verdade: o corte bloqueia acesso, não apaga nada.

Cada injeção derruba um caso diferente, e é isso que mostra que os três medem
coisas distintas.
"""
from __future__ import annotations

import pathlib
import re

from core.services.billing_dunning import DUNNING_GRACE_DAYS

TERMOS = (pathlib.Path(__file__).resolve().parent.parent
          / "frontend" / "termos.html")


def _texto() -> str:
    """O documento com as tags fora e os espaços normalizados.

    Sem isso, `<strong>` no meio de uma frase faz uma asserção de conteúdo
    falhar por FORMA — e forma é o que envelhece primeiro
    (`docs/controles_declarados.md`)."""
    bruto = TERMOS.read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", bruto))


def test_os_termos_nao_prometem_plano_gratuito():
    """Nenhuma das quatro passagens pode devolver a conta ao Grátis.

    Cita o PREDICADO ("plano Grátis" como destino), não a seção nem a linha:
    renumerar o documento não pode quebrar este teste, e mover a frase de seção
    não pode fazê-lo passar."""
    texto = _texto()
    baixa = texto.lower()
    for proibido in ("plano grátis", "plano gratis",
                     "volta para o plano", "volta automaticamente para o plano",
                     "retorna ao plano"):
        assert proibido not in baixa, (
            f"os Termos ainda descrevem o Grátis como destino ({proibido!r}) — "
            "depois do corte a conta é bloqueada, não rebaixada")


def test_a_carencia_dos_termos_bate_com_a_constante():
    """§0.7: o número está em dois lugares porque HTML não importa Python."""
    texto = _texto()
    achados = re.findall(r"carência de (\d+) dias", texto)
    assert achados, f"não achei a carência escrita nos Termos: {texto[:400]}"
    assert len(set(achados)) == 1, f"os Termos citam carências diferentes: {achados}"
    assert int(achados[0]) == DUNNING_GRACE_DAYS, (
        f"os Termos prometem carência de {achados[0]} dias e "
        f"`billing_dunning.DUNNING_GRACE_DAYS` concede {DUNNING_GRACE_DAYS}")


def test_os_termos_continuam_garantindo_que_os_dados_ficam():
    """O corte bloqueia acesso e não apaga nada, e é isso que o documento tem de
    continuar dizendo. Sem este caso, "tirar a promessa do Grátis" poderia ter
    levado a garantia junto."""
    texto = _texto()
    assert "Os dados são preservados" in texto, texto[:400]
    assert "não perde lançamentos, histórico ou conta" in texto, texto[:400]


def test_os_termos_dizem_que_a_saida_continua_aberta():
    """Exportar os dados e excluir a conta seguem alcançáveis com o acesso
    bloqueado — é o `/settings`, isento do gate por decisão do dono, e é
    obrigação de LGPD. Passou a ser verdade relevante para quem for bloqueado, e
    não estava no documento."""
    texto = _texto()
    baixa = texto.lower()
    assert "exportar os seus dados" in baixa, texto[:400]
    assert "excluir a conta" in baixa, texto[:400]
