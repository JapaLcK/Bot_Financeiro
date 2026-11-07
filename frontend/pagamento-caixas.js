/**
 * Página de pagamento própria: o resumo do pedido e o texto do botão, a partir da sessão do Stripe (as caixas do
 * order bump são do bump-caixas.js, as mesmas do modal do Pix). Quem monta, trava e paga é o pagamento-pagina.js,
 * que usa `window.PBPagamentoCaixas`. Texto vindo do Stripe (nome do item, cupom) entra SÓ por `textContent`.
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const brl = function (c) { return typeof c === "number" && isFinite(c) ? BRL.format(c / 100) : ""; };
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

  function el(tag, cls, texto) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto) e.textContent = texto;
    return e;
  }

  const primeiraCobranca = function (d) {
    const r = d && d.recurring;
    return !!d && !d.promotionCode && d.percentOff > 0 && !!r && r.type === "repeating" && r.durationInMonths === 1;
  };
  /**
   * A linha "depois" do resumo: `{depois}` (o `dueNext.total` do Stripe) ou, com cupom AUTOMÁTICO de 1ª cobrança
   * (sem código, `percentOff`, repeating de 1 mês) e trial curto, `{primeira, depois}`: a 1ª cobrança e o preço
   * cheio da renovação. Exceção à regra "o front não faz conta": medido com test clock em 2026-11-06, com o e-book
   * (one_time) no carrinho o `dueNext` chega SEM o desconto (total 19900, discount 0), e a fatura real cobra 11940.
   * Código digitado fica de fora (pode durar meses): mantém o `dueNext.total`.
   */
  function proxima(s, td, hoje) {
    const n = s.recurring && s.recurring.dueNext, tot = n && centavos(n.total), sub = n && centavos(n.subtotal);
    if (tot === null || tot === undefined) return null;
    const auto = (Array.isArray(s.discountAmounts) ? s.discountAmounts : []).find(primeiraCobranca);
    if (!(td > 0 && td < 28 && auto && sub !== null)) return { depois: tot };
    // Stripe mandou o desconto: o número dele ganha. Senão, só com cobrança hoje (o e-book): a conta do Stripe, que arredonda o DESCONTO.
    const primeira = centavos(n.discount) > 0 || !(hoje > 0) ? tot : sub - Math.round(sub * auto.percentOff / 100);
    return primeira < sub ? { primeira: primeira, depois: sub } : { depois: tot };
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
    const p = proxima(s, td, hoje);
    if (p) {
      r.appendChild(el("div", "pp-r-depois", p.primeira === undefined
        ? (td > 0 ? "Depois do teste: " : "Depois: ") + fmt(p.depois) + por
        : fmt(p.primeira) + " na 1ª cobrança, depois " + fmt(p.depois) + por));
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

  window.PBPagamentoCaixas = { resumo: resumo, rotulo: rotulo, brl: brl };
})();
