import type { ImageSourcePropType } from "react-native";

/**
 * Fonte única dos 12 stickers do Piggy (identidade pigbank-frontend). Mesmos
 * arquivos publicados em `frontend/brand/stickers/` — `tests/test_app_espelhos.py`
 * compara os bytes (sha256) para as duas árvores nunca divergirem em silêncio.
 * Chave = nome do arquivo sem extensão; `__tests__/ui/stickers.test.ts` prova
 * que este conjunto bate com o que existe em `app/assets/stickers/`.
 */
export type NomeSticker =
  | "approved"
  | "chill"
  | "expense-alert"
  | "goal"
  | "hello"
  | "income"
  | "loading"
  | "ok"
  | "point"
  | "report"
  | "success"
  | "thinking";

// `require()`, não `import`: não existe declaração de módulo `*.webp` neste
// projeto (nem em `@types/`, nem em `expo-env.d.ts` — que sequer foi gerado
// ainda), então um `import img from "./x.webp"` falharia no `tsc`. O Metro
// resolve `require()` de um asset estático normalmente, e `require` já é
// tipado globalmente como `any` (mesmo caminho das fontes em `app/_layout.tsx`).
export const STICKERS: Record<NomeSticker, ImageSourcePropType> = {
  approved: require("../../assets/stickers/approved.webp"),
  chill: require("../../assets/stickers/chill.webp"),
  "expense-alert": require("../../assets/stickers/expense-alert.webp"),
  goal: require("../../assets/stickers/goal.webp"),
  hello: require("../../assets/stickers/hello.webp"),
  income: require("../../assets/stickers/income.webp"),
  loading: require("../../assets/stickers/loading.webp"),
  ok: require("../../assets/stickers/ok.webp"),
  point: require("../../assets/stickers/point.webp"),
  report: require("../../assets/stickers/report.webp"),
  success: require("../../assets/stickers/success.webp"),
  thinking: require("../../assets/stickers/thinking.webp"),
};
