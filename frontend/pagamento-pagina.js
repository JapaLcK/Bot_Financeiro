/**
 * Página de pagamento própria (sessão `ui_mode="elements"`, flag CHECKOUT_PAGINA_PROPRIA): o Payment Element,
 * o resumo do pedido, as caixas do order bump e o cupom. Quem decide o modo, carrega o Stripe.js (endive), tem o
 * relógio de 10 s e o plano B é o assinar.js; este arquivo só monta a tela dentro de `#pagina` e devolve
 * `{destroy}`. Publica `window.PBPagamento`. Texto vindo do Stripe (nome, descrição, cupom) entra SÓ por
 * `textContent`; o preço do caderno sai do servidor (`valor_centavos`), e o total, da sessão no `change` do
 * Stripe: a tela não guarda tabela de preços. Testes: tests/frontend/pagamento_pagina.test.mjs.
 */
(function () {
  "use strict";

  const ERRO_BUMP = "Não deu para atualizar seu pedido. Ele continua como estava.";
  const ERRO_SYNC = "Não deu para confirmar seu pedido agora. Tente de novo ou use a página segura do Stripe, logo abaixo.";
  // O loadActions sem prazo prenderia o botão (o relógio do assinar.js só olha o iframe, que já existe); o /bump
  // tem o seu abaixo dos 20 s do runServerUpdate.
  const ACOES_MS = 10000, BUMP_MS = 15000;
  const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const $ = function (id) { return document.getElementById(id); };
  const brl = function (c) { return typeof c === "number" && isFinite(c) ? BRL.format(c / 100) : ""; };
  /** A mensagem do Stripe num `{type:"error", error}`, ou a padrão. */
  const msg = function (r, padrao) { const e = (r && r.error) || {}; return typeof e.message === "string" && e.message ? e.message : padrao; };
  const expirada = function (s) { return !!(s && s.status && s.status.type === "expired"); };
  /** Formatador do dinheiro DA SESSÃO: moeda e divisor lidos dela. O SDK exige essa leitura junto com a do
   *  total.total.minorUnitsAmount: sem ela o `confirm` LANÇA (docs.stripe.com/js/custom_checkout). */
  function formatador(s) {
    const div = typeof s.minorUnitsAmountDivisor === "number" && s.minorUnitsAmountDivisor > 0 ? s.minorUnitsAmountDivisor : 100;
    let f = BRL;
    try {
      if (typeof s.currency === "string") f = new Intl.NumberFormat("pt-BR", { style: "currency", currency: s.currency.toUpperCase() });
    } catch (_) { /* moeda desconhecida: fica BRL */ }
    return function (c) { return typeof c === "number" && isFinite(c) ? f.format(c / div) : ""; };
  }
  const centavos = function (v) { return v && typeof v.minorUnitsAmount === "number" ? v.minorUnitsAmount : null; };
  const espera = function (ms) { return new Promise(function (ok) { setTimeout(function () { ok(null); }, ms); }); };

  function el(tag, cls, texto) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto) e.textContent = texto;
    return e;
  }

  function aviso(id, texto) {
    $(id).textContent = texto || "";
    $(id).classList.toggle("show", !!texto);
  }

  /** Apaga a tela de uma montagem anterior (outra conta, sessão expirada): nada dela fica visível nem clicável. */
  function limpa() {
    $("pp-resumo").textContent = $("pp-bump").textContent = $("pp-cupom-cod").value = "";
    $("pp-cta").textContent = $("pp-cta-sub").textContent = "";
    $("pp-bump").hidden = $("pp-cupom").hidden = true;
    $("pp-cupom-abre").hidden = false;
    aviso("pp-erro", ""); aviso("pp-cupom-erro", "");
    $("pp-pagar").disabled = $("pp-cupom-ok").disabled = true;
    $("pp-pagar").onclick = $("pp-cupom").onsubmit = null;
  }

  /** O resumo e o texto do botão, a partir da sessão do `change`. Devolve o total de hoje (null = sem total). */
  function resumo(s, td, por) {
    const r = $("pp-resumo"), itens = Array.isArray(s.lineItems) ? s.lineItems : [];
    const linha = function (a, b, cls) {
      const l = el("div", "pp-r-linha" + (cls ? " " + cls : ""));
      l.append(el("span", "", a), el("span", "", b));
      r.appendChild(l);
    };
    const fmt = formatador(s);
    r.textContent = "";
    // `total` da linha já vem com o cupom (medido no Stripe de teste): a linha mostra o valor de antes, e o
    // desconto sai numa linha só dele.
    itens.forEach(function (li) {
      const t = centavos(li.total);
      linha(li.name || "", fmt(t === null ? null : t + (centavos(li.discount) || 0)));
    });
    // O código aparece sempre que houver um; o valor, só se descontar algo hoje (no trial, o cupom só no plano
    // desconta 0 hoje e aparece no "Depois do teste").
    const desc = centavos(s.total && s.total.discount);
    const cod = (Array.isArray(s.discountAmounts) ? s.discountAmounts : [])
      .map(function (d) { return d && d.promotionCode; }).filter(function (c) { return typeof c === "string" && c; })[0];
    if (cod || desc > 0) linha(cod ? "Cupom " + cod + " aplicado" : "Desconto", desc > 0 ? "−" + fmt(desc) : "", "pp-r-desc");
    const hoje = centavos(s.total && s.total.total);
    linha("Total hoje", fmt(hoje), "pp-r-hoje");
    const prox = s.recurring && s.recurring.dueNext && centavos(s.recurring.dueNext.total);
    if (prox !== null && prox !== undefined) {
      r.appendChild(el("div", "pp-r-depois", (td > 0 ? "Depois do teste: " : "Depois: ") + fmt(prox) + por));
    }
    // Com trial, o que se paga hoje são os cadernos: as linhas com valor só escolhem o singular/plural.
    rotulo(td, hoje, itens.filter(function (li) { return centavos(li.total) > 0; }).length, fmt);
    return hoje;
  }

  /** O texto do botão. Sem total (antes do 1º `change`), nada de "Hoje você não paga nada". */
  function rotulo(td, hoje, n, fmt) {
    $("pp-cta").textContent = td > 0 ? "Começar meus " + td + " dias grátis"
      : hoje === null ? "Assinar" : "Assinar · " + fmt(hoje) + " hoje";
    let sub = "";
    if (td > 0 && hoje !== null) {
      // Guiado pelo TOTAL de hoje, nunca pela contagem de linhas.
      const o = n === 1 ? "só o caderno, " : n > 1 ? "só os cadernos, " : "";
      sub = hoje > 0 ? "Hoje: " + o + fmt(hoje) : "Hoje você não paga nada";
    }
    $("pp-cta-sub").textContent = sub;
  }

  /**
   * Monta a página. `d`: a resposta do create-checkout (publishable_key, client_secret, trial_days, extras).
   * `ctx`: `post` (o do assinar.js, com CSRF; nunca lança), `falha()` (plano B) e `expirou()` (S3 com "Tentar
   * de novo"). Devolve `{destroy}` depois de montar o Payment Element; as ações chegam depois (`loadActions`).
   */
  let atual = 0;  // a montagem mais nova: uma velha que resolve depois dela não toca no DOM (é o mesmo #pagina)
  function montar(d, ctx) {
    const td = Number(d.trial_days) || 0, eu = ++atual;
    // O ciclo vem da NOSSA resposta (o plano escolhido), não do SDK do Stripe.
    const por = d.interval === "annual" ? "/ano" : "/mês";
    const stripe = window.Stripe(d.publishable_key, { locale: "pt-BR" });
    return Promise.resolve(stripe.initCheckoutElementsSdk({
      clientSecret: Promise.resolve(d.client_secret),
      elementsOptions: { appearance: { variables: { colorPrimary: "#FF2D8E", borderRadius: "12px" } } },
    })).then(function (checkout) {
      if (eu !== atual) return { destroy: function () {} };
      limpa();
      // As caixas são do bump-caixas.js (as mesmas do modal do Pix); sem ele, o throw cai no plano B do assinar.js.
      const cbs = window.PBBumpCaixas.montar($("pp-bump"), Array.isArray(d.extras) ? d.extras : []);
      // voo: um /bump, cupom ou pagamento em andamento. temTotal: já chegou um `change` com o total.
      let actions = null, voo = false, vivo = true, temTotal = false, foco = null, prazo = null;
      // O conjunto que o servidor JÁ tem: é para ele que as caixas voltam quando um /bump falha.
      let confirmado = cbs.filter(function (c) { return c.checked; });
      const marcadas = function () { return cbs.filter(function (c) { return c.checked; }); };

      function pinta() {
        const morto = voo || !actions || !vivo;
        window.PBBumpCaixas.pintar($("pp-bump"), cbs, morto);
        $("pp-pagar").disabled = morto || !temTotal;
        $("pp-cupom-ok").disabled = morto;
      }

      /** Trava tudo durante um pedido; ao soltar, devolve o foco a quem o tinha (a caixa desabilitada o perde). */
      function trava(sim) {
        if (sim && !voo) foco = document.activeElement;
        voo = sim;
        pinta();
        if (sim || !foco) return;
        const f = foco, a = document.activeElement;
        foco = null;
        // Só se o foco ficou sem dono: quem já foi para outro campo (o cupom) não perde o que digita.
        if (f.isConnected && !f.disabled && (!a || a === document.body || a === f)) f.focus();
      }

      const volta = function () {
        cbs.forEach(function (c) { c.checked = confirmado.indexOf(c) !== -1; });
        aviso("pp-erro", ERRO_BUMP);
      };

      /** Leva ao Stripe o conjunto marcado na tela. true = o carrinho é o da tela; senão "fechada" (sessão
       *  paga ou expirada), "recusado" (o Stripe recusou o extra) ou false. Nunca lança. */
      async function sincroniza() {
        const posicoes = marcadas().map(function (c) { return Number(c.dataset.pos); });
        let erro = false;
        try {
          const r = await actions.runServerUpdate(async function () {
            const resp = await Promise.race([
              ctx.post("/billing/checkout/bump", { sid: actions.getSession().id, posicoes: posicoes }), espera(BUMP_MS)]);
            if (resp && resp.ok) return;
            const e = resp && resp.status === 409 && resp.d.detail && resp.d.detail.error;
            erro = e === "sessao_fechada" ? "fechada" : e === "extra_recusado" ? "recusado" : false;
            throw new Error("bump");
          });
          if (r && r.type === "error") return erro;
          confirmado = marcadas();
          return true;
        } catch (_) {
          return erro;
        }
      }

      async function marcou() {
        if (voo || !actions) return;
        aviso("pp-erro", "");
        trava(true);
        const ok = await sincroniza();
        if (!vivo) return;
        if (ok === "fechada") return ctx.expirou();
        if (ok !== true) volta();
        trava(false);
      }

      async function pagar() {
        if (voo || !actions || !temTotal) return;
        aviso("pp-erro", "");
        trava(true);
        // O que a tela mostra é o que se cobra: a sincronização vem ANTES do confirm, sempre que há caixa.
        const ok = cbs.length ? await sincroniza() : true;
        if (!vivo) return;
        if (ok === "fechada") return ctx.expirou();
        if (ok !== true) {
          if (ok === "recusado") volta(); else aviso("pp-erro", ERRO_SYNC);
          return trava(false);
        }
        let r;
        try { r = await actions.confirm(); } catch (_) { r = { type: "error" }; }  // exceção não vira texto
        if (!vivo) return;
        if (r && r.type === "error") {
          // Expirada se reconhece pelo status da sessão (open|expired|complete), não por um código de erro.
          let st = null;
          try { st = actions.getSession(); } catch (_) { /* sem sessão: segue o erro */ }
          if (expirada(st)) return ctx.expirou();
          aviso("pp-erro", msg(r, "O pagamento não foi concluído."));
          trava(false);
        }
        // Sucesso: o Stripe leva ao return_url; a tela fica travada até sair.
      }

      async function cupom(ev) {
        ev.preventDefault();
        const cod = $("pp-cupom-cod").value.trim();
        if (!cod || voo || !actions) return;
        aviso("pp-cupom-erro", "");
        trava(true);  // nem /bump nem Pagar com o cupom em voo
        let r;
        try { r = await actions.applyPromotionCode(cod); } catch (_) { r = { type: "error" }; }
        if (!vivo) return;
        trava(false);
        // O sucesso aparece no resumo ("Cupom X aplicado"), pelo `change`.
        if (r && r.type === "error") aviso("pp-cupom-erro", msg(r, "Cupom não aplicado."));
      }

      cbs.forEach(function (c) { c.addEventListener("change", marcou); });
      $("pp-pagar").onclick = pagar;
      $("pp-cupom").onsubmit = cupom;
      $("pp-cupom-abre").onclick = function () { $("pp-cupom-abre").hidden = true; $("pp-cupom").hidden = false; $("pp-cupom-cod").focus(); };
      rotulo(td, null, 0, brl);
      pinta();

      checkout.on("change", function (s) {
        if (!vivo || !s) return;
        if (expirada(s)) return ctx.expirou();
        temTotal = resumo(s, td, por) !== null;
        pinta();
      });
      $("pagamento").textContent = "";
      const pe = checkout.createPaymentElement({ wallets: { applePay: "never", googlePay: "never" } });
      pe.mount("#pagamento");
      // Limites aceitos (Tester, PR 3): rede muito lenta passa dos 10 s do loadActions e vai ao plano B; um /bump
      // que volta depois do prazo é corrigido pela sincronização antes do Pagar; o Pagar faz 1 /bump de sincronização quando
      // há caixas (conta no limite de 120/h por IP).
      // PR 6: o Express Checkout (Apple Pay/Google Pay) entra em #pp-express, acima do Payment Element.
      prazo = setTimeout(function () { if (vivo && !actions) ctx.falha(); }, ACOES_MS);
      Promise.resolve(checkout.loadActions()).then(function (r) {
        clearTimeout(prazo);
        if (!vivo) return;
        if (!r || r.type !== "success" || !r.actions) return ctx.falha();
        actions = r.actions;
        // O total da sessão já agora, sem depender de um `change` inicial.
        let s0 = null;
        try { s0 = actions.getSession(); } catch (_) { /* fica para o `change` */ }
        if (expirada(s0)) return ctx.expirou();
        if (s0) temTotal = resumo(s0, td, por) !== null;
        pinta();
      }, function () { clearTimeout(prazo); if (vivo) ctx.falha(); });

      return {
        destroy: function () {
          vivo = false;
          clearTimeout(prazo);
          if (eu === atual) limpa();
          try { pe.destroy(); } catch (_) { /* o iframe some com a tela */ }
        },
      };
    });
  }

  window.PBPagamento = { montar: montar };
})();
