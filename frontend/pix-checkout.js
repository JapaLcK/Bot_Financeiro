/**
 * Pix anual na /precos — o CTA nos cards, o overlay e o checkout.
 *
 * O QR, a cópia e o poll moram no pix-poll.js (par deste arquivo; os dois
 * dividem o escopo global e a divisão é do teto de 350 linhas do
 * `quality/max-lines`).
 *
 * Script CLÁSSICO (sem módulo ES) de propósito: a precos.html chama
 * `pbPixInit`/`pbPixRefresh` do escopo global e este arquivo lê de lá o que ela já
 * tem (`currentCycle`, `PLAN_NAMES`, `fmtBrDate`, `getCsrfToken`, `showToast`) —
 * nenhum fetch novo, nenhuma segunda tabela de preços.
 *
 * Duas regras não negociáveis, as duas do docs/plano_pix_anual_asaas.md:
 *  1. §13.6 — o `qr_payload` é INSTRUMENTO AO PORTADOR: só memória e DOM (nada de
 *     localStorage, sessionStorage, cookie, history.state, query string ou log), e
 *     sai do DOM ao pagar, expirar ou fechar. Quem viaja na URL é o `public_token`,
 *     que vira o `sid` do pixel da Meta e do GA4 na home.html — nunca o id do
 *     pagamento no provedor.
 *  2. Sem `pix_annual_available` no /billing/plans-config, NENHUM botão nasce: a
 *     página é a de hoje. É o que deixa este PR ir para a main antes do backend.
 *  3. O CPF/CNPJ do pagador — que o Asaas exige e que NÓS não persistimos — segue
 *     a MESMA disciplina do item 1: só memória e DOM, e some dos dois ao fechar
 *     o modal, ao aparecer o QR e ao terminar a compra. Nunca em storage, nunca
 *     na URL, nunca em `console`, nunca no GA4 nem na CAPI.
 *
 * 401 não se trata na mão além do redirect do checkout: o auth-refresh.js renova e
 * refaz a requisição sozinho, e o que chega aqui como 401 é o que SOBROU dele.
 */
"use strict";

// Pix é só anual (§7 do plano). O Premium não tem preço, então fica de fora.
const PIX_PLANOS = ["essencial", "plus", "pro"];

let pixCfg = null;
let pixSub = null;

// Fronteira de confiança de valor monetário: sem `amount_cents` o
// `toLocaleString` escrevia "R$ NaN" no título do modal de pagamento. Devolve
// string vazia, e quem chama decide o que mostrar sem o número.
const pixBrl = (c) => (Number.isFinite(Number(c))
  ? (Number(c) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })
  : "");

/**
 * Ícone + texto de uma vez. Por `createElement` e não por markup em string: o
 * handlers_inline.test.mjs levanta handler de dentro das strings dos `.js`, e sem
 * markup gerado a `handlers_inline.baseline.json` não muda.
 * Só ícones do subset de frontend/phosphor.css — o de QR code, por exemplo, não
 * está lá. E o nome não se escreve nem em COMENTÁRIO: o extrator de
 * scripts/build_phosphor_subset.py é `\bph-([a-z0-9-]+)` sobre o texto do
 * arquivo, então citá-lo em prosa já o torna "usado" e deixa o
 * test_phosphor_subset.py vermelho (medido).
 */
function pixRotular(el, icone, texto) {
  const i = document.createElement("i");
  i.className = "ph " + icone;
  i.setAttribute("aria-hidden", "true");
  el.replaceChildren(i, document.createTextNode(" " + texto));
}

function pixLinha(texto) {
  const p = document.createElement("p");
  p.className = "pix-line";
  p.textContent = texto;
  return p;
}

function pixBotao(classe, texto) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-block " + classe;
  b.textContent = texto;
  return b;
}

// ── O CTA nos cards ─────────────────────────────────────────────────────────

/** Chamado pelo loadPlansState da precos.html, com o que ela já buscou. */
function pbPixInit(cfg, sub) {
  pixCfg = cfg || null;
  pixSub = sub || null;
  pbPixRefresh();
}

/** Chamado pelo setCycle: o CTA de Pix só existe no ciclo anual. */
function pbPixRefresh() {
  // `!== true` e não `!`: portão de venda não abre com valor truthy qualquer.
  if (!pixCfg || pixCfg.pix_annual_available !== true) return;
  // Vitalício NÃO compra: o `refreshPlanButtons` da precos.html já marca os
  // cards como acesso permanente, e o backend recusa o checkout com 409
  // `lifetime` — um CTA aqui é um clique rumo ao erro, com CPF digitado antes.
  // Mesmo caminho do mensal: sai do DOM (escondido ainda recebe Tab), o que
  // também apaga o CTA já criado quando a assinatura chega depois do cfg.
  const anual = currentCycle === "annual" && !(pixSub && pixSub.lifetime === true);
  for (const plano of PIX_PLANOS) {
    const existente = document.querySelector('[data-pix-cta="' + plano + '"]');
    // Sai do DOM no mensal em vez de ficar escondido: escondido ainda recebe Tab.
    if (!anual) { if (existente) existente.remove(); continue; }
    const b = existente || pixCriarCta(plano);
    if (!b) continue;
    const renova = !!pixSub && pixSub.gateway === "pix" && pixSub.plan === plano;
    pixRotular(b, "ph-lightning", (renova ? "Renovar" : "Pagar") + " no Pix (à vista)");
  }
}

function pixCriarCta(plano) {
  // Só nos cards: o tfoot da tabela comparativa tem as células asseveradas pelo
  // precos_sem_plano_gratis.test.mjs, e uma célula nova o deixa vermelho.
  const cartao = document.querySelector('#plans-v2 [data-plan-btn="' + plano + '"]');
  if (!cartao) return null;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-outline btn-block pix-cta";
  b.dataset.pixCta = plano;
  b.addEventListener("click", () => pixCheckout(plano));
  cartao.after(b);
  return b;
}

// ── Overlay: fundo + caixa + Esc + Tab preso + devolução do foco ────────────
function pixOverlay(titulo, aoFechar) {
  const foco = document.activeElement;
  const ov = document.createElement("div");
  ov.className = "pix-ov";
  ov.setAttribute("role", "dialog");
  ov.setAttribute("aria-modal", "true");
  ov.setAttribute("aria-labelledby", "pix-modal-titulo");
  const box = document.createElement("div");
  box.className = "pix-box";
  const h = document.createElement("h3");
  h.id = "pix-modal-titulo";
  h.textContent = titulo;
  box.appendChild(h);
  ov.appendChild(box);
  const tecla = (e) => {
    if (e.key === "Escape") { fechar(); return; }
    // Trap de Tab do modals.js (§0.1 — o mesmo helper que o modal_keys.test.mjs
    // e o settings_security_fanout.test.mjs já asseveram). Sem ele o Tab
    // alcançava o CTA ATRÁS do overlay e o Enter abria um SEGUNDO QR por cima:
    // dois instrumentos ao portador na tela e duas cobranças no provedor.
    if (window.pigTrapTab) window.pigTrapTab(e, ov);
  };
  function fechar() {
    document.removeEventListener("keydown", tecla);
    ov.remove();
    if (aoFechar) aoFechar();
    if (foco && foco.focus) foco.focus();
  }
  ov.addEventListener("click", (e) => { if (e.target === ov) fechar(); });
  document.addEventListener("keydown", tecla);
  document.body.appendChild(ov);
  // `titulo` sai daqui porque o modal tem DOIS estados (formulário e QR) e o
  // segundo reescreve o cabeçalho do primeiro em vez de abrir outra caixa.
  return { box, fechar, titulo: h };
}

// ── Checkout: o documento primeiro, o QR depois ─────────────────────────────

/**
 * O `<input>` do CPF/CNPJ enquanto o formulário está na tela.
 *
 * O Asaas EXIGE o documento do pagador para criar a cobrança, e NÓS não o
 * persistimos em lugar nenhum. Aqui vale a mesma disciplina do `qr_payload`
 * (§13.6): só memória e DOM — nada de localStorage, sessionStorage, cookie,
 * history.state, query string, console, GA4 ou CAPI —, e ele sai dos dois assim
 * que não há mais uso legítimo: modal fechado, QR na tela, compra terminada.
 */
let pixDoc = null;

/**
 * Par do `pixApagarQr`, e chamado por ele: tira o documento do DOM.
 *
 * O `value = ""` vem ANTES do `remove()` de propósito. Nó destacado continua
 * guardando o `value`, e quem segura a referência é este módulo — foi
 * exatamente assim que o wipe do QR passou a ser medível (`retido`, no PT7).
 */
function pixApagarDoc() {
  if (!pixDoc) return;
  pixDoc.value = "";
  pixDoc.remove();
  pixDoc = null;
}

/**
 * Só a FORMA: 11 dígitos (CPF) ou 14 (CNPJ), depois de tirar a pontuação.
 *
 * O dígito verificador NÃO se confere aqui: quem confere o mod-11 é o SERVIDOR,
 * e uma cópia só da regra é a do §0.7. O Asaas segue sendo a autoridade final —
 * ele recusa por regras próprias documento estruturalmente válido.
 */
const pixDigitos = (v) => String(v || "").replace(/\D/g, "");
const pixFormaOk = (d) => d.length === 11 || d.length === 14;

/**
 * O clique no CTA NÃO cobra mais: ele abre o formulário. O `POST` sai no submit
 * dele, com o documento — sem ele o Asaas recusa a cobrança.
 */
function pixCheckout(plano) {
  // Um modal por vez, nos DOIS estados. Com o QR na tela cada checkout é uma
  // COBRANÇA NOVA no provedor; com o formulário aberto, um segundo overlay
  // deixaria o primeiro órfão no DOM com o documento digitado dentro dele.
  if (pixPoll || document.querySelector(".pix-ov")) return;
  const nome = (PLAN_NAMES && PLAN_NAMES[plano]) || plano;
  // Um `aoFechar` só para os dois estados: o `pixEncerrar` é inerte sem
  // `pixPoll`, então fechar no formulário limpa o documento e mais nada.
  const ctx = pixOverlay(nome + " anual no Pix", () => {
    pixApagarDoc();
    pixEncerrar();
  });
  pixFormulario(plano, ctx);
}

/** Estado 1 do modal: o documento. `<form>` de verdade — o submit nativo dá o
 *  Enter de graça e não custa um listener de tecla a mais dentro do overlay. */
function pixFormulario(plano, ctx) {
  const f = document.createElement("form");
  f.className = "pix-form";
  const rot = document.createElement("label");
  rot.className = "pix-rot";
  rot.htmlFor = "pix-doc";
  rot.textContent = "CPF ou CNPJ de quem vai pagar";
  const campo = document.createElement("input");
  campo.id = "pix-doc";
  campo.className = "pix-doc";
  campo.type = "text";
  // `inputmode` e não `type="number"`: o number come zero à esquerda, aceita
  // `e`/`+`/`-` e ignora `maxLength`. Teclado numérico no celular sem nada disso.
  campo.inputMode = "numeric";
  campo.autocomplete = "off";
  campo.maxLength = 18;              // 14 dígitos + os 4 separadores de um CNPJ
  campo.placeholder = "Só os números";
  campo.setAttribute("aria-label", "CPF ou CNPJ de quem vai pagar");
  campo.setAttribute("aria-describedby", "pix-doc-erro");
  const erro = document.createElement("p");
  erro.className = "pix-erro";
  erro.id = "pix-doc-erro";
  erro.setAttribute("role", "alert");
  const enviar = pixBotao("btn-primary", "Gerar código Pix");
  enviar.type = "submit";
  f.append(rot, campo, erro, enviar);
  const sair = pixBotao("pix-ghost", "Cancelar");
  sair.addEventListener("click", () => ctx.fechar());
  ctx.box.append(pixLinha("O Pix pede o CPF ou CNPJ de quem paga. A gente não"
    + " guarda esse número — ele vai só para emitir a cobrança."), f, sair);
  pixDoc = campo;
  campo.focus();
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const d = pixDigitos(campo.value);
    if (!pixFormaOk(d)) {
      erro.textContent = "Informe os 11 dígitos do CPF ou os 14 do CNPJ.";
      campo.focus();
      return;
    }
    erro.textContent = "";
    pixEnviar(plano, d, false, ctx, enviar);
  });
}

/**
 * O `POST`. `documento` viaja por ARGUMENTO — memória, nunca DOM: a caixa da
 * migração troca o corpo do modal e o número precisa sobreviver ao segundo
 * envio sem voltar para um campo na tela.
 */
async function pixEnviar(plano, documento, confirmarCancelamentoStripe, ctx, botao) {
  if (botao.disabled) return;   // Enter repetido não vira duas cobranças
  botao.disabled = true;
  const rotulo = botao.textContent;
  pixRotular(botao, "ph-clock", "Gerando o código…");
  const corpo = { plan: plano, interval: "annual", cpf_cnpj: documento };
  if (confirmarCancelamentoStripe) corpo.confirm_cancel_stripe = true;
  try {
    const r = await fetch("/billing/pix/checkout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "x-csrf-token": getCsrfToken() },
      body: JSON.stringify(corpo),
    });
    const d = await r.json().catch(() => ({}));
    // Fechou no meio — e a conferência vem DEPOIS do corpo, não antes: dá para
    // fechar entre a chegada dos cabeçalhos e o fim do download do JSON, e aí o
    // QR ia para uma caixa já destacada, com o poll rodando por trás dela e o
    // `pixPoll` invisível bloqueando o próximo checkout até vencer. A cobrança
    // criada lá expira sozinha; o que não pode é sobrar aqui.
    if (!ctx.box.isConnected) return;
    if (r.status === 401) {
      showToast("Faça login pra continuar a assinatura.", "err");
      setTimeout(() => {
        window.location.href = "/login?next=" + encodeURIComponent("/precos");
      }, 900);
      return;
    }
    const det = (d && d.detail) || {};
    if (r.status === 409 && det.error === "stripe_active") {
      return pixModalMigracao(plano, det, documento, ctx);
    }
    const pago = det.error === "pix_future_purchase_conflict"
      && /^\d{4}-\d{2}-\d{2}/.exec(det.covered_until || "");
    if (r.status === 409 && pago) return pixModalJaPago(pago[0], ctx);
    // `detail` do FastAPI é STRING quando o raise passa texto (400 do documento) e
    // OBJETO nos 409 — sem esta linha a mensagem específica virava o genérico.
    if (!r.ok) {
      const msg = (typeof det === "string" ? det : det.message);
      return showToast(msg || "Não consegui gerar o código Pix agora.", "err");
    }
    pixApagarDoc();          // o QR vai entrar: o documento sai da tela antes
    pixModalQr(d, plano, ctx);
  } catch {
    showToast("Erro de conexão. Tente novamente.", "err");
  } finally {
    // `isConnected`: deu certo, este botão já saiu do modal junto com o
    // formulário e reanimá-lo seria escrever num nó que ninguém vê.
    if (botao.isConnected) { botao.disabled = false; botao.textContent = rotulo; }
  }
}

/** 409 stripe_active: a migração cartão → Pix (§9 do plano). Mesmo modal. */
function pixModalMigracao(plano, det, documento, ctx) {
  const { box, fechar, titulo } = ctx;
  // O documento sai da tela agora — esta caixa decide sobre o Stripe, e o número
  // segue vivo só na closure do botão abaixo.
  pixApagarDoc();
  titulo.textContent = "Trocar o cartão pelo Pix?";
  box.replaceChildren(titulo);
  const data = fmtBrDate(String(det.current_period_end || "").slice(0, 10));
  box.append(
    pixLinha("Sua assinatura no cartão é cancelada no fim do período que você já"
      + " pagou (" + data + "). Não existe cobrança dupla."),
    pixLinha("Não cancele pelo painel do Stripe: quem cancela somos nós, na data"
      + " certa. Cancelando por lá você perde o acesso antes."),
    pixLinha("Seu ano de Pix começa em " + data + ", quando o cartão termina."),
  );
  const ok = pixBotao("btn-primary", "Continuar no Pix");
  ok.addEventListener("click", () => pixEnviar(plano, documento, true, ctx, ok));
  const nao = pixBotao("pix-ghost", "Manter o cartão");
  nao.addEventListener("click", () => fechar());
  box.append(ok, nao);
  ok.focus();
}

// 409 pix_future_purchase_conflict: caixa e não toast — reenviar dá o mesmo 409.
function pixModalJaPago(dia, ctx) {
  pixApagarDoc();                       // o formulário sai, e o CPF com ele
  ctx.titulo.textContent = "Esse ano já é seu";
  const ok = pixBotao("btn-primary", "Entendi");
  ok.addEventListener("click", () => ctx.fechar());
  ctx.box.replaceChildren(ctx.titulo, pixLinha("Você já pagou esse plano até " + fmtBrDate(dia) + ". Não cobramos nada agora."), ok);
  ok.focus();
}

// A precos.html chama o `pbPixInit` depois de duas requisições, guardada por
// `typeof pbPixInit === "function"` — e estes dois scripts ainda podem estar
// sendo BAIXADOS quando elas terminam: ali a guarda daria falso e ninguém
// tentaria de novo, deixando a página sem CTA nenhum com a flag ligada. Então
// quem chegar por ÚLTIMO lê o estado que o outro deixou, seja qual for a ordem.
if (window.pbPixState) pbPixInit(window.pbPixState.cfg, window.pbPixState.sub);
