// O bundle mora em frontend/, ao lado de brand/: o /painel o pede como /dashboard-app.js e o
// protótipo (dashboard-v2/index.html) como ../frontend/dashboard-app.js. Resolver pelo
// endereço do próprio script serve as duas páginas; um <base href> quebraria os links #/.
// `currentScript` só existe enquanto o script roda, por isso é lido aqui, no topo do módulo.
const SCRIPT = (document.currentScript as HTMLScriptElement | null)?.src || location.href;
export const ICON = new URL("brand/icon.png", SCRIPT).href;
