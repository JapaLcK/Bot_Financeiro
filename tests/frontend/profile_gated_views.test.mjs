/** Perfil tardio e mudanças de gates exercitam loaders e render reais. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);
const html = readFileSync(new URL("../../frontend/dashboard.html", import.meta.url), "utf8");

async function viewPage(view) {
  const page = await loadDashboardJs();
  page.setDefaultTimeout(2500);
  await page.evaluate(({ html, view }) => {
    USER_ID = 1;
    const doc = new DOMParser().parseFromString(html, "text/html");
    document.body.append(doc.getElementById("analytics-view"), doc.getElementById("fixed-view"));
    document.body.insertAdjacentHTML("beforeend", '<div id="user-label"></div><div id="user-email"></div><div id="user-plan"></div>');
    document.getElementById(view + "-view").classList.add("active");
    _recurringTab = "bills";
    window.calls = [];
    window.gates = tier => ({ financial_comparison: tier !== "essencial", insights: tier !== "essencial", forecast: tier !== "essencial", cashflow: tier === "pro" });
    window.payload = () => ({
      kpis: { total_income: 600, total_expense: 300, delta_pct: featureAllowed("financial_comparison") ? { income: 25, expense: 10 } : null },
      categories: [{ name: "Mercado", total: 300 }], bills: [],
      patterns: [], insights: [], evolution: [], weekdays: [], merchants: [],
      forecast: { horizons: Object.fromEntries((featureAllowed("cashflow") ? [30, 60, 90] : [30]).map(n => [n, { projetado: 123, tranquilo: true }])) },
    });
    window.fetch = async url => {
      calls.push(url);
      if (url.includes("dashboard-profile")) return new Promise(resolve => {
        window.releaseProfile = tier => resolve({ ok: true, json: async () => ({ plan: tier, feature_gates: gates(tier) }) });
      });
      return { ok: true, json: async () => payload() };
    };
  }, { html, view });
  return page;
}

for (const tier of ["essencial", "plus", "pro"]) {
  test(`perfil tardio ${tier} reidrata Analytics ativa sem reabrir`, async () => {
    const page = await viewPage("analytics");
    await page.evaluate(async () => { window.profile = loadUserMenuState(); await loadAnalyticsView(); });
    assert.equal(await page.evaluate(() => calls.filter(u => /evolution|weekday|patterns|insights/.test(u)).length), 0);
    await page.evaluate(async tier => { releaseProfile(tier); await profile; }, tier);
    if (tier !== "essencial") await page.waitForFunction(() => _analyticsCache?.kpis?.delta_pct?.income === 25);
    assert.equal(await page.evaluate(() => calls.filter(u => /evolution|weekday|patterns|insights/.test(u)).length), tier === "essencial" ? 0 : 4);
    assert.equal(await page.evaluate(() => _analyticsCurrentMonths), 6);
    assert.match(await page.locator("#analytics-stats").textContent(), /Receita média/);
    await page.close();
  });

  test(`perfil tardio ${tier} atualiza só previsão da aba Boletos`, async () => {
    const page = await viewPage("fixed");
    await page.evaluate(async () => { window.profile = loadUserMenuState(); await loadBillsView(); });
    assert.match(await page.locator("#forecast-result").textContent(), /Previsão de saldo/);
    await page.evaluate(async tier => { releaseProfile(tier); await profile; }, tier);
    if (tier !== "essencial") await page.waitForFunction(() => document.getElementById("forecast-result").textContent.includes("Em 30 dias"));
    assert.equal(await page.evaluate(() => calls.filter(u => u.includes("/forecast/")).length), tier === "essencial" ? 0 : 1);
    assert.equal(await page.evaluate(() => calls.filter(u => u.includes("/recurring-bills/")).length), 1);
    assert.equal((await page.locator("#forecast-result").textContent()).includes("Em 90 dias"), tier === "pro");
    await page.close();
  });
}

test("perfil igual e gates de outra área não recarregam dados; filtro é preservado", async () => {
  const page = await viewPage("analytics");
  await page.evaluate(async () => { USER_GATES = gates("plus"); await loadAnalyticsView(true, 3); calls.length = 0; });
  await page.evaluate(() => {
    applyUserMenuState("a@test", "plus", "A", { ...gates("plus"), export: true });
    applyUserMenuState("a@test", "plus", "A");
  });
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.evaluate(() => applyUserMenuState("a@test", "essencial", "A", gates("essencial")));
  await page.waitForFunction(() => _analyticsCache?.kpis && _analyticsCache.kpis.delta_pct === null);
  assert.ok((await page.evaluate(() => calls)).every(u => u.includes("months=3")));
  assert.doesNotMatch(await page.locator("#analytics-stats").textContent(), /vs período anterior/);
  await page.close();
});

for (const active of [true, false]) {
  test(`Analytics descarta JSON antigo após downgrade, view ativa=${active}`, async () => {
    const page = await viewPage("analytics");
    await page.evaluate(async active => {
      USER_GATES = gates("pro");
      await loadAnalyticsView();
      const realFetch = window.fetch;
      window.fetch = async url => {
        if (url.includes("/kpis")) {
          window.fetch = realFetch;
          return { ok: true, json: () => new Promise(resolve => { window.releaseOld = () => resolve({ kpis: { total_income: 999, delta_pct: { income: 99 } } }); }) };
        }
        return realFetch(url);
      };
      window.oldRequest = loadAnalyticsView(true);
      document.getElementById("analytics-view").classList.toggle("active", active);
    }, active);
    await page.waitForFunction(() => typeof releaseOld === "function");
    await page.evaluate(async () => {
      calls.length = 0;
      applyUserMenuState("a@test", "essencial", "A", gates("essencial"));
      releaseOld();
      await oldRequest;
    });
    if (active) await page.waitForFunction(() => _analyticsCache?.kpis?.total_income === 600);
    else assert.equal(await page.evaluate(() => _analyticsCache), null);
    assert.equal(await page.evaluate(() => calls.length), active ? 3 : 0);
    assert.doesNotMatch(await page.locator("#analytics-stats").textContent(), /vs período anterior/);
    await page.close();
  });
}

for (const outcome of ["json", "error"]) {
  test(`previsão Pro antiga (${outcome}) não sobrescreve Plus depois da troca de plano`, async () => {
    const page = await viewPage("fixed");
    await page.evaluate(outcome => {
      USER_GATES = gates("pro");
      const realFetch = window.fetch;
      window.fetch = async () => {
        window.fetch = realFetch;
        const stale = payload();
        if (outcome === "error") return new Promise((_, reject) => { window.releaseOld = () => reject(new Error("antigo")); });
        return { ok: true, json: () => new Promise(resolve => { window.releaseOld = () => resolve(stale); }) };
      };
      window.oldRequest = loadForecast();
    }, outcome);
    await page.waitForFunction(() => typeof releaseOld === "function");
    await page.evaluate(() => applyUserMenuState("a@test", "plus", "A", gates("plus")));
    await page.waitForFunction(() => document.getElementById("forecast-result").textContent.includes("Em 30 dias"));
    await page.evaluate(async () => { releaseOld(); await oldRequest; });
    const result = await page.locator("#forecast-result").textContent();
    assert.match(result, /Em 30 dias/);
    assert.doesNotMatch(result, /Em (60|90) dias|Não consegui/);
    await page.close();
  });
}

test("downgrade com Boletos inativo cancela previsão sem buscar nem repintar", async () => {
  const page = await viewPage("fixed");
  await page.evaluate(() => {
    USER_GATES = gates("pro");
    window.fetch = async () => ({ ok: true, json: () => new Promise(resolve => { window.releaseOld = () => resolve(payload()); }) });
    window.oldRequest = loadForecast();
  });
  await page.waitForFunction(() => typeof releaseOld === "function");
  await page.evaluate(async () => {
    document.getElementById("fixed-view").classList.remove("active");
    applyUserMenuState("a@test", "essencial", "A", gates("essencial"));
    releaseOld();
    await oldRequest;
  });
  assert.doesNotMatch(await page.locator("#forecast-result").textContent(), /Em 30 dias|Calculando/);
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.close();
});

for (const mode of ["fresh", "cache", "background"]) {
  for (const outcome of ["data", "error"]) {
    test(`Analytics ${mode}: ${outcome} já entregue pelo canal respeita novo perfil`, async () => {
      const page = await viewPage("analytics");
      const result = await page.evaluate(async ({ mode, outcome }) => {
        USER_GATES = gates("pro");
        await loadAnalyticsView();
        document.getElementById("analytics-view").classList.remove("active");
        const run = _analyticsChannel.run.bind(_analyticsChannel);
        // O canal já decidiu entregar; o perfil chega antes do commit no loader.
        const changeProfile = () => queueMicrotask(() => applyUserMenuState("a@test", "essencial", "A", gates("essencial")));
        _analyticsChannel.run = (...args) => run(...args).then(data => {
          changeProfile();
          return data;
        }, err => { changeProfile(); throw err; });
        if (outcome === "error") window.fetch = async () => ({ ok: false, status: 401 });
        let rejected = false;
        try { await loadAnalyticsView(mode !== "cache", null, { background: mode === "background" }); }
        catch { rejected = true; }
        // SWR retorna antes da resposta; aguarda sua fila de microtasks também.
        await new Promise(resolve => setTimeout(resolve, 0));
        return { rejected, cache: _analyticsCache, text: document.getElementById("analytics-stats").textContent };
      }, { mode, outcome });
      assert.equal(result.rejected, false);
      assert.equal(result.cache, null);
      assert.doesNotMatch(result.text, /vs período anterior|sessão expirou/i);
      await page.close();
    });
  }
}

for (const outcome of ["data", "error", "forbidden"]) {
  test(`projeção de prazo ${outcome}: downgrade preserva formulário e descarta resultado antigo`, async () => {
    const page = await viewPage("fixed");
    await page.evaluate(outcome => {
      USER_GATES = gates("pro");
      document.getElementById("fixed-view").classList.remove("active");
      document.getElementById("boleto-sim-date").value = "2026-10-20";
      document.getElementById("boleto-sim-amount").value = "123";
      document.getElementById("boleto-sim-result").textContent = "Resultado Pro anterior";
      window.fetch = async () => {
        if (outcome === "error") return new Promise((_, reject) => { window.releaseOld = () => reject(new Error("antigo")); });
        return { ok: outcome !== "forbidden", status: outcome === "forbidden" ? 403 : 200, json: () => new Promise(resolve => {
          window.releaseOld = () => resolve({ detail: { message: "Pro antigo" }, projection: { target: "2026-10-20", projetado: 123, tranquilo: true } });
        }) };
      };
      window.oldRequest = simularPrazo();
    }, outcome);
    await page.waitForFunction(() => typeof releaseOld === "function");
    await page.evaluate(async () => {
      applyUserMenuState("a@test", "plus", "A", gates("plus"));
      releaseOld();
      await oldRequest;
    });
    assert.equal(await page.locator("#boleto-sim-result").textContent(), "");
    assert.equal(await page.locator("#boleto-sim-date").inputValue(), "2026-10-20");
    assert.equal(await page.locator("#boleto-sim-amount").inputValue(), "123");
    await page.close();
  });
}

test("projeção permitida continua renderizando; erro de horizonte atual permanece visível", async () => {
  const page = await viewPage("fixed");
  await page.evaluate(async () => {
    USER_GATES = gates("plus");
    document.getElementById("boleto-sim-date").value = "2026-10-20";
    window.fetch = async () => ({ ok: true, json: async () => ({ projection: { target: "2026-10-20", projetado: 123, tranquilo: true, saldo_atual: 123, receitas_previstas: 0, gastos_fixos_previstos: 0, boletos_ate: 0, n_boletos: 0, faturas_cartao: 0, boleto_novo: 0, estado: "a_conferir", motivos: [{ codigo: "gastos_variaveis_nao_estimados" }], cabe_nas_premissas: false } }) });
    await simularPrazo();
  });
  assert.match(await page.locator("#boleto-sim-result").textContent(), /Saldo condicional até 20 de outubro: R\$ 123,00 · a conferir/);
  await page.evaluate(async () => {
    window.fetch = async () => ({ ok: false, status: 403, json: async () => ({ detail: { message: "Limite de 30 dias" } }) });
    await simularPrazo();
  });
  assert.equal(await page.locator("#boleto-sim-result").textContent(), "Limite de 30 dias");
  await page.close();
});


const projectionBase = {
  target: "2026-10-20", saldo_atual: 200, receitas_previstas: 100,
  gastos_fixos_previstos: 40, boletos_ate: 77, n_boletos: 1,
  faturas_cartao: 10, boleto_novo: 50, projetado: 123, tranquilo: true,
  estado: "a_conferir", motivos: [{ codigo: "gastos_variaveis_nao_estimados" }],
  cabe_nas_premissas: false, balance_source: "manual", banks_excluded: false,
};
const projectionCases = [
  ["positivo a conferir", {}, "R$ 123,00"],
  ["negativo a conferir", { saldo_atual: -46, projetado: -123, tranquilo: false }, "R$ -123,00"],
  ["zero disponível", { saldo_atual: 77, projetado: 0 }, "R$ 0,00"],
  ["base indisponível", { saldo_atual: null, projetado: null, receitas_previstas: null, gastos_fixos_previstos: null, estado: "indisponivel", tranquilo: false, balance_source: "unavailable" }, "Indisponível"],
  ["condicional sem motivos", { estado: "condicional", motivos: [] }, "R$ 123,00"],
  ["final positivo com percurso negativo", { minimo_percurso: -100 }, "R$ 123,00"],
];

for (const [name, changes, value] of projectionCases) {
  test(`projeção condicional pelo loader: ${name}`, async () => {
    const page = await viewPage("fixed");
    const projection = { ...projectionBase, ...changes };
    await page.evaluate(() => { document.getElementById("recurring-bills-pane").style.display = "block"; });
    await page.locator("#boleto-sim-amount").focus();
    const rendered = await page.evaluate(async projection => {
      USER_GATES = gates("plus");
      document.getElementById("boleto-sim-date").value = projection.target;
      document.getElementById("boleto-sim-amount").value = "50";
      window.fetch = async url => { calls.push(url); return { ok: true, json: async () => ({ projection }) }; };
      await simularPrazo();
      const box = document.getElementById("boleto-sim-result").firstElementChild;
      return {
        text: box.textContent, title: box.firstElementChild.textContent,
        background: box.style.background, border: box.style.border,
        titleColor: box.firstElementChild.style.color,
        rows: [...box.children].filter(row => row.children.length === 2 && row.children[0].tagName === "SPAN")
          .map(row => ({ label: row.children[0].textContent, value: row.children[1].textContent, color: row.children[1].style.color })),
        focus: document.activeElement.id, calls: [...calls],
      };
    }, projection);
    assert.match(rendered.title, /20 de outubro/);
    assert.ok(rendered.title.includes(value), rendered.title);
    assert.equal(rendered.background, "var(--bg-elev-2)");
    assert.equal(rendered.border, "1px solid var(--border)");
    assert.equal(rendered.titleColor, "var(--text)");
    assert.doesNotMatch(rendered.text, /Tranquilo|sobra|Aperta|falta R\$|gastos_variaveis_nao_estimados|minimo_percurso/);
    assert.match(rendered.text, /faturas.*Não inclui estimativa variável nem autoriza uma compra/s);
    const rows = Object.fromEntries(rendered.rows.map(row => [row.label, row]));
    assert.equal(rows["Projeção do caixa"].value, value);
    assert.equal(rows["Projeção do caixa"].color, "var(--text)");
    assert.equal(rows["Boletos até lá (1)"].value, "− R$ 77,00");
    assert.equal(rows["Faturas de cartão até lá"].value, "− R$ 10,00");
    assert.equal(rows["Boleto novo em análise"].value, "− R$ 50,00");
    if (projection.estado === "indisponivel") {
      for (const label of ["Saldo hoje", "Receitas previstas", "Gastos fixos"]) assert.equal(rows[label].value, "Indisponível");
      assert.doesNotMatch(rendered.text, /R\$ 0,00|NaN|Infinity/);
      // A ausência e valores não finitos também são desconhecidos, sem zerar somas conhecidas.
      for (const unknown of [undefined, NaN, Infinity]) {
        const result = await page.evaluate(async ({ projection, unknown }) => {
          window.fetch = async () => ({ ok: true, json: async () => ({ projection: {
            ...projection, estado: "a_conferir", projetado: unknown, saldo_atual: unknown,
            receitas_previstas: unknown, gastos_fixos_previstos: unknown,
          } }) });
          await simularPrazo();
          return document.getElementById("boleto-sim-result").textContent;
        }, { projection, unknown });
        assert.match(result, /Indisponível/);
        assert.doesNotMatch(result, /R\$ 0,00|NaN|Infinity/);
        assert.match(result, /− R\$ 77,00/);
      }
    } else {
      assert.match(rendered.title, /Saldo condicional/);
      assert.equal(rendered.title.includes("a conferir"), projection.estado === "a_conferir");
      assert.equal(rows["Saldo hoje"].value, projection.saldo_atual < 0 ? "− R$ 46,00" : `+ R$ ${projection.saldo_atual},00`);
      assert.equal(rows["Receitas previstas"].value, "+ R$ 100,00");
      assert.equal(rows["Gastos fixos"].value, "− R$ 40,00");
      if (projection.minimo_percurso < 0) {
        assert.match(rendered.text, /saldo positivo no alvo não garante caixa positivo no percurso/);
        assert.doesNotMatch(rendered.text, /100,00.*percurso|mínimo/i);
      }
    }
    assert.equal(rendered.focus, "boleto-sim-amount");
    assert.ok(rendered.calls.some(url => url.endsWith("projection?date=2026-10-20&amount=50")));
    assert.equal(await page.locator("#boleto-sim-date").inputValue(), "2026-10-20");
    assert.equal(await page.locator("#boleto-sim-amount").inputValue(), "50");
    assert.deepEqual(page.__errs, []);
    await page.close();
  });
}
