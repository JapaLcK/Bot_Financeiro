// Tailwind do novo dashboard (protótipo em ../dashboard-v2). Só serve o grid
// arrastável e os utilitários que os widgets usarem: o visual do produto mora
// em src/dashboard/styles/*.css. Sem prefixo, com escopo no nó raiz da ilha e
// sem preflight, como a ilha de preços. Os tokens do shadcn apontam para as
// variáveis do dashboard (canais RGB, para o `/40` de opacidade funcionar).
const token = (name) => `rgb(var(--tw-${name}) / <alpha-value>)`;

export default {
  content: ["./src/dashboard/**/*.{ts,tsx}", "./src/components/ui/draggable-widget-grid.tsx"],
  important: "#pigbank-dashboard",
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        card: { DEFAULT: token("card"), foreground: token("foreground") },
        foreground: token("foreground"),
        border: token("border"),
        ring: token("ring"),
      },
    },
  },
};
