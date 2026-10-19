// Bancos conectados (conta/Bancos.tsx): cor do banco, tom do estado, os bancos disponíveis para conectar e o "quando" da última sincronização.
import type { Bancos } from "./api-v2.gen";
import { FUSO, instante } from "./v2";

// Cópia de `coresBanco` (app/src/ui/tokens.ts): os dois projetos npm não se importam, e
// tests/frontend/bancos_paridade.test.mjs compara o texto dos dois (§0.7).
export const CORES_BANCO: Record<string, string> = {
  nubank: "#820AD1", itau: "#EC7000", bradesco: "#CC092F", santander: "#EC0000", inter: "#FF7A00",
  "banco do brasil": "#FAE128", caixa: "#0070AF", "mercado pago": "#009EE3", xp: "#F5C400", btg: "#001E62",
};
// A superfície do card `.panel`: igual a --plane-1 e a --tw-card de styles/base.css (o teste compara).
export const SUPERFICIE = "#101013";

// Contraste WCAG 2.x, portado de app/src/ui/contraste.ts (só hex de 6 dígitos).
const canal = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const luminancia = (hex: string) => {
  const n = Number.parseInt(hex.replace("#", ""), 16);
  return 0.2126 * canal((n >> 16) & 0xff) + 0.7152 * canal((n >> 8) & 0xff) + 0.0722 * canal(n & 0xff);
};
const contraste = (a: string, b: string) => {
  const [l1, l2] = [luminancia(a), luminancia(b)];
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};

const semAcento = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const TINTA = ["var(--ink)", "var(--ink-2)", "var(--ink-3)"];
// A cor se passar 3:1 sobre o card; senão a escala de tinta, ciclando pelo índice (como o app).
const corOuTinta = (cor: string | null | undefined, indice: number) => (cor && contraste(cor, SUPERFICIE) >= 3 ? cor : TINTA[indice % TINTA.length]);
export function corDoBanco(instituicao: string, indice: number): string {
  const nome = semAcento(instituicao);
  const chave = Object.keys(CORES_BANCO).find((k) => new RegExp(`\\b${k}\\b`).test(nome));
  return corOuTinta(chave && CORES_BANCO[chave], indice);
}
// A cor do conector (`color` do /connectors: hex sem '#', dado de terceiro que vai para o estilo): só 6 dígitos hex; qualquer outra coisa é a tinta.
export const corDoConector = (hex: unknown, indice: number) => corOuTinta(typeof hex === "string" && /^[0-9a-fA-F]{6}$/.test(hex) ? `#${hex}` : null, indice);

// Disponíveis para conectar: os populares, nesta ordem, que a conta ainda não tem. O NOME só escolhe a ordem de exibição; "já conectado" é
// pelo ID (`institution_id` do snapshot × `id` do conector): o gêmeo Open Finance de um banco tem nome igual e id diferente, e é outra conexão.
const POPULARES = ["itau", "bradesco", "banco do brasil", "santander", "caixa", "inter", "c6", "nubank", "btg", "mercado pago"];
export type Conector = { id: number; name: string; color: string | null; logo: string | null };
export function disponiveis(todos: Conector[], conectados: (string | null)[], max = 5): Conector[] {
  const tem = new Set(conectados);
  const porPosicao = new Map<number, Conector>(); // um por posição: o de nome exato vence o parecido ("Itaú" × "Itaú Personnalité")
  for (const c of todos) {
    if (tem.has(String(c.id))) continue;
    const nome = semAcento(c.name).trim();
    const pos = POPULARES.findIndex((k) => new RegExp(`\\b${k}\\b`).test(nome));
    if (pos < 0) continue;
    const atual = porPosicao.get(pos);
    if (!atual || (nome === POPULARES[pos] && semAcento(atual.name).trim() !== POPULARES[pos])) porPosicao.set(pos, c);
  }
  return [...porPosicao.entries()].sort((a, b) => a[0] - b[0]).slice(0, max).map(([, c]) => c);
}

// O estado da conexão (`ui.state`, de connection_ui_state) em tom da pílula. Mesmo mapa do OF_PILL_CLASS do settings.html
// (active→ok, pending→pendente, error→erro); o teste compara. Estado fora da tabela cai em "pendente", nunca em verde.
export const TOM_DO_ESTADO: Record<string, "ok" | "pendente" | "erro"> = {
  updated: "ok", partial: "pendente", updating: "pendente", no_accounts: "pendente",
  error_recoverable: "erro", needs_user_action: "erro", item_missing: "erro", paused: "erro", removed: "erro",
};
export const tomDoEstado = (s: unknown) => (Object.prototype.hasOwnProperty.call(TOM_DO_ESTADO, String(s)) ? TOM_DO_ESTADO[String(s)] : "pendente"); // "constructor" não é estado (herdaria função)

// "Hoje, 14:32", "Ontem, 19:45" ou "03/10, 08:10" no fuso do painel (nunca o do aparelho); sem data válida, "—". Sem dateStyle/hourCycle: Safari 14.0.
const AAAA_MM_DD = new Intl.DateTimeFormat("en-CA", { timeZone: FUSO, year: "numeric", month: "2-digit", day: "2-digit" });
const HORA_MIN = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, hour: "2-digit", minute: "2-digit" });
export function quandoSync(iso: unknown, agora = Date.now()): string {
  const ms = instante(iso);
  if (!Number.isFinite(ms)) return "—";
  const dia = AAAA_MM_DD.format(ms);
  const quando = dia === AAAA_MM_DD.format(agora) ? "Hoje" : dia === AAAA_MM_DD.format(agora - 864e5) ? "Ontem" : dia.slice(8) + "/" + dia.slice(5, 7);
  return `${quando}, ${HORA_MIN.format(ms)}`;
}
// A sincronização mais recente entre as conexões (a que o resumo mostra); nenhuma com data válida = null.
export const ultimaSync = (datas: (string | null)[]): string | null =>
  datas.reduce<string | null>((a, d) => (Number.isFinite(instante(d)) && (a === null || instante(d) > instante(a)) ? d : a), null);

// A resposta do /bancos tem a forma do contrato? Fora dela a tela trata como leitura falha (nunca quebra a SPA).
const textos = (m: unknown) => Array.isArray(m) && m.every((s) => typeof s === "string"); // motivo que não é texto derrubaria o Selos (erro #31)
export const bancosValidos = (b: unknown): b is Bancos => {
  const x = b as Bancos | null;
  return !!x && textos(x.motivos) && Array.isArray(x.bancos) && x.bancos.every((y) => !!y && textos(y.motivos));
};
