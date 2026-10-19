import { Tabs } from "expo-router";
import { PainelProvider, useEstadoPainel } from "@/features/painel/provider";
import { ConversaProvider } from "@/features/painel/conversaEstado";
import { TipografiaPainelProvider } from "@/features/painel/tipografia";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { useTema } from "@/ui/tema";
import { texto } from "@/ui/tokens";
export const unstable_settings = { initialRouteName: "resumo" };
function Abas() {
 const { cores } = useTema(); const p = useEstadoPainel();
 const tabs: { name: string; title: string; icon: NomeIcone }[] = [{ name: "resumo", title: "Resumo", icon: "House" }, { name: "gastos", title: "Gastos", icon: "ChartBar" }, { name: "piggy", title: "Piggy", icon: "ChatCircle" }, { name: "metas", title: "Metas", icon: "Target" }, { name: "extrato", title: "Extrato", icon: "ListBullets" }];
 return <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: cores.ink, tabBarInactiveTintColor: cores.inkFaint, tabBarStyle: { backgroundColor: cores.bg, borderTopColor: cores.border, display: p.gate === "liberado" && !p.travado ? "flex" : "none" }, tabBarLabelStyle: { fontFamily: texto.legenda.fontFamily, fontSize: 11 } }}>{tabs.map((t) => <Tabs.Screen key={t.name} name={t.name} options={{ title: t.title, tabBarAccessibilityLabel: `Aba ${t.title}`, tabBarIcon: ({ focused }) => <Icone nome={t.icon} tom={focused ? "brand" : "inkFaint"} /> }} />)}</Tabs>;
}
export default function LayoutPainel() { return <TipografiaPainelProvider><PainelProvider><ConversaProvider><Abas /></ConversaProvider></PainelProvider></TipografiaPainelProvider>; }
