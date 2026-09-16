# ADR 0004 — O app nativo fala com o backend sem cookie

Status: aceito para implementação em 15/09/2026, decisão explícita do dono na Fase 1 do PigBank Mobile 2.0.

O aplicativo reconstruído não usa cookie jar. A sessão viaja em `Authorization: Bearer`, guardada no keychain do aparelho. O backend entrega as credenciais no corpo da resposta para quem envia `X-PigBank-Client: app`, e para esse cliente não emite `Set-Cookie` nenhum. Sem o header, toda resposta continua idêntica ao que o site recebe hoje: três cookies e nenhum token no corpo.

A escolha não é de conveniência. Com cookie jar perde-se rotação limpa de token, logout confiável e renovação fora do navegador. E há um efeito concreto na direção contrária: o `fetch` do React Native tem cookie jar ligado por padrão, então um `Set-Cookie` que o servidor mandasse seria guardado sem que o app pedisse, e a requisição seguinte passaria a carregar credencial ambiente. O CSRF então voltaria a exigir o par cookie mais cabeçalho, que o app não tem, e a segunda escrita falharia depois de a primeira ter funcionado. As duas metades da decisão — token no corpo e ausência de cookie — são a mesma decisão, e por isso moram na mesma função.

## A isenção de CSRF, e o que a sustenta

O CSRF é dispensado quando a requisição apresenta `Authorization: Bearer` e nenhum dos três cookies de sessão. O que o CSRF protege é credencial **ambiente**: o navegador anexa o cookie sozinho, então uma página de terceiro dispara uma escrita autenticada sem precisar ler nada da vítima. Um token no cabeçalho só entra na requisição se quem a monta o possui. Sem cookie de sessão não sobra credencial que o atacante consiga usar sem tê-la, e o par deixa de proteger alguma coisa.

O CORS não sustenta esta isenção e não é o argumento. Cliente nativo não passa por CORS, e tratar o `allow_origins` como a garantia seria descrever a proteção errada. A condição exige que os três cookies estejam ausentes — `auth_token`, `dashboard_token` e `refresh_token` — porque basta um no jar para a requisição voltar a ser disparável por terceiro, e aí o par volta a ser exigido mesmo com um Bearer junto.

Credencial inválida continua morrendo na autenticação, com 401. O CSRF não pode responder antes dela: um 403 de cabeçalho ausente no lugar do 401 esconde o motivo real de quem depura um login.

## Renovação

O refresh token viaja em `Authorization: Bearer` quando não há cookie de refresh. O cookie tem precedência; o cabeçalho é a segunda fonte, nunca um atalho. A escolha foi imposta pela medição, não pelo gosto: no momento da renovação o app não tem Bearer de sessão para apresentar, porque o access token é justamente o que expirou, e sem Bearer nenhum a requisição não satisfaz a condição acima e morre no CSRF antes de alcançar o handler. Pôr a credencial no cabeçalho resolve isso pela porta da frente e dispensa um modelo de corpo novo, que traria junto risco de 422 nos dois clientes web, que enviam JSON com corpo vazio.

A rotação e a detecção de reapresentação permanecem inteiras e não foram tocadas. Quem renova pelo cabeçalho apresenta o mesmo token opaco, sofre a mesma rotação, e um replay revoga tudo do usuário exatamente como no caminho por cookie. Isso é condição de saída da fase, não efeito colateral esperado.

## Um token, não dois

O app carrega apenas o access token. A resolução de identidade das rotas de dados passa a aceitar tanto o token de dashboard quanto o access JWT. Não é ampliação de privilégio: os dois são assinados pelo mesmo segredo, apontam para o mesmo usuário, carregam o mesmo identificador de sessão e continuam sujeitos à mesma revogação. O access token é o mais curto dos dois, então aceitar quinze minutos onde doze horas já valiam não afrouxa nada. O que se evita é obrigar um cliente sem cookie jar a guardar, rotacionar e renovar dois segredos para a mesma sessão.

## Rate limit

A chave do teto passa a ser o usuário quando há credencial legível, e continua o endereço de rede quando não há. O motivo é o CGNAT das operadoras móveis, onde uma antena inteira compartilha um endereço e um usuário ativo derrubaria os vizinhos — cenário em que o app entra por definição.

As rotas sob `/auth` continuam por endereço de rede, de propósito. É onde mora a defesa contra força bruta e onde ainda não existe usuário identificado; trocar a chave ali mudaria um controle de segurança de lado sem nada a ganhar. Com essa exceção, a mudança é provadamente não enfraquecedora.

## O que isto não autoriza

Não autoriza isentar gate de plano, de assinatura ou de acesso com base em cabeçalho enviado pelo cliente. O `X-PigBank-Client` escolhe o canal de entrega da credencial e nada mais; quem chega nele já passou pela autenticação, e o que recebe é a própria credencial, a mesma que sairia no cookie. A lição está registrada no `_is_pigbank_app`, onde o User-Agent chegou a conceder acesso e virou brecha justamente por ser escolhido por quem chama.
