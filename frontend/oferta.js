/* /oferta (frontend/oferta.html): só o que precisa de JS. O resto da página é markup.
   Sem JS a página já mostra o ciclo anual, a 1ª pergunta aberta e os números finais.
   Só textContent/setAttribute: nada de innerHTML (o texto das respostas vem do atributo). */
(function () {
  "use strict";

  const $ = (sel, raiz = document) => raiz.querySelector(sel);
  const calmo = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const temIO = "IntersectionObserver" in window;

  // ── Mensal / Anual: o CSS esconde o par .so-mensal / .so-anual pelo data-ciclo.
  const planos = $("#planos");
  planos.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-set]");
    if (!btn) return;
    planos.dataset.ciclo = btn.dataset.set;
    for (const b of planos.querySelectorAll("[data-set]")) b.setAttribute("aria-pressed", String(b === btn));
  });

  // ── Chat de demonstração: pergunta → ✓ azul (450 ms) → digitando (750 ms) → resposta.
  const chat = $("#chat");
  const typ = $("#ch-typ");
  const status = $("#ch-status");
  let enviadas = 0;
  let ocupado = false;

  function estado(texto, cor) {
    status.textContent = texto;
    status.style.color = cor;
  }

  function bolha(modelo, texto, hora) {
    const el = document.importNode($(modelo).content, true).firstElementChild;
    $(".ch-txt", el).textContent = texto;
    $(".ch-hora", el).textContent = hora;
    chat.insertBefore(el, typ);
    const todas = chat.querySelectorAll(".ch-msg");
    if (todas.length > 6) todas[0].remove(); // só as 6 últimas cabem no celular
    return el;
  }

  $(".pb-wchat").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-resposta]");
    if (!btn || ocupado) return;
    ocupado = true;
    const resposta = btn.dataset.resposta;
    const hora = "12:" + Math.min(40 + enviadas * 2, 59);
    enviadas++;
    const minha = bolha("#t-eu", btn.firstChild.textContent, hora);
    setTimeout(() => {
      for (const p of minha.querySelectorAll(".ch-tk")) p.setAttribute("stroke", "#53BDEB");
    }, 450);
    setTimeout(() => {
      typ.hidden = false;
      estado("digitando...", "#25A855");
    }, 750);
    setTimeout(() => {
      typ.hidden = true;
      estado("online", "#8A8A8E");
      bolha("#t-bot", resposta, hora);
      ocupado = false;
    }, 750 + Math.min(2600, 900 + 14 * resposta.length));
  });

  // ── Animações ao rolar. O HTML já traz os valores finais; ao armar, o JS põe a classe
  // -wait (e zera os números) e troca por -go quando a seção aparece. Com
  // prefers-reduced-motion ou sem IntersectionObserver não arma nada: o valor final fica.
  function contar(raiz, duracao, atraso) {
    const nos = raiz.querySelectorAll("[data-alvo]");
    let t0 = null;
    const passo = (ts) => {
      if (t0 === null) t0 = ts + atraso;
      const p = Math.max(0, Math.min(1, (ts - t0) / duracao));
      const suave = 1 - Math.pow(1 - p, 3);
      for (const no of nos) no.textContent = Math.round(no.dataset.alvo * suave).toLocaleString("pt-BR");
      if (p < 1) requestAnimationFrame(passo);
    };
    requestAnimationFrame(passo);
  }

  function armar(sel, prefixo, limiar, aoVer) {
    if (calmo || !temIO) return;
    const el = $(sel);
    el.classList.add(prefixo + "-wait");
    for (const no of el.querySelectorAll("[data-alvo]")) no.textContent = "0";
    new IntersectionObserver((es, io) => {
      if (!es.some((x) => x.isIntersecting)) return;
      io.disconnect();
      el.classList.replace(prefixo + "-wait", prefixo + "-go");
      if (aoVer) aoVer(el);
    }, { threshold: limiar }).observe(el);
  }

  armar("#bento", "pb", 0.3, (el) => contar(el, 1600, 350));
  armar("#dash", "dk", 0.3, (el) => contar(el, 1900, 450));
  armar("#piggy", "pg", 0.45);

  // ── Barra fixa do celular: aparece depois que o CTA do hero saiu por cima e some
  // enquanto #planos ou o CTA final estão na tela. (No desktop o CSS a mantém oculta.)
  if (temIO) {
    const barra = $("#sticky");
    const link = $("a", barra);
    const vis = {};
    const atualizar = () => {
      const ligada = Boolean(vis.hero) && !vis.planos && !vis.final;
      barra.classList.toggle("st-on", ligada);
      barra.setAttribute("aria-hidden", String(!ligada));
      link.tabIndex = ligada ? 0 : -1;
    };
    for (const [chave, sel] of [["hero", "#hero-cta"], ["planos", "#planos"], ["final", "#cta-final"]]) {
      new IntersectionObserver((es) => {
        const e = es[es.length - 1];
        vis[chave] = chave === "hero" ? !e.isIntersecting && e.boundingClientRect.top < 0 : e.isIntersecting;
        atualizar();
      }).observe($(sel));
    }
  }
})();
