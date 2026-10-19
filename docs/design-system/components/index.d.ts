import type { ReactNode } from "react";

/** A color token name from tokens.json, e.g. "ink", "ink-muted", "brand-ink", "positive". */
type Tone = string;
type IconName =
  | "CaretRight" | "CaretLeft" | "CaretDown" | "ArrowLeft" | "ArrowUp" | "ArrowDown" | "ArrowSquareOut" | "ArrowsClockwise"
  | "Wallet" | "Bank" | "CreditCard" | "PiggyBank" | "ChartBar" | "Target" | "ListBullets" | "CalendarBlank" | "ClockCounterClockwise"
  | "Bell" | "CheckCircle" | "Info" | "WarningCircle" | "WarningOctagon" | "LinkBreak" | "PauseCircle" | "Trash" | "Question"
  | "Envelope" | "Lock" | "Eye" | "EyeSlash" | "UserCircle" | "Phone" | "WhatsappLogo" | "GoogleLogo" | "ChatCircle"
  | "Minus" | "Plus" | "X" | "House" | "Gear" | "SlidersHorizontal" | "Copy" | "ThumbsUp" | "ThumbsDown" | "Stop";
interface Action { label: string; onClick: () => void }

export interface TextProps { variant?: "display" | "title" | "section" | "body" | "label" | "caption"; tone?: Tone; numeric?: boolean; as?: string; style?: Record<string, string | number>; children: ReactNode }
export interface IconProps { name: IconName; size?: number; tone?: Tone; /** A data hue; wins over tone. */ color?: string }
export interface ButtonProps { variant?: "primary" | "secondary" | "ghost" | "link" | "danger"; size?: "M" | "L"; loading?: boolean; disabled?: boolean; icon?: IconName; onClick?: () => void; children: ReactNode }
export interface ChipProps { selected?: boolean; disabled?: boolean; onClick?: () => void; children: ReactNode }
export interface SegmentedControlProps { options: string[]; value: string; onChange?: (value: string) => void }
export interface InputProps { label: string; value: string; onChange?: (value: string) => void; error?: string; icon?: IconName; placeholder?: string; type?: string; disabled?: boolean; readOnly?: boolean }
export interface AmountInputProps { label: string; /** Integer centavos. */ cents: number; onChange: (cents: number) => void; error?: string; disabled?: boolean }
export interface CardProps { elevation?: "surface" | "raised"; style?: Record<string, string | number>; children: ReactNode }
export interface InsightCardProps { title: string; message: string; /** URL of an official sticker. */ sticker?: string; action?: Action }
export interface BannerProps { tone?: "info" | "warning" | "danger"; title?: string; message: string; action?: Action; onClose?: () => void }
export interface ToastProps { tone?: "sucesso" | "info" | "aviso" | "erro"; message: string }
export interface EmptyStateProps { /** URL of an official sticker. */ sticker: string; message: string; action?: Action }
export interface ConnectionStatusProps {
  state: "updated" | "partial" | "updating" | "error_recoverable" | "needs_user_action" | "item_missing" | "paused" | "removed" | "no_accounts" | string;
  label: string; detail?: string; action?: Action;
}
export interface ListRowProps { title: string; subtitle?: string; icon?: IconName; trailing?: ReactNode; chevron?: boolean; onClick?: () => void; tone?: "danger"; disabled?: boolean; loading?: boolean }
export interface ListGroupProps { children: ReactNode }
export interface TransactionRowProps { title: string; category: string; date?: string; cents: number; type: "entrada" | "saida"; hidden?: boolean }
export interface MoneyProps { /** Integer centavos. */ cents: number; type?: "saldo" | "entrada" | "saida"; variant?: "display" | "body" | "label"; hidden?: boolean }
export interface AvatarProps { name: string; image?: string; size?: number }
export interface ProgressBarProps { /** 0 to 1. */ value: number; tone?: "brand" | "positive" | "warning" | "danger"; /** A data hue; wins over tone. */ color?: string }
export interface SkeletonProps { width: number | string; height: number; radius?: number | string }
export interface CompositionBarProps { segments: { value: number; color: string }[] }
export interface MonthlyBarsProps { months: { key: string; label: string; value: number; current?: boolean; dimmed?: boolean }[]; formatValue?: (cents: number) => string }
export interface CumulativeCurveProps { points: number[]; previous?: number[] | null; daysInMonth: number; todayIndex: number | null; tone?: "ink" | "positive" | "warning" }
export interface ScreenProps { children: ReactNode }
export interface SheetProps { children: ReactNode }
