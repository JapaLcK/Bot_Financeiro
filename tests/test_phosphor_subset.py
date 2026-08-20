"""frontend/phosphor.css é um subset: só os ícones que o frontend realmente usa.

O risco desse subset é silencioso — um `<i class="ph ph-foo">` cujo `ph-foo` não
está no CSS não dá erro nenhum, só não desenha nada. Estes testes fecham isso:
todo ícone referenciado precisa existir no CSS servido.

A lista de referências vem da mesma extração do gerador
(scripts/build_phosphor_subset.py), importada dele para as duas não divergirem.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CSS = RAIZ / "frontend" / "phosphor.css"

_spec = importlib.util.spec_from_file_location(
    "build_phosphor_subset", RAIZ / "scripts" / "build_phosphor_subset.py"
)
_gerador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gerador)


def _declarados() -> set[str]:
    return set(re.findall(r"\.ph\.ph-([a-z0-9-]+):before", CSS.read_text(encoding="utf-8")))


def test_todo_icone_referenciado_existe_no_css():
    faltando = sorted(_gerador.icones_usados() - _declarados())
    assert not faltando, (
        "ícones usados no frontend que não estão em phosphor.css (vão sumir "
        "calados): " + ", ".join(faltando) + ". Rode scripts/build_phosphor_subset.py."
    )


def test_css_e_subset_nao_o_pacote_inteiro():
    """Se alguém regerar o arquivo cheio por cima, o subset se perde sem aviso."""
    n = len(_declarados())
    assert n < 400, f"phosphor.css tem {n} ícones — parece o pacote inteiro, não o subset."


def test_fallbacks_dos_helpers_estao_no_css():
    """phIcon/catIcon/atividade caem em nomes literais quando o mapa não bate.
    Eles não aparecem como `ph-x` em lugar nenhum, então só este teste os cobre."""
    declarados = _declarados()
    for nome in _gerador.FALLBACKS:
        assert nome in declarados, f"fallback '{nome}' ausente do CSS"


def test_nao_surgiu_fonte_dinamica_de_icone_fora_dos_mapas_conhecidos():
    """O ponto cego dos testes acima: eles usam o mesmo extrator do gerador, então
    uma QUARTA fonte de nomes (um mapa novo, um campo vindo da API) seria invisível
    para os dois. Este teste olha o outro lado — enumera os sites de interpolação
    `ph-${...}` no frontend e falha se aparecer um que o gerador não conhece.

    Se este teste falhar por um site novo e legítimo, adicione o mapa em
    MAPAS (scripts/build_phosphor_subset.py) e a expressão aqui.
    """
    esperados = {
        ("dashboard.js", "name"),        # phIcon(), alimentado por EMOJI_TO_PH
        ("settings.html", "name"),       # catIcon(), alimentado por CAT_ICONS
        ("settings.html", 'ACTIVITY_ICONS[ev.event] || "circle"'),
    }
    achados = set()
    for f in (RAIZ / "frontend").rglob("*"):
        if f.suffix not in (".html", ".js"):
            continue
        for expr in re.findall(r"ph-\$\{([^}]*)\}", f.read_text(encoding="utf-8", errors="ignore")):
            achados.add((f.name, expr.strip()))

    novos = achados - esperados
    assert not novos, (
        "site(s) de interpolação de ícone que o gerador não conhece — o subset "
        f"pode estar sem esses ícones: {sorted(novos)}"
    )


def test_regra_base_de_alinhamento_sobreviveu_ao_subset():
    """`.ph{vertical-align:-0.125em}` vem DEPOIS das regras de ícone no upstream.
    Uma geração que remonte o arquivo a partir do cabeçalho a perde, e todo ícone
    desalinha 0.125em — sem erro nenhum, só torto. Já aconteceu uma vez."""
    css = CSS.read_text(encoding="utf-8")
    assert "vertical-align:-0.125em" in css, "regra base de alinhamento sumiu do subset"
    assert "@font-face" in css and 'font-family: "Phosphor"' in css
