import NumberFlow from "@number-flow/react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

const FORMATOS = {
  monthly: { minimumFractionDigits: 2, maximumFractionDigits: 2 },
  annual: { minimumFractionDigits: 0, maximumFractionDigits: 0 },
};

function lerValor(elemento) {
  const texto = (elemento?.textContent || "").replace(/\s+/g, " ").trim();
  const correspondencia = texto.match(/^R\$\s*([\d.]+(?:,\d+)?)\s*\/(?:mês|ano)$/);
  if (!correspondencia) return Number.NaN;
  return Number(correspondencia[1].replace(/\./g, "").replace(",", "."));
}

function PrecoAnimado({ ciclo, mensal, anual }) {
  const cicloAnual = ciclo === "annual";

  return (
    <span
      className="price-flow-display"
      data-cycle={ciclo}
      data-direction={cicloAnual ? "up" : "down"}
      aria-hidden="true"
    >
      <span className="price-flow-prefix" data-text={"R$\u00a0"} />
      <NumberFlow
        value={cicloAnual ? anual : mensal}
        locales="pt-BR"
        format={FORMATOS[ciclo]}
        trend={cicloAnual ? 1 : -1}
      />
      {/* O suffix do NumberFlow trata texto como símbolo e só faz fade. Este
          trilho dá à unidade o mesmo sentido vertical dos dígitos. */}
      <span className="price-flow-unit" aria-hidden="true">
        <span className="price-flow-unit-track" data-monthly="/mês" data-annual="/ano" />
      </span>
    </span>
  );
}

/**
 * Melhoria progressiva: os preços originais continuam no DOM e sustentam
 * acessibilidade, checkout e fallback. Cada NumberFlow vive em uma raiz React
 * separada para que sua atualização não remonte os cards nem apague handlers
 * imperativos aplicados pelos scripts de assinatura.
 */
export function montarPriceFlows(raiz, cicloInicial = "monthly") {
  const precos = [...raiz.querySelectorAll("article.plan .price")];
  const valores = precos.map((preco) => ({
    preco,
    mensal: lerValor(preco.querySelector("[data-price-monthly]")),
    anual: lerValor(preco.querySelector("[data-price-annual]")),
  }));

  // Tudo ou nada: markup fora do contrato mantém os preços estáticos visíveis.
  if (valores.length === 0 || valores.some(({ mensal, anual }) =>
    !Number.isFinite(mensal) || !Number.isFinite(anual))) return;

  const fluxos = valores.map((dados) => {
    const host = document.createElement("span");
    host.className = "price-flow-host";
    dados.preco.append(host);
    return { ...dados, root: createRoot(host) };
  });

  const renderizar = (ciclo) => {
    flushSync(() => {
      fluxos.forEach(({ root, mensal, anual }) => {
        root.render(<PrecoAnimado ciclo={ciclo} mensal={mensal} anual={anual} />);
      });
    });
  };

  renderizar(cicloInicial);
  fluxos.forEach(({ preco }) => preco.classList.add("has-number-flow"));
  globalThis.pbPriceFlowSetCycle = renderizar;
}
