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
  // Os MESMOS dois que o `setCycle` já chama, e ambos idempotentes: o
  // `pbPixRefresh` reusa o `[data-pix-cta]` existente (pix-checkout.js:96) e o
  // `refreshPlanButtons` sai cedo sem assinatura. Aqui eles cobrem o caso de o
  // fetch do `loadPlansState` ter voltado ANTES deste script — o CTA de Pix não
  // pode existir ainda (o pix-checkout.js só carrega depois), mas chamar é mais
  // barato que raciocinar sobre a janela.
  globalThis.pbPixRefresh?.();
  globalThis.refreshPlanButtons?.();
} else if (raiz) {
  // O fallback é seguro, mas SILENCIOSO — e silencioso é como ele ficaria meses
  // no ar sem ninguém saber que a ilha parou de montar.
  console.warn("#plans-v2 fora do contrato do lerPlanos: a ilha React não"
    + " montou, ficou o markup do servidor.");
}
