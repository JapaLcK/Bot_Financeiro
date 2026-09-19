# Pesquisa: emissão automática de NFS-e no PigBank

**Data da verificação:** 15/09/2026  
**Escopo:** empresa ME/EPP optante pelo Simples Nacional, não MEI; cobranças recorrentes do PigBank; fontes oficiais do Governo Federal, Stripe e Asaas.

## Resumo executivo

A recomendação para o PigBank é usar **um único emissor fiscal, o Asaas, integrado ao Portal Nacional da NFS-e**, e manter Stripe e Asaas apenas como origens financeiras:

- cobrança de cartão liquidada na Stripe (`invoice.paid`) cria uma NFS-e **avulsa** no Asaas, vinculada ao cliente e identificada por `stripe:<invoice_id>`;
- cobrança Pix liquidada no Asaas (`PAYMENT_RECEIVED`) cria uma NFS-e vinculada ao próprio `payment`, identificada por `asaas:<payment_id>`;
- a emissão só é considerada concluída em `INVOICE_AUTHORIZED`, nunca na resposta de agendamento;
- cancelamentos, estornos e correções entram em uma fila fiscal e seguem uma política validada pelo contador; cancelar uma assinatura não cancela notas de períodos já prestados.

Esse caminho reaproveita o provedor e o modelo de outbox que o projeto já possui. A integração direta com a SEFIN Nacional é possível, mas exige credenciamento, certificado ICP-Brasil, mTLS, XML assinado, GZip/base64, numeração da DPS, leiautes e regras municipais. Para o volume inicial do PigBank, isso aumenta bastante a superfície operacional sem eliminar as decisões contábeis.

> **Correção importante:** em 15/09/2026 a obrigatoriedade nacional **ainda não está vigente para todas as ME/EPP do Simples**. A Resolução CGSN nº 191/2026 revogou expressamente a Resolução nº 189/2026 e adiou a data de 01/09/2026 para **01/11/2026**, conforme a [Receita Federal](https://www.gov.br/receitafederal/pt-br/assuntos/noticias/2026/agosto/simples-nacional-nfs-e-nacional-sera-obrigatoria-para-me-e-epp-a-partir-de-1o-de-novembro-de-2026) e o [Portal oficial da NFS-e](https://www.gov.br/nfse/pt-br/noticias/comite-gestor-do-simples-nacional-prorroga-a-obrigatoriedade-de-emissao-de-notas-fiscais-de-servico-pelo-emissor-nacional-da-nfs-e). Até 31/10/2026, vale o emissor/regra atual do município da LcK, salvo migração municipal antecipada. As regras de CBS/IBS para optantes do Simples passam a produzir efeitos em 01/01/2027.

## O que existe hoje no repositório

### Stack e implantação

- Backend Python/FastAPI em `frontend/finance_bot_websocket_custom.py` e routers em `frontend/routes/`.
- PostgreSQL via `psycopg`; o banco já é usado para outbox, efeitos idempotentes e grants.
- Deploy preparado para Railway (`Procfile`, `docs/ambiente.md`).
- O repositório não informa CNPJ, Inscrição Municipal nem município da LcK; esses dados não podem ser inferidos do código.

### Pagamentos

1. **Stripe**
   - cria assinaturas de cartão em `POST /billing/create-checkout`;
   - recebe eventos em `POST /billing/webhook`;
   - os ramos pagos atuais tratam `invoice.paid` e `invoice.payment_succeeded`;
   - o ID da fatura Stripe já é usado como chave única de comissão de afiliado, um bom precedente para a chave fiscal.

2. **Asaas**
   - cria cobranças Pix anuais;
   - recebe eventos em `POST /billing/asaas/webhook`;
   - persiste primeiro em `pix_webhook_events` e processa depois;
   - deduplica a entrega por `event.id` e o efeito de negócio por `(asaas_payment_id, effect)`;
   - o checkout cria cliente Asaas com CPF/CNPJ, mas o documento não é persistido pelo PigBank; somente `asaas_customer_id` fica em `pix_charges`.

O padrão existente do Pix está alinhado à recomendação oficial do Asaas: Webhooks têm entrega *at least once*, o `id` do evento deve ser único, o evento deve ser persistido antes do `HTTP 200` e processado em segundo plano ([documentação oficial](https://docs.asaas.com/docs/como-implementar-idempotencia-em-webhooks)).

## Regra vigente e alcance municipal

Desde 01/11/2026, as ME/EPP do Simples sujeitas à emissão de NFS-e deverão usar o Emissor Nacional, pelo portal web ou por ERP/software via API da SEFIN Nacional ([comunicado oficial vigente](https://www.gov.br/receitafederal/pt-br/assuntos/noticias/2026/agosto/simples-nacional-nfs-e-nacional-sera-obrigatoria-para-me-e-epp-a-partir-de-1o-de-novembro-de-2026)). Até 31/10/2026, deve-se confirmar o emissor vigente no município da LcK. Para emissão por sistema próprio no ambiente nacional, o serviço oficial informa que é necessário credenciamento prévio no Painel do Contribuinte ([serviço gov.br](https://www.gov.br/pt-br/servicos/emitir-nota-fiscal-de-servico-eletronica)).

A padronização nacional não transforma em decisão técnica o que continua sendo tributário. Antes da produção, o contador deve confirmar:

- município e código IBGE do estabelecimento;
- Inscrição Municipal e habilitação para NFS-e;
- CNAE e item/subitem da lista de serviços da LC 116/2003 aplicável ao PigBank;
- `municipalServiceCode`, nome e descrição do serviço;
- regime especial, natureza da operação, local de incidência e retenção de ISS;
- alíquotas/deduções e o regime de apuração nacional do Simples;
- momento de emissão/competência: pagamento, prestação ou fechamento do período;
- dados obrigatórios do tomador pessoa física e pessoa jurídica;
- procedimento para estorno integral, parcial, chargeback, substituição e cancelamento fora do prazo.

A API oficial consulta parâmetros do convênio, serviços, alíquotas, retenções e benefícios por município; a DPS é validada contra esses dados ([Manual dos Contribuintes da API do Emissor Público Nacional](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/manual-contribuintes-emissor-publico-api-sistema-nacional-nfs-e-v1-2-out2025.pdf/@@download/file)). O código de serviço não deve ser escolhido apenas pelo nome comercial “software”. A lista legal está na [Lei Complementar nº 116/2003](https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp116.htm).

## Opção recomendada: Asaas como emissor fiscal único

O Asaas documenta emissão de NFS-e pela API em três formas: vinculada a uma cobrança (`payment`), a um parcelamento (`installment`) ou avulsa para um cliente (`customer`). Também documenta suporte ao Portal Nacional e exige `municipalServiceCode` nesse cenário ([introdução oficial](https://docs.asaas.com/docs/notas-fiscais), [agendamento da NFS-e](https://docs.asaas.com/reference/agendar-nota-fiscal), [configuração do Portal Nacional](https://docs.asaas.com/reference/configurar-portal-emissor-de-notas-fiscais)).

Isso permite centralizar a emissão sem migrar a cobrança de cartão para o Asaas:

- **Pix Asaas:** `POST /v3/invoices` com `payment=<asaas_payment_id>`;
- **cartão Stripe:** criar/reusar um cliente no Asaas e chamar `POST /v3/invoices` com `customer=<asaas_customer_id>` e `externalReference=stripe:<stripe_invoice_id>`.

A documentação do Asaas alerta para não misturar emissores, pois ele controla a sequência de RPS e o uso paralelo de outro sistema pode provocar conflitos ([pré-requisitos oficiais](https://central.ajuda.asaas.com/hc/pt-br/articles/32087733805723-Nunca-emiti-nota-fiscal-O-que-eu-preciso-fazer)). Se o Asaas for adotado, emissões manuais de produção também devem passar pelo Asaas; não alternar entre Asaas, Portal Web e integração direta usando a mesma série.

### Configuração inicial no Asaas

1. Garantir que a conta Asaas da LcK esteja aprovada como pessoa jurídica.
2. Confirmar Inscrição Municipal regular e habilitação fiscal.
3. Obter e-CNPJ **A1** em PFX/P12 e senha, se esse for o método exigido. O Asaas informa que A3 não é aceito em sua emissão de NFS-e ([parametrização oficial](https://central.ajuda.asaas.com/hc/pt-br/articles/45795346246299-Parametriza%C3%A7%C3%A3o-de-NFS-e-pelo-menu-configura%C3%A7%C3%A3o)).
4. No painel, acessar **Menu do usuário → Configurações → Unidade de Negócio → Nota Fiscal Eletrônica**, completar endereço e dados da empresa, selecionar Simples Nacional e ambiente de homologação primeiro.
5. Habilitar o Portal Nacional como emissor. Via API, o Asaas expõe `POST /v3/fiscalInfo/nationalPortal` com `enabled=true`.
6. Consultar `GET /v3/fiscalInfo/municipalOptions` e preencher `POST /v3/fiscalInfo` somente com os campos exigidos. A configuração inclui `simplesNacional`, IM, CNAE, série/número de RPS e, quando aplicável, certificado/senha e `nationalPortalTaxCalculationRegime` ([guia oficial](https://docs.asaas.com/docs/configurar-informacoes-fiscais), [referência da API](https://docs.asaas.com/reference/criar-e-atualizar-informacoes-fiscais)).
7. Usar `municipalServiceCode` e nome aprovados pelo contador. No Portal Nacional, o Asaas não lista serviços por `GET /v3/fiscalInfo/services`.
8. Fazer uma emissão completa em Sandbox/homologação e validar XML/DANFSe com o contador antes de Produção.
9. Conferir a tarifa de NFS-e na própria conta; o Asaas cobra por emissão e orienta consultar **Menu do usuário → Taxas** ([fonte oficial](https://central.ajuda.asaas.com/hc/pt-br/articles/32087796483099-Quais-s%C3%A3o-as-taxas-para-emiss%C3%A3o-de-notas-fiscais-no-Asaas)).

### Limitação da automação nativa de assinatura

O Asaas oferece `POST /v3/subscriptions/{id}/invoiceSettings` e a regra `ON_PAYMENT_CONFIRMATION` para emitir notas das cobranças geradas por **uma assinatura Asaas** ([documentação oficial](https://docs.asaas.com/docs/emitir-notas-fiscais-automaticamente-para-assinaturas)).

O PigBank usa assinaturas Stripe. Portanto, essa configuração não automatiza as renovações atuais. O app precisa criar a NFS-e avulsa pelo endpoint `/v3/invoices` quando a fatura Stripe for paga. Para o Pix atual, que é cobrança avulsa Asaas e não `subscription`, a NFS-e deve ser vinculada ao `payment` no mesmo endpoint.

## Fluxo de implementação recomendado

### 1. Estado fiscal durável

Criar uma tabela/outbox fiscal própria, por exemplo `fiscal_documents`, sem acoplar o ciclo fiscal ao grant do plano:

```text
id
provider                  # stripe | asaas
payment_ref               # invoice Stripe ou payment Asaas
business_key              # stripe:in_... | asaas:pay_...
user_id
asaas_customer_id
gross_amount_cents
competence_date
service_code_snapshot
tax_profile_version
status                    # pending | scheduling | scheduled | authorized | error |
                          # cancel_review | cancel_requested | canceled | cancel_denied
asaas_invoice_id
nfse_access_key
nfse_number
pdf_url
xml_url
attempts
last_error_code
created_at / updated_at / authorized_at
unique (business_key)
unique (asaas_invoice_id) where not null
```

Guardar um **snapshot da regra fiscal aplicada** à nota é importante: código de serviço, descrição, competência, valor e versão do perfil tributário não podem mudar silenciosamente quando uma retentativa ocorrer semanas depois.

### 2. Gatilhos financeiros

#### Stripe

- Evento primário: `invoice.paid` com `amount_paid > 0`.
- Não emitir em `checkout.session.completed` quando houver trial ou valor zero.
- Não emitir em `invoice.created`, `invoice.finalized` ou `invoice.payment_failed`.
- Usar `invoice.id`, não `event.id`, como chave fiscal. A Stripe pode reenviar o mesmo evento e o código atual também aceita dois tipos de evento de pagamento para a mesma fatura.

A Stripe define `invoice.paid` como o evento de fatura paga e recomenda Webhooks para eventos assíncronos de assinatura ([documentação oficial](https://docs.stripe.com/billing/subscriptions/webhooks)). A Stripe não garante ordem, pode reenviar eventos e recomenda deduplicar IDs e processar de forma assíncrona ([boas práticas oficiais](https://docs.stripe.com/webhooks)).

#### Asaas Pix

- Evento primário: `PAYMENT_RECEIVED`.
- Usar `payment.id` como chave fiscal de negócio; `event.id` continua sendo apenas a chave de entrega.
- Criar a nota com `payment=<payment.id>` para aproveitar cliente e cobrança já existentes.
- Não emitir em `PAYMENT_CREATED`, `PAYMENT_OVERDUE`, `PAYMENT_REFUNDED` ou eventos de QR.

O projeto já possui exatamente a separação correta entre evento e efeito em `pix_webhook_events`/`pix_payment_effects`; o efeito fiscal deve seguir o mesmo modelo.

### 3. Processamento assíncrono

No recebimento do evento financeiro:

1. validar assinatura/token do Webhook;
2. inserir atomicamente `fiscal_documents` com `ON CONFLICT (business_key) DO NOTHING`;
3. concluir o processamento financeiro normal;
4. responder rapidamente ao gateway;
5. um worker reclama registros `pending/error` com lock, monta o snapshot fiscal e chama o Asaas;
6. antes de repetir um `POST` cujo resultado ficou incerto, consultar `GET /v3/invoices?externalReference=<business_key>`; a API permite filtrar por `externalReference` ([referência oficial](https://docs.asaas.com/reference/listar-notas-fiscais));
7. salvar `asaas_invoice_id` e ficar em `scheduled`;
8. aguardar Webhook fiscal para chegar a `authorized` ou `error`.

Esse desenho evita duplicidade em quatro cenários: reentrega do gateway, `invoice.paid` + `invoice.payment_succeeded`, morte do processo depois do POST e reentrega do Webhook fiscal.

### 4. Cliente fiscal

Para Pix, `pix_charges.asaas_customer_id` já existe e deve ser reutilizado.

Para cliente exclusivamente Stripe:

- coletar no checkout/cadastro os dados do tomador que o município exigir;
- criar ou reutilizar um cliente Asaas com `externalReference=pigbank-user:<user_id>`;
- persistir `asaas_customer_id` em uma relação fiscal própria;
- evitar duplicatas: o Asaas informa que a API permite clientes duplicados e recomenda validar/reutilizar por documento ou `externalReference` ([documentação oficial](https://docs.asaas.com/reference/create-new-customer));
- revisar Política de Privacidade e retenção: hoje o texto descreve Asaas como processador de cobrança Pix, não como emissor fiscal de todos os assinantes.

Não é seguro assumir que nome e e-mail bastam. CPF/CNPJ, endereço, IM do tomador e outros campos variam por município/operação; o perfil mínimo deve ser fechado com contador e com `municipalOptions`.

### 5. Webhook fiscal

Configurar um Webhook Asaas separado ou ampliar o atual com:

- `INVOICE_CREATED`;
- `INVOICE_SYNCHRONIZED`;
- `INVOICE_AUTHORIZED`;
- `INVOICE_ERROR`;
- `INVOICE_PROCESSING_CANCELLATION`;
- `INVOICE_CANCELED`;
- `INVOICE_CANCELLATION_DENIED`.

Cada entrega traz `id`, `event` e `invoice`. Persistir `event.id` com unique, validar `asaas-access-token`, responder `HTTP 200` após persistir e processar depois. O Asaas documenta esses eventos e informa que `INVOICE_AUTHORIZED` pode trazer `pdfUrl`, `xmlUrl`, `number` e `validationCode` ([eventos oficiais de NFS-e](https://docs.asaas.com/docs/webhook-para-notas-fiscais)).

Uma chamada bem-sucedida a `POST /v3/invoices` significa apenas **agendamento**. O documento só deve aparecer como emitido no painel do PigBank e ser enviado ao cliente após `INVOICE_AUTHORIZED`.

### 6. Conciliação

Executar ao menos diariamente:

- pagamentos liquidados sem `fiscal_documents`;
- documentos `scheduling`/`scheduled` antigos;
- `error` com causa retentável;
- notas autorizadas no Asaas ausentes localmente, consultadas por período e paginação;
- divergência de valor, cliente, competência ou chave de negócio.

Webhooks são o caminho principal, mas a API de listagem existe para recuperação e conciliação ([orientação oficial](https://docs.asaas.com/reference/listar-notas-fiscais)).

## Cancelamentos, reembolsos e chargebacks

### Regras seguras para o produto

- **Cancelamento da assinatura:** não cancela NFS-e já autorizada. Apenas impede notas de ciclos futuros sem pagamento.
- **Falha de pagamento:** não gera nota e não cancela nota anterior.
- **Estorno integral antes da prestação:** cria `cancel_review`; se a política contábil permitir, solicita `POST /v3/invoices/{id}/cancel`.
- **Estorno integral depois de serviço prestado, parcial, crédito ou chargeback:** não cancelar automaticamente. Encaminhar para regra definida pelo contador: manter, cancelar, substituir ou emitir documento de ajuste.
- **Erro cadastral/valor/código:** se a nota ainda está agendada, corrigir antes da autorização; depois de autorizada, usar cancelamento/substituição conforme regra municipal.

O cancelamento no Asaas depende do município, do estado e do prazo da nota; o resultado pode ser `PROCESSING_CANCELLATION`, `CANCELED` ou `CANCELLATION_DENIED` ([referência oficial](https://docs.asaas.com/reference/cancel-an-invoice)). Na API Nacional, cancelamento e cancelamento por substituição são eventos vinculados à chave da NFS-e e sujeitos à validação ([Manual da API Nacional](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/manual-contribuintes-emissor-publico-api-sistema-nacional-nfs-e-v1-2-out2025.pdf/@@download/file)).

A Nota Técnica 009/2026 acrescentou notas de ajuste de débito/crédito e novos campos de IBS/CBS, mas o Portal oficial ainda indicava cronograma de implantação a divulgar. Não transformar isso em regra automática de reembolso sem confirmar que o recurso está ativo e qual tratamento a contabilidade exige ([comunicado oficial da NT 009](https://www.gov.br/nfse/pt-br/noticias/publicada-a-nota-tecnica-009-da-nfs-e)).

## Alternativa: integração direta com a SEFIN Nacional

O contrato oficial atual expõe, entre outros:

- `GET /parametros_municipais/...` para convênio, serviço, retenções e benefícios;
- `POST /nfse` para emissão síncrona a partir da DPS;
- `GET /nfse/{chaveAcesso}`;
- `GET`/`HEAD /dps/{id}` para reconciliar uma DPS;
- `POST /nfse/{chaveAcesso}/eventos` e consultas de eventos.

Fontes oficiais: [Manual dos Contribuintes](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/manual-contribuintes-emissor-publico-api-sistema-nacional-nfs-e-v1-2-out2025.pdf/@@download/file) e [endereços de produção restrita/produção](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao).

Pré-requisitos técnicos:

- credenciamento no Painel do Contribuinte;
- certificado ICP-Brasil com autenticação de cliente;
- mTLS;
- DPS XML assinada com XMLDSIG;
- payload JSON com XML compactado em GZip/base64;
- controle de série e numeração sequencial da DPS;
- XSDs e regras de negócio da versão de produção;
- atualização para mudanças de leiaute, inclusive RTC/CNPJ alfanumérico;
- armazenamento seguro do A1 e rotação antes do vencimento;
- reconciliação por ID da DPS em toda resposta incerta.

A DPS é responsabilidade do contribuinte, deve ter numeração sequencial crescente e ser convertida em NFS-e no prazo da legislação municipal ([explicação oficial](https://www.gov.br/nfse/pt-br/saiba-mais/como-a-nfs-e-e-gerada/o-que-e-dps)). A documentação oficial de certificado exige ICP-Brasil A1 ou A3, autenticação de cliente na transmissão e certificado com CNPJ/CPF do emitente na assinatura ([Modelo Conceitual do Sistema Nacional](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/manualintegradosnnfse_v1-00-02-producao.pdf)).

Essa alternativa passa a fazer sentido se o custo por nota do Asaas superar claramente o custo de manutenção fiscal, ou se houver requisito que o Asaas não suporte. Se for escolhida, não manter o Asaas emitindo em paralelo.

## Plano de adoção

### Fase 0 — decisões externas obrigatórias

- obter município, IM, CNAE, certificado A1 e dados fiscais da LcK;
- pedir ao contador a matriz fiscal assinada/aprovada: código, descrição, alíquotas, retenções, competência e regras de estorno;
- confirmar a tarifa de NFS-e no Asaas;
- decidir quais dados do tomador serão coletados e atualizar textos de privacidade.

### Fase 1 — prova manual

- configurar Portal Nacional e informações fiscais no Asaas;
- emitir uma NFS-e de teste para pessoa física e uma para pessoa jurídica;
- validar XML/DANFSe e cancelamento em homologação;
- provar Webhooks `AUTHORIZED`, `ERROR`, `CANCELED` e `CANCELLATION_DENIED`.

### Fase 2 — automação do Pix

- adicionar o efeito fiscal a `PAYMENT_RECEIVED` reutilizando `payment.id` e `asaas_customer_id`;
- criar outbox/tabela fiscal e Webhook de notas;
- ativar atrás de feature flag, primeiro para contas internas.

### Fase 3 — automação Stripe

- coletar/corrigir dados fiscais do tomador;
- criar/reutilizar cliente Asaas para cada usuário Stripe;
- enfileirar por `invoice.id` em `invoice.paid`;
- ativar gradualmente e conciliar diariamente.

### Fase 4 — operação

- painel de pendências, erros e cancelamentos negados;
- alerta de nota paga ainda não autorizada após SLA definido;
- reconciliação diária gateway × fiscal;
- monitorar vencimento do A1, fila pausada e mudanças de leiaute;
- exportar relatório mensal para o contador.

## Critérios de aceite

- uma renovação paga gera exatamente uma NFS-e;
- trial, cupom de 100%, falha e fatura de valor zero não geram nota sem regra contábil explícita;
- reentrega de Webhook e morte após POST não duplicam nota;
- o cliente recebe PDF/XML apenas depois de autorização;
- erro fiscal não revoga acesso de quem pagou, mas fica visível e retentável;
- cancelamento de assinatura não cancela nota anterior;
- reembolso parcial/integral segue matriz aprovada pelo contador;
- pagamentos e notas conciliam por chave estável e valor;
- nenhum certificado, senha fiscal, CPF/CNPJ ou XML fica em log;
- existe procedimento manual para indisponibilidade do emissor e vencimento do certificado.

## Pendências que impedem ligar em Produção

1. Município e Inscrição Municipal da LcK não aparecem no repositório.
2. CNAE, código de serviço e descrição fiscal ainda não foram validados.
3. Não há informação de certificado A1 nem de habilitação no Portal Nacional/Asaas.
4. Clientes Stripe não fornecem hoje um perfil fiscal completo; o checkout cria Customer Stripe apenas com e-mail e país BR.
5. O Webhook Stripe atual não tem uma outbox fiscal durável por `invoice.id`.
6. A Política de Privacidade atual descreve o Asaas somente no contexto de cobranças Pix e precisa ser revista para emissão fiscal ampla.
