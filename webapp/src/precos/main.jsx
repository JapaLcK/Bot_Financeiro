/**
 * Ilha React do `#plans-v2` da /precos. Primeiro passo da migração para React;
 * a convenção é UM bundle por página (`frontend/precos-app.js`), com espaço para
 * um compartilhado quando a segunda página chegar.
 *
 * `flushSync` e não `root.render` solto: o render do React 19 é agendado, e
 * agendado significa que o `document.querySelector('#plans-v2 [data-plan-btn=…]')`
 * do `pix-checkout.js` — que roda no `<script>` seguinte — poderia não achar
 * botão nenhum. Com `flushSync` o DOM está pronto antes deste script terminar.
 */
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import "./precos.css";

import { Planos } from "./Planos.jsx";
import { lerPlanos } from "./lerPlanos.js";

const raiz = document.getElementById("plans-v2");
// `null` = o markup saiu do contrato enumerado no lerPlanos.js, e aí NÃO se
// monta: `createRoot` LIMPA o container, então montar sobre um markup que este
// bundle não sabe reproduzir apagaria justamente o que ele não leu — no melhor
// caso um parágrafo, no pior a escada de preços inteira. Fica o markup do
// servidor, que vende sozinho.
const planos = raiz && lerPlanos(raiz);

if (planos) {
  flushSync(() => createRoot(raiz).render(<Planos planos={planos} />));
  // OBRIGATÓRIA, não uma precaução barata — e a janela dela é alcançável SÓ POR
  // ATRASO DE REDE, medido, não suposto.
  //
  // O quê: o `refreshPlanButtons` põe o handler de troca de plano em
  // PROPRIEDADE (`btn.onclick = function () { openChangeModal(p); }`), e
  // propriedade não vira atributo — o `lerCartao` lê o ATRIBUTO, que continua
  // valendo `startCheckout(…)`. Sem esta linha o assinante recebe um botão
  // escrito "Trocar pro Pro", HABILITADO, cujo clique abre assinatura NOVA.
  //
  // Por que a rede basta: o `refreshPlanButtons` não é chamado por um
  // `<script>`. Ele roda dentro do `await` do `loadPlansState`, que é inline e
  // JÁ EXECUTOU — a continuação da promessa dele roda enquanto o parser está
  // BLOQUEADO baixando este bundle. Com o bundle atrasado 1500 ms: texto do
  // botão trocado em 62 ms, mount só em 1555 ms. A ordem dos `<script>` no
  // documento não protege nada aqui. É o PI8 do
  // `tests/frontend/precos_ilha_react.test.mjs`.
  //
  // O CTA de Pix não precisa de linha nenhuma neste bloco, e isso é do contrato,
  // não sorte: se ele existe, o card tem um 2º `<button>`, está FORA do contrato
  // do `lerPlanos` e não se monta nada. (Havia aqui um `globalThis.pbPixRefresh?.()`
  // que nunca pôde fazer trabalho: os `pix-*.js` são `<script>` clássicos
  // POSTERIORES a este na mesma fila, então na hora do mount `pbPixRefresh` é
  // sempre `undefined`.)
  globalThis.refreshPlanButtons?.();
} else if (raiz) {
  // O fallback é seguro, mas SILENCIOSO — e silencioso é como ele ficaria meses
  // no ar sem ninguém saber que a ilha parou de montar.
  console.warn("#plans-v2 fora do contrato do lerPlanos: a ilha React não"
    + " montou, ficou o markup do servidor.");
}
