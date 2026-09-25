(function () {
  const STORAGE_KEY = "pb_sidenav_groups";

  function readStates() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (_) {
      return {};
    }
  }

  function group(name, root = document) {
    return root.querySelector(`.sidenav-group[data-group="${name}"]`);
  }

  function set(name, open, { persist = true, root = document } = {}) {
    const target = group(name, root);
    if (!target) return;
    target.classList.toggle("open", open);
    target.querySelector(".sidenav-group-toggle")?.setAttribute("aria-expanded", open ? "true" : "false");
    if (!persist) return;
    const states = readStates();
    states[name] = open;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(states)); } catch (_) {}
  }

  function toggle(name, root = document) {
    const target = group(name, root);
    if (target) set(name, !target.classList.contains("open"), { root });
  }

  function openForView(view, root = document) {
    const target = root.querySelector(`.sidenav-item[data-nav="${view}"]`)?.closest(".sidenav-group");
    if (target && !target.classList.contains("open")) set(target.dataset.group, true, { root });
  }

  function init(root = document) {
    const states = readStates();
    root.querySelectorAll(".sidenav-group").forEach((target) => {
      set(target.dataset.group, !!states[target.dataset.group], { persist: false, root });
    });
  }

  window.PigBankSidenavGroups = { init, openForView, set, toggle };
  window.toggleSidenavGroup = toggle;
})();
