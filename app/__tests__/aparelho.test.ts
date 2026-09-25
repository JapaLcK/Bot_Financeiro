/**
 * O User-Agent que o app manda. A fixture é a MESMA que o pytest lê
 * (`tests/test_sessoes_app.py`): o app gera exatamente o que o servidor
 * reconhece, e mudar o formato de um lado sem o outro fica vermelho aqui.
 */
import { montarUserAgent } from "@/api/aparelho";

import casos from "../../tests/fixtures/user_agents_app.json";

const base = { versao: "0.1.0", so: "android", versaoSo: "14" };

describe("montarUserAgent", () => {
  it.each(casos.filter((c) => c.entrada))("gera o UA da fixture: $ua", (caso) => {
    expect(montarUserAgent(caso.entrada!)).toBe(caso.ua);
  });

  it("tira o que não é ASCII — o OkHttp lança com header não-ASCII", () => {
    expect(montarUserAgent({ ...base, modelo: "Galaxy™ S24 Ültra" })).toBe(
      "PigBankApp/0.1.0 (Galaxy S24 ltra; Android 14)",
    );
  });

  it("modelo só com caractere proibido cai no fallback", () => {
    expect(montarUserAgent({ ...base, modelo: "华为 ();" })).toBe(
      "PigBankApp/0.1.0 (Android; Android 14)",
    );
  });

  it("o modelo nunca passa de 64", () => {
    const ua = montarUserAgent({ ...base, modelo: "x".repeat(200) });
    expect(ua).toBe(`PigBankApp/0.1.0 (${"x".repeat(64)}; Android 14)`);
  });
});
