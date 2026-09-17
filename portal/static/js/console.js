/* agent-orchestrator console — shell chrome (issue #39)
 * The shell is the super-admin/tenant frame host: rail navigation, tenant
 * switcher, dark-mode toggle. Each nav target loads a view *frame* that links
 * tokens.css + console.css independently (per-frame no-cascade). */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () { init(); });

  var state = { me: null, tenant: null };

  async function init() {
    var me;
    try {
      var payload = await CP.get("/api/console/me");
      me = payload.data;
    } catch (err) {
      window.location.href = "/auth/login";
      return;
    }
    state.me = me;
    state.tenant = me.scopedTenants[0] || "acme";
    renderIdentity(me);
    renderNav(me);
    wireTheme();
    showCrumb();
    loadView(currentView());
    offerErpModule();
  }

  var NAV_TENANT = [
    { id: "overview", label: "Overview", icon: "◈" },
    { id: "agents", label: "Agents", icon: "◉" },
    { id: "personas", label: "Personas", icon: "✦" },
    { id: "prompts", label: "Prompts", icon: "❯_" },
    { id: "policies", label: "Policies", icon: "⇄" },
    { id: "budgets", label: "Budgets", icon: "▤" },
    { id: "usage", label: "Usage", icon: "◔" },
    { id: "audit", label: "Audit", icon: "≡" },
    { id: "approvals", label: "Approvals", icon: "◷" }
  ];
  var NAV_GLOBAL = [
    { id: "tenants", label: "Tenants", icon: "▦" },
    // The operator terminal (issue #774) is the browser IT-terminal behind
    // this same SSO session — a live fleet projection plus the closed control-
    // verb steer panel. Like Tenants and Fleet it is an enterprise/org-level
    // view, so it takes no ?tenant= and its crumb is its own label.
    { id: "console", label: "Console", icon: "▣" },
    // The fleet single-pane-of-glass (issue #332) is an enterprise/org-level
    // view like Tenants: it is not scoped to one tenant, so it takes no
    // ?tenant= and its crumb is its own label.
    { id: "fleet", label: "Fleet", icon: "◍" }
  ];

  function isGlobalView(view) {
    return NAV_GLOBAL.some(function (item) { return item.id === view; });
  }

  /* A view whose frame is not under /views/ names its own target. The ERP module
   * (ERP-07, issue #652) is the first: its frame is a whole module directory
   * (/erp/module.html + its own script and stylesheet), so the shell must not
   * synthesise /views/erp.html for it. */
  var VIEW_TARGETS = {
    erp: "/erp/module.html"
  };

  /* The ERP module is a *gated* view: it is offered only when the module is
   * actually reachable. `GET /api/erp/module` is refused 404 `feature_disabled`
   * while its flag is off, so a probe that fails adds no nav entry — an
   * unpromoted module is absent from the shell, not a dead link in it. */
  async function offerErpModule() {
    try {
      var payload = await CP.get("/api/erp/module");
      if (!payload || !payload.data) return;
    } catch (err) {
      return;
    }
    var item = { id: "erp", label: "ERP", icon: "\u25a4" };
    var nav = document.getElementById("nav");
    var group = nav.querySelector("[data-group='erp']");
    if (!group) {
      group = CP.el("div", { class: "ng", "data-group": "erp", text: "Modules" });
      nav.appendChild(group);
    }
    nav.appendChild(navButton(item));
    window.ErpModule = { offered: true };
  }

  function currentView() {
    var value = new URLSearchParams(window.location.search).get("view");
    return value || (state.me.superAdmin ? "tenants" : "overview");
  }

  function renderIdentity(me) {
    document.getElementById("who").textContent = me.email;
    document.getElementById("role").textContent = me.superAdmin ? "super-admin" : me.role;
    var badge = document.getElementById("roleBadge");
    badge.className = "badge " + (me.superAdmin ? "blue" : "info");
  }

  function renderNav(me) {
    var nav = document.getElementById("nav");
    CP.clear(nav);
    if (me.superAdmin) {
      nav.appendChild(CP.el("div", { class: "ng", text: "Control plane" }));
      NAV_GLOBAL.forEach(function (item) { nav.appendChild(navButton(item)); });
      var tenantSwitcher = document.getElementById("tenantSwitcher");
      tenantSwitcher.style.display = "flex";
      tenantSwitcher.innerHTML = "";
      me.scopedTenants.forEach(function (tenantId) {
        tenantSwitcher.appendChild(CP.el("option", { value: tenantId, text: tenantId }));
      });
      if (state.tenant) tenantSwitcher.value = state.tenant;
      tenantSwitcher.addEventListener("change", function () {
        state.tenant = tenantSwitcher.value;
        loadView(currentView());
      });
    }
    nav.appendChild(CP.el("div", { class: "ng", text: "Organization" }));
    NAV_TENANT.forEach(function (item) { nav.appendChild(navButton(item)); });
  }

  function navButton(item) {
    var isCurrent = currentView() === item.id;
    return CP.el("button", {
      class: "nav-btn" + (isCurrent ? " active" : ""),
      "data-view": item.id,
      onclick: function () {
        document.querySelectorAll(".nav-btn").forEach(function (btn) {
          btn.classList.remove("active");
        });
        this.classList.add("active");
        navigate(item.id);
      }
    }, CP.el("span", { text: item.icon + "  " + item.label }));
  }

  function navigate(view) {
    var next = window.location.pathname + "?view=" + view +
      (isGlobalView(view) ? "" : "&tenant=" + state.tenant);
    window.history.pushState({}, "", next);
    loadView(view);
  }

  function loadView(view) {
    var frame = document.getElementById("stageFrame");
    var target = VIEW_TARGETS[view] || ("/views/" + view + ".html");
    if (!isGlobalView(view)) target += "?tenant=" + encodeURIComponent(state.tenant);
    frame.src = target;
    showCrumb(view);
  }

  function showCrumb(view) {
    var crumb = document.getElementById("crumb");
    var global = NAV_GLOBAL.filter(function (item) { return item.id === view; })[0];
    var label = global ? global.label
      : (VIEW_TARGETS[view] ? view : state.tenant + " / " + view);
    crumb.textContent = label;
  }

  function wireTheme() {
    var button = document.getElementById("themeToggle");
    var saved = "light";
    try { saved = localStorage.getItem("cp-theme") || "light"; } catch (e) { /* ignore */ }
    document.documentElement.setAttribute("data-theme", saved);
    button.textContent = saved === "dark" ? "☀ light" : "☾ dark";
    button.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      CP.applyTheme(next);
      document.documentElement.setAttribute("data-theme", next);
      button.textContent = next === "dark" ? "☀ light" : "☾ dark";
      var frame = document.getElementById("stageFrame");
      if (frame.contentWindow) {
        frame.contentWindow.postMessage({ type: "cp:theme", theme: next }, "*");
      }
    });
  }

  document.getElementById("logoutBtn").addEventListener("click", async function () {
    try { await CP.post("/api/console/logout", {}); } catch (e) { /* ignore */ }
    // Clear the gate's session by delegating to its authoritative navigation
    // logout (/auth/logout clears os_session + os_csrf + os-session-token).
    // Navigating to /views/login.html would loop back in: the gate re-issues a
    // fresh os-session-token from the still-valid os_session cookie.
    window.location.href = "/auth/logout";
  });
  window.addEventListener("popstate", function () { loadView(currentView()); });
})();
