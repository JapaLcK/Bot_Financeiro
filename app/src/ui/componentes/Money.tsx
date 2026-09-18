import { faladoComSinal, partes, VALOR_INDISPONIVEL_FALADO } from "@/ui/dinheiro";
import type { Paleta } from "@/ui/tokens";

import { Texto } from "./Texto";

type Tipo = "saldo" | "entrada" | "saida";
type Variante = "display" | "corpo" | "rotulo";
type Tom = keyof Paleta;

interface Props {
  centavos: number;
  tipo?: Tipo;
  variante?: Variante;
  /** Máscara fixa, sem nenhum dígito de `centavos` na árvore (decisão do dono). */
  oculto?: boolean;
}

// U+2212 (sinal de menos matemático), nunca o hífen "-" — decisão 4 do dono:
// "−R$ 12,30" diverge de propósito do "R$ -12,30" do site.
const SINAL_MENOS = "−";

/**
 * Decisão 2 do dono: `entrada`/`saida` mostram o MÓDULO de `centavos` com o
 * sinal do TIPO, não o sinal do número — uma saída de -1230 (lançamento
 * estornado, por exemplo) ainda é "−R$ 12,30", nunca "+R$ 12,30". Só `saldo`
 * segue o sinal do próprio valor. "−" nunca é `danger` (vermelho é reservado
 * para erro de formulário, não para saída de dinheiro no extrato).
 */
export function Money({ centavos, tipo = "saldo", variante = "corpo", oculto = false }: Props) {
  if (oculto) {
    return (
      <Texto variante={variante} tom="ink" numerico accessible accessibilityLabel="Valor oculto">
        R$ ••••
      </Texto>
    );
  }

  const p = partes(centavos);
  if (!p) {
    // Mesmo texto de `VALOR_INDISPONIVEL_FALADO` (fonte única, CLAUDE.md
    // §0.7) — capitalizado aqui porque este rótulo é a frase INTEIRA e
    // isolada (início), diferente do uso dentro de `faladoComSinal`/
    // `AmountInput`, que o embutem no MEIO de uma frase maior.
    const rotulo = VALOR_INDISPONIVEL_FALADO.charAt(0).toUpperCase() + VALOR_INDISPONIVEL_FALADO.slice(1);
    return (
      <Texto variante={variante} tom="ink" numerico accessible accessibilityLabel={rotulo}>
        —
      </Texto>
    );
  }

  const zero = p.inteiro === "0" && p.centavos === "00";
  let sinal = "";
  let tom: Tom = "ink";
  if (!zero) {
    if (tipo === "entrada") {
      sinal = "+";
      tom = "positive";
    } else if (tipo === "saida") {
      sinal = SINAL_MENOS;
    } else if (p.negativo) {
      sinal = SINAL_MENOS;
    }
  }

  // Mesma função que `TransactionRow` usa para o rótulo do container
  // `accessible` (CLAUDE.md §0.7) — antes cada um remontava "sinal + falado"
  // a seu próprio jeito, e já tinham divergido em zero/valor inválido.
  const label = faladoComSinal(centavos, tipo);
  const antesDaVirgula = `${sinal}R$ ${p.inteiro},`;

  if (variante !== "display") {
    return (
      <Texto variante={variante} tom={tom} numerico accessible accessibilityLabel={label}>
        {antesDaVirgula}
        {p.centavos}
      </Texto>
    );
  }

  // No `display` os centavos vão num `Texto` aninhado: só assim eles ganham
  // um tom diferente do valor principal (decisão 3) sem duplicar o rótulo de
  // acessibilidade — o pai concentra o `accessibilityLabel` inteiro.
  const tomCentavos: Tom = tom === "ink" ? "inkMuted" : tom;
  return (
    <Texto
      variante={variante}
      tom={tom}
      numerico
      accessible
      accessibilityLabel={label}
      numberOfLines={1}
      adjustsFontSizeToFit
      minimumFontScale={0.6}
    >
      {antesDaVirgula}
      <Texto variante={variante} tom={tomCentavos} numerico>
        {p.centavos}
      </Texto>
    </Texto>
  );
}
