// Reconectar um banco (W3, plano em docs/plano-bancos-conectados-painel.md): o widget é o do módulo legado /open-finance-connect.js
// (`PBOpenFinance.reconnect(itemId)`), que a página Bancos já inicia (um `init` só). Aqui ficam a máquina de UM fim por reconexão e os textos de falha.

const PRAZO = 30000; // do clique até o widget iniciar; depois de aberto não há prazo (a pessoa autoriza no banco)

// Como a reconexão falhou: `status` é o HTTP do servidor (o erro do SDK não tem); `tipo` é a falha que o servidor nunca viu;
// `widget` = já depois de o widget abrir (muda só o texto genérico).
export type Falha = { status?: number; msg?: string; tipo?: "prazo" | "ocupado"; widget?: boolean };
// null = a pessoa fechou o widget sem concluir nem errar; `ok` = o banco foi salvo.
export type Resultado = { ok: true } | ({ ok: false } & Falha) | null;

// O que vai à tela. O texto do servidor só nos status em que ele é PT e feito para a pessoa; o 5xx (o 502 traz o `str()` do erro
// da Pluggy) e a rede NUNCA entram. Nem o corpo que não é texto de pessoa: o `apiError` do legado devolve o corpo cru quando não é
// JSON (página HTML de proxy), então nada com `<`, `{` ou `[` (o 402 do gate de plano traz o JSON cru em `detail`) nem mais de 300 caracteres.
// `semId`: a falha não é de um card (o card pode sumir na releitura).
const DO_SERVIDOR = [400, 402, 404, 409, 429];
// O texto do servidor que pode ir à tela (o seletor "+" usa a mesma regra para o toast dele), ou "".
export function textoDoServidor(f: { status?: number; msg?: string }): string {
  const t = f.status !== undefined && DO_SERVIDOR.includes(f.status) ? f.msg?.trim() : "";
  return t && t.length <= 300 && !/[<{[]/.test(t) ? t : "";
}
export function textoDaFalha(f: Falha, nome: string): { texto: string; semId: boolean } {
  if (f.tipo === "ocupado") return { texto: "Já tem uma conexão com banco aberta. Conclua ou feche e tente de novo.", semId: true };
  if (f.status === 403) return { texto: "Não deu para reconectar. Recarregue a página e tente de novo.", semId: true };
  const servidor = textoDoServidor(f);
  if (servidor) return { texto: servidor, semId: f.status === 404 };
  return { texto: f.widget ? `Não deu para concluir a reconexão de ${nome}. Tente de novo.` : `Não deu para abrir a conexão com ${nome}. Tente de novo.`, semId: false };
}

// Uma reconexão. `fim` roda UMA vez (com `semFoco` = o widget segue aberto: o foco não vai ao botão, que está atrás do overlay) e depois
// do fim nada mais chega ao host. Os callbacks são os do módulo, roteados pela página. Quem termina, e como:
//  - `onClose` (fim normal): `null`; o do legado só vem depois do /pluggy-item. Se o SDK mandar `onClose` antes do `onSuccess`, o save ainda
//    acontece e o `onConnected` tardio cai no handler do seletor (relê as listas).
//  - `onConnected` (o legado o chama depois de o /pluggy-item concluir): `{ ok: true }` na hora, `semFoco`: o SDK pode nunca mandar `onClose`.
//  - `onError` com o widget aberto: a falha na hora (`widget: true`), `semFoco`: a Pluggy não garante `onClose` depois dele.
//  - `onError` antes de o widget abrir (o connect-token falhou): a falha. O legado já liberou a guarda de uma conexão por vez.
//  - prazo: termina com `destroy()` (aborta o connect-token em voo, solta a guarda, e a resposta tardia não abre widget).
//  - `reconnect` que devolve `false` (há conexão de outro mount ou do seletor): "ocupado", sem `destroy()`: a alheia segue, e o save dela também.
export function iniciar(fim: (r: Resultado, semFoco: boolean) => void, destruir: () => void) {
  let acabou = false, aberta = false;
  let prazo: ReturnType<typeof setTimeout> | undefined;
  const terminar = (r: Resultado, semFoco = false, destroy = false) => { if (acabou) return; acabou = true; clearTimeout(prazo); if (destroy) destruir(); fim(r, semFoco); };
  prazo = setTimeout(() => terminar({ ok: false, tipo: "prazo" }, false, true), PRAZO);
  return {
    acabou: () => acabou, // depois do fim os callbacks do módulo voltam ao seletor, mesmo com a releitura ainda em voo
    cancelar: () => { acabou = true; clearTimeout(prazo); }, // a página saiu antes de o widget abrir: nada mais é dito
    ocupado: () => terminar({ ok: false, tipo: "ocupado" }),
    onOpen: () => { if (!acabou) { clearTimeout(prazo); aberta = true; } },
    onConnected: () => terminar({ ok: true }, true),
    onError: (msg: string, status?: number) => (aberta ? terminar({ ok: false, status, msg, widget: true }, true) : terminar({ ok: false, status, msg })),
    onClose: () => terminar(null),
  };
}
export type Sessao = ReturnType<typeof iniciar>;
