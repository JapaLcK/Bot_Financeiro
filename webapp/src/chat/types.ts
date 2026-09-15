export interface ChatAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  state?: "pending" | "error" | "complete";
  author?: string;
  markdown?: boolean;
  actions?: ChatAction[];
}

export interface ChatView {
  conversationId?: string;
  title: string;
  subtitle: string;
  avatar: string;
  draft: string;
  messages: ChatMessage[];
  disabled: boolean;
  emptyText: string;
  suggestions?: ChatAction[];
  status?: string;
  actions?: ChatAction[];
  usage?: string;
  usageTone?: "normal" | "warning" | "error";
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onHidden?: () => void;
}

export type ChatId = "piggy" | "agent";
