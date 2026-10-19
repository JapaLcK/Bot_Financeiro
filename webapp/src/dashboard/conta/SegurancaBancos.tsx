// O aviso do fim da página: de onde vêm os dados e que a pessoa pode revogar. O link é externo (Banco Central): nova aba, sem `opener`.
const BCB_OPEN_FINANCE ="https://www.bcb.gov.br/estabilidadefinanceira/openfinance";

export const SegurancaBancos = () => (
  <section className="panel banco-seguro" aria-label="Segurança">
    <span className="resumo-ico" aria-hidden="true"><i className="ph ph-lock" /></span>
    <span className="resumo-txt">
      <b>Seus dados estão seguros</b>
      <span>Utilizamos o Open Finance, um padrão do Banco Central. Você pode revogar as conexões a qualquer momento.</span>
    </span>
    <a className="banco-saiba" href={BCB_OPEN_FINANCE} target="_blank" rel="noopener noreferrer" aria-label="Saiba mais sobre o Open Finance (abre em nova aba)">
      Saiba mais<i className="ph ph-arrow-square-out" aria-hidden="true" />
    </a>
  </section>
);
