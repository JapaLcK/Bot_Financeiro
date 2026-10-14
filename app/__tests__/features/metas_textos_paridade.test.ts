/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DIAS_MINIMOS, DO_ALVO, FRASE_CUSTO, FRASE_RITMO, MESES_PADRAO, SO_DO_RITMO, fraseCusto, fraseRitmo } from "@/features/painel/metasTextos";
// `metasTextos.ts` copia `webapp/src/dashboard/lib/metas-textos.ts` (§0.7): o app não importa o web, então este teste compara os dois.
// As duas frases em que a voz só-leitura muda ("no painel", sem botão) são comparadas com o literal de `core/services/metas_conversa.py`.
const RAIZ = resolve(__dirname, "../../..");
const web = readFileSync(resolve(RAIZ, "webapp/src/dashboard/lib/metas-textos.ts"), "utf8");
const py = readFileSync(resolve(RAIZ, "core/services/metas_conversa.py"), "utf8");
const EXCECOES = ["fonte_indisponivel", "plano_sem_custo_mensal"];
const bloco = (fonte: string, nome: string) => fonte.split(`export const ${nome}`)[1]!.split("\n};")[0]!;
const chaves = (b: string) => [...b.matchAll(/^ {2}(\w+):/gm)].map((m) => m[1]!);
const pyDe = (k: string) => new RegExp(`^ {4}"${k}": "([^"]*)"`, "m").exec(py)![1]!;
const textos = (b: string, re: RegExp) => Object.fromEntries([...b.matchAll(re)].map((m) => [m[1]!, m[2]!]));
/** O que o app diz de diferente do web (lista vazia = paridade); `ritmo`/`custo` entram como parâmetro para o controle negativo poder mutá-los. */
function divergencias(ritmo: typeof FRASE_RITMO, custo: typeof FRASE_CUSTO): string[] {
  const d: string[] = [], r = bloco(web, "FRASE_RITMO"), c = bloco(web, "FRASE_CUSTO");
  const wr = textos(r, /^ {2}(\w+): \{ texto: "((?:[^"\\]|\\.)*)"/gm), wc = textos(c, /^ {2}(\w+): "((?:[^"\\]|\\.)*)"/gm);
  const curto = r.split("historico_curto:")[1]!.split("sem_historico_da_fonte:")[0]!, nulo = /"(Ainda estou juntando[^"]*)"/.exec(curto)![1], numero = /`([^`]*)`/.exec(curto)![1]!;
  if (JSON.stringify(Object.keys(ritmo)) !== JSON.stringify(chaves(r))) d.push("chaves/ordem do ritmo");
  if (JSON.stringify(Object.keys(custo)) !== JSON.stringify(chaves(c))) d.push("chaves/ordem do custo");
  for (const [k, f] of Object.entries(ritmo)) {
    if (typeof f === "function") {
      if (f(null) !== nulo) d.push(`${k}(null)`);
      if (f(3) !== numero.replace("${dias}", "3").replaceAll("${DIAS_MINIMOS}", String(DIAS_MINIMOS))) d.push(`${k}(3)`);
    } else if (f !== (EXCECOES.includes(k) ? pyDe(k) : wr[k])) d.push(k);
  }
  for (const [k, f] of Object.entries(custo)) if (f !== (EXCECOES.includes(k) ? pyDe(k) : wc[k])) d.push(k);
  return d;
}
it("chaves, ordem e textos do ritmo e do custo são os do web (menos as duas frases só-leitura, que são as do Python)", () => expect(divergencias(FRASE_RITMO, FRASE_CUSTO)).toEqual([]));
it("controle negativo: o comparador acusa texto mudado, chave trocada de lugar e frase de exceção desviada", () => {
  expect(divergencias({ ...FRASE_RITMO, sem_banco_conectado: "Conecte um banco." }, FRASE_CUSTO)).toContain("sem_banco_conectado");
  expect(divergencias(FRASE_RITMO, { ...FRASE_CUSTO, sem_recorrencias: "x" })).toContain("sem_recorrencias");
  const { sem_banco_conectado, ...resto } = FRASE_RITMO; expect(divergencias({ ...resto, sem_banco_conectado: sem_banco_conectado! }, FRASE_CUSTO)).toEqual(expect.arrayContaining(["chaves/ordem do ritmo"])); // mesma chave, fim da fila
  expect(divergencias({ ...FRASE_RITMO, historico_curto: () => "x" }, FRASE_CUSTO)).toEqual(expect.arrayContaining(["historico_curto(null)", "historico_curto(3)"]));
  expect(divergencias({ ...FRASE_RITMO, fonte_indisponivel: "Não acho mais essa fonte. Ela pode ter sido desconectada: edite a meta e escolha outra." }, FRASE_CUSTO)).toContain("fonte_indisponivel"); // a do web não vale aqui
});
it("DIAS_MINIMOS e MESES_PADRAO são os literais do web (o web é comparado ao Python em tests/test_metas_textos_constantes.py)", () => {
  const n = (nome: string) => Number(new RegExp(`export const ${nome} = (\\d+);`).exec(web)![1]);
  expect(DIAS_MINIMOS).toBe(n("DIAS_MINIMOS")); expect(MESES_PADRAO).toBe(n("MESES_PADRAO"));
});
it("SO_DO_RITMO é o MOTIVOS_RITMO de db/metas_ritmo.py (mesmos códigos, mesma ordem)", () => {
  const python = readFileSync(resolve(RAIZ, "db/metas_ritmo.py"), "utf8");
  expect(SO_DO_RITMO).toEqual([...new RegExp("^MOTIVOS_RITMO = \\(([^)]*)\\)", "m").exec(python)![1]!.matchAll(/"(\w+)"/g)].map((m) => m[1]));
  expect(SO_DO_RITMO.every((c) => c in FRASE_RITMO)).toBe(true); // cada um tem a frase própria: quem os tira dos avisos não os perde do ritmo
});
it("DO_ALVO é o do Python e o do MetaCartao.tsx (mesmos códigos, mesma ordem)", () => {
  const lista = (t: string, re: RegExp) => [...re.exec(t)![1]!.matchAll(/"(\w+)"/g)].map((m) => m[1]);
  expect(DO_ALVO).toEqual(lista(py, /^DO_ALVO = \(([^)]*)\)/m));
  expect(DO_ALVO).toEqual(lista(readFileSync(resolve(RAIZ, "webapp/src/dashboard/parts/MetaCartao.tsx"), "utf8"), /^const DO_ALVO = \[([^\]]*)\]/m));
});
it("as frases de fallback fora dos mapas são as do web", () => {
  const vazio = { valor: null, motivos: [], dias_de_historico: null } as never;
  for (const f of [fraseRitmo(vazio), fraseCusto(vazio)!]) expect(web).toContain(`"${f}"`);
  const velho = fraseCusto({ valor: "1.00", motivos: ["recorrencias_banco_nao_lidas"] } as never)!;
  expect(velho).toBe(`${FRASE_CUSTO.recorrencias_banco_nao_lidas} O valor pode estar velho.`);
  expect(web).toContain("${FRASE_CUSTO[k]} O valor pode estar velho."); // o sufixo, no template do web
  expect(py).toContain("} O valor pode estar velho.");
});
