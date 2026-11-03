// A troca de conta no /painel. O servidor grava o cookie `pb_conta=<uid>` (legível, nunca lido por ele) ao emitir a sessão, ao
// encerrá-la e ao servir o /painel (`marcar_conta`, frontend/routes/shared.py). Ele é SÓ um sinal de mudança: quem diz de quem é a
// sessão é o `dashboard_token` HttpOnly. Todo ponto em que um dado pode entrar na tela ou no cache confere o marcador visto na carga;
// mudou ou sumiu, a página some e recarrega uma vez (o servidor relê a sessão e o cache vai junto).
const NOME = "pb_conta=";

export class SessaoTrocada extends Error {}

const marca = () => document.cookie.split("; ").find((c) => c.startsWith(NOME))?.slice(NOME.length) ?? null;

let vista = marca();
// Sessão de antes do deploy (HTML sem o marcador): o uid confirmado pelo servidor na carga, para adotar o marcador quando ele aparecer.
let dona: string | null = null;
let saindo = false;

function trocou(): never {
  saindo = true;
  document.documentElement.style.visibility = "hidden";
  location.reload();
  throw new SessaoTrocada();
}

export function conferir() {
  if (saindo) throw new SessaoTrocada();
  const m = marca();
  if (m === vista) return;
  if (vista === null && m === dona) { vista = m; return; }
  trocou();
}

// Logout (o "Sair" daqui ou o de outra aba): a página já está indo embora, e o reload da troca (o marcador some com a
// sessão) cancelaria essa navegação. Com a trava ligada, nada mais recarrega. O "Sair" daqui liga a trava já no POST (`travar`): a
// resposta apaga o marcador e o auth-refresh.js ainda limpa o aparelho antes de devolvê-la, tempo de sobra para o intervalo recarregar.
export const travar = () => { saindo = true; };
export function sairPara(url: string) {
  travar();
  location.replace(url);
}

export async function buscar(input: RequestInfo, init?: RequestInit) {
  conferir();
  const r = await fetch(input, init);
  conferir();
  return r;
}

export function vigiar() {
  if (vista === null) {
    void fetch("/auth/dashboard-profile", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((p: { user_id?: number } | null) => { if (p?.user_id != null) dona = String(p.user_id); }, () => undefined);
  }
  setInterval(() => { try { conferir(); } catch { /* recarregando */ } }, 1000);
  const barra = (e: Event) => { try { conferir(); } catch { e.preventDefault(); e.stopImmediatePropagation(); } };
  window.addEventListener("keydown", barra, true);
  // O `<input type="range">` muda de valor no mousedown/touchstart, antes de qualquer click (e no arrasto por toque não há click).
  window.addEventListener("pointerdown", barra, true);
  window.addEventListener("click", barra, true);
  window.addEventListener("visibilitychange", barra, true);
  // Saindo, nenhum `beforeunload` (o do Cofre do MFA) pergunta: "Cancelar" deixaria a página escondida e travada. O dono aceitou perder
  // os códigos na tela. Em captura no `window`, roda antes dos ouvintes comuns dele.
  window.addEventListener("beforeunload", (e) => { if (saindo) e.stopImmediatePropagation(); }, true);
}
