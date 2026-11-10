"""Fonte única (§0.7): o texto do Plano Família nas páginas estáticas bate com o código."""
import re
from pathlib import Path

from core.services.plan_service import FEATURE_MIN_TIER_V2
from db.family import FAMILY_BASE_SEATS

F = Path(__file__).resolve().parent.parent / "frontend"
RE = re.compile(r"Plano Família(?:</strong>)?: compartilhe o acesso com até (\d+) pessoas")
LI = re.compile(r"<li[^>]*>(?:(?!</li>).)*Plano Família.*?</li>", re.S)


def _card_pro(html):
    # precos.html: <h3>Pro</h3> ... </article>; index.html: data-plano="pro" ... próximo data-plano / fim da seção
    if "<h3>Pro</h3>" in html:
        i = html.index("<h3>Pro</h3>")
        return html[i:html.index("</article>", i)]
    i = html.index('data-plano="pro"')
    return html[i:html.index("</ul>", i)]


def test_familia_no_card_pro_com_vagas_do_codigo():
    assert FEATURE_MIN_TIER_V2["family"] == "pro"
    for nome in ("precos.html", "index.html"):
        m = RE.search(_card_pro((F / nome).read_text(encoding="utf-8")))
        assert m, f"{nome}: Plano Família ausente do card Pro"
        assert int(m.group(1)) == FAMILY_BASE_SEATS - 1, nome  # além do titular
        assert "Plus" not in LI.search(_card_pro((F / nome).read_text(encoding="utf-8"))).group(0), nome
