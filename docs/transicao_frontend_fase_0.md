# Transição do frontend — fase 0 executável

Este documento é a primeira entrega do pacote A. Ele transforma a proposta em
uma linha de base reproduzível e em contratos que não podem se perder ao trocar
somente a entrega de uma rota. Não autoriza publicação nem troca de rota.

## Referência da árvore

Em 11/09/2026, a referência local `origin/main` (`12d643c`) é ancestral de
`feat/implementacao-react` (`64f1ed1`), que está 133 commits à frente. Antes de
cada pacote, conferir a árvore contra a main remota:

```sh
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git merge-base --is-ancestor origin/main HEAD
```

O commit publicado não pode ser concluído dessa relação. A confirmação de
produção é o `/health` com o token de smoke no pipeline, pois o SHA não é exposto
para visitantes.

## Inventário de rotas e contratos

| Rota ou recurso | Situação | Contratos que uma migração não pode assumir |
| --- | --- | --- |
| `/` | pública, candidata após otimização | VSL bloqueia CTAs de `/cadastro`; `vsl_play`, `vsl_progress` e `vsl_unlock`; falha da mídia libera; `pb_vsl_visto`; `nav-auth.js` reescreve CTAs de sessão viva. |
| `/como-funciona` | pública, piloto técnico proposto | Conteúdo institucional, tags injetadas pelo FastAPI, navegação convencional e `safe-area.js`. |
| `/precos` | pública com dados e escrita | Consulta sessão e configuração de planos; inicia checkout; GA4/Meta; não é piloto estático até existir contrato de catálogo e matriz de estados. |
| `/cadastro`, `/login`, `/completar-cadastro` | entrada | Cookies HttpOnly, CSRF, Google e deduplicação de `sign_up`/`CompleteRegistration`. |
| `/app`, `/home`, `/settings`, `/onboarding` | autenticadas | Gates Python de plano/onboarding, refresh de sessão, PWA e pontes iOS. Não entram na primeira exportação. |
| `/blog/*`, `/changelog` | protegidas | `gate_pro_page`; não podem virar arquivos públicos. |
| `/brand/{path}` | asset com allowlist | MIME e cache imutável; a VSL e o poster dependem desta rota. |
| `/service-worker.js`, `auth-refresh.js`, `app-mode.js` | compatibilidade | Cache de versões, logout, sessão e WebView. Um piloto não pode sobrescrever seus nomes nem seus contratos. |

O HTML continua sendo servido por `frontend/routes/static_pages.py`. `html_file`
em `frontend/routes/shared.py` injeta GA4 e Meta Pixel no HTML final; validar
somente um servidor Next futuramente não prova o documento entregue ao visitante.

## Baseline de performance

O coletor [`scripts/medir_frontend_publico.mjs`](../scripts/medir_frontend_publico.mjs)
mede as rotas públicas reais por Chromium/Playwright. Para cada rota, ele cria
cinco contextos sem cache e cinco navegações medidas após uma visita de aquecimento
sem throttle. A visita de aquecimento só libera a coleta quente quando todos os
recursos da primeira origem encerrarem, seja por conclusão ou por cancelamento
intencional do navegador após satisfazer a leitura. POSTs e outros métodos que
não populam cache não bloqueiam essa espera; falha real ou GET/HEAD ainda aberto
após 90 segundos reprova a coleta, em vez de rotular uma amostra parcialmente quente.
Registra todas as amostras, mediana, mínimo e máximo de TTFB, FCP, LCP, load e bytes
efetivamente recebidos da primeira origem/terceiros. A rede 4G e CPU 4x são
fixadas por padrão nas amostras medidas.

```sh
npm ci
npx playwright install chromium
node scripts/medir_frontend_publico.mjs --runs 5
```

O JSON entra em `tmp/`, que não é versionado. Anexar o artefato bruto à revisão
do pacote e registrar data, SHA do deploy confirmado, comando, rota, perfil,
mediana e dispersão. Reexecutar depois da otimização do HTML; essa versão, e não
o relatório PageSpeed isolado, será o controle da decisão sobre Next.

Esta coleta é laboratório reproduzível, não RUM e não uma prova de conversão.
LCP de campo, INP e conversão exigem dados de produção com amostra suficiente.

### Resultado válido — controle anterior à fase 1

Coleta iniciada em `2026-09-11T21:47:03Z`, a partir do checkout local
`1d6d3a9`, pelo comando `node scripts/medir_frontend_publico.mjs --runs 5
--output tmp/frontend-baseline-corrigida-2026-09-11.json`. O SHA de produção
não foi exposto pela resposta pública; portanto os números descrevem o deploy
observado, sem afirmar qual commit o produziu. O perfil foi iPhone 13, rede 4G
simulada (150 ms, 1,6 Mbps de download) e CPU 4x. O JSON bruto está em `tmp/`,
fora do Git. Cada célula mostra mediana e, entre parênteses, mínimo–máximo.

| Rota | Cache | TTFB | FCP | LCP | Transferência | Elemento LCP |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `/` | frio | 169 ms (166–208) | 936 ms (924–944) | 936 ms (924–944) | 1,17 MiB (1,17–1,17) | `p.hero-sub` |
| `/` | quente | 134 ms (123–142) | 420 ms (412–420) | 420 ms (412–420) | 20,1 KiB (20,1–22,4) | `p.hero-sub` |
| `/precos` | frio | 183 ms (178–191) | 956 ms (920–1.004) | 1.060 ms (1.040–1.240) | 970 KiB (969–971) | mascote (`img`) |
| `/precos` | quente | 126 ms (119–144) | 408 ms (404–412) | 408 ms (404–412) | 30,6 KiB (30,6–33,1) | mascote (`img`) |
| `/como-funciona` | frio | 171 ms (169–194) | 744 ms (720–756) | 744 ms (720–756) | 838 KiB (838–839) | parágrafo da primeira seção |
| `/como-funciona` | quente | 117 ms (115–122) | 268 ms (268–280) | 268 ms (268–280) | 7,64 KiB (7,63–9,96) | parágrafo da primeira seção |

Nas cinco amostras frias da landing, `/brand/vsl.mp4?v=1` respondeu `206`,
transferiu cerca de 193 KiB e foi encerrado pelo Chromium depois de obter os
metadados. Nas amostras quentes não houve nova transferência do vídeo. O arquivo
já tem os metadados no início; a hipótese anterior de download integral de
10,43 MiB era erro de contagem do coletor. Em comparação controlada no mesmo
Chromium, `preload="none"` reduziu a transferência pré-interação de cerca de
194 KB para zero e `play()` iniciou a transmissão normalmente. A fase 1 adota
essa opção, preservando `src`, reprodução, portão de cadastro e eventos. O vídeo
não é o LCP atual; o elemento observado foi o subtítulo da hero.

## Funil e medição

O evento principal é a assinatura ou teste confirmado pelo backend. A tabela
`checkout_funnel_events` é o registro de checkout; `?upgrade=success` apenas
indica retorno do provedor e não confirma cobrança. Os eventos a preservar são:

| Etapa | Evidência ou evento | Regra |
| --- | --- | --- |
| visita | `page_view` / Meta `PageView` | Preservar `_ga`, `_fbp`, `_fbc`, UTMs e referenciador. |
| início do vídeo | `vsl_play` | Uma vez por reprodução iniciada. |
| progresso | `vsl_progress` | Marcos de 25%, 50% e 75%. |
| desbloqueio | `vsl_unlock` | Distinguir assistiu, memória, sessão e falha de mídia. |
| cadastro | `sign_up` / `CompleteRegistration` | Deduplicar cliente e CAPI pelo identificador existente. |
| checkout | `begin_checkout` e `checkout_funnel_events` | Uma ação deve criar uma única solicitação. |
| aquisição | confirmação backend/webhook | Separar teste iniciado, compra imediata e primeira cobrança posterior. |

Antes de experimento ou exposição parcial, definir no painel denominador, janela
de atribuição, chave de deduplicação e versão/variante persistida por visitante.
Sem esses dados, comparar apenas orçamento técnico e erros críticos; não declarar
ganho de conversão.

## Ambiente de ensaio e aceite do piloto

- Usar credenciais e provedores de teste; rastreamento real desligado por
  `META_PIXEL_ID=`, `GA4_MEASUREMENT_ID=` e `CLARITY_PROJECT_ID=` vazios no
  ambiente de ensaio.
- Entregar o HTML compilado pelo FastAPI, com seleção explícita por rota. Não
  montar um diretório exportado como fallback para APIs ou rotas protegidas.
- Validar no documento final: status, MIME, chunks tardios, rastreamento uma vez,
  CSRF, sessão existente, retorno ao legado, iOS/PWA, aba aberta e rollback.
- Não avançar se houver quebra de cadastro/checkout, bloqueio indevido,
  duplicação/perda de conversão ou assets recorrentes indisponíveis.

O piloto permanece `/como-funciona`. A landing só entra depois de otimizar e
medir o HTML atual; `/precos` depende do contrato de catálogo e elegibilidade.
