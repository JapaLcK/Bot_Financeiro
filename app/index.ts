/**
 * Ponto de entrada do app. A ÚNICA coisa que ele faz é garantir ordem.
 *
 * O `expo-router/entry` carrega a árvore inteira de rotas, e o JavaScript avalia
 * os imports de um módulo antes do corpo dele. Com o `expo-router/entry` como
 * `main`, qualquer exceção ao carregar o roteador, um layout ou uma dependência
 * acontecia com o Sentry ainda desinstalado — e falha de inicialização é
 * justamente o que uma camada de observabilidade nova mais precisa relatar.
 *
 * Aqui o `iniciarLogging()` roda ANTES do import do roteador, que é o que o
 * `import` estático não permite expressar: por isso ele é `require`, e por isso
 * este arquivo existe em vez de a chamada morar no layout.
 */
import { iniciarAnalytics } from "./src/services/analytics";
import { iniciarLogging } from "./src/services/logging";

iniciarLogging();
iniciarAnalytics();

// eslint-disable-next-line @typescript-eslint/no-require-imports
require("expo-router/entry");
