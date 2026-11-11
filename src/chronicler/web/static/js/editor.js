// Edit-mode glue: only what HTML/HTMX cannot do. Everything else is server-rendered (HTMX)
// and every editing rule lives in Python (edit_text.py, changes.py).
//   1. Run CodeMirror with tag highlighting.   2. Send the selection to Python for the
//   toolbar and quick fixes, and apply the edit that comes back.   3. Mark issue lines.
//   4. Open the <dialog>s the server sends.   5. Ask before leaving with unsaved edits.
(function () {
  "use strict";
  var root = document.getElementById("editor");
  if (!root || !window.CodeMirror || !window.htmx) return;
  var form = document.getElementById("editor-form");
  var entryId = root.dataset.entryId;
  var TAG = "(secret|revealed|prep)";
  var OPEN = new RegExp("^\\{" + TAG + "(?:\\s+(?:[^{}\\[\\]\\n]|\\[\\[[^\\[\\]\\n]*\\]\\])*)?\\}");
  var CLOSE = new RegExp("^\\{/" + TAG + "\\}");

  // 1. Highlighting: tags and the text inside them (spans end at headings, like the parser).
  CodeMirror.defineMode("chronicler", function (config) {
    return CodeMirror.overlayMode(CodeMirror.getMode(config, "markdown"), {
      startState: function () { return { stack: [] }; },
      copyState: function (s) { return { stack: s.stack.slice() }; },
      token: function (stream, state) {
        if (stream.sol() && /^#{1,6}\s/.test(stream.string)) state.stack = [];
        var m = stream.match(OPEN);
        if (m) { state.stack.push(m[1]); return "tag tag-" + m[1]; }
        if ((m = stream.match(CLOSE))) {
          if (state.stack[state.stack.length - 1] === m[1]) state.stack.pop();
          return "tag tag-" + m[1];
        }
        var inside = state.stack.map(function (k) { return "in-" + k; }).join(" ");
        if (stream.match(/^!?\[\[[^\[\]\n]+\]\]/)) return ("wikilink " + inside).trim();
        stream.next();
        while (!stream.eol() && "{[!".indexOf(stream.peek()) < 0) stream.next();
        return inside || null;
      },
    }, true);
  });
  var cm = CodeMirror.fromTextArea(document.getElementById("body-editor"), {
    mode: "chronicler", lineWrapping: true, inputStyle: "contenteditable",
    spellcheck: true, viewportMargin: Infinity,
  });
  var dirty = false, leaving = false;
  cm.on("change", function () { cm.save(); dirty = true; htmx.trigger(form, "cm-change"); });
  form.addEventListener("input", function () { dirty = true; });

  function say(text) {
    var status = document.querySelector("#editor-feedback .editor-status");
    if (status) status.textContent = text;
  }

  // 2. Toolbar and quick fixes: Python decides, the editor applies one edit (undo works).
  function transform(op, extra) {
    var body = Object.assign({
      op: op, text: cm.getValue(),
      start: cm.indexFromPos(cm.getCursor("from")), end: cm.indexFromPos(cm.getCursor("to")),
    }, extra || {});
    return fetch("/api/transform", {
      method: "POST", credentials: "same-origin", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", "X-Chronicler": "1" },
    }).then(function (r) { return r.json(); }).then(function (edit) {
      if (edit.confirm) {
        if (window.confirm(edit.confirm)) transform(op, Object.assign({}, extra, { confirmed: true }));
        return;
      }
      if (edit.notice) say(edit.notice);
      if (edit.insert === null || edit.insert === undefined) return;
      cm.operation(function () {
        cm.replaceRange(edit.insert, cm.posFromIndex(edit.start), cm.posFromIndex(edit.end));
        cm.setSelection(cm.posFromIndex(edit.sel_from), cm.posFromIndex(edit.sel_to));
      });
      cm.focus();
    });
  }
  document.addEventListener("click", function (event) {
    var t = event.target, el;
    if ((el = t.closest("[data-tool]")) && !el.closest(".picker")) {
      transform(el.dataset.tool);
    } else if ((el = t.closest("[data-fix]"))) {
      transform(el.dataset.fix, { line: +el.dataset.line, column: +el.dataset.column });
    } else if ((el = t.closest("[data-jump]"))) {
      cm.focus();
      cm.setCursor({ line: +el.dataset.line, ch: +el.dataset.column });
      cm.scrollIntoView(null, 120);
    } else if ((el = t.closest("#modal-root .pick"))) {
      var tool = el.closest(".picker").dataset.tool;
      var op = { reveal: "reveal", link: "link", image: "embed" }[tool];
      el.closest("dialog").close();
      transform(op, { target: el.dataset.pick, asset_kind: el.dataset.kind || "image" });
    }
  });

  // 3. Mark the lines that have problems (the server lists them in the feedback).
  var marked = [];
  function markIssues() {
    marked.forEach(function (h) { cm.removeLineClass(h, "background", "cm-issue-line"); });
    marked = [];
    document.querySelectorAll("#editor-feedback [data-issue-line]").forEach(function (li) {
      var line = +li.dataset.issueLine;
      if (line < cm.lineCount()) marked.push(cm.addLineClass(line, "background", "cm-issue-line"));
    });
  }
  markIssues();

  // 4. Dialogs: open what the server swaps in; close them once the editor has news.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail.target;
    if (target.id === "modal-root") {
      var dialog = target.querySelector("dialog");
      if (dialog && !dialog.open) dialog.showModal();
    }
    if (target.id === "editor-feedback") {
      markIssues();
      document.querySelectorAll("#modal-root dialog[open]").forEach(function (d) { d.close(); });
    }
  });
  document.body.addEventListener("htmx:afterRequest", function (event) {
    var path = (event.detail.pathInfo || {}).requestPath || "";
    if (event.detail.successful && /\/(draft|save|keep)\b/.test(path)) dirty = false;
  });
  document.body.addEventListener("htmx:beforeOnLoad", function (event) {
    if (event.detail.xhr.getResponseHeader("HX-Redirect")) leaving = true;
  });

  // 5. Leaving: the server says whether anything is unsaved (and offers save / keep / discard).
  document.addEventListener("click", function (event) {
    var a = event.target.closest("a[href]");
    if (!a || leaving || a.target || event.ctrlKey || event.metaKey || event.shiftKey) return;
    var href = a.getAttribute("href");
    if (href === "" || href.charAt(0) === "#" || a.closest("#modal-root")) return;
    event.preventDefault();
    cm.save();
    htmx.ajax("POST", "/edit/" + entryId + "/leave?to=" + encodeURIComponent(a.pathname + a.search + a.hash),
      { source: form, target: "#modal-root", swap: "innerHTML" });
  }, true);
  window.addEventListener("beforeunload", function (event) {
    if (dirty && !leaving) { event.preventDefault(); event.returnValue = ""; }
  });
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      cm.save();
      root.querySelector("[data-save]").click();
    }
  });
})();
