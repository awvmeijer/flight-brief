/* The route on a Mapbox globe, in the 3DREAMS night style. The page gives the data as JSON in
   #fb-map-data; this file only draws it. Without a token or without WebGL the window keeps the
   sentence the server put there. */
(function () {
  "use strict";
  var holder = document.getElementById("fb-map");
  var raw = document.getElementById("fb-map-data");
  if (!holder || !raw || !window.mapboxgl) { return; }
  var data;
  try { data = JSON.parse(raw.textContent); } catch (err) { return; }
  if (!data.token || !mapboxgl.supported || !mapboxgl.supported()) { return; }

  var GREEN = "#39ff14", AMBER = "#e8962c", INK = "#ffffff", GROUND = "#06090f";
  mapboxgl.accessToken = data.token;
  holder.textContent = "";
  holder.classList.add("is-live");

  var bounds = new mapboxgl.LngLatBounds();
  data.hours.forEach(function (h) { h.line.forEach(function (p) { bounds.extend(p); }); });

  var map = new mapboxgl.Map({
    container: holder,
    style: "mapbox://styles/mapbox/navigation-night-v1",
    projection: "globe",
    bounds: bounds,
    fitBoundsOptions: { padding: 56 },
    attributionControl: true,
    cooperativeGestures: true,
    dragRotate: false,
    pitchWithRotate: false
  });
  map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");

  /* Mapbox answers 401 or 403 when the token is wrong, or is limited to other sites than this
     one. Then there is no map to show, and a grey globe would say nothing: say why instead. */
  var refused = false;
  map.on("error", function (ev) {
    var status = ev && ev.error && ev.error.status;
    if (refused || (status !== 401 && status !== 403)) { return; }
    refused = true;
    map.remove();
    holder.classList.remove("is-live");
    holder.textContent = "";
    var p = document.createElement("p");
    p.textContent = "Mapbox refused this page's token (answer " + status + "). The token may be " +
      "limited to other site addresses than " + window.location.origin + ", or it may be mistyped.";
    holder.appendChild(p);
  });

  function features(pick) {
    return {
      type: "FeatureCollection",
      features: data.hours.filter(pick).map(function (h) {
        return { type: "Feature", properties: { hour: h.label, state: h.state },
                 geometry: { type: "LineString", coordinates: h.line } };
      })
    };
  }

  map.on("load", function () {
    map.setFog({
      color: "rgb(10, 16, 24)", "high-color": "rgb(14, 30, 28)", "horizon-blend": 0.08,
      "space-color": "rgb(4, 7, 11)", "star-intensity": 0.35
    });
    map.addSource("route", { type: "geojson", data: features(function () { return true; }) });
    map.addSource("flagged", { type: "geojson", data: features(function (h) { return h.state === "flagged"; }) });
    map.addSource("marks", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: data.hours.map(function (h) {
          return { type: "Feature", properties: { hour: h.label, state: h.state },
                   geometry: { type: "Point", coordinates: h.mid } };
        })
      }
    });
    map.addLayer({ id: "route-glow", type: "line", source: "route",
      paint: { "line-color": GREEN, "line-width": 9, "line-opacity": 0.18, "line-blur": 6 } });
    map.addLayer({ id: "route", type: "line", source: "route",
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": GREEN, "line-width": 2.2,
               "line-dasharray": ["case", ["in", ["get", "state"], ["literal", ["too_early", "not_covered"]]],
                                  ["literal", [2, 2]], ["literal", [1, 0]]] } });
    map.addLayer({ id: "flagged-glow", type: "line", source: "flagged",
      paint: { "line-color": AMBER, "line-width": 14, "line-opacity": 0.22, "line-blur": 8 } });
    map.addLayer({ id: "flagged", type: "line", source: "flagged",
      paint: { "line-color": AMBER, "line-width": 5 } });
    map.addLayer({ id: "marks", type: "symbol", source: "marks",
      layout: { "text-field": ["get", "hour"], "text-size": 11, "text-offset": [1.4, 0],
                "text-font": ["DIN Pro Medium", "Arial Unicode MS Regular"], "text-allow-overlap": false },
      paint: { "text-color": ["case", ["==", ["get", "state"], "flagged"], AMBER, INK],
               "text-halo-color": GROUND, "text-halo-width": 1.4 } });
    data.airports.forEach(function (a) {
      var el = document.createElement("div");
      el.className = "fb-pin";
      el.textContent = a.code;
      new mapboxgl.Marker({ element: el, anchor: "bottom-left" }).setLngLat(a.at).addTo(map);
    });
  });
})();
