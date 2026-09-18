import { flushSync } from "react-dom";
import type { ChatId, ChatView } from "./types";

type Snapshot = { active: ChatId | null; views: Partial<Record<ChatId, ChatView>> };
let snapshot: Snapshot = { active: null, views: {} };
let opener: HTMLElement | null = null;
const listeners = new Set<() => void>();
const previousInert = new Map<HTMLElement, boolean>();

function publish(next: Snapshot) {
  if (next.active === "agent" && snapshot.active !== "agent") {
    for (const child of document.body.children) {
      if (!(child instanceof HTMLElement) || child.id === "pigbank-chat-root") continue;
      previousInert.set(child, child.inert);
      child.inert = true;
    }
  } else if (next.active !== "agent" && snapshot.active === "agent") {
    for (const [child, inert] of previousInert) child.inert = inert;
    previousInert.clear();
  }
  snapshot = next;
  document.documentElement.classList.toggle("pb-chat-open", Boolean(next.active));
  document.documentElement.classList.toggle("pb-agent-chat-open", next.active === "agent");
  document.getElementById("piggy-fab")?.classList.toggle("open", next.active === "piggy");
  flushSync(() => listeners.forEach(listener => listener()));
}

function panel(id: ChatId) {
  return document.getElementById(id === "piggy" ? "piggy-panel" : "agent-chat-panel");
}

export const chatStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot: () => snapshot,
};

export const chatUI = {
  register(id: ChatId, view: ChatView) {
    publish({ ...snapshot, views: { ...snapshot.views, [id]: view } });
  },
  update(id: ChatId, view: ChatView) {
    publish({ ...snapshot, views: { ...snapshot.views, [id]: view } });
  },
  open(id: ChatId) {
    if (!snapshot.views[id]) return;
    const previous = snapshot.active;
    const focused = document.activeElement;
    if (focused instanceof HTMLElement && focused !== document.body
      && (!previous || !panel(previous)?.contains(focused))) opener = focused;
    if (previous && previous !== id) snapshot.views[previous]?.onHidden?.();
    publish({ ...snapshot, active: id });
    chatUI.focusInput(id);
  },
  close(id: ChatId) {
    if (snapshot.active !== id) return;
    snapshot.views[id]?.onHidden?.();
    publish({ ...snapshot, active: null });
    if (opener?.isConnected) opener.focus();
  },
  isOpen: (id: ChatId) => snapshot.active === id,
  focusInput(id: ChatId) {
    if (snapshot.active !== id) return;
    const target = panel(id)?.querySelector<HTMLElement>("textarea:not(:disabled)")
      || panel(id)?.querySelector<HTMLElement>("[data-chat-close]");
    target?.focus({ preventScroll: true });
  },
};

declare global {
  interface Window { PigBankChatUI: typeof chatUI; }
}
