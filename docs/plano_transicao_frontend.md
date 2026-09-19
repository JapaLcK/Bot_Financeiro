# Plano de transição gradual do frontend do PigBank

Status: proposta para revisão, com decisões de produto pendentes. Este documento não inicia a migração nem autoriza publicação.

## 1. Objetivo e decisões

Prioridade confirmada pelo Lucas: melhorar velocidade e conversão do site público. Preservar a identidade visual, corrigindo problemas de experiência por tela. A escolha da tecnologia foi delegada à recomendação técnica.

Recomendação: **React + TypeScript + Next.js, inicialmente com exportação estática**, mantendo FastAPI, Railway, Cloudflare e o domínio atual. Primeiro otimizar e medir o HTML existente; depois introduzir a nova implementação por rota. O benefício esperado do framework é organização e reutilização; o ganho de velocidade precisa ser demonstrado.

Entre as opções discutidas, Vue também permitiria uma transição gradual, mas uma aplicação Vue puramente no navegador exigiria decisões adicionais para gerar as páginas públicas antecipadamente. React sozinho também exigiria escolher essa estrutura. Next reúne a geração de HTML e a organização por páginas numa base que pode ser usada posteriormente na área logada. A recomendação é condicionada ao piloto: se o custo de JavaScript prejudicar o resultado, reduzir esse custo antes de publicar, ou rever a escolha.

Não é necessário migrar o backend para JavaScript. O site atual já entrega HTML pronto; renderização no servidor não é uma vantagem inédita que a migração, sozinha, acrescentaria.

### Confirmado, proposto e pendente

| Assunto | Estado |
|---|---|
| Prioridade | Confirmado: velocidade e conversão do site público |
| Identidade visual | Confirmado: preservar, com melhorias pontuais de UX |
| Tecnologia | Proposta: React + TypeScript + Next com exportação estática |
| Infraestrutura | Proposta: mesma origem e serviço Python; Node somente no build |
| Conversão principal | Confirmado: assinatura/teste confirmado pelo backend; receita efetivamente recebida acompanhada separadamente |
| Vídeo | Confirmado: manter a exigência de assistir antes do cadastro e otimizar o carregamento |
| Executor, dedicação e prazo | Confirmado: Codex executa, sem prazo fixo, com máxima dedicação; avanço por entregas verificadas |
| Staging, tráfego e métricas disponíveis | Verificar na fase 0 |

Manter a exigência de assistir ao vídeo é decisão confirmada pelo Lucas. A hospedagem continua como proposta em esclarecimento: preservar Railway/Cloudflare e evitar outro serviço inicialmente. Preservar também os eventos de marketing existentes.

## 2. Base usada e limites da análise

Análise realizada em 11/09/2026 no checkout `feat/coluna-dupla`, commit `64f1ed1`. A referência local `origin/main` e o PR #380 são árvores diferentes. Antes de implementar, atualizar a análise contra a `main` vigente e identificar o commit realmente publicado; não transportar conclusões sobre autorização ou pagamento de uma árvore para outra.

O relatório [PageSpeed mobile fornecido](https://pagespeed.web.dev/analysis/https-pigbankai-com/6tokpen95t?form_factor=mobile), de 11/09/2026, registrou performance 58, FCP 4,7 s, LCP 7,9 s, TBT 260 ms, CLS 0 e 11.182 KiB transferidos. O vídeo respondeu por 10.183 KiB. São valores de uma execução em laboratório, não uma linha de base estatística nem dados de usuários reais. Repetir antes de usar para decidir.

Constatações que afetam a transição:

- `frontend/routes/static_pages.py` entrega páginas e assets por rotas explícitas. Um arquivo compilado novo precisa de uma forma explícita de ser servido.
- `frontend/routes/shared.py` injeta GA4 e Meta Pixel no HTML; o middleware Python também emite o cookie CSRF. Trocar a entrega por arquivos estáticos sem integrar esses comportamentos pode quebrar conversão e formulários.
- `frontend/index.html` já usa vídeo com `preload="metadata"`, sem autoplay. O download observado precisa ser investigado: atributo de preload, requisições Range, resposta do servidor e posição dos metadados do MP4.
- A landing bloqueia seus CTAs de cadastro até o vídeo ser assistido, com exceções para usuário logado, reprodução concluída anteriormente e falha de mídia. Essa é uma regra de produto existente.
- `/precos` consulta autenticação, configuração dos planos e assinatura; também inicia checkout e alterações de plano. Não é apenas uma página institucional.
- `/blog/*` e `/changelog` têm proteção de acesso no checkout analisado. Não publicar seu conteúdo por export estático aberto.
- O app iOS em `mobile/` carrega o site remoto. Mudanças web podem chegar a aparelhos já instalados sem atualização na loja.
- `frontend/static/auth-refresh.js`, `frontend/service-worker.js` e `frontend/app-mode.js` contêm contratos de sessão, cache e integração nativa que precisam sobreviver à migração.

Não foram consultados nesta etapa painéis privados de produção, volume real de tráfego ou taxas de conversão. Não foi executada uma nova auditoria Lighthouse nem a suíte completa: este trabalho é de planejamento.

## 3. Arquitetura da convivência

```text
Visitante acessa pigbankai.com
            |
       Cloudflare
            |
       FastAPI / Railway
            |
            +-- Rota migrada: entrega HTML gerado pelo Next
            +-- Rota ainda antiga: entrega HTML atual
            +-- Auth, billing, APIs e WebSocket: backend existente

Preparação da versão: Next build -> HTML + CSS + JS + manifest de entrega
Produção inicial: processo Python existente; sem servidor Next adicional
```

Um serviço frontend separado seria outra aplicação em execução na hospedagem, por exemplo um processo Node/Next no Railway responsável por atender as páginas, enquanto o Python atende APIs e bots. Ele teria sua própria configuração, publicação, logs, uso de recursos e monitoramento. O usuário ainda poderia acessar tudo por `pigbankai.com`, com o encaminhamento correto de rotas.

A proposta inicial dispensa esse processo adicional: o Next prepara os arquivos durante o build e o serviço Python já existente os entrega. Há uma etapa nova de compilação, mas não um servidor Next permanentemente ligado. Reavaliar somente quando surgir necessidade concreta de recursos por requisição do Next.

Regras da implementação:

1. **Mesmas URLs.** Migrar `/` mantendo `/`; preservar query strings, links de campanha, referências de afiliados, links mágicos e retornos do app. Não renomear `/app` para `/dashboard` como parte desta migração.
2. **Seleção por rota no servidor.** Cada rota aprovada pode escolher HTML antigo ou novo. O seletor altera apresentação e nunca dispensa autenticação, plano ou onboarding.
3. **Entrega restrita.** Servir assets do build em prefixo reservado e HTML por mapa explícito. Não montar todo o diretório exportado como fallback universal de APIs ou rotas protegidas.
4. **Navegação convencional inicialmente.** Usar links normais entre documentos, inclusive entre telas antigas e novas. Isso simplifica a convivência e o rastreamento. Navegação sem recarregar pode ser uma evolução posterior, com testes próprios.
5. **Pouco JavaScript no cliente.** Conteúdo institucional gerado no build; componentes interativos apenas onde há necessidade, como vídeo, menu e seleção de plano. Evitar tornar o layout inteiro um componente cliente.
6. **Um dono por trecho da tela.** Não executar `nav-auth.js` ou handlers antigos sobre elementos controlados pelo React. Migrar o comportamento de cada componente junto com seu HTML.
7. **Python mantém a autoridade.** Login, autorização, preços efetivos, elegibilidade de teste, cobrança e dados financeiros continuam nas APIs existentes. Nada disso é decidido por regra duplicada no navegador.

Organização proposta, a criar somente na implementação:

```text
web/                         código do novo frontend
  src/app/                   rotas migradas
  src/components/            componentes realmente reutilizados
  src/features/              vídeo, planos e outros comportamentos por assunto
  src/lib/                   cliente HTTP e integrações pequenas, com nomes específicos
  src/styles/                identidade visual e estilos por componente
  public/                    assets públicos necessários
  package.json               build e validações da nova aplicação

frontend/                    páginas antigas e backend existentes
tests/frontend/              cobertura antiga preservada
tests/...                    novos testes de entrega e fluxos compilados
```

Manter um pacote separado evita confundir o build com o harness de testes da raiz. Fixar versões compatíveis e lockfile na fase 2. Reusar cores, tipografia e espaçamentos atuais; não adicionar uma biblioteca visual inteira para começar.

Limites do modo estático: recursos por requisição, Server Actions e APIs do Next ficam fora da primeira etapa. Otimização de imagens precisa ocorrer no preparo dos arquivos ou por um serviço explicitamente escolhido; `next/image` com o otimizador padrão não funciona nesse modo. [Documentação de exportação estática](https://nextjs.org/docs/app/guides/static-exports).

## 4. Etapas e critérios de conclusão

### Fase 0 — Definir o que estamos medindo

**Entregas:** inventário de rotas e comportamentos, baseline reproduzível, mapa do funil, critérios de aceite e definição do ambiente de validação.

- Confirmar `main`, commit publicado e estado das correções do PR #380, especialmente recuperação da conta após bloqueio.
- Medir `/`, `/precos` e `/como-funciona` em cache frio e quente, com condições fixas de dispositivo, rede e versão da ferramenta. Fazer pelo menos cinco execuções comparáveis e guardar a mediana e a dispersão.
- Identificar o elemento LCP e separar atraso de servidor, recursos bloqueantes, mídia, fontes e execução de scripts. O maior arquivo não é necessariamente o responsável principal pelo LCP.
- Registrar eventos de visita, vídeo, clique, cadastro, checkout iniciado e checkout confirmado. Reusar `checkout_funnel_events`, GA4 e Meta CAPI existentes; verificar se estão configurados e emitindo.
- Usar a conversão principal confirmada pelo Lucas: assinatura/teste confirmado pelo backend. Não contar visita a `?upgrade=success` como prova de pagamento. Distinguir teste iniciado, compra imediata e primeira cobrança posterior ao teste; não contar a cobrança posterior como uma segunda aquisição. Definir deduplicação, denominador e janela de atribuição na baseline.
- Confirmar origem predominante do tráfego, volume semanal e possibilidade de teste controlado. Preservar `_ga`, `_fbp`, `_fbc`, UTMs e referências existentes; não inventar uma segunda medição concorrente.
- Separar staging/testes do tráfego real, com dados e credenciais de teste. Desativar rastreamento real e impedir efeitos de cobrança ou mensagens em ambientes de ensaio.

**Concluída quando:** há uma linha de base verificável, um evento principal escolhido e um roteiro que reproduz o funil nas condições relevantes.

### Fase 1 — Ganhos imediatos no HTML atual

**Entregas:** melhorias de mídia, caminho de renderização e acessibilidade, em PRs pequenos; nova medição que servirá de comparação para o framework.

- Investigar por que `preload="metadata"` transferiu tanto vídeo. Conferir Range/206 e metadados antes de escolher recompressão, fast-start, alteração de preload ou atribuição de `src` apenas após interação.
- Manter capa e espaço reservado visíveis. Se a capa for o elemento LCP, priorizá-la; não adiar o recurso principal da primeira tela indiscriminadamente.
- Otimizar imagens e fontes, eliminar pesos/ícones não usados e reduzir CSS bloqueante conforme a medição. Validar `safe-area.js` no iPhone antes de adiar sua execução.
- Avaliar custo de GA4/Meta preservando atribuição, disparos de conversão e navegação. Adiar tags até o primeiro clique não é decisão automática: pode perder a visita e os identificadores que conectam anúncio e compra.
- Corrigir contraste, estrutura de `<main>` e dimensões de mídia. Avaliar legendas acessíveis a partir de conteúdo validado; legenda queimada no vídeo não equivale a uma faixa selecionável.
- Manter a regra de assistir ao vídeo, conforme decisão confirmada. Otimizar a transferência sem abrir os CTAs antes da conclusão, exceto nas exceções já existentes.

**Concluída quando:** a página entrega o mesmo funil, a medição continua correta e os ganhos de performance foram registrados. Essa versão otimizada passa a ser o controle da migração.

### Fase 2 — Preparar build, entrega e piloto

**Entregas:** pacote `web/`, build estático, integração FastAPI, seleção de versão e teste de rollback. Piloto técnico em `/como-funciona` num ambiente de ensaio.

- Gerar HTML, CSS e JS em build reproduzível. Empacotar os artefatos no mesmo release do Python. Não compilar a aplicação na primeira requisição ou depender de um servidor de desenvolvimento.
- Definir no pipeline como Node prepara os arquivos e como o deploy Python os recebe. Validar essa etapa no Railway antes da primeira troca de rota em produção.
- Registrar commit e identificador do build; conferir ambos nos testes de publicação.
- Servir arquivos com tipos MIME corretos, rotas reais e tratamento de arquivo ausente. Configurar o prefixo também no build e conferir chunks tardios e arquivos de `public/`; mover pastas ou reescrever apenas o HTML não corrige referências internas do runtime. Preservar o fallback antigo quando a versão nova estiver desativada.
- Integrar rastreamento uma única vez, sem injetar tags que entrem em conflito com a hidratação. Testar a resposta final do FastAPI, não apenas o servidor do Next.
- Demonstrar login existente, primeira emissão de CSRF, retorno ao legado e compatibilidade básica com iOS/PWA.
- Ensaiar duas publicações e uma reversão com abas abertas, inclusive carregamento tardio de chunks.

**Concluída quando:** o piloto funciona pelo caminho real de entrega, não tem erros de hidratação, respeita o orçamento de performance e pode voltar ao HTML antigo sem perder recursos necessários às abas já abertas.

### Fase 3 — Migrar a landing

**Entregas:** `/` na nova base, preservando identidade, conteúdo essencial, regra do vídeo e eventos existentes.

- Dividir a página em cabeçalho, primeira seção, vídeo, demonstração, benefícios, oferta, FAQ e rodapé conforme a composição real.
- Portar a personalização de visitante/usuário logado para componentes pequenos. Preservar a saída da PWA para o site via `?site=1`.
- Preservar `vsl_play`, `vsl_progress` e `vsl_unlock`, estados de falha de mídia, retorno de quem já assistiu e navegação por teclado. No React, compartilhar o estado explícito de login/desbloqueio; não depender de observar mudanças de `href` feitas pelo script antigo.
- Verificar o CTA antes de a hidratação terminar: clique imediato, JavaScript atrasado/indisponível, primeira tentativa de play e erro ao atribuir ou carregar o `src`. A transição não pode introduzir liberação transitória dos botões nem deixar o visitante preso por falha de mídia. Registrar o comportamento sem JavaScript contra a baseline.
- Comparar primeiro uma versão com conteúdo e regra de produto equivalentes ao HTML otimizado. Corrigir problemas de UX identificados, registrando a hipótese de cada mudança.
- Liberar internamente e depois de forma controlada. Manter a URL canônica e evitar URLs alternativas indexáveis.

**Concluída quando:** não há regressão técnica ou de medição, as metas aprovadas são atendidas e o funil funciona no celular e no desktop.

### Fase 4 — Migrar preços e melhorar o caminho até a assinatura

**Entregas:** `/precos` migrada, com a mesma autoridade de cobrança no backend e estados de conta explícitos.

- Antes de portar a tela, fechar o contrato de configuração: no checkout analisado, `/billing/plans-config` informa flags de disponibilidade e duração do teste, mas não valores, moeda, disponibilidade completa por ciclo ou elegibilidade individual. Estender esse contrato de forma compatível, ou expor uma fonte compartilhada serializável, alinhando valores aos preços usados no checkout. A elegibilidade individual continua sendo informação autenticada, separada do catálogo público.
- Consumir essa fonte na página nova, sem criar outra tabela de preços. Validar também a oferta da landing contra a mesma fonte. Se o HTML inicial trouxer um catálogo preparado no build, definir revalidação e atualização de versão para não exibir preço antigo após uma alteração comercial.
- Cobrir visitante, usuário sem escolha de plano, teste elegível/inelegível, assinatura ativa, carência, bloqueio, troca agendada e erro de consulta. Confirmar a lista final na `main` vigente.
- Cobrir cobrança mensal/anual e os provedores efetivamente disponíveis, incluindo Pix quando aplicável. Não assumir que todos os caminhos são Stripe.
- Garantir uma solicitação de checkout por ação, proteção contra clique duplo, confirmação clara, erro recuperável e preservação dos parâmetros de campanha.
- Manter `/cadastro`, login, callbacks e retorno `/home` funcionando no legado enquanto suas telas não forem migradas.
- Testar o ciclo completo com provedores em modo de teste. Aguardar confirmação do backend, inclusive quando o webhook demora ou é reentregue.

**Concluída quando:** todos os caminhos de contratação suportados passam, a medição não duplica conversões e uma falha de pagamento não aparece como sucesso visual.

### Fase 5 — Expandir as páginas públicas e testar melhorias de UX

**Entregas:** migração progressiva de `/funcionalidades`, `/whatsapp`, `/agents` e demais páginas institucionais elegíveis; `/suporte` com seu formulário em etapa própria.

- Ordenar as páginas por tráfego e participação no funil, usando a fase 0. Reusar os componentes já provados.
- Preservar conteúdo contratual de termos/privacidade; mudança de estrutura visual não modifica seu significado.
- Tratar FAQ e conteúdo gerado pelo Python sem criar duas fontes editoriais divergentes.
- Executar experimentos de copy, posição de CTA ou explicação de planos conforme decisões de produto, mantendo a exigência de assistir ao vídeo. Separar o experimento da troca de framework.
- Manter `/blog/*`, `/changelog` e páginas com autenticação fora da exportação pública até seu desenho específico.

**Concluída quando:** o conjunto público escolhido usa a nova base, os fluxos de aquisição estão estáveis e existe evidência suficiente para decidir o investimento seguinte.

### Fase 6 — Migrar entrada e recuperação de conta

**Entregas:** login, cadastro, conclusão via Google e recuperação de senha, uma família de fluxos por PR.

Esta etapa começa após estabilizar o site público. Reutilizar os endpoints Python, cookies HttpOnly, CSRF e contratos de redirecionamento. Não introduzir outro sistema de autenticação junto com a migração visual.

Validar e-mail, Google no navegador e no app, MFA, link mágico, sessão expirada, múltiplas abas, logout e retornos instalados em versões antigas do iOS. Preservar `/onboarding?token=` e `/d/{code}`. Garantir que uma conta bloqueada possa alcançar os meios necessários para exportar dados e excluir a conta, inclusive quando ainda não possui senha.

**Concluída quando:** toda entrada, saída e recuperação funciona contra o backend real de teste, com isolamento entre contas e verificação em aparelho para as pontes nativas.

### Fase 7 — Migrar a área logada por domínio

**Entregas:** evolução posterior do produto, sem condicionar o resultado do site público a uma reescrita completa.

Ordem inicial proposta: `/home` com leitura e estado de sessão; configurações de notificações como primeiro fluxo de escrita; demais configurações; funcionalidades do `/app` por domínio financeiro; Open Finance e administração em pacotes próprios. Revalidar a ordem após inventário de dependências.

- Na primeira versão de `/home`, preservar o retorno do checkout e o polling de confirmação; a tela não é somente um resumo financeiro.
- Para substituir apenas uma seção de uma página antiga, criar um ponto de montagem exclusivo: React e scripts globais não podem manipular os mesmos elementos. Migrar a propriedade do estado junto da seção.
- Centralizar o cliente HTTP sem instalar dois interceptadores de refresh. Limpar estado ao trocar de conta e preservar a distinção entre falha de rede, sessão inválida e senha incorreta.
- Preservar os eventos de WebSocket e remover listeners/conexões ao desmontar componentes. Invalidar somente os dados afetados; não duplicar lançamentos nem exibir saldo zerado como fallback de erro.
- Validar operações financeiras e persistência, concorrência entre edição/refresh, retorno do segundo plano, rascunhos, `PBRefresh`, teclado, áreas seguras e pontes nativas.
- Não ativar a navegação experimental `pb-nav.js` junto com o roteador novo. A adoção de navegação interna entre telas migradas é uma entrega separada.

**Concluída por domínio quando:** os fluxos equivalentes foram verificados, não há divergência de dados e a versão antiga pode ser retirada daquela unidade com segurança.

### Fase 8 — Retirar o legado e decidir sobre servidor Next

Remover HTML, scripts e rotas antigas somente após estabilidade e término da janela de reversão de cada unidade. Remover por evidência de ausência de consumidores, incluindo app instalado, links de e-mail e assets requisitados tardiamente.

Servidor Next separado é uma decisão futura se houver necessidade concreta de renderização por requisição ou outros recursos incompatíveis com exportação estática. Esse passo exige plano de proxy, cookies, cache e identificação de releases próprios. Não é requisito para concluir a primeira migração pública.

## 5. Metas e comparação

Metas propostas, a fechar com a baseline da fase 0:

| Medida | Critério proposto |
|---|---|
| LCP, usuários reais | p75 até 2,5 s, quando houver amostra suficiente |
| INP, usuários reais | p75 até 200 ms |
| CLS, usuários reais | p75 até 0,1; preservar a estabilidade já observada |
| Lighthouse mobile | Mediana alvo de performance pelo menos 90; não usar nota isolada como único aceite |
| JavaScript próprio inicial | Orçamento inicial de até 200 KiB transferidos/comprimidos por rota pública; ajustar na fase 2 com justificativa, nunca ignorar o peso do framework |
| Transferência antes de interação | Meta inicial de até 1 MiB na landing; medir terceiros separadamente e no total |
| Vídeo antes do play | Se adotado carregamento sob demanda, não transferir o corpo do vídeo antes da intenção de reprodução; poster entra no orçamento inicial |
| Comparação com HTML otimizado | Investigar regressões maiores que a variabilidade medida; não aprovar pela comparação exclusiva com o relatório antigo de 58 |
| Conversão | Sem regressão confirmada; avaliar ativação, compra e receita em métricas distintas |
| Erros | Zero falha conhecida nos caminhos críticos; acompanhar novas classes de erro e taxas por visita |

Os limites de LCP/INP/CLS vêm das definições de [Core Web Vitals](https://web.dev/articles/defining-core-web-vitals-thresholds). Orçamentos de bytes e nota são propostas deste projeto, não garantias da ferramenta.

Separar métricas de laboratório e campo. INP não é substituído por TBT; ambos têm usos distintos. Sem volume suficiente, não declarar ganho de conversão ou significância. Definir janela de atribuição, efeito mínimo relevante e tamanho de amostra antes de um teste A/B. Com pouco tráfego, usar critérios técnicos, observação do funil e liberação cautelosa, sem chamar variação casual de vitória.

## 6. Publicação, cache e reversão

- **Etapas de exposição:** ambiente de ensaio → acesso interno → público controlado → todas as visitas. Percentuais, duração e amostra são definidos com o volume real; não avançam só pelo relógio.
- **Visita consistente:** se houver A/B ou porcentagem, fixar a variante por visitante/sessão e registrar sua versão. A pessoa não deve trocar de implementação aleatoriamente no mesmo funil.
- **HTML inicialmente sem cache compartilhado:** manter o comportamento de entrega e CSRF. Cache de HTML na Cloudflare é outra mudança, com testes de `Set-Cookie`, personalização e variantes.
- **Assets com hash:** cache longo somente em URLs imutáveis. Não aplicar essa regra indistintamente a APIs, HTML ou scripts antigos sem hash no nome.
- **Retenção:** manter os assets de todas as versões dentro da janela operacional, inclusive da versão recém-desativada. Apenas guardar o build anterior não protege a aba aberta na versão que acabou de ser revertida. Arquivos copiados de `public/` sem hash exigem versionamento explícito; não sobrescrevê-los no mesmo endereço durante a janela.
- **Reversão por rota:** desativar a seleção nova e servir a versão antiga; conservar assets. O mecanismo de configuração e seu tempo real serão testados na fase 2. Variáveis de ambiente podem exigir reinício: não prometer reversão instantânea sem essa medição.
- **Gatilhos imediatos:** quebra de cadastro/checkout, bloqueio indevido, dados de outra conta, perda/duplicação de conversão ou falha recorrente de assets. Para oscilação de métricas, usar a baseline e a amostra acordadas.
- **Compatibilidade:** validar cache frio, cache antigo, service worker anterior, aba aberta durante deploy, rollback e retorno ao aplicativo. Se o worker mudar, atualizar sua versão e os testes exigidos pelo projeto.

## 7. Testes, revisão e organização dos PRs

Preservar a cobertura existente e ampliá-la para o build real. A suíte atual contém testes que leem HTML e funções globais; eles não provam, por si, que a versão React funciona.

- Executar o baseline de backend conforme a skill local `baseline-testes` antes de qualquer `pytest`; comparar nomes das falhas, não só quantidades.
- Preservar `npm run lint` e `npm run test:frontend` da raiz. Adicionar build e checagem de tipos do pacote novo ao CI.
- Rodar testes de navegador contra HTML compilado entregue pelo FastAPI, incluindo eventos, requests e respostas reais de teste.
- Ampliar `scripts/smoke_prod.py`: a descoberta atual por `?v=hash` não cobre automaticamente assets com hash no nome. Verificar manifest, arquivos, MIME e chunks dinâmicos.
- Manter o smoke de produção vinculado ao commit publicado e acrescentar identificação do build frontend.
- Cobrir Chromium e Safari/WebKit; validar no iPhone os casos de área segura, vídeo, Google e pontes nativas que o navegador automatizado não reproduz integralmente.
- Usar contas e provedores de teste nos fluxos com efeito. A suíte não deve disparar cobranças reais para demonstrar a migração.
- Cada PR informa rotas afetadas, comportamento, testes, comparação de performance e reversão. Commits em português do Brasil. Merge depende da autorização do dono, conforme processo do repositório.

Pacotes sugeridos de trabalho:

| Pacote | Resultado revisável |
|---|---|
| A | Inventário, baseline, eventos e metas |
| B | Vídeo e mídia no HTML atual |
| C | CSS, fontes e acessibilidade no HTML atual |
| D | Build, entrega estática e teste do piloto |
| E | Seleção por rota, identificação de release e ensaio de rollback |
| F | Landing equivalente, com testes e medição |
| G | UX da landing conforme decisões, separada da troca técnica |
| H | Preços e contratação com matriz de estados |
| I | Páginas institucionais por grupos coesos |
| J | Entradas e recuperação de conta |
| K | Área logada por domínio, com plano detalhado próprio |
| L | Retirada do legado já estabilizado |

Um pacote pode exigir mais de um PR. Não concentrar infraestrutura, landing, checkout e mudança de regra do vídeo num único diff.

## 8. Responsáveis, prazo e decisões para fechar a versão executável

Responsabilidade confirmada: Codex executa a implementação e registra evidência, com máxima dedicação e sem prazo fixo. Lucas define produto, aceita as telas e autoriza publicação/merge conforme o processo do repositório. A revisão deve verificar o resultado entregue; cada publicação terá um responsável definido por acompanhar métricas e executar reversão.

Não fixar um prazo global: organizar a execução pelos critérios de conclusão de cada pacote. Priorizar profundidade de validação e resultado de negócio. Ao concluir D/E, recalibrar as próximas entregas pelo esforço observado. A migração da área logada continua como etapa posterior e não bloqueia os resultados públicos.

Ponto ainda em esclarecimento: hospedagem. Lucas pediu explicação sobre o que seria um serviço frontend; a recomendação é manter Railway/Cloudflare e servir os arquivos compilados pelo Python existente, sem processo Next adicional inicialmente.

O carregamento do vídeo será escolhido após a medição, preservando a exigência confirmada de assistir para cadastrar. A pergunta anterior sobre autoplay não descrevia o código analisado: ele não tem autoplay.

Na fase 0, complementar com volume de visitas, campanhas predominantes, acesso às métricas e ambiente de ensaio. Se esses dados não estiverem disponíveis, registrar a limitação e definir os critérios técnicos; não inventar taxa de conversão nem duração estatística.

Primeira entrega de implementação proposta: **pacote A**, seguido das melhorias no HTML e do piloto. A liberação da landing migrada depende do aceite técnico de D/E e da comparação de F com a versão otimizada.

## Referências

- [Exportação estática do Next](https://nextjs.org/docs/app/guides/static-exports): geração de arquivos e limites do modo sem servidor.
- [Convivência de aplicações e navegação entre zonas](https://nextjs.org/docs/app/guides/multi-zones): referência para separar domínios de navegação; não exige adotar toda a arquitetura de multi-zones neste projeto.
- [Performance de vídeo](https://web.dev/learn/performance/video-performance): preload, poster e prioridade de carregamento.
- Fontes locais: `frontend/routes/static_pages.py`, `frontend/routes/shared.py`, `frontend/index.html`, `frontend/precos.html`, `frontend/static/auth-refresh.js`, `frontend/service-worker.js`, `mobile/capacitor.config.json`, `.github/workflows/tests.yml`, `.github/workflows/smoke.yml`, `CLAUDE.md` e `docs/armadilhas.md`.
