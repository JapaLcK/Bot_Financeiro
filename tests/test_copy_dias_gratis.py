"""O "7 dias grátis" escrito à mão na copy bate com `TRIAL_DAYS_DEFAULT` (§0.7).

HTML estático não importa Python, então o número fica duplicado nos CTAs da /
(`index.html`), no título e no subtítulo do S1 (`assinar.html`) e no título que o
`assinar.js` monta. Este teste é o que impede as cópias de divergirem da fonte
(`core/services/plan_service.py`). O S4 não entra: lá o número vem do
`trial_days` do backend.

Negativo medido: trocar um "7" de qualquer um dos três arquivos por "8" deixa vermelho.
"""
from __future__ import annotations

import pathlib
import re

from core.services.plan_service import TRIAL_DAYS_DEFAULT

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"


def _ler(nome: str) -> str:
    return (FRONTEND / nome).read_text(encoding="utf-8")


def test_os_4_ctas_da_landing_prometem_os_dias_do_default():
    links = re.findall(r'<a\b[^>]*\bdata-utm\b[^>]*>.*?</a>', _ler("index.html"), re.S)
    ctas = [a for a in links if "so-anual" not in a and "so-mensal" not in a]
    dias = [int(n) for a in ctas for n in re.findall(r"(\d+) dias grátis", a)]
    assert dias == [TRIAL_DAYS_DEFAULT] * 4, dias


def test_s1_da_assinar_promete_os_dias_do_default():
    html = _ler("assinar.html")
    titulo = re.search(r'<h1 id="s1-titulo">Seus (\d+) dias grátis do Plus começam aqui</h1>', html)
    sub = re.search(r"a primeira cobrança só vem depois dos (\d+) dias\.", html)
    js = re.search(r'"Seus (\d+) dias grátis do "', _ler("assinar.js"))
    assert titulo and sub and js, "a copy do S1 mudou: atualize este teste junto"
    assert [int(m.group(1)) for m in (titulo, sub, js)] == [TRIAL_DAYS_DEFAULT] * 3
