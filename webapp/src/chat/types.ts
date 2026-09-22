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
  portfolio?: {
    count: number;
    amount: number;
    note: string;
    groups: { title: string; amount: number; items: { name: string; institution: string; amount: number }[] }[];
  };
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
  onPortfolioAsk?: (question: string) => void;
  onFeedback?: (messageId: string, value: "up" | "down" | "dismissed") => void;
  onHidden?: () => void;
}

export type ChatId = "piggy" | "agent";
