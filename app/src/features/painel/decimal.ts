/** Dinheiro da API: arredondamento HALF_UP exato em centavos, sem multiplicação de floats. */
export function decimalParaCentavos(valor: string): number {
  if (!/^-?\d+(?:\.\d+)?$/.test(valor)) throw new Error("Valor monetário incompatível.");
  const negativo = valor.startsWith("-");
  const [inteiro = "0", fracao = ""] = valor.replace(/^-/, "").split(".");
  const total = BigInt(inteiro) * 100n + BigInt(fracao.slice(0, 2).padEnd(2, "0")) + (fracao.length > 2 && fracao.charAt(2) >= "5" ? 1n : 0n);
  if (total > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Valor monetário fora do limite.");
  return Number(negativo ? -total : total);
}
export function numeroLegadoParaCentavos(valor: number): number {
  if (!Number.isFinite(valor)) throw new Error("Valor monetário incompatível.");
  // JSON legado já contém um number. Expande sua representação decimal mais
  // curta em texto, inclusive notação científica; a quantização continua exata.
  const texto = String(valor);
  const cientifico = /^(-?)(\d+)(?:\.(\d+))?e([+-]?\d+)$/.exec(texto);
  if (!cientifico) return decimalParaCentavos(texto);
  const [, sinal = "", inteiro = "", fracao = "", expoente = "0"] = cientifico;
  const digitos = inteiro + fracao;
  const posicao = inteiro.length + Number(expoente);
  const expandido = posicao <= 0 ? `0.${"0".repeat(-posicao)}${digitos}`
    : posicao >= digitos.length ? digitos + "0".repeat(posicao - digitos.length)
    : `${digitos.slice(0, posicao)}.${digitos.slice(posicao)}`;
  return decimalParaCentavos(sinal + expandido);
}
export function somarCentavos(valores: number[]): number {
  return valores.reduce((s, n) => {
    const total = s + n;
    if (!Number.isSafeInteger(total)) throw new Error("Total monetário fora do limite.");
    return total;
  }, 0);
}

/** % de `valor` em `total`, inteiro, HALF_UP afastando do zero; 0 com total 0. Espelha `api/nativo/mes_detalhes.py::periodo` (`percentual`); paridade em `tests/fixtures/percentual_categorias.json`. */
export function percentualDe(valor: number, total: number): number {
  if (total === 0) return 0;
  const n = BigInt(valor) * 100n, d = BigInt(total), an = n < 0n ? -n : n, ad = d < 0n ? -d : d;
  const q = (2n * an + ad) / (2n * ad);
  return Number((n < 0n) !== (d < 0n) ? -q : q);
}
