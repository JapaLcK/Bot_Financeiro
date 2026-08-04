/**
 * app-mode.js — Ativa o "modo app" do PigBank e monta a tab bar inferior.
 *
 * Ativação (qualquer um):
 *   - user agent contém "PigBankApp" (WebView do app iOS/Capacitor)
 *   - ?pbapp=1 na URL (persiste em localStorage — preview no navegador)
 *   - localStorage.pbApp === "1"
 *   Desativação no preview: ?pbapp=0
 *
 * Deve ser incluído no <head> (sem defer) pra classe pb-app existir antes do
 * primeiro paint — evita o "pulo" de layout do chrome de site sumindo.
 */
(() => {
  const qs = new URLSearchParams(location.search);
  if (qs.get("pbapp") === "0") { try { localStorage.removeItem("pbApp"); } catch (_) {} }
  if (qs.get("pbapp") === "1") { try { localStorage.setItem("pbApp", "1"); } catch (_) {} }

  let stored = null;
  try { stored = localStorage.getItem("pbApp"); } catch (_) {}
  const inApp = /PigBankApp/.test(navigator.userAgent) || stored === "1";
  if (!inApp) return;

  const root = document.documentElement;
  root.classList.add("pb-app");

  // Página atual → classe no body (CSS escopa por página) + aba ativa
  const PAGES = {
    "/home":         "home",
    "/app":          "app",
    "/comandos-app": "comandos",
    "/settings":     "settings",
  };
  // Variantes do preview local (mock serve arquivos .html e o dash na raiz)
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    Object.assign(PAGES, {
      "/home.html": "home", "/dashboard.html": "app",
      "/comandos-app.html": "comandos", "/settings.html": "settings",
      "/": "app",
    });
  }
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const page = PAGES[path];

  // Safe areas exigem viewport-fit=cover (WebKit aplica dinamicamente)
  function fixViewport() {
    const m = document.querySelector('meta[name="viewport"]');
    if (m && !/viewport-fit/.test(m.content)) m.content += ", viewport-fit=cover";
  }

  const TABS = [
    { href: "/home", label: "Início", icon:
      '<svg viewBox="0 0 24 24"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg>' },
    { href: "/app", label: "Dashboard", icon:
      '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/></svg>' },
    { href: "/comandos-app", label: "O que pedir", icon:
      '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.3 8.9 8.9 0 0 1-3.7-.8L3 20l1.1-4.1a8 8 0 0 1-1.1-4.4A8.4 8.4 0 0 1 11.5 3.2 8.4 8.4 0 0 1 21 11.5z"/></svg>' },
    { href: "/settings", label: "Ajustes", icon:
      '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5h.1a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/></svg>' },
  ];

  function buildTabbar() {
    if (!page) { root.classList.add("pb-no-tabs"); return; }
    document.body.classList.add("pb-page-" + page);

    const bar = document.createElement("nav");
    bar.className = "pb-tabbar";
    bar.setAttribute("aria-label", "Navegação principal");
    bar.innerHTML = TABS.map(t => {
      const active = PAGES[t.href] === page;
      return `<a class="pb-tab${active ? " active" : ""}" href="${t.href}"` +
        `${active ? ' aria-current="page"' : ""}>${t.icon}<span>${t.label}</span></a>`;
    }).join("");
    document.body.appendChild(bar);
  }

  // Glifos de texto (☰, 🐷) viram SVG/imagem — WebView pode não ter as fontes
  function hardenGlyphs() {
    const burger =
      '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    document.querySelectorAll(".sidenav-toggle, .hamburger-icon").forEach(el => {
      el.innerHTML = burger;
    });
    const fabIcon = document.querySelector("#piggy-fab span[aria-hidden]");
    if (fabIcon) {
      fabIcon.innerHTML =
        '<img src="/brand/avatar.webp" alt="" style="width:40px;height:40px;' +
        'border-radius:50%;display:block;object-fit:cover" />';
    }
  }

  function init() { fixViewport(); buildTabbar(); hardenGlyphs(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
