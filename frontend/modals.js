/**
 * modals.js — Substitui os dialogs nativos do browser (alert/confirm) por
 * modais com a identidade visual do PigBank AI. Auto-injeta CSS no primeiro
 * uso, sem dependencias.
 *
 * API publica:
 *   await alertModal(message, opts?)              -> resolve sempre
 *   await confirmModal(message, opts?)            -> resolve true|false
 *
 * opts:
 *   title          (string)        — titulo opcional acima da mensagem
 *   confirmText    (string)        — label do botao primario (default "OK" / "Confirmar")
 *   cancelText     (string)        — label do botao secundario (default "Cancelar")
 *   destructive    (bool)          — botao primario fica vermelho (acoes irreversiveis)
 *
 * Suporta multilinha: \n vira <br>. HTML cru no message NAO e renderizado
 * (escape-by-default) — passe `opts.html=true` se quiser inline (use com cuidado).
 */
(function () {
  if (window.__pigModalReady) return;
  window.__pigModalReady = true;

  const STYLE_ID = "pig-modal-styles";
  const CSS = `
    .pig-modal-overlay {
      position: fixed; inset: 0;
      background: rgba(8, 11, 24, .72);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      z-index: 99999;
      display: none;
      align-items: center; justify-content: center;
      padding: 20px;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
      animation: pig-modal-fade .15s ease-out;
    }
    .pig-modal-overlay.open { display: flex; }
    @keyframes pig-modal-fade {
      from { opacity: 0; }
      to   { opacity: 1; }
    }
    .pig-modal {
      background: #0f1320;
      border: 1px solid rgba(255, 255, 255, .1);
      border-radius: 16px;
      width: min(440px, 92vw);
      max-height: 88vh;
      overflow: auto;
      padding: 24px 24px 20px;
      color: #e2e8f0;
      box-shadow: 0 24px 64px rgba(0, 0, 0, .55);
      animation: pig-modal-pop .18s cubic-bezier(.2, .8, .25, 1.05);
    }
    @keyframes pig-modal-pop {
      from { transform: translateY(8px) scale(.97); opacity: 0; }
      to   { transform: translateY(0)   scale(1);   opacity: 1; }
    }
    .pig-modal-title {
      font-size: 1.02rem;
      font-weight: 700;
      margin: 0 0 8px;
      color: #fff;
      letter-spacing: -.01em;
    }
    .pig-modal-body {
      font-size: .9rem;
      line-height: 1.55;
      color: rgba(226, 232, 240, .82);
      margin: 0 0 20px;
      word-wrap: break-word;
    }
    .pig-modal-actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .pig-modal-btn {
      padding: 9px 18px;
      border-radius: 10px;
      border: 1px solid transparent;
      font-size: .86rem;
      font-weight: 650;
      cursor: pointer;
      transition: background .15s ease, transform .05s ease, border-color .15s ease;
      font-family: inherit;
    }
    .pig-modal-btn:active { transform: translateY(1px); }
    .pig-modal-btn-cancel {
      background: rgba(255, 255, 255, .04);
      border-color: rgba(255, 255, 255, .12);
      color: rgba(226, 232, 240, .78);
    }
    .pig-modal-btn-cancel:hover {
      background: rgba(255, 255, 255, .08);
      color: #e2e8f0;
    }
    .pig-modal-btn-primary {
      background: linear-gradient(135deg, #7c3aed, #6d28d9);
      color: #fff;
    }
    .pig-modal-btn-primary:hover {
      background: linear-gradient(135deg, #8b5cf6, #7c3aed);
    }
    .pig-modal-btn-destructive {
      background: linear-gradient(135deg, #dc2626, #b91c1c);
      color: #fff;
    }
    .pig-modal-btn-destructive:hover {
      background: linear-gradient(135deg, #ef4444, #dc2626);
    }
    @media (max-width: 480px) {
      .pig-modal { width: 100%; }
      .pig-modal-actions { flex-direction: column-reverse; }
      .pig-modal-btn { width: 100%; }
    }
  `;

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement("style");
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  function renderBody(message, isHtml) {
    if (isHtml) return String(message ?? "");
    return escapeHtml(message).replace(/\n/g, "<br>");
  }

  function openModal({ kind, message, opts }) {
    ensureStyles();
    const o = opts || {};
    const isConfirm = kind === "confirm";
    const isDestructive = !!o.destructive;
    const confirmText = o.confirmText || (isConfirm ? "Confirmar" : "OK");
    const cancelText = o.cancelText || "Cancelar";
    const title = o.title;

    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "pig-modal-overlay";

      const modal = document.createElement("div");
      modal.className = "pig-modal";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      modal.innerHTML = `
        ${title ? `<h3 class="pig-modal-title">${escapeHtml(title)}</h3>` : ""}
        <div class="pig-modal-body">${renderBody(message, !!o.html)}</div>
        <div class="pig-modal-actions">
          ${isConfirm ? `<button type="button" class="pig-modal-btn pig-modal-btn-cancel">${escapeHtml(cancelText)}</button>` : ""}
          <button type="button" class="pig-modal-btn ${isDestructive ? "pig-modal-btn-destructive" : "pig-modal-btn-primary"}">${escapeHtml(confirmText)}</button>
        </div>
      `;
      overlay.appendChild(modal);
      document.body.appendChild(overlay);
      // Trigger animation on next frame
      requestAnimationFrame(() => overlay.classList.add("open"));

      const close = (value) => {
        document.removeEventListener("keydown", onKey);
        overlay.remove();
        resolve(value);
      };

      const btnPrimary = modal.querySelector(".pig-modal-btn-primary, .pig-modal-btn-destructive");
      const btnCancel = modal.querySelector(".pig-modal-btn-cancel");
      btnPrimary.addEventListener("click", () => close(isConfirm ? true : undefined));
      if (btnCancel) btnCancel.addEventListener("click", () => close(false));

      // Click no overlay = cancela (so em confirm; alert exige clique no OK)
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay && isConfirm) close(false);
      });

      const onKey = (e) => {
        if (e.key === "Escape") {
          if (isConfirm) close(false);
        } else if (e.key === "Enter") {
          e.preventDefault();
          close(isConfirm ? true : undefined);
        }
      };
      document.addEventListener("keydown", onKey);

      // Foco no botao primario para keyboard nav
      setTimeout(() => btnPrimary.focus(), 50);
    });
  }

  window.alertModal = function (message, opts) {
    return openModal({ kind: "alert", message, opts });
  };
  window.confirmModal = function (message, opts) {
    return openModal({ kind: "confirm", message, opts });
  };
})();
