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
      window.location.href = "/views/login.html";
      return;
    }
    state.me = me;
    state.tenant = me.scopedTenants[0] || "acme";
    renderIdentity(me);
    renderNav(me);
    wireTheme();
    showCrumb();
    loadView(currentView());
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
  var NAV_GLOBAL = [{ id: "tenants", label: "Tenants", icon: "▦" }];

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
      (view === "tenants" ? "" : "&tenant=" + state.tenant);
    window.history.pushState({}, "", next);
    loadView(view);
  }

  function loadView(view) {
    var frame = document.getElementById("stageFrame");
    var target = "/views/" + view + ".html";
    if (view !== "tenants") target += "?tenant=" + encodeURIComponent(state.tenant);
    frame.src = target;
    showCrumb(view);
  }

  function showCrumb(view) {
    var crumb = document.getElementById("crumb");
    var label = (view === "tenants") ? "Tenants" : state.tenant + " / " + view;
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
    window.location.href = "/views/login.html";
  });
  window.addEventListener("popstate", function () { loadView(currentView()); });
})();
