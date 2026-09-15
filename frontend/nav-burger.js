// Nav mobile das páginas de marketing: o menu recolhido abaixo de 900px.
// Os MESMOS botões do site (Funcionalidades / Como funciona / Planos /
// WhatsApp) recolhem atrás de um botão, ao lado do entrar/conta. Antes
// ocupavam uma 2ª linha sempre aberta, e a nav é fixa: comia tela o tempo todo.
// (Esta prosa morava no cabeçalho do nav-auth.js, que ficou só com o menu da
// conta.) As 12 páginas públicas carregam os DOIS arquivos, e o `.nav` daqui
// segura o layout mobile também do usuário logado — quem escreve no .nav-right
// é o nav-auth.js, mas quem o põe em `order:2` é este CSS.
// Estilos injetados via <style> (prefixo pb-) pra não depender do cache do site.css.
(function () {
  function injectStyles() {
    // O id é `pb-nav-css` porque a site.css (bloco @media 340) e o caso 8 do
    // tests/frontend/nav_publica_320.test.mjs nomeiam exatamente este id.
    if (document.getElementById("pb-nav-css")) return;
    const s = document.createElement("style");
    s.id = "pb-nav-css";
    s.textContent = [
      /* ── Nav mobile: menu recolhido ───────────────────────────────────
         Os links ficavam SEMPRE abertos numa 2ª linha, e a nav é sticky (não
         fixed): o bloco acompanhava a rolagem comendo a tela. Agora recolhem. */
      ".pb-burger{display:none}",
      "@media (max-width:900px){",
      /* UM auto por linha, o resto por justify-content: dois `margin-left:auto` na
         mesma linha flex REPARTEM a sobra em vez de empurrar para a direita, e o
         "Entrar / Criar conta" descolava da borda. A sobra da 1ª linha vai para o
         logo; a 2ª, quando o botão quebra, alinha pelo container. O column-gap de
         6px nasceu para salvar 375px com o CTA LONGO e já não é o que segura essa
         largura. Os números que moravam aqui envelheceram duas vezes, então em vez de outro número, o comando (console, na largura que interessa): n=document.querySelector('.nav'),c=getComputedStyle(n),i=[...n.children].filter(e=>e.getBoundingClientRect().width>0),{pede:i.reduce((s,e)=>s+e.getBoundingClientRect().width,0)+parseFloat(c.columnGap)*(i.length-1),ha:n.clientWidth-parseFloat(c.paddingLeft)-parseFloat(c.paddingRight)} */
      ".nav{flex-wrap:wrap;row-gap:0;column-gap:6px;justify-content:flex-end}",
      ".nav .nav-logo{order:1;margin-right:auto}",
      ".nav .nav-right{order:2;margin-left:0}",
      /* alvo de toque 44×44 (mínimo iOS/WCAG) mesmo com o glifo pequeno */
      ".pb-burger{order:3;display:flex;align-items:center;justify-content:center;width:44px;height:44px;margin-left:0;padding:0;flex-shrink:0;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:12px;color:#fff;font-size:1.1rem;line-height:1;cursor:pointer;font-family:inherit}",
      ".pb-burger:hover{background:rgba(255,255,255,.12)}",
      ".nav .nav-links{order:4;display:flex;width:100%;flex-direction:column;align-items:stretch;gap:2px;margin:10px 0 0;padding-top:10px;border-top:1px solid rgba(255,255,255,.08)}",
      ".nav.pb-nav-ready .nav-links{display:none}",
      ".nav.pb-nav-open .nav-links{display:flex}",
      /* linha de 44px: item de menu, não link solto no meio da barra */
      ".nav .nav-links a{font-size:.95rem;white-space:nowrap;padding:11px 12px;border-radius:10px;min-height:44px;display:flex;align-items:center}",
      ".nav .nav-links a:hover{background:rgba(255,255,255,.06)}",
      "}",
    ].join("");
    document.head.appendChild(s);
  }

  // ── Botão do menu mobile ──────────────────────────────────────────────────
  // O CSS acima recolhe .nav-links abaixo de 900px; sem este botão não haveria
  // como reabrir. Quem mostra ou esconde o botão é o CSS; o JS só zera o estado
  // ao SAIR do breakpoint, senão pb-nav-open e aria-expanded="true" ficariam de
  // pé num botão invisível e quem gira o aparelho reencontraria o menu aberto.
  //
  // O botão entra ANTES do .nav-links de propósito: um DOM só serve a UMA das
  // duas ordens visuais (desktop logo→links→entrar, mobile logo→entrar→botão→
  // links), então uma delas sempre diverge do Tab. Esta posição mantém o
  // desktop igual ao visual e deixa o Tab entrar no menu ABERTO logo depois do
  // botão; o desvio sobra no mobile fechado. Pô-lo depois do .nav-right faria o
  // contrário e deixaria os links inalcançáveis por Tab com o menu aberto —
  // nada vem depois deles. Não mova sem medir os dois estados.
  function mountBurger() {
    const nav = document.querySelector(".nav");
    const links = nav && nav.querySelector(".nav-links");
    if (!nav || !links || nav.querySelector(".pb-burger")) return;

    if (!links.id) links.id = "pb-nav-links";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pb-burger";
    b.setAttribute("aria-label", "Abrir menu");
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-controls", links.id);
    b.innerHTML = "&#9776;";

    function setOpen(open) {
      nav.classList.toggle("pb-nav-open", open);
      b.setAttribute("aria-expanded", open ? "true" : "false");
      b.setAttribute("aria-label", open ? "Fechar menu" : "Abrir menu");
    }
    b.addEventListener("click", function () {
      setOpen(!nav.classList.contains("pb-nav-open"));
    });
    // Escape fecha e devolve o foco ao botão — senão o foco fica órfão no menu.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("pb-nav-open")) {
        setOpen(false);
        b.focus();
      }
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".pb-burger,.nav-links")) setOpen(false);  // fecha em TODO clique fora do gatilho e de dentro do menu — categoria MAIS LARGA que o `nav.contains` que substituiu, não só "o menu da conta também fecha": logo, `.nav-right` e o padding da barra passaram a fechar (medido; nas 12 páginas eles navegam, então o estado não chega a ser visto). `closest` pede target Element, e o `nav.contains` era total: `document.dispatchEvent(new MouseEvent("click"))` estoura (1 erro deslogado, 2 logado) e o setOpen não roda — sem emissor no frontend/, fica sem guarda (§0.2)
    });
    // Clicar num link fecha; em âncora a página não troca e o foco cairia no body.
    // Quem conserta é o `tabindex`: medido no Chromium, a navegação para o
    // fragmento foca o alvo sozinha quando ele vira focável — o `focus()` abaixo é
    // inerte aqui, e fica para motor onde isso não é garantido (NÃO medido).
    links.addEventListener("click", function (e) {
      const a = e.target.closest("a"), alvo = a && a.hash && document.getElementById(a.hash.slice(1));
      if (a) setOpen(false);
      if (alvo) { alvo.setAttribute("tabindex", "-1"); alvo.focus({ preventScroll: true }); }  // o tabindex fica no DOM pelo resto da sessão (as 2 seções da index, depois de ativadas): não muda ordem de Tab nem pinta anel fora do foco, mas é mutação permanente
    });
    // Passou de 900px: o botão some e o menu volta a ser a barra do desktop.
    const mq = window.matchMedia("(max-width:900px)");
    const onBreakpoint = e => { if (!e.matches) setOpen(false); };
    if (mq.addEventListener) mq.addEventListener("change", onBreakpoint);
    else if (mq.addListener) mq.addListener(onBreakpoint); // Safari < 14

    nav.insertBefore(b, links);
    nav.classList.add("pb-nav-ready");
  }

  injectStyles();
  mountBurger();
})();
