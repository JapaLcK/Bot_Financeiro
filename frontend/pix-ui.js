/**
 * As peças de UI do Pix anual: rótulo com ícone, linha de texto, botão e o
 * overlay. Terceiro arquivo do trio `pix-ui` → `pix-checkout` → `pix-poll`,
 * e o ÚNICO cuja divisão é por assunto e não pelo teto de 350 linhas: aqui
 * mora o que os outros dois COMPARTILHAM, sem um pedaço de estado do Pix
 * dentro (nada de `pixCfg`, `pixSub`, `pixPoll` ou fetch).
 *
 * Carrega ANTES dos outros dois na precos.html, e isso é obrigatório: `pixBrl`
 * é `const` de topo de script clássico, então tem TDZ até este arquivo rodar —
 * e a última linha do pix-checkout.js já chama `pbPixInit`.
 *
 * Script CLÁSSICO (sem módulo ES) pelo mesmo motivo dos outros dois: módulo é
 * deferido, e o defer reintroduz a corrida com o script inline da página.
 */
"use strict";

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
