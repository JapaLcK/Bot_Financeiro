import { useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { ChatMessages } from "@/components/ui/chat-messages";
import { chatStore, chatUI } from "./store";
import type { ChatId } from "./types";
import "./chat.css";

function Chats() {
  const state = useSyncExternalStore(chatStore.subscribe, chatStore.getSnapshot);
  return <>{(["piggy", "agent"] as ChatId[]).map(id => {
    const view = state.views[id];
    return view && <ChatMessages key={`${id}:${view.conversationId || id}`} id={id} view={view} active={state.active === id}
      onClose={() => chatUI.close(id)} />;
  })}</>;
}

const host = document.getElementById("pigbank-chat-root");
if (host) {
  // Fixo fora de pais com transform/overflow do dashboard.
  document.body.appendChild(host);
  const root = createRoot(host);
  flushSync(() => root.render(<Chats />));
  window.PigBankChatUI = chatUI;
}
