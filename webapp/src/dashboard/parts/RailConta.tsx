import { useEffect, useState } from "react";
import { CONTA, href, route, type Path } from "../router";
import { sair } from "../lib/v2";

// O grupo "Conta" do menu lateral (só o do desktop; o Menu do celular, MenuTelas.tsx, lista tudo em folha rolável): o subtítulo é o botão que recolhe.
// Cada navegação põe o grupo no padrão da rota nova: aberto numa tela da Conta, fechado nas outras (o que o clique fez na tela anterior não vale). Sem storage. Fechado dentro de uma tela da Conta, o item da página atual
// continua à vista (nunca some o "você está aqui"). O Sair fica FORA do que recolhe (decisão do dono): um clique de qualquer tela, no pé do trilho, abaixo do grupo.
export function RailConta({ path }: { path: Path }) {
  const emConta = CONTA.includes(path);
  const [aberto, setAberto] = useState(emConta);
  useEffect(() => setAberto(emConta), [path]); // toda navegação volta ao padrão da rota nova; o clique só vale dentro da mesma tela
  return (
    <div className="rail-conta" role="group" aria-labelledby="rail-conta-t">
      <button type="button" id="rail-conta-t" className="rail-conta-t" title="Conta" aria-expanded={aberto} aria-controls="rail-conta-lista" onClick={() => setAberto(!aberto)}>
        <span className="rail-label">Conta</span><i className="ph ph-caret-right" aria-hidden="true" />
      </button>
      <ul id="rail-conta-lista">
        {CONTA.map(route).map((r) => (
          <li key={r.path} hidden={!aberto && path !== r.path}>
            <a href={href(r.path)} aria-current={path === r.path ? "page" : undefined} title={r.label}>
              <i className={`ph ${r.icon}`} aria-hidden="true" /><span className="rail-label">{r.label}</span>
            </a>
          </li>
        ))}
      </ul>
      <ul>
        <li><button type="button" title="Sair" onClick={sair}><i className="ph ph-sign-out" aria-hidden="true" /><span className="rail-label">Sair</span></button></li>
      </ul>
    </div>
  );
}
