"""Páginas estáticas, assets, SEO e health — rotas públicas sem auth e sem banco.

Etapa 1 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.
"""

import asyncio
import html as _html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel

from frontend.routes.shared import FRONTEND_DIR, html_file, limiter, public_site_url

router = APIRouter()


class ContactBody(BaseModel):
    name: str = ""
    email: str = ""
    subject: str = ""
    message: str = ""
    website: str = ""  # honeypot: bots preenchem; humanos não veem


@router.get("/")
async def serve_landing():
    return html_file(FRONTEND_DIR / "index.html")


@router.get("/app")
async def serve_dashboard():
    return html_file(FRONTEND_DIR / "dashboard.html")


@router.get("/home")
async def serve_home():
    return html_file(FRONTEND_DIR / "home.html")


@router.get("/settings")
async def serve_settings():
    return html_file(FRONTEND_DIR / "settings.html")


@router.get("/reset-password")
async def serve_reset_password():
    return html_file(FRONTEND_DIR / "reset-password.html")


@router.get("/onboarding")
async def serve_onboarding():
    return html_file(FRONTEND_DIR / "onboarding.html")


@router.get("/login")
async def serve_login():
    return html_file(FRONTEND_DIR / "login.html")


@router.get("/cadastro")
async def serve_cadastro():
    return html_file(FRONTEND_DIR / "cadastro.html")


@router.get("/static/auth-refresh.js")
async def serve_auth_refresh_js():
    """Interceptor de fetch que renova access em 401. Incluído nas páginas
    autenticadas (dashboard, home, settings, onboarding)."""
    path = FRONTEND_DIR / "auth-refresh.js"
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


@router.get("/privacy")
async def serve_privacy():
    return html_file(FRONTEND_DIR / "privacy.html")


@router.get("/termos")
async def serve_termos():
    return html_file(FRONTEND_DIR / "termos.html")


@router.get("/changelog")
async def serve_changelog():
    return html_file(FRONTEND_DIR / "changelog.html")


def _guide_card_html(g: dict) -> str:
    """Card de um guia pra seção 'Continue lendo' (link interno /blog/<slug>)."""
    return (
        f'<a class="article" href="/blog/{g["slug"]}">'
        f'<div class="article-thumb article-thumb-emoji">{g["emoji"]}</div>'
        f'<div class="article-body">'
        f'<span class="tag-cat">{_html.escape(g["category"])}</span>'
        f'<h3>{_html.escape(g["title"])}</h3>'
        f'<div class="meta">Leitura de {_html.escape(g["read_time"])}</div>'
        f'</div></a>'
    )


@router.get("/blog/{slug}")
async def serve_blog_guide(slug: str):
    """Página de um guia/dica evergreen (conteúdo próprio do PigBank).

    Renderizada no servidor a partir de core.blog_guides + o template
    blog-article.html. Embaixo do artigo vão os outros guias ('Continue lendo').
    """
    from core.blog_guides import get_guide, other_guides

    guide = get_guide(slug)
    if not guide:
        raise HTTPException(status_code=404, detail="Guia não encontrado.")

    more = "".join(_guide_card_html(g) for g in other_guides(slug))
    template = (FRONTEND_DIR / "blog-article.html").read_text(encoding="utf-8")
    page = (
        template
        .replace("{{TITLE}}", _html.escape(guide["title"]))
        .replace("{{DESCRIPTION}}", _html.escape(guide["description"]))
        .replace("{{CANONICAL}}", f"https://pigbankai.com/blog/{slug}")
        .replace("{{CATEGORY}}", _html.escape(guide["category"]))
        .replace("{{READ_TIME}}", _html.escape(guide["read_time"]))
        .replace("{{EMOJI}}", guide["emoji"])
        .replace("{{BODY}}", guide["body"])            # HTML confiável (nosso)
        .replace("{{MORE_GUIDES}}", more)
    )
    return Response(content=page, media_type="text/html; charset=utf-8")


@router.get("/whatsapp")
async def serve_whatsapp():
    return html_file(FRONTEND_DIR / "whatsapp.html")


@router.get("/funcionalidades")
async def serve_funcionalidades():
    return html_file(FRONTEND_DIR / "funcionalidades.html")


@router.get("/comandos")
async def serve_comandos():
    return html_file(FRONTEND_DIR / "comandos.html")


@router.get("/comandos-app")
async def serve_comandos_app():
    """Versao logged-in da pagina /comandos. Layout interno (mesmo header
    da home), personalizado com base no snapshot/plano. Mantém a URL
    /comandos pra landing publica intacta."""
    return html_file(FRONTEND_DIR / "comandos-app.html")


@router.get("/api/commands-catalog")
async def get_commands_catalog():
    """Catálogo de "O que pedir ao Piggy" pra o modal da home.

    Source-of-truth em core/commands_catalog.CATALOG (mesma usada pelo
    WhatsApp e Discord). Endpoint público — não tem dado sensível.
    """
    from core.commands_catalog import CATALOG
    return {"catalog": CATALOG}


@router.get("/api/blog/news")
async def get_blog_news(limit: int = 12):
    """Notícias financeiras curadas pelo news_bot (core/services/news_bot.py).

    Curadoria/link-out: título + resumo original + link pra fonte. Público, sem
    dado sensível. db.get_recent_news é síncrono → roda em thread pool.
    """
    import db

    try:
        rows = await asyncio.to_thread(db.get_recent_news, limit)
    except Exception:
        # Tabela pode não existir ainda numa primeira subida — degrada pra vazio.
        return {"news": []}

    news = [
        {
            "source": r["source"],
            "url": r["source_url"],
            "title": r["title"],
            "summary": r["summary"],
            "category": r.get("category"),
            "emoji": r.get("thumb_emoji") or "📰",
            "image": r.get("image_url"),
            "published_at": r["published_at"].isoformat() if r.get("published_at") else None,
        }
        for r in rows
    ]
    return {"news": news}


@router.get("/como-funciona")
async def serve_como_funciona():
    return html_file(FRONTEND_DIR / "como-funciona.html")


@router.get("/precos")
async def serve_precos():
    return html_file(FRONTEND_DIR / "precos.html")


@router.get("/suporte")
async def serve_suporte():
    return html_file(FRONTEND_DIR / "suporte.html")


@router.get("/robots.txt")
async def serve_robots_txt():
    content = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /app",
        "Disallow: /home",
        "Disallow: /settings",
        "Disallow: /onboarding",
        "Disallow: /reset-password",
        "Disallow: /auth/",
        "Disallow: /admin",
        f"Sitemap: {public_site_url('/sitemap.xml')}",
        "",
    ])
    return Response(content=content, media_type="text/plain")


@router.get("/sitemap.xml")
async def serve_sitemap_xml():
    urls = [
        ("/", "weekly", "1.0"),
        ("/whatsapp", "weekly", "0.8"),
        ("/funcionalidades", "weekly", "0.8"),
        ("/como-funciona", "weekly", "0.8"),
        ("/precos", "weekly", "0.7"),
        ("/suporte", "weekly", "0.7"),
        ("/privacy", "monthly", "0.4"),
        ("/termos", "monthly", "0.4"),
        ("/changelog", "weekly", "0.5"),
    ]
    items = "\n".join(
        "  <url>\n"
        f"    <loc>{public_site_url(path)}</loc>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>"
        for path, changefreq, priority in urls
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</urlset>\n"
    )
    return Response(content=content, media_type="application/xml")


@router.get("/favicon.png")
async def serve_favicon():
    return FileResponse(FRONTEND_DIR / "favicon.png", media_type="image/png")


@router.get("/manifest.json")
async def serve_manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")


@router.get("/service-worker.js")
async def serve_sw():
    resp = FileResponse(FRONTEND_DIR / "service-worker.js", media_type="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"]          = "no-cache"
    return resp


@router.get("/modals.js")
async def serve_modals_js():
    """Componente de modal estilizado (alertModal/confirmModal) usado em todas
    as paginas no lugar dos dialogs nativos do browser."""
    return FileResponse(FRONTEND_DIR / "modals.js", media_type="application/javascript")


@router.get("/nav-auth.js")
async def serve_nav_auth_js():
    """Nav ciente de login nas páginas de marketing: troca 'Entrar/Começar'
    por 'Ir para o dashboard' quando o usuário está autenticado."""
    return FileResponse(
        FRONTEND_DIR / "nav-auth.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/blog-news.js")
async def serve_blog_news_js():
    """JS da seção 'Notícias do mercado' do /blog (consome /api/blog/news).
    Externalizado (não inline) pra viabilizar remover 'unsafe-inline' do CSP."""
    return FileResponse(
        FRONTEND_DIR / "blog-news.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/dashboard.js")
async def serve_dashboard_js():
    """JS principal do dashboard, extraído do inline de dashboard.html
    (refactor Fase 1: viabiliza remover 'unsafe-inline' do script-src).
    no-cache: revalida via etag a cada load pra não dessincronizar do HTML
    (servido no-store) em deploys."""
    return FileResponse(
        FRONTEND_DIR / "dashboard.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/dashboard-chat.js")
async def serve_dashboard_chat_js():
    """Widget de chat IA (Piggy) do dashboard, extraído do inline."""
    return FileResponse(
        FRONTEND_DIR / "dashboard-chat.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/dashboard.css")
async def serve_dashboard_css():
    """CSS do dashboard, extraído do inline de dashboard.html.
    no-cache: revalida via etag pra não dessincronizar do HTML em deploys."""
    return FileResponse(
        FRONTEND_DIR / "dashboard.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/dashboard-mobile.css")
async def serve_dashboard_mobile_css():
    """Overrides mobile do dashboard (o <link> só baixa em viewport ≤900px)."""
    return FileResponse(
        FRONTEND_DIR / "dashboard-mobile.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/site.css")
async def serve_site_css():
    """Sistema de design do site de marketing (protótipo v2).
    no-cache: revalida sempre (304 se não mudou) — o site está em iteração
    ativa, então mudanças de CSS precisam aparecer na hora."""
    return FileResponse(
        FRONTEND_DIR / "site.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/brand.css")
async def serve_brand_css():
    """Design tokens da marca (paleta, tokens semânticos, @font-face Inter).
    Cache longo — muda pouco; querystring de versão invalida se precisar."""
    return FileResponse(
        FRONTEND_DIR / "brand.css",
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/fonts/{name}")
async def serve_font(name: str):
    """Serve as woff2 da Inter self-hostada. Allowlist explícita — sem path
    traversal, só arquivos conhecidos."""
    allowed = {
        "Inter-Regular.woff2", "Inter-Medium.woff2", "Inter-SemiBold.woff2",
        "Inter-Bold.woff2", "Inter-ExtraBold.woff2", "Inter-Black.woff2",
    }
    if name not in allowed:
        return Response(status_code=404)
    return FileResponse(
        FRONTEND_DIR / "fonts" / name,
        media_type="font/woff2",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


_BRAND_MEDIA = {".png": "image/png", ".webp": "image/webp"}


@router.get("/brand/{path:path}")
async def serve_brand_asset(path: str):
    """Serve os assets de marca de frontend/brand/ (favicon, avatar, mascote,
    stickers). Sem path traversal: só nomes [a-z0-9_-], um nível opcional de
    subpasta (stickers/), extensão png/webp. Cache imutável."""
    import posixpath
    import re

    parts = [p for p in path.split("/") if p]
    if not 1 <= len(parts) <= 2 or any(
        not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?", p) for p in parts
    ):
        return Response(status_code=404)

    base = (FRONTEND_DIR / "brand").resolve()
    target = (base / posixpath.join(*parts)).resolve()
    if base not in target.parents or not target.is_file():
        return Response(status_code=404)
    media = _BRAND_MEDIA.get(target.suffix.lower())
    if media is None:
        return Response(status_code=404)
    return FileResponse(
        target,
        media_type=media,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/wa")
async def open_whatsapp_bot():
    """Abre o chat DIRETO com a Piggy no WhatsApp (deep link), com saudação
    pré-preenchida. Botões do site apontam pra cá — o número real fica no
    servidor (WHATSAPP_NUMBER), nada hardcoded no HTML. Serve pra reencontrar
    o bot rápido. Sem número configurado, cai no seletor genérico do WhatsApp."""
    import os
    import urllib.parse

    text = urllib.parse.quote("Oi Piggy! Quero acessar minha conta PigBank 🐷")
    number = "".join(ch for ch in os.getenv("WHATSAPP_NUMBER", "") if ch.isdigit())
    url = (
        f"https://api.whatsapp.com/send?phone={number}&text={text}"
        if number
        else f"https://wa.me/?text={text}"
    )
    return RedirectResponse(url, status_code=302)


@router.post("/contact")
@limiter.limit("4/hour")
async def contact_submit(request: Request, body: ContactBody):
    """Recebe o formulário de contato do site e envia por e-mail pra equipe
    (Reply-To = e-mail do usuário, pra responder direto). Honeypot + rate
    limit contra spam. Substitui o mailto: frágil (que exigia cliente de
    e-mail configurado no dispositivo)."""
    # Honeypot: se o campo oculto veio preenchido, é bot — finge sucesso e dropa.
    if (body.website or "").strip():
        return {"ok": True}

    name = (body.name or "").strip()[:80]
    email = (body.email or "").strip()[:120]
    subject = (body.subject or "").strip()[:120]
    message = (body.message or "").strip()[:4000]

    if not name or not email or not message:
        raise HTTPException(status_code=400, detail="Preencha nome, e-mail e mensagem.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Informe um e-mail válido.")

    from core.services.email_service import send_email

    esc = _html.escape
    html_body = (
        f"<p><b>Nome:</b> {esc(name)}</p>"
        f"<p><b>E-mail:</b> {esc(email)}</p>"
        f"<p><b>Assunto:</b> {esc(subject) or '(sem assunto)'}</p>"
        f"<hr><p>{esc(message).replace(chr(10), '<br>')}</p>"
    )
    text_body = f"Nome: {name}\nE-mail: {email}\nAssunto: {subject}\n\n{message}"

    ok = await asyncio.to_thread(
        send_email,
        "contato@pigbankai.com",
        f"[Contato site] {subject or 'Sem assunto'} — {name}",
        html_body,
        text_body=text_body,
        headers={"Reply-To": email},
    )
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="Não conseguimos enviar agora. Escreva pra contato@pigbankai.com.",
        )
    return {"ok": True}


@router.get("/health")
async def health():
    return {"status": "ok"}
