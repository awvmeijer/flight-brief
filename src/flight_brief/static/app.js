/* Two conveniences, both optional: airport suggestions under the route fields, and the loading
   screen while a brief is being made. The page works the same without this file. */
(function () {
  "use strict";

  /* ---- airport suggestions ---- */
  function suggest(input) {
    var list = document.createElement("ul");
    var sub = input.parentNode.querySelector(".sub");
    var timer = null, items = [], on = -1, asked = "";
    list.className = "fb-suggest";
    list.hidden = true;
    list.setAttribute("role", "listbox");
    input.setAttribute("autocomplete", "off");
    input.parentNode.appendChild(list);

    function close() { list.hidden = true; on = -1; }
    function choose(a) {
      input.value = a.iata;
      if (sub) { sub.textContent = a.iata + " · " + a.name; }
      close();
    }
    function draw() {
      list.textContent = "";
      items.forEach(function (a, i) {
        var li = document.createElement("li"), b = document.createElement("b"), c = document.createElement("code");
        b.textContent = a.city;
        c.textContent = a.iata;
        li.appendChild(b);
        li.appendChild(document.createTextNode(" " + a.name + " "));
        li.appendChild(c);
        li.setAttribute("role", "option");
        if (i === on) { li.className = "on"; }
        li.addEventListener("mousedown", function (ev) { ev.preventDefault(); choose(a); });
        list.appendChild(li);
      });
      list.hidden = items.length === 0;
    }
    function ask() {
      var q = input.value.trim();
      if (q.length < 2) { items = []; draw(); return; }
      asked = q;
      fetch("/airports?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (found) { if (asked === q) { items = found; on = found.length ? 0 : -1; draw(); } })
        .catch(function () { items = []; draw(); });
    }
    input.addEventListener("input", function () {
      if (sub) { sub.textContent = ""; }
      clearTimeout(timer);
      timer = setTimeout(ask, 140);
    });
    input.addEventListener("keydown", function (ev) {
      if (list.hidden) { return; }
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        on = (on + (ev.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
        draw();
      } else if (ev.key === "Enter" && on >= 0) {
        ev.preventDefault();
        choose(items[on]);
      } else if (ev.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", close);
  }
  Array.prototype.forEach.call(document.querySelectorAll("input[data-airport]"), suggest);

  var swap = document.querySelector(".fb-swap");
  if (swap) {
    swap.addEventListener("click", function () {
      var a = swap.form.querySelector('input[name="from"]'), b = swap.form.querySelector('input[name="to"]');
      if (!a || !b) { return; }
      var v = a.value; a.value = b.value; b.value = v;
      var sa = a.parentNode.querySelector(".sub"), sb = b.parentNode.querySelector(".sub");
      if (sa && sb) { var t = sa.textContent; sa.textContent = sb.textContent; sb.textContent = t; }
    });
  }

  /* ---- the loading screen: shown when a brief is asked for, gone when the page changes ---- */
  var veil = document.getElementById("fb-loading");
  if (veil) {
    var show = function (label) {
      var where = veil.querySelector("[data-what]");
      if (where && label) { where.textContent = label; }
      veil.hidden = false;
    };
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest && ev.target.closest("a[data-brief]");
      if (a && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && ev.button === 0) { show(a.getAttribute("data-brief")); }
    });
    Array.prototype.forEach.call(document.querySelectorAll("form[data-brief]"), function (f) {
      f.addEventListener("submit", function () { show(""); });
    });
    window.addEventListener("pageshow", function () { veil.hidden = true; });
  }
})();
