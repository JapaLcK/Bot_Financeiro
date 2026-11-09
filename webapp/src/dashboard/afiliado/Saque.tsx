import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Confirma } from "../conta/Confirma";
import { money } from "../lib/format.js";
import { afiliadoQuery, falhaSaque, solicitarSaque, type Afiliado } from "./api";

// O saque é sempre do disponível inteiro, e o servidor decide o valor. A chave Pix só vive neste estado (nunca em storage, URL, cache ou log): sucesso a limpa, falha a mantém para tentar de novo.
// Este componente fica montado enquanto houver dado, então uma releitura não apaga o que foi digitado. `velho`: a releitura falhou; o valor na tela pode não ser o que o servidor sacaria.
export function Saque({ d, velho }: { d: Afiliado; velho: boolean }) {
  const qc = useQueryClient();
  const [chave, setChave] = useState("");
  const [confirmando, setConfirmando] = useState(false);
  const [falha, setFalha] = useState("");
  const [feito, setFeito] = useState(false);
  const aberto = d.stats.requested > 0 || d.payouts.some((p) => p.status === "requested");
  const abaixo = d.stats.available < d.min_payout;
  const pode = !aberto && !abaixo && !velho;
  const limpa = chave.trim();
  // Depois do sucesso a releitura trava o botão que tinha o foco, e ele cairia no body: vai ao título desta seção, à vista da confirmação.
  useEffect(() => { if (feito && !pode) document.getElementById("afil-saque")?.focus(); }, [feito, pode]);

  const pedir = (e: FormEvent) => {
    e.preventDefault();
    if (!pode) return;
    setFeito(false);
    if (limpa.length < 5) { setFalha("Informe sua chave Pix."); return; }
    setFalha("");
    setConfirmando(true);
  };
  const agir = async () => {
    try { await solicitarSaque(limpa); setChave(""); setFeito(true); }
    catch (e) { setFalha(falhaSaque(e)); }
    // Sucesso ou falha, quem diz o estado é o servidor: a resposta perdida pode ter criado o saque. Sem `await`: a releitura não prende o diálogo.
    void qc.invalidateQueries({ queryKey: afiliadoQuery.queryKey });
  };

  return <section className="panel span-5" aria-labelledby="afil-saque">
    <h2 id="afil-saque" tabIndex={-1}>Solicitar saque</h2>
    <p className="faint">O saque é do valor disponível inteiro, com mínimo de {money(d.min_payout)}, e o pagamento sai por Pix em alguns dias úteis.</p>
    <form className="afil-form" onSubmit={pedir} autoComplete="off">
      <label htmlFor="afil-pix">Sua chave Pix</label>
      <input id="afil-pix" className="afil-campo" type="text" value={chave} maxLength={140} disabled={!pode} autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="CPF, e-mail ou celular" onChange={(e) => { setChave(e.target.value); setFalha(""); setFeito(false); }} />
      <button type="submit" className="btn btn-primary" disabled={!pode}>Sacar {money(d.stats.available)}</button>
    </form>
    {aberto ? <p className="faint">Você já tem um saque em análise.</p> : abaixo && <p className="faint">Você ainda não chegou ao mínimo de {money(d.min_payout)}.</p>}
    {falha && <p role="alert" className="warn">{falha}</p>}
    <p role="status" className="gain afil-status">{feito ? "Saque solicitado. Acompanhe no histórico." : ""}</p>
    {confirmando && <Confirma titulo="Confirmar saque?" texto={`Saque de ${money(d.stats.available)} para a chave Pix ${limpa}. Confira a chave: é para ela que o pagamento vai.`} botao="Confirmar saque" pendente="Solicitando…" fechar={() => setConfirmando(false)} agir={agir} />}
  </section>;
}
