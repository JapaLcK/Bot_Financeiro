/**
 * Pix anual na /precos — o CTA nos cards, a etiqueta do toggle e o checkout.
 *
 * São TRÊS arquivos no mesmo escopo global, carregados nesta ordem:
 *
 *   pix-ui.js        rótulo, linha, botão e overlay — o que os outros dois
 *                    compartilham, sem estado do Pix dentro. Divisão por ASSUNTO.
 *   pix-checkout.js  este: o CTA nos cards, a etiqueta do toggle e o POST.
 *   pix-poll.js      o QR, a cópia, o poll e as duas caixas de recusa do 409.
 *                    Divisão pelo teto de 350 linhas do `quality/max-lines`.
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
// `pixSub = null` é AMBÍGUO e significa duas coisas opostas: "ainda não sei"
// (a publicação antecipada do loadPlansState, antes do /billing/subscription) e
// "sei: não tem assinatura" — que é o DESLOGADO, o público-alvo da etiqueta.
// Sem este terceiro estado não dá para separar as duas, e qualquer guarda que só
// teste `pixSub == null` esconde a etiqueta de quem ela existe para convencer.
let pixSubResolvida = false;

// ── O CTA nos cards ─────────────────────────────────────────────────────────

/**
 * Pix está à venda para ESTE usuário? Fonte única do CTA dos cards e da etiqueta
 * do toggle (§0.7). `!== true` e não `!`: portão de venda não abre com truthy
 * qualquer. Vitalício não compra — o backend recusa com 409 `lifetime`.
 */
function pixAVenda() {
  return !!pixCfg && pixCfg.pix_annual_available === true && !(pixSub && pixSub.lifetime === true);
}

/**
 * Chamado pelo loadPlansState da precos.html, com o que ela já buscou.
 * `subResolvida` só é true na segunda chamada, com o /billing/subscription na mão.
 */
function pbPixInit(cfg, sub, subResolvida) {
  pixCfg = cfg || null;
  pixSub = sub || null;
  pixSubResolvida = subResolvida === true;
  // A etiqueta do toggle é o único anúncio de Pix que o ciclo MENSAL tem (o CTA
  // dos cards só nasce no anual), então quem a revela é o init, não o refresh.
  //
  // E ela é o único dos dois que ESPERA a assinatura. O CTA é caminho de
  // RESGATE — nascer cedo ajuda quem quer migrar do cartão enquanto o Stripe
  // está lento (é o que a publicação antecipada da precos.html existe para
  // consertar, e o vitalício que clicar nele toma o 409 `lifetime`). A etiqueta
  // é ANÚNCIO: revelá-la antes de saber quem está olhando é propaganda enganosa
  // para o vitalício, e ela fica até a requisição voltar — indefinidamente, se
  // ela travar. Por isso `pixSubResolvida` entra aqui e não no `pixAVenda`.
  const nota = document.getElementById("pix-cycle-note");
  const btnAnual = document.getElementById("cycle-annual");
  if (nota) nota.hidden = !(pixAVenda() && pixSubResolvida);
  // Leitor de tela: a etiqueta é revelada DEPOIS do load, e quem está no botão
  // "Anual" nunca passa por ela. O `#pix-cycle-live` em volta dela é a região
  // `aria-live` que anuncia a revelação (a região tem de existir desde o parse
  // — registrar e revelar no mesmo instante não anuncia); o `aria-describedby`
  // é o que sobra para quem chega ao botão depois, navegando controle por
  // controle. Ele entra e SAI com a etiqueta: elemento diretamente referenciado
  // é lido mesmo `hidden`, então deixá-lo fixo anunciaria Pix para quem não
  // pode comprar — o mesmo erro que o `hidden` do markup existe para evitar.
  if (btnAnual && nota && !nota.hidden) btnAnual.setAttribute("aria-describedby", "pix-cycle-note");
  else if (btnAnual) btnAnual.removeAttribute("aria-describedby");
  pbPixRefresh();
}

/** Chamado pelo setCycle: o CTA de Pix só existe no ciclo anual. */
function pbPixRefresh() {
  const anual = currentCycle === "annual" && pixAVenda();
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
      showToast("");   // o único desfecho que NÃO passa pelo `pixEnviar` (limpeza lá)
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
  // Clicou em enviar: a mensagem anterior deixou de valer, DÊ NO QUE DER — QR,
  // migração, "já pago", inline do 400 ou toast novo. Um ponto só, em vez de um por desfecho.
  showToast("");
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
    // `detail` STRING é a metade que faltava: o FastAPI manda `{"detail": "<frase>"}`
    // em todo `HTTPException(detail="…")`, e aqui isso caía num `det.message`
    // undefined — a frase que o servidor escreveu era descartada e o cliente lia o
    // genérico. São seis: os três 400 (plano, documento e o titular recusado pelo
    // Asaas), o 429 do limitador (por IP — routes/shared.py:98, então não é só quem
    // digitou que o toma), o 503 da indisponibilidade e o 403 do CSRF, sem isenção.
    // Mesma forma do `apiError` do comecar.js:175 — o 500 real não tem `detail`
    // nenhum (`{"error": …}`, finance_bot_websocket_custom.py:2415), então segue
    // no genérico. O #355 consertou o mesmo defeito só no toast; normalizar aqui
    // em cima cobre o toast E as duas caixas do 409 de uma vez — por isso o
    // rebase deixou UMA das duas versões, não as duas.
    const det = (d && (typeof d.detail === "string" ? { message: d.detail } : d.detail)) || {};
    if (r.status === 409 && det.error === "stripe_active") {
      return pixModalMigracao(plano, det, documento, ctx);
    }
    // Guarda de FORMA, não de data: "2028-13-45" passa e a tela escreve
    // "45/13/2028" (medido). Não se aperta porque não é alcançável — o
    // `covered_until` é o `isoformat()` de um timestamptz (o `CoberturaJaPaga` do
    // `criar_checkout_pix`, em `frontend/routes/billing_pix.py`). O
    // que ela barra é o que já chegava: ausente, ou texto livre virando "undefined".
    // ponytail: e o dia recortado é o do calendário UTC, não o de Brasília — compra
    // entre 21h e 24h (3 das 24 horas) nomeia o dia seguinte. Categoria, não caso:
    // o `pixModalMigracao` e o `pixModalQr` (`pix-poll.js`) recortam igual. Fechar é converter o
    // fuso nos três, não recortar string.
    const pago = det.error === "pix_future_purchase_conflict"
      && /^\d{4}-\d{2}-\d{2}/.exec(det.covered_until || "");
    if (r.status === 409 && pago) return pixModalJaPago(pago[0], ctx);
    // 400 é erro DO CAMPO: vai para o `#pix-doc-erro` (alvo do `aria-describedby`), DENTRO do modal, e não para o toast. O `disabled` largou o foco no <body>: volta pro campo.
    const alvo = r.status === 400 && det.message && document.getElementById("pix-doc-erro");
    if (alvo) { alvo.textContent = det.message; pixDoc?.focus(); return; }
    if (!r.ok) return showToast(det.message || "Não consegui gerar o código Pix agora.", "err");
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

// A precos.html chama o `pbPixInit` depois de duas requisições, guardada por
// `typeof pbPixInit === "function"` — e estes dois scripts ainda podem estar
// sendo BAIXADOS quando elas terminam: ali a guarda daria falso e ninguém
// tentaria de novo, deixando a página sem CTA nenhum com a flag ligada. Então
// quem chegar por ÚLTIMO lê o estado que o outro deixou, seja qual for a ordem.
if (window.pbPixState) pbPixInit(window.pbPixState.cfg, window.pbPixState.sub, window.pbPixState.resolvida);
