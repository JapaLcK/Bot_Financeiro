// Pop-up do cupom (visitante deslogado em / e /precos): e-mail + consentimento -> POST /cupom.
// Aparece uma vez só (pb_cupom_visto), por exit-intent (mouse) ou 15s, o que vier primeiro.
(function () {
  "use strict";
  const CHAVE = "pb_cupom_visto";
  try { if (localStorage.getItem(CHAVE)) return; } catch (_e) { /* storage bloqueado: segue */ }

  let timer, aberto = false, anterior = null;

  function agendar() {
    // Logado não é público-alvo; falha da rede = trata como deslogado (pior caso: um pop-up a mais).
    fetch("/auth/validate", { credentials: "same-origin" }).then(function (r) {
      if (r.ok) return;
      timer = setTimeout(mostrar, 15000);
      if (window.matchMedia && matchMedia("(pointer:fine)").matches) {
        document.addEventListener("mouseout", saida);
      }
    }).catch(function () { timer = setTimeout(mostrar, 15000); });
  }
  function saida(e) { if (!e.relatedTarget && e.clientY <= 0) mostrar(); }

  // ponytail: base do .pig-modal copiada do modals.js (a / não o carrega); se mudar lá, mude aqui.
  const CSS =
    "#pb-cupom{position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px;" +
    "background:rgba(8,11,24,.72);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}" +
    "#pb-cupom .pig-modal{box-sizing:border-box;position:relative;background:#0f1320;border:1px solid rgba(255,255,255,.1);border-radius:16px;" +
    "width:min(420px,92vw);max-height:88vh;overflow:auto;padding:22px 24px 20px;color:#e2e8f0;text-align:center;" +
    "box-shadow:0 24px 64px rgba(0,0,0,.55)}" +
    "#pb-cupom img{width:120px;height:120px;object-fit:contain;display:block;margin:0 auto 6px}" +
    "#pb-cupom h2{font-size:1.25rem;font-weight:800;margin:0 0 8px;color:#fff;letter-spacing:-.01em}" +
    "#pb-cupom p{font-size:.92rem;line-height:1.5;margin:0 0 14px;color:rgba(226,232,240,.82)}" +
    "#pb-cupom input[type=email]{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:10px;font:inherit;font-size:16px;" +
    "background:#080b18;color:#fff;border:1px solid rgba(255,255,255,.18);margin-bottom:12px}" +
    "#pb-cupom label.pb-c{display:flex;gap:8px;align-items:flex-start;text-align:left;font-size:.8rem;line-height:1.4;" +
    "color:rgba(226,232,240,.82);margin-bottom:14px}" +
    "#pb-cupom label.pb-c input{margin-top:2px;flex:none;accent-color:#FF2D8E}" +
    "#pb-cupom a{color:#FF66AB}" +
    "#pb-cupom .pb-btn{width:100%;padding:12px 18px;border-radius:10px;border:0;font:inherit;font-weight:700;font-size:.95rem;" +
    "color:#fff;cursor:pointer;background:linear-gradient(135deg,#FF2D8E,#C7186B)}" +
    "#pb-cupom .pb-btn:disabled{opacity:.6;cursor:default}" +
    "#pb-cupom .pb-x{position:absolute;top:8px;right:8px;width:36px;height:36px;border:0;border-radius:50%;cursor:pointer;" +
    "background:rgba(255,255,255,.06);color:#e2e8f0;font-size:20px;line-height:1}" +
    "#pb-cupom .pb-erro{color:#fca5a5;font-size:.82rem;min-height:1.2em;margin:0 0 10px}" +
    "#pb-cupom .pb-hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}" +
    "#pb-cupom :focus-visible{outline:2px solid #FF66AB;outline-offset:2px}";

  function mostrar() {
    if (aberto) return;
    aberto = true;
    clearTimeout(timer);
    document.removeEventListener("mouseout", saida);
    try { localStorage.setItem(CHAVE, "1"); } catch (_e) { /* ok */ }
    anterior = document.activeElement;

    const st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
    const o = document.createElement("div");
    o.id = "pb-cupom";
    o.className = "pig-modal-overlay open";
    // Só texto fixo aqui; nada vindo do usuário entra no innerHTML.
    o.innerHTML =
      '<div class="pig-modal" role="dialog" aria-modal="true" aria-labelledby="pb-c-t">' +
      '<button type="button" class="pb-x" aria-label="Fechar">&times;</button>' +
      '<img src="/brand/stickers/hello.webp" alt="" width="120" height="120">' +
      '<h2 id="pb-c-t">Desconto na sua primeira compra</h2>' +
      '<p id="pb-c-p">Deixe seu e-mail e receba um cupom para usar no checkout do PigBank.</p>' +
      '<form novalidate>' +
      '<input type="email" name="email" autocomplete="email" placeholder="seu@email.com" aria-label="Seu e-mail" required>' +
      '<input class="pb-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">' +
      '<label class="pb-c"><input type="checkbox" name="consent"><span>Concordo em receber o cupom por e-mail e aceito os ' +
      '<a href="/termos" target="_blank" rel="noopener">Termos de Uso</a>.</span></label>' +
      '<p class="pb-erro" role="alert"></p>' +
      '<button type="submit" class="pb-btn">Quero meu desconto</button></form></div>';
    document.body.appendChild(o);

    const form = o.querySelector("form"), erro = o.querySelector(".pb-erro"), btn = o.querySelector(".pb-btn");
    const email = form.elements.email;
    email.focus();

    function fechar() {
      document.removeEventListener("keydown", tecla, true);
      o.remove(); st.remove();
      if (anterior && anterior.focus) anterior.focus();
    }
    function tecla(e) {
      if (e.key === "Escape") { e.preventDefault(); fechar(); return; }
      if (e.key !== "Tab") return;
      const f = o.querySelectorAll("button,input:not(.pb-hp),a[href]");
      const a = f[0], z = f[f.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    }
    document.addEventListener("keydown", tecla, true);
    o.querySelector(".pb-x").addEventListener("click", fechar);
    o.addEventListener("mousedown", function (e) { if (e.target === o) fechar(); });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      erro.textContent = "";
      if (!form.elements.consent.checked || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) {
        erro.textContent = "Confira o e-mail e marque a caixa de aceite.";
        return;
      }
      btn.disabled = true;
      // Mesmo cookie/header de contact.js: o CSRF do app exige X-CSRF-Token em todo POST.
      const csrf = decodeURIComponent((document.cookie.split("; ").find(function (r) { return r.indexOf("csrf_token=") === 0; }) || "").split("=")[1] || "");
      const h = { "Content-Type": "application/json" };
      if (csrf) h["x-csrf-token"] = csrf;
      fetch("/cupom", {
        method: "POST", headers: h,
        body: JSON.stringify({ email: email.value.trim(), consent: true, origem: location.pathname.replace(/\W/g, "") || "home",
                               website: form.elements.website.value })
      }).then(function (r) {
        if (r.ok) {
          form.remove();
          o.querySelector("h2").textContent = "Cupom a caminho";
          o.querySelector("#pb-c-p").textContent = "Se o e-mail estiver certo, o código chega em instantes. Olhe também o spam. No checkout, use em Adicionar código promocional.";
          o.querySelector(".pb-x").focus();
          return;
        }
        erro.textContent = r.status === 429 ? "Muitas tentativas. Tente de novo mais tarde."
          : r.status === 400 ? "Confira o e-mail e marque a caixa de aceite." : "Não deu certo agora. Tente de novo.";
        btn.disabled = false;
      }).catch(function () { erro.textContent = "Sem conexão. Tente de novo."; btn.disabled = false; });
    });
  }

  agendar();
})();
