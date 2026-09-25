// Ponte do protótipo com o painel de chat de produção (frontend/chat-app.js, carregado
// pelo index.html). Sem backend: a pergunta vai para a conversa e o Piggy avisa que só
// responde no app. No corte para produção isto vira `piggyAsk` + `/ai/chat`.
import type { ChatMessage, ChatView } from "../../chat/types";

const NOTICE = "Aqui é a demonstração: no app de verdade eu respondo com os seus dados.";
const messages: ChatMessage[] = [];
let draft = "";
let seq = 0;
let registered = false;

function send(text: string) {
  messages.push({ id: `demo-${++seq}`, role: "user", content: text });
  messages.push({ id: `demo-${++seq}`, role: "assistant", content: NOTICE });
}
function view(): ChatView {
  return {
    title: "Piggy", subtitle: "IA das suas finanças", avatar: "../frontend/brand/stickers/hello.webp",
    messages: messages.map((m) => ({ ...m })), draft, disabled: false,
    emptyText: "Oi! Sou o Piggy. Pergunta o que quiser sobre o seu dinheiro.",
    onDraftChange: (v: string) => { draft = v; render(); },
    onSend: () => { const t = draft.trim(); if (!t) return; draft = ""; send(t); render(); },
  };
}
function render() { window.PigBankChatUI?.update("piggy", view()); }

/** Abre o chat do Piggy; com `question`, ela já vai enviada. */
export function askPiggy(question: string | null) {
  const ui = window.PigBankChatUI as typeof window.PigBankChatUI | undefined; // o bundle do chat pode falhar
  if (!ui) return;
  if (!registered) { ui.register("piggy", view()); registered = true; }
  if (question) send(question);
  render();
  ui.open("piggy");
}
