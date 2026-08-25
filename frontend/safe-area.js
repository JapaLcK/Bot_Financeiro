/**
 * safe-area.js — reserva de área segura para as páginas que NÃO carregam o
 * app-mode (precos, landing, legal, blog…).
 *
 * Por que existe: o WebView do app usa contentInset "never", então ele vai
 * até a borda física da tela em TODAS as páginas do mesmo domínio — e o
 * allowNavigation do capacitor.config permite qualquer rota de pigbankai.com.
 * O app-mode.css só é carregado por seis páginas; sem este shim, as outras
 * (ex.: /precos, alcançável pelo "Ver planos" do dashboard) desenhariam sob
 * o status bar, o notch e o home indicator.
 *
 * Diferença pro app-mode: aqui NÃO há reskin. Nada de esconder nav, footer
 * ou montar tab bar — só o respiro das bordas. A landing, por exemplo,
 * precisa continuar mostrando a nav dela (é o caminho de volta pro app).
 *
 * Inerte fora do app: sem o user agent do app e sem ícone instalado, não faz
 * nada. Navegador comum não tem como entrar — o preview ?pbapp=1 foi removido.
 */
(() => {
  const standalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;

  // Mesma condição do app-mode.js, repetida na unha: a página que carrega os
  // dois (changelog.html) põe ESTE script antes, então não dá pra ler a classe
  // pb-app dele — e /precos & landing carregam só este. Se mudar aqui, mude lá.
  //
  // Não há assimetria entre os dois arquivos: nenhum dos dois GRAVA nada, e
  // nenhum LÊ storage ou query pra decidir. A condição é puramente derivada do
  // ambiente (UA do app nativo ou ícone instalado), então os dois chegam sempre
  // à mesma resposta sem precisar combinar estado. A única escrita que sobrou
  // no par mora no app-mode.js e é uma limpeza: removeItem da chave legada
  // localStorage.pbApp, do preview ?pbapp=1 que foi removido.
  const inApp = /PigBankApp/.test(navigator.userAgent) || standalone;
  if (!inApp) return;

  document.documentElement.classList.add("pb-safe");

  // viewport-fit=cover é o que faz env(safe-area-inset-*) reportar valor real
  const m = document.querySelector('meta[name="viewport"]');
  if (m && !/viewport-fit/.test(m.content)) m.content += ", viewport-fit=cover";

  const css = document.createElement("style");
  css.textContent =
    "html.pb-safe body{" +
    "padding-top:env(safe-area-inset-top);" +
    "padding-right:env(safe-area-inset-right);" +
    "padding-bottom:env(safe-area-inset-bottom);" +
    "padding-left:env(safe-area-inset-left);" +
    "box-sizing:border-box}" +
    // A nav do site é sticky com top:16px (site.css): quando gruda, o padding
    // do body já rolou pra fora, então ela precisa do inset no próprio top.
    "html.pb-safe .nav{top:calc(16px + env(safe-area-inset-top));" +
    "padding-left:max(18px,env(safe-area-inset-left));" +
    "padding-right:max(18px,env(safe-area-inset-right))}" +
    // Fixos presos à base: margin-bottom empurra pra cima SEM sobrescrever o
    // "bottom" que cada página escolheu (o /precos usa 26px, por exemplo).
    "html.pb-safe #toast,html.pb-safe .toast{" +
    "margin-bottom:env(safe-area-inset-bottom)}";
  document.head.appendChild(css);
})();
