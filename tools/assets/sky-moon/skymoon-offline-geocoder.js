/* UOY Sky+Moon offline place lookup. Pure ES5: no filesystem, network or DOM.
 * GeoNames settlement data is supplied by the caller, with its version.
 * Exact place/alias + administrative qualifiers only; population never picks
 * a winner among different places with the same name. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.UOYSkyMoonOfflineGeocoder = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var OWN = Object.prototype.hasOwnProperty;
  var COUNTRY_ALIASES = {
    GB: ["UK", "U K", "Great Britain", "Britain", "United Kingdom"],
    US: ["USA", "U S", "U S A", "United States", "United States of America"],
    VN: ["Vietnam", "Viet Nam", "VietNam"],
    NL: ["Netherlands", "The Netherlands"],
    NZ: ["New Zealand"],
    AE: ["UAE", "United Arab Emirates"],
    KR: ["South Korea", "Republic of Korea"],
    KP: ["North Korea"],
    CZ: ["Czech Republic", "Czechia"],
    RU: ["Russia", "Russian Federation"]
  };
  function trim(value) { return String(value == null ? "" : value).replace(/^\s+|\s+$/g, ""); }
  function array(value) { return Object.prototype.toString.call(value) === "[object Array]"; }
  function fold(value) {
    var text = trim(value).toLowerCase();
    try { if (typeof text.normalize === "function") text = text.normalize("NFD").replace(/[\u0300-\u036f]/g, ""); } catch (_) {}
    // Keep non-Latin names intact. ASCII-only token stripping would collapse
    // many unrelated Chinese/Arabic/etc. names to the same empty key.
    return trim(text.replace(/\u0111/g, "d").replace(/[\u2018\u2019']/g, "")
      .replace(/[.,;:\/\\()[\]{}_\-]+/g, " ").replace(/\s+/g, " "));
  }
  function parseCoordinates(query) {
    var value = trim(query), parts = value.split(","), number = /^[+\-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
    var first = trim(parts[0]), numericLike = (/^[+\-\d.eE\s,;]+$/.test(value) && /\d/.test(value)) ||
      (parts.length > 1 && (/^[+\-\d.eE\s]+$/.test(first) || /^[+\-]?(?:nan|infinity)$/i.test(first)));
    if (!value || !numericLike) return null;
    if (parts.length !== 2 || !number.test(first) || !number.test(trim(parts[1]))) {
      return { error: "Tọa độ phải theo dạng latitude, longitude; dùng dấu chấm thập phân, không tự đổi thứ tự." };
    }
    var lat = Number(first), lon = Number(trim(parts[1]));
    if (!isFinite(lat) || !isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      return { error: "Latitude phải từ -90 đến 90; longitude từ -180 đến 180. Không tự đảo hai tọa độ." };
    }
    return { lat: lat, lon: lon, label: String(lat) + ", " + String(lon) + " (tọa độ)", source: "coordinates" };
  }
  function dataError(message) { return new Error("Dữ liệu địa điểm offline không hợp lệ: " + message + "."); }
  function dictionary(value) { return value && typeof value === "object" && !array(value); }
  function addTerm(terms, value) { var key = fold(value); if (key) terms[key] = true; }
  function createLookup(catalog) {
    if (!catalog || catalog.schema !== 1 || typeof catalog.version !== "string" || !trim(catalog.version) ||
        !dictionary(catalog.countries) || !dictionary(catalog.admin1) || !dictionary(catalog.admin2) ||
        !array(catalog.cities) || catalog.cities.length > 500000) throw dataError("schema");
    var rows = catalog.cities, index = Object.create(null), countryTerms = Object.create(null);
    var version = catalog.version, countryCode, country, aliases, i, j, row, names, key;
    for (countryCode in catalog.countries) if (OWN.call(catalog.countries, countryCode)) {
      country = catalog.countries[countryCode];
      if (!/^[A-Z]{2}$/.test(countryCode) || !array(country) || country.length < 2 ||
          typeof country[0] !== "string" || !trim(country[0]) || typeof country[1] !== "string") throw dataError("quốc gia");
      var terms = Object.create(null);
      addTerm(terms, countryCode); addTerm(terms, country[0]); addTerm(terms, country[1]);
      aliases = COUNTRY_ALIASES[countryCode] || [];
      for (j = 0; j < aliases.length; j++) addTerm(terms, aliases[j]);
      countryTerms[countryCode] = terms;
    }
    function validateAdmins(entries) {
      for (var code in entries) if (OWN.call(entries, code)) {
        var item = entries[code];
        if (!array(item) || item.length < 2 || typeof item[0] !== "string" || typeof item[1] !== "string") throw dataError("vùng hành chính");
      }
    }
    validateAdmins(catalog.admin1); validateAdmins(catalog.admin2);
    function addName(name, rowIndex) {
      var nameKey = fold(name), stored;
      if (!nameKey) return;
      stored = index[nameKey];
      if (stored === undefined) index[nameKey] = rowIndex;
      else if (typeof stored === "number") {
        if (stored !== rowIndex) index[nameKey] = [stored, rowIndex];
      } else if (stored[stored.length - 1] !== rowIndex) stored.push(rowIndex);
    }
    for (i = 0; i < rows.length; i++) {
      row = rows[i];
      if (!array(row) || row.length !== 11 || typeof row[0] !== "number" || !isFinite(row[0]) || row[0] <= 0 || Math.floor(row[0]) !== row[0] ||
          typeof row[1] !== "string" || !trim(row[1]) || typeof row[2] !== "string" || typeof row[3] !== "string" || row[3].length > 65536 ||
          typeof row[4] !== "number" || !isFinite(row[4]) || row[4] < -90 || row[4] > 90 ||
          typeof row[5] !== "number" || !isFinite(row[5]) || row[5] < -180 || row[5] > 180 ||
          typeof row[6] !== "string" || !OWN.call(countryTerms, row[6]) || typeof row[7] !== "string" || typeof row[8] !== "string" ||
          typeof row[9] !== "number" || !isFinite(row[9]) || row[9] < 0 || !/^(?:PPL[A-Z0-9]*|STLMT)$/.test(row[10])) throw dataError("dòng " + (i + 1));
      addName(row[1], i); addName(row[2], i);
      names = row[3].split(",");
      for (j = 0; j < names.length; j++) addName(names[j], i);
    }
    function admin(rowValue, level) {
      var code = rowValue[6] + "." + rowValue[7];
      if (level === 2) code += "." + rowValue[8];
      var source = level === 2 ? catalog.admin2 : catalog.admin1;
      return OWN.call(source, code) ? source[code] : null;
    }
    function qualifierTerms(rowValue) {
      var terms = Object.create(null), countrySet = countryTerms[rowValue[6]], term, a1 = admin(rowValue, 1), a2 = admin(rowValue, 2);
      for (term in countrySet) if (OWN.call(countrySet, term)) terms[term] = true;
      // Codes are accepted only for this exact administrative path, never as
      // substring matches against another place's display label.
      addTerm(terms, rowValue[7]); addTerm(terms, rowValue[8]);
      if (a1) { addTerm(terms, a1[0]); addTerm(terms, a1[1]); }
      if (a2) { addTerm(terms, a2[0]); addTerm(terms, a2[1]); }
      return terms;
    }
    function matchesQualifier(qualifier, terms) {
      var words = fold(qualifier).split(" "), reachable = [], start, end, phrase;
      if (!words.length || !words[0]) return false;
      reachable[0] = true;
      for (start = 0; start < words.length; start++) if (reachable[start]) {
        phrase = "";
        for (end = start; end < words.length; end++) {
          phrase += (phrase ? " " : "") + words[end];
          if (OWN.call(terms, phrase)) reachable[end + 1] = true;
        }
      }
      return reachable[words.length] === true;
    }
    function result(rowValue) {
      var labels = [], seen = Object.create(null), a1 = admin(rowValue, 1), a2 = admin(rowValue, 2);
      function add(value) { var normalized = fold(value); if (normalized && !seen[normalized]) { seen[normalized] = true; labels.push(value); } }
      add(rowValue[1]); if (a2) add(a2[0]); if (a1) add(a1[0]); add(catalog.countries[rowValue[6]][0]);
      return { lat: rowValue[4], lon: rowValue[5], label: labels.join(", "), name: rowValue[1],
        countryCode: rowValue[6].toLowerCase(), source: "geonames", geonameId: rowValue[0], datasetVersion: version };
    }
    return function lookup(query) {
      var source = trim(query), segments = source.split(","), place = fold(segments.shift()), qualifiers = [], stored, at, tail;
      if (!place || source.length > 1000 || segments.length > 8) return { status: "not-found" };
      for (var si = 0; si < segments.length; si++) if (trim(segments[si])) qualifiers.push(trim(segments[si]));
      stored = index[place];
      // The no-comma form "Layton UT" / "Leeds UK" may use a suffix of
      // exact country/admin terms. Longest exact city name is preserved.
      if (stored === undefined) {
        at = place.lastIndexOf(" ");
        while (at > 0) {
          key = place.substring(0, at);
          if (index[key] !== undefined) { stored = index[key]; tail = place.substring(at + 1); qualifiers.unshift(tail); break; }
          at = place.lastIndexOf(" ", at - 1);
        }
      }
      if (stored === undefined) return { status: "not-found" };
      var indexes = typeof stored === "number" ? [stored] : stored, found = [], seenIds = Object.create(null), candidate, valid, candidateTerms;
      for (var ci = 0; ci < indexes.length; ci++) {
        candidate = rows[indexes[ci]]; if (seenIds[candidate[0]]) continue;
        candidateTerms = qualifierTerms(candidate); valid = true;
        for (var qi = 0; qi < qualifiers.length; qi++) if (!matchesQualifier(qualifiers[qi], candidateTerms)) { valid = false; break; }
        if (valid) { seenIds[candidate[0]] = true; found.push(result(candidate)); }
      }
      found.sort(function (a, b) { return a.geonameId - b.geonameId; });
      if (found.length === 1) return { status: "matched", result: found[0] };
      if (found.length > 1) return { status: "ambiguous", candidates: found };
      return { status: "not-found" };
    };
  }
  return { parseCoordinates: parseCoordinates, createLookup: createLookup };
});
// Browser ES-module adapter only. The original pure lookup factory is unchanged.
export default globalThis.UOYSkyMoonOfflineGeocoder;
