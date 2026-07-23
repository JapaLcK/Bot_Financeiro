/* blog-article.js — página de artigo /blog/<slug>:
   1) barra de progresso de leitura no topo
   2) animações ao rolar (a linha do gráfico se desenha, as barras crescem)
   Externalizado (não inline) pra viabilizar remover 'unsafe-inline' do CSP.
   Salvaguardas: sem JS os blocos já aparecem cheios (o estado animado é opt-in
   via classe .g-anim adicionada aqui); respeita prefers-reduced-motion. */
(function () {
  "use strict";

  // ── 1. Barra de progresso de leitura ──────────────────────────────────────
  var bar = document.getElementById("readProgress");
  if (bar) {
    var update = function () {
      var doc = document.documentElement;
      var scrollable = doc.scrollHeight - doc.clientHeight;
      var pct = scrollable > 0 ? (doc.scrollTop || document.body.scrollTop) / scrollable : 0;
      bar.style.width = Math.min(100, Math.max(0, pct * 100)) + "%";
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update, { passive: true });
  }

  // ── 2. Animações ao entrar na tela ────────────────────────────────────────
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var blocks = [].slice.call(document.querySelectorAll(".g-chart, .g-bars, .g-goal, .g-cardshot"));
  if (!blocks.length || reduce) return;

  // Prepara as linhas dos gráficos pra "desenhar" via stroke-dashoffset.
  [].slice.call(document.querySelectorAll(".g-chart-line")).forEach(function (line) {
    try {
      var len = line.getTotalLength();
      line.style.strokeDasharray = len;
      line.style.strokeDashoffset = len;
      line.style.transition = "stroke-dashoffset 1.2s ease";
    } catch (e) { /* getTotalLength indisponível — deixa a linha estática */ }
  });

  // Só entra no estado "colapsado" agora (com JS garantido) — sem JS fica cheio.
  blocks.forEach(function (el) { el.classList.add("g-anim"); });

  var reveal = function (el) {
    el.classList.add("in-view");
    [].slice.call(el.querySelectorAll(".g-chart-line")).forEach(function (line) {
      line.style.strokeDashoffset = "0";
    });
  };

  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { reveal(e.target); io.unobserve(e.target); }
      });
    }, { threshold: 0.2 });
    blocks.forEach(function (el) { io.observe(el); });
  } else {
    blocks.forEach(reveal); // sem IntersectionObserver: mostra tudo cheio
  }
})();
