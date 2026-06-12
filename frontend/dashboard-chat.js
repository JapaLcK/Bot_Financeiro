/* dashboard-chat.js — widget de chat IA (Piggy), extraído de dashboard.html.
   Servido em /dashboard-chat.js. */
(function(){
  let piggyLoaded = false;
  let piggyBusy = false;

  window.togglePiggy = function() {
    if (!window.isProUser || !isProUser()) {
      if (window.showUpgradeModal) showUpgradeModal("ai_chat");
      return;
    }
    const panel = document.getElementById("piggy-panel");
    const fab = document.getElementById("piggy-fab");
    if (panel.classList.contains("open")) {
      closePiggy();
    } else {
      panel.classList.add("open");
      fab.classList.add("open");
      if (!piggyLoaded) loadPiggyHistory();
      setTimeout(() => document.getElementById("piggy-input").focus(), 200);
    }
  };

  window.closePiggy = function() {
    document.getElementById("piggy-panel").classList.remove("open");
    document.getElementById("piggy-fab").classList.remove("open");
  };

  window.piggyKeyDown = function(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      piggySend();
    }
  };

  window.piggyAutoresize = function(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  };

  window.piggyAsk = function(text) {
    const input = document.getElementById("piggy-input");
    input.value = text;
    piggyAutoresize(input);
    piggySend();
  };

  async function loadPiggyHistory() {
    // O widget abre VAZIO em cada nova sessão do browser. Mensagens antigas
    // do user (do WhatsApp/Discord/sessão anterior) ficam só no DB pra
    // contexto da IA — não pra exibição. Decisão de UX: histórico misturado
    // entre canais confunde o user, e o widget no dashboard é um ponto de
    // entrada novo (não deve "lembrar" o que aconteceu fora dele).
    //
    // O backend continua usando o histórico real pra montar o contexto da
    // OpenAI; só a UI que esconde. Aqui só buscamos o `usage` pra renderizar
    // o contador.
    piggyLoaded = true;
    try {
      const r = await fetch("/ai/messages?limit=1", { credentials: "same-origin" });
      if (!r.ok) return;
      const data = await r.json();
      updateUsage(data.usage);
    } catch (e) {
      console.warn("[piggy] erro ao buscar usage:", e);
    }
  }

  window.piggySend = async function() {
    if (piggyBusy) return;
    const input = document.getElementById("piggy-input");
    const text = input.value.trim();
    if (!text) return;
    if (text.length > 2000) {
      alert("Mensagem muito longa (máx 2000 caracteres).");
      return;
    }

    piggyBusy = true;
    document.getElementById("piggy-send").disabled = true;
    document.getElementById("piggy-empty").style.display = "none";
    renderPiggyMsg("user", text, true);
    input.value = "";
    input.style.height = "40px";
    scrollPiggyToBottom();
    showTyping();

    try {
      const r = await fetch("/ai/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ message: text }),
      });
      hideTyping();
      if (r.status === 403) {
        // Plano Pro expirou ou foi rebaixado. Fecha o widget e abre upgrade.
        const data = await r.json().catch(() => ({}));
        renderPiggyMsg("assistant", "🔒 Conversar com a IA é um recurso do PigBank+. " +
                                     "[Faça upgrade](/precos) pra liberar.", true);
        return;
      }
      if (!r.ok) {
        renderPiggyMsg("assistant", "🐷 Deu ruim aqui. Tenta de novo em instantes.", true);
        return;
      }
      const data = await r.json();
      renderPiggyMsg("assistant", data.reply || "🐷 Sem resposta.", true);
      updateUsage(data.usage);
    } catch (e) {
      hideTyping();
      console.error("[piggy] erro no send:", e);
      renderPiggyMsg("assistant", "🐷 Sem conexão. Tenta de novo.", true);
    } finally {
      piggyBusy = false;
      document.getElementById("piggy-send").disabled = false;
      scrollPiggyToBottom();
    }
  };

  function renderPiggyMsg(role, content, animate) {
    const body = document.getElementById("piggy-body");
    const div = document.createElement("div");
    div.className = "piggy-msg " + role;
    div.innerHTML = formatPiggy(content);
    if (!animate) div.style.animation = "none";
    body.appendChild(div);
  }

  function formatPiggy(text) {
    // Markdown leve: escapa HTML, depois aplica **bold**, `code`, [link](url) e quebra de linha.
    const esc = String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return esc
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\n/g, "<br>");
  }

  function showTyping() {
    if (document.getElementById("piggy-typing")) return;
    const body = document.getElementById("piggy-body");
    const div = document.createElement("div");
    div.id = "piggy-typing";
    div.className = "piggy-typing";
    div.innerHTML = "<span></span><span></span><span></span>";
    body.appendChild(div);
    scrollPiggyToBottom();
  }

  function hideTyping() {
    const t = document.getElementById("piggy-typing");
    if (t) t.remove();
  }

  function scrollPiggyToBottom() {
    const body = document.getElementById("piggy-body");
    body.scrollTop = body.scrollHeight;
  }

  function updateUsage(u) {
    if (!u) return;
    const el = document.getElementById("piggy-usage");
    if (!el) return;
    const pct = u.limit > 0 ? u.used / u.limit : 0;
    // < 80%: oculto (sensacao de ilimitado).
    // >= 80%: amarelo. >= 100%: vermelho (bloqueado).
    if (pct >= 1) {
      el.textContent = `Limite mensal atingido (${u.used} / ${u.limit}). Reseta no dia 1º.`;
      el.style.color = "#fb7185";
      el.style.display = "";
    } else if (pct >= 0.8) {
      el.textContent = `${u.used} / ${u.limit} mensagens este mês`;
      el.style.color = "#fbbf24";
      el.style.display = "";
    } else {
      el.textContent = "";
      el.style.display = "none";
    }
  }

  // Fecha com Esc
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const panel = document.getElementById("piggy-panel");
      if (panel && panel.classList.contains("open")) closePiggy();
    }
  });
})();
