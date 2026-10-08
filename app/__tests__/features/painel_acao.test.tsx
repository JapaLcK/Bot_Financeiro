import { Animated, StyleSheet } from "react-native";
import { Acao } from "@/features/painel/base";
import { claro } from "@/ui/tokens";
import { renderInterativo } from "../ui/_render";

const visual = (r: ReturnType<typeof renderInterativo>) => r.UNSAFE_getByType(Animated.View);

it("Acao tem cara de botão: contorno, alinhada ao início e não esticada na coluna", () => {
  const r = renderInterativo(<Acao rotulo="Ver lançamentos" onPress={jest.fn()} />);
  expect(StyleSheet.flatten(r.getByRole("button").props.style)).toMatchObject({ alignSelf: "flex-start", minHeight: 44 });
  expect(StyleSheet.flatten(visual(r).props.style)).toMatchObject({ borderWidth: 1, borderColor: claro.inkMuted, minHeight: 36 });
});

it("compacta (ao lado de valor) fica com 30pt de contorno, mas o alvo de toque continua 44", () => {
  const r = renderInterativo(<Acao rotulo="Ignorar Netflix" texto="Ignorar" compacta onPress={jest.fn()} />);
  expect(StyleSheet.flatten(r.getByRole("button").props.style)).toMatchObject({ minHeight: 44 });
  expect(StyleSheet.flatten(visual(r).props.style)).toMatchObject({ minHeight: 30, borderWidth: 1 });
});

it("texto visível curto, rótulo do VoiceOver completo; iconeFim vem depois do texto", () => {
  const r = renderInterativo(<Acao rotulo="Ignorar Netflix" texto="Ignorar" iconeFim="CaretRight" onPress={jest.fn()} />);
  expect(r.getByRole("button", { name: "Ignorar Netflix" })).toBeTruthy();
  expect(r.getByText("Ignorar")).toBeTruthy(); expect(r.queryByText("Ignorar Netflix")).toBeNull();
  const filhos = r.toJSON() as { children: { children: { type: string }[] }[] };
  expect(filhos.children[0]!.children.map((c) => c.type)).toEqual(["Text", "View"]);
});

it("somenteIcone continua sem contorno", () => {
  const r = renderInterativo(<Acao rotulo="Subir" icone="ArrowUp" somenteIcone onPress={jest.fn()} />);
  expect(StyleSheet.flatten(visual(r).props.style).borderWidth).toBeUndefined();
});
