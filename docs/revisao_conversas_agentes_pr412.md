# Inventário e revisão de impactos — PR 412

Revisão de 13/09/2026. Escopo: diff completo do PR contra o merge-base de `main`, mais as correções desta rodada. As medições abaixo pertencem a esta revisão; execute novamente os comandos antes de reutilizá-las.

## Mudanças do PR completo

1. **Conversa por especialista:** serviço `core/services/agent_chat.py` e endpoint autenticado em `frontend/routes/agents.py`; classificação por tema, respostas educativas, validação adicional e encaminhamentos. O contexto é cifrado, ligado ao usuário/agente e guardado somente na memória da página.
2. **Dados de consulta:** ferramentas permitidas por tema, sem comandos financeiros. Carteiro consulta instâncias registradas; Banqueiro consulta saldos sem aplicar juros; Barão e Faria Limer usam retratos dos investimentos cadastrados.
3. **Detetive:** extração das consultas de recorrências e duplicidades em `core/services/piggy_agents.py`. Os alertas automáticos reutilizam essas consultas e continuam responsáveis pela gravação e deduplicação dos eventos.
4. **Cota compartilhada:** leitura, consumo atômico, reserva e restituição em `db/ai_quota.py`, com a API existente preservada por `db/ai_chat.py`. O chat geral reserva antes de executar ferramentas; especialistas descontam depois de produzir uma resposta de consulta.
5. **Efeitos das ferramentas:** `core/services/ai_chat/runner.py` acompanha tentativas de gravação. O cadastro `tools/_base.py` identifica consultas que sincronizam contas, aplicam juros ou reconciliam faturas; flags em `bills.py`, `pockets.py`, `investments.py` e `cards.py` impedem restituição após esses efeitos.
6. **Painel do dashboard:** `frontend/dashboard-agent-chat.js`, `dashboard.html` e `dashboard.css` implementam abertura, mensagens, rascunhos, erros, ativação, upsell e envio. `dashboard.js` acrescenta o botão Conversar aos cartões disponíveis.
7. **Entrega do script:** rota própria com `no-cache` em `frontend/routes/static_pages.py`; a integração mantém scripts clássicos, handlers e os assets existentes.
8. **Acesso:** a prateleira e o serviço compartilham a permissão de conversa. Energia continua sendo capacidade de agentes ativos; conversar não gasta energia por mensagem.
9. **Renda fixa:** `db/rv.py` permite filtro opcional por moeda antes do agrupamento. Os especialistas pedem BRL; consumidores que omitem o argumento mantêm seu comportamento anterior.
10. **Documentação:** `CONTEXT.md`, `docs/conversas_agentes.md`, `docs/agente_faria_limer.md` e este inventário registram o vocabulário, o contrato e os limites da implementação.
11. **Testes:** testes Python de contrato, autorização, consultas, cota, concorrência e falhas; testes de navegador em `tests/frontend/agent_chat.test.mjs` para sessão, recarga, encaminhamento, acesso, erro e adaptação desktop/celular. Os caminhos das capturas usam o diretório temporário do sistema para também funcionar no CI Linux.

## Correções desta rodada e efeitos conferidos

| Mudança | Falha reproduzida | Impactos conferidos |
|---|---|---|
| Reserva identifica contas e mês | Restituir uma reserva de uma conta também reduzia outras contas já esgotadas ou criadas depois | Restituição repetida, consumo simultâneo por especialista, virada de mês e API usada pelo chat geral |
| Limite de detalhes do Faria Limer | Mais de 100 investimentos manuais entravam no payload do modelo | Total manual integral separado da amostra; precisão dos saldos; listas vazias, no limite e acima dele |
| Cobertura das listas e lotes do Barão | Corte externo não limitava os lotes internos; amostra não informava registros excluídos | Remoção de lotes somente do payload; dados originais, saldos, taxas e unidades preservados |
| Cobertura de caixinhas | RF vinculada a caixinha era excluída, podendo resultar em RF zero no resumo | Exclusão explicitada; zero não prova ausência de RF; helper compartilhado mantém exclusão para evitar duplicação patrimonial em outros consumidores |
| Moeda antes de agregar | BRL, USD e EUR com o mesmo nome eram somados e apresentados como reais | Filtro BRL antes do agrupamento nos dois especialistas; isolamento por usuário; comportamento padrão dos helpers preservado |
| Data dos saldos do Banqueiro | Metas usavam saldo sem juros atualizados, sem informar a data disponível | Data e nota na variante pura; cálculo identificado como baseado no último saldo registrado; chat geral preserva sua variante com juros |
| Requisições antigas de acesso | Sucesso/falha antigos sobrescreviam acesso, erro ou cache de uma abertura mais recente | Falha HTTP e de transporte, reabertura, troca de agente, rascunho, envio em andamento e resposta/contexto na sessão de origem |
| Permissão de conversa explícita | Free legado podia ativar um agente, mas a interface inferia daí o direito de conversar | `can_chat` comum ao backend e à prateleira; upsell antes de ativar/enviar; energia e ativação legada mantêm seus contratos |
| Módulo próprio de cota | O CI anterior reprovou `db/ai_chat.py` no limite de tamanho do repositório | Cota separada por responsabilidade; nomes públicos preservados; gate conferido sem ampliar a lista de exceções |

## Estados e eventos verificados

| Subsistema | Estados/eventos cobertos |
|---|---|
| Cota | Sem vaga, última vaga concorrente, múltiplas contas com contadores divergentes, conta criada depois, restituição após consumo de outra conversa, mês anterior/atual/posterior, falha sem escrita, falha após escrita e falha na persistência da resposta |
| Abertura do painel | Aberto/fechado, mesmo agente/outro agente, resposta antiga/nova, sucesso/HTTP inválido/erro de rede, permitido/bloqueado, rascunho e envio em andamento |
| Planos | Legado Free/Pro e modelo de energia, agente ativo/inativo, falta de acesso, falta de capacidade e downgrade |
| Carteira | Coleção vazia, limite, truncamento, lotes internos, totais integrais, caixinhas excluídas, moedas diferentes e registros de outro usuário |
| Consultas | Ferramenta fora do tema, comando de escrita, tentativa do modelo de habilitar sincronização/juros, saldo registrado sem atualização e detecção sem emitir evento |

## Validação reproduzível

Os testes usam PostgreSQL descartável e o isolamento de banco criado pelo `conftest.py`. Não usar banco de produção. As regressões dos apontamentos foram executadas antes das correções e falharam no sintoma esperado.

```sh
PYTHONPATH=. python -m pytest -q
npm run test:frontend
node --test tests/frontend/agent_chat.test.mjs tests/frontend/handlers_inline.test.mjs
python -m pytest -q tests/test_ai_chat_*.py tests/test_agent_chat*.py tests/test_piggy_agents.py tests/test_agents_energy_sweep.py tests/test_plan_tiers.py tests/test_pockets_endpoints.py tests/test_routes_pockets_cards.py tests/test_rv.py
python -m pytest -q tests/test_max_lines_python.py tests/test_frontend_assets_e_rotas.py
git diff --check
```

Use o Python do ambiente virtual preparado conforme `.claude/skills/baseline-testes/SKILL.md`, com `DATABASE_URL` apontando para o banco descartável. As contagens e eventuais falhas da rodada final são registradas no comentário do PR, junto ao commit validado.

## Revisão independente

- **Standards:** verificados isolamento por usuário, consumidores dos helpers, preservação dos alertas e consultas, regra comum de acesso e contrato da reserva. A divergência de acesso legado encontrada nessa revisão foi corrigida.
- **Spec:** conferidos tema, consultas, duplicidades, reflexão, contexto, encaminhamento, planos/energia e cota compartilhada. Foram corrigidas a mistura de moedas e a ausência de data/ressalva nos saldos do Banqueiro.

## Limites que permanecem explícitos

- Chamadas ao modelo são simuladas nos testes. Classificação semântica e qualidade das respostas com IA real ainda precisam de avaliação; uma validação por IA não é uma garantia determinística de tema ou orientação.
- Os resumos não representam necessariamente todo o patrimônio. RF vinculada a caixinhas e RF em outras moedas ficam fora dos retratos de BRL; a resposta deve explicitar essa cobertura.
- O limite de itens restringe o payload enviado ao modelo. As consultas ainda leem a coleção necessária para produzir totais e cobertura; não foi introduzida paginação no banco.
- Carteiro consulta contas/instâncias já registradas, sem gerar novos ciclos. Esta rodada não acrescenta uma fonte de faturas de cartão ao especialista.
- O navegador local não comprova integração em produção, comportamento nativo do WKWebView nem interpretação real de mensagens de WhatsApp. Não houve deploy ou merge nesta revisão.
