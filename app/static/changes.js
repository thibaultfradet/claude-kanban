// Ticket "source control" page: changed files on the left, a read-only Monaco
// diff on the right, re-polled every 3 s while the agent is running.
(function () {
  var CONFIG = window.KANBAN_CHANGES;
  var MONACO_BASE = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/";
  var POLL_MS = 3000;
  var api = "/api/tickets/" + CONFIG.ticketId + "/changes";

  var els = {
    badge: document.querySelector("[data-status-badge]"),
    range: document.querySelector("[data-range]"),
    live: document.querySelector("[data-live]"),
    count: document.querySelector("[data-count]"),
    files: document.querySelector("[data-files]"),
    head: document.querySelector("[data-file-head]"),
    editor: document.querySelector("[data-editor]"),
    message: document.querySelector("[data-message]"),
    sidebar: document.querySelector("[data-sidebar]"),
    resizer: document.querySelector("[data-resizer]"),
  };

  var state = {
    files: [],
    selected: null,
    live: false,
    polling: true,
    timer: null,
    fileToken: 0,
    sideBySide: localStorage.getItem("kanban.changes.view") !== "inline",
  };

  var monacoReady = null;
  var diffEditor = null;
  var shownPath = null;

  // ---------- Monaco ----------

  window.MonacoEnvironment = {
    getWorkerUrl: function () {
      var src =
        "self.MonacoEnvironment={baseUrl:'" + MONACO_BASE + "'};" +
        "importScripts('" + MONACO_BASE + "vs/base/worker/workerMain.js');";
      return "data:text/javascript;charset=utf-8," + encodeURIComponent(src);
    },
  };

  var darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function theme() {
    return darkQuery.matches ? "vs-dark" : "vs";
  }

  function loadMonaco() {
    if (monacoReady) return monacoReady;
    monacoReady = new Promise(function (resolve, reject) {
      if (!window.require) return reject(new Error("loader Monaco indisponible"));
      window.require.config({ paths: { vs: MONACO_BASE + "vs" } });
      window.require(["vs/editor/editor.main"], function () {
        diffEditor = monaco.editor.createDiffEditor(els.editor, {
          readOnly: true,
          originalEditable: false,
          renderSideBySide: state.sideBySide,
          automaticLayout: true,
          theme: theme(),
          fontFamily: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 13,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          renderOverviewRuler: true,
          hideUnchangedRegions: { enabled: false },
        });
        darkQuery.addEventListener("change", function () {
          monaco.editor.setTheme(theme());
        });
        resolve(monaco);
      }, reject);
    });
    return monacoReady;
  }

  var languageByExt = null;

  function languageFor(path) {
    if (!languageByExt) {
      languageByExt = { ext: {}, name: {} };
      monaco.languages.getLanguages().forEach(function (lang) {
        (lang.extensions || []).forEach(function (ext) {
          var key = ext.toLowerCase();
          if (!languageByExt.ext[key]) languageByExt.ext[key] = lang.id;
        });
        (lang.filenames || []).forEach(function (name) {
          languageByExt.name[name.toLowerCase()] = lang.id;
        });
      });
    }
    var name = path.split("/").pop().toLowerCase();
    if (languageByExt.name[name]) return languageByExt.name[name];
    var dot = name.lastIndexOf(".");
    return (dot >= 0 && languageByExt.ext[name.slice(dot)]) || "plaintext";
  }

  // ---------- rendering ----------

  function shortSha(sha) {
    return sha ? sha.slice(0, 7) : "";
  }

  function showMessage(text, sub) {
    els.editor.hidden = true;
    els.message.hidden = false;
    els.message.innerHTML = "";
    var strong = document.createElement("strong");
    strong.textContent = text;
    els.message.appendChild(strong);
    if (sub) {
      var p = document.createElement("span");
      p.textContent = sub;
      els.message.appendChild(p);
    }
  }

  function renderHeader(data) {
    var label = CONFIG.statusLabels[data.status] || data.status;
    els.badge.textContent = label;
    els.badge.style.setProperty("--c", "var(--status-" + data.status + ")");
    els.badge.style.setProperty("--c-soft", "var(--status-" + data.status + "-soft)");
    els.live.hidden = !data.live;
    if (data.base) {
      els.range.innerHTML = "";
      els.range.append("base ");
      var b = document.createElement("code");
      b.textContent = shortSha(data.base);
      els.range.append(b, " → ");
      if (data.target === "WORKTREE") {
        els.range.append("working tree");
      } else {
        var t = document.createElement("code");
        t.textContent = shortSha(data.target);
        els.range.append(t);
      }
      els.range.hidden = false;
    } else {
      els.range.hidden = true;
    }
  }

  function splitPath(path) {
    var i = path.lastIndexOf("/");
    return i < 0 ? { name: path, dir: "" } : { name: path.slice(i + 1), dir: path.slice(0, i) };
  }

  var STATUS_TITLES = { A: "Ajouté", M: "Modifié", D: "Supprimé", R: "Renommé" };

  function renderList() {
    els.count.textContent = String(state.files.length);
    els.files.innerHTML = "";
    state.files.forEach(function (file) {
      var parts = splitPath(file.path);
      var li = document.createElement("li");
      li.className = "sc-file sc-file-" + file.status;
      li.setAttribute("role", "option");
      li.dataset.path = file.path;
      li.title = file.old_path ? file.old_path + " → " + file.path : file.path;
      if (file.path === state.selected) {
        li.classList.add("is-selected");
        li.setAttribute("aria-selected", "true");
      }

      var name = document.createElement("span");
      name.className = "sc-file-name";
      name.textContent = parts.name;
      var dir = document.createElement("span");
      dir.className = "sc-file-dir";
      dir.textContent = parts.dir;
      var stats = document.createElement("span");
      stats.className = "sc-file-stats";
      if (file.additions) {
        var add = document.createElement("span");
        add.className = "sc-add";
        add.textContent = "+" + file.additions;
        stats.appendChild(add);
      }
      if (file.deletions) {
        var del = document.createElement("span");
        del.className = "sc-del";
        del.textContent = "−" + file.deletions;
        stats.appendChild(del);
      }
      var letter = document.createElement("span");
      letter.className = "sc-file-status";
      letter.textContent = file.status;
      letter.title = STATUS_TITLES[file.status] || file.status;

      li.append(name, dir, stats, letter);
      li.addEventListener("click", function () {
        select(file.path);
      });
      els.files.appendChild(li);
    });
  }

  function renderListEmpty(text, sub) {
    els.count.textContent = "0";
    els.files.innerHTML = "";
    var li = document.createElement("li");
    li.className = "sc-files-empty";
    var strong = document.createElement("strong");
    strong.textContent = text;
    li.appendChild(strong);
    if (sub) {
      var span = document.createElement("span");
      span.textContent = sub;
      li.appendChild(span);
    }
    els.files.appendChild(li);
  }

  function renderFileHead(file) {
    var parts = splitPath(file.path);
    els.head.hidden = false;
    els.head.innerHTML = "";
    var letter = document.createElement("span");
    letter.className = "sc-file-status sc-file-" + file.status;
    letter.textContent = file.status;
    var name = document.createElement("span");
    name.className = "sc-file-name";
    name.textContent = parts.name;
    var dir = document.createElement("span");
    dir.className = "sc-file-dir";
    dir.textContent = file.old_path ? file.old_path + " → " + file.path : parts.dir;
    els.head.append(letter, name, dir);
  }

  // ---------- data ----------

  function getJSON(url) {
    return fetch(url).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.detail || "Erreur " + r.status);
        return data;
      });
    });
  }

  function findFile(path) {
    for (var i = 0; i < state.files.length; i++) {
      if (state.files[i].path === path) return state.files[i];
    }
    return null;
  }

  function select(path) {
    if (path === state.selected) return;
    state.selected = path;
    els.files.querySelectorAll(".sc-file").forEach(function (li) {
      var on = li.dataset.path === path;
      li.classList.toggle("is-selected", on);
      if (on) {
        li.setAttribute("aria-selected", "true");
        li.scrollIntoView({ block: "nearest" });
      } else {
        li.removeAttribute("aria-selected");
      }
    });
    loadFile(path);
  }

  function loadFile(path) {
    var file = findFile(path);
    if (!file) return;
    var token = ++state.fileToken;
    renderFileHead(file);
    Promise.all([getJSON(api + "/file?path=" + encodeURIComponent(path)), loadMonaco()])
      .then(function (res) {
        if (token !== state.fileToken) return;
        var data = res[0];
        if (data.binary) return showMessage("Fichier binaire", "Le contenu ne peut pas être affiché.");
        if (data.too_large) return showMessage("Fichier trop volumineux", "Plus de 1 Mo : le diff n'est pas affiché.");

        var language = languageFor(data.path);
        var sameFile = shownPath === data.path;
        var viewState = sameFile ? diffEditor.saveViewState() : null;
        var previous = diffEditor.getModel();
        diffEditor.setModel({
          original: monaco.editor.createModel(data.original, language),
          modified: monaco.editor.createModel(data.modified, language),
        });
        if (previous) {
          previous.original.dispose();
          previous.modified.dispose();
        }
        if (viewState) diffEditor.restoreViewState(viewState);
        shownPath = data.path;
        els.message.hidden = true;
        els.editor.hidden = false;
      })
      .catch(function (err) {
        if (token !== state.fileToken) return;
        showMessage("Impossible d'afficher ce fichier", err.message);
      });
  }

  function refresh() {
    return getJSON(api)
      .then(function (data) {
        var wasLive = state.live;
        state.live = data.live;
        renderHeader(data);

        if (!data.base) {
          state.files = [];
          state.selected = null;
          els.head.hidden = true;
          renderListEmpty("Ce ticket n'a pas encore démarré", "Les changements apparaîtront dès que l'agent commencera.");
          showMessage("Ce ticket n'a pas encore démarré", "Aucun changement à afficher pour le moment.");
          return { wasLive: wasLive, live: data.live };
        }

        var before = {};
        state.files.forEach(function (f) {
          before[f.path] = f.additions + ":" + f.deletions + ":" + f.status;
        });
        state.files = data.files;

        if (!data.files.length) {
          state.selected = null;
          els.head.hidden = true;
          renderListEmpty("Aucun changement", data.live ? "L'agent n'a encore modifié aucun fichier." : "Ce ticket n'a modifié aucun fichier.");
          showMessage("Aucun changement", "Rien à comparer depuis le début du ticket.");
          return { wasLive: wasLive, live: data.live };
        }

        if (!state.selected || !findFile(state.selected)) {
          state.selected = data.files[0].path;
          renderList();
          loadFile(state.selected);
        } else {
          renderList();
          var cur = findFile(state.selected);
          if (before[cur.path] !== cur.additions + ":" + cur.deletions + ":" + cur.status) {
            loadFile(cur.path);
          }
        }
        return { wasLive: wasLive, live: data.live };
      })
      .catch(function (err) {
        renderListEmpty("Erreur", err.message);
        showMessage("Impossible de lire les changements", err.message);
        return { wasLive: state.live, live: state.live };
      });
  }

  // ---------- live polling ----------

  function schedule() {
    clearTimeout(state.timer);
    state.timer = null;
    if (!state.polling || document.hidden) return;
    state.timer = setTimeout(tick, POLL_MS);
  }

  function tick() {
    state.timer = null;
    refresh().then(function (r) {
      if (!r.live) {
        // Agent just stopped: one last refresh to catch its final writes, then stop.
        state.polling = false;
        if (r.wasLive) setTimeout(refresh, POLL_MS);
        return;
      }
      schedule();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimeout(state.timer);
      state.timer = null;
    } else if (state.polling && !state.timer) {
      tick();
    }
  });

  // ---------- controls ----------

  function applyView() {
    document.querySelectorAll("[data-view]").forEach(function (btn) {
      var on = (btn.dataset.view === "side") === state.sideBySide;
      btn.setAttribute("aria-pressed", String(on));
    });
    if (diffEditor) diffEditor.updateOptions({ renderSideBySide: state.sideBySide });
  }

  document.querySelectorAll("[data-view]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.sideBySide = btn.dataset.view === "side";
      localStorage.setItem("kanban.changes.view", state.sideBySide ? "side" : "inline");
      applyView();
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (e.target.closest && e.target.closest(".sc-editor, input, textarea, select")) return;
    if (!state.files.length) return;
    e.preventDefault();
    var idx = -1;
    for (var i = 0; i < state.files.length; i++) {
      if (state.files[i].path === state.selected) idx = i;
    }
    idx += e.key === "ArrowDown" ? 1 : -1;
    idx = Math.max(0, Math.min(state.files.length - 1, idx));
    select(state.files[idx].path);
  });

  var savedWidth = parseInt(localStorage.getItem("kanban.changes.sidebar"), 10);
  if (savedWidth >= 180) els.sidebar.style.width = savedWidth + "px";

  els.resizer.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    els.resizer.setPointerCapture(e.pointerId);
    document.body.classList.add("is-resizing");
    var startX = e.clientX;
    var startW = els.sidebar.getBoundingClientRect().width;
    function move(ev) {
      var w = Math.max(180, Math.min(window.innerWidth * 0.6, startW + ev.clientX - startX));
      els.sidebar.style.width = w + "px";
    }
    function up() {
      els.resizer.removeEventListener("pointermove", move);
      els.resizer.removeEventListener("pointerup", up);
      document.body.classList.remove("is-resizing");
      localStorage.setItem("kanban.changes.sidebar", String(Math.round(els.sidebar.getBoundingClientRect().width)));
    }
    els.resizer.addEventListener("pointermove", move);
    els.resizer.addEventListener("pointerup", up);
  });

  applyView();
  loadMonaco().catch(function () {
    showMessage("Impossible de charger l'éditeur de diff", "Vérifie la connexion au CDN jsdelivr.");
  });
  tick();
})();
