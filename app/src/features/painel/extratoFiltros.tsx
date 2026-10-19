import DateTimePicker, { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { useState } from "react";
import { Platform, View } from "react-native";
import { AmountInput } from "@/ui/componentes/AmountInput";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { reais as brl } from "@/ui/componentes/CurvaAcumulada";
import { useTema } from "@/ui/tema";
import { Acao, Filtros, Folha } from "./base";
import { dataPtBr, hojeSP } from "./catalogo";
import { dataDoSeletor, isoDoSeletor } from "./metasEscrita";
import { ButtonPainel as Button, ChipPainel as Chip, TextoPainel as Texto } from "./tipografia";

// "Registro antigo" só rotula o detalhe da linha: o filtro de origem não o oferece.
export const ORIGENS = [["banco", "Banco"], ["cartao", "Cartão"], ["carteira", "Carteira"], ["registro_antigo", "Registro antigo"]] as const;
const TIPOS = [["", "Tudo"], ["entrada", "Entradas"], ["saida", "Saídas"]] as const;
/** Atalhos da tela (tipo, origem, rótulo). Espelho do `ATALHOS` do /painel (`webapp/src/dashboard/lib/extrato-dias.ts`): um teste do frontend compara os dois. */
export const ATALHOS = [["", "", "Tudo"], ["entrada", "", "Entradas"], ["saida", "", "Saídas"], ["", "cartao", "Cartão"]] as const;
/** O atalho que representa EXATAMENTE o par (tipo, origem) atual; combinação fora deles (ex.: saída + banco) não seleciona nenhum. */
export const atalhoDe = (f: Pick<FiltrosExtrato, "tipo" | "origem">) => ATALHOS.find(([tipo, origem]) => tipo === f.tipo && origem === f.origem);
/** `dia` é "AAAA-MM-DD" ou ""; `min`/`max` em centavos, 0 = sem limite. */
export type FiltrosExtrato = { tipo: string; origem: string; dia: string; min: number; max: number };
export const SEM_FILTROS: FiltrosExtrato = { tipo: "", origem: "", dia: "", min: 0, max: 0 };

type Ficha = { rotulo: string; texto: string; tirar: () => void };
/** Os filtros ativos, um por ficha; `f.length` é a contagem do botão. A busca e o mês não contam. Tipo/origem que um atalho já mostra não geram ficha. `categoria` (route param) entra com o rótulo já resolvido. Com `oculto`, a ficha de valor não mostra a faixa. */
export function fichasAtivas(f: FiltrosExtrato, aplicar: (n: FiltrosExtrato) => void, categoria?: { nome: string; tirar: () => void }, oculto = false): Ficha[] {
  const atalho = atalhoDe(f), tipo = TIPOS.find(([id]) => id === f.tipo)?.[1], origem = ORIGENS.find(([id]) => id === f.origem)?.[1] ?? f.origem;
  const valor = oculto ? "Valor" : f.min && f.max ? `${brl(f.min)} a ${brl(f.max)}` : f.min ? `A partir de ${brl(f.min)}` : `Até ${brl(f.max)}`;
  return [
    ...(f.tipo && !atalho ? [{ rotulo: `Limpar filtro de ${tipo}`, texto: tipo!, tirar: () => aplicar({ ...f, tipo: "" }) }] : []),
    ...(f.origem && !atalho ? [{ rotulo: `Limpar filtro de origem ${origem}`, texto: origem, tirar: () => aplicar({ ...f, origem: "" }) }] : []),
    ...(f.dia ? [{ rotulo: `Limpar filtro do dia ${dataPtBr(f.dia)}`, texto: dataPtBr(f.dia), tirar: () => aplicar({ ...f, dia: "" }) }] : []),
    ...(f.min || f.max ? [{ rotulo: "Limpar filtro de valor", texto: valor, tirar: () => aplicar({ ...f, min: 0, max: 0 }) }] : []),
    ...(categoria ? [{ rotulo: `Limpar filtro de ${categoria.nome}`, texto: categoria.nome, tirar: categoria.tirar }] : []),
  ];
}
export const FichasAtivas = ({ fichas }: { fichas: Ficha[] }) => fichas.length ? <Filtros>{fichas.map((x) => <Acao key={x.rotulo} compacta ativa rotulo={x.rotulo} texto={x.texto} iconeFim="X" onPress={x.tirar} />)}</Filtros> : null;

/** Rascunho local: a consulta só muda em "Aplicar filtros" (ou "Limpar tudo"), nunca a cada toque. O dia fica limitado ao mês que a tela mostra. */
function Corpo({ atual, mes, aplicar, limpar }: { atual: FiltrosExtrato; mes: string; aplicar: (n: FiltrosExtrato) => void; limpar: () => void }) {
  // Dia de OUTRO mês (vindo de um link) entra no rascunho como "Qualquer dia": o seletor não o mostraria e "Aplicar" o reenviaria às cegas.
  const { esquema } = useTema(); const [r, setR] = useState({ ...atual, dia: atual.dia.startsWith(mes) ? atual.dia : "" }); const [invertido, setInvertido] = useState(false);
  const mudar = (p: Partial<FiltrosExtrato>) => { setR((x) => ({ ...x, ...p })); setInvertido(false); };
  const android = Platform.OS === "android"; const hoje = hojeSP();
  const sugestao = hoje.startsWith(mes) ? hoje : `${mes}-01`, valorDia = dataDoSeletor(r.dia || sugestao);
  const limites = { minimumDate: dataDoSeletor(`${mes}-01`), maximumDate: new Date(Number(mes.slice(0, 4)), Number(mes.slice(5, 7)), 0, 12) }; // dia 0 do mês seguinte = último deste
  const abrirDia = () => DateTimePickerAndroid.open({ value: valorDia, mode: "date", ...limites, onChange: (e, d) => { if (e.type === "set" && d) mudar({ dia: isoDoSeletor(d) }); } });
  const confirmar = () => { if (r.min && r.max && r.min > r.max) setInvertido(true); else aplicar(r); };
  return <View style={{ gap: 20 }}>
    <View style={{ gap: 8 }}><Texto variante="rotulo">Tipo</Texto><SegmentedControl opcoes={TIPOS.map(([, nome]) => nome)} valor={TIPOS.find(([id]) => id === r.tipo)![1]} onChange={(nome) => mudar({ tipo: TIPOS.find(([, n]) => n === nome)![0] })} /></View>
    <View style={{ gap: 8 }}><Texto variante="rotulo">Origem</Texto><View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>{([["", "Todas"], ...ORIGENS.filter(([id]) => id !== "registro_antigo")] as const).map(([id, nome]) => <Chip key={id} rotulo={nome} selecionado={r.origem === id} onPress={() => mudar({ origem: id })} />)}</View></View>
    <GrupoDeLinhas>
      {r.dia && !android
        ? <ListRow titulo="Dia" trailing={<DateTimePicker value={valorDia} mode="date" display="compact" {...limites} themeVariant={esquema} locale="pt-BR" accessibilityLabel="Dia" onChange={(_, d) => { if (d) mudar({ dia: isoDoSeletor(d) }); }} />} />
        : <ListRow titulo="Dia" trailing={<Texto tom="inkMuted">{r.dia ? dataPtBr(r.dia) : "Qualquer dia"}</Texto>} chevron onPress={android ? abrirDia : () => mudar({ dia: sugestao })} />}
      {r.dia ? <ListRow titulo="Qualquer dia" onPress={() => mudar({ dia: "" })} /> : null}
    </GrupoDeLinhas>
    <AmountInput rotulo="Valor mínimo" centavos={r.min} onChange={(min) => mudar({ min })} />
    <AmountInput rotulo="Valor máximo" centavos={r.max} onChange={(max) => mudar({ max })} erro={invertido ? "O máximo não pode ser menor que o mínimo." : undefined} />
    <Texto variante="legenda" tom="inkMuted">0,00 = sem limite.</Texto>
    <Button rotulo="Aplicar filtros" onPress={confirmar} />
    <Button rotulo="Limpar tudo" variante="secondary" onPress={limpar} />
  </View>;
}
export function FolhaFiltros({ aberta, fechar, ...resto }: { aberta: boolean; fechar: () => void; atual: FiltrosExtrato; mes: string; aplicar: (n: FiltrosExtrato) => void; limpar: () => void }) {
  return <Folha titulo="Filtros" aberta={aberta} fechar={fechar}>{aberta && <Corpo {...resto} />}</Folha>;
}
