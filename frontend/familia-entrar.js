/**
 * /familia/entrar: aceita o convite do Plano Família. Sem React (esta página não carrega o painel).
 *
 * O token chega no fragmento (#t=...), sai da URL e do histórico no primeiro instante e fica só no
 * sessionStorage (familia-convite.js) e no corpo do POST de aceite. Nunca em query, log ou storage persistente.
 */
(function () {
  "use strict";

  const C = window.PBFamiliaConvite;
  const m = /^#t=(.+)$/.exec(location.hash);
  // Aberta com fragmento que não vira convite válido: o guardado de antes não pode passar por este link.
  if (location.hash) {
    if (!(m && C.guardar(m[1]))) C.limpar();
    history.replaceState(null, "", location.pathname + location.search);
  }

  const $ = function (id) { return document.getElementById(id); };
  const BASE = "/api/v2/familia";

  function el(tag, texto, attrs) {
    const e = document.createElement(tag);
    e.textContent = texto;
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  // Troca o conteúdo do cartão. `acoes`: [{texto, href | agir, primario}]
  function mostrar(titulo, texto, acoes, email) {
    $("titulo").textContent = titulo;
    $("texto").textContent = texto;
    $("texto").hidden = !texto;
    const quem = $("quem");
    quem.textContent = "";
    quem.hidden = !email;
    if (email) {
      quem.append("Entrando como " + email + ". ");
      const sair = el("a", "Não é você? Sair", { href: "#", style: "text-decoration:underline" });
      sair.addEventListener("click", function (ev) { ev.preventDefault(); sairDaConta(); });
      quem.append(sair);
    }
    const box = $("acoes");
    box.textContent = "";
    (acoes || []).forEach(function (a) {
      const b = a.href ? el("a", a.texto, { href: a.href }) : el("button", a.texto, { type: "button" });
      b.className = "btn btn-block btn-lg " + (a.primario ? "btn-primary" : "btn-ghost");
      if (a.agir) b.addEventListener("click", function () { a.agir(b); });
      box.append(b);
    });
  }

  function sairDaConta() {
    try { localStorage.setItem("finbot_logout_at", String(Date.now())); } catch (_) {}
    const fim = function () { location.assign("/painel"); };
    fetch("/auth/logout", { method: "POST", credentials: "same-origin", headers: window.pbCsrfHeaders ? window.pbCsrfHeaders() : {} }).then(fim, fim);
  }

  const GENERICO = "Não foi possível abrir o convite agora. Tente de novo.";
  const PAINEL = { texto: "Abrir painel", href: "/painel", primario: true };
  const tentar = { texto: "Tentar de novo", agir: function () { location.reload(); } };

  // O código do envelope v2 ({"error": {"code"}}); vazio se o corpo não for esse.
  function codigo(corpo) { return (corpo && corpo.error && corpo.error.code) || ""; }
  function json(r) { return r.json().catch(function () { return null; }); }

  function falha(r, corpo) { // erro do aceite: a regra de limpar o token mora aqui
    const c = codigo(corpo);
    if (r.status === 404 && c === "invite_invalid") { C.limpar(); return mostrar("Convite inválido", "Esse convite não vale mais. Peça um novo link.", [PAINEL]); }
    if (r.status === 409 && c === "already_in_family") { C.limpar(); return mostrar("Você já está numa família.", "", [PAINEL]); }
    if (r.status === 409 && c === "full") { C.limpar(); return mostrar("Família cheia", "Não há lugar nesta família.", [PAINEL]); }
    if (r.status === 402 && c === "family_coverage_required") return mostrar("Convite indisponível agora.", "Tente de novo mais tarde.", [tentar]);
    if (r.status === 429) return mostrar("Muitas tentativas", "Tente de novo mais tarde.", [tentar]);
    mostrar("Não foi possível entrar", GENERICO, [tentar]); // rede, 5xx e o resto: o token fica
  }

  function aceitar(botao) {
    if (botao.disabled) return;
    botao.disabled = true;
    botao.textContent = "Entrando…";
    const token = C.ler();
    if (!token) return mostrar("Link de convite inválido.", "", []);
    const json_ = { Accept: "application/json", "Content-Type": "application/json" };
    fetch(BASE + "/convites/aceitar", { method: "POST", credentials: "same-origin", headers: window.pbCsrfHeaders ? window.pbCsrfHeaders(json_) : json_, body: JSON.stringify({ token: token }) })
      .then(function (r) {
        if (r.ok) { C.limpar(); return mostrar("Você entrou na família.", "", [PAINEL]); }
        return json(r).then(function (corpo) { falha(r, corpo); });
      }, function () { mostrar("Não foi possível entrar", GENERICO, [tentar]); });
  }

  function inicio() {
    if (!C.ler()) return mostrar("Link de convite inválido.", "", []);
    fetch(BASE, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status !== 401) $("nav-entrar").style.display = "none"; // há sessão: o cartão já tem a ação; dois rosas não
      if (r.status === 401) return mostrar("Entre para aceitar o convite", "Depois de entrar, você volta aqui.", [{ texto: "Entrar", href: "/login", primario: true }, { texto: "Criar conta", href: "/cadastro" }]);
      if (r.ok) { C.limpar(); return mostrar("Você já está numa família.", "", [PAINEL]); }
      return json(r).then(function (corpo) {
        const c = codigo(corpo);
        if (r.status === 403 && c === "password_required") return mostrar("Defina uma senha para continuar.", "", [{ texto: "Definir senha", href: "/home", primario: true }]);
        if (r.status === 404 && c === "not_found") return confirmar();
        mostrar("Não foi possível abrir o convite", GENERICO, [tentar]);
      });
    }, function () { mostrar("Não foi possível abrir o convite", GENERICO, [tentar]); });
  }

  // Sem família: mostra com quem o aceite vai valer, e o clique é do usuário.
  function confirmar() {
    fetch("/auth/dashboard-profile", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? json(r) : null; }, function () { return null; })
      .then(function (p) {
        const email = p && typeof p.email === "string" ? p.email : "";
        // Sem saber com quem o aceite vale, não se oferece o aceite.
        if (!email) return mostrar("Não foi possível abrir o convite", GENERICO, [tentar]);
        mostrar("Entrar na família", "Você foi convidado para uma família no PigBank.", [{ texto: "Entrar na família", agir: aceitar, primario: true }], email);
      });
  }

  inicio();
})();
