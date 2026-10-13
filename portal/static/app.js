/* local-repo — interactions du portail (sans dépendance). */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var CSRF = document.querySelector('meta[name="csrf"]') ? document.querySelector('meta[name="csrf"]').content : "";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------------- format */

  var UNITS = ["o", "Ko", "Mo", "Go", "To"];
  function bytesParts(n) {
    n = Number(n) || 0;
    var i = 0;
    while (n >= 1024 && i < UNITS.length - 1) { n /= 1024; i++; }
    var txt = i === 0 ? String(Math.round(n)) : n.toFixed(n < 10 ? 2 : 1).replace(".", ",");
    return [txt, UNITS[i]];
  }
  function fmtBytes(n) { var p = bytesParts(n); return p[0] + " " + p[1]; }
  window.lrBytes = fmtBytes;

  function render(el, value) {
    var fmt = el.dataset.fmt || "count";
    if (fmt === "bytes") {
      var p = bytesParts(value);
      if (el.classList.contains("value")) el.innerHTML = p[0] + '<span class="unit">' + p[1] + "</span>";
      else el.textContent = p[0] + " " + p[1];
    } else if (fmt === "pct") {
      el.textContent = Math.round(value) + " %";
    } else if (fmt === "ring") {
      el.style.setProperty("--p", Math.max(0, Math.min(100, value)));
    } else {
      el.textContent = Math.round(value).toLocaleString("fr-FR");
    }
  }

  function tween(el, to) {
    to = Number(to) || 0;
    var from = Number(el.dataset.current || 0);
    el.dataset.current = to;
    if (el.dataset.fmt === "ring") { render(el, to); return; }
    if (reduced || from === to) { render(el, to); return; }
    var start = null, dur = 900;
    function step(ts) {
      if (!start) start = ts;
      var t = Math.min(1, (ts - start) / dur);
      var e = 1 - Math.pow(1 - t, 3);
      render(el, from + (to - from) * e);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function get(obj, path) {
    return path.split(".").reduce(function (o, k) { return o == null ? undefined : o[k]; }, obj);
  }
  function bind(data) {
    $$("[data-bind]").forEach(function (el) {
      var v = get(data, el.dataset.bind);
      if (v === undefined) return;
      if (el.dataset.fmt === "service") {
        el.classList.toggle("ok", !!v);
        el.classList.toggle("err", !v);
        var label = $(".label", el);
        if (label) label.textContent = v ? "Actif" : "Arrêté";
        return;
      }
      if (el.dataset.fmt === "text") { el.textContent = v; return; }
      tween(el, v);
    });
  }
  window.lrBind = bind;

  /* ---------------------------------------------------------------- thème */

  var root = document.documentElement;
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(pref, animate) {
    var theme = pref === "auto" ? (mq.matches ? "oled" : "light") : pref;
    if (animate) {
      root.classList.add("theme-anim");
      setTimeout(function () { root.classList.remove("theme-anim"); }, 400);
    }
    root.dataset.theme = theme;
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.content = theme === "oled" ? "#000000" : "#ffffff";
    $$(".theme-switch").forEach(function (sw) {
      var btns = $$("button", sw);
      btns.forEach(function (b, i) {
        var on = b.dataset.theme === pref;
        b.setAttribute("aria-pressed", on);
        if (on) $(".thumb", sw).style.transform = "translateX(" + (i * 100) + "%)";
      });
    });
  }
  function themePref() { try { return localStorage.getItem("lr-theme") || "auto"; } catch (e) { return "auto"; } }
  applyTheme(themePref(), false);
  mq.addEventListener && mq.addEventListener("change", function () { if (themePref() === "auto") applyTheme("auto", true); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".theme-switch button");
    if (!b) return;
    try { localStorage.setItem("lr-theme", b.dataset.theme); } catch (err) { /* navigation privée */ }
    applyTheme(b.dataset.theme, true);
  });

  /* ---------------------------------------------------------------- notifications */

  var ICONS = { ok: "check", error: "alert", info: "info" };
  function toast(msg, kind) {
    kind = kind || "info";
    var box = $(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.appendChild(box); }
    var tint = kind === "ok" ? "t-green" : kind === "error" ? "t-rose" : "t-violet";
    var el = document.createElement("div");
    el.className = "toast " + tint;
    el.innerHTML = '<span class="ico"><svg class="i"><use href="#i-' + ICONS[kind] + '"/></svg></span><p></p><span class="life"></span>';
    $("p", el).textContent = msg;
    box.appendChild(el);
    var close = function () { el.classList.add("out"); setTimeout(function () { el.remove(); }, 350); };
    var timer = setTimeout(close, 5000);
    el.addEventListener("click", function () { clearTimeout(timer); close(); });
  }
  window.lrToast = toast;
  $$("[data-flash]").forEach(function (el, i) {
    setTimeout(function () { toast(el.dataset.flash, el.dataset.kind); }, 150 + i * 120);
  });

  /* ---------------------------------------------------------------- copie */

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    var ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
    return Promise.resolve();
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy");
    if (!btn) return;
    e.stopPropagation();
    var code = $("code", btn.closest(".term"));
    copyText(code.textContent.trim()).then(function () {
      btn.classList.add("done");
      var label = $(".lbl", btn);
      var old = label ? label.textContent : "";
      if (label) label.textContent = "Copié";
      setTimeout(function () { btn.classList.remove("done"); if (label) label.textContent = old; }, 1600);
    });
  });

  /* ---------------------------------------------------------------- onglets segmentés */

  document.addEventListener("click", function (e) {
    var b = e.target.closest(".seg [data-tab]");
    if (!b) return;
    var seg = b.closest(".seg");
    $$("[data-tab]", seg).forEach(function (x) { x.setAttribute("aria-selected", x === b); });
    var scope = seg.dataset.scope ? $(seg.dataset.scope) : seg.parentElement.parentElement;
    $$("[data-panel]", scope).forEach(function (p) { p.classList.toggle("on", p.dataset.panel === b.dataset.tab); });
    if (seg.dataset.target) {
      var t = $(seg.dataset.target);
      if (t) t.textContent = b.dataset.cmd;
    }
  });

  /* ---------------------------------------------------------------- confirmation */

  function confirmDialog(title, text, okLabel) {
    return new Promise(function (resolve) {
      var dlg = $("#confirm");
      $("h2", dlg).textContent = title;
      $(".dlg-body p", dlg).textContent = text || "";
      $("[data-ok]", dlg).textContent = okLabel || "Confirmer";
      var done = function (v) { dlg.close(); resolve(v); };
      $("[data-ok]", dlg).onclick = function () { done(true); };
      $("[data-cancel]", dlg).onclick = function () { done(false); };
      dlg.onclose = function () { resolve(false); };
      dlg.showModal();
    });
  }

  /* ---------------------------------------------------------------- formulaires */

  function send(form, btn) {
    var bar = form.querySelector(".upload-progress");
    var hasFile = $$('input[type="file"]', form).some(function (i) { return i.files && i.files.length; });
    if (btn) { btn.classList.add("loading"); btn.disabled = true; }
    var xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.responseType = "json";
    if (bar && hasFile) {
      bar.classList.add("on");
      var fill = $(".bar > span", bar), pct = $("[data-pct]", bar), size = $("[data-size]", bar);
      xhr.upload.onprogress = function (ev) {
        if (!ev.lengthComputable) return;
        var p = Math.round(100 * ev.loaded / ev.total);
        fill.style.width = p + "%";
        pct.textContent = p + " %";
        size.textContent = fmtBytes(ev.loaded) + " / " + fmtBytes(ev.total);
      };
    }
    xhr.onload = function () {
      var r = xhr.response;
      if (xhr.status < 400 && r && r.redirect) { window.location.href = r.redirect; return; }
      if (btn) { btn.classList.remove("loading"); btn.disabled = false; }
      toast(xhr.status === 400 ? "Session expirée : rechargez la page." : "Erreur " + xhr.status + " pendant l'envoi.", "error");
    };
    xhr.onerror = function () {
      if (btn) { btn.classList.remove("loading"); btn.disabled = false; }
      toast("Le serveur ne répond pas.", "error");
    };
    xhr.send(new FormData(form));
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var btn = e.submitter || $('[type="submit"]', form) || $("button:not([type])", form);
    if (form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      confirmDialog(form.dataset.confirm, form.dataset.confirmText, form.dataset.confirmOk).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmed = "1";
        if (form.hasAttribute("data-ajax")) send(form, btn); else form.submit();
      });
      return;
    }
    if (form.hasAttribute("data-ajax")) {
      e.preventDefault();
      send(form, btn);
    } else if (btn) {
      btn.classList.add("loading");
    }
  });

  /* ---------------------------------------------------------------- zone de dépôt */

  $$(".drop").forEach(function (drop) {
    var input = $('input[type="file"]', drop);
    var list = $(".files", drop);
    var show = function () {
      list.innerHTML = "";
      Array.prototype.forEach.call(input.files, function (f) {
        var s = document.createElement("span");
        s.textContent = f.name + " · " + fmtBytes(f.size);
        list.appendChild(s);
      });
      drop.classList.toggle("has-files", input.files.length > 0);
    };
    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function () { drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        show();
      }
    });
    input.addEventListener("change", show);
  });

  /* ---------------------------------------------------------------- puces (domaines) */

  $$(".chips").forEach(function (box) {
    var hidden = $("textarea", box.parentElement);
    var input = $("input", box);
    var values = hidden.value.split(/\s+/).filter(Boolean);
    var re = /^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$/;
    function sync() { hidden.value = values.join("\n"); }
    function draw() {
      $$(".chip", box).forEach(function (c) { c.remove(); });
      values.forEach(function (v, i) {
        var c = document.createElement("span");
        c.className = "chip";
        c.innerHTML = '<span class="w">*.</span><span></span><button type="button" aria-label="Retirer"><svg class="i i-sm"><use href="#i-x"/></svg></button>';
        c.children[1].textContent = v;
        c.lastChild.onclick = function () { values.splice(i, 1); sync(); draw(); };
        box.insertBefore(c, input);
      });
    }
    function add() {
      var v = input.value.trim().toLowerCase().replace(/^\*\./, "").replace(/\.$/, "");
      if (!v) return;
      if (!re.test(v)) { toast("Domaine invalide : " + v, "error"); return; }
      if (values.indexOf(v) < 0) values.push(v);
      input.value = ""; sync(); draw();
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === "," || e.key === " ") { e.preventDefault(); add(); }
      else if (e.key === "Backspace" && !input.value && values.length) { values.pop(); sync(); draw(); }
    });
    input.addEventListener("blur", add);
    box.addEventListener("click", function (e) { if (e.target === box) input.focus(); });
    draw();
  });

  /* ---------------------------------------------------------------- fenêtres */

  document.addEventListener("click", function (e) {
    var open = e.target.closest("[data-open]");
    if (open) {
      e.stopPropagation();
      var dlg = $(open.dataset.open);
      if (dlg.dataset.fill !== undefined && open.dataset.app) fillEdit(dlg, JSON.parse(open.dataset.app));
      dlg.showModal();
      var first = $("input:not([type=hidden]):not([type=file])", dlg);
      if (first) setTimeout(function () { first.focus(); }, 50);
      return;
    }
    var close = e.target.closest("[data-close]");
    if (close) { close.closest("dialog").close(); return; }
    if (e.target.tagName === "DIALOG") e.target.close();
  });

  function fillEdit(dlg, app) {
    var form = $("form", dlg);
    form.action = form.dataset.base.replace("__slug__", app.slug);
    $("[data-title]", dlg).textContent = app.name;
    ["name", "version", "args", "url", "kind"].forEach(function (k) {
      var el = form.elements[k];
      if (el) el.value = app[k] || "";
    });
    $("[data-file]", dlg).textContent = app.filename ? app.filename + " · SHA256 " + app.sha256.slice(0, 16) + "…" : "";
    var input = $('input[type="file"]', form);
    input.value = "";
    $(".files", form).innerHTML = "";
  }

  /* ---------------------------------------------------------------- recherche */

  $$("[data-filter]").forEach(function (input) {
    var items = $$(input.dataset.filter);
    var empty = input.dataset.empty ? $(input.dataset.empty) : null;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var shown = 0;
      items.forEach(function (it) {
        var ok = !q || it.textContent.toLowerCase().indexOf(q) >= 0;
        it.classList.toggle("hidden", !ok);
        if (ok) shown++;
      });
      if (empty) empty.classList.toggle("hidden", shown > 0);
    });
  });

  /* ---------------------------------------------------------------- sélection des logiciels */

  var selbar = $(".selbar");
  if (selbar) {
    var base = selbar.dataset.base;
    var picked = [];
    var refresh = function () {
      $(".count b", selbar).textContent = picked.length;
      $(".count span", selbar).textContent = picked.length > 1 ? "logiciels" : "logiciel";
      $("code", selbar).textContent = "irm '" + base + picked.join(",") + "' | iex";
      selbar.classList.toggle("on", picked.length > 0);
    };
    document.addEventListener("click", function (e) {
      var card = e.target.closest(".app-card");
      if (!card || card.classList.contains("disabled") || e.target.closest("button, a, form")) return;
      var slug = card.dataset.slug;
      var i = picked.indexOf(slug);
      if (i >= 0) picked.splice(i, 1); else picked.push(slug);
      card.classList.toggle("selected", i < 0);
      refresh();
    });
    $("[data-clear]", selbar).addEventListener("click", function () {
      picked = [];
      $$(".app-card.selected").forEach(function (c) { c.classList.remove("selected"); });
      refresh();
    });
  }

  /* ---------------------------------------------------------------- actualisation en direct */

  function poll(url, every, fn) {
    var busy = false;
    var tick = function () {
      if (document.hidden || busy) return;
      busy = true;
      fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { if (r.redirected || !r.ok) throw new Error(r.status); return r.json(); })
        .then(fn).catch(function () {}).then(function () { busy = false; });
    };
    setInterval(tick, every);
    return tick;
  }
  window.lrPoll = poll;

  // Valeurs initiales animées.
  requestAnimationFrame(function () {
    $$("[data-bind][data-value]").forEach(function (el) { tween(el, el.dataset.value); });
  });
})();
