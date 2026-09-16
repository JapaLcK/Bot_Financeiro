/**
 * Ponto de entrada do app. A ÚNICA coisa que ele faz é garantir ORDEM.
 *
 * O `expo-router/entry` carrega a árvore inteira de rotas, e o JavaScript avalia
 * os imports de um módulo antes do corpo dele. Com o roteador como `main`,
 * qualquer exceção ao carregar uma rota, um layout ou uma dependência acontecia
 * com o Sentry ainda desinstalado — e falha de inicialização é justamente o que
 * uma camada de observabilidade nova mais precisa relatar.
 *
 * Por isso TUDO aqui é `require`, e não `import`: o import estático é içado
 * para antes do corpo, o que desfaria o propósito do arquivo. Até o analytics
 * entra depois, porque ele arrasta o SDK do PostHog — e uma falha ao carregar
 * essa dependência é exatamente o tipo de coisa que o Sentry precisa ver.
 */
/* eslint-disable @typescript-eslint/no-require-imports */
const { iniciarLogging } = require("./src/services/logging");
iniciarLogging();

const { iniciarAnalytics } = require("./src/services/analytics");
iniciarAnalytics();

require("expo-router/entry");
