/**
 * Tailwind da ilha de comparação da /precos (`#cmp-v2`).
 *
 * Separado do `tailwind.config.js` (chat) de propósito: o chat usa prefixo
 * `pc-` e escopo `#pigbank-chat-root`; os blocos shadcn colados aqui usam as
 * classes SEM prefixo, então mexer no config do chat quebraria a ilha dele.
 *
 * Sem `prefix` para casar com o markup do shadcn; o isolamento vem do
 * `important: "#cmp-v2"`, que emite toda utility como `#cmp-v2 .classe` —
 * nada vaza para o resto da página e nada da página vence a ilha por
 * especificidade. `preflight` segue desligado: o reset global do site.css
 * (`* { box-sizing; margin: 0; padding: 0 }`) já cobre a página, e um reset
 * do Tailwind no meio dela reescreveria elementos alheios.
 */
export default {
  // O grid arrastável é do dashboard-v2 (tailwind.dashboard.config.js); fora daqui
  // para não mudar o CSS desta ilha.
  content: ["./src/precos/**/*.{js,jsx,ts,tsx}", "./src/components/ui/**/*.{ts,tsx}", "!./src/components/ui/draggable-widget-grid.tsx"],
  important: "#cmp-v2",
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
};
