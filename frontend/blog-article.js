/* blog-article.js — barra de progresso de leitura da página de artigo (/blog/<slug>).
   Externalizado (não inline) pra viabilizar remover 'unsafe-inline' do CSP. */
(function () {
  "use strict";
  var bar = document.getElementById("readProgress");
  if (!bar) return;

  function update() {
    var doc = document.documentElement;
    var scrollable = doc.scrollHeight - doc.clientHeight;
    var pct = scrollable > 0 ? (doc.scrollTop || document.body.scrollTop) / scrollable : 0;
    bar.style.width = Math.min(100, Math.max(0, pct * 100)) + "%";
  }

  update();
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update, { passive: true });
})();
