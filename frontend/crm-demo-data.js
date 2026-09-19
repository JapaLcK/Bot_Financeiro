(function () {
  "use strict";

  const organizations = [
    { id: "aurora", name: "Atelier Aurora", short: "AA", plan: "Growth", goal: 180000, revenue: 142800, winRate: 34, response: "4m 12s", forecast: 214000 },
    { id: "norte", name: "Norte Solar", short: "NS", plan: "Growth", goal: 260000, revenue: 198500, winRate: 41, response: "3m 05s", forecast: 312000 },
    { id: "vitta", name: "Vitta Educação", short: "VE", plan: "Starter", goal: 95000, revenue: 61800, winRate: 29, response: "7m 40s", forecast: 104500 }
  ];

  const companies = [
    { id: "c1", org: "aurora", name: "Marea Studio", segment: "Arquitetura", owner: "Camila Rocha", contact: "Bianca Nunes", value: 42000, health: "Alta", last: "Hoje, 09:40" },
    { id: "c2", org: "aurora", name: "Orbe Wellness", segment: "Bem-estar", owner: "Lucas Martins", contact: "Renato Lessa", value: 28500, health: "Média", last: "Ontem, 16:20" },
    { id: "c3", org: "aurora", name: "Trama Casa", segment: "Varejo", owner: "Joana Freire", contact: "Marina Alves", value: 68000, health: "Alta", last: "12 set, 14:10" },
    { id: "c4", org: "norte", name: "BioVale Alimentos", segment: "Indústria", owner: "Lucas Martins", contact: "Felipe Maia", value: 96000, health: "Alta", last: "Hoje, 10:05" },
    { id: "c5", org: "norte", name: "Grupo Sete", segment: "Logística", owner: "Joana Freire", contact: "Sofia Reis", value: 74000, health: "Média", last: "Ontem, 11:00" },
    { id: "c6", org: "vitta", name: "Colégio Horizonte", segment: "Educação", owner: "Camila Rocha", contact: "Lara Couto", value: 35000, health: "Alta", last: "Hoje, 08:30" },
    { id: "c7", org: "vitta", name: "Instituto Nexo", segment: "Educação", owner: "Lucas Martins", contact: "Caio Lima", value: 21800, health: "Baixa", last: "10 set, 18:10" }
  ];

  const contacts = [
    { id: "p1", org: "aurora", name: "Bianca Nunes", company: "Marea Studio", role: "Sócia-diretora", email: "bianca@marea.example", channel: "WhatsApp", score: 92 },
    { id: "p2", org: "aurora", name: "Renato Lessa", company: "Orbe Wellness", role: "Head de expansão", email: "renato@orbe.example", channel: "E-mail", score: 78 },
    { id: "p3", org: "aurora", name: "Marina Alves", company: "Trama Casa", role: "Diretora comercial", email: "marina@trama.example", channel: "WhatsApp", score: 87 },
    { id: "p4", org: "norte", name: "Felipe Maia", company: "BioVale Alimentos", role: "CFO", email: "felipe@biovale.example", channel: "WhatsApp", score: 95 },
    { id: "p5", org: "norte", name: "Sofia Reis", company: "Grupo Sete", role: "COO", email: "sofia@gruposete.example", channel: "E-mail", score: 82 },
    { id: "p6", org: "vitta", name: "Lara Couto", company: "Colégio Horizonte", role: "Mantenedora", email: "lara@horizonte.example", channel: "WhatsApp", score: 89 },
    { id: "p7", org: "vitta", name: "Caio Lima", company: "Instituto Nexo", role: "Coordenador", email: "caio@nexo.example", channel: "WhatsApp", score: 64 }
  ];

  const deals = [
    { id: "d1", org: "aurora", company: "Marea Studio", title: "Expansão anual", value: 42000, stage: "proposal", owner: "Camila", next: "Revisar proposta hoje" },
    { id: "d2", org: "aurora", company: "Orbe Wellness", title: "Unidade Pinheiros", value: 28500, stage: "qualified", owner: "Lucas", next: "Demo amanhã, 10h" },
    { id: "d3", org: "aurora", company: "Trama Casa", title: "Operação omnichannel", value: 68000, stage: "negotiation", owner: "Joana", next: "Aprovação jurídica" },
    { id: "d4", org: "aurora", company: "Alba Design", title: "Pacote consultivo", value: 18800, stage: "lead", owner: "Lucas", next: "Qualificar até sexta" },
    { id: "d5", org: "aurora", company: "Estação 23", title: "Renovação premium", value: 33600, stage: "won", owner: "Camila", next: "Onboarding em 16 set" },
    { id: "d6", org: "norte", company: "BioVale Alimentos", title: "Rollout nacional", value: 96000, stage: "negotiation", owner: "Lucas", next: "Call com CFO hoje" },
    { id: "d7", org: "norte", company: "Grupo Sete", title: "Frota conectada", value: 74000, stage: "proposal", owner: "Joana", next: "Follow-up em 2 dias" },
    { id: "d8", org: "norte", company: "Áxis Cargas", title: "Piloto regional", value: 36000, stage: "qualified", owner: "Camila", next: "Enviar escopo" },
    { id: "d9", org: "vitta", company: "Colégio Horizonte", title: "Jornada de matrículas", value: 35000, stage: "proposal", owner: "Camila", next: "Proposta enviada" },
    { id: "d10", org: "vitta", company: "Instituto Nexo", title: "Portal acadêmico", value: 21800, stage: "lead", owner: "Lucas", next: "Validar orçamento" }
  ];

  const tasks = [
    { id: "t1", org: "aurora", title: "Ligar para Bianca sobre condições", due: "Hoje, 11:30", priority: "Alta", owner: "Camila Rocha", done: false },
    { id: "t2", org: "aurora", title: "Revisar minuta da Trama Casa", due: "Hoje, 15:00", priority: "Alta", owner: "Joana Freire", done: false },
    { id: "t3", org: "aurora", title: "Preparar demonstração da Orbe", due: "Amanhã, 09:00", priority: "Média", owner: "Lucas Martins", done: false },
    { id: "t4", org: "aurora", title: "Registrar notas da Estação 23", due: "Concluída hoje", priority: "Baixa", owner: "Camila Rocha", done: true },
    { id: "t5", org: "norte", title: "Enviar estudo para BioVale", due: "Hoje, 14:00", priority: "Alta", owner: "Lucas Martins", done: false },
    { id: "t6", org: "norte", title: "Atualizar previsão do Grupo Sete", due: "Amanhã, 12:00", priority: "Média", owner: "Joana Freire", done: false },
    { id: "t7", org: "vitta", title: "Retomar contato com Instituto Nexo", due: "Hoje, 16:30", priority: "Alta", owner: "Lucas Martins", done: false }
  ];

  const conversations = [
    { id: "i1", org: "aurora", kind: "sales", contact: "Bianca Nunes", company: "Marea Studio", channel: "WhatsApp", preview: "Podemos ajustar o prazo para 18 meses?", time: "2 min", unread: 2, owner: "Não atribuída", status: "open", messages: [
      { from: "them", text: "Olá, Camila. Gostamos bastante da proposta." }, { from: "them", text: "Podemos ajustar o prazo para 18 meses?" }, { from: "us", text: "Claro, Bianca. Vou recalcular as condições e retorno ainda hoje." }
    ] },
    { id: "i2", org: "aurora", kind: "sales", contact: "Renato Lessa", company: "Orbe Wellness", channel: "E-mail", preview: "Confirmado para amanhã às 10h.", time: "18 min", unread: 0, owner: "Lucas Martins", status: "open", messages: [
      { from: "them", text: "Confirmado para amanhã às 10h. Até lá!" }, { from: "us", text: "Perfeito. Já enviei o convite com a pauta da demonstração." }
    ] },
    { id: "i3", org: "aurora", kind: "support", contact: "Marina Alves", company: "Trama Casa", channel: "WhatsApp", preview: "O relatório não atualizou hoje.", time: "34 min", unread: 1, owner: "Não atribuída", status: "open", messages: [
      { from: "them", text: "Bom dia. O relatório não atualizou hoje." }, { from: "us", text: "Recebido. Vou verificar com o time e manter você informada por aqui." }
    ] },
    { id: "i4", org: "norte", kind: "sales", contact: "Felipe Maia", company: "BioVale Alimentos", channel: "WhatsApp", preview: "Conseguimos falar às 15h?", time: "6 min", unread: 1, owner: "Lucas Martins", status: "open", messages: [{ from: "them", text: "Conseguimos falar às 15h? Quero fechar os pontos do rollout." }] },
    { id: "i5", org: "vitta", kind: "support", contact: "Lara Couto", company: "Colégio Horizonte", channel: "WhatsApp", preview: "Agora funcionou, obrigada!", time: "1 h", unread: 0, owner: "Ana Dias", status: "closed", messages: [{ from: "them", text: "Agora funcionou, obrigada!" }] }
  ];

  const whatsapp = [
    { id: "w1", org: "aurora", name: "Comercial oficial", type: "API Oficial", number: "+55 11 4002-8922", status: "connected", quality: "Alta", today: 184 },
    { id: "w2", org: "aurora", name: "Prospecção SDR", type: "API não oficial", number: "+55 11 98888-1040", status: "paused", quality: "Atenção", today: 76 },
    { id: "w3", org: "norte", name: "Norte Solar Vendas", type: "API Oficial", number: "+55 31 3111-2400", status: "connected", quality: "Alta", today: 212 },
    { id: "w4", org: "vitta", name: "Central de matrículas", type: "API não oficial", number: "+55 21 97777-0808", status: "disconnected", quality: "—", today: 0 }
  ];

  const team = [
    { id: "u1", org: "aurora", name: "Lucas Martins", initials: "LM", role: "Administrador", points: 4820, streak: 12, goal: 86, status: "online" },
    { id: "u2", org: "aurora", name: "Camila Rocha", initials: "CR", role: "Gestor", points: 5290, streak: 18, goal: 94, status: "online" },
    { id: "u3", org: "aurora", name: "Joana Freire", initials: "JF", role: "Vendedor", points: 4310, streak: 8, goal: 79, status: "away" },
    { id: "u4", org: "aurora", name: "Ana Dias", initials: "AD", role: "Atendimento", points: 3890, streak: 15, goal: 91, status: "online" },
    { id: "u5", org: "norte", name: "Lucas Martins", initials: "LM", role: "Administrador", points: 4820, streak: 12, goal: 76, status: "online" },
    { id: "u6", org: "norte", name: "Joana Freire", initials: "JF", role: "Gestor", points: 4310, streak: 8, goal: 82, status: "away" },
    { id: "u7", org: "vitta", name: "Camila Rocha", initials: "CR", role: "Gestor", points: 5290, streak: 18, goal: 71, status: "online" }
  ];

  window.CRMDemoData = {
    organizations, companies, contacts, deals, tasks, conversations, whatsapp, team,
    stages: [
      { id: "lead", label: "Novos leads" }, { id: "qualified", label: "Qualificados" },
      { id: "proposal", label: "Proposta" }, { id: "negotiation", label: "Negociação" },
      { id: "won", label: "Ganhos" }
    ],
    activities: [
      { org: "aurora", icon: "ph-handshake", text: "Camila moveu Marea Studio para Proposta", time: "há 12 min" },
      { org: "aurora", icon: "ph-chat-circle", text: "Nova resposta de Bianca Nunes no WhatsApp", time: "há 18 min" },
      { org: "aurora", icon: "ph-check-circle", text: "Joana concluiu revisão de contrato", time: "há 42 min" },
      { org: "norte", icon: "ph-chart-line-up", text: "Forecast da BioVale atualizado para R$ 96 mil", time: "há 9 min" },
      { org: "vitta", icon: "ph-lifebuoy", text: "Chamado do Colégio Horizonte resolvido", time: "há 1 h" }
    ]
  };
}());
