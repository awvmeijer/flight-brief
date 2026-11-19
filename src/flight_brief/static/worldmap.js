/* The world map: the turbulence estimate and the forecast storm clouds as two pictures draped
   on a Mapbox globe, in the route map's night style. The page gives the data as JSON in
   #fb-world-data (the token, the four corners, and for each forecast hour its label and the
   address of each picture); this file draws it and swaps the pictures when a control changes.
   Without a token or without WebGL the window keeps the sentence the server put there, and the
   controls stay switched off. */
(function () {
  "use strict";
  var holder = document.getElementById("fb-world");
  var raw = document.getElementById("fb-world-data");
  if (!holder || !raw || !window.mapboxgl) { return; }
  var data;
  try { data = JSON.parse(raw.textContent); } catch (err) { return; }
  if (!data.token || !data.frames || !data.frames.length) { return; }
  if (!mapboxgl.supported || !mapboxgl.supported()) { return; }

  var slider = document.getElementById("fb-world-hour");
  var when = document.getElementById("fb-world-when");
  var note = document.getElementById("fb-world-note");
  var stormBox = document.getElementById("fb-world-storm");
  var levels = Array.prototype.slice.call(document.querySelectorAll('input[name="fb-world-level"]'));
  if (!slider || !when || !levels.length) { return; }

  mapboxgl.accessToken = data.token;
  holder.textContent = "";
  holder.classList.add("is-live");

  var map = new mapboxgl.Map({
    container: holder,
    style: "mapbox://styles/mapbox/navigation-night-v1",
    projection: "globe",
    center: [10, 25],
    zoom: 1.3,
    attributionControl: true,
    cooperativeGestures: true,
    dragRotate: false,
    pitchWithRotate: false
  });
  map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");

  function controls(on) {
    slider.disabled = !on;
    levels.forEach(function (input) { input.disabled = !on; });
    if (stormBox) { stormBox.disabled = !on || !data.storm; }
  }

  /* Mapbox answers 401 or 403 when the token is wrong, or is limited to other sites than this
     one. Then there is no map to show, and a grey globe would say nothing: say why instead. */
  var refused = false;
  var ready = false;
  map.on("error", function (ev) {
    var status = ev && ev.error && ev.error.status;
    if (refused || (status !== 401 && status !== 403)) { return; }
    refused = true;
    ready = false;
    map.remove();
    controls(false);
    holder.classList.remove("is-live");
    holder.textContent = "";
    var p = document.createElement("p");
    p.textContent = "Mapbox refused this page's token (answer " + status + "). The token may be " +
      "limited to other site addresses than " + window.location.origin + ", or it may be mistyped.";
    holder.appendChild(p);
  });

  /* What each layer should show now (the address of a picture, or null for nothing), and what
     went wrong with it, if anything. A picture is asked for first and given to the map only when
     the app has answered with it: the map never keeps showing one hour under another's label. */
  var want = { estimate: undefined, storm: undefined };
  var wrong = { estimate: "", storm: "" };
  var labels;

  function say() {
    if (note) { note.textContent = [wrong.estimate, wrong.storm].filter(Boolean).join(" "); }
  }

  function hide(kind) {
    if (map.getLayer(kind)) { map.setLayoutProperty(kind, "visibility", "none"); }
  }

  function draw(kind, url) {
    var source = map.getSource(kind);
    if (source) {
      source.updateImage({ url: url });
      map.setLayoutProperty(kind, "visibility", "visible");
      return;
    }
    map.addSource(kind, { type: "image", url: url, coordinates: data.corners });
    /* Under the place names, and the estimate under the storm clouds. */
    var above = kind === "estimate" && map.getLayer("storm") ? "storm" : labels;
    map.addLayer({ id: kind, type: "raster", source: kind,
      paint: { "raster-opacity": 1, "raster-fade-duration": 0 } }, above);
  }

  function show(kind, path, none) {
    if (want[kind] === path) { return; }
    want[kind] = path;
    if (!path) {
      hide(kind);
      wrong[kind] = none || "";
      say();
      return;
    }
    var url = new URL(path, window.location.href).href;
    fetch(url, { credentials: "same-origin" }).then(function (answer) {
      if (!answer.ok) { throw answer.status; }
      return answer.blob();
    }).then(function () {
      if (refused || want[kind] !== path) { return; }
      draw(kind, url);
      wrong[kind] = "";
      say();
    }).catch(function (why) {
      if (refused || want[kind] !== path) { return; }
      hide(kind);
      var what = "The picture of the " + (kind === "estimate" ? "estimate" : "storm clouds");
      wrong[kind] = why === 404
        ? what + " for this time is no longer served. Load the page again for the newest forecast."
        : why === 429
          ? "Too many pictures were asked for from this address. Wait a few minutes."
          : what + " for this time could not be loaded.";
      say();
    });
  }

  function level() {
    var picked = levels.filter(function (input) { return input.checked; })[0] || levels[0];
    return picked.value;
  }

  function apply() {
    var frame = data.frames[Number(slider.value)] || data.frames[0];
    when.textContent = frame.label;
    slider.setAttribute("aria-valuetext", frame.label);
    if (!ready) { return; }
    show("estimate", frame.estimate[level()] || null, "");
    var storms = !!(stormBox && stormBox.checked && data.storm);
    show("storm", storms ? frame.storm : null,
         storms ? "There is no storm-cloud forecast for this time." : "");
  }

  slider.addEventListener("input", apply);
  slider.addEventListener("change", apply);
  levels.forEach(function (input) { input.addEventListener("change", apply); });
  if (stormBox) { stormBox.addEventListener("change", apply); }

  map.on("load", function () {
    if (refused) { return; }
    map.setFog({
      color: "rgb(10, 16, 24)", "high-color": "rgb(14, 30, 28)", "horizon-blend": 0.08,
      "space-color": "rgb(4, 7, 11)", "star-intensity": 0.35
    });
    var first = (map.getStyle().layers || []).filter(function (layer) {
      return layer.type === "symbol";
    })[0];
    labels = first ? first.id : undefined;
    ready = true;
    controls(true);
    apply();
  });
})();
