import { flushSync } from "react-dom";
import type { ChatId, ChatView } from "./types";

type Snapshot = { active: ChatId | null; views: Partial<Record<ChatId, ChatView>> };
let snapshot: Snapshot = { active: null, views: {} };
let opener: HTMLElement | null = null;
const listeners = new Set<() => void>();
const previousInert = new Map<HTMLElement, boolean>();
const sidebarMedia = window.matchMedia("(min-width: 901px) and (hover: hover) and (pointer: fine)");

function showsSidebarRail() {
  return sidebarMedia.matches && !document.documentElement.classList.contains("pb-app");
}

function syncSidebarInert() {
  if (snapshot.active !== "agent") return;
  const sidebar = document.getElementById("sidenav");
  if (sidebar) sidebar.inert = !showsSidebarRail();
}

sidebarMedia.addEventListener("change", syncSidebarInert);

function publish(next: Snapshot) {
  if (next.active === "agent" && snapshot.active !== "agent") {
    for (const child of document.body.children) {
      if (!(child instanceof HTMLElement) || child.id === "pigbank-chat-root") continue;
      previousInert.set(child, child.inert);
      child.inert = child.id === "sidenav" ? !showsSidebarRail() : true;
    }
  } else if (next.active !== "agent" && snapshot.active === "agent") {
    for (const [child, inert] of previousInert) {
      child.inert = child.id === "sidenav"
        ? !showsSidebarRail() && !child.classList.contains("open")
        : inert;
    }
    previousInert.clear();
  }
  snapshot = next;
  syncSidebarInert();
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
  close(id: ChatId, restoreFocus = true) {
    if (snapshot.active !== id) return;
    snapshot.views[id]?.onHidden?.();
    publish({ ...snapshot, active: null });
    if (restoreFocus && opener?.isConnected) opener.focus();
  },
  isOpen: (id: ChatId) => snapshot.active === id,
  focusInput(id: ChatId) {
    if (snapshot.active !== id) return;
    const target = panel(id)?.querySelector<HTMLElement>("textarea:not(:disabled)")
      || panel(id)?.querySelector<HTMLElement>("[data-chat-close]");
    target?.focus({ preventScroll: true });
  },
};

document.getElementById("sidenav")?.addEventListener("click", event => {
  if (snapshot.active !== "agent") return;
  const item = event.target instanceof Element ? event.target.closest(".sidenav-item") : null;
  if (!item || item.classList.contains("pro-locked")) return;
  if (item instanceof HTMLButtonElement && item.dataset.nav) {
    chatUI.close("agent", false);
  } else if (item instanceof HTMLAnchorElement && (!item.target || item.target === "_self")
    && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
    chatUI.close("agent", false);
  }
});

document.addEventListener("click", event => {
  if (snapshot.active !== "agent" || !(event.target instanceof Element)) return;
  if (event.target.closest("#sidenav .sidenav-item.pro-locked[data-pro-feature]")) {
    chatUI.close("agent", false);
  }
}, true);

declare global {
  interface Window { PigBankChatUI: typeof chatUI; }
}
