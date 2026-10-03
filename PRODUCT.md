# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Young adults, 18–24, managing their own money for the first time. They log spending
by talking to Piggy on WhatsApp during the day; the web dashboard is where they come
back to understand what that stream of messages adds up to. Often on a phone, often
at night, often right after a purchase or on payday.

## Product Purpose

PigBank is a financial assistant that lives in WhatsApp. The user records expenses,
income, bills and card purchases by message (text, audio, photo), optionally syncs
bank accounts through Open Finance, and Piggy categorizes, reminds and answers.

The dashboard's job is comprehension, not bookkeeping. Success means the user can
answer four questions within seconds: where the money is going, how much will be
left at the end of the month, what happens if they change a habit, and how their
goals and savings are progressing.

## Positioning

The data arrives by conversation, not by form. The dashboard is the other half of a
chat: it turns what the user told Piggy into a picture, and hands actions back to
WhatsApp or to Piggy instead of making the user fill spreadsheets.

## Operating Context

- Data sources: launches (manual and Open Finance), categories and category rules,
  budgets, credit cards with invoices and installments, recurring bills / expenses /
  incomes, pockets ("caixinhas") with goals, investments, cash-flow forecast, and an
  LLM-generated monthly insight.
- Real endpoints live under `/{resource}/{user_id}` (see `frontend/dashboard.js`).
  Every query is filtered by `user_id`; the dashboard never shows cross-user data.
- The current dashboard is `frontend/dashboard.html` + `frontend/dashboard.js`.

## Capabilities and Constraints

- The new dashboard is being built first as a standalone prototype with synthetic
  data, and will later replace the current dashboard. Replacement must preserve
  every existing function (Pix, cards, Open Finance, pockets, recurring items).
- Locale pt-BR, currency BRL, dates dd/mm.
- There is no free plan; all users are on paid plans.
- Undecided: which interactions of the prototype survive into production, and
  whether the simulator becomes a real feature.

## Brand Commitments

- Name PigBank; mascot Piggy; the agents of Piggy may appear. Only official assets
  from `frontend/brand/`.
- Primary color `#FF2D8E`, used as accent, not on every surface. `#C7186B` is the
  text-safe ink of the pink on light grounds.
- Base in black, white and neutrals. Green only for success, growth, positive values.
- Inter is the product typeface (self-hosted in `frontend/fonts/`); Phosphor icons.
- Voice: smart, young, friendly, a little irreverent, trustworthy. Playful but not
  childish.
- User decision (2026-09-23): the new dashboard is PigBank in dark mode, futuristic
  and technological.
- User decision (2026-09-23), after two rejected direction rounds: no metaphor. The
  dashboard is a finance dashboard, not dressed as weather, airports or cars. The
  "futuristic" comes from craft, not costume. Bar: premium and clean, sitting beside
  Linear, Vercel, Revolut and Apple.

## Evidence on Hand

- Official assets: `frontend/brand/` (logo, icon, mascot, agents, stickers).
- No real user data may be used in prototypes; demonstration data must be labeled
  synthetic. No invented testimonials, benchmarks or features.

## Product Principles

1. Financial clarity beats visual impact: numbers read first, decoration never
   competes with them.
2. Every number answers a question the user actually has.
3. Show the future, not only the past: projection and simulation are first-class.
4. Actions go back to the conversation: Piggy is one tap away.
5. Honest states: empty, loading and error are designed, never faked as success.

## Accessibility & Inclusion

WCAG AA contrast, keyboard navigation, visible focus, `prefers-reduced-motion`
respected, color never the only carrier of meaning (gains and losses also carry sign
and wording).
