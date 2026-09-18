import { ActivityIndicator, StyleSheet } from "react-native";

import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Icone } from "@/ui/componentes/Icone";
import { claro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

describe("ConnectionStatus", () => {
  it.each([
    ["updated", "CheckCircle", "positive"],
    ["partial", "WarningCircle", "warning"],
    ["error_recoverable", "WarningCircle", "warning"],
    ["needs_user_action", "WarningCircle", "warning"],
    ["item_missing", "LinkBreak", "danger"],
    ["paused", "PauseCircle", "inkMuted"],
    ["removed", "Trash", "inkMuted"],
    ["no_accounts", "Info", "inkMuted"],
  ] as const)("estado %s: ícone %s, tom %s", (estado, icone, tom) => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado={estado} label="x" />);
    expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: icone, tom });
  });

  it("updating: spinner (ActivityIndicator), sem Icone", () => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado="updating" label="Atualizando…" />);
    expect(c.UNSAFE_getByType(ActivityIndicator)).toBeTruthy();
    expect(c.UNSAFE_queryByType(Icone)).toBeNull();
  });

  it("updating: spinner escondido do leitor de tela (o texto ao lado já diz o estado)", () => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado="updating" label="Atualizando…" />);
    const spinner = c.UNSAFE_getByType(ActivityIndicator);
    expect(spinner.props.accessibilityElementsHidden).toBe(true);
    expect(spinner.props.importantForAccessibility).toBe("no-hide-descendants");
  });

  it("estado desconhecido: cai no visual neutro (Question/inkMuted) e mostra o label do servidor", () => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado="estado_que_ainda_nao_existe" label="Rótulo novo do backend" />);
    expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: "Question", tom: "inkMuted" });
    expect(c.getByText("Rótulo novo do backend")).toBeTruthy();
  });

  it("nunca guarda texto próprio: o rótulo exibido é sempre a prop `label`", () => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado="updated" label="Texto arbitrário do servidor" />);
    expect(c.getByText("Texto arbitrário do servidor")).toBeTruthy();
  });

  it("detalhe só aparece quando passado", () => {
    const { claro: semDetalhe } = renderNosDoisTemas(<ConnectionStatus estado="partial" label="Parcial" />);
    expect(semDetalhe.queryByText("mais detalhe")).toBeNull();

    const { claro: comDetalhe } = renderNosDoisTemas(<ConnectionStatus estado="partial" label="Parcial" detalhe="mais detalhe" />);
    expect(comDetalhe.getByText("mais detalhe")).toBeTruthy();
  });

  it("a cor do texto do label é a mesma do tom do estado", () => {
    const { claro: c } = renderNosDoisTemas(<ConnectionStatus estado="item_missing" label="Conexão perdida" />);
    const estilo = StyleSheet.flatten(c.getByText("Conexão perdida").props.style);
    expect(estilo.color).toBe(claro.danger);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <ConnectionStatus estado="item_missing" label="Conexão perdida" detalhe="Reconecte para continuar." acao={{ rotulo: "Reconectar", onPress: () => {} }} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
