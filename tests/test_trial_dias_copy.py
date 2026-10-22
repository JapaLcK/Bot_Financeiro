"""Todo "N dias grátis" que o produto OFERECE é o TRIAL_DAYS_DEFAULT.

Varredura do texto versionado (frontend/, webapp/src/, core/, adapters/, app/, api/): o dia
que o trial mudou de 15 para 7, a copy ficou para trás em 11 arquivos. Esta
varredura é o que pega o próximo. Fixtures de teste (tests/) ficam de fora de
propósito: simulam o servidor devolvendo outro número.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

from core.services.plan_service import TRIAL_DAYS_DEFAULT

RAIZ = pathlib.Path(__file__).resolve().parent.parent
_ESP = r"(?:\s|&nbsp;|-)+"
RE = re.compile(
    rf"(\d+){_ESP}dias{_ESP}(?:gr[aá]tis|gratuit[oa]s?|(?:de{_ESP})?(?:teste|trial|pro)\b|p(?:ra|ara){_ESP}testar)"
    rf"|teste{_ESP}de{_ESP}(\d+){_ESP}dias"
    r"|Trial de (\d+) dias"
    r"|gratuita de <strong>(\d+) dias"
    r"|trial gratuito de (\d+) dias",
    re.IGNORECASE,
)
OBRIGATORIOS = {
    "frontend/precos.html", "frontend/termos.html", "frontend/whatsapp.html",
    "frontend/dashboard.html", "frontend/home.html",
    "webapp/src/components/ui/comparison-3.tsx", "frontend/precos-app.js",
}


def _achados() -> dict[str, list[int]]:
    arquivos = subprocess.run(
        ["git", "ls-files", "frontend", "webapp/src", "core", "adapters", "app", "api"],
        cwd=RAIZ, capture_output=True, text=True, check=True,
    ).stdout.split()
    achados: dict[str, list[int]] = {}
    for rel in arquivos:
        try:
            texto = (RAIZ / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        nums = [int(next(g for g in m.groups() if g)) for m in RE.finditer(texto)]
        if nums:
            achados[rel] = nums
    return achados


def test_todo_numero_de_dias_gratis_e_o_padrao():
    errados = {
        rel: nums for rel, nums in _achados().items()
        if any(n != TRIAL_DAYS_DEFAULT for n in nums)
    }
    assert not errados, f"copy com dias != {TRIAL_DAYS_DEFAULT}: {errados}"


def test_controle_positivo_a_varredura_enxerga_os_arquivos_obrigatorios():
    faltando = OBRIGATORIOS - set(_achados())
    assert not faltando, f"a varredura não achou o trial em: {sorted(faltando)}"
