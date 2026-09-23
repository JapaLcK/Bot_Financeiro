import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

// Controle segmentado (radiogroup) com um indicador que desliza até a opção marcada.
export function Seg<T extends string>({ label, options, value, onChange }: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState({ x: 0, w: 0 });

  useLayoutEffect(() => {
    const btn = wrap.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]');
    if (btn) setThumb({ x: btn.offsetLeft - 3, w: btn.offsetWidth });
  }, [value, options.length]);

  const onKey = (e: KeyboardEvent) => {
    const i = options.findIndex((o) => o.value === value);
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = options[(i + step + options.length) % options.length];
    onChange(next.value);
    requestAnimationFrame(() => wrap.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus());
  };

  return (
    <div className="seg" role="radiogroup" aria-label={label} ref={wrap} onKeyDown={onKey}>
      <span className="seg-thumb" aria-hidden="true" style={{ width: thumb.w, transform: `translateX(${thumb.x}px)` }} />
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          tabIndex={o.value === value ? 0 : -1}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
