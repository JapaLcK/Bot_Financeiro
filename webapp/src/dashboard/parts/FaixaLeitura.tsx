import { useLayoutEffect } from "react";

// A faixa da sessão de leitor (credencial de visualização). Fixa no topo ao rolar; a altura vai para `--faixa-h` no <html>
// (styles/leitor.css) para o trilho e a barra do topo, que também são fixos, começarem abaixo dela.
export function FaixaLeitura() {
  useLayoutEffect(() => {
    document.documentElement.dataset.leitor = "";
    return () => { delete document.documentElement.dataset.leitor; };
  }, []);
  return <div className="faixa-leitura" role="status"><i className="ph ph-eye" aria-hidden="true" />Modo visualização · só leitura</div>;
}
