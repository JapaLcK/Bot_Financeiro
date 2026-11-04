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
let saindo = false; // a página já sumiu: todo conferir lança
let logout: string | null = null; // o destino da saída do logout, já pedida: a troca esconde e lança, mas não recarrega

// Na troca ou no logout, o `beforeunload` do Cofre do MFA (o único do /painel) não pergunta: "Cancelar" deixaria a página escondida
// e travada. O dono aceitou perder os códigos na tela. É ele que consulta isto: um ouvinte de `beforeunload` aqui, desde a carga,
// atrasaria toda navegação do /painel (o navegador consulta a página antes de sair), e um registrado só na saída rodaria depois do dele
// (no `window`, a ordem é a de registro, com ou sem captura).
export const deSaida = () => saindo || logout !== null;

function trocou(): never {
  saindo = true;
  document.documentElement.style.visibility = "hidden";
  // No logout, o prazo só arma depois que o marcador mudou, isto é, depois da resposta do servidor: se a limpeza do auth-refresh.js
  // travar, a página não fica escondida para sempre. O que se perde é só a limpeza do SW/Cache Storage (o storage já foi apagado,
  // de forma síncrona, antes).
  if (logout === null) location.reload();
  else { const url = logout; setTimeout(() => location.replace(url), 3000); }
  throw new SessaoTrocada();
}

export function conferir() {
  if (saindo) throw new SessaoTrocada();
  const m = marca();
  if (m === vista) return;
  if (vista === null && m === dona) { vista = m; return; }
  trocou();
}

// Logout (o "Sair" daqui ou o de outra aba): a página vai embora, e o reload da troca (o marcador some com a sessão) cancelaria
// essa navegação. A trava tira SÓ o reload: enquanto o marcador não muda, a página segue lendo e respondendo a gestos (o POST pode
// demorar); mudou ou sumiu, ela some e lança como na troca, e quem navega é a saída (ou o prazo do `trocou`, se ela travar). O
// "Sair" daqui liga a trava já no POST (`travar`): a resposta apaga o marcador e o auth-refresh.js ainda limpa o aparelho antes de
// devolvê-la.
export const travar = (url: string) => { logout = url; };
export function sairPara(url: string) {
  travar(url);
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
  setInterval(() => { try { conferir(); } catch { /* a página sumiu */ } }, 1000);
  const barra = (e: Event) => { try { conferir(); } catch { e.preventDefault(); e.stopImmediatePropagation(); } };
  window.addEventListener("keydown", barra, true);
  // O `<input type="range">` muda de valor no mousedown/touchstart, antes de qualquer click (e no arrasto por toque não há click).
  window.addEventListener("pointerdown", barra, true);
  window.addEventListener("click", barra, true);
  window.addEventListener("visibilitychange", barra, true);
}
