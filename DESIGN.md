---
name: PigBank Dashboard (dark)
description: The dark overview dashboard where a month of WhatsApp messages to Piggy becomes one forecast you can bend.
colors:
  bg: "#09090b"
  plane-1: "#101013"
  plane-2: "#16161a"
  plane-3: "#1d1d22"
  card-border: "#24242a"
  rule: "rgba(255, 255, 255, 0.07)"
  rule-strong: "rgba(255, 255, 255, 0.12)"
  ink: "#f4f4f6"
  ink-2: "#a8a8b3"
  ink-3: "#8b8b96"
  ink-4: "#5d5d68"
  pink: "#ff2d8e"
  pink-ink: "#ff5aa6"
  pink-wash: "rgba(255, 45, 142, 0.1)"
  pink-line: "rgba(255, 45, 142, 0.45)"
  gain: "#3ddc97"
  gain-wash: "rgba(61, 220, 151, 0.12)"
  warn: "#f5b544"
  warn-wash: "rgba(245, 181, 68, 0.12)"
  alert: "#ff7a45"
  heat-0: "#17161a"
  heat-1: "#2e1623"
  heat-2: "#4d1734"
  heat-3: "#761a4d"
  heat-4: "#a61c67"
  heat-5: "#d92280"
  heat-6: "#ff4f9f"
  cat-mercado: "#3987e5"
  cat-delivery: "#d95926"
  cat-transporte: "#2fa0c8"
  cat-lazer: "#c98500"
  cat-assinaturas: "#9085e9"
  cat-compras: "#e66767"
  cat-moradia: "#7c8391"
  cat-outros: "#5a5f6a"
typography:
  display:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "52px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.035em"
    fontFeature: "\"cv11\", \"ss01\", \"ss03\", \"tnum\""
  headline:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.03em"
    fontFeature: "\"cv11\", \"ss01\", \"ss03\", \"tnum\""
  title:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.012em"
  title-sm:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "-0.012em"
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"cv11\", \"ss01\", \"ss03\""
  body-sm:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 500
  button:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "-0.005em"
  label:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 500
  caption:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "11px"
    fontWeight: 400
    fontFeature: "\"tnum\""
  icon:
    # Icon font, not a text face: Phosphor (regular) from the repo's subset, 15-18px.
    fontFamily: "Phosphor"
    fontSize: "16px"
    fontWeight: 400
rounded:
  panel: "16px"
  ctrl: "10px"
  item: "9px"
  chip: "8px"
  cell: "7px"
  tag: "6px"
  track: "4px"
  round: "50%"
spacing:
  gutter: "40px"
  gutter-md: "28px"
  gutter-sm: "16px"
  section: "20px"
  grid-gap: "14px"
  stack: "12px"
  widget-pad-x: "18px"
  widget-pad-top: "16px"
  cell-gap: "4px"
components:
  button-primary:
    backgroundColor: "{colors.pink}"
    textColor: "#0f172a"
    typography: "{typography.button}"
    rounded: "{rounded.ctrl}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "#ff4a9e"
  button-ghost:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.ctrl}"
    padding: "0 14px"
    height: "36px"
  button-ghost-hover:
    backgroundColor: "{colors.plane-3}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.button}"
    rounded: "{rounded.ctrl}"
    padding: "0 10px"
    height: "36px"
  button-pressed:
    backgroundColor: "{colors.pink-wash}"
    textColor: "{colors.pink-ink}"
  icon-button:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.item}"
    size: "32px"
  chip:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.chip}"
    padding: "0 10px"
    height: "28px"
  chip-selected:
    backgroundColor: "{colors.pink-wash}"
    textColor: "{colors.pink-ink}"
  segmented:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.ctrl}"
    padding: "3px"
  segmented-thumb:
    backgroundColor: "{colors.plane-3}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cell}"
    height: "26px"
  field:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "0 10px"
    height: "32px"
  search-input:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.ctrl}"
    padding: "0 12px 0 34px"
    height: "34px"
    width: "260px"
  tag-demo:
    backgroundColor: "{colors.plane-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.tag}"
    padding: "0 8px"
    height: "22px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.item}"
    padding: "0 12px"
    height: "36px"
  nav-item-active:
    backgroundColor: "{colors.pink-wash}"
    textColor: "{colors.ink}"
  widget:
    backgroundColor: "{colors.plane-1}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "16px 18px 18px"
  tooltip:
    backgroundColor: "rgba(24, 24, 29, 0.96)"
    textColor: "{colors.ink}"
    rounded: "{rounded.ctrl}"
    padding: "10px 12px"
  command-palette:
    backgroundColor: "#141418"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    width: "min(620px, calc(100vw - 32px))"
---

# Design System: PigBank Dashboard (dark)

> Scope: this documents the new dark dashboard (`dashboard-v2/`, source in `webapp/src/dashboard/`), which will later replace `frontend/dashboard.html`. It does not describe the marketing site or the current production dashboard.

## Overview

**Creative North Star: "The Graphite Ledger"**

One month on near-black graphite, read like a well-kept ledger. The forecast is the spine: the hero figure ("Saldo previsto") and its trajectory chart sit at the top, and every other panel (categories, calendar, simulator, bills, goals) writes into that same number. The world is dark by decision, and its "futuristic" feel comes from craft rather than costume. Tight display tracking, tabular figures, 1px rules, 2px data lines and quiet tonal steps carry it. It uses no metaphor and no glowing edges; the only surface gradient is one faint ambient pink behind everything (see Category palette).

The density is high but calm. Panels are small tiles on a 4-column draggable grid. Inside them, divisions are hairlines, never nested boxes, and most text sits at 13px on four steps of ink. Color is scarce and semantic. Pink marks *now*, selection, focus and the one primary action. Green marks money gained. Amber marks spending above pace. A sequential pink ramp shows spending intensity. The eight category hues only identify categories. Every other surface is graphite.

Motion is functional. Numbers roll (NumberFlow), the chart draws once on load, state changes run in 160–240ms, and widgets settle on springs when dragged. Only live indicators loop.

**Key Characteristics:**
- Graphite planes stepped in tone, separated by hairlines at 7% white
- Inter with negative display tracking and tabular numbers everywhere
- Pink for now, selection, focus and the primary action; green for money gained
- A pink sequential heat ramp for spending intensity
- Phosphor regular icons at 15–17px
- 2px data lines, 1px rules, a dashed line for the forecast
- A 4-column tiled widget board that users can rearrange; one column on phones

## Colors

A graphite neutral stack with one brand accent, one gain color and one caution color. Every other hue is data.

### Primary
- **PigBank Pink** (`pink`): the primary action ("Simular"), focus rings, the today marker on the chart and calendar, active nav icons, the filled part of slider tracks, caret and selection.
- **Pink Ink** (`pink-ink`): pink as small text on the dark ground. Used for links ("Ver 90 dias →") and pressed chip or button labels. It is lighter than the brand pink so that small type stays legible.
- **Pink Wash / Pink Line** (`pink-wash`, `pink-line`): the fill and 1px inset stroke for selected or pressed states, and the chart's today line.

### Secondary
- **Ledger Green** (`gain`, `gain-wash`): money gained and nothing else. Used for positive deltas, incoming amounts, the simulated trajectory line and its shaded gain area, and a goal arriving earlier. The Open Finance "live" dot also uses it as a success status.

### Tertiary
- **Pace Amber** (`warn`, `warn-wash`): spending above pace (a category up more than 15%, a negative simulation result) and invoice markers on the chart.
- **Zero Orange** (`alert`): only the chart's zero line, drawn at 55% opacity.

### Neutral
- **Graphite Black** (`bg`): the page, the scrollbar track border and the theme color.
- **Plane 1–3** (`plane-1`, `plane-2`, `plane-3`): elevation by tone. Plane 1 is the widget and ledger surface, plane 2 holds controls, inputs and hover (the active nav item sits on `pink-wash` instead), and plane 3 holds hover-on-controls, meter tracks and the segmented thumb.
- **Card Border** (`card-border`): the 1px inset ring around every widget and the ledger.
- **Rule / Rule Strong** (`rule`, `rule-strong`): hairlines between ledger rows, stat quadrants and the simulator footer. Rule Strong is used on hover and on floating layers.
- **Ink 1–4** (`ink` to `ink-4`): primary text, secondary text (widget titles, descriptions), tertiary text (captions, axis, dt), and a disabled or future state (future calendar days only).

### Spending ramp
- **Heat 0–6** (`heat-0` to `heat-6`): a sequential pink ramp for daily spending in "Dia a dia" and its legend. Steps 1–3 carry `ink-2` numerals and steps 4–6 carry white.

### Category palette
- **Category hues** (`cat-*`): one fixed hue per spending category. It appears on the category icon, the category bar and the simulator lever icon. These hues identify categories and never signal state.
- **Goal hues**: each goal carries one identity hue (Intercâmbio `#3987e5`, Reserva `#d95926`, Notebook `#9085e9`, Festival `#c98500`; this order passes the palette validator on the dark surface, worst adjacent ΔE 26). It colors the goal icon, its progress meter and its timeline bar.
- **Tints**: an identity hue at 13% alpha fills the ledger's icon tile (`tint()` in `lib/format.js`). Chart areas get a vertical wash from the series hue: pink 16%→0 under the realized balance, violet 28%→0 under net worth. The forecast band is pink at 8%.
- **Stat keys**: a 6px dot before each "Resumo do mês" label: Entrou `gain`, Saiu `alert`, Fatura `warn`, Guardado violet `#9085e9` (the same violet as "Caixinhas" in the net-worth composition).
- **Ambient**: one fixed radial pink at 7% near the top right, and violet at 4% near the bottom left, behind all content. It is the only color that is not attached to data or state.

### Named Rules
**The Pink-Is-Now Rule.** Pink means now, selected, focused or "the one thing to press". It never colors an amount, a loss or a decorative surface. Expenses are ink with a minus sign.

**The Green-Is-Money Rule.** Green appears only where the user ends up with more money. Positive values also carry a `+` sign or wording, so color never carries the meaning alone.

**The Data-Hue Rule.** The heat ramp and category hues belong to data marks. Chrome (buttons, nav, panels) stays graphite and pink.

## Typography

**Display Font:** Inter (variable, self-hosted), with -apple-system, BlinkMacSystemFont and "Segoe UI" as fallbacks
**Body Font:** Inter
**Label/Mono Font:** Inter with tabular figures (no separate mono)

**Character:** a single-family ramp. Hierarchy comes from size, weight (400/500/600, with 550 and 650 as variable in-betweens) and tracking that tightens as size grows. The stylistic sets `cv11`, `ss01` and `ss03` are always on.

### Hierarchy
- **Display** (`display`): the one hero figure, the forecast balance. It drops to 40px when the widget is narrower than 440px.
- **Headline** (`headline`): the stat quadrants in "Resumo do mês" (24px when narrow). The simulator result uses the same treatment at 28px.
- **Title** (`title`): section headings such as "Lançamentos".
- **Title small** (`title-sm`): month title, hero facts, tooltip value and bill day numbers.
- **Body** (`body`): the page default.
- **Body small** (`body-sm`): the working size. Used for widget titles in `ink-2`, category, bill, goal and ledger labels (ledger rows use 13.5px), and inputs.
- **Button** (`button`): all buttons.
- **Label** (`label`): chips, legend, captions, day heads and dt terms. Many labels sit at 11.5–12.5px.
- **Caption** (`caption`): chart axes, weekday initials and tab bar labels.

### Named Rules
**The Tabular Rule.** Every number that can change or be compared uses tabular figures (`.num`, or `font-variant-numeric: tabular-nums`).

**The Tighten-With-Size Rule.** Tracking goes from 0 at 13px to -0.012em at 15–17px, -0.03em at 28–30px and -0.035em at 52px. Small text is never tracked negative.

**The No-Uppercase Rule.** Labels are sentence case at 11–13px. No text is uppercase and none is letter-spaced wide.

## Layout

The shell is a sticky left rail (236px) plus a main column. At 1180px and below, the rail collapses to 72px of icons. At 760px and below, it is replaced by a fixed bottom tab bar with 5 items and a safe-area inset. The top bar is sticky, 64px tall (56px on mobile), and uses a translucent, blurred `bg`. Its hairline appears only after the page scrolls. The page column is capped at 1240px, with a 40px gutter (28px at ≤1180px, 16px at ≤760px) and 20px between sections.

The widget board is a 4-column grid with 272px target cells, 14px gaps and a 16px radius. Widgets come in four spans: `sm` 1×1, `wide` 2×1, `tall` 1×2 and `lg` 2×2. The default order tiles 24 cells with no holes. At 640px and below, the board switches to one column with "fit rows": each widget grows to its content, the chart is fixed at 240px, the net-worth chart at 140px and the bills list at 420px max.

Widgets are size containers, and their internals adapt through container queries rather than viewport breakpoints (for example at 440px, 460px and 470px wide, and 230px tall).

The ledger ("Lançamentos") sits below the board as a full-width panel. It has day headers that stick under the top bar and rows on a `34px / 1fr / auto / 104px` grid.

### Named Rules
**The Gap-Free Tiling Rule.** A new widget must fit the default order so the 4-column board still tiles without holes.

**The Container-First Rule.** Widget internals respond to their own box (container queries), never to the viewport.

## Elevation & Depth

Depth is tonal. Surfaces step up from `bg` through plane 1–3, and each raised control carries a 1px top highlight (`inset 0 1px 0 rgba(255,255,255,0.045)`) rather than a drop shadow. Widgets are outlined by a 1px inset ring in `card-border`. Drop shadows exist only for things that float above the page: the tooltip, the command palette, the slider thumb, the segmented thumb, and a widget while it is lifted during a drag.

### Shadow Vocabulary
- **Lift** (`box-shadow: inset 0 1px 0 rgba(255,255,255,0.045)`): widgets, ghost buttons, the segmented track.
- **Segment thumb** (`box-shadow: 0 1px 2px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.06)`): the sliding selection in segmented controls.
- **Tooltip** (`box-shadow: 0 0 0 1px var(--rule-strong), 0 12px 32px -8px rgba(0,0,0,0.7)`): the single floating chart tooltip.
- **Palette** (`box-shadow: 0 0 0 1px var(--rule-strong), 0 30px 80px -20px rgba(0,0,0,0.85)`): the ⌘K command dialog, over a 55% black backdrop.
- **Widget lifted** (`box-shadow: 0px 28px 60px -16px rgba(0,0,0,0.45), 0px 10px 24px -8px rgba(0,0,0,0.3)`): a widget being dragged, scaled to 1.06.

### Named Rules
**The Tonal-Stack Rule.** A surface at rest never gets a drop shadow. Shadows appear only on layers that float above the page, or while an element is held.

## Shapes

Corners are softly rounded and nest. The outer panel radius is 16px, controls are 10px, list items and icon buttons 9px, chips and fields 8px, segment thumbs and calendar cells 7px, tags 6px, and bars and tracks 3–4px. Status dots and slider thumbs are circles. Borders are always 1px and drawn as inset box-shadows or hairlines, never as thick strokes. Selected calendar days use a 2px white inset ring, and today uses a 1.5px pink inset ring. Data lines are 2px with round joins and caps. The forecast line is dashed at 4/5.

## Components

### Buttons
Compact and firm, with no borders.
- **Shape:** gently rounded (`rounded.ctrl`), 36px tall, with Phosphor icons at 16px and an 8px gap.
- **Primary:** pink fill with near-black text `#0f172a` (`button-primary`; the same ink as `--on-purple` in the current dashboard, 5.12:1, where white would give 3.49:1). There is one per screen, "Simular" in the top bar. On mobile it collapses to a 40px icon button.
- **Ghost:** plane 2 with the lift highlight (`button-ghost`), for secondary actions like "Organizar" and "Ver no gráfico".
- **Quiet:** transparent with `ink-2` text (`button-quiet`), for "Zerar" and "Restaurar padrão".
- **Pressed / toggled:** pink wash, pink ink and a 1px inset in `pink-line` (`button-pressed`).
- **Hover / Active:** hover changes only the background tone and applies only under `(hover: hover) and (pointer: fine)`. Active scales to 0.97 (0.94 for icon buttons) over 160ms `ease-out`.

### Chips
- **Style:** plane 2, `ink-2` label, 28px tall (`chip`). They are used for simulator presets, ledger source filters and "Limpar".
- **State:** selected becomes pink wash with pink ink (`chip-selected`).

### Segmented control
A plane-2 track with a plane-3 thumb that slides between options over 240ms `ease-out` (`segmented`, `segmented-thumb`). It is used for the forecast horizon and the ledger filters. The selected label is `ink` and the others are `ink-3`.

### Cards / Containers
- **Corner Style:** `rounded.panel` (16px).
- **Background:** plane 1 (`widget`).
- **Shadow Strategy:** Lift highlight at rest and Widget lifted while dragging (see Elevation).
- **Border:** a 1px inset ring in `card-border`. It becomes a 2px ring at 40% ink for 620ms after a drop.
- **Internal Padding:** 16px top and 18px on the sides and bottom, with 12px stacks. A 30px header holds the title in `ink-2` at 13px/500 and an optional aside on the right.
- **Inside:** no nested cards. Sub-areas are split by `rule` hairlines (the 2×2 stat quadrants, the simulator footer, ledger rows).

### Inputs / Fields
- **Style:** plane 2 with a 1px inset ring in `rule` (`field`, `search-input`), and placeholders in `ink-3`.
- **Focus:** the inset ring turns pink. No glow.
- **Range slider:** a 4px track, pink up to the value and plane 3 after it, with a 16px white thumb ringed by 3px of plane 1. Focus adds a 2px pink outer ring, and dragging scales the thumb to 1.12.

### Navigation
- **Rail:** 36px items at 9px radius in `ink-2` with 17px icons. Hover moves to plane 1 and the active item to plane 2 with an `ink` label and a pink icon. The active item follows scroll through an intersection observer.
- **Tab bar (≤760px):** 5 items with 21px icons over 11px labels. The active item has an `ink` label and a pink icon.
- **Top bar:** month switcher (icon buttons around a 15px/600 title), a "Demonstração" tag, the ⌘K trigger (plane 1, 1px rule ring, 260px wide, icon-only below 1060px), and the primary action.

### Trajectory chart (signature)
The hero chart combines several layers:
- the realized balance as a 2px `ink` line
- the forecast as a 2px dashed line at 55% ink, inside a 6% white "faixa provável" band
- bill and income markers as circles ringed in the card color (green for income, amber for the card invoice)
- a pink today line with a dot and a 2.4s pulse
- when the simulator is active, a green simulated line with the gain area shaded in `gain-wash`

The chart draws once, over 900ms `ease-out`, when the month changes. A crosshair (1px at 32% white) drives a single floating tooltip. The chart also carries a screen-reader table.

### Spending calendar
A 7-column grid of square cells with 7px radius and 4px gaps, filled from the heat ramp. Future days are outlined only. Today has a pink ring and the selected day a white ring. A small ramp legend runs from "menos" to "mais".

### Command palette
A native `<dialog>`, 620px max, at 16px radius on `#141418`. It has a 15px search field, grouped 38px items with the selected item on plane 3 and its icon in pink, and a keyboard-hint footer. It opens with no animation because it is called from the keyboard.

## Do's and Don'ts

### Do:
- **Do** separate content inside a widget with 1px `rule` hairlines, not nested boxes.
- **Do** set every amount in tabular figures, and give gains a `+` or wording as well as green.
- **Do** keep pink to now, selection, focus and the single primary action.
- **Do** use the `heat-*` ramp for any intensity-of-spending encoding and the fixed `cat-*` hue for each category.
- **Do** gate hover styles behind `(hover: hover) and (pointer: fine)` and give every control a `:focus-visible` state (2px pink outline, 2px offset).
- **Do** use 160ms (`--t-fast`) or 240ms (`--t-mid`) with `cubic-bezier(0.23, 1, 0.32, 1)` for state changes, and honor `prefers-reduced-motion` (all durations collapse to 1ms).
- **Do** label synthetic data ("Dados de demonstração", "Demonstração") wherever demo numbers appear.

### Don't:
- **Don't** put a drop shadow on a surface at rest. Depth is plane tone plus the 1px lift.
- **Don't** color expenses or losses pink or red. An expense is ink with a minus sign, and above-pace spending is amber.
- **Don't** use a category hue or heat step on chrome such as buttons, nav or panel backgrounds.
- **Don't** loop an animation unless it signals a live state (the today pulse, the sync dot).
- **Don't** uppercase or wide-track labels.
- **Don't** add a metaphor layer (weather, cockpit, circuits). The owner rejected it; the finance dashboard stays literal.
