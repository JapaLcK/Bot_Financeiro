import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, View } from "react-native";
import { ErroDeApi, SessaoExpirada } from "@/api/client";
import { responderReceita } from "@/services/painel";
import { Banner } from "@/ui/componentes/Banner";
import { Card } from "@/ui/componentes/Card";
import { aviso as vibrarAviso, sucesso } from "@/ui/haptics";
import { Acao, Linha } from "./base";
import { LinhaExpansivel } from "./expansao";
import { usePainel, type Dados } from "./provider";
import { TextoPainel as Texto } from "./tipografia";
type Receita = Dados["previsao"]["receitas_do_banco"][number];
type Resposta = "sim" | "nao" | "desfazer";
type Espera = { chave: string; nome: string; resposta: Resposta; postOk: boolean; antes: unknown };
// Mesmos status e falas da web (`widgets/ReceitasDoBanco.tsx`): o que a lista passa a ter quando o POST pegou, e o que o leitor de tela ouve.
const ESPERADO: Record<Resposta, Receita["status"]> = { sim: "confirmada", nao: "negada", desfazer: "pendente" };
const DITO: Record<Resposta, string> = { sim: "confirmada.", nao: "recusada.", desfazer: "resposta desfeita." };
/** `key` pelo usuário: zera trava, aviso, espera e lista (nada da conta A aparece na B). Defesa de custo zero: o `PortaoPainel` já desmonta tudo na troca de conta, então tirar o `key` não deixa teste vermelho. */
export const ReceitasDoBanco = () => <Caixa key={usePainel().usuario.user_id} />;
/**
 * Receitas que o servidor achou no extrato (`receitas_do_banco` da Previsão): só a que o usuário confirma entra na conta.
 * Sem atualização otimista: a lista é sempre a última leitura do servidor, e os botões só destravam depois da releitura da previsão
 * (a resposta do POST é a do MENOR horizonte, não serve para a tela). Resposta perdida só vira sucesso se a releitura MOSTRA a chave no status
 * esperado; chave ausente, releitura que falhou ou status antigo viram erro (falso erro é melhor que falso sucesso).
 * Nome, chave e valor são do pagador: nada disso vai para analytics ou Sentry.
 */
function Caixa() {
  const p = usePainel(); const r = p.dados.previsao;
  const [ocupado, setOcupado] = useState(false); const [falhou, setFalhou] = useState(false); const [espera, setEspera] = useState<Espera | null>(null); const [abertas, setAbertas] = useState(false);
  const voo = useRef<symbol | null>(null); const ultima = useRef<Receita[]>([]); const atual = useRef(r); atual.current = r;
  const pronto = r?.fase === "pronto" ? r.dado.receitas_do_banco : null;
  if (pronto) ultima.current = pronto;
  // Durante qualquer releitura parcial (a deste POST, a troca de horizonte) a lista anterior fica na tela, em vez de a caixa piscar.
  const lista = pronto ?? (r?.fase === "carregando" ? ultima.current : []);
  // Estado do aviso (`falhou`) x previsão `r`: aparece só com r em pronto/erro/carregando; recarga completa (r undefined) e `negado` escondem; leitura boa ou recarga APAGAM o estado, para o Banner não remontar (ele anuncia ao montar) com aviso velho.
  // Troca de horizonte (carregando) e `erro` mantêm o aviso. `negado` não apaga: só se sai dele passando por undefined (recarga), que apaga.
  // DECLARADO ANTES do efeito abaixo de propósito: a releitura que acompanha a falha chega no mesmo passe, e é o de baixo que acende o aviso depois deste apagar.
  useEffect(() => { if (!r || r.fase === "pronto") setFalhou(false); }, [r]);
  useEffect(() => {
    if (!espera || !r || r === espera.antes || r.fase === "carregando") return;
    const pegou = r.fase === "pronto" && r.dado.receitas_do_banco.some((x) => x.chave === espera.chave && x.status === ESPERADO[espera.resposta]);
    if (espera.postOk || pegou) { AccessibilityInfo.announceForAccessibility(`${espera.nome}: ${DITO[espera.resposta]}`); sucesso(); } else { setFalhou(true); vibrarAviso(); }
    setEspera(null); voo.current = null; setOcupado(false);
  }, [r, espera]);
  const responder = async (rec: Receita, resposta: Resposta) => {
    if (voo.current) return;
    const token = Symbol(); voo.current = token; setOcupado(true); setFalhou(false);
    const soltar = () => { if (voo.current === token) { voo.current = null; setOcupado(false); } };
    const op = p.operacao(); let erro: unknown = null;
    try { await responderReceita(rec.chave, resposta, op.controlador); } catch (e) { erro = e; }
    if (!op.atual()) return soltar(); // segundo plano, trava, puxar para atualizar ou troca de conta: sem aviso, a recarga completa mostra a verdade
    if (erro) { p.falhou(erro); if (erro instanceof SessaoExpirada || (erro instanceof ErroDeApi && erro.status === 402)) return soltar(); }
    setEspera({ chave: rec.chave, nome: rec.nome, resposta, postOk: !erro, antes: atual.current }); p.relerPrevisao();
  };
  if (!r || r.fase === "negado" || (!lista.length && !falhou)) return null; // negado (plano sem Previsão, p.ex. caiu para Essencial com o app aberto): nem caixa nem aviso; `erro` mantém o aviso, que o "Tentar novamente" do card apaga
  const pendentes = lista.filter((x) => x.status === "pendente"), respondidas = lista.filter((x) => x.status !== "pendente");
  return <View testID="receitas-do-extrato"><Card><View style={{ gap: 8 }}>
    <Texto variante="rotulo" tom="inkMuted" accessibilityRole="header">Receitas do extrato</Texto>
    {falhou && <Banner tom="danger" mensagem="Não deu para salvar. Tente de novo." />}
    {pendentes.map((x) => <Linha key={x.chave} titulo={x.nome} valor={x.valor} detalhe={`todo dia ${x.dia}. É sua receita fixa?`}>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
        <Acao texto="Sim" rotulo={`Sim, é receita fixa: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "sim")} />
        <Acao texto="Não" rotulo={`Não é receita fixa: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "nao")} />
      </View>
    </Linha>)}
    {respondidas.length > 0 && <LinhaExpansivel aberta={abertas} alternar={() => setAbertas((a) => !a)} linha={<Texto variante="rotulo">{`Já respondidas (${respondidas.length})`}</Texto>}>
      {respondidas.map((x) => <Linha key={x.chave} titulo={x.nome} valor={x.valor} detalhe={`${x.status === "confirmada" ? "Confirmada" : "Recusada"} · todo dia ${x.dia}`}>
        <Acao texto="Desfazer" rotulo={`Desfazer resposta: ${x.nome}`} desativado={ocupado} onPress={() => void responder(x, "desfazer")} />
      </Linha>)}
    </LinhaExpansivel>}
  </View></Card></View>;
}
