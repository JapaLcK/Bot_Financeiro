import * as React from "react";

import { cn } from "@/lib/utils";

export const Message = React.forwardRef<
  HTMLDivElement,
  React.ComponentProps<"div"> & { align?: "start" | "end" }
>(function Message({ className, align = "start", ...props }, ref) {
  return (
    <div
      ref={ref}
      data-slot="message"
      data-align={align}
      className={cn("pc-chat-message", className)}
      {...props}
    />
  );
});

export function MessageContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="message-content"
      className={cn("pc-message-content-wrap", className)}
      {...props}
    />
  );
}

export function MessageFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="message-footer"
      className={cn("pc-message-footer", className)}
      {...props}
    />
  );
}

export function Bubble({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"div"> & {
  variant?: "default" | "muted" | "ghost";
}) {
  return (
    <div
      data-slot="bubble"
      data-variant={variant}
      className={cn("pc-message-bubble", variant === "muted" && "pc-message-user", className)}
      {...props}
    />
  );
}

export function BubbleContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="bubble-content"
      className={cn("pc-bubble-content", className)}
      {...props}
    />
  );
}

export function Marker({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="marker" className={cn("pc-message-marker", className)} {...props} />;
}

export function MarkerIcon({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="marker-icon" className={cn("pc-marker-icon", className)} {...props} />;
}

export function MarkerContent({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="marker-content" className={cn("pc-marker-content", className)} {...props} />;
}
