/**
 * As caixas do order bump ("Leva junto? Só nesta compra"): os cadernos extras vendidos junto com o plano. Duas
 * telas desenham com elas: a página própria da /assinar (pagamento-pagina.js) e o modal do Pix anual da /precos
 * (pix-extras.js). Aqui só se desenha e se pinta; o que o clique faz é de cada tela. Texto do Stripe (nome,
 * descrição) entra SÓ por `textContent`, capa só https, e o preço sai do servidor (`valor_centavos`): a tela não
 * guarda tabela de preços. Publica `window.PBBumpCaixas`; o CSS é o bump-caixas.css.
 */
(function () {
  "use strict";

  const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const brl = function (c) { return typeof c === "number" && isFinite(c) ? BRL.format(c / 100) : ""; };

  function el(tag, cls, texto) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto) e.textContent = texto;
    return e;
  }

  function capa(url, cls) {
    if (typeof url !== "string" || url.indexOf("https://") !== 0) return null;
    const i = el("img", cls);
    i.src = url; i.alt = ""; i.loading = "lazy";
    return i;
  }

  /** As linhas do bump dentro de `box` (vazio). Uma oferta = a versão A do protótipo; duas ou três = uma caixa
   *  com uma linha cada. Marcada = `no_carrinho === true`. Devolve os checkboxes, na ordem de `extras`. */
  function montar(box, extras) {
    const unica = extras.length === 1;
    box.hidden = !extras.length;
    box.classList.toggle("unica", unica);
    if (!extras.length) return [];
    const topo = el("div", "pp-bump-topo", "Leva junto? Só nesta compra");
    const seta = el("span", "pp-seta", unica ? "➜" : "↓");
    seta.setAttribute("aria-hidden", "true");
    box.appendChild(topo);
    if (!unica) topo.appendChild(seta);  // a seta aparece UMA vez: na faixa, ou na linha da oferta única
    return extras.map(function (x) {
      const l = el("label", "pp-linha"), cb = el("input");
      cb.type = "checkbox";
      cb.checked = x.no_carrinho === true;
      cb.dataset.pos = String(x.posicao);
      const tit = el("span", "pp-tit");
      tit.append(unica ? "Sim! Quero o caderno " : "Quero o caderno ", el("b", "", x.nome),
                 (unica ? " por " : " · ") + brl(x.valor_centavos));
      const desc = el("span", "pp-desc", x.descricao);
      const img = capa(x.imagem, unica ? "pp-capa-g" : "pp-capa");
      if (unica) {
        l.append(seta, cb, tit);
        const info = el("div", "pp-info");
        if (img) info.appendChild(img);
        info.appendChild(desc);
        box.append(l, info);
      } else {
        const txt = el("span", "pp-txt");
        txt.append(tit, desc);
        // Sem capa, o lugar dela: o texto fica alinhado com o das linhas que têm.
        l.append(cb, img || el("span", "pp-capa"), txt);
        box.appendChild(l);
      }
      return cb;
    });
  }

  /** O estado na tela: `travado` desliga as caixas; a linha marcada é `.on`; ≥1 marcada deixa a borda sólida e a
   *  seta parada (o `.marcado` do protótipo). */
  function pintar(box, cbs, travado) {
    cbs.forEach(function (c) { c.disabled = travado; c.closest("label").classList.toggle("on", c.checked); });
    box.classList.toggle("marcado", cbs.some(function (c) { return c.checked; }));
  }

  window.PBBumpCaixas = { montar: montar, pintar: pintar };
})();
