/** "há 12 min". `agora` entra de fora (o teste congela o relógio); relógio do aparelho atrás do servidor não vira "há -1 min": clampa em "agora". */
export function tempoRelativo(instante: string, agora: number = Date.now()): string {
  const min = Math.floor(Math.max(0, agora - new Date(instante).getTime()) / 60_000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h} h`;
  const d = Math.floor(h / 24);
  return d === 1 ? "há 1 dia" : `há ${d} dias`;
}
