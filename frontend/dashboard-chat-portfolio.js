/* Cartão da carteira Open Finance no chat do Piggy. */
(function () {
  function asksAboutPortfolio(text) {
    return /\b(quanto|quais|como|mostr\w*|list\w*|detalh\w*|ver|veja|tenho|saldo|carteira|posi[cç][aã]o)\b/i.test(text)
      && /caixinh|investiment|carteira|renda fixa|renda vari[aá]vel|a[cç][oõ]es|ativos|cdb|tesouro/i.test(text);
  }

  async function renderPortfolioCard(userId, ask) {
    const body = document.getElementById("piggy-body");
    const card = document.createElement("section");
    card.className = "piggy-msg assistant piggy-portfolio";
    card.setAttribute("aria-label", "Carteira compartilhada pelo Open Finance");
    card.textContent = "Carregando carteira do Open Finance…";
    body.append(card);
    document.getElementById("piggy-panel").classList.add("has-portfolio");
    body.scrollTop = body.scrollHeight;

    try {
      const response = await fetch(`/open-finance/${userId}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Não foi possível consultar o Open Finance.");
      const data = await response.json();
      card.replaceChildren();
      const investments = (data.investments || []).filter(item =>
        (item.currency || "BRL").toUpperCase() === "BRL" &&
        Number.isFinite(Number(item.balance)) && Number(item.balance) > 0);
      if (!investments.length) {
        card.textContent = "Nenhum investimento em reais foi compartilhado pelo Open Finance até agora.";
        return;
      }
      const groups = [
        { title: "Renda fixa", items: investments.filter(i => (i.type || "").toUpperCase() === "FIXED_INCOME") },
        { title: "Ações e FIIs", items: investments.filter(i => (i.type || "").toUpperCase() === "EQUITY") },
        { title: "Outros ativos", items: investments.filter(i => !["FIXED_INCOME", "EQUITY"].includes((i.type || "").toUpperCase())) },
      ].filter(group => group.items.length);
      const amount = value => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
      const sum = items => items.reduce((total, item) => total + Number(item.balance), 0);
      const total = sum(investments);
      const heading = document.createElement("div");
      heading.className = "piggy-portfolio-heading";
      const caption = document.createElement("span");
      caption.textContent = `${investments.length} ${investments.length === 1 ? "ativo" : "ativos"} · Open Finance`;
      const value = document.createElement("strong");
      value.textContent = amount(total);
      heading.append(caption, value);
      card.append(heading);

      const bar = document.createElement("div");
      bar.className = "piggy-portfolio-bar";
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", groups.map(group => `${group.title}: ${amount(sum(group.items))}`).join("; "));
      groups.forEach((group, index) => {
        const segment = document.createElement("span");
        segment.style.width = `${100 * sum(group.items) / total}%`;
        segment.className = `piggy-portfolio-segment piggy-portfolio-segment-${index}`;
        bar.append(segment);
      });
      card.append(bar);

      for (const group of groups) {
        const details = document.createElement("details");
        details.className = "piggy-portfolio-group";
        const summary = document.createElement("summary");
        const label = document.createElement("span");
        label.textContent = `${group.title} · ${group.items.length} ${group.items.length === 1 ? "ativo" : "ativos"}`;
        const groupValue = document.createElement("b");
        groupValue.textContent = amount(sum(group.items));
        summary.append(label, groupValue);
        details.append(summary);
        for (const item of group.items) {
          const row = document.createElement("div");
          row.className = "piggy-portfolio-row";
          const name = document.createElement("span");
          name.textContent = item.name || item.subtype || "Investimento";
          name.title = name.textContent;
          const institution = document.createElement("small");
          institution.textContent = item.institution_name || "Instituição não informada";
          name.append(institution);
          const balance = document.createElement("b");
          balance.textContent = amount(Number(item.balance));
          row.append(name, balance);
          details.append(row);
        }
        card.append(details);
      }
      const note = document.createElement("p");
      note.className = "piggy-portfolio-note";
      const hasNubankCdb = investments.some(i => /nubank|nu financeira/i.test(i.institution_name || "")
        && (i.subtype || "").toUpperCase() === "CDB");
      note.textContent = hasNubankCdb
        ? "Saldos da última sincronização. CDBs do Nubank podem incluir caixinhas; o Open Finance não identifica o apelido de cada uma."
        : "Saldos da última sincronização do Open Finance. A lista não inclui investimentos cadastrados manualmente.";
      card.append(note);
      const followups = document.createElement("div");
      followups.className = "piggy-portfolio-followups";
      for (const question of ["Quais são meus maiores CDBs?", "Como está minha renda variável?"]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.addEventListener("click", () => ask(question));
        followups.append(button);
      }
      card.append(followups);
    } catch (error) {
      card.textContent = "Não consegui carregar a carteira do Open Finance agora. Tente novamente mais tarde.";
      console.warn("[piggy] carteira Open Finance:", error);
    }
    body.scrollTop = body.scrollHeight;
  }

  window.piggyPortfolio = { applies: asksAboutPortfolio, render: renderPortfolioCard };
})();
