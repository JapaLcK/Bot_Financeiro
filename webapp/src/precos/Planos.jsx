/**
 * Os cards de plano da /precos.
 *
 * SEM ESTADO, de propósito, e isso é o contrato com o script clássico da página:
 * o React renderiza UMA vez e sai do caminho. Quem manda no ciclo continua sendo
 * o `setCycle` (mensal/anual por `style.display` nos `[data-price-*]`), quem
 * marca "Indisponível" continua sendo o `markUnavailable`, e quem troca o rótulo
 * do botão continua sendo o `refreshPlanButtons`. Todos mutam o DOM. Se este
 * componente tivesse estado e rerenderizasse, ele desfaria as três coisas.
 *
 * Os dados vêm do `lerPlanos` — nenhum preço, nome ou feature mora aqui.
 */

/**
 * Atributos que o JSX não sabe escrever crus.
 *
 * `onclick` minúsculo o React não emite (ele só conhece `onClick`, e ali o
 * `this` do handler seria outro), e `style` como string o JSX recusa. Os dois
 * são atributos do markup de hoje e o contrato manda preservá-los atributo por
 * atributo, então vão por `setAttribute`.
 */
const crus = (attrs) => (el) => {
  if (!el) return;
  for (const [nome, valor] of Object.entries(attrs)) {
    if (valor != null) el.setAttribute(nome, valor);
  }
};

function Cartao({ plano }) {
  const { botao } = plano;
  return (
    <article
      className={plano.featured ? "plan featured" : "plan"}
      ref={crus({ style: plano.estilo })}
    >
      {plano.badge && (
        <span
          className="plan-badge"
          ref={crus({ style: plano.badge.estilo })}
          dangerouslySetInnerHTML={{ __html: plano.badge.html }}
        />
      )}
      <h3>{plano.titulo}</h3>
      <div className="plan-sub">{plano.sub}</div>
      <div className="price-block" dangerouslySetInnerHTML={{ __html: plano.precoHtml }} />
      <ul>
        {plano.itens.map((html, i) => (
          // A chave é o índice porque a lista é ESTÁTICA: o componente nunca
          // rerenderiza (ver o topo do arquivo), então não há reordenação nem
          // remoção para uma chave estável proteger.
          // eslint-disable-next-line react/no-array-index-key
          <li key={i} dangerouslySetInnerHTML={{ __html: html }} />
        ))}
      </ul>
      <button
        className={botao.classe}
        type="button"
        data-plan-btn={botao.plano ?? undefined}
        data-unavailable={botao.indisponivel ?? undefined}
        disabled={botao.disabled}
        ref={crus({ onclick: botao.onclick })}
      >
        {botao.texto}
      </button>
    </article>
  );
}

export function Planos({ planos }) {
  return planos.map((plano) => <Cartao key={plano.titulo} plano={plano} />);
}
