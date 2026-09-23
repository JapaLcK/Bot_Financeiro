import { createContext, useContext, type ReactNode } from "react";
import { href, route, type Path } from "../router";

// No Resumo cada bloco aponta para a sua página; nas páginas o contexto fica vazio.
export const FrameLink = createContext<Path | null>(null);

// Moldura comum dos widgets: título, ação à direita e corpo.
export function Frame({ id, title, aside, children, className = "" }: {
  id: string;
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const to = useContext(FrameLink);
  return (
    <article className={`w ${className}`} id={`w-${id}`} aria-labelledby={`w-${id}-h`}>
      <header className="w-head">
        <h2 id={`w-${id}-h`} className="w-title">{title}</h2>
        {aside && <div className="w-aside">{aside}</div>}
        {to && (
          <a className="w-open" href={href(to)} aria-label={`Abrir ${route(to).label}`}>
            <i className="ph ph-arrow-right" aria-hidden="true" />
          </a>
        )}
        <svg className="w-grip" width="10" height="16" viewBox="0 0 10 16" aria-hidden="true">
          {[3, 8, 13].map((y) => [2.5, 7.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.4" />))}
        </svg>
      </header>
      <div className="w-body">{children}</div>
    </article>
  );
}
