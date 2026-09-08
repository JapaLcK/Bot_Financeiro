/* blog-news.js — popula a seção "Notícias do mercado" do /blog (changelog.html).
   Consome /api/blog/news (curadoria/link-out): cada card leva pra fonte original.
   Externalizado (não inline) pra viabilizar remover 'unsafe-inline' do CSP. */
(function () {
  "use strict";

  const grid = document.getElementById("news-grid");
  const section = document.getElementById("news-section");
  const empty = document.getElementById("news-empty");
  if (!grid || !section) return;

  function timeAgo(iso) {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    const mins = Math.floor((Date.now() - then) / 60000);
    if (mins < 60) return "há " + Math.max(1, mins) + " min";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return "há " + hrs + "h";
    const days = Math.floor(hrs / 24);
    if (days < 7) return "há " + days + (days === 1 ? " dia" : " dias");
    return new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text; // textContent = anti-XSS
    return e;
  }

  function card(n) {
    const a = document.createElement("a");
    a.className = "article";
    a.href = n.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";

    const thumb = el("div", "article-thumb");
    if (n.image) {
      // Foto real da matéria. Se falhar ao carregar, cai no emoji.
      const img = document.createElement("img");
      img.className = "article-photo";
      img.src = n.image;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer"; // alguns CDNs bloqueiam hotlink com referer
      img.onerror = function () {
        thumb.removeChild(img);
        thumb.classList.add("article-thumb-emoji");
        thumb.innerHTML = '<i class="ph ph-newspaper" aria-hidden="true"></i>';
      };
      thumb.appendChild(img);
    } else {
      thumb.classList.add("article-thumb-emoji");
      thumb.innerHTML = '<i class="ph ph-newspaper" aria-hidden="true"></i>';
    }
    const body = el("div", "article-body");
    if (n.category) body.appendChild(el("span", "tag-cat", n.category));
    body.appendChild(el("h3", null, n.title));
    body.appendChild(el("p", "article-summary", n.summary));

    const metaBits = [];
    if (n.source) metaBits.push(n.source);
    const t = timeAgo(n.published_at);
    if (t) metaBits.push(t);
    body.appendChild(el("div", "meta", metaBits.join(" · ")));

    // O resumo é só uma chamada — a notícia completa está na fonte (nova aba).
    body.appendChild(el("div", "article-source-cta", "Ler notícia completa →"));

    a.appendChild(thumb);
    a.appendChild(body);
    return a;
  }

  // Geração: o 1º load (linha de baixo) roda no defer, ANTES do DOMContentLoaded
  // onde o app-mode.js instala o wrap de pageFetches — então esse fetch inicial
  // NÃO é contado pelo guard do PTR. Um pull durante um load inicial lento
  // dispara um 2º request; se o inicial chegar por último, repintaria cards
  // velhos (ou limparia com snapshot vazio). Cada load carimba uma geração; só
  // a mais recente pinta — resposta superada (inicial ou refresh velho) é no-op.
  let _gen = 0;
  function load() {
    const myGen = ++_gen;
    // non-OK vira reject: cai no catch (tratado como falha transitória), não é
    // confundido com "genuinamente sem notícias" — senão um 502 no meio de um
    // pull-to-refresh apagaria os cards que já estavam na tela.
    return fetch("/api/blog/news?limit=12", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (data) {
        if (myGen !== _gen) return;   // superado por um load mais novo: não pinta
        const news = (data && data.news) || [];
        if (!news.length) {
          // 200 com lista vazia = genuinamente sem notícias: limpa e mostra o
          // estado vazio (vale pro 1º load e pra um refresh que esvaziou).
          grid.textContent = "";
          section.style.display = "none";
          if (empty) empty.style.display = "";
          return;
        }
        grid.textContent = "";
        news.forEach(function (n) { grid.appendChild(card(n)); });
        section.style.display = ""; // revela (começa oculta pra não piscar vazio)
        if (empty) empty.style.display = "none"; // some com o estado vazio
      })
      .catch(function (err) {
        // Superado por um load mais novo: não mexe no DOM nem propaga — quem
        // reporta sucesso/falha pro indicador é o load atual.
        if (myGen !== _gen) return;
        // Falha de rede / non-OK: preserva o que já está renderizado (um refresh
        // que falha não pode apagar cards bons). Só no 1º load, sem nada ainda,
        // esconde a seção pra deixar o estado vazio à mostra.
        if (!grid.children.length) section.style.display = "none";
        // Re-lança: o PTR (app-mode.js) decide sucesso/falha pela promise —
        // engolir aqui fazia o indicador reportar sucesso mesmo caindo a rede.
        throw err;
      });
  }

  // Pull-to-refresh do modo app (app-mode.js chama window.PBRefresh e espera a
  // promise). Sem isso o gesto caía no reload da página inteira.
  window.PBRefresh = load;
  // 1º load não é aguardado por ninguém: engole a rejeição (já tratada
  // visualmente) pra não virar unhandled promise rejection no console.
  load().catch(function () {});
})();
