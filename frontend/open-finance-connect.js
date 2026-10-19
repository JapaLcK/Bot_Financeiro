/**
 * frontend/open-finance-connect.js — escolher um banco e conectá-lo pelo
 * Open Finance (Pluggy). Extraído do settings.html para que a tela de
 * Configurações e o wizard de primeira configuração usem O MESMO código.
 *
 * O módulo não sabe o que é "Configurações" nem "onboarding": tudo que é
 * específico de tela entra por callback no `init`. É esse o seam — sem ele, a
 * alternativa era copiar ~550 linhas para a segunda tela.
 *
 *   PBOpenFinance.init({ apiBase, userId, onConnected, ... });
 *   PBOpenFinance.open();      // abre o modal na hora; preenche depois
 *   PBOpenFinance.open({ id, name });  // idem, com esse banco já escolhido e à vista (o /painel, "Disponíveis para conectar")
 *   PBOpenFinance.destroy();   // remove markup e listeners; idempotente
 *   PBOpenFinance.busy();      // há conexão em curso? (um host que desmonta sem destruir enquanto há, para o item autorizado ser salvo)
 *   init({ guardaDuploClique: true }) // opcional: nos 350 ms depois de abrir, linha e "Conectar" também ignoram o clique (2º clique de um duplo clique no botão do host)
 *   PBOpenFinance.reconnect(itemId);  // reconecta um banco já conectado, sem picker
 *
 * Reconexão (`reconnect`): connect-token com `item_id` e `updateItem` no widget,
 * sem `selectedConnectorId`. Callbacks extras: `onOpen()` quando o widget foi iniciado (não quando terminou de abrir);
 * `onError(msg, status)` (status HTTP quando vem do servidor; erro do SDK não tem);
 * `onClose()` (sem argumento) só depois de o /pluggy-item terminar — nada disso muda
 * o fluxo do picker.
 *
 * Uma conexão por vez: do início do connect (picker ou reconexão) até o onClose,
 * o erro ou o fim do save, `open()` e `reconnect()` são IGNORADOS SEM CALLBACK
 * (`reconnect()` devolve `false` nesse caso; senão devolve a promessa do connect) —
 * duplo clique não vira dois connect-tokens e dois widgets, e `onError` aqui
 * pintaria erro pra quem só clicou duas vezes. Depois do `destroy()` nenhum
 * callback do host dispara para a conexão que estava em curso.
 * O `onError`/`onClose` de um widget VELHO (já há outra conexão em curso, `ocupado` é outra sessão) não chegam ao host: a conexão mais nova é dona dele.
 * `init()` do SDK que rejeita: `onError` e em seguida `onClose()`, e a guarda é liberada.
 * `onError` do SDK também libera a guarda: a Pluggy não garante `onClose` depois dele, e
 * sem isso o settings (que reabilita o botão no onError) ficaria mudo até recarregar. O
 * custo é o de antes desta guarda: com o widget com erro ainda na tela, um novo clique
 * abre outro; o `onClose` daquele chega depois ao host como tardio.
 *
 * A pré-escolha só põe o banco como selecionado: quem toca "Conectar" ainda é o usuário, e o confirmar (teto, plano, reconexão)
 * é o mesmo. Id fora da lista de bancos = sem pré-escolha. Um argumento que não é `{id inteiro, name texto}` (um Event, por
 * exemplo) é ignorado: `open()` sem argumento segue idêntico.
 *
 * A ordem do original é preservada de propósito: o modal aparece SÍNCRONO e só
 * o preenchimento é assíncrono. O estado do plano é rebuscado em paralelo e
 * quem espera por ele é o confirmar — que é onde a decisão importa, porque é
 * dali que se abre a Pluggy.
 *
 * Servido por @router.get("/open-finance-connect.js") em
 * frontend/routes/static_pages.py — sem essa rota o arquivo dá 404 e o sintoma
 * só aparece no navegador (não há StaticFiles mount neste projeto).
 *
 * Sem framework e sem build, como o resto do frontend. Sem `onclick` inline: o
 * markup é montado com createElement/textContent e os cliques chegam por
 * delegação — o que também dispensa um helper de escape e tira handlers inline
 * do settings.html, na direção de fechar o 'unsafe-inline' do script-src.
 */
(function () {
  "use strict";

  /* ─── Configuração e estado ───────────────────────────────────────────── */

  let cfg = null;          // o que o host passou no init
  let root = null;         // nó do overlay, injetado sob demanda
  let onKeyRef = null;     // listener de teclado ativo (só enquanto aberto)
  let lastFocus = null;    // quem tinha o foco antes de abrir
  let connectors = null;   // cache da lista da Pluggy (não muda durante a sessão)
  let selected = null;     // {id, name}
  let abertoEm = 0;        // performance.now() da última abertura do picker (ver o clique no fundo em onClick)
  let trazer = false;      // a abertura veio com pré-escolha: depois de desenhar a lista, leva o banco à vista
  // Limites do plano e conexões, rebuscados a cada open() — confiar no que a
  // página leu no load faz quem acabou de fazer upgrade em outra aba continuar
  // batendo no teto antigo.
  //
  // A busca roda EM PARALELO com a abertura, não antes dela: quem espera é o
  // confirmar, que é onde a decisão importa (é ali que se abre a Pluggy e nasce
  // o risco de consentimento órfão). Bloquear a abertura punha dois fetches na
  // frente do modal e abria uma janela de corrida que o fluxo original não
  // tinha — ele mostrava o modal na hora e só o preenchimento era assíncrono.
  //
  // O resultado NÃO vira estado compartilhado: `refreshLimits()` devolve um
  // retrato, e quem decide usa o retrato que ele mesmo esperou. Com um objeto
  // mutável no módulo, duas buscas concorrentes (abre, fecha, abre) podiam
  // terminar fora de ordem e a mais VELHA sobrescrever a mais nova pouco antes
  // de o confirmar ler — decidindo teto e reconexão com dado vencido.
  let limitsPromise = null;   // busca em voo, iniciada no open()
  // Conexão em andamento (guarda de uma por vez) e geração do destroy(): cada
  // connect() fixa a geração em que nasceu e, se ela mudou, é um fluxo morto —
  // seus callbacks (widget e save) não chegam mais ao host.
  let ocupado = null;
  let geracao = 0;

  function noop() {}

  /**
   * Resolve um callback do host. `host` permite fixar QUAL configuração recebe
   * o aviso: o fluxo de conexão captura a sua no início e despacha por ela, pra
   * o resultado de quem iniciou não ser pintado na tela de quem estiver
   * configurado quando a Pluggy terminar. Sem `host`, usa a atual.
   */
  function conf(name, host) {
    const c = host || cfg;
    return (c && typeof c[name] === "function") ? c[name] : noop;
  }

  /* ─── Helpers de texto (vindos do settings.html sem mudança) ──────────── */

  function stripAccent(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function bankInitials(name) {
    const small = { de: 1, do: 1, da: 1, dos: 1, das: 1, e: 1, "-": 1 };
    const parts = (name || "").replace(/[^\wÀ-ÿ\s-]/g, "").split(/\s+/)
      .filter(function (w) { return w && !small[w.toLowerCase()]; });
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return (name || "?").replace(/[^A-Za-zÀ-ÿ]/g, "").slice(0, 2).toUpperCase();
  }

  function bankColor(hex) {
    const c = String(hex || "").replace("#", "");
    return /^[0-9a-f]{6}$/i.test(c) ? "#" + c : "#5f6470";
  }

  /* ─── Rede ────────────────────────────────────────────────────────────── */

  function headers(extra) {
    // Implementação única, do auth-refresh.js, que as duas páginas carregam.
    return window.pbCsrfHeaders ? window.pbCsrfHeaders(extra) : (extra || {});
  }

  function url(path) {
    return (cfg && cfg.apiBase ? cfg.apiBase : "") + path;
  }

  /**
   * O `detail` do FastAPI vem string OU objeto estruturado — o limite de bancos
   * por plano, por exemplo, chega como {code:"OF_BANK_LIMIT", message:"… /precos"}.
   * Aceitar só string jogaria fora justamente a mensagem acionável e mostraria
   * um genérico no lugar. Mesma regra do readApiError() que havia no
   * settings.html antes desta extração.
   */
  async function apiError(resp) {
    let raw = "";
    try { raw = await resp.text(); } catch (_e) { /* corpo ilegível */ }
    let msg;
    try {
      const detail = JSON.parse(raw).detail;
      msg = (detail && typeof detail === "object") ? (detail.message || detail.code || raw) : (detail || raw);
    } catch (_e) {
      msg = raw || "Erro inesperado";
    }
    // O status viaja junto: quem reconecta decide pelo código (401 sessão, 404 banco sumiu…).
    const err = new Error(msg);
    err.status = resp.status;
    return err;
  }

  /**
   * Toda requisição do módulo entra neste controlador, e `destroy()` o aborta.
   *
   * É o que fecha, de uma vez, a classe inteira de "operação em voo sobrevive
   * ao destroy": em vez de uma checagem de propriedade depois de cada await —
   * que precisa ser lembrada em cada await novo, e não foi em três deles —, o
   * próprio fetch é cancelado e a continuação nunca roda. Mecanismo da
   * plataforma no lugar de disciplina de quem escreve.
   */
  let inflight = null;

  function signal() {
    if (!inflight) inflight = new AbortController();
    return inflight.signal;
  }

  function abortInflight() {
    if (inflight) { inflight.abort(); inflight = null; }
  }

  /** Erro de cancelamento não é falha: ninguém precisa ser avisado. */
  function foiAbortado(err) {
    return !!err && (err.name === "AbortError" || err.__abort);
  }

  async function get(path) {
    const resp = await fetch(url(path), {
      credentials: "same-origin", headers: headers(), signal: signal(),
    });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  async function post(path, body) {
    const resp = await fetch(url(path), {
      method: "POST",
      credentials: "same-origin",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body || {}),
      signal: signal(),
    });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  /** Conexões que contam no teto — espelha o backend (`count_open_finance_connections`):
   *  só provider "pluggy" (mock não conta) e PAUSED não conta. */
  function countsTowardLimit(conn) {
    return !!conn && conn.provider === "pluggy" &&
      String(conn.status || "").toUpperCase() !== "PAUSED";
  }

  /**
   * Retrato do teto do plano e das conexões, devolvido a quem pediu — nunca
   * escrito num objeto do módulo, pra duas buscas concorrentes não terminarem
   * fora de ordem e a mais velha decidir pela mais nova.
   *
   * Devolve null se qualquer um dos dois lados não vier, e o confirmar trata
   * null como "não dá pra seguir". Falha aqui não pode virar permissão: com um
   * default permissivo, quem está no teto autorizaria a instituição na Pluggy
   * antes de o /pluggy-item recusar com 402, deixando o consentimento órfão.
   */
  async function refreshLimits() {
    let me, of;
    try {
      const both = await Promise.all([
        get("/auth/me"),
        get("/open-finance/" + cfg.userId),
      ]);
      me = both[0];
      of = both[1];
    } catch (_e) {
      return null;
    }

    const counted = ((of && of.connections) || []).filter(countsTowardLimit);
    return {
      banksMax: (me && me.of_banks_max !== undefined) ? me.of_banks_max : null,
      cobrancaEmAtraso: !!(me && me.cobranca_em_atraso),
      count: counted.length,
      ids: counted.map(function (c) { return String(c.institution_id); }),
    };
  }

  /* ─── Markup ──────────────────────────────────────────────────────────── */

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildRoot() {
    const overlay = el("div", "bankpick-overlay");
    overlay.id = "bankpick-overlay";

    const modal = el("div", "bankpick-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Conectar banco");

    const head = el("div", "bankpick-head");
    const headRow = el("div", "bankpick-head-row");
    const titles = el("div");
    titles.appendChild(el("div", "bankpick-title", "Conectar banco"));
    titles.appendChild(el("div", "bankpick-sub", "Busque entre as instituições disponíveis"));
    const x = el("button", "bankpick-x");
    x.type = "button";
    x.setAttribute("aria-label", "Fechar");
    x.setAttribute("data-act", "close");
    x.innerHTML = '<i class="ph ph-x" aria-hidden="true"></i>';
    headRow.appendChild(titles);
    headRow.appendChild(x);

    const searchWrap = el("div", "bankpick-search-wrap");
    const icon = el("span");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<i class="ph ph-magnifying-glass" aria-hidden="true"></i>';
    const search = el("input");
    search.id = "bankpick-search";
    search.type = "text";
    search.autocomplete = "off";
    search.placeholder = "Buscar banco (ex: Nubank, Itaú, C6…)";
    searchWrap.appendChild(icon);
    searchWrap.appendChild(search);

    head.appendChild(headRow);
    head.appendChild(searchWrap);

    const list = el("div", "bankpick-list");
    list.id = "bankpick-list";

    const foot = el("div", "bankpick-foot");
    const count = el("div", "bankpick-count", "Nenhum banco selecionado");
    count.id = "bankpick-count";
    const go = el("button", "btn-connect bankpick-go", "Conectar Open Finance");
    go.id = "bankpick-go";
    go.type = "button";
    go.disabled = true;
    go.setAttribute("data-act", "confirm");
    foot.appendChild(count);
    foot.appendChild(go);

    modal.appendChild(head);
    modal.appendChild(list);
    modal.appendChild(foot);
    overlay.appendChild(modal);

    // Delegação: um listener no root, nenhum handler inline.
    overlay.addEventListener("click", onClick);
    search.addEventListener("input", function () { renderList(search.value); });
    return overlay;
  }

  function ensureRoot() {
    if (root && document.contains(root)) return root;
    root = buildRoot();
    document.body.appendChild(root);
    return root;
  }

  function q(sel) { return root ? root.querySelector(sel) : null; }

  /* ─── Render da lista ─────────────────────────────────────────────────── */

  function renderList(filter) {
    const listEl = q("#bankpick-list");
    if (!listEl) return;
    const f = stripAccent(filter || "").trim();
    const items = (connectors || []).filter(function (b) {
      return stripAccent(b.name).indexOf(f) !== -1;
    });

    listEl.innerHTML = "";
    if (!items.length) {
      listEl.appendChild(el("div", "bankpick-empty",
        'Nenhum banco encontrado para "' + (filter || "") + '".'));
      return;
    }

    let letter = "";
    items.forEach(function (b) {
      const L = (b.name[0] || "#").toUpperCase();
      if (L !== letter) {
        letter = L;
        listEl.appendChild(el("div", "bankpick-alpha", L));
      }
      const on = selected && selected.id === b.id;
      const row = el("button", "bank-row bankpick-row" + (on ? " active" : ""));
      row.type = "button";
      row.setAttribute("data-name", b.name);
      row.setAttribute("data-id", String(b.id));

      const logo = el("span", "bank-logo bankpick-logo", bankInitials(b.name));
      logo.style.color = bankColor(b.color);
      if (b.logo) {
        const img = document.createElement("img");
        img.src = b.logo;
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", function () { img.remove(); });
        logo.appendChild(img);
      }

      const info = el("span", "bank-info");
      info.appendChild(el("span", "bank-name", b.name));
      info.appendChild(el("span", "bank-meta",
        b.inv ? "Conta, cartão · caixinha/investimentos" : "Conta corrente e cartão"));

      const check = el("span", "bank-check");
      if (on) check.innerHTML = '<i class="ph ph-check" aria-hidden="true"></i>';

      row.appendChild(logo);
      row.appendChild(info);
      row.appendChild(check);
      listEl.appendChild(row);
    });
  }

  function pick(id, node) {
    selected = { id: id, name: node ? node.getAttribute("data-name") : "" };
    const rows = root ? root.querySelectorAll("#bankpick-list .bank-row") : [];
    Array.prototype.forEach.call(rows, function (r) {
      const on = r === node;
      r.classList.toggle("active", on);
      const chk = r.querySelector(".bank-check");
      if (chk) chk.innerHTML = on ? '<i class="ph ph-check" aria-hidden="true"></i>' : "";
    });
    syncFoot();
  }

  function syncFoot() {
    const count = q("#bankpick-count");
    const go = q("#bankpick-go");
    if (!count || !go) return;
    if (selected) {
      count.textContent = "";
      count.appendChild(document.createTextNode("Selecionado: "));
      count.appendChild(el("strong", null, selected.name));
      go.disabled = false;
    } else {
      count.textContent = "Nenhum banco selecionado";
      go.disabled = true;
    }
  }

  /* ─── Teclado: o trap que o aria-modal promete ────────────────────────── */

  function focusables() {
    const modal = q(".bankpick-modal");
    if (!modal) return [];
    const sel = 'a[href], button:not([disabled]), input:not([disabled]),'
            + ' select:not([disabled]), textarea:not([disabled]),'
            + ' [tabindex]:not([tabindex="-1"])';
    return Array.prototype.filter.call(modal.querySelectorAll(sel), function (e) {
      return e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0;
    });
  }

  function onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab") return;

    const modal = q(".bankpick-modal");
    const alvos = focusables();
    if (!modal || !alvos.length) return;

    const primeiro = alvos[0];
    const ultimo = alvos[alvos.length - 1];
    const proximo = e.shiftKey ? ultimo : primeiro;
    const borda = e.shiftKey ? primeiro : ultimo;

    // `foco fora` cobre o caso de já ter escapado (clique no fundo, foco no body)
    if (!modal.contains(document.activeElement) || document.activeElement === borda) {
      e.preventDefault();
      proximo.focus();
    }
  }

  /* ─── Cliques (delegados) ─────────────────────────────────────────────── */

  function onClick(e) {
    // Nos primeiros 350 ms o clique pode ser o 2º de um duplo clique no botão que abriu o picker: ele cai no fundo (que já cobre a
    // página) ou numa linha do modal (escolheria outro banco). O fundo é sempre ignorado nesse prazo; linha e "Conectar" só com
    // `guardaDuploClique` no init (o /painel); só o "fechar" explícito vale. O mousedown tirou o foco da busca: devolve-o.
    if (performance.now() - abertoEm < 350) {
      const modal = q(".bankpick-modal"), busca = q("#bankpick-search");
      if (modal && busca && !modal.contains(document.activeElement)) busca.focus();
      if ((e.target === root || cfg.guardaDuploClique) && !(e.target.closest && e.target.closest('[data-act="close"]'))) return;
    }
    if (e.target === root) { close(); return; }           // clique no fundo
    const act = e.target.closest ? e.target.closest("[data-act]") : null;
    if (act) {
      if (act.getAttribute("data-act") === "close") return close();
      if (act.getAttribute("data-act") === "confirm") return confirmPick();
    }
    const row = e.target.closest ? e.target.closest(".bank-row") : null;
    if (row) pick(Number(row.getAttribute("data-id")), row);
  }

  /* ─── Abrir / fechar ──────────────────────────────────────────────────── */

  /**
   * Abre o picker. SÍNCRONO até o modal estar na tela — nada de await antes,
   * como no fluxo original: o modal aparece na hora e só o preenchimento é
   * assíncrono. Pôr fetch na frente disso criava uma janela em que um segundo
   * clique disparava tudo de novo e o `lastFocus` era capturado depois do foco
   * já ter ido pro campo de busca.
   */
  function open(pre) {
    if (!cfg || ocupado) return;

    // Sem atalho de plano aqui. O original tinha um, mas lendo um valor que a
    // PÁGINA acabara de buscar; aqui a única fonte seria um cache do módulo, e
    // um cache que decide vira armadilha: gravado 0, ele redirecionaria pra
    // /precos antes de qualquer busca nova, e quem fizesse upgrade em outra aba
    // nunca sairia disso. Quem decide é o confirmar, com dado fresco. No
    // Settings o caso nem chega aqui — o applyOfConnectButton já roteia o botão
    // pra /precos quando o teto é 0.
    ensureRoot();
    lastFocus = document.activeElement;
    root.classList.add("open");
    abertoEm = performance.now();
    // Registrado aqui, antes dos returns/await abaixo, pra valer em TODOS os
    // caminhos: lista em cache, lista carregada do servidor e erro de fetch.
    onKeyRef = onKey;
    document.addEventListener("keydown", onKeyRef);

    const search = q("#bankpick-search");
    if (search) { search.value = ""; search.focus(); }
    const preOk = !!pre && typeof pre === "object" && Number.isInteger(pre.id) && typeof pre.name === "string" && !!pre.name;
    selected = preOk ? { id: pre.id, name: pre.name } : null;
    trazer = preOk;
    syncFoot();

    // Estado do plano em paralelo: quem espera é o confirmar.
    limitsPromise = refreshLimits();

    if (connectors) { desenha(); return; }
    carregarBancos();
  }

  /** Desenha a lista inteira. Com pré-escolha, descarta a que não está na lista e leva a escolhida à vista. */
  function desenha() {
    if (selected && !(connectors || []).some(function (b) { return b.id === selected.id; })) {
      selected = null;
      syncFoot();
    }
    renderList("");
    if (trazer) {
      trazer = false;
      const on = q("#bankpick-list .bank-row.active");
      if (on && on.scrollIntoView) on.scrollIntoView({ block: "center" });
    }
  }

  /**
   * Preenche a lista. Depois do await não recria nada: se o modal foi destruído
   * no meio, `q()` devolve null com o root ausente e tudo aqui sai cedo — é o
   * que dispensa qualquer controle de ciclo de vida.
   */
  async function carregarBancos() {
    const listEl = q("#bankpick-list");
    if (listEl) {
      listEl.innerHTML = "";
      listEl.appendChild(el("div", "bankpick-empty", "Carregando bancos…"));
    }
    try {
      const data = await get("/open-finance/" + cfg.userId + "/connectors");
      connectors = (data.connectors || []).filter(function (b) { return b && b.name; });
      desenha();
      // sem focar de novo: o foco já entrou no modal antes do await, e refocar
      // agora roubaria o foco de quem já tivesse tabulado pra dentro do card
    } catch (err) {
      if (foiAbortado(err)) return;   // destruído no meio: nada a mostrar
      const alvo = q("#bankpick-list");
      if (alvo) {
        alvo.innerHTML = "";
        alvo.appendChild(el("div", "bankpick-empty",
          "Não deu pra carregar a lista de bancos. Tente de novo."));
      }
    }
  }

  function close() {
    if (root) root.classList.remove("open");
    if (onKeyRef) { document.removeEventListener("keydown", onKeyRef); onKeyRef = null; }

    // devolve o foco pra quem abriu, senão o teclado volta pro topo da página
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    lastFocus = null;
  }

  /**
   * É AQUI que o estado do plano precisa estar fresco: o passo seguinte abre a
   * Pluggy, e é dali que sai o consentimento órfão se o teto já estiver cheio.
   * Por isso o await mora neste ponto, e não na abertura do modal.
   */
  async function confirmPick() {
    if (!selected) return;
    const escolhido = selected;          // pra detectar troca/cancelamento no await
    const go = q("#bankpick-go");
    // Desabilita durante a espera: sem isso um segundo clique passaria antes de
    // os limites chegarem. Na prática a busca começou lá no open() e já
    // resolveu, mas "na prática" não é garantia.
    if (go) go.disabled = true;
    try {
      // O retrato que ESTE confirmar esperou — não um objeto de módulo que uma
      // busca mais velha pudesse ter sobrescrito no caminho.
      const plano = await (limitsPromise || refreshLimits());

      // Mover a espera pra cá criou uma janela de cancelamento que o confirmar
      // original (síncrono) não tinha: Esc, clique no fundo ou o X fecham o
      // picker enquanto os limites ainda vêm, e sem esta checagem a Pluggy
      // abriria depois, sozinha, sobre uma tela que o usuário já dispensou.
      if (!root || !root.classList.contains("open") || selected !== escolhido) return;

      if (!plano) {
        conf("notify")("Não deu pra confirmar seu plano agora. Tente de novo em instantes.", "error");
        return;
      }
      // Carência de cobrança: já é assinante, a /precos o recusaria — vai pro cartão.
      if (plano.banksMax === 0) { window.location.href = plano.cobrancaEmAtraso ? "/conta" : "/precos"; return; }

      // Teto atingido: só segue se for RECONEXÃO de um banco já conectado (mesmo
      // nome). Banco novo abriria o widget da Pluggy só pra tomar 402 no
      // /pluggy-item — deixando item e consentimento órfãos. Bloqueia antes.
      if (plano.banksMax !== null && plano.banksMax > 0 && plano.count >= plano.banksMax) {
        // Por id do conector, não por nome: o nome gravado vem cru da Pluggy, e o
        // gêmeo Open Finance de um direto tem nome igual mas id diferente — o widget
        // abriria um item NOVO e o /pluggy-item daria 402 depois da autorização.
        const isReconnect = plano.ids.indexOf(String(selected.id)) !== -1;
        if (!isReconnect) {
          const n = plano.banksMax;
          conf("notify")("Seu plano conecta até " + n + " banco" + (n > 1 ? "s" : "") +
                         ". Faça upgrade pra conectar mais: /precos", "error");
          return;
        }
      }
      close();
      connect();
    } finally {
      if (go) go.disabled = false;
    }
  }

  /* ─── Widget da Pluggy ────────────────────────────────────────────────── */

  function pluggyFactory() {
    return window.PluggyConnect || window.pluggyConnect || window.PluggyConnectWidget;
  }

  /**
   * `uid` é o usuário de QUEM INICIOU o fluxo, fixado lá no connect(). Ler
   * `cfg.userId` aqui gravaria o item autorizado por A na conta de B se um
   * init() trocasse de usuário enquanto o widget estava aberto — e isolamento
   * por usuário é regra dura deste repositório.
   */
  async function savePluggyItem(itemData, uid, host, vivo) {
    const item = (itemData && itemData.item) || itemData || {};
    if (!item.id && !item.itemId) throw new Error("A Pluggy não retornou o item conectado.");
    // Só depois desta resposta a conexão existe do lado do PigBank — o
    // onSuccess da Pluggy diz apenas que o banco autorizou.
    const data = await post("/open-finance/" + uid + "/pluggy-item", { item: item });
    if (vivo()) conf("onConnected", host)(data);
    return data;
  }

  /** `itemId` = reconexão de um banco já conectado; sem ele, o fluxo de sempre. */
  async function openWidget(uid, host, itemId, sessao, vivo) {
    // Libera a guarda só se ainda for a desta conexão (não a de uma mais nova).
    const liberar = function () { if (ocupado === sessao) ocupado = null; };
    // O widget é de uma conexão VELHA quando há outra, mais nova, em curso: o onError/onClose dele já não são do host (o onSuccess segue valendo: o item autorizado é sempre salvo).
    const velha = function () { return !!ocupado && ocupado !== sessao; };
    const data = await post("/open-finance/" + uid + "/connect-token", itemId ? { item_id: itemId } : {});
    const accessToken = data.accessToken;
    if (!accessToken) throw new Error("A Pluggy não retornou accessToken.");

    const PluggyConnect = pluggyFactory();
    if (!PluggyConnect) {
      throw new Error("Widget da Pluggy não carregou. Recarregue a página e tente novamente.");
    }

    const connectorTypes = ["PERSONAL_BANK", "BUSINESS_BANK"];
    if (data.includeSandbox) connectorTypes.push("SANDBOX");

    let salvando = null;   // o save do /pluggy-item em voo (só a reconexão espera por ele)
    const opts = {
      connectToken: accessToken,
      includeSandbox: Boolean(data.includeSandbox),
      connectorTypes: connectorTypes,
      // Reconexão não escolhe banco: `selected` sobrevive ao close() do picker e vazaria.
      selectedConnectorId: itemId ? undefined : ((selected && selected.id) || undefined),
      // A mesma lista que foi no connect token (pluggy_products(), CLAUDE.md §0.7).
      products: data.products || undefined,
      language: "pt",
      theme: "dark",
      onSuccess: function (itemData) {
        if (!vivo()) return;
        salvando = (async function () {
          try {
            await savePluggyItem(itemData, uid, host, vivo);
            if (vivo()) conf("notify", host)("Banco conectado com sucesso!", "ok");
          } catch (err) {
            if (!vivo() || foiAbortado(err)) return;
            // Ex.: limite de bancos do plano (402 OF_BANK_LIMIT) — o banco foi
            // autorizado na Pluggy mas o plano não comporta mais conexões.
            conf("onError", host)(String((err && err.message) || err) || "Não foi possível salvar a conexão.",
              err && err.status);
          } finally {
            // Sucesso encerra a conexão mesmo se o SDK nunca mandar onClose.
            liberar();
          }
        })();
        return salvando;
      },
      onError: function (error) {
        console.error("Pluggy Connect error", error);
        if (!vivo() || velha()) return;
        // A Pluggy não garante onClose depois de onError: sem liberar aqui, o botão
        // do settings (que se reabilita no onError) ficaria mudo até recarregar.
        liberar();
        conf("onError", host)("Não foi possível concluir a conexão.");
      },
      onClose: function () {
        // Reconexão: o host relê a lista no onClose, então ele só vem depois do
        // save — senão relê antes de o /pluggy-item gravar. O picker segue como era.
        if (!vivo() || velha()) return;
        const fim = function () {
          if (!vivo()) return;
          liberar();
          conf("onClose", host)();   // sem argumento, como no picker
        };
        if (itemId) Promise.resolve(salvando).then(fim, fim);
        else fim();
      },
    };
    if (itemId) opts.updateItem = itemId;
    const widget = new PluggyConnect(opts);
    sessao.aberto = true;   // a partir daqui busy() vale: o widget existe e o onSuccess dele precisa ser salvo (antes, sair da página ainda aborta o /connect-token)

    let aberto;
    if (typeof widget.init === "function") aberto = widget.init();
    else if (typeof widget.open === "function") aberto = widget.open();
    else throw new Error("Widget da Pluggy carregou em um formato inesperado.");
    // SDK assíncrono: a rejeição não pode ficar solta nem deixar a guarda presa.
    if (aberto && typeof aberto.then === "function") {
      aberto.then(null, function (err) {
        console.error("Pluggy init falhou", err);
        if (!vivo()) return;
        liberar();
        conf("onError", host)("Não foi possível concluir a conexão.");
        // o SDK não manda onClose depois de um init que rejeita; o onError do host pode ter chamado destroy()
        if (vivo()) conf("onClose", host)();
      });
    }
    // Fora do try do connect: um onOpen do host que lança não é erro de conexão.
    if (vivo()) {
      try { conf("onOpen", host)(); } catch (e) { console.error("onOpen do host falhou", e); }
    }
  }

  async function connect(itemId) {
    // Fixa AQUI as duas pontas do fluxo — o usuário e o host — e carrega as
    // duas até o fim. O widget da Pluggy fica aberto por minutos: reler `cfg`
    // depois gravaria o item de quem iniciou na conta de quem estiver
    // configurado no fim, e pintaria as contas e transações de um na tela do
    // outro. Fixar só a URL resolvia metade.
    if (ocupado) return;   // uma conexão por vez (ver docstring)
    const host = cfg;
    const uid = host && host.userId;
    if (!uid) { conf("onError", host)("Sessão inválida. Faça login novamente"); return; }
    const sessao = ocupado = {};
    const g = geracao;
    const vivo = function () { return g === geracao; };
    // Isolado como o onOpen: um host que lança aqui não pode deixar a guarda presa.
    try { conf("onConnectStart", host)(); } catch (e) { console.error("onConnectStart do host falhou", e); }
    if (!vivo()) return;   // o onConnectStart do host chamou destroy(): nada abre
    try {
      await openWidget(uid, host, itemId, sessao, vivo);
    } catch (err) {
      if (!vivo() || foiAbortado(err)) return;   // destruído no meio: o widget nem chegou a abrir
      if (ocupado === sessao) ocupado = null;     // erro antes do widget: libera o botão
      conf("onError", host)((err && err.message) || "Erro ao abrir a conexão Pluggy", err && err.status);
    }
  }

  /* ─── API pública ─────────────────────────────────────────────────────── */

  function init(options) {
    // Idempotente: chamar de novo troca a configuração e NÃO soma listener —
    // os listeners vivem no root (um só, criado sob demanda) e no document
    // apenas enquanto o modal está aberto.
    cfg = options || {};
    return api;
  }

  /** Reconecta um banco já conectado, sem picker. Não passa por refreshLimits:
   *  o backend deixa reconexão passar pelo teto (test_b_p1_reconexao_passa_no_teto).
   *  Devolve `false` se há conexão em curso (ignorado, sem callback): o host não é dono dela. */
  function reconnect(itemId) {
    if (ocupado) return false;   // em curso: ignora sem callback, antes de validar (a regra vale para todo chamador)
    if (typeof itemId !== "string" || !itemId) {
      conf("onError")("Conexão inválida para reconectar.");
      return;
    }
    return connect(itemId);
  }

  function destroy() {
    // Cancela o que estiver em voo ANTES de desmontar. Sem isto, uma requisição
    // pendente volta depois com a tela já desfeita — e o caso que mais dói é o
    // /connect-token: a continuação abriria o widget da Pluggy sozinha, sobre
    // uma página que não existe mais.
    abortInflight();
    geracao++;
    ocupado = null;
    close();
    if (root && root.parentNode) root.parentNode.removeChild(root);
    root = null;
    selected = null;
    // `connectors` é cache de dado remoto que não muda entre telas; mantido de
    // propósito pra reabrir sem novo fetch da lista.
  }

  /** Há conexão em curso com o widget já criado (aberto ou salvando)? A fase do /connect-token ainda não conta: `destroy()` a aborta. Quem desmonta o host decide: `destroy()` agora descartaria o `onSuccess` de um item que a Pluggy já autorizou. */
  function busy() { return !!(ocupado && ocupado.aberto); }

  const api = { init: init, open: open, close: close, destroy: destroy, reconnect: reconnect, busy: busy };
  window.PBOpenFinance = api;
})();
