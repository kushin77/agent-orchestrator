/* agent-orchestrator console — shell chrome (issue #39)
 * ---knowledge---
 * module_id: portal.static.js.console
 * system: portal
 * app: static
 * solution_class: pattern
 * patterns: [frame-host, per-frame-no-cascade]
 * derives_from: null
 * owner_sme: frontend-sme
 * tier: L0
 * interfaces: [portal/static/views/shell.html]
 * invariants: "each nav target loads an isolated view frame that links its own tokens.css + console.css"
 * gotchas: "an unauthenticated /api/console/me redirects the shell to /auth/login"
 * related: ["#39", "#1561"]
 * do_not_duplicate: null
 * ---knowledge---
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
    offerGatedViews();
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
    { id: "fleet", label: "Fleet", icon: "◍" },
    // The org chart (issue #642 workbook-11, view via #1521) renders the
    // workbook-1 declaration (registry/personas/org-chart.yaml) joined to the
    // workbook-6 role-health feed — a single org-wide declaration, not scoped
    // to one tenant, so it takes no ?tenant= and its crumb is its own label.
    { id: "orgchart", label: "Org Chart", icon: "◱" },
    // The cross-engine Sessions view (issue #1563) joins .fleet/, .board/ and
    // .deepseek-agent/ into one operator row set — org-wide, not scoped to
    // one tenant, so it takes no ?tenant= and its crumb is its own label.
    { id: "sessions", label: "Sessions", icon: "⌘" },
    // The skill studio (issue #642 workbook-11, view via #1521) is the
    // workbook-9 author → test → publish surface over the pack registry — an
    // org-level studio, not scoped to one tenant, so it takes no ?tenant= and
    // its crumb is its own label.
    { id: "skillstudio", label: "Skill Studio", icon: "⚒" },
    // The Settings view (issue #1757) renders the workbook settings
    // aggregator (portal/server/settings.py) — an org-wide, read-only join
    // of IaC-declared config, not scoped to one tenant, so it takes no
    // ?tenant= and its crumb is its own label.
    { id: "settings", label: "Settings", icon: "⚙" }
  ];

  function isGlobalView(view) {
    return allGlobalViews().some(function (item) { return item.id === view; });
  }

  /* Gated *org-wide* views (issue #1561). These are global in the same sense
   * NAV_GLOBAL is — the provider is not scoped to one tenant, so the frame
   * takes no ?tenant= and its crumb is its own label — but they are a second
   * list rather than rows of NAV_GLOBAL for one deliberate reason: NAV_GLOBAL
   * entries are the console's own control-plane views and are always present,
   * while a provider surface is offered only while its backend is actually
   * reachable. An unpromoted provider surface is ABSENT from the shell, not a
   * dead link in it — the same rule offerErpModule and offerGatedViews keep.
   * The Nous provider surface (issue #1561) is the first member: its probe is
   * /api/nous/overview, which answers 404 feature_disabled while
   * portal/config/feature-flags.yaml surfaces.nous is off. */
  var GATED_GLOBAL_VIEWS = [
    { id: "nous", label: "Nous", icon: "◐", probe: "/api/nous/overview" }
  ];

  /* Every view the shell treats as org-wide: the always-present ones plus the
   * gated ones. Both take no ?tenant= and both name their own crumb, so each
   * lookup below reads this one list rather than NAV_GLOBAL alone. */
  function allGlobalViews() {
    return NAV_GLOBAL.concat(GATED_GLOBAL_VIEWS);
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

  /* FinOps (issue #341) and Ops/SLO (issue #342) are *gated* tenant views
   * (issue #1520): each nav entry is offered only while its backend surface is
   * actually reachable. GET /api/finops/* and /api/ops/* are refused 404
   * feature_disabled while their flags are off (infra/feature-flags/registry.yaml
   * surfaces.finops_reports / surfaces.ops_health), so a probe that fails adds
   * no nav entry — an unpromoted surface is absent, not a dead link. */
  async function offerGatedViews() {
    var specs = [
      { id: "finops", label: "FinOps", icon: "\u25eb", probe: "/api/finops/overview" },
      { id: "ops", label: "Ops / SLO", icon: "\u25b7", probe: "/api/ops/overview" }
    ];
    var nav = document.getElementById("nav");
    for (var i = 0; i < specs.length; i++) {
      var spec = specs[i];
      try {
        var payload = await CP.get(spec.probe);
        if (!payload || !payload.data) continue;
      } catch (err) {
        continue;
      }
      nav.appendChild(navButton({ id: spec.id, label: spec.label, icon: spec.icon }));
    }
    /* The gated *org-wide* views (issue #1561). They join the "Control plane"
     * group and, like NAV_GLOBAL, are offered to super-admins only — an
     * org-level provider surface (cost, credits, account) is not tenant-scoped
     * and is not a tenant view. Same probe-and-hide rule as above. */
    if (!state.me.superAdmin) return;
    for (var j = 0; j < GATED_GLOBAL_VIEWS.length; j++) {
      var gspec = GATED_GLOBAL_VIEWS[j];
      try {
        var gpayload = await CP.get(gspec.probe);
        if (!gpayload || !gpayload.data) continue;
      } catch (err) {
        continue;
      }
      nav.appendChild(navButton({ id: gspec.id, label: gspec.label, icon: gspec.icon }));
    }
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
    var global = allGlobalViews().filter(function (item) { return item.id === view; })[0];
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
