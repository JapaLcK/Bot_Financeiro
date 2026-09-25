import { useState } from "react";
import type { WidgetItem } from "@/components/ui/draggable-widget-grid";
import { PLAN } from "../lib/api";
import { PROFILES, locked, tierOf } from "../lib/profiles.js";

// Qual perfil monta o Resumo. Sempre visível; trocar leva ao layout daquele perfil.
export function ProfileSelect({ value, onPick }: { value: string; onPick: (p: string) => void }) {
  return (
    <label className="board-profile">
      <span>Painel</span>
      <select id="board-profile" className="field" value={value} onChange={(e) => onPick(e.target.value)}>
        <option value="padrao">Padrão</option>
        {PROFILES.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
    </label>
  );
}

const TIER_NAME: Record<string, string> = { plus: "Plus", pro: "Pro" };

// Blocos fora do painel, só no Organizar. Os do plano acima aparecem com cadeado e
// não entram. Depois de adicionar, o foco fica na lista (o item some dela).
export function Catalog({ missing, onAdd }: { missing: WidgetItem[]; onAdd: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="catalog">
      <button id="board-add" type="button" className="btn btn-ghost" aria-expanded={open} aria-controls="board-catalog" onClick={() => setOpen(!open)}>
        <i className="ph ph-plus" aria-hidden="true" />Adicionar bloco
      </button>
      {open && (
        <ul id="board-catalog" className="catalog-list" aria-label="Blocos fora do painel">
          {missing.length === 0 && <li className="faint">Todos os blocos já estão no painel.</li>}
          {missing.map((w) => {
            const off = locked(w.id, PLAN);
            return (
              <li key={w.id}>
                <button type="button" className="catalog-item" aria-disabled={off || undefined}
                  onClick={(e) => {
                    if (off) return;
                    const li = e.currentTarget.parentElement;
                    const to = (li?.nextElementSibling ?? li?.previousElementSibling)?.querySelector("button");
                    onAdd(w.id);
                    requestAnimationFrame(() => (to ?? document.getElementById("board-add"))?.focus());
                  }}>
                  <i className={`ph ${off ? "ph-lock" : "ph-plus"}`} aria-hidden="true" />
                  <span>{w.label}</span>
                  {off && <span className="catalog-tier">No {TIER_NAME[tierOf(w.id)]}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
