---
version: 1
slug: "dashboard-v2-index-html"
primary_target: "dashboard-v2/index.html"
related_targets: []
---

# Surface brief: dashboard-v2 (novo dashboard PigBank)

Scope: the authenticated overview dashboard, built first as a standalone prototype
with synthetic data (`dashboard-v2/`, source in `webapp/src/dashboard/`), to later replace
`frontend/dashboard.html`. Mode: Operate. Since 2026-09-23 it is split into one page per
section (hash routes: #/ Resumo with the draggable grid, #/previsao, #/gastos, #/simulador,
#/metas, #/patrimonio, #/lancamentos).

Audience and job: 18–24 PigBank users, often on a phone at night after a purchase or on
payday, answering four questions: where the money goes, how much will be left at month
end, what changes if a habit changes, how goals and savings are progressing.

Constraints: PigBank dark, pink #FF2D8E as accent only, green only for positive, Inter,
Phosphor. No metaphor (user rejected weather/airport/car/circuit/sequencer worlds after two
rounds). Premium and clean, beside Linear, Vercel, Revolut, Apple. Synthetic data labeled.
Subtle color details requested by the owner (goal hues, tinted icon tiles, chart washes).

Unresolved: which interactions survive into production; whether the simulator ships; how
Open Finance CDB positions map to caixinhas (one caixinha can be several CDBs).

## Direction contract

THESIS: One month, one source of truth: the forecast is the page's spine and every other
panel (categories, bills, simulator, goals) writes into it live. Refuses the category default
of four same-size KPI cards over a donut and a line chart.

OWN-WORLD: Near-black graphite planes separated by hairlines, not boxed cards; Inter with
tight display tracking and tabular columns; pink only for now, selection and the primary
action; green only for money gained; a pink sequential ramp for spending intensity (the calendar), while categories keep their own validated categorical hues (color follows the entity, never its rank); Phosphor
regular icons at small size; precise 1px rules and 2px data lines.

STORY: The user sees what will be left, understands which categories are driving it,
drags a cut in the simulator and watches the month bend, then sees the goal arrive earlier.

FIRST VIEWPORT: Left rail of sections. Top: month switcher and command bar. Hero row: the
single hero figure "sobra prevista em 30 set" at ~64px left, balance trajectory chart right
spanning 60% width with realized line, forecast wash band, bill markers and today pulse. A ruled
stats strip under it (entrou, saiu, comprometido). Primary action "Simular" in pink.

FORM: canon (category standard at full craft), chosen by the user after re-roll ×2; seed key e38b9882.
Signature interaction: simulator sliders redraw a second, simulated trajectory on the hero chart
in real time with the gain area shaded, and Piggy insights set the simulator in one click.
Motion grammar: numbers roll, chart draws once on load, 150–250ms state transitions, nothing
decorative loops except the today pulse.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
