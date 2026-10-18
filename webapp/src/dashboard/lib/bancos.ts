// Bancos conectados (conta/Bancos.tsx): cor do banco, tom do estado e a barra por banco.
// O painel não soma dinheiro para mostrar: o número do topo é o `total` do servidor. A barra só COMPARA, em inteiros exatos.
import type { Bancos } from "./api-v2.gen";
import { decimal } from "./format.js";

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
// Cor do banco se for conhecido E passar 3:1 sobre o card; senão a escala de tinta, ciclando pelo índice (como o app).
export function corDoBanco(instituicao: string, indice: number): string {
  const nome = semAcento(instituicao);
  const chave = Object.keys(CORES_BANCO).find((k) => new RegExp(`\\b${k}\\b`).test(nome));
  const cor = chave && CORES_BANCO[chave];
  return cor && contraste(cor, SUPERFICIE) >= 3 ? cor : TINTA[indice % TINTA.length];
}

// O estado da conexão (`ui.state`, de connection_ui_state) em tom da pílula. Mesmo mapa do OF_PILL_CLASS do settings.html
// (active→ok, pending→pendente, error→erro); o teste compara. Estado fora da tabela cai em "pendente", nunca em verde.
export const TOM_DO_ESTADO: Record<string, "ok" | "pendente" | "erro"> = {
  updated: "ok", partial: "pendente", updating: "pendente", no_accounts: "pendente",
  error_recoverable: "erro", needs_user_action: "erro", item_missing: "erro", paused: "erro", removed: "erro",
};
export const tomDoEstado = (s: unknown) => (Object.prototype.hasOwnProperty.call(TOM_DO_ESTADO, String(s)) ? TOM_DO_ESTADO[String(s)] : "pendente"); // "constructor" não é estado (herdaria função)

// A resposta do /bancos tem a forma do contrato? Fora dela a tela trata como leitura falha (nunca quebra a SPA).
const textos = (m: unknown) => Array.isArray(m) && m.every((s) => typeof s === "string"); // motivo que não é texto derrubaria o Selos (erro #31)
export const bancosValidos = (b: unknown): b is Bancos => {
  const x = b as Bancos | null;
  return !!x && textos(x.motivos) && Array.isArray(x.bancos) && x.bancos.every((y) => !!y && textos(y.motivos));
};

export type Fatia = { conexao_id: number; nome: string; cor: string; total: string };
// A barra por banco: um segmento por conexão com saldo > 0, na ordem e com a cor do card (índice no snapshot; como o app, que só
// desenha segmento > 0). Banco com total EXATAMENTE zero fica fora da barra mas não a impede. [] (sem barra) se QUALQUER coisa deixar o
// número incerto: motivo no topo ou no banco (lista branca vazia), total de banco nulo/negativo/inválido, banco que o snapshot não
// conhece, id repetido, menos de 2 segmentos, ou soma (inteiros exatos) que não fecha com o topo.
export function barraDosBancos(b: Bancos, conexoes: { id: number; institution_name: string | null }[]): Fatia[] {
  const topo = decimal(b.total);
  const ids = new Set(b.bancos.map((x) => x.conexao_id));
  if (!topo || b.motivos.length || ids.size !== b.bancos.length || !Number.isFinite(Number(b.total))) return []; // "1E999" é decimal válido e Number() dá Infinity: a barra desenharia "NaN%" (as partes são >= 0 e somam o topo)
  const partes = b.bancos.map((x) => (x.motivos.length ? null : decimal(x.total)));
  if (partes.some((p) => !p || p.n < BigInt(0)) || !b.bancos.every((x) => conexoes.some((c) => c.id === x.conexao_id))) return [];
  if (partes.filter((p) => p!.n > BigInt(0)).length < 2) return [];
  const escala = Math.max(topo.escala, ...partes.map((p) => p!.escala));
  const ate = (d: { n: bigint; escala: number }) => d.n * BigInt(10) ** BigInt(escala - d.escala);
  if (partes.reduce((s, p) => s + ate(p!), BigInt(0)) !== ate(topo)) return [];
  const comSaldo = new Set(b.bancos.filter((_, i) => partes[i]!.n > BigInt(0)).map((x) => x.conexao_id));
  return conexoes.flatMap((c, i) => {
    const x = b.bancos.find((y) => y.conexao_id === c.id);
    return x && comSaldo.has(c.id) ? [{ conexao_id: c.id, nome: c.institution_name?.trim() || "Banco", cor: corDoBanco(c.institution_name ?? "", i), total: x.total! }] : [];
  });
}
