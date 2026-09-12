/**
 * O QR do Pix anual da /precos: o modal, a cópia, o poll, o §13.6 e as duas
 * caixas de recusa do 409.
 *
 * Metade de trás do pix-checkout.js, e não um arquivo por gosto: juntos os dois
 * passam das 350 linhas do `quality/max-lines`. Os dois são script CLÁSSICO e
 * dividem o mesmo escopo global — daqui saem `pixModalQr`, `pixEncerrar` e as
 * duas caixas de recusa, e de lá vêm `pixLinha`, `pixBotao`, `pixRotular`,
 * `pixBrl`, `pixApagarDoc`, `pixCheckout` e `pixEnviar`. O `pixPoll` (estado do
 * modal aberto) mora SÓ aqui; o `pixDoc` mora SÓ lá: nenhum dos dois arquivos
 * escreve variável do outro.
 *
 * O modal é UM só, com dois estados: o pix-checkout.js abre a caixa pedindo o
 * CPF/CNPJ (o Asaas exige o documento do pagador) e o `pixModalQr` daqui troca o
 * corpo dela pelo QR. Por isso ele recebe o `ctx` do overlay em vez de abrir
 * outro — e por isso o `pixApagarQr` limpa o documento junto com o payload.
 *
 * §13.6 — o `qr_payload` é INSTRUMENTO AO PORTADOR: só memória e DOM (nada de
 * localStorage, sessionStorage, cookie, history.state, query string ou log), e
 * sai do DOM ao pagar, expirar ou fechar. Quem viaja na URL é o `public_token`.
 */
"use strict";

// Terminais que não são pagamento: o QR morre e não há o que esperar.
const PIX_TERMINAIS = new Set(["canceled", "expired", "refunded", "chargeback"]);
// Teto do cliente, e SÓ para `expires_at` ausente/ilegível — o teto de verdade é
// o do servidor (fonte única, CLAUDE.md §0.7). Era um `Math.min` com este valor,
// que atropelava o servidor: com `expires_at` de +1h a tela parava aos 15 min e
// declarava expirada uma cobrança que o backend ainda aceitava pagar.
const PIX_TETO_MS = 15 * 60 * 1000;

let pixPoll = null;   // estado do modal aberto; null = não há QR na tela

function pixModalQr(d, plano, ctx) {
  const nome = (PLAN_NAMES && PLAN_NAMES[plano]) || plano;
  const valor = pixBrl(d.amount_cents);
  // `ctx` é o modal que o pixCheckout já abriu para pedir o CPF/CNPJ: o QR é o
  // SEGUNDO ESTADO dele, não uma segunda caixa. Reescreve o cabeçalho e troca o
  // corpo. O `aoFechar` daquele overlay já chama o `pixEncerrar` — não há
  // listener novo a registrar aqui, e nem um segundo lugar de onde fechar.
  const { box, fechar, titulo } = ctx;
  titulo.textContent = nome + " anual" + (valor ? " · " + valor : "");
  box.replaceChildren(titulo);

  // Tudo que morre junto com o código num container só: expirar é trocar o
  // conteúdo dele, não caçar cinco elementos soltos na caixa.
  const vivo = document.createElement("div");
  const credito = pixBrl(d.credit_cents);
  if (credito && d.credit_cents > 0) {
    vivo.appendChild(pixLinha("Já com " + credito + " de crédito do seu plano atual."));
  }
  // Quem separa agendado de imediato é o `agendada` que o servidor manda
  // (`agendada()` em `core/services/pix_checkout_resposta.py`), não a
  // presença de `starts_at` — que na compra imediata vem com `agora` e fazia
  // esta linha dizer "Seu ano começa em <hoje>" a quem começa ao pagar.
  vivo.appendChild(pixLinha(d.agendada && d.starts_at
    ? "Seu ano começa em " + fmtBrDate(String(d.starts_at).slice(0, 10)) + "."
    : "Seu ano começa agora, assim que o pagamento cair."));

  // Sem `qr_image` o <img> nem entra: vazio ele desenha um quadrado BRANCO de
  // 230px (o fundo claro que o leitor de QR exige) com cara de código ilegível.
  const img = document.createElement("img");
  img.className = "pix-qr";
  img.alt = "QR Code do Pix";
  if (d.qr_image) { img.src = d.qr_image; vivo.appendChild(img); }

  const code = document.createElement("input");
  code.className = "pix-code";
  code.readOnly = true;
  code.setAttribute("aria-label", "Código Pix copia e cola");
  code.value = d.qr_payload || "";

  // Copiar é a ação PRIMÁRIA (§16.1): dentro do app o aparelho não se escaneia.
  const copiar = pixBotao("btn-primary", "");
  pixRotular(copiar, "ph-clipboard-text", "Copiar código Pix");
  copiar.addEventListener("click", () => pixCopiar(code, copiar));
  const status = document.createElement("p");
  status.className = "pix-status";
  pixRotular(status, "ph-clock", "Esperando o pagamento…");
  // Fora do `vivo` de propósito: fechar tem de continuar possível depois que o
  // código expira e o corpo é trocado.
  const sair = pixBotao("pix-ghost", "Fechar");
  sair.addEventListener("click", () => fechar());

  vivo.append(code, copiar, status);
  box.append(vivo, sair);
  copiar.focus();

  pixPoll = {
    token: d.public_token, plano, vivo, img, code, status, fechar,
    inicio: Date.now(), falhas: 0, timer: null,
    // Nem a data nem o valor viajam daqui para a /home: ela busca os dois no
    // `GET /billing/pix/<token>`, que é autenticado e filtra por dono. Este
    // objeto é um retrato tirado no checkout, e o retrato ENVELHECE — na
    // migração Stripe→Pix o `_stripe_cancel` adia o começo do acesso depois
    // que o QR já está na tela.
    // `Date.parse(undefined)` é NaN, e NaN é falsy: sem `expires_at` legível
    // sobra o teto do cliente. COM ele, quem manda é o servidor.
    deadline: Date.parse(d.expires_at) || Date.now() + PIX_TETO_MS,
  };
  document.addEventListener("visibilitychange", pixVisivel);
  pixAgendar(0);
}

/**
 * Só diz "Copiado" quando copiou.
 *
 * `execCommand("copy")` devolve `false` quando não copia, e pode LEVANTAR — o
 * retorno ignorado era a tela mentindo sobre a ação primária de um pagamento.
 *
 * ponytail: teto conhecido e não provável aqui — no WKWebView do iOS (a /precos
 * abre dentro do app) `select()` + `execCommand` num `<input readonly>` é caso
 * clássico de falha silenciosa, e `execCommand` chega a devolver `true` sem ter
 * copiado. Não dá para medir em Chromium headless; o que dá é não mentir quando
 * ele devolve `false`, e deixar o campo selecionado para a cópia à mão.
 */
function pixCopiar(code, botao) {
  const ok = () => {
    pixRotular(botao, "ph-check-circle", "Copiado ✓");
    setTimeout(() => pixRotular(botao, "ph-clipboard-text", "Copiar código Pix"), 1600);
  };
  const manual = () => {
    code.select();
    let copiou = false;
    try { copiou = document.execCommand("copy") === true; } catch { copiou = false; }
    if (copiou) { ok(); return; }
    pixRotular(botao, "ph-warning", "Não consegui copiar — o código está"
      + " selecionado, copie à mão");
  };
  if (navigator.clipboard) navigator.clipboard.writeText(code.value).then(ok, manual);
  else manual();
}

/** §13.6: o copia-e-cola sai do DOM assim que não há mais uso legítimo dele. */
function pixApagarQr() {
  // O CPF/CNPJ vai junto, e ANTES do `return`: a mesma disciplina, a mesma hora.
  // Sem esta linha, "pagou" e "expirou" limpavam o payload e deixavam o
  // documento no `<input>` destacado que o pix-checkout.js ainda segura.
  pixApagarDoc();
  if (!pixPoll) return;
  pixPoll.code.value = "";
  pixPoll.img.removeAttribute("src");
  pixPoll.img.remove();
  pixPoll.code.remove();
}

/** Fechou o modal (Esc, fundo, botão): para o poll e apaga o código. */
function pixEncerrar() {
  if (!pixPoll) return;
  clearTimeout(pixPoll.timer);
  document.removeEventListener("visibilitychange", pixVisivel);
  pixApagarQr();
  pixPoll = null;
}

// ── O poll ──────────────────────────────────────────────────────────────────

// O Pix liquida em segundos; a cauda não merece 3 s por 15 minutos.
const pixIntervalo = () => (Date.now() - pixPoll.inicio < 120000 ? 3000 : 10000);

function pixAgendar(ms) {
  clearTimeout(pixPoll.timer);
  pixPoll.timer = setTimeout(pixBater, ms);
}

/** Voltou para a aba: confirma na hora (saiu para o app do banco e pagou). */
function pixVisivel() {
  if (!document.hidden && pixPoll) pixAgendar(0);
}

async function pixBater() {
  // A cobrança que ESTA pergunta é sobre. Fechar o modal com a requisição no ar
  // e abrir outro checkout deixava `pixPoll` NÃO-NULO de novo — e a resposta da
  // cobrança VELHA passava pela guarda: um `paid` antigo redirecionava com o
  // `sid` da outra, e um terminal antigo apagava o QR novo. Identidade do
  // objeto, não `token`: duas cobranças podem trazer o mesmo.
  const meu = pixPoll;
  if (!meu) return;
  // O deadline NÃO expira sozinho. Na cauda o poll é de 10 s: a cobrança liquida
  // dentro dessa janela, depois do último poll, e a tela dizia "expirou e nada
  // foi cobrado" sobre cobrança PAGA — com um "Gerar novo código" ao lado, que
  // CANCELA a cobrança remota (§10). Vencido, pergunta-se uma última vez.
  const venceu = Date.now() >= meu.deadline;
  // Aba escondida não gasta requisição: quem retoma é o visibilitychange. A
  // última pergunta é a exceção — é ela que decide a mensagem.
  if (document.hidden && !venceu) { pixAgendar(pixIntervalo()); return; }

  let corpo = null;
  let desistir = false;
  try {
    const r = await fetch("/billing/pix/" + encodeURIComponent(meu.token),
      { credentials: "same-origin" });
    if (pixPoll !== meu) return;                // fechou ou trocou de cobrança
    if (r.status === 401) desistir = true;      // sobrou do auth-refresh: não insiste
    else if (!r.ok) throw new Error("http " + r.status);
    else corpo = await r.json();
  } catch {
    if (pixPoll !== meu) return;
    if (++meu.falhas >= 3) desistir = true;
  }
  if (pixPoll !== meu) return;
  if (corpo) {
    meu.falhas = 0;
    if (corpo.status === "paid") { pixPago(); return; }
    if (PIX_TERMINAIS.has(corpo.status)) { pixExpirou("Esta cobrança não vale mais.", true); return; }
  }
  // Só o "não" do servidor autoriza afirmar que nada foi cobrado — e só ele
  // autoriza oferecer código novo, porque gerar outro cancela este.
  if (venceu) {
    pixExpirou(corpo
      ? "Este código expirou e nada foi cobrado."
      : "Este código venceu e não deu pra confirmar por aqui. Se você pagou,"
        + " seu acesso é liberado sozinho.", !!corpo);
    return;
  }
  if (desistir) { pixDesistir(); return; }
  pixAgendar(pixIntervalo());
}

function pixPago() {
  const { token, plano, status } = pixPoll;
  clearTimeout(pixPoll.timer);
  document.removeEventListener("visibilitychange", pixVisivel);
  pixRotular(status, "ph-check-circle", "Pagamento confirmado! Liberando seu acesso…");
  status.classList.add("ok");
  pixApagarQr();
  // `sid` = public_token (§13.6): vira o eventID do pixel da Meta e o
  // transaction_id do GA4 na /home. Sem `ia=` — a home.html trata a ausência.
  // `gw=pix` é só o MARCADOR de gateway: ele manda a /home buscar a cobrança em
  // `/billing/pix/<sid>` e tirar de lá o valor e a data. Dinheiro não viaja na
  // query string — `vl=` e `inicio=` saíram daqui porque qualquer um os digita,
  // e o `vl` forjado virava receita inventada na NOSSA conta de anúncios.
  window.location.href = "/home?upgrade=success&sid=" + encodeURIComponent(token)
    + "&ev=purchase&td=0&pl=" + encodeURIComponent(plano) + "&gw=pix";
}

/**
 * `confirmado` = o servidor respondeu que esta cobrança não foi paga. Só nesse
 * caso nasce o "Gerar novo código": ele CANCELA a cobrança remota e cria outra
 * (§10), e oferecer isso a quem talvez tenha pago é o caminho do pagamento
 * duplicado / `paid_orphan`. Sem confirmação sobra o "Fechar", que nunca sai da
 * caixa.
 */
function pixExpirou(motivo, confirmado) {
  if (!pixPoll) return;
  clearTimeout(pixPoll.timer);
  // Sem isto, voltar para a aba depois de expirar remontava o corpo e roubava o
  // foco de quem já estava lendo a mensagem.
  document.removeEventListener("visibilitychange", pixVisivel);
  const { vivo, plano, fechar } = pixPoll;
  pixApagarQr();
  vivo.replaceChildren(pixLinha(motivo));
  if (!confirmado) return;
  const novo = pixBotao("btn-primary", "Gerar novo código");
  // Reabre no ESTADO 1: o documento não ficou guardado, então é pedido de novo.
  novo.addEventListener("click", () => { fechar(); pixCheckout(plano); });
  vivo.appendChild(novo);
  novo.focus();
}

/**
 * Parou de conseguir perguntar (401 que passou pelo auth-refresh, ou rede fora).
 * O código NÃO some agora: é pagável sem sessão nenhuma, e apagá-lo puniria quem
 * está com o app do banco aberto. Mas o poll não morre — ele DORME até o
 * vencimento, e lá tenta de novo (a rede pode ter voltado) e, dando ou não, o
 * payload sai do DOM. Sem isso o copia-e-cola ficava na tela para sempre, com a
 * mensagem "o código continua válido" uma hora depois de vencer.
 */
function pixDesistir() {
  pixRotular(pixPoll.status, "ph-clock", "Não consegui confirmar por aqui — o"
    + " código continua válido. Entre de novo e a gente confirma.");
  pixAgendar(Math.max(0, pixPoll.deadline - Date.now()));
}

// ── As duas caixas de recusa do 409 ─────────────────────────────────────────
//
// Aqui e não no pix-checkout.js por TETO: aquele bateu nas 350 linhas do
// `quality/max-lines`, e este é a metade de trás dele. Quem chama as duas é o
// `pixEnviar` de lá, e as duas tiram o formulário — com o CPF digitado dentro
// dele — da tela antes de escrever.
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
//
// A frase NÃO nomeia plano, de propósito: quem bloqueia pode ser um grant de
// OUTRO tier. O `plano_da_cobranca` junta `pix_futuro_pago` (qualquer Pix futuro,
// de qualquer tier) com `cobre_o_tier` e levanta `CoberturaJaPaga(plano_novo, …)`
// — o `plan` que volta no corpo do 409 é o PEDIDO, não o pago
// (core/services/pix_pricing.py:230-240; o caso Plus futuro → Pro está em
// tests/test_pix_recompra.py:109). "Você já pagou esse plano" era falso ali, numa
// tela de dinheiro. O que é verdade nos dois caminhos: existe período pago até
// tal dia, e por isso não há cobrança agora.
function pixModalJaPago(dia, ctx) {
  pixApagarDoc();                       // o formulário sai, e o CPF com ele
  ctx.titulo.textContent = "Você já tem tempo pago";
  const ok = pixBotao("btn-primary", "Entendi");
  ok.addEventListener("click", () => ctx.fechar());
  ctx.box.replaceChildren(ctx.titulo, pixLinha("Seu período pago vai até "
    + fmtBrDate(dia) + ". Por isso não cobramos nada agora."), ok);
  ok.focus();
}
