import { useLayoutEffect, useRef, useSyncExternalStore } from "react";

// Um tooltip só para a página inteira. Conteúdo estruturado (nada de HTML cru):
// rótulos vêm de dados e entram como texto.
export interface TipRow { label: string; value: string; color?: string }
export interface TipContent { title: string; value?: string; rows?: TipRow[]; note?: string }
interface TipState { x: number; y: number; content: TipContent | null }

let tip: TipState = { x: 0, y: 0, content: null };
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export function showTip(x: number, y: number, content: TipContent) {
  tip = { x, y, content };
  emit();
}
export function hideTip() {
  if (!tip.content) return;
  tip = { ...tip, content: null };
  emit();
}

export function Tip() {
  const state = useSyncExternalStore(
    (f) => { subs.add(f); return () => subs.delete(f); },
    () => tip,
  );
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !state.content) return;
    const { width, height } = el.getBoundingClientRect();
    const pad = 12;
    let x = state.x + 14;
    let y = state.y - height - 12;
    if (x + width > window.innerWidth - pad) x = state.x - width - 14;
    if (y < pad) y = state.y + 18;
    el.style.transform = `translate(${Math.max(pad, x)}px, ${y}px)`;
  }, [state]);

  const c = state.content;
  return (
    <div ref={ref} className="tip" role="tooltip" hidden={!c}>
      {c && (
        <>
          <p className="tip-title">{c.title}</p>
          {c.value && <p className="tip-value">{c.value}</p>}
          {c.rows?.map((r, i) => (
            <p className="tip-row" key={i}>
              {r.color && <span className="tip-key" style={{ background: r.color }} />}
              <span>{r.label}</span>
              <b>{r.value}</b>
            </p>
          ))}
          {c.note && <p className="tip-row">{c.note}</p>}
        </>
      )}
    </div>
  );
}
