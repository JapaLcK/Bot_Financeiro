"""Páginas estáticas, assets, SEO e health — rotas públicas sem auth e sem banco.

Etapa 1 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from frontend.routes.shared import FRONTEND_DIR, html_file, public_site_url

router = APIRouter()


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


@router.get("/health")
async def health():
    return {"status": "ok"}
