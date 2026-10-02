/**
 * /assinar (funil v3 do quiz): cria a conta e abre o checkout do Stripe, embutido
 * ou hospedado. A /q manda `plano`/`ciclo` na query e nome, e-mail e WhatsApp no
 * FRAGMENTO (`n`, `e`, `w`). Este é o 1º script síncrono do <head>, ANTES do ponto
 * onde o `inject_tracking` põe o Pixel e o GA4: o topo tira o fragmento (e
 * p/r/e/c/n/w da query) da URL antes de qualquer rastreio ler `location.href`.
 * Os estados são as <section> da assinar.html; testes em tests/frontend/assinar_*.
 */
(function () {
  "use strict";

  // ── Topo síncrono: limpa a URL ────────────────────────────────────────────
  const frag = new URLSearchParams(location.hash.slice(1));
  // "{{" é o placeholder do XQuiz quando o campo vem vazio.
  const valor = function (v) { return v && v.indexOf("{{") === -1 ? v.trim() : ""; };
  const dados = {
    nome: valor(frag.get("n")),
    // O URLSearchParams lê `+` como espaço, e `+` é legítimo em e-mail.
    email: valor((frag.get("e") || "").replace(/ /g, "+")),
    whatsapp: valor(frag.get("w")),
  };
  const query = new URLSearchParams(location.search);
  // Link malmontado com PII na query: ela sai sem ser lida. O p/r não é desta
  // página (o cookie do quiz é da /q), então sai sem cookie.
  ["p", "r", "e", "c", "n", "w"].forEach(function (k) { query.delete(k); });
  const qs = query.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  const plano = query.get("plano");
  const ciclo = query.get("ciclo");

  // ── Estado ────────────────────────────────────────────────────────────────
  const TELAS = ["c-carga", "l0", "s1", "s2", "s3", "s4", "h", "f1"];
  const STRIPE_JS = "https://js.stripe.com/dahlia/stripe.js";
  const RELOGIO_MS = 10000, ME_MS = 5000;  // ME_MS: a espera pelo /auth/me no clique do S2
  const TXT = {
    0: "Sem conexão. Confira sua internet e tente de novo.",
    403: "Recarregue a página e tente de novo.",
    429: "Muitas tentativas. Aguarde alguns minutos e tente de novo.",
  };
  let estado = "c-carga";
  let gen = 0;          // muda a cada tela: a resposta de um pedido velho é descartada
  let voo = false;      // uma ação do usuário por vez (2º clique, Enter)
  let me = null;        // a sessão que JÁ estava no navegador, de outra conta (S1a)
  let contaEmail = "";  // a conta que vai pagar, para o "Assinando como"
  let checkout = null;
  let relogio = null;
  let stripeJs = null;
  let inicioSaiu = false;
  let refazer = null;
  const $ = function (id) { return document.getElementById(id); };

  function mostra(id) {
    estado = id;
    TELAS.forEach(function (t) { $(t).hidden = t !== id; });
    $("conta").hidden = !contaEmail || (id !== "s3" && id !== "s4");
    $("conteudo").classList.toggle("largo", id === "s4");
    return ++gen;
  }

  function aviso(id, texto, ok) {
    $(id).textContent = texto || "";
    $(id).classList.toggle("show", !!texto);
    $(id).classList.toggle("ok", !!ok);
  }

  /** POST da nossa API. Nunca lança: falha de rede volta como status 0. */
  async function post(url, corpo) {
    try {
      // pbCsrfHeaders relê o cookie a cada chamada: o logout reemite o CSRF.
      const r = await fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: window.pbCsrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(corpo || {}),
      });
      let d = null;
      try { d = await r.json(); } catch (_) { /* corpo vazio ou não-JSON */ }
      return { status: r.status, ok: r.ok, d: d && typeof d === "object" ? d : {} };
    } catch (_) {
      return { status: 0, ok: false, d: {} };
    }
  }

  // Quem navega devolve true e segura a trava até a página sair; o pageshow a
  // solta na volta pelo bfcache.
  async function acao(fn) {
    if (voo) return;
    voo = true;
    try { voo = (await fn()) === true; } catch (_) { voo = false; }
  }

  function mascara(email) {
    const i = email.lastIndexOf("@");
    return i > 0 ? email.charAt(0) + "•••" + email.slice(i) : email;
  }

  // ── S1: o formulário ──────────────────────────────────────────────────────
  async function criaConta(segunda) {
    aviso("s1-erro", "");
    $("s1-pendente").hidden = true;
    const corpo = {
      nome: $("nome").value.trim(), email: $("email").value.trim(),
      whatsapp: $("whatsapp").value.trim(), aceitou_termos: $("termos").checked,
    };
    const r = await post("/auth/quiz/conta", corpo);
    const e = r.d.estado;
    if (r.ok && (e === "criada" || e === "logado")) {
      if (e === "criada" && r.d.user_id) {
        // Mesmo molde do cadastro.html: o eventID casa com o CAPI do servidor.
        if (window.fbq) window.fbq("track", "CompleteRegistration", {}, { eventID: "signup_" + r.d.user_id });
        if (window.pbTrack) window.pbTrack("sign_up", { method: "quiz" });
      }
      me = null;  // a sessão nova substituiu a de outra conta, se havia
      contaEmail = corpo.email;
      iniciaS3();
      return;
    }
    if (r.ok && e === "tem_conta") {
      $("s2-email").textContent = corpo.email;
      aviso("s2-erro", "");
      mostra("s2");
      return;
    }
    if (r.ok && e === "cadastro_pendente") { $("s1-pendente").hidden = false; return; }
    // Outra aba ou pedido do mesmo e-mail está com a trava: tenta uma vez mais.
    if (r.status === 409 && e === "ocupado" && !segunda) {
      await new Promise(function (ok) { setTimeout(ok, 1000); });
      return criaConta(true);
    }
    let texto = TXT[r.status] || "Não deu para criar sua conta. Tente de novo.";
    if (r.status === 409) texto = "Não deu certo agora. Tente de novo em instantes.";
    // `detail` só quando é texto: o 422 manda uma lista, que viraria "[object Object]".
    if (r.status === 400 && typeof r.d.detail === "string") texto = r.d.detail;
    aviso("s1-erro", texto);
  }

  // ── S2: já tem conta ──────────────────────────────────────────────────────
  /** Logout. A limpeza do auth-refresh roda com qualquer resposta, ou sem ela. */
  async function saiDaConta() { await post("/auth/logout"); me = null; contaEmail = ""; }

  /** O e-mail logado AGORA (carga ou outra aba); "" só no 401; null se não deu para saber (rede,
   *  5xx, `ms`, JSON quebrado). O S2 desloga com e-mail ou null (o /login devolveria a conta logada);
   *  com "" não (o Clear-Site-Data apagaria o _fbc). Via auth-refresh: 401 sem refresh limpa o storage
   *  → begin() depois. `ms` só no clique (o `voo` não fica preso); a carga espera (plano §3, C). */
  function sessaoViva(ms) {
    const c = new AbortController(), t = ms && setTimeout(function () { c.abort(); }, ms);
    return fetch("/auth/me", { credentials: "same-origin", signal: c.signal }).then(function (r) {
      if (r.status === 401) return "";
      return r.ok ? r.json().then(function (s) { return s && typeof s.email === "string" ? s.email : ""; }) : null;
    }).catch(function () { return null; }).finally(function () { clearTimeout(t); });
  }

  async function entrarComSenha() {
    if (me || (await sessaoViva(ME_MS)) !== "") await saiDaConta();
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    return true;
  }

  async function entrarComGoogle() {
    if (me || (await sessaoViva(ME_MS)) !== "") await saiDaConta();
    // DEPOIS do logout: a limpeza dele apaga a sessionStorage, intenção inclusive.
    const PI = window.PBPurchaseIntent;
    PI.begin(plano, ciclo, "card");
    PI.markAwaitingAuth();
    location.href = "/auth/google/start?next=" + encodeURIComponent(PI.continueUrl());
    return true;
  }

  async function esqueci() {
    const r = await post("/auth/forgot-password", { email: $("email").value.trim() });
    if (r.ok && typeof r.d.message === "string") aviso("s2-erro", r.d.message, true);
    else aviso("s2-erro", TXT[r.status] || "Não deu certo agora. Tente de novo em instantes.");
  }

  // ── S3: prepara o pagamento ───────────────────────────────────────────────
  async function iniciaS3() {
    $("conta-email").textContent = mascara(contaEmail);
    $("s3-titulo").textContent = "Preparando o pagamento…";
    aviso("s3-erro", "");
    $("s3-retry").hidden = true;
    const g = mostra("s3");
    // No app o embutido não abre (D-p): nem POST embutido, nem Stripe.js.
    if (window.PB_IN_APP) return irParaHospedado();
    const [r, cfg] = await Promise.all([
      post("/billing/create-checkout", { plan: plano, interval: ciclo, embutido: true, origem: "assinar" }),
      fetch("/billing/plans-config", { credentials: "same-origin" })
        .then(function (x) { return x.json(); }).catch(function () { return null; }),
    ]);
    if (g !== gen) return;
    if (!(r.ok && r.d.client_secret && r.d.publishable_key)) return falhaCheckout(r, iniciaS3);
    const td = Number(r.d.trial_days) || 0;
    $("s4-trial").textContent = td > 0
      ? "Você tem " + td + " dias grátis. A cobrança do plano só começa depois, e dá para cancelar antes."
      : "Esta assinatura não tem período grátis: a cobrança começa hoje.";
    $("s4-pix").hidden = !(cfg && cfg.pix_annual_available === true);
    montar(r.d.publishable_key, r.d.client_secret);
  }

  /** Desfecho de um create-checkout que não deu certo, embutido (S3) ou hospedado (H). */
  function falhaCheckout(r, deNovo) {
    const d = r.d.detail && typeof r.d.detail === "object" ? r.d.detail : {};
    if (r.status === 409 && ["already_subscribed", "lifetime", "pix_active"].includes(d.error)) {
      if (d.error === "pix_active" && typeof d.message === "string") $("f1-msg").textContent = d.message;
      mostra("f1");
      return;
    }
    if (r.status === 401) {  // o auth-refresh já tentou renovar
      me = null; contaEmail = "";
      mostra("s1");
      aviso("s1-erro", "Sua sessão expirou. Entre de novo para continuar.");
      return;
    }
    $("s3-titulo").textContent = "Não deu para abrir o pagamento agora.";
    aviso("s3-erro", TXT[r.status] || "");
    refazer = deNovo;
    $("s3-retry").hidden = false;
    mostra("s3");
  }

  // ── S4: o embutido ────────────────────────────────────────────────────────
  function carregaStripe() {
    if (!stripeJs) {
      stripeJs = new Promise(function (ok, falha) {
        const s = document.createElement("script");
        s.src = STRIPE_JS;
        s.onload = ok;
        s.onerror = falha;
        document.head.appendChild(s);
      });
    }
    return stripeJs;
  }

  function montar(pk, cs) {
    const g = mostra("s4");
    $("stripe-checkout").textContent = "";
    // Desde a INJEÇÃO, e não desde a montagem: um Stripe.js pendurado (sem load
    // nem error) nunca chegaria a ela, e a página ficaria em "carregando".
    relogio = setTimeout(function () {
      if (!document.querySelector("#stripe-checkout iframe")) irParaHospedado();
    }, RELOGIO_MS);
    carregaStripe()
      .then(function () {
        if (g !== gen) return;
        // Sem `window.Stripe`, ou API que lança: o TypeError cai no catch → H.
        // Promise.resolve: aceita a montagem que devolve o objeto direto ou uma Promise.
        return Promise.resolve(window.Stripe(pk)
          .createEmbeddedCheckoutPage({ fetchClientSecret: function () { return Promise.resolve(cs); } }))
          .then(function (c) {
            if (g !== gen) { c.destroy(); return; }
            checkout = c;
            c.mount("#stripe-checkout");
            disparaInicio();
          });
      })
      .catch(function () { irParaHospedado(); });
  }

  function destroiEmbutido() {
    clearTimeout(relogio);
    if (checkout) {
      try { checkout.destroy(); } catch (_) { /* o iframe some com a página */ }
      checkout = null;
    }
  }

  /** InitiateCheckout e begin_checkout, uma vez por página. */
  function disparaInicio(depois) {
    const fim = depois || function () {};
    if (inicioSaiu) return fim();
    inicioSaiu = true;
    if (window.fbq) window.fbq("track", "InitiateCheckout", { content_category: plano, content_name: ciclo });
    // Sem `value`: a /assinar não tem tabela de preços, e criar uma seria a 2ª cópia.
    if (window.pbTrack) window.pbTrack("begin_checkout", { items: [{ item_id: plano, item_category: ciclo }] }, fim);
    else fim();
  }

  /**
   * O ÚNICO caminho para o hospedado (plano B). Gatilhos: o app, o Stripe.js que
   * não carrega, a montagem que falha, o relógio sem iframe e o link manual.
   * A guarda é o estado: só sai de S3 ou S4, e a tela H trava os outros gatilhos.
   */
  async function irParaHospedado() {
    if (estado !== "s3" && estado !== "s4") return;
    destroiEmbutido();
    const g = mostra("h");
    const r = await post("/billing/create-checkout", { plan: plano, interval: ciclo, embutido: false, origem: "assinar" });
    if (g !== gen) return;
    if (!(r.ok && typeof r.d.checkout_url === "string")) return falhaCheckout(r, irParaHospedado);
    // `replace`: o Voltar do Stripe não cai de novo nesta tela, que dispararia outro H.
    disparaInicio(function () { location.replace(r.d.checkout_url); });
  }

  function pix() {
    clearTimeout(relogio);
    estado = "pix";  // o relógio e os erros do embutido não mandam mais para o H
    const PI = window.PBPurchaseIntent;
    PI.begin(plano, "annual", "pix");
    PI.markAwaitingAuth();
    location.href = "/continuar-compra";
    return true;
  }

  async function sair() {
    destroiEmbutido();
    estado = "sair"; gen++;  // nenhum gatilho nem resposta velha age durante o logout
    await saiDaConta();
    mostra("s1");
  }

  // ── Carga ─────────────────────────────────────────────────────────────────
  function liga() {
    $("form-s1").addEventListener("submit", function (ev) {
      ev.preventDefault();
      acao(criaConta);
    });
    $("s2-senha").addEventListener("click", function () { acao(entrarComSenha); });
    $("s2-google").addEventListener("click", function () { acao(entrarComGoogle); });
    $("s2-esqueci").addEventListener("click", function () { acao(esqueci); });
    $("s2-outro").addEventListener("click", function () { mostra("s1"); $("email").focus(); });
    $("s3-retry").addEventListener("click", function () { if (refazer) refazer(); });
    $("s4-b").addEventListener("click", function () { irParaHospedado(); });
    $("s4-pix").addEventListener("click", function () { acao(pix); });
    $("sair").addEventListener("click", function () { acao(sair); });
  }

  // Volta pelo bfcache (do /login ou da /continuar-compra): a tela fica, a trava sai.
  window.addEventListener("pageshow", function (ev) {
    if (!ev.persisted) return;
    voo = false;
    if (estado === "pix") estado = "s4";  // o embutido continua montado
  });

  document.addEventListener("DOMContentLoaded", async function () {
    Object.keys(dados).forEach(function (id) { $(id).value = dados[id]; });  // as chaves são os ids
    liga();
    const PI = window.PBPurchaseIntent;
    if (!PI || !PI.isValidChoice(plano, ciclo)) { mostra("l0"); return; }
    // "Plus · Mensal · Cartão" → "Plus · Mensal": os nomes ficam numa fonte só.
    $("s1-titulo").textContent = "Falta pouco para assinar o "
      + PI.summary({ plan: plano, cycle: ciclo, method: "card" }).split(" · ").slice(0, 2).join(" · ");
    const atual = await sessaoViva();
    if (atual && (!dados.email || dados.email.toLowerCase() === atual.toLowerCase())) {
      contaEmail = atual;
      iniciaS3();
      return;
    }
    me = atual || null;  // S1a: e-mail digitado ≠ conta logada
    mostra("s1");
  });
})();
