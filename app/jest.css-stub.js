// Stub do `global.css` para o Jest: quem entende `@tailwind` é o PostCSS via
// Metro, não o Jest. O import em `app/_layout.tsx` só precisa não quebrar o
// require — nenhuma regra de estilo é avaliada em teste (RNTL não lê CSS).
module.exports = {};
