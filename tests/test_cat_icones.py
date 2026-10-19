"""`db/cat_icones.py`: o ícone Phosphor da categoria, derivado do emoji, é o MESMO do dashboard.js."""
import json
import re
from pathlib import Path

from db.cat_icones import EMOJI_PARA_ICONE, ICONE_NEUTRO, icone_da_categoria
from db.connection import CAT_NEUTRA_EMOJI, cat_visual

RAIZ = Path(__file__).resolve().parent.parent


def test_mapa_e_o_emoji_to_ph_do_dashboard_js():
    m = re.search(r"const EMOJI_TO_PH = (\{.*?\});", (RAIZ / "frontend" / "dashboard.js").read_text(encoding="utf-8"))
    assert m, "EMOJI_TO_PH sumiu do dashboard.js: a fonte da paridade mudou de forma"
    assert EMOJI_PARA_ICONE == json.loads(m.group(1))


def test_todo_icone_do_mapa_existe_no_phosphor_css():
    css = (RAIZ / "frontend" / "phosphor.css").read_text(encoding="utf-8")
    declarados = set(re.findall(r"\.ph\.ph-([a-z0-9-]+):before", css))
    assert declarados, "phosphor.css sem nenhum .ph.ph-X:before: o formato mudou"
    assert {*EMOJI_PARA_ICONE.values(), ICONE_NEUTRO} <= declarados


def test_icone_da_categoria_normaliza_e_cai_em_tag():
    assert icone_da_categoria("🍽️") == icone_da_categoria(" 🍽 ") == "fork-knife"  # FE0F e espaços saem
    assert icone_da_categoria("🏷️") == "tag"
    for vazio_ou_desconhecido in (None, "", "   ", "🥑", "abc"):
        assert icone_da_categoria(vazio_ou_desconhecido) == ICONE_NEUTRO


def test_cat_visual_devolve_o_icone_e_mantem_o_emoji():
    assert cat_visual("🍔", "#f59e0b") == {"emoji": "🍔", "icone": "hamburger", "cor": "#f59e0b"}
    assert cat_visual("🥑", None)["icone"] == "tag"  # emoji que o usuário escolheu e a galeria não tem
    v = cat_visual("  ", "#f59e0b")  # vazio vira o neutro, e o ícone acompanha
    assert (v["emoji"], v["icone"]) == (CAT_NEUTRA_EMOJI, "tag")
