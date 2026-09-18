import { TextInput, View } from "react-native";

import { digitoAscii, digitosParaCentavos, falado, partes, TETO_CENTAVOS } from "@/ui/dinheiro";
import { useAvisoAoErrar } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { espaco, texto as escalas } from "@/ui/tokens";

import { Texto } from "./Texto";

interface Props {
  centavos: number;
  onChange: (centavos: number) => void;
  rotulo: string;
  erro?: string;
  desativado?: boolean;
}

/**
 * Preenchimento pela direita (decisão do dono): o campo guarda só
 * `centavos`, nunca o texto digitado — a vírgula anda sozinha porque fica
 * sempre a duas casas do fim. Por isso o cursor é fixo no fim (`selection`),
 * nunca livre como num `<TextInput>` comum: não existe "meio" do número.
 */
export function AmountInput({ centavos, onChange, rotulo, erro, desativado = false }: Props) {
  const { cores } = useTema();
  useAvisoAoErrar(!!erro);

  // Prop inválida (fora de 0..TETO ou não inteiro seguro): campo nasce vazio
  // com "—" de placeholder — "0,00" mentiria sobre um valor que não existe.
  // A primeira digitação/colagem é tratada como substituição de um exibido
  // vazio (ver `aoDigitar`) e propaga o valor que a pessoa vê.
  const dentroDoTeto = centavos >= 0 && centavos <= TETO_CENTAVOS;
  const p = dentroDoTeto ? partes(centavos) : null;
  const valorExibido = p ? `${p.inteiro},${p.centavos}` : "";
  const fim = valorExibido.length;

  /**
   * Decisão do dono (2026-09-17): só substitui
   * quando o trecho colado tem símbolo — não basta ter crescido. Cursor fixo
   * no fim faz digitar e colar chegarem do mesmo jeito no evento nativo,
   * `exibido + texto novo`, então a classificação olha o SUFIXO
   * (`novoTexto` menos `valorExibido`) quando `novoTexto` cresce a partir do
   * `valorExibido` atual (`startsWith`):
   * - sufixo só com dígitos ASCII, de qualquer tamanho → digitação (inclusive
   *   digitação rápida que chegou em lote): acumula, parseando `novoTexto`
   *   inteiro. Consequência aceita pelo dono: colar "50" puro sobre R$ 1,23
   *   vira R$ 123,50, e não R$ 0,50 — colar só dígitos não tem como se
   *   distinguir de digitar rápido.
   * - sufixo com pelo menos 1 dígito e algum outro caractere da lista branca
   *   (R, $, vírgula, ponto, espaço, sinal) → colagem de verdade: substitui,
   *   parseando só o sufixo.
   * - sufixo sem nenhum dígito (só símbolo, ou vazio) → recusa: nenhum
   *   `onChange`, mesmo com `centavos` inválido (evita "," ou "R$" virarem
   *   "0,00" pelo fallback de `digitosParaCentavos` para string sem dígito).
   * Fora desse caso (`novoTexto` não começa com `valorExibido` — seleção
   * total e cola, ou encolheu por apagar) → `novoTexto` inteiro, como antes;
   * `""` continua zerando. Mesma recusa do sufixo se aplica aqui: um
   * `novoTexto` não vazio sem nenhum dígito (ex.: colar "R$" ou "--" por
   * cima de um valor já digitado) não chama `onChange` — sem a guarda,
   * `digitosParaCentavos` devolveria 0 para essa string e apagaria o valor.
   * Limite conhecido, não resolvido: selecionar tudo e colar um
   * texto que por coincidência começa igual ao `valorExibido` (ex.: colar
   * "1,234.56" sobre "1,23") é lido como colagem do sufixo, não como
   * substituição total — exigiria `onSelectionChange` para diferenciar.
   */
  function aoDigitar(novoTexto: string) {
    let paraAnalisar: string;
    if (novoTexto.length > valorExibido.length && novoTexto.startsWith(valorExibido)) {
      const sufixo = novoTexto.slice(valorExibido.length);
      const caracteresDoSufixo = [...sufixo];
      const sufixoSoDigitos = caracteresDoSufixo.length > 0 && caracteresDoSufixo.every(digitoAscii);
      const sufixoTemDigito = caracteresDoSufixo.some(digitoAscii);
      if (sufixoSoDigitos) {
        paraAnalisar = novoTexto;
      } else if (sufixoTemDigito) {
        paraAnalisar = sufixo;
      } else {
        return;
      }
    } else {
      if (novoTexto.length > 0 && ![...novoTexto].some(digitoAscii)) return;
      paraAnalisar = novoTexto;
    }

    const n = digitosParaCentavos(paraAnalisar);
    // `n === null`: caractere fora da lista branca ou acima do teto — o
    // `onChange` nunca é chamado, e o RN 0.86 sincroniza o nativo de volta
    // pro `value` atual sozinho (`TextInput.js`, `useTextInputStateSynchronization`).
    // `n === centavos`: dígito redundante — não emitir evita um render à toa.
    if (n !== null && n !== centavos) onChange(n);
  }

  const fala = p ? (falado(centavos) ?? "zero reais") : "valor indisponível";
  const label = `${rotulo}, ${fala}${erro ? `, erro: ${erro}` : ""}`;
  const tom = desativado ? "inkMuted" : "ink";

  return (
    <View>
      <Texto variante="rotulo" tom={tom}>
        {rotulo}
      </Texto>
      <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
        <Texto variante="titulo" tom={tom}>
          R$
        </Texto>
        <TextInput
          testID="valor-input"
          value={valorExibido}
          placeholder="—"
          onChangeText={aoDigitar}
          editable={!desativado}
          keyboardType="number-pad"
          selection={{ start: fim, end: fim }}
          maxFontSizeMultiplier={1.3}
          autoCorrect={false}
          accessibilityLabel={label}
          accessibilityState={{ disabled: desativado }}
          // `flex: 1`: sem ele o campo nasce com a largura do valor inicial e,
          // ao crescer, rola o texto para a esquerda por baixo do "R$" (visto
          // no simulador: 45,90 → 45.907,12 cortava o primeiro dígito).
          style={[escalas.titulo, { flex: 1, color: cores[tom], fontVariant: ["tabular-nums"] }]}
        />
      </View>
      {erro ? (
        <Texto variante="legenda" tom="danger">
          {erro}
        </Texto>
      ) : null}
    </View>
  );
}
