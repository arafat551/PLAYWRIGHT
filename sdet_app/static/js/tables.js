/* SDET tables: lightweight search + sort + pagination (no dependency). */
(function () {
  "use strict";

  var PAGE_SIZE = 10;

  function parseVal(txt) {
    var t = (txt || "").replace(/\s+/g, "").trim();
    if (t === "" || t === "-") return null;
    if (/%$/.test(t)) {
      var p = parseFloat(t);
      return isNaN(p) ? null : p;
    }
    var dt = Date.parse(t);
    if (!isNaN(dt) && /^\d{4}-\d{2}-\d{2}/.test(t)) return dt;
    var num = parseFloat(t.replace(",", "."));
    return isNaN(num) ? t.toLowerCase() : num;
  }

  function buildTable(table) {
    var theadCells = Array.prototype.slice.call(table.querySelectorAll("thead th"));
    var headRow = table.querySelector("thead tr");
    var wrap = table.closest(".card");
    if (!headRow) return;

    var jsIndex = headRow.querySelector(".js-index");
    var sortable = theadCells.filter(function (th) { return !th.classList.contains("js-index"); });

    var tools = document.createElement("div");
    tools.className = "table-tools";
    var search = document.createElement("input");
    search.type = "text";
    search.className = "table-filter";
    search.placeholder = "Rechercher…";
    var count = document.createElement("span");
    count.className = "table-count";

    var pager = document.createElement("div");
    pager.className = "table-pager";
    var prev = document.createElement("button");
    prev.type = "button";
    prev.className = "btn-sm";
    prev.textContent = "←";
    var next = document.createElement("button");
    next.type = "button";
    next.className = "btn-sm";
    next.textContent = "→";
    pager.appendChild(prev);
    pager.appendChild(next);

    tools.appendChild(search);
    tools.appendChild(count);
    table.insertAdjacentElement("beforebegin", tools);
    table.insertAdjacentElement("afterend", pager);

    var state = { rows: [], query: "", sort: null, dir: 1, page: 0 };

    function collect() {
      state.rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
    }

    function visibleRows() {
      var q = state.query;
      var filtered = state.rows.filter(function (tr) {
        if (!q) return true;
        return tr.textContent.toLowerCase().indexOf(q) !== -1;
      });
      if (state.sort !== null) {
        var si = state.sort;
        var dir = state.dir;
        filtered.sort(function (a, b) {
          var va = parseVal(a.children[si].textContent);
          var vb = parseVal(b.children[si].textContent);
          if (va === null) return 1;
          if (vb === null) return -1;
          if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
          return String(va).localeCompare(String(vb), "fr") * dir;
        });
      }
      return filtered;
    }

    function render() {
      var rows = visibleRows();
      var total = rows.length;
      var pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      if (state.page >= pages) { state.page = pages - 1; }
      var start = state.page * PAGE_SIZE;
      var pageRows = rows.slice(start, start + PAGE_SIZE);

      table.querySelectorAll("tbody tr").forEach(function (tr) { tr.style.display = "none"; });
      pageRows.forEach(function (tr) { tr.style.display = ""; });

      count.textContent = total + (total > 1 ? " résultats" : " résultat");
      prev.disabled = state.page <= 0 || total === 0;
      next.disabled = state.page >= pages - 1 || total === 0;
    }

    search.addEventListener("input", function () {
      state.query = search.value.trim().toLowerCase();
      state.page = 0;
      render();
    });

    prev.addEventListener("click", function () { if (state.page > 0) { state.page--; render(); } });
    next.addEventListener("click", function () { if (state.page < state.rows.length / PAGE_SIZE - 1) { state.page++; render(); } });

    sortable.forEach(function (th, idx) {
      th.style.cursor = "pointer";
      th.title = "Trier";
      th.addEventListener("click", function () {
        var col = theadCells.indexOf(th);
        if (state.sort === col) {
          state.dir = -state.dir;
        } else {
          state.sort = col;
          state.dir = 1;
        }
        sortable.forEach(function (t) { t.classList.remove("sorted", "asc", "desc"); });
        th.classList.add("sorted", state.dir === 1 ? "asc" : "desc");
        state.page = 0;
        render();
      });
    });

    // Add a trailing empty column header when an actions column exists (keeps sorting clickable)
    collect();
    render();
  }

  function init() {
    document.querySelectorAll("table.data-table[data-sortable]").forEach(buildTable);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();