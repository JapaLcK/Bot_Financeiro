export interface ChatAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt?: string;
  state?: "pending" | "error" | "complete";
  author?: string;
  markdown?: boolean;
  actions?: ChatAction[];
  feedback?: "up" | "down" | "dismissed";
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
  greeting?: string;
  suggestions?: ChatAction[];
  status?: string;
  actions?: ChatAction[];
  usage?: string;
  usageTone?: "normal" | "warning" | "error";
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onFeedback?: (messageId: string, value: "up" | "down" | "dismissed") => void;
  onHidden?: () => void;
}

export type ChatId = "piggy" | "agent";
