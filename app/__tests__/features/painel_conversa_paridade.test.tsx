import fs from "fs";
import path from "path";
import { LIMITE_CHAT_MS, conversarFluxo } from "@/services/painel";
import { TITULOS } from "@/features/painel/catalogo";
import { BY_PROFILE, COMMON, widgetDoTopico } from "@/features/painel/perguntas";
import { fetchFalso, prepararCaso } from "./auth_apoio";
// Saído de painel_conversa.test.tsx (teto de 350 linhas): estes dois casos não usam o servidor falso do chat.
beforeEach(() => { prepararCaso(); });
afterEach(() => { jest.useRealTimers(); });

// ---- paridade com o protótipo do /painel
const prompts = () => {
 const fonte = fs.readFileSync(path.resolve(__dirname, "../../../webapp/src/dashboard/lib/prompts.js"), "utf8").replace(/^export /gm, "");
 return new Function(`${fonte}; return { COMMON, BY_PROFILE };`)() as { COMMON: Registro[]; BY_PROFILE: Record<string, Registro[]> };
};
type Registro = { key: string; topic?: string; cat?: string; ask: string | null };
const util = (xs: Registro[]) => xs.filter((x) => x.ask && x.topic).map(({ key, topic, cat, ask }) => ({ key, topic, ask, ...(cat ? { cat } : {}) }));
it("perguntas.ts espelha prompts.js (key, topic, cat, ask) e todo assunto tem widget no catálogo", () => {
 const web = prompts(); expect(COMMON).toEqual(util(web.COMMON)); expect(Object.keys(BY_PROFILE).sort()).toEqual(Object.keys(web.BY_PROFILE).sort());
 for (const [perfil, lista] of Object.entries(web.BY_PROFILE)) expect(BY_PROFILE[perfil as keyof typeof BY_PROFILE]).toEqual(util(lista));
 for (const p of [...COMMON, ...Object.values(BY_PROFILE).flat()]) expect(Object.keys(TITULOS)).toContain(widgetDoTopico(p!));
 expect(widgetDoTopico({ topic: "categoria", cat: "assinaturas" })).toBe("assinaturas"); expect(widgetDoTopico({ topic: "categoria", cat: "delivery" })).toBe("categorias");
});
it("o POST do chat tem limite de 60 s, não de 15", async () => {
 jest.useFakeTimers(); expect(LIMITE_CHAT_MS).toBe(60_000);
 const pai = new AbortController(); fetchFalso.mockImplementation(() => new Promise(() => undefined)); void conversarFluxo("oi", pai, () => undefined).catch(() => undefined);
 await jest.advanceTimersByTimeAsync(15_001); expect(pai.signal.aborted).toBe(false);
 await jest.advanceTimersByTimeAsync(45_000); expect(pai.signal.aborted).toBe(true);
});
