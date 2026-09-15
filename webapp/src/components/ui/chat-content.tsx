import { Fragment, type ReactNode } from "react";

// O Piggy já aceita este subconjunto de Markdown. React escapa o texto;
// somente destinos http(s) e caminhos locais viram links clicáveis.
export function ChatContent({ content, markdown }: { content: string; markdown?: boolean }) {
  if (!markdown) return <>{content}</>;
  const tokens = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const match of content.matchAll(tokens)) {
    const start = match.index!;
    parts.push(content.slice(cursor, start));
    if (match[1]) parts.push(<strong key={start}>{match[1]}</strong>);
    else if (match[2]) parts.push(<code key={start}>{match[2]}</code>);
    else {
      const href = match[4].trim();
      const safe = /^(https?:\/\/|\/(?!\/))[^\s\\]*$/i.test(href);
      parts.push(safe
        ? <a key={start} href={href} target="_blank" rel="noopener noreferrer">{match[3]}</a>
        : <Fragment key={start}>{match[3]}</Fragment>);
    }
    cursor = start + match[0].length;
  }
  parts.push(content.slice(cursor));
  return <>{parts}</>;
}
