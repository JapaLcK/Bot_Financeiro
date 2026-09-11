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
cinco contextos sem cache e cinco navegações medidas após uma visita de aquecimento.
Registra todas as amostras, mediana, mínimo e máximo de TTFB, FCP, LCP, load e
bytes da primeira origem/terceiros. A rede 4G e CPU 4x são fixadas por padrão.

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

### Resultado inicial — remedir antes de reutilizar

Coleta em `2026-09-11T16:33:33Z`, a partir do checkout local `64f1ed1`, pelo
comando `node scripts/medir_frontend_publico.mjs --runs 5 --output
tmp/frontend-baseline-2026-09-11.json`. O SHA de produção não foi confirmado
nesta sessão; portanto estes números descrevem a resposta pública observada,
mas não comprovam qual commit a produziu. O JSON bruto está em
`tmp/frontend-baseline-2026-09-11.json` e deve acompanhar a revisão que o usar.

| Rota | Cache | TTFB mediano | FCP mediano | LCP mediano | Transferência mediana | Elemento LCP |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `/` | frio | 201 ms | 972 ms | 972 ms | 11,43 MiB | `p.hero-sub` |
| `/` | quente | 129 ms | 428 ms | 428 ms | 10,45 MiB | `p.hero-sub` |
| `/precos` | frio | 184 ms | 964 ms | 1.224 ms | 957 KiB | mascote (`img`) |
| `/precos` | quente | 125 ms | 420 ms | 420 ms | 27 KiB | mascote (`img`) |
| `/como-funciona` | frio | 193 ms | 756 ms | 756 ms | 827 KiB | parágrafo da primeira seção |
| `/como-funciona` | quente | 119 ms | 284 ms | 284 ms | 6 KiB | parágrafo da primeira seção |

Em todas as cinco amostras da landing, o maior recurso foi
`/brand/vsl.mp4?v=1`: resposta `206` de aproximadamente 10,43 MiB, inclusive
com cache quente. Isso confirma a investigação prioritária da fase 1: determinar
por que `preload="metadata"` baixa esse intervalo, antes de escolher a mudança de
atribuição do `src`, fast-start ou recompressão. Não atribuir esse custo ao LCP
atual: o elemento LCP observado foi o subtítulo da hero.

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
  `META_PIXEL_ID=` e `GA4_MEASUREMENT_ID=` vazios no ambiente de ensaio.
- Entregar o HTML compilado pelo FastAPI, com seleção explícita por rota. Não
  montar um diretório exportado como fallback para APIs ou rotas protegidas.
- Validar no documento final: status, MIME, chunks tardios, rastreamento uma vez,
  CSRF, sessão existente, retorno ao legado, iOS/PWA, aba aberta e rollback.
- Não avançar se houver quebra de cadastro/checkout, bloqueio indevido,
  duplicação/perda de conversão ou assets recorrentes indisponíveis.

O piloto permanece `/como-funciona`. A landing só entra depois de otimizar e
medir o HTML atual; `/precos` depende do contrato de catálogo e elegibilidade.
