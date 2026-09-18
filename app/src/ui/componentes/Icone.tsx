import type { ComponentType } from "react";

import { useTema } from "@/ui/tema";
import type { Paleta } from "@/ui/tokens";

/**
 * União restrita aos ícones que C1 usa de fato (chevron do `ListRow` e as
 * duas amostras do catálogo) — nada de pré-popular a lista inteira do
 * pacote.
 */
export type NomeIcone = "CaretRight" | "Wallet" | "Bell";

/** Forma mínima que todo ícone do Phosphor e o stub de teste têm em comum. */
type ComponenteIcone = ComponentType<{ size?: number; color?: string; weight?: string }>;

/**
 * `require()`, não `import`: a lib publica cada ícone como `.tsx` FONTE, e o
 * arquivo interno que eles importam (`phosphor-react-native/src/lib/icon-base.tsx`)
 * tem um erro de tipo real contra a versão instalada do `react-native-svg`
 * (prop `className` que esta versão não declara) — um `import` estático
 * arrasta o `tsc` para dentro daquele arquivo quebrado (bug da lib, não
 * deste projeto). `require()` devolve o módulo sem o TS precisar checar o
 * tipo da árvore inteira; em runtime é o mesmo componente (Metro resolve
 * `require` de caminho ESTÁTICO normalmente — só não aceita caminho
 * dinâmico, por isso um `require` por ícone, não uma função genérica).
 *
 * Cada arquivo real exporta só nomeado (`<Nome>Icon`), nunca `default`. O
 * `moduleNameMapper` do Jest troca QUALQUER ícone pelo MESMO stub, que só
 * tem `default` — por isso `resolver` aceita os dois formatos: nomeado em
 * produção, `default` no teste.
 */
function resolver(mod: unknown, nomeado: string): ComponenteIcone {
  const registro = mod as Record<string, ComponenteIcone | undefined>;
  const componente = registro[nomeado] ?? registro.default;
  if (!componente) throw new Error(`Ícone "${nomeado}" não encontrado no módulo.`);
  return componente;
}

const ICONES: Record<NomeIcone, ComponenteIcone> = {
  CaretRight: resolver(require("phosphor-react-native/src/icons/CaretRight"), "CaretRightIcon"),
  Wallet: resolver(require("phosphor-react-native/src/icons/Wallet"), "WalletIcon"),
  Bell: resolver(require("phosphor-react-native/src/icons/Bell"), "BellIcon"),
};

interface Props {
  nome: NomeIcone;
  tamanho?: 20 | 24;
  /** Nunca ícone em quadrado colorido (identidade pigbank-frontend): a cor é sempre um tom neutro do texto, nunca `brand`/`acao` soltos. */
  tom?: keyof Paleta;
}

export function Icone({ nome, tamanho = 24, tom = "ink" }: Props) {
  const { cores } = useTema();
  const Componente = ICONES[nome];
  return <Componente size={tamanho} color={cores[tom]} weight="regular" />;
}
