Curva do gasto acumulado no mês, com o mês passado tracejado e o ponto de hoje pulsando, de `app/src/ui/componentes/CurvaAcumulada.tsx`.

**Quem fornece:** `points` (acumulado diário em centavos até hoje), `daysInMonth`, `todayIndex`, e opcionalmente `previous` (acumulado do mês passado) e `tone` (`ink`, `positive`, `warning`).

Eixo Y com até 5 marcas redondas ("R$ 1,2k"), eixo X nos dias 1, 15, 25 e o último. A linha tem 2px e se desenha em 500ms. A área embaixo vai de 15% do tom até zero. O ponto de hoje pulsa a cada 2,4s. Com o mouse, passar por cima mostra o dia e o valor; no toque, segure 250ms e arraste, como no app. O gráfico é escondido do leitor de tela: escreva o total em texto ao lado.
