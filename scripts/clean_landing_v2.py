"""Limpa o espelho da landing v2 (build do Lovable) em frontend/landing-v2/.

Roda após o download (scripts/sync_landing_v2.sh) ou sozinho para reaplicar
as limpezas sobre os arquivos já espelhados. Idempotente.

O que faz:
- index.html: remove o <script> de analytics (/~flock.js), o badge
  "Made with Lovable" (<a id="lovable-badge-cta"> + bloco <style> do
  #lovable-badge), corrige lang="en" -> "pt-BR" e alinha o trial para
  15 dias (a oferta vigente no produto — a página do Lovable dizia 7).
- bundles JS: mesmo alinhamento 7 dias -> 15 dias (o React hidrata o DOM
  a partir do JS; trocar só no HTML faria a página voltar a "7 dias"
  depois da hidratação).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "frontend" / "landing-v2"

TRIAL_SUBS = [("7 DIAS", "15 DIAS"), ("7 dias", "15 dias")]


def replace_trial(text: str) -> str:
    for old, new in TRIAL_SUBS:
        text = text.replace(old, new)
    return text


def clean_html(html: str) -> str:
    # Analytics do host do Lovable: <script defer src="/~flock.js" …></script>
    html, n_flock = re.subn(r'<script[^>]*src="/~flock\.js"[^>]*></script>', "", html)

    # Badge "Made with Lovable": <aside id="lovable-badge"> inteiro (dentro dele
    # o <a id="lovable-badge-cta"> e o botão de dismiss; sem <aside> aninhado).
    html, n_aside = re.subn(r'<aside[^>]*id="lovable-badge"[^>]*>.*?</aside>', "", html, flags=re.S)

    # Link interno do badge, caso sobre algum (defesa em profundidade)
    html, n_badge = re.subn(r'<a[^>]*id="lovable-badge-cta"[^>]*>.*?</a>', "", html, flags=re.S)

    # Blocos <style> e <script> dedicados ao badge (o <script> inline antes de
    # </body> é só a lógica de dismiss dele). Conta só remoções reais: o regex
    # casa com TODO <style>/<script> do documento.
    removed = {"style": 0, "script": 0}

    def _strip_badge_block(kind: str):
        def _inner(m: "re.Match[str]") -> str:
            if "lovable-badge" in m.group(1):
                removed[kind] += 1
                return ""
            return m.group(0)

        return _inner

    html = re.sub(r"<style[^>]*>(.*?)</style>", _strip_badge_block("style"), html, flags=re.S)
    html = re.sub(r"<script[^>]*>(.*?)</script>", _strip_badge_block("script"), html, flags=re.S)

    html = html.replace('lang="en"', 'lang="pt-BR"', 1)
    html = replace_trial(html)
    return html, {"flock": n_flock, "badge_aside": n_aside, **removed}


def main() -> int:
    index = LANDING / "index.html"
    html, stats = clean_html(index.read_text(encoding="utf-8"))
    if stats != {"flock": 1, "badge_aside": 1, "style": 1, "script": 1}:
        print(f"AVISO: limpeza do HTML removeu quantidades inesperadas: {stats}", file=sys.stderr)
    index.write_text(html, encoding="utf-8")

    for js in sorted((LANDING / "assets").glob("*.js")):
        raw = js.read_text(encoding="utf-8")
        fixed = replace_trial(raw)
        if fixed != raw:
            js.write_text(fixed, encoding="utf-8")

    rest = [
        p
        for p in LANDING.rglob("*")
        if p.is_file() and p.suffix in {".html", ".js"}
        and re.search(r"7 [dD][iI][aA][sS]", p.read_text(encoding="utf-8", errors="ignore"))
    ]
    if rest:
        for p in rest:
            print(f"ERRO: '7 dias' restante em {p.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print("landing-v2 limpa: badge/flock removidos, lang=pt-BR, trial=15 dias")
    return 0


if __name__ == "__main__":
    sys.exit(main())
