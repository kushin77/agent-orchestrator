/* ERP module — the portal frame's client (ERP-07, issue #652).
 *
 * WHAT THIS FILE IS ALLOWED TO KNOW. Nothing about the ERP module's domain. The
 * family vocabulary, each family's states and legal moves, and the fields a form
 * offers are all read from `GET /api/erp/module` — which is the manifest plus
 * ERP-06's own served contract — and every document is read or written through
 * `/api/erp/documents/...`, which the server proxies verbatim to the ERP-06
 * surface. No family name, no state name and no field name is typed here: a copy
 * of any of them would be the second declaration the module forbids.
 *
 * The frame is served only while the module's flag is on: with `erp_module` off
 * the app answers 404 `feature_disabled` for this file and the API alike, so a
 * request from here can only ever be made against a promoted module.
 */
(function () {
  "use strict";

  var state = {
    module: null,
    contract: null,
    dashboard: null,
    documents: [],
    selected: null,
    kind: "",
    filter: "",
    report: "inventory"
  };

  function $(id) { return document.getElementById(id); }

  function note(text, kind) {
    var node = $("status");
    node.textContent = text;
    node.setAttribute("data-state", kind || "info");
  }

  /* The console's CP helper covers GET/POST; a replace is a PUT and this frame
   * needs one, so the two extra verbs are sent through the same shape rather
   * than widening the shared script for one caller. */
  function send(method, path, body) {
    var init = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) init.body = JSON.stringify(body);
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (json) {
        return { status: response.status, ok: response.ok, body: json };
      });
    });
  }

  /* The refusal a mutation earned, named the way ERP-06 named it: its code, its
   * status and its message, never a generic failure. */
  function refusal(result) {
    var error = (result.body && result.body.error) || {};
    return (error.code || ("http_" + result.status)) + " — " +
      (error.message || "the ERP surface refused the request") +
      " (status " + result.status + ")";
  }

  function resolveRef(ref) {
    if (!state.contract || !ref) return null;
    var prefix = "#/components/schemas/";
    if (String(ref).indexOf(prefix) !== 0) return null;
    var path = String(ref).slice(prefix.length).split("/");
    var node = { components: state.contract.components };
    for (var index = 0; index < path.length; index += 1) {
      if (!node || typeof node !== "object") return null;
      node = node[path[index]];
    }
    return node || null;
  }

  function familySchema(kind) {
    var schemas = (state.contract && state.contract.components &&
      state.contract.components.schemas) || {};
    return schemas[kind] || null;
  }

  function familyFields(kind) {
    var schema = familySchema(kind);
    if (!schema || !schema.properties) return [];
    var required = schema.required || [];
    var names = Object.keys(schema.properties).sort();
    return names.map(function (name) {
      var property = schema.properties[name] || {};
      var resolved = property.$ref ? resolveRef(property.$ref) : property;
      resolved = resolved || {};
      return {
        name: name,
        type: resolved.type || property.type || "string",
        format: resolved.format || property.format || "",
        description: property.description || resolved.description || "",
        required: required.indexOf(name) !== -1,
        array: (resolved.type || property.type) === "array"
      };
    });
  }

  function legalMoves(kind, stateName) {
    var transitions = (state.contract && state.contract["x-erp-transitions"]) || {};
    var moves = (transitions[kind] || {})[stateName];
    return moves || [];
  }

  function el(tag, attrs, children) {
    return CP.el.apply(null, [tag, attrs].concat(children || []));
  }

  function row(cells, attrs) {
    var node = el("tr", attrs, cells.map(function (cell) {
      return el("td", cell.attrs || {}, cell.children || [String(cell.text == null ? "" : cell.text)]);
    }));
    return node;
  }

  /* -- the dashboard ------------------------------------------------------ */

  function renderFlag(module) {
    var flag = (module && module.flag) || {};
    var badge = $("erpFlag");
    badge.textContent = "flag: " + (flag.declared || "unknown");
    badge.setAttribute("data-state", flag.declared || "unknown");
    badge.setAttribute("data-surface", flag.surface || "");
    $("erpSource").textContent = (module && module.declarationSource) || "";
    $("erpModuleId").textContent = (module && module.module && module.module.id) || "—";
    $("erpModuleId").setAttribute("data-module",
      (module && module.module && module.module.id) || "");
    $("erpTitle").textContent = (module && module.module && module.module.name)
      ? module.module.name + " module" : "ERP module";
  }

  function renderDashboard(dashboard) {
    var body = $("familyRows");
    CP.clear(body);
    var families = dashboard.families || [];
    families.forEach(function (family) {
      var states = Object.keys(family.byState || {}).sort().map(function (name) {
        return name + " " + family.byState[name];
      });
      if (family.stateless) states.push(family.stateless + " stateless");
      body.appendChild(row([
        { text: family.kind, attrs: { class: "erp-mono" } },
        { text: String(family.documents) },
        { text: family.lifecycle ? "yes" : "no" },
        { text: states.join(", ") || "—" },
        { text: (family.redactedFields || []).join(", ") || "—" }
      ], {
        "data-row": "family",
        "data-kind": family.kind,
        "data-documents": String(family.documents),
        "data-lifecycle": family.lifecycle ? "yes" : "no",
        "data-redacted": (family.redactedFields || []).join(",")
      }));
    });
    var totals = dashboard.totals || {};
    var summary = $("dashTotals");
    summary.textContent = (totals.families || 0) + " families · " + (totals.documents || 0) +
      " documents · states " + JSON.stringify(totals.byState || {}) +
      ((totals.stateless || 0) ? " · " + totals.stateless + " stateless" : "");
    summary.setAttribute("data-documents", String(totals.documents || 0));
    summary.setAttribute("data-families", String(totals.families || 0));
  }

  /* -- the family selectors ---------------------------------------------- */

  function renderKindOptions() {
    var model = (state.contract && state.contract["x-erp-model"]) || {};
    var kinds = model.kinds || [];
    ["docKind"].forEach(function (id) {
      var select = $(id);
      CP.clear(select);
      kinds.forEach(function (kind) {
        select.appendChild(el("option", { value: kind, text: kind }));
      });
      if (kinds.indexOf(state.kind) !== -1) select.value = state.kind;
    });
  }

  function renderStateOptions() {
    var select = $("docState");
    CP.clear(select);
    select.appendChild(el("option", { value: "", text: "all states" }));
    var family = (state.dashboard && state.dashboard.families || []).filter(function (item) {
      return item.kind === state.kind;
    })[0];
    ((family && family.states) || []).forEach(function (name) {
      select.appendChild(el("option", { value: name, text: name }));
    });
    select.value = "";
  }

  /* -- documents ---------------------------------------------------------- */

  function filtersKey() {
    return (state.kind || "") + "|" + (state.filter || "");
  }

  function loadDocuments() {
    if (!state.kind) return Promise.resolve();
    state.selected = null;
    return CP.get("/api/erp/documents/" + encodeURIComponent(state.kind))
      .then(function (payload) {
        if (payload.ok === false) {
          throw new Error((payload.error && payload.error.code) || "refused");
        }
        state.documents = (payload.data && payload.data.items) || [];
        state.redacted = (payload.data && payload.data.redacted) || [];
        renderDocuments();
      });
  }

  function matches(document) {
    if (!state.filter) return true;
    var needle = state.filter.toLowerCase();
    return JSON.stringify(document).toLowerCase().indexOf(needle) !== -1;
  }

  function renderDocuments() {
    var body = $("docRows");
    CP.clear(body);
    var wanted = $("docState").value;
    var visible = 0;
    state.documents.forEach(function (document) {
      if (wanted && document.state !== wanted) return;
      if (!matches(document)) return;
      visible += 1;
      var moves = legalMoves(state.kind, document.state || "");
      var buttons = moves.map(function (move) {
        var button = el("button", {
          class: "erp-move", type: "button", text: move,
          "data-move": move,
          "data-doc": document.id
        }, []);
        button.addEventListener("click", function () { transition(document.id, move); });
        return button;
      });
      var load = el("button", {
        class: "erp-move", type: "button", text: "load", "data-load": document.id
      }, []);
      load.addEventListener("click", function () { fillForm(document); });
      body.appendChild(row([
        { text: document.id, attrs: { class: "erp-mono" } },
        { text: state.kind },
        { text: document.state || "—" },
        { children: buttons.concat([load]) }
      ], {
        "data-row": "document",
        "data-doc": document.id,
        "data-kind": state.kind,
        "data-state": document.state || ""
      }));
    });
    var count = $("docCount");
    count.textContent = visible + " of " + state.documents.length + " " + state.kind +
      " documents rendered" +
      ((state.redacted || []).length
        ? " · fields withheld by policy: " + state.redacted.join(", ") : "");
    count.setAttribute("data-count", String(state.documents.length));
    count.setAttribute("data-visible", String(visible));
    if (!visible) {
      body.appendChild(row([{ children: [el("span", {
        class: "erp-empty", text: "no document of this family matches the filters"
      }, [])], attrs: { colspan: "4" } }], { "data-row": "empty" }));
    }
  }

  /* -- the form ----------------------------------------------------------- */

  function fieldInput(field) {
    if (field.array) {
      return el("textarea", {
        id: "field-" + field.name, rows: "3", "data-field": field.name,
        "data-type": "array", placeholder: "one JSON value per line"
      }, []);
    }
    if (field.type === "boolean") {
      var select = el("select", { id: "field-" + field.name, "data-field": field.name, "data-type": "boolean" }, []);
      ["", "true", "false"].forEach(function (value) {
        select.appendChild(el("option", { value: value, text: value || "—" }));
      });
      return select;
    }
    return el("input", {
      id: "field-" + field.name, type: "text", "data-field": field.name,
      "data-type": field.type || "string",
      placeholder: field.format || field.type || "string"
    }, []);
  }

  function renderForm() {
    var fields = $("formFields");
    CP.clear(fields);
    familyFields(state.kind).forEach(function (field) {
      fields.appendChild(el("div", { class: "erp-field", "data-field-row": field.name }, [
        el("label", { for: "field-" + field.name }, [
          field.name + " ",
          el("span", { class: "erp-mono", text: field.type + (field.required ? " *" : "") }, [])
        ]),
        fieldInput(field),
        field.description ? el("span", { class: "erp-help", text: field.description }, []) : null
      ]));
    });
  }

  function fillForm(document) {
    state.selected = document;
    $("docForm").setAttribute("data-mode", "replace");
    $("docForm").setAttribute("data-doc", document.id);
    $("formTitle").textContent = "Replace " + document.id;
    $("formSubmit").textContent = "replace";
    Object.keys(document).forEach(function (name) {
      var input = $("field-" + name);
      if (!input) return;
      var value = document[name];
      input.value = input.getAttribute("data-type") === "array"
        ? JSON.stringify(value) : String(value);
    });
    $("formResult").textContent = "loaded " + document.id + " — " +
      "replace re-validates the whole document against its family schema";
    $("formResult").setAttribute("data-state", "info");
  }

  function resetForm() {
    state.selected = null;
    $("docForm").setAttribute("data-mode", "create");
    $("docForm").removeAttribute("data-doc");
    $("formTitle").textContent = "New document";
    $("formSubmit").textContent = "create";
    Array.prototype.forEach.call($("formFields").querySelectorAll("[data-field]"), function (input) {
      input.value = "";
    });
    $("formResult").setAttribute("data-state", "idle");
    $("formResult").textContent = "the form's fields are the family's own schema, read from the ERP-06 contract";
  }

  function formBody() {
    var body = {};
    familyFields(state.kind).forEach(function (field) {
      var input = $("field-" + field.name);
      if (!input) return;
      var raw = input.value;
      if (raw === "" || raw == null) return;
      if (field.array) {
        try { body[field.name] = JSON.parse(raw); }
        catch (error) { body[field.name] = raw.split("\n").map(function (line) { return JSON.parse(line); }); }
      } else if (field.type === "boolean") {
        body[field.name] = raw === "true";
      } else if (field.type === "integer" || field.type === "number") {
        body[field.name] = Number(raw);
      } else {
        body[field.name] = raw;
      }
    });
    return body;
  }

  function submitForm(event) {
    event.preventDefault();
    var result = $("formResult");
    if (!state.kind) return;
    var mode = $("docForm").getAttribute("data-mode");
    var id = $("docForm").getAttribute("data-doc");
    var path = mode === "replace"
      ? "/api/erp/documents/" + encodeURIComponent(state.kind) + "/" + encodeURIComponent(id)
      : "/api/erp/documents/" + encodeURIComponent(state.kind);
    var body = formBody();
    send(mode === "replace" ? "PUT" : "POST", path, body).then(function (answer) {
      if (answer.ok) {
        result.textContent = "accepted: " + state.kind + " " + ((answer.body.data || {}).document || {}).id;
        result.setAttribute("data-state", "ok");
        state.selected = null;
        return loadDocuments();
      }
      result.textContent = "refused: " + refusal(answer);
      result.setAttribute("data-state", "refused");
    }).catch(function (error) {
      result.textContent = "refused: " + error.message;
      result.setAttribute("data-state", "refused");
    });
  }

  function transition(id, move) {
    var result = $("formResult");
    var path = "/api/erp/documents/" + encodeURIComponent(state.kind) + "/" +
      encodeURIComponent(id) + "/transitions/" + encodeURIComponent(move);
    send("POST", path, undefined).then(function (answer) {
      if (answer.ok) {
        var reached = ((answer.body.data || {}).transition || {}).state || "";
        result.textContent = "moved " + id + " by " + move + " → " + reached;
        result.setAttribute("data-state", "ok");
        return loadDocuments();
      }
      result.textContent = "refused: " + refusal(answer);
      result.setAttribute("data-state", "refused");
    });
  }

  /* -- reports ------------------------------------------------------------ */

  function renderReportOptions() {
    var select = $("reportPick");
    CP.clear(select);
    return CP.get("/api/erp/reports").then(function (payload) {
      ((payload.data || {}).reports || []).forEach(function (report) {
        select.appendChild(el("option", { value: report.id, text: report.title || report.id }));
      });
    });
  }

  function loadReport() {
    state.report = $("reportPick").value || "inventory";
    return CP.get("/api/erp/reports/" + encodeURIComponent(state.report)).then(function (payload) {
      var report = payload.data || {};
      renderReport(report);
    });
  }

  function renderReport(report) {
    var head = $("reportHead");
    var body = $("reportBody");
    CP.clear(head);
    CP.clear(body);
    if (report.report === "inventory") {
      head.appendChild(row([
        { text: "Family" }, { text: "Documents" }
      ], {}));
      (report.rows || []).forEach(function (entry) {
        body.appendChild(row([
          { text: entry.kind, attrs: { class: "erp-mono" } },
          { text: String(entry.documents) }
        ], { "data-row": "report-inventory", "data-kind": entry.kind }));
      });
      return;
    }
    if (report.report === "lifecycle") {
      head.appendChild(row([
        { text: "Family" }, { text: "States" }, { text: "Moves" }, { text: "Tally" }
      ], {}));
      (report.rows || []).forEach(function (entry) {
        body.appendChild(row([
          { text: entry.kind, attrs: { class: "erp-mono" } },
          { text: (entry.states || []).join(", ") },
          { text: JSON.stringify(entry.transitions || {}) },
          { text: JSON.stringify(entry.byState || {}) }
        ], { "data-row": "report-lifecycle", "data-kind": entry.kind }));
      });
      return;
    }
    head.appendChild(row([
      { text: "Schema source" }
    ], {}));
    var sources = report.schemaSources || {};
    Object.keys(sources).sort().forEach(function (name) {
      body.appendChild(row([
        { text: name + " → " + sources[name], attrs: { class: "erp-mono" } }
      ], { "data-row": "report-contract", "data-kind": name }));
    });
  }

  /* -- boot --------------------------------------------------------------- */

  function loadAll() {
    note("loading the module declaration…", "loading");
    return CP.get("/api/erp/module").then(function (payload) {
      state.module = payload.data;
      renderFlag(state.module);
      /* The contract itself, not a summary of it: the form's fields and the
       * refs they carry live in the document ERP-06 serves, so the frame reads
       * that document rather than a copy of any part of it. */
      var declared = (state.module && state.module.contract) || {};
      var contractPath = declared.path || "/v1/erp/openapi.json";
      return CP.get(contractPath.replace(/^\/v1\/erp/, "/api/erp"));
    }).then(function (payload) {
      state.contract = payload.data;
      if (!state.kind) {
        var kinds = ((state.contract || {})["x-erp-model"] || {}).kinds || [];
        state.kind = kinds[0] || "";
      }
      renderKindOptions();
      return CP.get("/api/erp/dashboard");
    }).then(function (payload) {
      state.dashboard = payload.data;
      renderDashboard(state.dashboard);
      renderStateOptions();
      return renderReportOptions();
    }).then(function () {
      return loadReport();
    }).then(function () {
      return loadDocuments();
    }).then(function () {
      renderForm();
      note("the module is promoted; every datum above is read from the ERP-06 contract",
        "ready");
    }).catch(function (error) {
      note("the module could not be loaded: " + error.message +
        (error.status === 404 ? " — an unpromoted module is absent, not merely unauthorised" : ""),
        "refused");
      throw error;
    });
  }

  function init() {
    $("erpReload").addEventListener("click", function () { loadAll(); });
    $("formReset").addEventListener("click", function () { resetForm(); });
    $("docForm").addEventListener("submit", submitForm);
    $("docKind").addEventListener("change", function () {
      state.kind = $("docKind").value;
      renderStateOptions();
      renderForm();
      resetForm();
      loadDocuments();
    });
    $("docState").addEventListener("change", function () { renderDocuments(); });
    $("docFilter").addEventListener("input", function () {
      state.filter = $("docFilter").value;
      renderDocuments();
    });
    $("reportPick").addEventListener("change", function () { loadReport(); });
    loadAll().catch(function () { /* the note above already says what happened */ });
  }

  document.addEventListener("DOMContentLoaded", init);
  window.ErpModule = { state: state };
})();
