// Copia para a área de transferência. O `Promise.resolve` faz a API ausente (página fora de HTTPS) rejeitar em vez de lançar: quem chama trata uma falha só.
export const copiar = (texto: string) => Promise.resolve().then(() => navigator.clipboard.writeText(texto));
