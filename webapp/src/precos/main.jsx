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
const planos = raiz ? lerPlanos(raiz) : [];

// Sem cartão lido, não monta: `createRoot` LIMPA o container, então montar sobre
// um markup que mudou de forma trocaria os cards por nada — a página inteira
// perderia a venda. Nesse caso fica o markup do servidor, que funciona sozinho.
if (planos.length) {
  flushSync(() => createRoot(raiz).render(<Planos planos={planos} />));
  // Os MESMOS dois que o `setCycle` já chama, e ambos idempotentes: o
  // `pbPixRefresh` reusa o `[data-pix-cta]` existente (pix-checkout.js:96) e o
  // `refreshPlanButtons` sai cedo sem assinatura. Aqui eles cobrem o caso de o
  // fetch do `loadPlansState` ter voltado ANTES deste script — o CTA de Pix não
  // pode existir ainda (o pix-checkout.js só carrega depois), mas chamar é mais
  // barato que raciocinar sobre a janela.
  globalThis.pbPixRefresh?.();
  globalThis.refreshPlanButtons?.();
}
