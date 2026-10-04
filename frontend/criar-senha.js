/* "Crie sua senha" — o overlay que não fecha (PR 4 do funil v3).

   Conta paga sem senha e sem Google/Apple (a do quiz, antes de clicar no link
   do e-mail) não lê nem grava dado: o servidor responde 403
   `password_required` (frontend/routes/shared.py, `exigir_credencial`). Este
   arquivo é a tela desse estado, carregado pela /home e pelo /app — NÃO pela
   /settings, que é a saída para corrigir o e-mail.

   API: `window.PBCriarSenha.mostrar(me)`. Sem `me`, busca o /auth/me. Também
   se liga sozinho: qualquer fetch que volte 403 `password_required` sobe o
   overlay (mesmo molde do interceptor de `pro_required` do dashboard.js). */
(function () {
  if (window.PBCriarSenha) return;

  let aberto = false;
  let buscando = false;

  // `pbCsrfHeaders` é do auth-refresh.js, que as duas páginas carregam antes.
  const csrfHeaders = () => window.pbCsrfHeaders();

  function buscarMe() {
    return fetch("/auth/me", { credentials: "same-origin" });
  }

  function el(tag, attrs, texto) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (texto) n.textContent = texto;
    return n;
  }

  function sair() {
    fetch("/auth/logout", { method: "POST", credentials: "same-origin", headers: csrfHeaders() })
      .catch(() => {})
      .finally(() => {
        try { localStorage.setItem("finbot_logout_at", String(Date.now())); } catch {}
        window.location.replace("/?logout=1");
      });
  }

  // A aba volta ao foco: o link do e-mail pode ter sido usado noutra aba. O
  // reset revoga as sessões, então 401 (o que sobra depois do auth-refresh.js)
  // vai para o login; senha criada, recarrega a tela sem o overlay.
  async function aoVoltar() {
    if (document.visibilityState !== "visible") return;
    try {
      const r = await buscarMe();
      if (r.status === 401) { window.location.replace("/login"); return; }
      if (!r.ok) return;
      const me = await r.json();
      if (me && me.precisa_criar_senha === false) window.location.reload();
    } catch {}
  }

  function montar(me) {
    const ov = el("div", {
      class: "pb-cs-overlay", id: "pb-criar-senha", role: "dialog",
      "aria-modal": "true", "aria-labelledby": "pb-cs-titulo",
    });
    const card = el("div", { class: "pb-cs-card" });
    card.appendChild(el("img", {
      class: "pb-cs-piggy", src: "/brand/landing-mascot-150.webp", alt: "", width: "72", height: "72",
    }));
    // Só sobe para conta com plano (os vereditos de plano vêm antes): quem acabou de pagar vê que deu certo.
    card.appendChild(el("p", { class: "pb-cs-ok" }, "✅ Assinatura confirmada!"));
    card.appendChild(el("h2", { id: "pb-cs-titulo" }, "Crie sua senha para proteger sua conta"));
    card.appendChild(el("p", { class: "pb-cs-sub" },
      "Se você levou algum caderno, ele chega no seu e-mail logo depois."));

    const enviar = el("button", { type: "button", class: "pb-cs-primario" });
    enviar.textContent = me.email ? `Enviar link para ${me.email}` : "Enviar link";
    const status = el("p", { class: "pb-cs-status", role: "status", "aria-live": "polite" });
    card.append(enviar, status);

    const acoes = el("div", { class: "pb-cs-acoes" });
    const corrigir = el("a", { href: "/settings?view=security", class: "pb-cs-link" }, "E-mail errado? Corrigir");
    const botaoSair = el("button", { type: "button", class: "pb-cs-link" }, "Sair");
    acoes.append(corrigir, botaoSair);
    card.appendChild(acoes);
    ov.appendChild(card);

    enviar.addEventListener("click", async () => {
      enviar.disabled = true;
      status.textContent = "";
      try {
        const r = await fetch(`/settings/${me.user_id}/password-reset`, {
          method: "POST", credentials: "same-origin", headers: csrfHeaders(),
        });
        if (r.ok) {
          status.textContent = "Enviamos. Abra no seu e-mail.";
          enviar.textContent = "Reenviar";
        } else if (r.status === 429) {
          status.textContent = "Aguarde um minuto para reenviar.";
        } else {
          status.textContent = "Não deu para enviar agora. Tente de novo.";
        }
      } catch {
        status.textContent = "Não deu para enviar agora. Tente de novo.";
      }
      enviar.disabled = false;
    });
    botaoSair.addEventListener("click", sair);

    // Sem Esc e sem clique fora: o overlay só some quando a senha existe.
    // Tab preso no card; o resto da página fica `inert`.
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); return; }
      if (window.pigTrapTab) window.pigTrapTab(e, card);
      else if (e.key === "Tab" && !card.contains(document.activeElement)) { e.preventDefault(); enviar.focus(); }
    }, true);
    return { ov, enviar };
  }

  function exibir(me) {
    if (aberto) return;
    aberto = true;
    const { ov, enviar } = montar(me);
    for (const filho of document.body.children) {
      if (filho.tagName !== "SCRIPT") filho.setAttribute("inert", "");
    }
    document.body.appendChild(ov);
    document.documentElement.classList.add("pb-cs-aberto");
    document.addEventListener("visibilitychange", aoVoltar);
    enviar.focus({ preventScroll: true });
  }

  async function mostrar(me) {
    if (aberto) return;
    if (!me) {
      if (buscando) return;
      buscando = true;
      try {
        const r = await buscarMe();
        me = r.ok ? await r.json() : null;
      } catch { me = null; }
      buscando = false;
      if (!me || !me.precisa_criar_senha) return;
    }
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", () => exibir(me), { once: true });
      return;
    }
    exibir(me);
  }

  // 403 `password_required` de qualquer chamada sobe o overlay. Clona para não
  // consumir o corpo de quem chamou.
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await _fetch.apply(this, args);
    if (res.status === 403 && !aberto) {
      try {
        const data = await res.clone().json();
        if (data && data.detail && data.detail.error === "password_required") mostrar();
      } catch {}
    }
    return res;
  };

  window.PBCriarSenha = { mostrar };
})();
