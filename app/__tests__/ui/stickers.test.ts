import { STICKERS } from "@/ui/stickers";

declare const __dirname: string;

describe("stickers", () => {
  it("as chaves de STICKERS batem exatamente com os arquivos .webp em app/assets/stickers/", () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- sem @types/node neste projeto (mesmo motivo do require em icone.test.tsx).
    const fs = require("node:fs") as { readdirSync: (dir: string) => string[] };
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- idem.
    const path = require("node:path") as { resolve: (...partes: string[]) => string };

    const diretorio = path.resolve(__dirname, "../../assets/stickers");
    const arquivos = fs
      .readdirSync(diretorio)
      .filter((f: string) => f.endsWith(".webp"))
      .map((f: string) => f.replace(/\.webp$/, ""))
      .sort();
    expect(Object.keys(STICKERS).sort()).toEqual(arquivos);
  });
});
