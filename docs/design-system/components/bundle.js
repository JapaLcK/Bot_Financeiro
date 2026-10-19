/* @ds-bundle: {"format":4,"namespace":"PigBank","components":[{"name":"Text"},{"name":"Icon"},{"name":"Button"},{"name":"Chip"},{"name":"SegmentedControl"},{"name":"Input"},{"name":"AmountInput"},{"name":"Card"},{"name":"InsightCard"},{"name":"Banner"},{"name":"Toast"},{"name":"EmptyState"},{"name":"ConnectionStatus"},{"name":"ListRow"},{"name":"ListGroup"},{"name":"TransactionRow"},{"name":"Money"},{"name":"Avatar"},{"name":"ProgressBar"},{"name":"Skeleton"},{"name":"CompositionBar"},{"name":"MonthlyBars"},{"name":"CumulativeCurve"},{"name":"Screen"},{"name":"Sheet"}]} */
(function () {
  var React = window.React;
  var h = React.createElement;
  var cx = function () { return Array.prototype.filter.call(arguments, Boolean).join(" "); };
  var tone = function (t) { return "var(--" + t + ")"; };

  // Regular-weight paths of the icons the app ships (phosphor-react-native 3.0.6, viewBox 256).
  var ICONS = {"CaretRight":["m181.66 133.66-80 80a8 8 0 0 1-11.32-11.32L164.69 128 90.34 53.66a8 8 0 0 1 11.32-11.32l80 80a8 8 0 0 1 0 11.32"],"CaretLeft":["M165.66 202.34a8 8 0 0 1-11.32 11.32l-80-80a8 8 0 0 1 0-11.32l80-80a8 8 0 0 1 11.32 11.32L91.31 128Z"],"Wallet":["M216 64H56a8 8 0 0 1 0-16h136a8 8 0 0 0 0-16H56a24 24 0 0 0-24 24v128a24 24 0 0 0 24 24h160a16 16 0 0 0 16-16V80a16 16 0 0 0-16-16m0 128H56a8 8 0 0 1-8-8V78.63A23.8 23.8 0 0 0 56 80h160Zm-48-60a12 12 0 1 1 12 12 12 12 0 0 1-12-12"],"Bell":["M221.8 175.94c-5.55-9.56-13.8-36.61-13.8-71.94a80 80 0 1 0-160 0c0 35.34-8.26 62.38-13.81 71.94A16 16 0 0 0 48 200h40.81a40 40 0 0 0 78.38 0H208a16 16 0 0 0 13.8-24.06M128 216a24 24 0 0 1-22.62-16h45.24A24 24 0 0 1 128 216m-80-32c7.7-13.24 16-43.92 16-80a64 64 0 1 1 128 0c0 36.05 8.28 66.73 16 80Z"],"CheckCircle":["M173.66 98.34a8 8 0 0 1 0 11.32l-56 56a8 8 0 0 1-11.32 0l-24-24a8 8 0 0 1 11.32-11.32L112 148.69l50.34-50.35a8 8 0 0 1 11.32 0M232 128A104 104 0 1 1 128 24a104.11 104.11 0 0 1 104 104m-16 0a88 88 0 1 0-88 88 88.1 88.1 0 0 0 88-88"],"Info":["M128 24a104 104 0 1 0 104 104A104.11 104.11 0 0 0 128 24m0 192a88 88 0 1 1 88-88 88.1 88.1 0 0 1-88 88m16-40a8 8 0 0 1-8 8 16 16 0 0 1-16-16v-40a8 8 0 0 1 0-16 16 16 0 0 1 16 16v40a8 8 0 0 1 8 8m-32-92a12 12 0 1 1 12 12 12 12 0 0 1-12-12"],"WarningCircle":["M128 24a104 104 0 1 0 104 104A104.11 104.11 0 0 0 128 24m0 192a88 88 0 1 1 88-88 88.1 88.1 0 0 1-88 88m-8-80V80a8 8 0 0 1 16 0v56a8 8 0 0 1-16 0m20 36a12 12 0 1 1-12-12 12 12 0 0 1 12 12"],"WarningOctagon":["M120 136V80a8 8 0 0 1 16 0v56a8 8 0 0 1-16 0m112-44.45v72.9a15.86 15.86 0 0 1-4.69 11.31l-51.55 51.55a15.86 15.86 0 0 1-11.31 4.69h-72.9a15.86 15.86 0 0 1-11.31-4.69l-51.55-51.55A15.86 15.86 0 0 1 24 164.45v-72.9a15.86 15.86 0 0 1 4.69-11.31l51.55-51.55A15.86 15.86 0 0 1 91.55 24h72.9a15.86 15.86 0 0 1 11.31 4.69l51.55 51.55A15.86 15.86 0 0 1 232 91.55m-16 0L164.45 40h-72.9L40 91.55v72.9L91.55 216h72.9L216 164.45ZM128 160a12 12 0 1 0 12 12 12 12 0 0 0-12-12"],"LinkBreak":["M198.63 57.37a32 32 0 0 0-45.19-.06l-11.65 12.21a8 8 0 0 1-11.58-11l11.72-12.29a2 2 0 0 1 .13-.13 48 48 0 0 1 67.88 67.88 2 2 0 0 1-.13.13l-12.29 11.72a8 8 0 0 1-11-11.58l12.21-11.65a32 32 0 0 0-.1-45.23m-84.42 129.11-11.65 12.21a32 32 0 0 1-45.25-45.25l12.21-11.65a8 8 0 0 0-11-11.58l-12.33 11.72a2 2 0 0 0-.13.13 48 48 0 0 0 67.88 67.88 2 2 0 0 0 .13-.13l11.72-12.29a8 8 0 1 0-11.58-11ZM216 152h-24a8 8 0 0 0 0 16h24a8 8 0 0 0 0-16M40 104h24a8 8 0 0 0 0-16H40a8 8 0 0 0 0 16m120 80a8 8 0 0 0-8 8v24a8 8 0 0 0 16 0v-24a8 8 0 0 0-8-8M96 72a8 8 0 0 0 8-8V40a8 8 0 0 0-16 0v24a8 8 0 0 0 8 8"],"PauseCircle":["M128 24a104 104 0 1 0 104 104A104.11 104.11 0 0 0 128 24m0 192a88 88 0 1 1 88-88 88.1 88.1 0 0 1-88 88M112 96v64a8 8 0 0 1-16 0V96a8 8 0 0 1 16 0m48 0v64a8 8 0 0 1-16 0V96a8 8 0 0 1 16 0"],"Trash":["M216 48h-40v-8a24 24 0 0 0-24-24h-48a24 24 0 0 0-24 24v8H40a8 8 0 0 0 0 16h8v144a16 16 0 0 0 16 16h128a16 16 0 0 0 16-16V64h8a8 8 0 0 0 0-16M96 40a8 8 0 0 1 8-8h48a8 8 0 0 1 8 8v8H96Zm96 168H64V64h128Zm-80-104v64a8 8 0 0 1-16 0v-64a8 8 0 0 1 16 0m48 0v64a8 8 0 0 1-16 0v-64a8 8 0 0 1 16 0"],"Question":["M140 180a12 12 0 1 1-12-12 12 12 0 0 1 12 12M128 72c-22.06 0-40 16.15-40 36v4a8 8 0 0 0 16 0v-4c0-11 10.77-20 24-20s24 9 24 20-10.77 20-24 20a8 8 0 0 0-8 8v8a8 8 0 0 0 16 0v-.72c18.24-3.35 32-17.9 32-35.28 0-19.85-17.94-36-40-36m104 56A104 104 0 1 1 128 24a104.11 104.11 0 0 1 104 104m-16 0a88 88 0 1 0-88 88 88.1 88.1 0 0 0 88-88"],"Envelope":["M224 48H32a8 8 0 0 0-8 8v136a16 16 0 0 0 16 16h176a16 16 0 0 0 16-16V56a8 8 0 0 0-8-8m-96 85.15L52.57 64h150.86ZM98.71 128 40 181.81V74.19Zm11.84 10.85 12 11.05a8 8 0 0 0 10.82 0l12-11.05 58 53.15H52.57ZM157.29 128 216 74.18v107.64Z"],"Lock":["M208 80h-32V56a48 48 0 0 0-96 0v24H48a16 16 0 0 0-16 16v112a16 16 0 0 0 16 16h160a16 16 0 0 0 16-16V96a16 16 0 0 0-16-16M96 56a32 32 0 0 1 64 0v24H96Zm112 152H48V96h160zm-68-56a12 12 0 1 1-12-12 12 12 0 0 1 12 12"],"ArrowLeft":["M224 128a8 8 0 0 1-8 8H59.31l58.35 58.34a8 8 0 0 1-11.32 11.32l-72-72a8 8 0 0 1 0-11.32l72-72a8 8 0 0 1 11.32 11.32L59.31 120H216a8 8 0 0 1 8 8"],"GoogleLogo":["M224 128a96 96 0 1 1-21.95-61.09 8 8 0 1 1-12.33 10.18A80 80 0 1 0 207.6 136H128a8 8 0 0 1 0-16h88a8 8 0 0 1 8 8"],"Eye":["M247.31 124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57 61.26 162.88 48 128 48S61.43 61.26 36.34 86.35C17.51 105.18 9 124 8.69 124.76a8 8 0 0 0 0 6.5c.35.79 8.82 19.57 27.65 38.4C61.43 194.74 93.12 208 128 208s66.57-13.26 91.66-38.34c18.83-18.83 27.3-37.61 27.65-38.4a8 8 0 0 0 0-6.5M128 192c-30.78 0-57.67-11.19-79.93-33.25A133.5 133.5 0 0 1 25 128a133.3 133.3 0 0 1 23.07-30.75C70.33 75.19 97.22 64 128 64s57.67 11.19 79.93 33.25A133.5 133.5 0 0 1 231.05 128c-7.21 13.46-38.62 64-103.05 64m0-112a48 48 0 1 0 48 48 48.05 48.05 0 0 0-48-48m0 80a32 32 0 1 1 32-32 32 32 0 0 1-32 32"],"EyeSlash":["M53.92 34.62a8 8 0 1 0-11.84 10.76l19.24 21.17C25 88.84 9.38 123.2 8.69 124.76a8 8 0 0 0 0 6.5c.35.79 8.82 19.57 27.65 38.4C61.43 194.74 93.12 208 128 208a127.1 127.1 0 0 0 52.07-10.83l22 24.21a8 8 0 1 0 11.84-10.76Zm47.33 75.84 41.67 45.85a32 32 0 0 1-41.67-45.85M128 192c-30.78 0-57.67-11.19-79.93-33.25A133.2 133.2 0 0 1 25 128c4.69-8.79 19.66-33.39 47.35-49.38l18 19.75a48 48 0 0 0 63.66 70l14.73 16.2A112 112 0 0 1 128 192m6-95.43a8 8 0 0 1 3-15.72 48.16 48.16 0 0 1 38.77 42.64 8 8 0 0 1-7.22 8.71 6 6 0 0 1-.75 0 8 8 0 0 1-8-7.26A32.09 32.09 0 0 0 134 96.57m113.28 34.69c-.42.94-10.55 23.37-33.36 43.8a8 8 0 1 1-10.67-11.92 132.8 132.8 0 0 0 27.8-35.14 133.2 133.2 0 0 0-23.12-30.77C185.67 75.19 158.78 64 128 64a118.4 118.4 0 0 0-19.36 1.57A8 8 0 1 1 106 49.79 134 134 0 0 1 128 48c34.88 0 66.57 13.26 91.66 38.35 18.83 18.83 27.3 37.62 27.65 38.41a8 8 0 0 1 0 6.5Z"],"UserCircle":["M128 24a104 104 0 1 0 104 104A104.11 104.11 0 0 0 128 24M74.08 197.5a64 64 0 0 1 107.84 0 87.83 87.83 0 0 1-107.84 0M96 120a32 32 0 1 1 32 32 32 32 0 0 1-32-32m97.76 66.41a79.66 79.66 0 0 0-36.06-28.75 48 48 0 1 0-59.4 0 79.66 79.66 0 0 0-36.06 28.75 88 88 0 1 1 131.52 0"],"CaretDown":["m213.66 101.66-80 80a8 8 0 0 1-11.32 0l-80-80a8 8 0 0 1 11.32-11.32L128 164.69l74.34-74.35a8 8 0 0 1 11.32 11.32"],"ArrowUp":["M205.66 117.66a8 8 0 0 1-11.32 0L136 59.31V216a8 8 0 0 1-16 0V59.31l-58.34 58.35a8 8 0 0 1-11.32-11.32l72-72a8 8 0 0 1 11.32 0l72 72a8 8 0 0 1 0 11.32"],"ArrowDown":["m205.66 149.66-72 72a8 8 0 0 1-11.32 0l-72-72a8 8 0 0 1 11.32-11.32L120 196.69V40a8 8 0 0 1 16 0v156.69l58.34-58.35a8 8 0 0 1 11.32 11.32"],"Minus":["M224 128a8 8 0 0 1-8 8H40a8 8 0 0 1 0-16h176a8 8 0 0 1 8 8"],"Plus":["M224 128a8 8 0 0 1-8 8h-80v80a8 8 0 0 1-16 0v-80H40a8 8 0 0 1 0-16h80V40a8 8 0 0 1 16 0v80h80a8 8 0 0 1 8 8"],"ChartBar":["M224 200h-8V40a8 8 0 0 0-8-8h-56a8 8 0 0 0-8 8v40H96a8 8 0 0 0-8 8v40H48a8 8 0 0 0-8 8v64h-8a8 8 0 0 0 0 16h192a8 8 0 0 0 0-16M160 48h40v152h-40Zm-56 48h40v104h-40Zm-48 48h32v56H56Z"],"ChatCircle":["M128 24a104 104 0 0 0-91.82 152.88l-11.35 34.05a16 16 0 0 0 20.24 20.24l34.05-11.35A104 104 0 1 0 128 24m0 192a87.87 87.87 0 0 1-44.06-11.81 8 8 0 0 0-6.54-.67L40 216l12.47-37.4a8 8 0 0 0-.66-6.54A88 88 0 1 1 128 216"],"Target":["M221.87 83.16A104.1 104.1 0 1 1 195.67 49l22.67-22.68a8 8 0 0 1 11.32 11.32l-96 96a8 8 0 0 1-11.32-11.32l27.72-27.72a40 40 0 1 0 17.87 31.09 8 8 0 1 1 16-.9 56 56 0 1 1-22.38-41.65l22.75-22.75a87.88 87.88 0 1 0 23.13 29.67 8 8 0 0 1 14.44-6.9"],"ListBullets":["M80 64a8 8 0 0 1 8-8h128a8 8 0 0 1 0 16H88a8 8 0 0 1-8-8m136 56H88a8 8 0 0 0 0 16h128a8 8 0 0 0 0-16m0 64H88a8 8 0 0 0 0 16h128a8 8 0 0 0 0-16M44 52a12 12 0 1 0 12 12 12 12 0 0 0-12-12m0 64a12 12 0 1 0 12 12 12 12 0 0 0-12-12m0 64a12 12 0 1 0 12 12 12 12 0 0 0-12-12"],"House":["m219.31 108.68-80-80a16 16 0 0 0-22.62 0l-80 80A15.87 15.87 0 0 0 32 120v96a8 8 0 0 0 8 8h64a8 8 0 0 0 8-8v-56h32v56a8 8 0 0 0 8 8h64a8 8 0 0 0 8-8v-96a15.87 15.87 0 0 0-4.69-11.32M208 208h-48v-56a8 8 0 0 0-8-8h-48a8 8 0 0 0-8 8v56H48v-88l80-80 80 80Z"],"SlidersHorizontal":["M40 88h33a32 32 0 0 0 62 0h81a8 8 0 0 0 0-16h-81a32 32 0 0 0-62 0H40a8 8 0 0 0 0 16m64-24a16 16 0 1 1-16 16 16 16 0 0 1 16-16m112 104h-17a32 32 0 0 0-62 0H40a8 8 0 0 0 0 16h97a32 32 0 0 0 62 0h17a8 8 0 0 0 0-16m-48 24a16 16 0 1 1 16-16 16 16 0 0 1-16 16"],"X":["M205.66 194.34a8 8 0 0 1-11.32 11.32L128 139.31l-66.34 66.35a8 8 0 0 1-11.32-11.32L116.69 128 50.34 61.66a8 8 0 0 1 11.32-11.32L128 116.69l66.34-66.35a8 8 0 0 1 11.32 11.32L139.31 128Z"],"ArrowSquareOut":["M224 104a8 8 0 0 1-16 0V59.32l-66.33 66.34a8 8 0 0 1-11.32-11.32L196.68 48H152a8 8 0 0 1 0-16h64a8 8 0 0 1 8 8Zm-40 24a8 8 0 0 0-8 8v72H48V80h72a8 8 0 0 0 0-16H48a16 16 0 0 0-16 16v128a16 16 0 0 0 16 16h128a16 16 0 0 0 16-16v-72a8 8 0 0 0-8-8"],"PiggyBank":["M192 116a12 12 0 1 1-12-12 12 12 0 0 1 12 12m-40-52h-40a8 8 0 0 0 0 16h40a8 8 0 0 0 0-16m96 48v32a24 24 0 0 1-24 24h-2.36l-16.21 45.38A16 16 0 0 1 190.36 224h-12.72a16 16 0 0 1-15.07-10.62l-1.92-5.38h-57.3l-1.92 5.38A16 16 0 0 1 86.36 224H73.64a16 16 0 0 1-15.07-10.62L46 178.22a87.7 87.7 0 0 1-21.44-48.38A16 16 0 0 0 16 144a8 8 0 0 1-16 0 32 32 0 0 1 24.28-31A88.12 88.12 0 0 1 112 32h104a8 8 0 0 1 0 16h-21.39a87.93 87.93 0 0 1 30.17 37c.43 1 .85 2 1.25 3A24 24 0 0 1 248 112m-16 0a8 8 0 0 0-8-8h-3.66a8 8 0 0 1-7.64-5.6A71.9 71.9 0 0 0 144 48h-32a72 72 0 0 0-53.09 120.64 8 8 0 0 1 1.64 2.71L73.64 208h12.72l3.82-10.69a8 8 0 0 1 7.53-5.31h68.58a8 8 0 0 1 7.53 5.31l3.82 10.69h12.72l18.11-50.69A8 8 0 0 1 216 152h8a8 8 0 0 0 8-8Z"],"Gear":["M128 80a48 48 0 1 0 48 48 48.05 48.05 0 0 0-48-48m0 80a32 32 0 1 1 32-32 32 32 0 0 1-32 32m88-29.84q.06-2.16 0-4.32l14.92-18.64a8 8 0 0 0 1.48-7.06 107.2 107.2 0 0 0-10.88-26.25 8 8 0 0 0-6-3.93l-23.72-2.64q-1.48-1.56-3-3L186 40.54a8 8 0 0 0-3.94-6 107.7 107.7 0 0 0-26.25-10.87 8 8 0 0 0-7.06 1.49L130.16 40h-4.32L107.2 25.11a8 8 0 0 0-7.06-1.48 107.6 107.6 0 0 0-26.25 10.88 8 8 0 0 0-3.93 6l-2.64 23.76q-1.56 1.49-3 3L40.54 70a8 8 0 0 0-6 3.94 107.7 107.7 0 0 0-10.87 26.25 8 8 0 0 0 1.49 7.06L40 125.84v4.32L25.11 148.8a8 8 0 0 0-1.48 7.06 107.2 107.2 0 0 0 10.88 26.25 8 8 0 0 0 6 3.93l23.72 2.64q1.49 1.56 3 3L70 215.46a8 8 0 0 0 3.94 6 107.7 107.7 0 0 0 26.25 10.87 8 8 0 0 0 7.06-1.49L125.84 216q2.16.06 4.32 0l18.64 14.92a8 8 0 0 0 7.06 1.48 107.2 107.2 0 0 0 26.25-10.88 8 8 0 0 0 3.93-6l2.64-23.72q1.56-1.48 3-3l23.78-2.8a8 8 0 0 0 6-3.94 107.7 107.7 0 0 0 10.87-26.25 8 8 0 0 0-1.49-7.06Zm-16.1-6.5a74 74 0 0 1 0 8.68 8 8 0 0 0 1.74 5.48l14.19 17.73a91.6 91.6 0 0 1-6.23 15l-22.6 2.56a8 8 0 0 0-5.1 2.64 74 74 0 0 1-6.14 6.14 8 8 0 0 0-2.64 5.1l-2.51 22.58a91.3 91.3 0 0 1-15 6.23l-17.74-14.19a8 8 0 0 0-5-1.75h-.48a74 74 0 0 1-8.68 0 8 8 0 0 0-5.48 1.74l-17.78 14.2a91.6 91.6 0 0 1-15-6.23L82.89 187a8 8 0 0 0-2.64-5.1 74 74 0 0 1-6.14-6.14 8 8 0 0 0-5.1-2.64l-22.58-2.52a91.3 91.3 0 0 1-6.23-15l14.19-17.74a8 8 0 0 0 1.74-5.48 74 74 0 0 1 0-8.68 8 8 0 0 0-1.74-5.48L40.2 100.45a91.6 91.6 0 0 1 6.23-15L69 82.89a8 8 0 0 0 5.1-2.64 74 74 0 0 1 6.14-6.14A8 8 0 0 0 82.89 69l2.51-22.57a91.3 91.3 0 0 1 15-6.23l17.74 14.19a8 8 0 0 0 5.48 1.74 74 74 0 0 1 8.68 0 8 8 0 0 0 5.48-1.74l17.77-14.19a91.6 91.6 0 0 1 15 6.23L173.11 69a8 8 0 0 0 2.64 5.1 74 74 0 0 1 6.14 6.14 8 8 0 0 0 5.1 2.64l22.58 2.51a91.3 91.3 0 0 1 6.23 15l-14.19 17.74a8 8 0 0 0-1.74 5.53Z"],"CalendarBlank":["M208 32h-24v-8a8 8 0 0 0-16 0v8H88v-8a8 8 0 0 0-16 0v8H48a16 16 0 0 0-16 16v160a16 16 0 0 0 16 16h160a16 16 0 0 0 16-16V48a16 16 0 0 0-16-16M72 48v8a8 8 0 0 0 16 0v-8h80v8a8 8 0 0 0 16 0v-8h24v32H48V48Zm136 160H48V96h160z"],"ArrowsClockwise":["M224 48v48a8 8 0 0 1-8 8h-48a8 8 0 0 1 0-16h28.69l-14.63-14.63a79.56 79.56 0 0 0-56.13-23.43h-.45a79.52 79.52 0 0 0-55.89 22.77 8 8 0 0 1-11.18-11.44 96 96 0 0 1 135 .79L208 76.69V48a8 8 0 0 1 16 0m-37.59 135.29a80 80 0 0 1-112.47-.66L59.31 168H88a8 8 0 0 0 0-16H40a8 8 0 0 0-8 8v48a8 8 0 0 0 16 0v-28.69l14.63 14.63A95.43 95.43 0 0 0 130 222.06h.53a95.36 95.36 0 0 0 67.07-27.33 8 8 0 0 0-11.18-11.44Z"],"Copy":["M216 32H88a8 8 0 0 0-8 8v40H40a8 8 0 0 0-8 8v128a8 8 0 0 0 8 8h128a8 8 0 0 0 8-8v-40h40a8 8 0 0 0 8-8V40a8 8 0 0 0-8-8m-56 176H48V96h112Zm48-48h-32V88a8 8 0 0 0-8-8H96V48h112Z"],"ThumbsUp":["M234 80.12A24 24 0 0 0 216 72h-56V56a40 40 0 0 0-40-40 8 8 0 0 0-7.16 4.42L75.06 96H32a16 16 0 0 0-16 16v88a16 16 0 0 0 16 16h172a24 24 0 0 0 23.82-21l12-96A24 24 0 0 0 234 80.12M32 112h40v88H32Zm191.94-15-12 96a8 8 0 0 1-7.94 7H88v-94.11l36.71-73.43A24 24 0 0 1 144 56v24a8 8 0 0 0 8 8h64a8 8 0 0 1 7.94 9"],"ThumbsDown":["m239.82 157-12-96A24 24 0 0 0 204 40H32a16 16 0 0 0-16 16v88a16 16 0 0 0 16 16h43.06l37.78 75.58A8 8 0 0 0 120 240a40 40 0 0 0 40-40v-16h56a24 24 0 0 0 23.82-27M72 144H32V56h40Zm150 21.29a7.88 7.88 0 0 1-6 2.71h-64a8 8 0 0 0-8 8v24a24 24 0 0 1-19.29 23.54L88 150.11V56h116a8 8 0 0 1 7.94 7l12 96a7.87 7.87 0 0 1-1.94 6.29"],"Stop":["M200 40H56a16 16 0 0 0-16 16v144a16 16 0 0 0 16 16h144a16 16 0 0 0 16-16V56a16 16 0 0 0-16-16m0 160H56V56h144z"],"CreditCard":["M224 48H32a16 16 0 0 0-16 16v128a16 16 0 0 0 16 16h192a16 16 0 0 0 16-16V64a16 16 0 0 0-16-16m0 16v24H32V64Zm0 128H32v-88h192zm-16-24a8 8 0 0 1-8 8h-32a8 8 0 0 1 0-16h32a8 8 0 0 1 8 8m-64 0a8 8 0 0 1-8 8h-16a8 8 0 0 1 0-16h16a8 8 0 0 1 8 8"],"Bank":["M24 104h24v64H32a8 8 0 0 0 0 16h192a8 8 0 0 0 0-16h-16v-64h24a8 8 0 0 0 4.19-14.81l-104-64a8 8 0 0 0-8.38 0l-104 64A8 8 0 0 0 24 104m40 0h32v64H64Zm80 0v64h-32v-64Zm48 64h-32v-64h32ZM128 41.39 203.74 88H52.26ZM248 208a8 8 0 0 1-8 8H16a8 8 0 0 1 0-16h224a8 8 0 0 1 8 8"],"ClockCounterClockwise":["M136 80v43.47l36.12 21.67a8 8 0 0 1-8.24 13.72l-40-24A8 8 0 0 1 120 128V80a8 8 0 0 1 16 0m-8-48a95.44 95.44 0 0 0-67.92 28.15C52.81 67.51 46.35 74.59 40 82V64a8 8 0 0 0-16 0v40a8 8 0 0 0 8 8h40a8 8 0 0 0 0-16H49c7.15-8.42 14.27-16.35 22.39-24.57a80 80 0 1 1 1.66 114.75 8 8 0 1 0-11 11.64A96 96 0 1 0 128 32"],"Phone":["m222.37 158.46-47.11-21.11-.13-.06a16 16 0 0 0-15.17 1.4 8 8 0 0 0-.75.56L134.87 160c-15.42-7.49-31.34-23.29-38.83-38.51l20.78-24.71c.2-.25.39-.5.57-.77a16 16 0 0 0 1.32-15.06v-.12L97.54 33.64a16 16 0 0 0-16.62-9.52A56.26 56.26 0 0 0 32 80c0 79.4 64.6 144 144 144a56.26 56.26 0 0 0 55.88-48.92 16 16 0 0 0-9.51-16.62M176 208A128.14 128.14 0 0 1 48 80a40.2 40.2 0 0 1 34.87-40 .6.6 0 0 0 0 .12l21 47-20.67 24.74a6 6 0 0 0-.57.77 16 16 0 0 0-1 15.7c9.06 18.53 27.73 37.06 46.46 46.11a16 16 0 0 0 15.75-1.14 8 8 0 0 0 .74-.56L168.89 152l47 21.05h.11A40.21 40.21 0 0 1 176 208"],"WhatsappLogo":["m187.58 144.84-32-16a8 8 0 0 0-8 .5l-14.69 9.8a40.55 40.55 0 0 1-16-16l9.8-14.69a8 8 0 0 0 .5-8l-16-32A8 8 0 0 0 104 64a40 40 0 0 0-40 40 88.1 88.1 0 0 0 88 88 40 40 0 0 0 40-40 8 8 0 0 0-4.42-7.16M152 176a72.08 72.08 0 0 1-72-72 24 24 0 0 1 19.29-23.54l11.48 23L101 118a8 8 0 0 0-.73 7.51 56.47 56.47 0 0 0 30.15 30.15A8 8 0 0 0 138 155l14.61-9.74 23 11.48A24 24 0 0 1 152 176M128 24a104 104 0 0 0-91.82 152.88l-11.35 34.05a16 16 0 0 0 20.24 20.24l34.05-11.35A104 104 0 1 0 128 24m0 192a87.87 87.87 0 0 1-44.06-11.81 8 8 0 0 0-6.54-.67L40 216l12.47-37.4a8 8 0 0 0-.66-6.54A88 88 0 1 1 128 216"]};
  function Icon(p) {
    var size = p.size || 24;
    return h("svg", { className: "pb-icon", width: size, height: size, viewBox: "0 0 256 256", fill: p.color || tone(p.tone || "ink"), "aria-hidden": "true", focusable: "false" },
      (ICONS[p.name] || ICONS.Question).map(function (d, i) { return h("path", { key: i, d: d }); }));
  }

  var STYLE = { display: "display", title: "title", section: "section", body: "body", label: "label", caption: "caption" };
  function PbText(p) {
    var El = p.as || "span";
    return h(El, { className: cx("pb-text", STYLE[p.variant || "body"], p.numeric && "pb-num", p.className), style: Object.assign({ color: tone(p.tone || "ink") }, p.style) }, p.children);
  }

  function Spinner(p) { return h("span", { className: "pb-spinner", style: { color: tone(p.tone || "ink-muted") }, "aria-hidden": "true" }); }

  var BUTTON_TONE = { primary: "on-action", secondary: "ink", ghost: "brand-ink", link: "brand-ink", danger: "on-danger" };
  function Button(p) {
    var v = p.variant || "primary";
    return h("button", { type: "button", className: cx("pb-root pb-btn pb-focus", "pb-btn-" + v, p.size === "L" && "pb-btn-l", p.loading && "pb-btn-loading"), disabled: p.disabled || p.loading, "aria-busy": p.loading || undefined, onClick: p.onClick },
      h("span", { className: "pb-btn-label" }, p.icon ? h(Icon, { name: p.icon, size: 20, tone: BUTTON_TONE[v] }) : null, p.children),
      p.loading ? h(Spinner, { tone: BUTTON_TONE[v] }) : null);
  }

  function Chip(p) {
    return h("button", { type: "button", className: "pb-root pb-chip pb-focus", "aria-pressed": !!p.selected, disabled: p.disabled, onClick: p.onClick }, p.children);
  }

  function SegmentedControl(p) {
    return h("div", { className: "pb-root pb-seg", role: "tablist" },
      p.options.map(function (o) {
        return h("button", { key: o, type: "button", role: "tab", className: "pb-focus", "aria-selected": o === p.value, onClick: function () { if (o !== p.value && p.onChange) p.onChange(o); } }, o);
      }));
  }

  function Field(p, control) {
    return h("div", { className: "pb-root pb-field" },
      h("label", { htmlFor: p.id, className: "pb-text label", style: { color: tone(p.inactive ? "ink-muted" : "ink") } }, p.label),
      control,
      p.error ? h("span", { className: "pb-text caption", role: "alert", style: { color: tone("danger") } }, p.error) : null);
  }
  var nextId = 0;
  function useId() { var r = React.useRef(null); if (r.current === null) r.current = "pb-f" + ++nextId; return r.current; }
  function Input(p) {
    var id = useId(), inactive = p.disabled || p.readOnly;
    var box = cx("pb-input", p.error && "pb-input-error", inactive && "pb-input-inactive");
    var field = p.readOnly
      ? h("output", { id: id, className: cx(!p.icon && box, "pb-input-text") }, p.value)
      : h("input", { id: id, className: cx(!p.icon && box, "pb-input-text"), value: p.value, placeholder: p.placeholder, type: p.type || "text", disabled: p.disabled, "aria-invalid": !!p.error || undefined, onChange: function (e) { p.onChange && p.onChange(e.target.value); } });
    return Field({ id: id, label: p.label, error: p.error, inactive: inactive },
      p.icon ? h("div", { className: cx(box, "pb-input-icon") }, h(Icon, { name: p.icon, size: 20, tone: "ink-muted" }), field) : field);
  }

  // cents: integer centavos. Same rules as app Money: + and green only for "entrada", − (U+2212) for "saida" or a negative balance.
  var MAX_CENTS = 9999999999;
  function parts(cents) {
    if (typeof cents !== "number" || !isFinite(cents)) return null;
    var abs = Math.abs(Math.round(cents));
    var whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return { whole: whole, cents: String(abs % 100).padStart(2, "0"), negative: cents < 0, zero: abs === 0 };
  }
  function AmountInput(p) {
    var id = useId(), t = p.disabled ? "ink-muted" : "ink";
    var m = p.cents >= 0 && p.cents <= MAX_CENTS ? parts(p.cents) : null;
    return Field({ id: id, label: p.label, error: p.error, inactive: p.disabled },
      h("div", { className: "pb-amount" },
        h(PbText, { variant: "title", tone: t }, "R$"),
        h("input", { id: id, className: "pb-text title pb-num pb-amount-input", style: { color: tone(t) }, inputMode: "numeric", autoComplete: "off", placeholder: "—", disabled: p.disabled, "aria-invalid": !!p.error || undefined,
          value: m ? m.whole + "," + m.cents : "",
          onChange: function (e) {
            var digits = e.target.value.replace(/\D/g, "");
            var n = digits ? parseInt(digits, 10) : 0;
            if (n <= MAX_CENTS && n !== p.cents && p.onChange) p.onChange(n);
          } })));
  }

  function Card(p) {
    return h("div", { className: cx("pb-root pb-card", p.elevation === "raised" && "pb-card-raised"), style: p.style }, p.children);
  }

  function InsightCard(p) {
    return h(Card, { elevation: "raised" },
      h("div", { className: "pb-hrow" },
        p.sticker ? h("img", { src: p.sticker, alt: "", width: 40, height: 40, className: "pb-sticker" }) : null,
        h("div", { className: "pb-stack" },
          h(PbText, { variant: "label" }, p.title),
          h(PbText, { variant: "body", tone: "ink-muted" }, p.message),
          p.action ? h(Button, { variant: "ghost", onClick: p.action.onClick }, p.action.label) : null)));
  }

  var BANNER = { info: ["ink", "Info"], warning: ["warning", "WarningCircle"], danger: ["danger", "WarningOctagon"] };
  function Banner(p) {
    var v = BANNER[p.tone || "info"];
    return h("div", { className: "pb-root pb-banner", role: p.tone === "danger" ? "alert" : undefined },
      h(Icon, { name: v[1], tone: v[0] }),
      h("div", { className: "pb-stack" },
        p.title ? h(PbText, { variant: "label" }, p.title) : null,
        h(PbText, { variant: "body", tone: "ink-muted" }, p.message),
        p.action ? h(Button, { variant: "ghost", onClick: p.action.onClick }, p.action.label) : null),
      p.onClose ? h("button", { type: "button", className: "pb-close pb-focus", "aria-label": "Fechar aviso", onClick: p.onClose }, h(Icon, { name: "X", size: 20, tone: "ink-muted" })) : null);
  }

  var TOAST = { sucesso: ["positive", "CheckCircle"], info: ["ink", "Info"], aviso: ["warning", "WarningCircle"], erro: ["danger", "WarningOctagon"] };
  function Toast(p) {
    var v = TOAST[p.tone || "info"];
    return h("div", { className: "pb-toast", role: "status" },
      h(Card, { elevation: "raised" }, h("div", { className: "pb-hrow pb-center" }, h(Icon, { name: v[1], tone: v[0] }), h(PbText, { variant: "body", style: { flex: 1 } }, p.message))));
  }

  function EmptyState(p) {
    return h("div", { className: "pb-root pb-empty" },
      h("div", { className: "pb-halo" }, h("img", { src: p.sticker, alt: "", width: 96, height: 96, className: "pb-sticker" })),
      h(PbText, { variant: "body", tone: "ink-muted", style: { textAlign: "center" } }, p.message),
      p.action ? h(Button, { onClick: p.action.onClick }, p.action.label) : null);
  }

  var CONNECTION = {
    updated: ["positive", "CheckCircle"], partial: ["warning", "WarningCircle"], updating: ["ink-muted", null],
    error_recoverable: ["warning", "WarningCircle"], needs_user_action: ["warning", "WarningCircle"], item_missing: ["danger", "LinkBreak"],
    paused: ["ink-muted", "PauseCircle"], removed: ["ink-muted", "Trash"], no_accounts: ["ink-muted", "Info"]
  };
  function ConnectionStatus(p) {
    var v = CONNECTION[p.state] || ["ink-muted", "Question"];
    return h("div", { className: "pb-root pb-hrow" },
      v[1] ? h(Icon, { name: v[1], tone: v[0] }) : h(Spinner, { tone: v[0] }),
      h("div", { className: "pb-stack" },
        h(PbText, { variant: "label", tone: v[0] }, p.label),
        p.detail ? h(PbText, { variant: "caption", tone: "ink-muted" }, p.detail) : null,
        p.action ? h(Button, { variant: "ghost", onClick: p.action.onClick }, p.action.label) : null));
  }

  function ListRow(p) {
    var inactive = p.disabled || p.loading;
    var body = [
      p.icon ? h(Icon, { key: "i", name: p.icon, tone: "ink-muted" }) : null,
      h("div", { key: "t", className: "pb-row-main" },
        h(PbText, { variant: "body", tone: p.tone === "danger" ? "danger" : "ink", as: "div" }, p.title),
        p.subtitle ? h(PbText, { variant: "caption", tone: "ink-muted", as: "div" }, p.subtitle) : null),
      p.trailing ? h(React.Fragment, { key: "r" }, p.trailing) : null,
      p.loading ? h(Spinner, { key: "s", tone: p.tone === "danger" ? "danger" : "ink-muted" })
        : p.chevron && p.tone !== "danger" ? h(Icon, { key: "c", name: "CaretRight", size: 20, tone: "ink-faint" }) : null
    ];
    var cls = cx("pb-root pb-row", p.disabled && "pb-row-disabled");
    return p.onClick
      ? h("button", { type: "button", className: cx(cls, "pb-row-button pb-focus"), disabled: inactive, "aria-busy": p.loading || undefined, onClick: p.onClick }, body)
      : h("div", { className: cls }, body);
  }
  function ListGroup(p) {
    return h("div", { className: "pb-root pb-group" }, React.Children.toArray(p.children).map(function (c, i) {
      return h(React.Fragment, { key: i }, i > 0 ? h("div", { className: "pb-sep" }) : null, c);
    }));
  }

  function Money(p) {
    var type = p.type || "saldo", variant = p.variant || "body";
    var cls = cx("pb-money pb-text pb-num", variant);
    if (p.hidden) return h("span", { className: cls, "aria-label": "valor oculto" }, "R$ ••••••");
    var m = parts(p.cents);
    if (!m) return h("span", { className: cls, "aria-label": "Valor indisponível" }, "—");
    var sign = m.zero ? "" : type === "entrada" ? "+" : type === "saida" || m.negative ? "−" : "";
    var t = !m.zero && type === "entrada" ? "positive" : "ink";
    return h("span", { className: cls, style: { color: tone(t) } },
      sign + "R$ " + m.whole + ",", variant === "display" ? h("span", { style: { color: tone(t === "ink" ? "ink-muted" : t) } }, m.cents) : m.cents);
  }

  var GRAPHEME = /^(?:\p{Regional_Indicator}\p{Regional_Indicator}|\P{M}\p{M}*)/u;
  function initials(name) {
    var w = name.trim().split(/\s+/).filter(Boolean);
    if (!w.length) return "";
    var first = (GRAPHEME.exec(w[0]) || [""])[0], last = w.length > 1 ? (GRAPHEME.exec(w[w.length - 1]) || [""])[0] : "";
    return (first + last).toLocaleUpperCase("pt-BR");
  }
  function Avatar(p) {
    var size = p.size || 40, failed = React.useState(null);
    var showImg = p.image && failed[0] !== p.image;
    return h("div", { className: "pb-root pb-avatar", role: "img", "aria-label": p.name.trim() || "Sem nome", style: { width: size, height: size } },
      showImg ? h("img", { src: p.image, alt: "", width: size, height: size, onError: function () { failed[1](p.image); } })
        : h("span", { className: "pb-text label", style: { color: tone("ink-muted"), fontSize: size * 0.4, lineHeight: size * 0.5 + "px" } }, initials(p.name) || "?"));
  }

  function ProgressBar(p) {
    var v = isNaN(p.value) ? 0 : Math.min(1, Math.max(0, p.value));
    return h("div", { className: "pb-progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(v * 100) },
      h("span", { style: { transform: "scaleX(" + v + ")", background: p.color || tone(p.tone || "brand") } }));
  }

  function Skeleton(p) {
    return h("div", { className: "pb-skeleton", role: "status", "aria-label": "Carregando", style: { width: p.width, height: p.height, borderRadius: p.radius == null ? "var(--radius-chip)" : p.radius } });
  }

  function CompositionBar(p) {
    if (!p.segments || !p.segments.length) return null;
    return h("div", { className: "pb-composition", "aria-hidden": "true" }, p.segments.map(function (s, i) {
      return h("span", { key: i, style: { flexGrow: s.value, background: s.color } });
    }));
  }

  var BAR_H = 120;
  function MonthlyBars(p) {
    if (!p.months || !p.months.length) return null;
    var max = Math.max.apply(null, p.months.map(function (m) { return m.value; }));
    var fmt = p.formatValue || String;
    return h("div", { className: "pb-root pb-bars", style: { minWidth: p.months.length * 40 } },
      h("div", { className: "pb-bars-plot", "aria-hidden": "true" }, p.months.map(function (m) {
        var bh = m.value > 0 && max > 0 ? Math.max(4, Math.round(m.value / max * BAR_H)) : 0;
        return h("div", { key: m.key, className: "pb-bars-col" },
          h("div", { className: "pb-bars-value" }, m.current ? h(PbText, { variant: "caption", numeric: true }, fmt(m.value)) : null),
          h("div", { className: "pb-bars-track", style: { opacity: m.dimmed ? 0.4 : 1 } },
            bh ? h("span", { className: "pb-bars-bar", style: { height: bh, opacity: m.current ? 1 : 0.5 } }) : null),
          h(PbText, { variant: "caption", tone: m.current ? "brand-ink" : m.dimmed ? "ink-faint" : "ink-muted", style: m.current ? { fontWeight: 600 } : null }, m.label + (m.current ? "*" : "")));
      })),
      p.months.some(function (m) { return m.current; }) ? h(PbText, { variant: "caption", tone: "ink-muted", as: "div", style: { textAlign: "center" } }, "* mês em andamento") : null);
  }

  var CH = 130, MX = 12, TOP = 8, BASE = 8;
  function ticksY(max) {
    for (var e = 0; e < 14; e++) for (var k of [1, 2, 3, 5]) {
      var step = k * Math.pow(10, e) * 100, n = Math.max(1, Math.ceil(max / step));
      if (n <= 4) return Array.from({ length: n + 1 }, function (_, i) { return i * step; });
    }
    return [0, max];
  }
  function compact(c) { var r = c / 100; return Math.round(r) < 1000 ? "R$ " + Math.round(r) : "R$ " + String(Math.round(r / 100) / 10).replace(".", ",") + "k"; }
  function reais(c) { var m = parts(c); return m ? "R$ " + m.whole + "," + m.cents : "—"; }
  var curveId = 0;
  function CumulativeCurve(p) {
    var plot = React.useRef(null), wS = React.useState(0), selS = React.useState(null), timer = React.useRef(null);
    var idRef = React.useRef(null); if (idRef.current === null) idRef.current = "pb-area" + ++curveId;
    React.useEffect(function () {
      var el = plot.current; if (!el) return;
      var ro = new ResizeObserver(function () { wS[1](el.clientWidth); }); ro.observe(el);
      return function () { ro.disconnect(); clearTimeout(timer.current); };
    }, []);
    var pts = p.points || [], days = p.daysInMonth;
    if (!pts.length || days < 2) return null;
    var w = wS[0], prev = p.previous ? p.previous.slice(0, days) : null;
    var ticks = ticksY(Math.max.apply(null, pts.concat(prev || []))), topV = ticks[ticks.length - 1];
    var x = function (i) { return MX + (i / (days - 1)) * (w - 2 * MX); };
    var y = function (v) { return TOP + (1 - v / topV) * (CH - TOP - BASE); };
    var path = function (s) { return s.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); }).join(" "); };
    var line = path(pts), area = line + " L" + x(pts.length - 1).toFixed(1) + "," + y(0) + " L" + x(0).toFixed(1) + "," + y(0) + " Z";
    var col = tone(p.tone || "ink"), limit = p.todayIndex == null ? pts.length - 1 : p.todayIndex;
    var pick = function (px) { var u = w - 2 * MX; selS[1](Math.min(Math.max(Math.round(((px - MX) / u) * (days - 1)), 0), limit)); };
    var local = function (e) { return e.clientX - plot.current.getBoundingClientRect().left; };
    var sel = selS[0], today = p.todayIndex == null ? null : { cx: x(p.todayIndex), cy: y(pts[p.todayIndex]) };
    var bubble = sel == null ? null : { cx: x(sel), cy: y(pts[sel]), w: prev ? 148 : 124 };
    var axisDays = [1, 15, 25, days].filter(function (n, i, a) { return n <= days && a.indexOf(n) === i; });
    return h("div", { className: "pb-root pb-curve", "aria-hidden": "true" },
      h("div", { className: "pb-curve-row" },
        h("div", { className: "pb-curve-y" }, ticks.map(function (t) { return h(PbText, { key: t, variant: "caption", tone: "ink-faint", style: { top: y(t) - 8 } }, compact(t)); })),
        h("div", { className: "pb-curve-main" },
          h("div", { ref: plot, className: "pb-curve-plot",
            onPointerDown: function (e) { if (e.pointerType === "mouse") return; var px = local(e); clearTimeout(timer.current); timer.current = setTimeout(function () { pick(px); }, 250); },
            onPointerMove: function (e) { if (e.pointerType === "mouse" || sel != null) pick(local(e)); },
            onPointerUp: function (e) { if (e.pointerType !== "mouse") { clearTimeout(timer.current); selS[1](null); } },
            onPointerLeave: function () { clearTimeout(timer.current); selS[1](null); } },
            w > 0 ? h("svg", { width: w, height: CH },
              ticks.map(function (t) { return h("line", { key: t, x1: 0, x2: w, y1: y(t), y2: y(t), stroke: tone("border"), strokeWidth: 1 }); }),
              h("defs", null, h("linearGradient", { id: idRef.current, x1: 0, y1: 0, x2: 0, y2: 1 }, h("stop", { offset: 0, style: { stopColor: col, stopOpacity: 0.15 } }), h("stop", { offset: 1, style: { stopColor: col, stopOpacity: 0 } }))),
              prev ? h("path", { className: "pb-curve-fade", d: path(prev), fill: "none", stroke: tone("ink-faint"), strokeWidth: 1.5, strokeDasharray: "4 4", strokeLinecap: "round", strokeLinejoin: "round" }) : null,
              h("path", { className: "pb-curve-fade", d: area, fill: "url(#" + idRef.current + ")" }),
              h("path", { className: "pb-curve-line", d: line, pathLength: 1, fill: "none", stroke: col, strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" })) : null,
            w > 0 && today ? h("span", { className: "pb-curve-today", style: { left: today.cx - 4, top: today.cy - 4, background: col } }, h("span", { style: { background: col } })) : null,
            bubble ? h(React.Fragment, null,
              h("span", { className: "pb-curve-scrub", style: { left: bubble.cx - 0.5 } }),
              h("span", { className: "pb-curve-dot", style: { left: bubble.cx - 4, top: bubble.cy - 4, background: col } }),
              h("div", { className: "pb-curve-bubble", style: Object.assign({ width: bubble.w, left: Math.min(Math.max(bubble.cx - bubble.w / 2, 0), Math.max(0, w - bubble.w)) },
                bubble.cy >= (prev ? 2 : 1) * 16 + 18 ? { bottom: CH - bubble.cy + 10 } : { top: bubble.cy + 10 }) },
                h(PbText, { variant: "caption", tone: "ink-muted", numeric: true, as: "div" }, "dia " + (sel + 1) + " · ", h("b", { style: { color: tone("ink"), fontWeight: 600 } }, reais(pts[sel]))),
                prev ? h(PbText, { variant: "caption", tone: "ink-muted", numeric: true, as: "div" }, "mês passado " + reais(prev[Math.min(sel, prev.length - 1)])) : null)) : null),
          h("div", { className: "pb-curve-x" }, w > 0 ? axisDays.map(function (n) { return h(PbText, { key: n, variant: "caption", tone: "ink-faint", style: { left: x(n - 1) - 12 } }, n); }) : null))),
      h("div", { className: "pb-curve-legend" },
        h("span", null, h("i", { style: { width: 16, background: col } }), h(PbText, { variant: "caption", tone: "ink-muted" }, "Este mês")),
        prev ? h("span", null, h("i", { className: "pb-dash" }), h(PbText, { variant: "caption", tone: "ink-muted" }, "Mês passado")) : null));
  }

  function Screen(p) { return h("div", { className: cx("pb-root pb-screen", p.className) }, p.children); }
  function Sheet(p) { return h("div", { className: "pb-root pb-sheet" }, p.children); }

  window.PigBank = {
    Text: PbText, Icon: Icon, Button: Button, Chip: Chip, SegmentedControl: SegmentedControl, Input: Input, AmountInput: AmountInput,
    Card: Card, InsightCard: InsightCard, Banner: Banner, Toast: Toast, EmptyState: EmptyState, ConnectionStatus: ConnectionStatus,
    ListRow: ListRow, ListGroup: ListGroup, TransactionRow: function (p) {
      return h(ListRow, { title: p.title, subtitle: p.date ? p.category + " • " + p.date : p.category, trailing: h(Money, { cents: p.cents, type: p.type, hidden: p.hidden }) });
    },
    Money: Money, Avatar: Avatar, ProgressBar: ProgressBar, Skeleton: Skeleton, CompositionBar: CompositionBar, MonthlyBars: MonthlyBars,
    CumulativeCurve: CumulativeCurve, Screen: Screen, Sheet: Sheet, iconNames: Object.keys(ICONS)
  };
})();
