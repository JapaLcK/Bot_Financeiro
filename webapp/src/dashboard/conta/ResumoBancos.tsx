import type { ReactNode } from "react";
import { quandoSync, tomDoEstado, ultimaSync } from "../lib/bancos";
import type { Conexao } from "./api";

// O card do topo: quantos bancos, a sincronização mais recente entre eles e o "Conectar banco" (`conectar`: o link, ou a mensagem do servidor quando não há vaga).
// "Concluída" só com todos os bancos em tom ok (`tomDoEstado`, o mesmo das pílulas); senão "Atenção" no tom do pior (erro vence pendente). `vagas` ("2 de 3 bancos"): discreto, só com teto.
// Sem `conectar` (sem vaga e sem mensagem, ou o /limite ainda lendo) a 3ª zona nem existe: o divisor dela só nasce junto com o botão. Sem banco nenhum o ladrilho é neutro (nem verde nem alerta).
export function ResumoBancos({ lista, vagas, conectar }: { lista: Conexao[]; vagas: string | null; conectar: ReactNode }) {
  const n = lista.length;
  const tons = lista.map((c) => tomDoEstado(c.ui.state));
  const tom = tons.includes("erro") ? "erro" : tons.includes("pendente") ? "pendente" : "ok";
  return (
    <section className="panel banco-resumo" aria-label="Resumo dos bancos">
      <div className="resumo-zona">
        <span className="resumo-ico" aria-hidden="true"><i className="ph ph-bank" /></span>
        <span className="resumo-txt">
          <b className="num resumo-n">{n}</b>
          <span>{n === 1 ? "banco conectado" : "bancos conectados"}</span>
          {vagas && <span className="banco-leg">{vagas}</span>}
        </span>
      </div>
      <div className="resumo-zona">
        <span className={`resumo-ico${n ? ` estado-${tom}` : ""}`} aria-hidden="true"><i className={`ph ${!n ? "ph-clock" : tom !== "ok" ? "ph-warning" : "ph-check-circle"}`} /></span>
        <span className="resumo-txt">
          <span className="banco-leg">Última sincronização</span>
          <b className="resumo-quando">{quandoSync(ultimaSync(lista.map((c) => c.last_sync_at)))}</b>
          {n > 0 && <span className={`banco-estado estado-${tom}`}><span className="ponto" aria-hidden="true" />{tom === "ok" ? "Concluída" : "Atenção"}</span>}
        </span>
      </div>
      {conectar && <div className="resumo-conectar">{conectar}</div>}
    </section>
  );
}
