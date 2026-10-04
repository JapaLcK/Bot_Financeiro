/**
 * Os cadernos extras no modal do Pix anual (/precos e /continuar-compra): as mesmas caixas da /assinar
 * (bump-caixas.js), dentro do formulário do documento. Quarto arquivo do trio pix-*, e por teto: o
 * pix-checkout.js está nas 350 linhas do `quality/max-lines`.
 *
 * O Pix do PLANO nunca espera nem depende daqui: sem o bump-caixas.js, com o GET falhando ou com a oferta
 * vazia, não nasce caixa e o POST vai com `extras: []`. Só os ids viajam: o valor dos cadernos o servidor
 * relê do Stripe. O que a tela mostra marcado é o que vai no POST — inclusive no reenvio da migração, que
 * troca o corpo do modal e por isso lê a seleção daqui, não do DOM.
 */
"use strict";

const PIX_EXTRAS_MUDOU = "Os cadernos disponíveis mudaram. Confira o pedido e gere o código de novo.";

let pixExtrasCaixa = null;   // o `.pp-bump` do formulário aberto
let pixExtrasAviso = null;   // a mensagem do 409, logo acima dele
let pixExtrasSel = [];       // ids marcados
let pixExtrasCbs = [];       // os checkboxes desenhados

/** O formulário do documento abriu: busca a oferta e desenha antes de `antes` (o botão de enviar). */
function pixExtrasMontar(form, antes) {
  pixExtrasSel = [];
  pixExtrasCbs = [];
  pixExtrasCaixa = pixExtrasAviso = null;
  if (!window.PBBumpCaixas) return;
  const aviso = document.createElement("p");
  aviso.className = "pix-erro";
  aviso.setAttribute("role", "alert");
  const caixa = document.createElement("div");
  caixa.className = "pp-bump";
  caixa.hidden = true;
  form.insertBefore(aviso, antes);
  form.insertBefore(caixa, antes);
  pixExtrasCaixa = caixa;
  pixExtrasAviso = aviso;
  fetch("/billing/pix-extras", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      // Chegou com o POST já em voo (botão travado) ou com o modal fechado: não aparece caixa nova que o
      // pedido em andamento não leva.
      if (!d || caixa !== pixExtrasCaixa || !caixa.isConnected || antes.disabled) return;
      pixExtrasSel = Array.isArray(d.selecao) ? d.selecao : [];   // Q6: a cobrança pendente vem marcada
      pixExtrasDesenhar(Array.isArray(d.extras) ? d.extras : []);
    })
    .catch(() => {});
}

/** Redesenha com `ofertas`, marcando os ids da seleção que estão nela; o `pinta` refaz a seleção pelas
 *  caixas, e o id que saiu da oferta sai dela também. */
function pixExtrasDesenhar(ofertas) {
  const caixa = pixExtrasCaixa;
  caixa.replaceChildren();
  const cbs = window.PBBumpCaixas.montar(caixa, ofertas.map((x) => (
    { ...x, no_carrinho: pixExtrasSel.includes(x.price) })));
  pixExtrasCbs = cbs;
  const pinta = () => {
    pixExtrasSel = ofertas.filter((x, i) => cbs[i].checked).map((x) => x.price);
    window.PBBumpCaixas.pintar(caixa, cbs, false);
  };
  cbs.forEach((c) => c.addEventListener("change", pinta));
  pinta();
}

/** O POST em voo trava as caixas e o botão de volta as destrava (o `trava` do pagamento-pagina.js): o que
 *  se marca depois do clique não vai no pedido em andamento. */
function pixExtrasTravar(sim) {
  if (pixExtrasCaixa) window.PBBumpCaixas.pintar(pixExtrasCaixa, pixExtrasCbs, sim);
}

/** Os ids que vão no POST. Um envio novo apaga o aviso do 409 anterior. */
function pixExtrasIds() {
  if (pixExtrasAviso) pixExtrasAviso.textContent = "";
  return pixExtrasSel.slice();
}

/** 409 `extras_indisponiveis`: a oferta mudou entre abrir o modal e enviar. Redesenha com a oferta nova
 *  e para; nada é cobrado sem um novo clique. Na caixa da migração o formulário já saiu da tela: a seleção
 *  encolhe do mesmo jeito e o aviso vai para o toast. */
function pixExtrasRecusa(ofertas) {
  const lista = Array.isArray(ofertas) ? ofertas : [];
  if (pixExtrasCaixa && pixExtrasCaixa.isConnected) {
    pixExtrasDesenhar(lista);
    pixExtrasAviso.textContent = PIX_EXTRAS_MUDOU;
  } else {
    pixExtrasSel = pixExtrasSel.filter((id) => lista.some((x) => x.price === id));
    showToast(PIX_EXTRAS_MUDOU, "err");
  }
}
