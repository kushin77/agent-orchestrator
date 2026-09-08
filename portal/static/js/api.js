/* agent-orchestrator console — shared frame API (issue #39)
 * Every view frame is an isolated document: it links tokens.css + console.css
 * itself and applies its own theme (no cross-frame cascade reliance). This
 * helper supplies fetch/json helpers, tiny DOM builder, formatting, and the
 * same-origin theme hook (localStorage + postMessage from the shell). */
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var tenant = params.get("tenant") || "";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
    try { localStorage.setItem("cp-theme", theme === "dark" ? "dark" : "light"); } catch (e) { /* ignore */ }
  }
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("cp-theme"); } catch (e) { /* ignore */ }
    applyTheme(saved || "light");
  }
  window.addEventListener("message", function (event) {
    if (event.data && event.data.type === "cp:theme") applyTheme(event.data.theme);
  });

  async function request(path, opts) {
    opts = opts || {};
    var init = {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: { "Content-Type": "application/json" }
    };
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
    var response = await fetch(path, init);
    var json = null;
    try { json = await response.json(); } catch (e) { /* non-json */ }
    if (!response.ok) {
      var message = (json && json.error && json.error.message) || ("HTTP " + response.status);
      var err = new Error(message);
      err.status = response.status;
      err.code = json && json.error && json.error.code;
      throw err;
    }
    return json;
  }

  function get(path) { return request(path); }
  function post(path, body) { return request(path, { method: "POST", body: body || {} }); }

  function el(tag, attrs) {
    var children = Array.prototype.slice.call(arguments, 2);
    var node = document.createElement(tag);
    attrs = attrs || {};
    for (var key in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, key)) continue;
      var value = attrs[key];
      if (value == null) continue;
      if (key === "class") node.className = value;
      else if (key === "html") node.innerHTML = value;
      else if (key === "text") node.textContent = value;
      else if (key.indexOf("on") === 0) node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, key);
      else node.setAttribute(key, value);
    }
    children.forEach(function (child) {
      if (child == null || child === false) return;
      node.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function money(value) {
    return "$" + Number(value || 0).toLocaleString("en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function num(value) { return Number(value || 0).toLocaleString("en-US"); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  window.CP = {
    tenant: tenant, params: params,
    get: get, post: post, el: el, clear: clear,
    money: money, num: num, esc: esc,
    initTheme: initTheme, applyTheme: applyTheme
  };
  CP.initTheme();
})();
