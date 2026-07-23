/* blog-news.js — popula a seção "Notícias do mercado" do /blog (changelog.html).
   Consome /api/blog/news (curadoria/link-out): cada card leva pra fonte original.
   Externalizado (não inline) pra viabilizar remover 'unsafe-inline' do CSP. */
(function () {
  "use strict";

  var grid = document.getElementById("news-grid");
  var section = document.getElementById("news-section");
  if (!grid || !section) return;

  function timeAgo(iso) {
    if (!iso) return "";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var mins = Math.floor((Date.now() - then) / 60000);
    if (mins < 60) return "há " + Math.max(1, mins) + " min";
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return "há " + hrs + "h";
    var days = Math.floor(hrs / 24);
    if (days < 7) return "há " + days + (days === 1 ? " dia" : " dias");
    return new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text; // textContent = anti-XSS
    return e;
  }

  function card(n) {
    var a = document.createElement("a");
    a.className = "article";
    a.href = n.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";

    var thumb = el("div", "article-thumb", n.emoji || "📰");
    var body = el("div", "article-body");
    if (n.category) body.appendChild(el("span", "tag-cat", n.category));
    body.appendChild(el("h3", null, n.title));
    body.appendChild(el("p", "article-summary", n.summary));

    var metaBits = [];
    if (n.source) metaBits.push(n.source);
    var t = timeAgo(n.published_at);
    if (t) metaBits.push(t);
    body.appendChild(el("div", "meta", metaBits.join(" · ")));

    // O resumo é só uma chamada — a notícia completa está na fonte (nova aba).
    body.appendChild(el("div", "article-source-cta", "Ler notícia completa →"));

    a.appendChild(thumb);
    a.appendChild(body);
    return a;
  }

  fetch("/api/blog/news?limit=12", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : { news: [] }; })
    .then(function (data) {
      var news = (data && data.news) || [];
      if (!news.length) {
        // Sem notícias ainda (bot não rodou) — esconde a seção em vez de mostrar vazio.
        section.style.display = "none";
        return;
      }
      grid.textContent = "";
      news.forEach(function (n) { grid.appendChild(card(n)); });
      section.style.display = ""; // revela (começa oculta pra não piscar vazio)
    })
    .catch(function () {
      section.style.display = "none";
    });
})();
