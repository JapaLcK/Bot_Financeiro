import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { View } from "react-native";
import { ErroDeApi, SessaoExpirada } from "@/api/client";
import { responderReceita } from "@/services/painel";
import { Banner } from "@/ui/componentes/Banner";
import { useToast } from "@/ui/componentes/Toast";
import { aviso as vibrarAviso, sucesso } from "@/ui/haptics";
import { Acao, Linha } from "./base";
import { LinhaExpansivel } from "./expansao";
import type { AvisoDaPilha } from "./pilhaAvisos";
import { usePainel, type Dados } from "./provider";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
type Receita = Dados["previsao"]["receitas_do_banco"][number];
type Resposta = "sim" | "nao" | "desfazer";
type Espera = { chave: string; nome: string; resposta: Resposta; postOk: boolean; antes: unknown };
// Status esperado na lista quando o POST pegou, e a fala curta do toast (que o leitor de tela também ouve), sem o nome da receita.
const ESPERADO: Record<Resposta, Receita["status"]> = { sim: "confirmada", nao: "negada", desfazer: "pendente" };
const DITO: Record<Resposta, string> = { sim: "Receita confirmada", nao: "Receita recusada", desfazer: "Resposta desfeita" };
/** O que a caixa devolve a quem a hospeda (`TelaPainel`): as pendentes como avisos da pilha, o "Não deu para salvar" (feedback, fica FORA da pilha) e a linha das respondidas, que NÃO fica no topo: `TelaPainel` a entrega pelo contexto abaixo ao card "Saldo previsto". */
export type Apresentacao = { itens: AvisoDaPilha[]; erro: ReactNode; respondidas: ReactNode };
/** A linha "Receitas na previsão" que o card "Saldo previsto" do Resumo desenha. UMA caixa só (a do topo) manda nas duas pontas: a trava, a espera e a releitura são dela, então Sim/Não no topo e Desfazer no card nunca correm juntos. Só existe provider em `TelaPainel` com `comReceitas`; no chat (e fora do Resumo) o contexto é nulo e nada aparece. */
const ContextoRespondidas = createContext<ReactNode>(null);
export const ProvedorRespondidas = ContextoRespondidas.Provider;
export const useRespondidas = () => useContext(ContextoRespondidas);
/** `key` pelo usuário: zera trava, aviso, espera e lista (nada da conta A aparece na B). Defesa de custo zero: o `PortaoPainel` já desmonta tudo na troca de conta, então tirar o `key` não deixa teste vermelho. */
export const ReceitasDoBanco = ({ children }: { children: (r: Apresentacao) => ReactNode }) => <Caixa key={usePainel().usuario.user_id}>{children}</Caixa>;
/**
 * Receitas que o servidor achou no extrato (`receitas_do_banco` da Previsão): só a que o usuário confirma entra na conta.
 * Sem atualização otimista: a lista é sempre a última leitura do servidor, e os botões só destravam depois da releitura da previsão
 * (a resposta do POST é a do MENOR horizonte, não serve para a tela). Resposta perdida só vira sucesso se a releitura MOSTRA a chave no status
 * esperado; chave ausente, releitura que falhou ou status antigo viram erro (falso erro é melhor que falso sucesso).
 * Nome, chave e valor são do pagador: nada disso vai para analytics ou Sentry.
 */
function Caixa({ children }: { children: (r: Apresentacao) => ReactNode }) {
  const p = usePainel(); const r = p.dados.previsao; const toast = useToast(); const { ampliado } = useTipografiaPainel();
  const [ocupado, setOcupado] = useState(false); const [falhou, setFalhou] = useState(false); const [espera, setEspera] = useState<Espera | null>(null); const [abertas, setAbertas] = useState(false);
  const voo = useRef<symbol | null>(null); const viu = useRef(false); const ultima = useRef<Receita[]>([]); const atual = useRef(r); atual.current = r;
  const pronto = r?.fase === "pronto" ? r.dado.receitas_do_banco : null;
  if (pronto) ultima.current = pronto;
  // Durante qualquer releitura parcial (a deste POST, a troca de horizonte) a lista anterior fica na tela, em vez de a caixa piscar.
  const lista = pronto ?? (r?.fase === "carregando" ? ultima.current : []);
  // Estado do aviso (`falhou`) x previsão `r`: aparece só com r em pronto/erro/carregando; recarga completa (r undefined) e `negado` escondem; leitura boa ou recarga APAGAM o estado, para o Banner não remontar (ele anuncia ao montar) com aviso velho.
  // Troca de horizonte (carregando) e `erro` mantêm o aviso. `negado` não apaga: só se sai dele passando por undefined (recarga), que apaga.
  // DECLARADO ANTES do efeito abaixo de propósito: a releitura que acompanha a falha chega no mesmo passe, e é o de baixo que acende o aviso depois deste apagar.
  useEffect(() => { if (!r || r.fase === "pronto") setFalhou(false); }, [r]);
  useEffect(() => {
    if (!espera) return;
    // Só julga a leitura que ESTA resposta pediu: ela começa num `carregando` NOVO (ou `undefined`, recarga completa). Leitura que já estava no ar (horizonte) pode cair antes e é dado de antes do POST.
    if (!r || (r.fase === "carregando" && r !== espera.antes)) { viu.current = true; return; }
    if (!viu.current || r.fase === "carregando") return;
    const pegou = r.fase === "pronto" && r.dado.receitas_do_banco.some((x) => x.chave === espera.chave && x.status === ESPERADO[espera.resposta]);
    // O Toast já anuncia ao leitor de tela: anunciar aqui também falaria duas vezes.
    if (espera.postOk || pegou) { toast.mostrar({ mensagem: DITO[espera.resposta], tom: "sucesso" }); sucesso(); } else { setFalhou(true); vibrarAviso(); }
    setEspera(null); voo.current = null; setOcupado(false);
  }, [r, espera, toast]);
  const responder = async (rec: Receita, resposta: Resposta) => {
    if (voo.current) return;
    const token = Symbol(); voo.current = token; setOcupado(true); setFalhou(false);
    const soltar = () => { if (voo.current === token) { voo.current = null; setOcupado(false); } };
    const op = p.operacao(); let erro: unknown = null;
    try { await responderReceita(rec.chave, resposta, op.controlador); } catch (e) { erro = e; }
    if (!op.atual()) return soltar(); // segundo plano, trava, puxar para atualizar ou troca de conta: sem aviso, a recarga completa mostra a verdade
    if (erro) { p.falhou(erro); if (erro instanceof SessaoExpirada || (erro instanceof ErroDeApi && erro.status === 402)) return soltar(); }
    viu.current = false; setEspera({ chave: rec.chave, nome: rec.nome, resposta, postOk: !erro, antes: atual.current }); p.relerPrevisao();
  };
  if (!r || r.fase === "negado" || (!lista.length && !falhou)) return <>{children({ itens: [], erro: null, respondidas: null })}</>; // negado (plano sem Previsão, p.ex. caiu para Essencial com o app aberto): nem aviso nem erro; `erro` mantém o aviso, que o "Tentar novamente" do card apaga
  const pendentes = lista.filter((x) => x.status === "pendente"), respondidas = lista.filter((x) => x.status !== "pendente");
  const itens: AvisoDaPilha[] = pendentes.map((x) => ({ id: `receita:${x.chave}`, tom: "decisao", icone: "Wallet", titulo: "Receitas do extrato", corpo: <Linha titulo={x.nome} valor={x.valor} detalhe={`todo dia ${x.dia}. É sua receita fixa?`}>
    <View style={{ flexDirection: ampliado ? "column" : "row", flexWrap: "wrap", alignItems: "flex-start", gap: 8 }}>
      <Acao texto="Sim" rotulo={`Sim, é receita fixa: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "sim")} />
      <Acao texto="Não" rotulo={`Não é receita fixa: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "nao")} />
    </View>
  </Linha> }));
  return <>{children({
    itens,
    erro: falhou ? <Banner tom="danger" mensagem="Não deu para salvar. Tente de novo." /> : null,
    respondidas: respondidas.length > 0 ? <View testID="receitas-respondidas"><LinhaExpansivel aberta={abertas} alternar={() => setAbertas((a) => !a)} linha={<Texto variante="rotulo" tom="inkMuted">{`Receitas na previsão (${respondidas.length})`}</Texto>}>
      {respondidas.map((x) => <Linha key={x.chave} titulo={x.nome} valor={x.valor} detalhe={`${x.status === "confirmada" ? "Confirmada" : "Recusada"} · todo dia ${x.dia}`}>
        <Acao texto="Desfazer" rotulo={`Desfazer resposta: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "desfazer")} />
      </Linha>)}
    </LinhaExpansivel></View> : null,
  })}</>;
}
