"""
build_brand_assets.py — pipeline de otimização dos assets de marca (Fase 3).

Lê os PNGs crus de identidade/ e gera versões web-ready:
  frontend/brand/            → favicon, avatar, mascote, stickers (web, webp)
  assets/wa_stickers/        → stickers 512x512 webp <=100KB (spec WhatsApp)

Idempotente: regenera tudo do zero. Não toca nos originais.
Uso: python scripts/build_brand_assets.py
"""
from __future__ import annotations

import pathlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "identidade"
WEB = ROOT / "frontend" / "brand"
WEB_STICKERS = WEB / "stickers"
WA = ROOT / "assets" / "wa_stickers"

# Número do arquivo cru → nome semântico (pose → contexto)
STICKERS = {
    "26": "point",          # apontando — CTA/boas-vindas
    "27": "success",        # soco pro alto — ação concluída
    "28": "ok",             # 👍 — confirmação
    "29": "thinking",       # pensativo + cifrão — analisando
    "30": "loading",        # no laptop — processando
    "31": "income",         # segurando moeda — receita/economia
    "32": "hello",          # braços cruzados — neutro/perfil
    "33": "goal",           # dois punhos + confete — meta batida
    "34": "expense-alert",  # chocado com conta — gasto alto
    "35": "approved",       # dois polegares — tudo certo
    "36": "report",         # prancheta gráfico ↑ — relatório
    "37": "chill",          # relaxando no puff — finanças em dia
}


def _trim_alpha(im: Image.Image) -> Image.Image:
    """Corta a moldura transparente em volta do sticker (bbox do alpha)."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def _fit_square(im: Image.Image, size: int) -> Image.Image:
    """Encaixa a imagem (aspecto preservado) numa canvas quadrada transparente."""
    im = im.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return canvas


def _save_webp(im: Image.Image, path: pathlib.Path, quality: int = 82, target_kb: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    q = quality
    while True:
        im.save(path, "WEBP", quality=q, method=6)
        kb = path.stat().st_size / 1024
        if target_kb is None or kb <= target_kb or q <= 40:
            print(f"  {path.relative_to(ROOT)}  {im.size}  {kb:.0f}KB  q{q}")
            return
        q -= 8


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    WEB_STICKERS.mkdir(parents=True, exist_ok=True)
    WA.mkdir(parents=True, exist_ok=True)

    # ── Favicon + avatar (do avatar circular WHATSAPP 3000²) ──────────────
    avatar_src = Image.open(SRC / "PIGBANK.WHATSAPP.png").convert("RGB")
    print("favicon / avatar:")
    for size in (32, 180):
        out = WEB / ("favicon.png" if size == 32 else "apple-touch-icon.png")
        avatar_src.resize((size, size), Image.LANCZOS).save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(ROOT)}  {size}x{size}  {out.stat().st_size/1024:.0f}KB")
    _save_webp(avatar_src.convert("RGBA").resize((128, 128), Image.LANCZOS), WEB / "avatar.webp", quality=88)

    # ── Mascote 3D corpo inteiro (onboarding/paywall) ─────────────────────
    print("mascote:")
    mascot = _trim_alpha(Image.open(SRC / "PIGBANK.PIGYY.PNG.png"))
    m = mascot.copy()
    m.thumbnail((520, 520), Image.LANCZOS)
    _save_webp(m, WEB / "mascot.webp", quality=86)

    # ── Stickers: web (256px) + WhatsApp (512² <=100KB) ───────────────────
    print("stickers web + whatsapp:")
    for num, name in STICKERS.items():
        raw = _trim_alpha(Image.open(SRC / f"{num}.png"))
        web = raw.copy()
        web.thumbnail((256, 256), Image.LANCZOS)
        _save_webp(web, WEB_STICKERS / f"{name}.webp", quality=84)
        _save_webp(_fit_square(raw, 512), WA / f"{name}.webp", quality=90, target_kb=98)


if __name__ == "__main__":
    main()
