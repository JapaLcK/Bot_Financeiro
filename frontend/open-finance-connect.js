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
 *   PBOpenFinance.open();      // busca estado fresco, injeta o modal e abre
 *   PBOpenFinance.destroy();   // remove markup e listeners; idempotente
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

  var cfg = null;          // o que o host passou no init
  var root = null;         // nó do overlay, injetado sob demanda
  var onKeyRef = null;     // listener de teclado ativo (só enquanto aberto)
  var lastFocus = null;    // quem tinha o foco antes de abrir
  var connectors = null;   // cache da lista da Pluggy (não muda durante a sessão)
  var selected = null;     // {id, name}
  var connecting = false;  // uma conexão em voo por vez
  var opening = false;     // uma abertura em voo por vez (ver open())

  // Geração do ciclo de vida: init() e destroy() a incrementam. Toda operação
  // assíncrona guarda a sua no começo e desiste se ela ficou velha. Sem isso,
  // um destroy() durante um open() em voo (ex.: troca de rota client-side) não
  // impede nada: quando os fetches resolvem, o open() segue, RECRIA o modal que
  // acabou de ser destruído e reinstala o listener no document — e, se houve um
  // init() novo no meio, ainda usa limites velhos com os callbacks novos.
  var generation = 0;

  /** A operação que começou na geração `gen` ainda vale? */
  function stale(gen) { return gen !== generation; }

  // Limites do plano e conexões atuais. Recarregados a CADA open() — confiar no
  // que a página leu no load faz quem acabou de fazer upgrade em outra aba
  // continuar batendo no teto antigo.
  var limits = { banksMax: null, hasConnection: false, count: 0, names: [] };

  function noop() {}

  function conf(name, fallback) {
    return (cfg && typeof cfg[name] === "function") ? cfg[name] : (fallback || noop);
  }

  /* ─── Helpers de texto (vindos do settings.html sem mudança) ──────────── */

  function stripAccent(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function bankInitials(name) {
    var small = { de: 1, do: 1, da: 1, dos: 1, das: 1, e: 1, "-": 1 };
    var parts = (name || "").replace(/[^\wÀ-ÿ\s-]/g, "").split(/\s+/)
      .filter(function (w) { return w && !small[w.toLowerCase()]; });
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return (name || "?").replace(/[^A-Za-zÀ-ÿ]/g, "").slice(0, 2).toUpperCase();
  }

  function bankColor(hex) {
    var c = String(hex || "").replace("#", "");
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
    var data = null;
    try { data = await resp.json(); } catch (e) { /* corpo não-JSON */ }
    var detail = data && data.detail;
    var msg;
    if (typeof detail === "string" && detail) msg = detail;
    else if (detail && typeof detail === "object") msg = detail.message || detail.code;
    var err = new Error(msg || "Não foi possível concluir agora.");
    err.status = resp.status;
    err.detail = detail;
    return err;
  }

  async function get(path) {
    var resp = await fetch(url(path), { credentials: "same-origin", headers: headers() });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  async function post(path, body) {
    var resp = await fetch(url(path), {
      method: "POST",
      credentials: "same-origin",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body || {}),
    });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  /** Conexões que contam no teto — espelha o backend: PAUSED não conta. */
  function countsTowardLimit(conn) {
    return String((conn && conn.status) || "").toUpperCase() !== "PAUSED";
  }

  /**
   * Recarrega teto do plano e conexões. Devolve true só quando os DOIS vieram.
   *
   * Falha aqui NÃO pode ser silenciosa. Se o /auth/me ou o snapshot do Open
   * Finance cair, os valores permissivos do estado inicial (banksMax null,
   * count 0) fariam o modal abrir como se não houvesse teto — e um usuário no
   * limite autorizaria a instituição na Pluggy antes de o /pluggy-item recusar
   * com 402, deixando o consentimento órfão. É exatamente o que esta checagem
   * existe pra evitar; na dúvida, não abrir.
   */
  async function refreshLimits() {
    var me, of;
    try {
      var both = await Promise.all([
        get("/auth/me"),
        get("/open-finance/" + cfg.userId),
      ]);
      me = both[0];
      of = both[1];
    } catch (e) {
      return false;
    }

    limits.banksMax = (me && me.of_banks_max !== undefined) ? me.of_banks_max : null;
    var list = (of && of.connections) || [];
    var counted = list.filter(countsTowardLimit);
    limits.hasConnection = list.length > 0;
    limits.count = counted.length;
    limits.names = counted.map(function (c) { return stripAccent(c.institution_name || ""); });
    return true;
  }

  /* ─── Markup ──────────────────────────────────────────────────────────── */

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildRoot() {
    var overlay = el("div", "bankpick-overlay");
    overlay.id = "bankpick-overlay";

    var modal = el("div", "bankpick-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Conectar banco");

    var head = el("div", "bankpick-head");
    var headRow = el("div", "bankpick-head-row");
    var titles = el("div");
    titles.appendChild(el("div", "bankpick-title", "Conectar banco"));
    titles.appendChild(el("div", "bankpick-sub", "Busque entre as instituições disponíveis"));
    var x = el("button", "bankpick-x");
    x.type = "button";
    x.setAttribute("aria-label", "Fechar");
    x.setAttribute("data-act", "close");
    x.innerHTML = '<i class="ph ph-x" aria-hidden="true"></i>';
    headRow.appendChild(titles);
    headRow.appendChild(x);

    var searchWrap = el("div", "bankpick-search-wrap");
    var icon = el("span");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<i class="ph ph-magnifying-glass" aria-hidden="true"></i>';
    var search = el("input");
    search.id = "bankpick-search";
    search.type = "text";
    search.autocomplete = "off";
    search.placeholder = "Buscar banco (ex: Nubank, Itaú, C6…)";
    searchWrap.appendChild(icon);
    searchWrap.appendChild(search);

    head.appendChild(headRow);
    head.appendChild(searchWrap);

    var list = el("div", "bankpick-list");
    list.id = "bankpick-list";

    var foot = el("div", "bankpick-foot");
    var count = el("div", "bankpick-count", "Nenhum banco selecionado");
    count.id = "bankpick-count";
    var go = el("button", "btn-connect bankpick-go", "Conectar Open Finance");
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
    var listEl = q("#bankpick-list");
    if (!listEl) return;
    var f = stripAccent(filter || "").trim();
    var items = (connectors || []).filter(function (b) {
      return stripAccent(b.name).indexOf(f) !== -1;
    });

    listEl.innerHTML = "";
    if (!items.length) {
      listEl.appendChild(el("div", "bankpick-empty",
        'Nenhum banco encontrado para "' + (filter || "") + '".'));
      return;
    }

    var letter = "";
    items.forEach(function (b) {
      var L = (b.name[0] || "#").toUpperCase();
      if (L !== letter) {
        letter = L;
        listEl.appendChild(el("div", "bankpick-alpha", L));
      }
      var on = selected && selected.id === b.id;
      var row = el("button", "bank-row bankpick-row" + (on ? " active" : ""));
      row.type = "button";
      row.setAttribute("data-name", b.name);
      row.setAttribute("data-id", String(b.id));

      var logo = el("span", "bank-logo bankpick-logo", bankInitials(b.name));
      logo.style.color = bankColor(b.color);
      if (b.logo) {
        var img = document.createElement("img");
        img.src = b.logo;
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", function () { img.remove(); });
        logo.appendChild(img);
      }

      var info = el("span", "bank-info");
      info.appendChild(el("span", "bank-name", b.name));
      info.appendChild(el("span", "bank-meta",
        b.inv ? "Conta, cartão · caixinha/investimentos" : "Conta corrente e cartão"));

      var check = el("span", "bank-check");
      if (on) check.innerHTML = '<i class="ph ph-check" aria-hidden="true"></i>';

      row.appendChild(logo);
      row.appendChild(info);
      row.appendChild(check);
      listEl.appendChild(row);
    });
  }

  function pick(id, node) {
    selected = { id: id, name: node ? node.getAttribute("data-name") : "" };
    var rows = root ? root.querySelectorAll("#bankpick-list .bank-row") : [];
    Array.prototype.forEach.call(rows, function (r) {
      var on = r === node;
      r.classList.toggle("active", on);
      var chk = r.querySelector(".bank-check");
      if (chk) chk.innerHTML = on ? '<i class="ph ph-check" aria-hidden="true"></i>' : "";
    });
    syncFoot();
  }

  function syncFoot() {
    var count = q("#bankpick-count");
    var go = q("#bankpick-go");
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
    var modal = q(".bankpick-modal");
    if (!modal) return [];
    var sel = 'a[href], button:not([disabled]), input:not([disabled]),'
            + ' select:not([disabled]), textarea:not([disabled]),'
            + ' [tabindex]:not([tabindex="-1"])';
    return Array.prototype.filter.call(modal.querySelectorAll(sel), function (e) {
      return e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0;
    });
  }

  function onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab") return;

    var modal = q(".bankpick-modal");
    var alvos = focusables();
    if (!modal || !alvos.length) return;

    var primeiro = alvos[0];
    var ultimo = alvos[alvos.length - 1];
    var proximo = e.shiftKey ? ultimo : primeiro;
    var borda = e.shiftKey ? primeiro : ultimo;

    // `foco fora` cobre o caso de já ter escapado (clique no fundo, foco no body)
    if (!modal.contains(document.activeElement) || document.activeElement === borda) {
      e.preventDefault();
      proximo.focus();
    }
  }

  /* ─── Cliques (delegados) ─────────────────────────────────────────────── */

  function onClick(e) {
    if (e.target === root) { close(); return; }          // clique no fundo
    var act = e.target.closest ? e.target.closest("[data-act]") : null;
    if (act) {
      if (act.getAttribute("data-act") === "close") return close();
      if (act.getAttribute("data-act") === "confirm") return confirmPick();
    }
    var row = e.target.closest ? e.target.closest(".bank-row") : null;
    if (row) pick(Number(row.getAttribute("data-id")), row);
  }

  /* ─── Abrir / fechar ──────────────────────────────────────────────────── */

  async function open() {
    if (!cfg) return;

    // Uma abertura por vez. Sem isto, um segundo clique enquanto o
    // refreshLimits() da primeira ainda está no ar dispara os fetches de novo E
    // grava o campo de busca (já focado pela primeira) como `lastFocus` — aí
    // fechar devolveria o foco pra um input escondido em vez do botão que
    // abriu, quebrando o fluxo de teclado.
    if (opening || (root && root.classList.contains("open"))) return;
    opening = true;

    // Quem abriu, guardado ANTES de qualquer await, pelo mesmo motivo.
    var opener = document.activeElement;
    var myGen = generation;

    try {
      // Estado fresco ANTES de decidir qualquer coisa sobre plano.
      var ok = await refreshLimits();
      if (stale(myGen)) return;          // destroy()/init() no meio: desiste
      if (!ok) {
        conf("onError")("Não deu pra confirmar seu plano agora. Tente de novo em instantes.");
        return;
      }

      // Sem Open Finance no plano: quem decide o que fazer é o host. O settings
      // manda pra /precos (comportamento de hoje); o wizard mostra upgrade sem
      // navegar, senão abortaria o onboarding no meio.
      if (limits.banksMax === 0) {
        conf("onUpgradeNeeded")(limits);
        return;
      }
    } finally {
      opening = false;
    }

    ensureRoot();
    lastFocus = opener;
    root.classList.add("open");
    // Registrado aqui, antes dos returns/await abaixo, pra valer em TODOS os
    // caminhos: lista em cache, lista carregada do servidor e erro de fetch.
    onKeyRef = onKey;
    document.addEventListener("keydown", onKeyRef);

    var search = q("#bankpick-search");
    if (search) { search.value = ""; search.focus(); }
    selected = null;
    syncFoot();

    if (connectors) { renderList(""); return; }

    var listEl = q("#bankpick-list");
    if (listEl) {
      listEl.innerHTML = "";
      listEl.appendChild(el("div", "bankpick-empty", "Carregando bancos…"));
    }
    try {
      var data = await get("/open-finance/" + cfg.userId + "/connectors");
      if (stale(myGen)) return;          // destruído enquanto a lista carregava
      connectors = (data.connectors || []).filter(function (b) { return b && b.name; });
      renderList("");
      // sem focar de novo: o foco já entrou no modal antes do await, e refocar
      // agora roubaria o foco de quem já tivesse tabulado pra dentro do card
    } catch (err) {
      if (stale(myGen)) return;
      if (listEl) {
        listEl.innerHTML = "";
        listEl.appendChild(el("div", "bankpick-empty",
          "Não deu pra carregar a lista de bancos. Tente de novo."));
      }
    }
  }

  function close() {
    if (root) root.classList.remove("open");
    if (onKeyRef) { document.removeEventListener("keydown", onKeyRef); onKeyRef = null; }

    // Tira o foco de DENTRO do modal antes de qualquer coisa. Sem isto, quando
    // não há um alvo focável pra devolver (o `body` não é focável e o .focus()
    // nele é ignorado), o foco ficava preso num subárvore display:none — pior
    // que cair no topo da página, porque o teclado some sem sinal nenhum.
    if (root && document.activeElement && root.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    // Devolve o foco pra quem abriu, senão o teclado volta pro topo da página.
    var alvo = (lastFocus && lastFocus !== document.body && document.contains(lastFocus))
      ? lastFocus : null;
    if (alvo) alvo.focus();
    lastFocus = null;
  }

  function confirmPick() {
    if (!selected) return;
    // Teto atingido: só segue se for RECONEXÃO de um banco já conectado (mesmo
    // nome). Banco novo abriria o widget da Pluggy só pra tomar 402 no
    // /pluggy-item — deixando item e consentimento órfãos. Bloqueia antes.
    if (limits.banksMax !== null && limits.banksMax > 0 && limits.count >= limits.banksMax) {
      var isReconnect = limits.names.indexOf(stripAccent(selected.name || "")) !== -1;
      if (!isReconnect) {
        var n = limits.banksMax;
        conf("notify")("Seu plano conecta até " + n + " banco" + (n > 1 ? "s" : "") +
                       ". Faça upgrade pra conectar mais: /precos", "error");
        return;
      }
    }
    close();
    connect();
  }

  /* ─── Widget da Pluggy ────────────────────────────────────────────────── */

  function pluggyFactory() {
    return window.PluggyConnect || window.pluggyConnect || window.PluggyConnectWidget;
  }

  async function savePluggyItem(itemData, gen) {
    var item = (itemData && itemData.item) || itemData || {};
    if (!item.id && !item.itemId) throw new Error("A Pluggy não retornou o item conectado.");
    // Só depois desta resposta a conexão existe do lado do PigBank — o
    // onSuccess da Pluggy diz apenas que o banco autorizou.
    var data = await post("/open-finance/" + cfg.userId + "/pluggy-item", { item: item });
    if (gen !== undefined && stale(gen)) return data;   // host já foi embora
    conf("onConnected")(data);
    return data;
  }

  async function openWidget() {
    // Mesma geração do open(): os callbacks abaixo disparam MUITO depois (o
    // usuário passa pelo fluxo do banco), e um destroy()/init() no meio não
    // pode deixá-los avisar callbacks de um ciclo que já acabou.
    var myGen = generation;

    var data = await post("/open-finance/" + cfg.userId + "/connect-token", {});
    if (stale(myGen)) return;
    var accessToken = data.accessToken;
    if (!accessToken) throw new Error("A Pluggy não retornou accessToken.");

    var PluggyConnect = pluggyFactory();
    if (!PluggyConnect) {
      throw new Error("Widget da Pluggy não carregou. Recarregue a página e tente novamente.");
    }

    var connectorTypes = ["PERSONAL_BANK", "BUSINESS_BANK"];
    if (data.includeSandbox) connectorTypes.push("SANDBOX");

    var widget = new PluggyConnect({
      connectToken: accessToken,
      includeSandbox: Boolean(data.includeSandbox),
      connectorTypes: connectorTypes,
      selectedConnectorId: (selected && selected.id) || undefined,
      // INVESTMENTS incluído: o sync lê /investments pra achar a Caixinha
      // (FIXED_INCOME/CDB). Precisa casar com PLUGGY_PRODUCTS do connect-token,
      // senão o produto não é coletado.
      products: ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS", "INVESTMENTS"],
      language: "pt",
      theme: "dark",
      onSuccess: async function (itemData) {
        try {
          // A persistência acontece de qualquer jeito — o banco já autorizou, e
          // largar o item sem salvar é pior que avisar um host que saiu. O que
          // a geração corta é só o retorno pra UI que não existe mais.
          await savePluggyItem(itemData, myGen);
          if (stale(myGen)) return;
          conf("notify")("Banco conectado com sucesso!", "ok");
        } catch (err) {
          if (stale(myGen)) return;
          // Ex.: limite de bancos do plano (402 OF_BANK_LIMIT) — o banco foi
          // autorizado na Pluggy mas o plano não comporta mais conexões.
          conf("onError")(String((err && err.message) || err) || "Não foi possível salvar a conexão.");
        } finally {
          connecting = false;
        }
      },
      onError: function (error) {
        console.error("Pluggy Connect error", error);
        connecting = false;
        if (stale(myGen)) return;
        conf("onError")("Não foi possível concluir a conexão.");
      },
      onClose: function () {
        connecting = false;
        if (stale(myGen)) return;
        conf("onClose")();
      },
    });

    if (typeof widget.init === "function") widget.init();
    else if (typeof widget.open === "function") widget.open();
    else throw new Error("Widget da Pluggy carregou em um formato inesperado.");
  }

  async function connect() {
    if (!cfg || !cfg.userId) { conf("onError")("Sessão inválida. Faça login novamente"); return; }
    if (connecting) return;                      // uma conexão em voo por vez
    connecting = true;
    conf("onConnectStart")();
    try {
      await openWidget();
    } catch (err) {
      connecting = false;
      conf("onError")((err && err.message) || "Erro ao abrir a conexão Pluggy");
    }
  }

  /* ─── API pública ─────────────────────────────────────────────────────── */

  function init(options) {
    // Idempotente: chamar de novo troca a configuração e NÃO soma listener —
    // os listeners vivem no root (um só, criado sob demanda) e no document
    // apenas enquanto o modal está aberto.
    //
    // Incrementa a geração: qualquer operação da configuração ANTERIOR que
    // ainda esteja em voo desiste em vez de avisar os callbacks novos com
    // resultado velho.
    generation += 1;
    cfg = options || {};
    return api;
  }

  function destroy() {
    // Antes de tudo: invalida o que estiver em voo. Sem isto, um open() que
    // ainda espera o refreshLimits() voltaria depois, recriaria o root que
    // acabamos de remover e reinstalaria o listener no document — um modal
    // fantasma numa tela que já saiu.
    generation += 1;
    close();
    if (root && root.parentNode) root.parentNode.removeChild(root);
    root = null;
    selected = null;
    connecting = false;
    opening = false;
    // `connectors` é cache de dado remoto que não muda entre telas; mantido de
    // propósito pra reabrir sem novo fetch da lista.
  }

  var api = { init: init, open: open, close: close, destroy: destroy };
  window.PBOpenFinance = api;
})();
