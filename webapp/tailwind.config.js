export default {
  content: ["./src/chat/**/*.{ts,tsx}", "./src/components/ui/**/*.{ts,tsx}", "!./src/components/ui/draggable-widget-grid.tsx"],
  prefix: "pc-",
  important: "#pigbank-chat-root",
  corePlugins: { preflight: false },
};
