/* Local, dependency-free WebGL relief preview. No network or file I/O.
 *
 * UoyDepthViewer.create(canvas[, options]) -> viewer
 * viewer.setData({width,height,depth:Float32Array|Uint16Array,
 *                 rgba:Uint8Array|Uint8ClampedArray,alpha?:Uint8Array,
 *                 support?:Uint8Array,product_type?:string,shape_alpha?:Uint8Array})
 * Ceramic requires an authoritative template shape_alpha (same native canvas).
 * Acrylic may supply its independent native cut shape as shape_alpha too.
 * Artwork alpha never cuts holes in an explicitly supplied substrate.
 * viewer.setDimensions({heightMm:100,totalMm:3,reliefMm:0.6})
 * viewer.setAppearance({rimBoost:1.2,reliefBoost:6,gloss:0.65}) // mockup only
 * viewer.preset('front'|'back'|'side'|'left'|'right')
 * viewer.exportPNG({width?,height?}) -> transparent PNG data URL
 * viewer.exportFullPNG({width?,height?,padding?:0.04}) -> native-resolution PNG
 * Full export fits the complete product at the current rotation;
 * interactive zoom and the legacy exportPNG framing are intentionally separate.
 * viewer.getView()/setView({yaw,pitch,zoom?}); getHoles(); getCord()/setCord(...)
 * viewer.dispose()
 *
 * Front height uses native depth precision. The interactive mesh approximates
 * a continuous alpha contour on a bounded 384-cell lattice (maximum 512); sub-grid
 * holes are not fabrication geometry. Front/back share one sealed alpha mesh;
 * texture colors are padded only for sampling beyond that mesh's cutline.
 * Appearance gain deliberately exaggerates the printed front independently
 * of the fixed acrylic foundation; displayed thickness can exceed 3 mm.
 * This is an illustrative preview, not a calibrated manufacturing mesh.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.UoyDepthViewer = api;
}(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  var MAX_PIXELS = 40000000, MAX_GRID = 512, DEFAULT_GRID = 384;
  var MAX_TEXTURE = 2048, STRIDE = 9, CHUNK_VERTICES = 60000;
  var PI = Math.PI;

  function finite(value) { return typeof value === "number" && isFinite(value); }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function dimensions(value) {
    value = value || {};
    var height = value.heightMm === undefined ? 100 : value.heightMm;
    var total = value.totalMm === undefined ? 3 : value.totalMm;
    var relief = value.reliefMm === undefined ? 0.6 : value.reliefMm;
    if (!finite(height) || height < 10 || height > 1000) throw new Error("heightMm must be between 10 and 1000 mm.");
    if (total !== 3) throw new Error("Total thickness is fixed at 3 mm, including the relief.");
    if (!finite(relief) || relief < 0.4 || relief > 0.8) throw new Error("reliefMm must be between 0.4 and 0.8 mm.");
    return { heightMm: height, totalMm: 3, reliefMm: relief, minimumMm: 3 - relief };
  }
  function appearance(value) {
    value = value || {};
    var rim = value.rimBoost === undefined ? 1.2 : value.rimBoost;
    var boost = value.reliefBoost === undefined ? 6 : value.reliefBoost;
    var gloss = value.gloss === undefined ? 0.65 : value.gloss;
    if (!finite(boost) || boost < 0 || boost > 12) throw new Error("Visual relief boost must be between 0 and 12.");
    if (!finite(rim) || rim < 0 || rim > 12) throw new Error("Visual rim boost must be between 0 and 12.");
    if (!finite(gloss) || gloss < 0 || gloss > 1) throw new Error("Gloss must be between 0 and 1.");
    return { rimBoost: rim, reliefBoost: boost, gloss: gloss };
  }
  function byteArray(value) {
    return value instanceof Uint8Array || value instanceof Uint8ClampedArray;
  }
  function productType(value) {
    if (value === undefined) return "acrylic";
    if (value !== "acrylic" && value !== "ceramic_circle" && value !== "ceramic_heart") throw new Error("Unknown 3D product_type.");
    return value;
  }
  function ceramicData(data) { return productType(data.product_type) !== "acrylic"; }
  function withProduct(data, type) {
    if (!data) throw new Error("Native 3D data is required.");
    return { width: data.width, height: data.height, depth: data.depth, rgba: data.rgba,
      alpha: data.alpha, support: data.support, shape_alpha: data.shape_alpha, product_type: productType(type) };
  }
  function validateData(data) {
    if (!data || !finite(data.width) || !finite(data.height) || data.width % 1 || data.height % 1 || data.width < 2 || data.height < 2) {
      throw new Error("Native depth width and height must be integers of at least 2.");
    }
    var count = data.width * data.height, i, value;
    if (count > MAX_PIXELS) throw new Error("The 3D viewer accepts at most 40 million native pixels.");
    if (!(data.depth instanceof Float32Array) && !(data.depth instanceof Uint16Array)) throw new Error("Native depth must be Float32Array or Uint16Array, never an 8-bit preview.");
    if (data.depth.length !== count) throw new Error("Depth dimensions do not match the native array.");
    if (!byteArray(data.rgba) || data.rgba.length !== count * 4) throw new Error("Source RGBA must contain four bytes per native pixel.");
    if (data.alpha !== undefined && (!byteArray(data.alpha) || data.alpha.length !== count)) throw new Error("Alpha must contain one byte per native pixel.");
    if (data.support !== undefined && (!byteArray(data.support) || data.support.length !== count)) throw new Error("Support mask must contain one byte per native pixel.");
    if ((ceramicData(data) || data.shape_alpha !== undefined) && (!byteArray(data.shape_alpha) || data.shape_alpha.length !== count)) throw new Error("Cut shape must match the exact native depth dimensions.");
    if (data.depth instanceof Float32Array) {
      for (i = 0; i < count; i++) {
        value = data.depth[i];
        if (!finite(value) || value < 0 || value > 1) throw new Error("Float depth must be finite and normalized from 0 to 1.");
      }
    }
    return data;
  }
  function artworkAlphaAt(data, index) {
    // Existing engine payloads call the depth coverage "alpha". In ceramic
    // mode that may already be the solid template, so only the color texture's
    // own alpha describes whether a pixel carries ink (including new Type).
    return !data.shape_alpha && !ceramicData(data) && data.alpha ? data.alpha[index] : data.rgba[index * 4 + 3];
  }
  function alphaAt(data, index) { return data.shape_alpha ? data.shape_alpha[index] : artworkAlphaAt(data, index); }
  function boundsOf(data) {
    var x0 = data.width, y0 = data.height, x1 = -1, y1 = -1, x, y;
    for (y = 0; y < data.height; y++) for (x = 0; x < data.width; x++) {
      if (alphaAt(data, y * data.width + x) >= 128) {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
    }
    if (x1 < x0) throw new Error("The alpha silhouette is empty.");
    return { x0: x0, y0: y0, x1: x1 + 1, y1: y1 + 1 };
  }
  function sample(data, x, y, channel) {
    x = clamp(x, 0, data.width - 1); y = clamp(y, 0, data.height - 1);
    var ix = Math.floor(x), iy = Math.floor(y), jx = Math.min(ix + 1, data.width - 1), jy = Math.min(iy + 1, data.height - 1);
    var fx = x - ix, fy = y - iy;
    function pixel(px, py) {
      var index = py * data.width + px;
      if (channel === -1) return data.depth[index] / (data.depth instanceof Uint16Array ? 65535 : 1);
      if (channel === 3) return alphaAt(data, index);
      if (channel === 4) return data.support ? data.support[index] : 0;
      if (channel === 5) return artworkAlphaAt(data, index);
      return data.rgba[index * 4 + channel];
    }
    return (pixel(ix, iy) * (1 - fx) + pixel(jx, iy) * fx) * (1 - fy)
      + (pixel(ix, jy) * (1 - fx) + pixel(jx, jy) * fx) * fy;
  }
  function normalise(x, y, z) {
    var length = Math.sqrt(x * x + y * y + z * z) || 1;
    return [x / length, y / length, z / length];
  }

  // Classify bounded display texels only; never alter the authoritative cut
  // mask. A disconnected island has its own outer void, even when that void
  // is enclosed by another piece. Diagonal void contacts are conservatively
  // exterior, matching the preview's refusal to invent sub-grid holes.
  function displayBoundaryRegions(coverage, width, height) {
    var count = width * height, voids = new Int32Array(count), solids = new Int32Array(count);
    var queue = new Uint32Array(count), exterior = [true], outer = [0], voidCount = 0, solidCount = 0, hasHole = false;
    var i, head, tail, p, x, y, xx, yy, q, dx, dy;
    for (i = 0; i < count; i++) if (coverage[i] < 128 && !voids[i]) {
      var label = ++voidCount, border = false; head = 0; tail = 0; queue[tail++] = i; voids[i] = label;
      while (head < tail) {
        p = queue[head++]; x = p % width; y = Math.floor(p / width);
        if (!x || !y || x === width - 1 || y === height - 1) border = true;
        for (dy = -1; dy <= 1; dy++) for (dx = -1; dx <= 1; dx++) {
          xx = x + dx; yy = y + dy; if ((!dx && !dy) || xx < 0 || yy < 0 || xx >= width || yy >= height) continue;
          q = yy * width + xx;
          if (coverage[q] < 128 && !voids[q]) { voids[q] = label; queue[tail++] = q; }
        }
      }
      exterior[label] = border; if (!border) hasHole = true;
    }
    // Keep the established no-hole display field bit-for-bit unchanged.
    if (!hasHole) return null;
    for (i = 0; i < count; i++) if (coverage[i] >= 128 && !solids[i]) {
      var component = ++solidCount; head = 0; tail = 0; queue[tail++] = i; solids[i] = component;
      // Scan order makes i this component's topmost texel. The void directly
      // above it therefore belongs to its outer silhouette, not an eyelet.
      var aboveVoid = i < width ? 0 : voids[i - width];
      outer[component] = exterior[aboveVoid] ? 0 : aboveVoid;
      while (head < tail) {
        p = queue[head++]; x = p % width; y = Math.floor(p / width);
        for (var direction = 0; direction < 4; direction++) {
          xx = x + (direction === 0 ? -1 : direction === 1 ? 1 : 0);
          yy = y + (direction === 2 ? -1 : direction === 3 ? 1 : 0);
          if (xx < 0 || yy < 0 || xx >= width || yy >= height) continue;
          q = yy * width + xx;
          if (coverage[q] >= 128 && !solids[q]) { solids[q] = component; queue[tail++] = q; }
        }
      }
    }
    return { voids: voids, solids: solids, exterior: exterior, outer: outer, queue: queue };
  }

  // One bounded, view-only field drives both displaced vertices and lighting.
  // A modest alpha-aware Gaussian bridges pixel grain; a C2 shoulder returns
  // the surface to the independently controlled rim at outer silhouettes.
  // A drilled interior cut retains the local surface all the way to its wall.
  // The original depth (including dark recessed marks) is never rewritten.
  function prepareDisplaySurface(rawData, cap) {
    var data = validateData(rawData), bounds = boundsOf(data);
    cap = cap === undefined ? 1024 : cap;
    if (!finite(cap) || cap % 1 || cap < 8 || cap > MAX_TEXTURE) throw new Error("Display surface cap must be between 8 and 2048.");
    var ratio = Math.min(1, cap / Math.max(data.width, data.height));
    var width = Math.max(1, Math.round(data.width * ratio)), height = Math.max(1, Math.round(data.height * ratio));
    var count = width * height, heights = new Float32Array(count), filtered = new Float32Array(count);
    var rimWeights = (data.support || data.shape_alpha) && !ceramicData(data) ? new Float32Array(count) : null;
    var coverage = new Uint8Array(count), opaque = new Uint8Array(count), distance = new Float32Array(count);
    var dx = data.width / width, dy = data.height / height, diagonal = Math.sqrt(dx * dx + dy * dy);
    var divisor = data.depth instanceof Uint16Array ? 65535 : 1, x, y, i, sx, sy;
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      i = y * width + x; sx = (x + .5) * dx - .5; sy = (y + .5) * dy - .5;
      coverage[i] = Math.round(sample(data, sx, sy, 3));
      if (rimWeights) rimWeights[i] = (1 - sample(data, sx, sy, 4) / 255) * (data.shape_alpha ? sample(data, sx, sy, 5) / 255 : 1);
      // Normalize bilinear interpolation over opaque depth pixels so an
      // antialias fringe / black export background cannot form a false trench.
      var ix = Math.floor(sx), iy = Math.floor(sy), fx = sx - ix, fy = sy - iy, sum = 0, weight = 0;
      for (var j = 0; j < 2; j++) for (var k = 0; k < 2; k++) {
        var px = clamp(ix + k, 0, data.width - 1), py = clamp(iy + j, 0, data.height - 1), index = py * data.width + px;
        if (alphaAt(data, index) < 250) continue;
        var w = (k ? fx : 1 - fx) * (j ? fy : 1 - fy);
        sum += data.depth[index] * w; weight += w;
      }
      heights[i] = weight > 1e-8 ? sum / (weight * divisor) : sample(data, sx, sy, -1);
      opaque[i] = weight > 1e-8 ? 1 : 0;
      distance[i] = coverage[i] >= 128 ? 1e20 : 0;
    }
    var kernel = [1, 4, 6, 4, 1];
    for (var pass = 0; pass < 2; pass++) {
      for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
        var total = 0, weights = 0;
        for (var offset = -2; offset <= 2; offset++) {
          var xx = pass === 0 ? clamp(x + offset, 0, width - 1) : x;
          var yy = pass === 1 ? clamp(y + offset, 0, height - 1) : y;
          var at = yy * width + xx;
          if (opaque[at]) { total += heights[at] * kernel[offset + 2]; weights += kernel[offset + 2]; }
        }
        filtered[y * width + x] = weights ? total / weights : heights[y * width + x];
      }
      var swap = heights; heights = filtered; filtered = swap;
    }
    var regions = displayBoundaryRegions(coverage, width, height);
    // Fractional seeds locate the 50% alpha boundary between texel centres,
    // including the empty ghost ring beyond an image that touches its edges.
    function seed(nx, ny, value, spacing, component) {
      var a = nx < 0 || ny < 0 || nx >= width || ny >= height ? 0 : coverage[ny * width + nx];
      if (regions && a < 128) {
        var voidId = nx < 0 || ny < 0 || nx >= width || ny >= height ? 0 : regions.voids[ny * width + nx];
        if (regions.exterior[voidId]) voidId = 0;
        if (voidId !== regions.outer[component]) return 1e20;
      }
      return a < 128 ? spacing * (value - 127.5) / Math.max(1, value - a) : 1e20;
    }
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      i = y * width + x; if (coverage[i] < 128) continue;
      var a = coverage[i];
      var component = regions ? regions.solids[i] : 0;
      distance[i] = Math.min(seed(x - 1, y, a, dx, component), seed(x + 1, y, a, dx, component), seed(x, y - 1, a, dy, component), seed(x, y + 1, a, dy, component),
        seed(x - 1, y - 1, a, diagonal, component), seed(x + 1, y - 1, a, diagonal, component), seed(x - 1, y + 1, a, diagonal, component), seed(x + 1, y + 1, a, diagonal, component));
    }
    function propagated(index, from) { return regions && (!regions.solids[from] || regions.solids[index] !== regions.solids[from]) ? 1e20 : distance[from]; }
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      i = y * width + x; if (coverage[i] < 128) continue;
      var d = distance[i];
      if (x > 0) d = Math.min(d, propagated(i, i - 1) + dx);
      if (y > 0) {
        d = Math.min(d, propagated(i, i - width) + dy);
        if (x > 0) d = Math.min(d, propagated(i, i - width - 1) + diagonal);
        if (x + 1 < width) d = Math.min(d, propagated(i, i - width + 1) + diagonal);
      }
      distance[i] = d;
    }
    for (y = height - 1; y >= 0; y--) for (x = width - 1; x >= 0; x--) {
      i = y * width + x; if (coverage[i] < 128) continue;
      var near = distance[i];
      if (x + 1 < width) near = Math.min(near, propagated(i, i + 1) + dx);
      if (y + 1 < height) {
        near = Math.min(near, propagated(i, i + width) + dy);
        if (x > 0) near = Math.min(near, propagated(i, i + width - 1) + diagonal);
        if (x + 1 < width) near = Math.min(near, propagated(i, i + width + 1) + diagonal);
      }
      distance[i] = near;
    }
    var shoulder = Math.max(2 * Math.max(dx, dy), (bounds.y1 - bounds.y0) * .0125);
    if (rimWeights) {
      // Only an explicit synthetic-support mask suppresses decorative rim
      // lift. A broad alpha-aware join avoids a new hard step where support
      // meets the print; genuine white art and source RGB are never classified.
      var rimFiltered = new Float32Array(count), radius = Math.min(16, Math.max(2, Math.ceil(shoulder / Math.max(dx, dy) * .6)));
      for (var rp = 0; rp < 2; rp++) {
        for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
          var rimSum = 0, rimWeight = 0;
          for (var ro = -radius; ro <= radius; ro++) {
            var rx = rp === 0 ? clamp(x + ro, 0, width - 1) : x;
            var ry = rp === 1 ? clamp(y + ro, 0, height - 1) : y;
            var ri = ry * width + rx;
            if (coverage[ri] < 128) continue;
            var rw = Math.exp(-2 * ro * ro / (radius * radius));
            rimSum += rimWeights[ri] * rw; rimWeight += rw;
          }
          rimFiltered[y * width + x] = rimWeight ? rimSum / rimWeight : rimWeights[y * width + x];
        }
        var rimSwap = rimWeights; rimWeights = rimFiltered; rimFiltered = rimSwap;
      }
    }
    for (i = 0; i < count; i++) {
      var t = coverage[i] >= 128 ? clamp(distance[i] / shoulder, 0, 1) : 0;
      var smooth = t * t * t * (t * (t * 6 - 15) + 10);
      heights[i] *= smooth;
      if (data.shape_alpha && !ceramicData(data)) {
        var sourceX = (i % width + .5) * dx - .5, sourceY = (Math.floor(i / width) + .5) * dy - .5;
        // Print transparency controls color, never authored white relief.
        // Keep an actual zero-height support area flat after display smoothing;
        // suppress only decorative ink-rim lift where there is no print.
        if (sample(data, sourceX, sourceY, -1) === 0) heights[i] = 0;
        if (sample(data, sourceX, sourceY, 5) === 0 && rimWeights) rimWeights[i] = 0;
      }
    }
    if (regions) {
      // Extend only the view-height field across a drilled void so bilinear
      // samples at its exact alpha contour do not blend with a fictitious
      // zero-depth floor. The void still has zero coverage and no face mesh.
      // An island's outer boundary seeds zero and retains its existing taper.
      var filled = new Uint8Array(count), queue = regions.queue, head = 0, tail = 0;
      for (i = 0; i < count; i++) if (coverage[i] < 128 && !regions.exterior[regions.voids[i]]) {
        x = i % width; y = Math.floor(i / width); var fillSum = 0, rimSum = 0, fillCount = 0, outerSide = false;
        for (var direction = 0; direction < 4; direction++) {
          var xx = x + (direction === 0 ? -1 : direction === 1 ? 1 : 0), yy = y + (direction === 2 ? -1 : direction === 3 ? 1 : 0);
          if (xx < 0 || yy < 0 || xx >= width || yy >= height) continue;
          var q = yy * width + xx;
          if (!regions.solids[q]) continue;
          if (regions.outer[regions.solids[q]] === regions.voids[i]) outerSide = true;
          fillSum += heights[q]; if (rimWeights) rimSum += rimWeights[q]; fillCount++;
        }
        if (fillCount) {
          heights[i] = outerSide ? 0 : fillSum / fillCount;
          if (rimWeights) rimWeights[i] = outerSide ? 0 : rimSum / fillCount;
          filled[i] = 1; queue[tail++] = i;
        }
      }
      while (head < tail) {
        var p = queue[head++]; x = p % width; y = Math.floor(p / width);
        for (var direction = 0; direction < 4; direction++) {
          var xx = x + (direction === 0 ? -1 : direction === 1 ? 1 : 0), yy = y + (direction === 2 ? -1 : direction === 3 ? 1 : 0);
          if (xx < 0 || yy < 0 || xx >= width || yy >= height) continue;
          var q = yy * width + xx;
          if (filled[q] || coverage[q] >= 128 || regions.voids[q] !== regions.voids[p]) continue;
          filled[q] = 1; heights[q] = heights[p]; if (rimWeights) rimWeights[q] = rimWeights[p]; queue[tail++] = q;
        }
      }
      regions.queue = null;
    }
    return { width: width, height: height, heights: heights, coverage: coverage,
      sourceWidth: data.width, sourceHeight: data.height, sourceDepth: data.depth, sourceAlpha: data.alpha || data.rgba,
      sourceSupport: data.support, sourceShape: data.shape_alpha, product_type: productType(data.product_type),
      rimWeights: rimWeights, distance: distance, boundaryRegions: regions, bounds: bounds, shoulderPixels: shoulder, viewOnly: true };
  }
  function displaySurfaceFor(data, value) {
    if (!value) return prepareDisplaySurface(data);
    if (value.sourceWidth !== data.width || value.sourceHeight !== data.height || value.sourceDepth !== data.depth || value.sourceAlpha !== (data.alpha || data.rgba) || value.sourceSupport !== data.support
        || value.sourceShape !== data.shape_alpha || value.product_type !== productType(data.product_type)
        || !(value.heights instanceof Float32Array) || value.heights.length !== value.width * value.height) {
      throw new Error("Display surface does not belong to this native depth and alpha.");
    }
    return value;
  }
  function displayHeight(surface, sx, sy) {
    var x = (sx + .5) * surface.width / surface.sourceWidth - .5;
    var y = (sy + .5) * surface.height / surface.sourceHeight - .5;
    var ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, sum = 0;
    for (var j = 0; j < 2; j++) for (var i = 0; i < 2; i++) {
      var px = ix + i, py = iy + j;
      if (px >= 0 && py >= 0 && px < surface.width && py < surface.height) {
        sum += surface.heights[py * surface.width + px] * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
      }
    }
    return sum;
  }
  function displayRimWeight(surface, sx, sy) {
    if (!surface.rimWeights) return 1;
    var x = clamp((sx + .5) * surface.width / surface.sourceWidth - .5, 0, surface.width - 1);
    var y = clamp((sy + .5) * surface.height / surface.sourceHeight - .5, 0, surface.height - 1);
    var ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, sum = 0;
    for (var j = 0; j < 2; j++) for (var i = 0; i < 2; i++) {
      var px = Math.min(ix + i, surface.width - 1), py = Math.min(iy + j, surface.height - 1);
      sum += surface.rimWeights[py * surface.width + px] * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
    }
    return sum;
  }
  function displayDistance(surface, sx, sy) {
    var x = (sx + .5) * surface.width / surface.sourceWidth - .5;
    var y = (sy + .5) * surface.height / surface.sourceHeight - .5;
    var ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, sum = 0;
    for (var j = 0; j < 2; j++) for (var i = 0; i < 2; i++) {
      var px = ix + i, py = iy + j;
      if (px >= 0 && py >= 0 && px < surface.width && py < surface.height) {
        sum += surface.distance[py * surface.width + px] * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
      }
    }
    return sum;
  }

  function buildGeometry(rawData, physical, options) {
    options = options || {};
    var data = rawData;
    if (options.product_type !== undefined) {
      if (data.product_type !== undefined && data.product_type !== options.product_type) throw new Error("Geometry product_type does not match its data.");
      data = withProduct(data, options.product_type);
    }
    data = validateData(data);
    var dim = dimensions(physical), bounds = boundsOf(data), ceramic = ceramicData(data);
    var limit = options.maxGrid === undefined ? DEFAULT_GRID : options.maxGrid;
    if (!finite(limit) || limit % 1 || limit < 8 || limit > MAX_GRID) throw new Error("maxGrid must be an integer between 8 and 512.");
    var reliefBoost = options.reliefBoost === undefined ? 1 : options.reliefBoost;
    if (!finite(reliefBoost) || reliefBoost < 0 || reliefBoost > 12) throw new Error("reliefBoost must be between 0 and 12.");
    var separated = ceramic || options.rimBoost !== undefined, rimBoost = ceramic ? 0 : (separated ? options.rimBoost : 0);
    if (!finite(rimBoost) || rimBoost < 0 || rimBoost > 12) throw new Error("rimBoost must be between 0 and 12.");
    var displaySurface = separated ? displaySurfaceFor(data, options.displaySurface) : null;
    // The illustrative relief has its own gain. The white acrylic foundation
    // stays fixed even when a mockup deliberately exaggerates the design.
    var baseTop = 1.5 - dim.reliefMm, rimTop = baseTop + dim.reliefMm * rimBoost, reliefScale = dim.reliefMm * reliefBoost;
    var heightPrecision = new Float32Array(1); heightPrecision[0] = baseTop;
    var meshBaseTop = heightPrecision[0], minZ = -1.5, maxZ = meshBaseTop;
    var bw = bounds.x1 - bounds.x0, bh = bounds.y1 - bounds.y0;
    var ratio = Math.min(1, limit / Math.max(bw, bh));
    var cols = Math.max(1, Math.round(bw * ratio)), rows = Math.max(1, Math.round(bh * ratio));
    var dx = bw / cols, dy = bh / rows, mm = dim.heightMm / bh;
    // A bounded quarter-round is cut inward from the authoritative ceramic
    // silhouette, never grown beyond it. Both faces join the white side with
    // horizontal normals. Black depth remains the flat substrate, not a hole.
    var bevelRadius = ceramic ? (3 - dim.reliefMm) * .48 : 0;
    // The printed relief grows continuously from the same cut edge as the
    // roundover. Starting only after the arc leaves a flat shelf followed by
    // a raised lip; a wider gain-aware join keeps that rise gradual instead.
    // This affects the preview only, never native depth or the base radius.
    var ceramicJoinWidth = ceramic ? Math.max(bevelRadius * 3, bevelRadius + reliefScale * 1.5) : 0;
    function bevelDrop(sx, sy, boundaryPoint) {
      if (!ceramic) return 0;
      var d = boundaryPoint ? 0 : clamp(displayDistance(displaySurface, sx, sy) * mm, 0, bevelRadius);
      return bevelRadius - Math.sqrt(Math.max(0, bevelRadius * bevelRadius - (bevelRadius - d) * (bevelRadius - d)));
    }
    // Sample pixel centres, with an empty ghost ring. Clipping the triangles
    // against the 50% alpha contour gives diagonal/sub-pixel edges instead of
    // extruding a staircase of occupied square cells.
    var x, y, occupied = 0, nodeWidth = cols + 2, values = new Float32Array((cols + 2) * (rows + 2));
    function sourceX(gx) { return bounds.x0 + (gx - 0.5) * dx - 0.5; }
    function sourceY(gy) { return bounds.y0 + (gy - 0.5) * dy - 0.5; }
    function signedAlpha(alpha) {
      var value = alpha - 127.5;
      // An exact saddle must not become a non-manifold point contact. This
      // sub-pixel bias is far smaller than one native alpha level.
      return Math.abs(value) < 0.05 ? -0.05 : value;
    }
    function contourAlpha(sx, sy) {
      // Unlike texture sampling, the silhouette must see empty pixels beyond
      // the image, not CLAMP_TO_EDGE copies of an opaque border pixel.
      var ix = Math.floor(sx), iy = Math.floor(sy), fx = sx - ix, fy = sy - iy, value = 0, i, j;
      for (j = 0; j < 2; j++) for (i = 0; i < 2; i++) {
        var px = ix + i, py = iy + j;
        if (px >= 0 && py >= 0 && px < data.width && py < data.height) {
          value += alphaAt(data, py * data.width + px) * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
        }
      }
      return signedAlpha(value);
    }
    for (y = 0; y < rows + 2; y++) for (x = 0; x < cols + 2; x++) {
      var value = x === 0 || y === 0 || x === cols + 1 || y === rows + 1
        ? -127.5 : signedAlpha(sample(data, sourceX(x), sourceY(y), 3));
      values[y * nodeWidth + x] = value;
      if (value > 0) occupied++;
    }
    if (!occupied) throw new Error("Silhouette is thinner than the interactive mesh resolution.");

    function foregroundDepth(sx, sy) {
      // Exported depth has a black background (and may have premultiplied
      // antialias pixels). Neither belongs in a surface slope. Bilinear
      // interpolation is normalized over opaque foreground only.
      sx = clamp(sx, 0, data.width - 1); sy = clamp(sy, 0, data.height - 1);
      var ix = Math.floor(sx), iy = Math.floor(sy), fx = sx - ix, fy = sy - iy;
      var weight = 0, sum = 0, i, j, px, py, index, w, divisor = data.depth instanceof Uint16Array ? 65535 : 1;
      for (j = 0; j < 2; j++) for (i = 0; i < 2; i++) {
        px = Math.min(ix + i, data.width - 1); py = Math.min(iy + j, data.height - 1);
        index = py * data.width + px;
        if (alphaAt(data, index) < 250) continue;
        w = (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
        sum += data.depth[index] * w; weight += w;
      }
      if (weight > 1e-8) return sum / (weight * divisor);
      // Only contour/normal samples need this bounded nearest interior search.
      // Do not pull a zero-alpha background value onto the top of a white wall.
      var radius = Math.max(2, Math.ceil(Math.max(dx, dy) * 1.6)), best = Infinity, nearest = 0, found = false;
      for (j = Math.max(0, iy - radius); j <= Math.min(data.height - 1, iy + radius); j++) {
        for (i = Math.max(0, ix - radius); i <= Math.min(data.width - 1, ix + radius); i++) {
          index = j * data.width + i;
          if (alphaAt(data, index) < 250) continue;
          var distance = (i - sx) * (i - sx) + (j - sy) * (j - sy);
          if (distance < best) { best = distance; nearest = data.depth[index] / divisor; found = true; }
        }
      }
      if (found) return nearest;
      // A genuinely translucent-only island has no opaque interior. Retain its
      // bounded native height instead of inventing a tall rim.
      return sample(data, sx, sy, -1);
    }
    var vertices = [], indices = [], cornerMap = Object.create(null), edges = Object.create(null), corners = [];
    function addVertex(px, py, pz, nx, ny, nz, u, v, material) {
      var index = vertices.length / STRIDE;
      vertices.push(px, py, pz, nx, ny, nz, u, v, material); return index;
    }
    function corner(gx, gy, boundaryPoint) {
      var key = gx.toFixed(8) + ":" + gy.toFixed(8), old = cornerMap[key];
      if (old !== undefined) return old;
      var sx = sourceX(gx), sy = sourceY(gy);
      heightPrecision[0] = separated ? baseTop + dim.reliefMm * rimBoost * displayRimWeight(displaySurface, sx, sy) + reliefScale * (boundaryPoint ? 0 : displayHeight(displaySurface, sx, sy))
        : baseTop + reliefScale * foregroundDepth(sx, sy);
      var rounding = bevelDrop(sx, sy, boundaryPoint);
      if (ceramic) heightPrecision[0] -= rounding;
      var z = heightPrecision[0];
      maxZ = Math.max(maxZ, z);
      var px = ((sx + 0.5) - (bounds.x0 + bounds.x1) / 2) * mm;
      var py = ((bounds.y0 + bounds.y1) / 2 - (sy + 0.5)) * mm;
      var dzdx = separated ? reliefScale * (displayHeight(displaySurface, sx + dx, sy) - displayHeight(displaySurface, sx - dx, sy)) / (2 * dx * mm)
        : reliefScale * (foregroundDepth(sx + dx, sy) - foregroundDepth(sx - dx, sy)) / (2 * dx * mm);
      var dzdy = separated ? -reliefScale * (displayHeight(displaySurface, sx, sy + dy) - displayHeight(displaySurface, sx, sy - dy)) / (2 * dy * mm)
        : -reliefScale * (foregroundDepth(sx, sy + dy) - foregroundDepth(sx, sy - dy)) / (2 * dy * mm);
      if (separated && boundaryPoint) dzdx = dzdy = 0;
      if (separated && displaySurface.rimWeights) {
        dzdx += dim.reliefMm * rimBoost * (displayRimWeight(displaySurface, sx + dx, sy) - displayRimWeight(displaySurface, sx - dx, sy)) / (2 * dx * mm);
        dzdy -= dim.reliefMm * rimBoost * (displayRimWeight(displaySurface, sx, sy + dy) - displayRimWeight(displaySurface, sx, sy - dy)) / (2 * dy * mm);
      }
      var bevelGX = 0, bevelGY = 0;
      if (ceramic) {
        var epsilon = Math.max(.1, Math.min(dx, dy) * .3);
        bevelGX = -(bevelDrop(sx + epsilon, sy, false) - bevelDrop(sx - epsilon, sy, false)) / (2 * epsilon * mm);
        bevelGY = (bevelDrop(sx, sy + epsilon, false) - bevelDrop(sx, sy - epsilon, false)) / (2 * epsilon * mm);
        dzdx += bevelGX; dzdy += bevelGY;
      }
      var n = normalise(-dzdx, -dzdy, 1), u = (sx + 0.5) / data.width, v = (sy + 0.5) / data.height;
      var edgeWeight = ceramic ? .4 * clamp(1 - displayDistance(displaySurface, sx, sy) * mm / (bevelRadius * 1.5), 0, 1) : 0;
      if (ceramic && boundaryPoint) edgeWeight = .4;
      var backN = ceramic ? normalise(-bevelGX, -bevelGY, -1) : [0, 0, -1];
      var front = addVertex(px, py, z, n[0], n[1], n[2], u, v, edgeWeight);
      var back = addVertex(px, py, -1.5 + rounding, backN[0], backN[1], backN[2], u, v, 1);
      old = corners.length; corners.push({ front: front, back: back, x: px, y: py, z: z, u: u, v: v, sx: sx, sy: sy }); cornerMap[key] = old;
      return old;
    }
    function edge(a, b) {
      var key = Math.min(a, b) + ":" + Math.max(a, b);
      if (edges[key]) edges[key].count++;
      else edges[key] = { a: a, b: b, count: 1 };
    }
    function emitPolygon(polygon) {
      if (polygon.length < 3) return;
      var ids = [], i;
      for (i = 0; i < polygon.length; i++) ids.push(corner(polygon[i][0], polygon[i][1], polygon[i][2] === 0));
      for (i = 1; i < ids.length - 1; i++) {
        indices.push(corners[ids[0]].front, corners[ids[i]].front, corners[ids[i + 1]].front);
        indices.push(corners[ids[0]].back, corners[ids[i + 1]].back, corners[ids[i]].back);
      }
      for (i = 0; i < ids.length; i++) edge(ids[i], ids[(i + 1) % ids.length]);
    }
    function clipTriangle(points) {
      var result = [], i, a, b, t;
      for (i = 0; i < points.length; i++) {
        a = points[i]; b = points[(i + 1) % points.length];
        if (a[2] > 0) result.push(a);
        if ((a[2] > 0) !== (b[2] > 0)) {
          t = a[2] / (a[2] - b[2]);
          var actualA = contourAlpha(sourceX(a[0]), sourceY(a[1])), actualB = contourAlpha(sourceX(b[0]), sourceY(b[1]));
          if ((actualA > 0) === (a[2] > 0) && (actualB > 0) === (b[2] > 0)) {
            // Find the native alpha crossing, not the midpoint of two distant
            // binary mesh samples. Shared edges use the same dyadic bisection.
            var low = 0, high = 1;
            for (var step = 0; step < 10; step++) {
              var midT = (low + high) / 2;
              var midAlpha = contourAlpha(sourceX(a[0] + (b[0] - a[0]) * midT), sourceY(a[1] + (b[1] - a[1]) * midT));
              if ((midAlpha > 0) === (a[2] > 0)) low = midT; else high = midT;
            }
            t = (low + high) / 2;
          }
          result.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, 0]);
        }
      }
      emitPolygon(result);
    }
    for (y = 0; y < rows + 1; y++) for (x = 0; x < cols + 1; x++) {
      var nodes = [[x, y, values[y * nodeWidth + x]], [x, y + 1, values[(y + 1) * nodeWidth + x]],
        [x + 1, y + 1, values[(y + 1) * nodeWidth + x + 1]], [x + 1, y, values[y * nodeWidth + x + 1]]];
      var count = 0, i;
      for (i = 0; i < 4; i++) if (nodes[i][2] > 0) count++;
      if (!count) continue;
      if (count === 4) { emitPolygon(nodes); continue; }
      // The native centre resolves diagonal saddles and curved cutlines. An
      // exact 50% saddle stays separate rather than becoming a pinch vertex.
      var centre = contourAlpha(sourceX(x + 0.5), sourceY(y + 0.5));
      var mid = [x + 0.5, y + 0.5, centre];
      for (i = 0; i < 4; i++) clipTriangle([nodes[i], nodes[(i + 1) % 4], mid]);
    }
    // The face winding gives closed drilled contours the opposite signed
    // area from each solid island's outer perimeter. Never infer an eyelet
    // from its size, location, print brightness, or nearest edge distance.
    // Ambiguous/open/non-manifold contours conservatively keep the old edge.
    var outgoing = Object.create(null), incoming = Object.create(null), boundaryKeys = [], visitedCut = Object.create(null), straightHoleCount = 0;
    for (var classifyKey in edges) if (Object.prototype.hasOwnProperty.call(edges, classifyKey) && edges[classifyKey].count === 1) {
      var classifyEdge = edges[classifyKey]; boundaryKeys.push(classifyKey);
      if (!outgoing[classifyEdge.a]) outgoing[classifyEdge.a] = [];
      if (!incoming[classifyEdge.b]) incoming[classifyEdge.b] = [];
      outgoing[classifyEdge.a].push(classifyKey); incoming[classifyEdge.b].push(classifyKey);
    }
    for (var bk = 0; bk < boundaryKeys.length; bk++) {
      var firstKey = boundaryKeys[bk]; if (visitedCut[firstKey]) continue;
      var loopKeys = [], cursorKey = firstKey, closedCut = false, signedArea = 0;
      while (!visitedCut[cursorKey]) {
        visitedCut[cursorKey] = true; loopKeys.push(cursorKey);
        var e = edges[cursorKey], ca = corners[e.a], cb = corners[e.b];
        signedArea += ca.x * cb.y - cb.x * ca.y;
        if (!outgoing[e.a] || outgoing[e.a].length !== 1 || !incoming[e.a] || incoming[e.a].length !== 1
          || !outgoing[e.b] || outgoing[e.b].length !== 1 || !incoming[e.b] || incoming[e.b].length !== 1) break;
        cursorKey = outgoing[e.b][0];
        if (cursorKey === firstKey) { closedCut = true; break; }
      }
      if (!closedCut || loopKeys.length < 3 || signedArea >= -1e-10) continue;
      straightHoleCount++;
      for (var lk = 0; lk < loopKeys.length; lk++) {
        var holeEdge = edges[loopKeys[lk]]; holeEdge.hole = true; holeEdge.holeLoop = straightHoleCount;
        corners[holeEdge.a].hole = true; corners[holeEdge.b].hole = true;
      }
    }
    if (separated && straightHoleCount) for (var hc = 0; hc < corners.length; hc++) if (corners[hc].hole) {
      var hp = corners[hc];
      heightPrecision[0] = baseTop + dim.reliefMm * rimBoost * displayRimWeight(displaySurface, hp.sx, hp.sy)
        + reliefScale * displayHeight(displaySurface, hp.sx, hp.sy);
      hp.z = heightPrecision[0]; maxZ = Math.max(maxZ, hp.z);
      vertices[hp.front * STRIDE + 2] = hp.z;
      var hgx = reliefScale * (displayHeight(displaySurface, hp.sx + dx, hp.sy) - displayHeight(displaySurface, hp.sx - dx, hp.sy)) / (2 * dx * mm);
      var hgy = -reliefScale * (displayHeight(displaySurface, hp.sx, hp.sy + dy) - displayHeight(displaySurface, hp.sx, hp.sy - dy)) / (2 * dy * mm);
      if (displaySurface.rimWeights) {
        hgx += dim.reliefMm * rimBoost * (displayRimWeight(displaySurface, hp.sx + dx, hp.sy) - displayRimWeight(displaySurface, hp.sx - dx, hp.sy)) / (2 * dx * mm);
        hgy -= dim.reliefMm * rimBoost * (displayRimWeight(displaySurface, hp.sx, hp.sy + dy) - displayRimWeight(displaySurface, hp.sx, hp.sy - dy)) / (2 * dy * mm);
      }
      var hn = normalise(-hgx, -hgy, 1);
      for (var hnComponent = 0; hnComponent < 3; hnComponent++) vertices[hp.front * STRIDE + 3 + hnComponent] = hn[hnComponent];
    }
    var boundaryEdges = 0, key, sideNormals = Object.create(null);
    var ceramicNeighbours = ceramic ? Object.create(null) : null;
    // Average the two contour segment normals at each shared endpoint. The
    // acrylic edge is a continuous polished wall, not individually lit teeth.
    function edgeUV(point) {
      // The contour itself is at half alpha. Sample its nearest opaque print
      // pixel on the coloured raised wall, not transparent RGB/white fringes.
      if (sample(data, point.sx, point.sy, 3) >= 250) return [point.u, point.v];
      var radius = Math.max(2, Math.ceil(Math.max(dx, dy) * 1.6));
      var ix = Math.floor(point.sx), iy = Math.floor(point.sy), best = Infinity, uv = [point.u, point.v];
      for (var py = Math.max(0, iy - radius); py <= Math.min(data.height - 1, iy + radius); py++) {
        for (var px = Math.max(0, ix - radius); px <= Math.min(data.width - 1, ix + radius); px++) {
          if (alphaAt(data, py * data.width + px) < 250) continue;
          var distance = (px - point.sx) * (px - point.sx) + (py - point.sy) * (py - point.sy);
          if (distance < best) { best = distance; uv = [(px + 0.5) / data.width, (py + 0.5) / data.height]; }
        }
      }
      return uv;
    }
    for (key in edges) if (Object.prototype.hasOwnProperty.call(edges, key) && edges[key].count === 1) {
      var boundary = edges[key], ca = corners[boundary.a], cb = corners[boundary.b];
      var normal = normalise(cb.y - ca.y, ca.x - cb.x, 0);
      var ends = [boundary.a, boundary.b];
      for (var end = 0; end < 2; end++) {
        if (!sideNormals[ends[end]]) sideNormals[ends[end]] = [0, 0];
        sideNormals[ends[end]][0] += normal[0]; sideNormals[ends[end]][1] += normal[1];
      }
      if (ceramic) {
        var edgeLength = Math.sqrt((cb.x - ca.x) * (cb.x - ca.x) + (cb.y - ca.y) * (cb.y - ca.y));
        if (!ceramicNeighbours[boundary.a]) ceramicNeighbours[boundary.a] = [];
        if (!ceramicNeighbours[boundary.b]) ceramicNeighbours[boundary.b] = [];
        ceramicNeighbours[boundary.a].push({ id: boundary.b, length: edgeLength, normal: normal });
        ceramicNeighbours[boundary.b].push({ id: boundary.a, length: edgeLength, normal: normal });
      }
    }
    if (ceramic) {
      // Alpha contours contain short, alternating facets even when the ceramic
      // shape is a smooth curve. Averaging just the two incident unit normals
      // makes those facets visible as vertical ribs in the glaze. Filter the
      // normals by physical arc length, without moving any contour vertices.
      // Each closed perimeter is independent, so an eyelet never borrows the
      // outer edge's normals and keeps its correctly inward-facing wall.
      var visitedPerimeter = Object.create(null);
      var normalRadius = Math.min(dim.heightMm * .025, Math.max(dim.heightMm * .014, Math.max(dx, dy) * mm * 3));
      for (var startKey in ceramicNeighbours) if (Object.prototype.hasOwnProperty.call(ceramicNeighbours, startKey) && !visitedPerimeter[startKey]) {
        var startId = +startKey, loopIds = [], perimeter = 0, currentId = startId, previousId = -1, closed = false;
        while (!visitedPerimeter[currentId]) {
          visitedPerimeter[currentId] = true;
          var links = ceramicNeighbours[currentId];
          if (!links || links.length !== 2) break;
          loopIds.push(currentId);
          var nextLink = links[0].id === previousId ? links[1] : links[0];
          perimeter += nextLink.length;
          previousId = currentId; currentId = nextLink.id;
          if (currentId === startId) { closed = true; break; }
        }
        // Leave a malformed/non-manifold contour's original normals alone.
        if (!closed || loopIds.length < 3 || perimeter < 1e-8) continue;
        var radius = Math.min(normalRadius, perimeter * .125);
        for (var lp = 0; lp < loopIds.length; lp++) {
          var centreId = loopIds[lp], smoothX = 0, smoothY = 0;
          for (var direction = 0; direction < 2; direction++) {
            var walked = 0, fromId = centreId, link = ceramicNeighbours[centreId][direction];
            for (var walkStep = 0; walkStep < loopIds.length && walked < radius; walkStep++) {
              var finish = Math.min(radius, walked + link.length), covered = finish - walked;
              // Exact integral of the squared triangular kernel over this
              // segment. Tiny alpha facets cannot receive oversized weights;
              // the zero-slope cutoff also avoids a moving-window shimmer.
              var weight = covered * (1 - (walked + finish) / radius
                + (walked * walked + walked * finish + finish * finish) / (3 * radius * radius));
              smoothX += link.normal[0] * weight; smoothY += link.normal[1] * weight;
              walked = finish;
              var toId = link.id, onward = ceramicNeighbours[toId];
              link = onward[0].id === fromId ? onward[1] : onward[0];
              fromId = toId;
            }
          }
          if (smoothX * smoothX + smoothY * smoothY > 1e-16) sideNormals[centreId] = [smoothX, smoothY];
        }
      }
    }
    var roundoverRefinementPasses = 0;
    if (ceramic) {
      // A ceramic blank has a nearly semicircular (bullnose) edge, not a wide
      // vertical acrylic wall with a small bevel. Merely increasing its radius
      // on the relief lattice still leaves a planar last triangle at grazing
      // angles. Refine only this curved band, sharing every split edge between
      // adjacent triangles. The existing alpha contour and each eyelet stay
      // exactly where they were; no inset mask or artwork alpha is substituted.
      var cuts = [], cutBins = Object.create(null);
      var cutSearchRadius = ceramicJoinWidth + bevelRadius;
      var binSize = Math.max(cutSearchRadius, Math.max(dx, dy) * mm);
      for (var cutKey in edges) if (Object.prototype.hasOwnProperty.call(edges, cutKey) && edges[cutKey].count === 1 && !edges[cutKey].hole) {
        var cutEdge = edges[cutKey], cutA = corners[cutEdge.a], cutB = corners[cutEdge.b];
        var cutIndex = cuts.length;
        cuts.push({ a: cutEdge.a, b: cutEdge.b, x: cutA.x, y: cutA.y,
          dx: cutB.x - cutA.x, dy: cutB.y - cutA.y });
        for (var binY = Math.floor(Math.min(cutA.y, cutB.y) / binSize); binY <= Math.floor(Math.max(cutA.y, cutB.y) / binSize); binY++) {
          for (var binX = Math.floor(Math.min(cutA.x, cutB.x) / binSize); binX <= Math.floor(Math.max(cutA.x, cutB.x) / binSize); binX++) {
            var binKey = binX + ":" + binY;
            if (!cutBins[binKey]) cutBins[binKey] = [];
            cutBins[binKey].push(cutIndex);
          }
        }
      }
      function cutDistance(px, py) {
        var bx = Math.floor(px / binSize), by = Math.floor(py / binSize);
        var best = cutSearchRadius * cutSearchRadius, nearest = -1, nearT = 0;
        function visitBin(bxOffset, byOffset) {
          var candidates = cutBins[(bx + bxOffset) + ":" + (by + byOffset)];
          if (!candidates) return;
          for (var ci = 0; ci < candidates.length; ci++) {
            var candidateIndex = candidates[ci], cut = cuts[candidateIndex];
            var length2 = cut.dx * cut.dx + cut.dy * cut.dy;
            var t = length2 > 1e-20 ? clamp(((px - cut.x) * cut.dx + (py - cut.y) * cut.dy) / length2, 0, 1) : 0;
            var ex = px - cut.x - cut.dx * t, ey = py - cut.y - cut.dy * t, d2 = ex * ex + ey * ey;
            if (d2 < best) { best = d2; nearest = candidateIndex; nearT = t; }
          }
        }
        for (var byOffset = -1; byOffset <= 1; byOffset++) for (var bxOffset = -1; bxOffset <= 1; bxOffset++) visitBin(bxOffset, byOffset);
        // A bin is at least as wide as the complete geometry/lighting join;
        // these nine bins therefore include every segment within that band.
        var distance = Math.sqrt(best);
        if (distance < 1e-8) distance = 0;
        var nx = 0, ny = 0;
        if (nearest >= 0 && distance > 0) {
          var nearestCut = cuts[nearest];
          nx = (px - nearestCut.x - nearestCut.dx * nearT) / distance;
          ny = (py - nearestCut.y - nearestCut.dy * nearT) / distance;
        }
        return { distance: distance, cut: nearest, t: nearT, inwardX: nx, inwardY: ny,
          angle: Math.acos(clamp(1 - distance / bevelRadius, 0, 1)) };
      }
      function ceramicReliefJoin(distance) {
        // C2 from the cut edge, not a second shoulder after the base arc.
        // Zero endpoint derivatives keep the original wall tangent and join
        // untouched interior relief without a ridge or a flat printed ledge.
        var t = clamp(distance / ceramicJoinWidth, 0, 1);
        return { weight: t * t * t * (10 + t * (-15 + 6 * t)),
          slope: 30 * t * t * (1 - t) * (1 - t) / ceramicJoinWidth };
      }
      var faceTriangles = [];
      for (var fi = 0; fi < indices.length; fi += 6) faceTriangles.push([indices[fi] / 2, indices[fi + 1] / 2, indices[fi + 2] / 2]);
      for (var cp = 0; cp < corners.length; cp++) corners[cp].roundover = cutDistance(corners[cp].x, corners[cp].y);
      var angleStep = PI / 15, profileError = bevelRadius * .01, finishedTriangles = [], blockedEdges = Object.create(null);
      function profileHeight(info) { return bevelRadius * Math.sin(info.angle); }
      function faceEdgeKey(a, b) { return Math.min(a, b) + ":" + Math.max(a, b); }
      // A hole close to the outer round-over may cross its curved band. Do
      // not subdivide its exact contour without also splitting the wall:
      // retaining that edge avoids a face/wall T-junction at the through-cut.
      for (var holeKey in edges) if (Object.prototype.hasOwnProperty.call(edges, holeKey) && edges[holeKey].hole) {
        blockedEdges[faceEdgeKey(edges[holeKey].a, edges[holeKey].b)] = true;
      }
      for (var refinement = 0; refinement < 20; refinement++) {
        var splits = Object.create(null), splitCount = 0;
        // Splitting two long sides of a needle triangle also halves its tiny
        // third side, even if that third edge itself was not selected. Protect
        // all three shared edges of such a sliver before scheduling any split;
        // otherwise two new vertices can collapse to the same Float32 point.
        for (var guardFace = 0; guardFace < faceTriangles.length; guardFace++) {
          var guardIds = faceTriangles[guardFace], shortest2 = Infinity, longest2 = 0;
          for (var guardSide = 0; guardSide < 3; guardSide++) {
            var guardA = corners[guardIds[guardSide]], guardB = corners[guardIds[(guardSide + 1) % 3]];
            var guardDX = guardA.x - guardB.x, guardDY = guardA.y - guardB.y;
            shortest2 = Math.min(shortest2, guardDX * guardDX + guardDY * guardDY);
            longest2 = Math.max(longest2, guardDX * guardDX + guardDY * guardDY);
          }
          var ga = corners[guardIds[0]], gb = corners[guardIds[1]], gc = corners[guardIds[2]];
          var area2 = (gb.x - ga.x) * (gc.y - ga.y) - (gb.y - ga.y) * (gc.x - ga.x);
          if (shortest2 < bevelRadius * bevelRadius * .000001 || area2 * area2 < longest2 * bevelRadius * bevelRadius * .000001) {
            blockedEdges[faceEdgeKey(guardIds[0], guardIds[1])] = true;
            blockedEdges[faceEdgeKey(guardIds[1], guardIds[2])] = true;
            blockedEdges[faceEdgeKey(guardIds[2], guardIds[0])] = true;
          }
        }
        function splitEdge(ia, ib) {
          var id = Math.min(ia, ib) + ":" + Math.max(ia, ib);
          if (splits[id] !== undefined) return;
          if (blockedEdges[id]) { splits[id] = -1; return; }
          var pa = corners[ia], pb = corners[ib], da = pa.roundover, db = pb.roundover;
          var length = Math.sqrt((pa.x - pb.x) * (pa.x - pb.x) + (pa.y - pb.y) * (pa.y - pb.y));
          // Avoid chasing sub-micron alpha slivers into Float32-degenerate
          // triangles. This is far below one pixel even in a native export.
          if (length < bevelRadius * .001) { splits[id] = -1; return; }
          // Distance is 1-Lipschitz. Most interior edges cannot enter the rim;
          // avoid querying or subdividing the high-detail central relief.
          if (Math.min(da.distance, db.distance) - length * .5 >= bevelRadius) { splits[id] = -1; return; }
          var mx = (pa.x + pb.x) * .5, my = (pa.y + pb.y) * .5, dm = cutDistance(mx, my);
          if ((Math.fround(mx) === Math.fround(pa.x) && Math.fround(my) === Math.fround(pa.y))
            || (Math.fround(mx) === Math.fround(pb.x) && Math.fround(my) === Math.fround(pb.y))) { splits[id] = -1; return; }
          var angleChange = Math.max(Math.abs(da.angle - db.angle), Math.abs(da.angle - dm.angle), Math.abs(db.angle - dm.angle));
          var curvatureError = Math.abs(profileHeight(dm) - (profileHeight(da) + profileHeight(db)) * .5);
          if (angleChange <= angleStep && curvatureError <= profileError) { splits[id] = -1; return; }
          var sx = (pa.sx + pb.sx) * .5, sy = (pa.sy + pb.sy) * .5;
          splits[id] = corners.length;
          corners.push({ x: mx, y: my, sx: sx, sy: sy,
            u: (pa.u + pb.u) * .5, v: (pa.v + pb.v) * .5, roundover: dm });
          splitCount++;
        }
        for (var ft = 0; ft < faceTriangles.length; ft++) {
          var tri = faceTriangles[ft]; splitEdge(tri[0], tri[1]); splitEdge(tri[1], tri[2]); splitEdge(tri[2], tri[0]);
        }
        if (!splitCount) break;
        var refined = [];
        for (var rt = 0; rt < faceTriangles.length; rt++) {
          var tr = faceTriangles[rt], va = tr[0], vb = tr[1], vc = tr[2];
          var ab = splits[Math.min(va, vb) + ":" + Math.max(va, vb)], bc = splits[Math.min(vb, vc) + ":" + Math.max(vb, vc)], ca = splits[Math.min(vc, va) + ":" + Math.max(vc, va)];
          if (ab >= 0 && bc >= 0 && ca >= 0) refined.push([va, ab, ca], [ab, vb, bc], [ca, bc, vc], [ab, bc, ca]);
          else if (ab >= 0 && bc >= 0) refined.push([vb, bc, ab], [va, ab, vc], [ab, bc, vc]);
          else if (bc >= 0 && ca >= 0) refined.push([vc, ca, bc], [vb, bc, va], [bc, ca, va]);
          else if (ca >= 0 && ab >= 0) refined.push([va, ab, ca], [vc, ca, vb], [ca, ab, vb]);
          else if (ab >= 0) refined.push([va, ab, vc], [ab, vb, vc]);
          else if (bc >= 0) refined.push([vb, bc, va], [bc, vc, va]);
          else if (ca >= 0) refined.push([vc, ca, vb], [ca, va, vb]);
          else finishedTriangles.push(tr);
        }
        faceTriangles = refined; roundoverRefinementPasses++;
      }
      faceTriangles = finishedTriangles.concat(faceTriangles);
      // Analytic arc normals, rather than finite differences of a square-root
      // height on a coarse grid. At the shared cutline both faces meet the wall
      // tangentially; their planar interiors remain front/back facing. Smooth
      // contour normals are interpolated along the same independent perimeter.
      vertices = []; indices = []; maxZ = meshBaseTop;
      for (var cv = 0; cv < corners.length; cv++) {
        var p = corners[cv], info = p.roundover, radial = Math.cos(info.angle), vertical = Math.sin(info.angle);
        var outward = [0, 0, 0];
        if (info.cut >= 0 && radial > 1e-10) {
          var closest = cuts[info.cut], an = sideNormals[closest.a], bn = sideNormals[closest.b];
          var unitA = normalise(an[0], an[1], 0), unitB = normalise(bn[0], bn[1], 0);
          outward = normalise(unitA[0] * (1 - info.t) + unitB[0] * info.t, unitA[1] * (1 - info.t) + unitB[1] * info.t, 0);
        }
        var drop = bevelRadius * (1 - vertical), h = info.distance === 0 ? 0 : displayHeight(displaySurface, p.sx, p.sy);
        var join = ceramicReliefJoin(info.distance);
        var gx = reliefScale * (join.weight * (displayHeight(displaySurface, p.sx + dx, p.sy) - displayHeight(displaySurface, p.sx - dx, p.sy)) / (2 * dx * mm) + h * join.slope * info.inwardX);
        var gy = reliefScale * (-join.weight * (displayHeight(displaySurface, p.sx, p.sy + dy) - displayHeight(displaySurface, p.sx, p.sy - dy)) / (2 * dy * mm) + h * join.slope * info.inwardY);
        var nf = normalise(outward[0] * radial - gx * vertical, outward[1] * radial - gy * vertical, vertical);
        var nr = normalise(outward[0] * radial, outward[1] * radial, -vertical);
        heightPrecision[0] = baseTop + reliefScale * h * join.weight - drop; p.z = heightPrecision[0]; maxZ = Math.max(maxZ, p.z);
        // vMaterial also hands normals to the ceramic shader. Hold the mesh
        // normals through the complete relief join so unmasked normal maps
        // cannot put a false sharp shoulder back on the rounded front. Fade
        // to native print detail only after the displacement is unchanged.
        var lightingT = clamp((info.distance - ceramicJoinWidth) / bevelRadius, 0, 1);
        var lightingWeight = .4 * (1 - lightingT * lightingT * (3 - 2 * lightingT));
        p.front = addVertex(p.x, p.y, p.z, nf[0], nf[1], nf[2], p.u, p.v, lightingWeight);
        p.back = addVertex(p.x, p.y, -1.5 + drop, nr[0], nr[1], nr[2], p.u, p.v, 1);
      }
      for (var tf = 0; tf < faceTriangles.length; tf++) {
        var triangle = faceTriangles[tf], aa = corners[triangle[0]], bb = corners[triangle[1]], cc = corners[triangle[2]];
        indices.push(aa.front, bb.front, cc.front, aa.back, cc.back, bb.back);
      }
    }
    // A visible, local 1 mm front round-over matches the supplied soft lip.
    // Whole interpolated triangle support remains proven within 1.008 mm;
    // the bore contour, back face and all relief beyond that band stay put.
    var holeRimRadius = 1, holeRimTolerance = .008, holeRimMinEdge = .00008, holeRimAffectedRadius = 0, holeRimUnsupportedFaces = 0, holeRimDegenerateFaces = 0, holeRimRefinementPasses = 0, roundedHoleCount = 0, roundedHoleLoops = Object.create(null), holeRimNormals = Object.create(null);
    var circularHoleNormalCount = 0, holeRimNormalFitMaxError = 0, holeRimCircles = Object.create(null);
    if (straightHoleCount) {
      // Round only the local lip beside an already-proved enclosed cut. This is
      // a real local quarter-round, not a gain-dependent relief shoulder.
      // Keep an entire bore sharp if it intersects the established outer
      // round-over; two independently rounded boundaries must not pinch the
      // remaining bridge or change the approved outside silhouette.
      var holeSegments = [], outerSegments = [], rimBinSize = Math.max(holeRimRadius, Math.max(dx, dy) * mm);
      var outerLimit = ceramic ? bevelRadius : 0;
      function rimSegment(edge) {
        var a = corners[edge.a], b = corners[edge.b];
        return { a: edge.a, b: edge.b, x: a.x, y: a.y, dx: b.x - a.x, dy: b.y - a.y, loop: edge.holeLoop };
      }
      function segmentDistance2(segment, px, py) {
        var length2 = segment.dx * segment.dx + segment.dy * segment.dy;
        var t = length2 > 1e-20 ? clamp(((px - segment.x) * segment.dx + (py - segment.y) * segment.dy) / length2, 0, 1) : 0;
        var ex = px - segment.x - t * segment.dx, ey = py - segment.y - t * segment.dy;
        return { distance2: ex * ex + ey * ey, t: t };
      }
      for (var rimKey in edges) if (Object.prototype.hasOwnProperty.call(edges, rimKey) && edges[rimKey].count === 1) {
        var segment = rimSegment(edges[rimKey]);
        if (edges[rimKey].hole) { holeSegments.push(segment); roundedHoleLoops[segment.loop] = true; }
        else outerSegments.push(segment);
      }
      for (var hs = 0; hs < holeSegments.length; hs++) {
        var holeSegment = holeSegments[hs]; if (!roundedHoleLoops[holeSegment.loop]) continue;
        // Include each segment midpoint; the conservative half-length margin
        // also covers a near crossing between vertices on a coarse lattice.
        var length = Math.sqrt(holeSegment.dx * holeSegment.dx + holeSegment.dy * holeSegment.dy);
        var safe2 = Math.pow(outerLimit + holeRimRadius + length * .5, 2);
        for (var os = 0; os < outerSegments.length; os++) {
          if (segmentDistance2(outerSegments[os], holeSegment.x + holeSegment.dx * .5, holeSegment.y + holeSegment.dy * .5).distance2 <= safe2) {
            roundedHoleLoops[holeSegment.loop] = false; break;
          }
        }
      }
      for (var loopId in roundedHoleLoops) if (Object.prototype.hasOwnProperty.call(roundedHoleLoops, loopId) && roundedHoleLoops[loopId]) roundedHoleCount++;
      if (roundedHoleCount) {
        var rimCuts = [], rimBins = Object.create(null), rimVertexCuts = Object.create(null), rimSearch = holeRimRadius + rimBinSize * 2.83;
        rimBinSize = rimSearch;
        for (var hs = 0; hs < holeSegments.length; hs++) if (roundedHoleLoops[holeSegments[hs].loop]) {
          var segment = holeSegments[hs], index = rimCuts.length; rimCuts.push(segment);
          if (!rimVertexCuts[segment.a]) rimVertexCuts[segment.a] = [];
          if (!rimVertexCuts[segment.b]) rimVertexCuts[segment.b] = [];
          rimVertexCuts[segment.a].push(index); rimVertexCuts[segment.b].push(index);
          for (var by = Math.floor(Math.min(segment.y, segment.y + segment.dy) / rimBinSize); by <= Math.floor(Math.max(segment.y, segment.y + segment.dy) / rimBinSize); by++) {
            for (var bx = Math.floor(Math.min(segment.x, segment.x + segment.dx) / rimBinSize); bx <= Math.floor(Math.max(segment.x, segment.x + segment.dx) / rimBinSize); bx++) {
              var binKey = bx + ":" + by; if (!rimBins[binKey]) rimBins[binKey] = []; rimBins[binKey].push(index);
            }
          }
        }
        // Filter the lighting tangent by physical contour arc length, never
        // by nearby XY: separate holes, bridges and islands cannot borrow
        // each other's normals. Keep the actual raster contour untouched.
        // The same endpoint normals will light both the lip and straight wall.
        var rimNext = Object.create(null), rimPrevious = Object.create(null), rimPerimeters = Object.create(null), rimFitLoops = Object.create(null);
        for (var rn = 0; rn < rimCuts.length; rn++) {
          var segment = rimCuts[rn];
          segment.length = Math.sqrt(segment.dx * segment.dx + segment.dy * segment.dy);
          segment.normal = normalise(segment.dy, -segment.dx, 0);
          rimNext[segment.a] = segment; rimPrevious[segment.b] = segment;
          rimPerimeters[segment.loop] = (rimPerimeters[segment.loop] || 0) + segment.length;
          if (!rimFitLoops[segment.loop]) rimFitLoops[segment.loop] = [];
          rimFitLoops[segment.loop].push(segment);
        }
        // A genuine circular drill is stored as short alpha-contour facets.
        // Fit only its LIGHTING reference, with arc-length weights so tiny
        // raster segments cannot bias it. Reject non-circular outlines at
        // both vertices and segment midpoints. Geometry still uses the exact
        // original polyline distance and positions, even for accepted bores.
        for (var fitId in rimFitLoops) if (Object.prototype.hasOwnProperty.call(rimFitLoops, fitId)) {
          var fitCuts = rimFitLoops[fitId], weightSum = 0, centreX = 0, centreY = 0;
          for (var fi = 0; fi < fitCuts.length; fi++) {
            var segment = fitCuts[fi], weight = (segment.length + rimPrevious[segment.a].length) * .5;
            weightSum += weight; centreX += segment.x * weight; centreY += segment.y * weight;
          }
          if (weightSum <= 1e-8 || fitCuts.length < 12) continue;
          centreX /= weightSum; centreY /= weightSum;
          var xx = 0, xy = 0, yy = 0, xrr = 0, yrr = 0, rr = 0;
          for (var fi = 0; fi < fitCuts.length; fi++) {
            var segment = fitCuts[fi], weight = (segment.length + rimPrevious[segment.a].length) * .5;
            var qx = segment.x - centreX, qy = segment.y - centreY, qrr = qx * qx + qy * qy;
            xx += weight * qx * qx; xy += weight * qx * qy; yy += weight * qy * qy;
            xrr += weight * qx * qrr * .5; yrr += weight * qy * qrr * .5; rr += weight * qrr;
          }
          var determinant = xx * yy - xy * xy;
          if (determinant <= Math.max(1e-16, xx * yy * 1e-8)) continue;
          var offsetX = (xrr * yy - yrr * xy) / determinant, offsetY = (yrr * xx - xrr * xy) / determinant;
          var fitRadius = Math.sqrt(rr / weightSum + offsetX * offsetX + offsetY * offsetY);
          centreX += offsetX; centreY += offsetY;
          // Raster extraction can depart by slightly over one source pixel
          // even for the supplied circular drill. Bound the lighting-only
          // approximation by all three limits: 80 microns, 1.5% of radius,
          // and one quarter of a mesh step. Never relax the actual cut geometry.
          var fitError = 0, fitLimit = Math.min(.08, fitRadius * .015, Math.max(dx, dy) * mm * .25);
          for (var fi = 0; fi < fitCuts.length; fi++) for (var midpoint = 0; midpoint < 2; midpoint++) {
            var segment = fitCuts[fi], qx = segment.x + segment.dx * midpoint * .5 - centreX;
            var qy = segment.y + segment.dy * midpoint * .5 - centreY;
            fitError = Math.max(fitError, Math.abs(Math.sqrt(qx * qx + qy * qy) - fitRadius));
          }
          if (fitError > fitLimit) continue;
          holeRimCircles[fitId] = { x: centreX, y: centreY, radius: fitRadius };
          circularHoleNormalCount++; holeRimNormalFitMaxError = Math.max(holeRimNormalFitMaxError, fitError);
        }
        for (var rn = 0; rn < rimCuts.length; rn++) {
          var segment = rimCuts[rn], centreId = segment.a;
          var filterRadius = Math.min(holeRimRadius * 1.6, rimPerimeters[segment.loop] * .125);
          var smoothX = 0, smoothY = 0;
          if (filterRadius <= 1e-8) continue;
          for (var direction = 0; direction < 2; direction++) {
            var fromId = centreId, walked = 0;
            for (var walk = 0; walk < rimCuts.length && walked < filterRadius; walk++) {
              var link = direction ? rimPrevious[fromId] : rimNext[fromId];
              if (!link || link.loop !== segment.loop) break;
              var finish = Math.min(filterRadius, walked + link.length), covered = finish - walked;
              var weight = covered * (1 - (walked + finish) / filterRadius
                + (walked * walked + walked * finish + finish * finish) / (3 * filterRadius * filterRadius));
              smoothX += link.normal[0] * weight; smoothY += link.normal[1] * weight;
              walked = finish; fromId = direction ? link.a : link.b;
            }
          }
          var originalNormal = normalise(sideNormals[centreId][0], sideNormals[centreId][1], 0);
          var smoothNormal = normalise(smoothX, smoothY, 0), fitCircle = holeRimCircles[segment.loop];
          if (fitCircle) smoothNormal = normalise(fitCircle.x - segment.x, fitCircle.y - segment.y, 0);
          // A validated circle uses the same inward tangent on lip and wall,
          // even when one tiny raster facet zigzags. For other outlines a
          // sharp re-entrant feature may cancel the neighbourhood: retain its
          // established normal rather than reverse the wall lighting.
          if (fitCircle || (smoothX * smoothX + smoothY * smoothY > 1e-16
            && smoothNormal[0] * originalNormal[0] + smoothNormal[1] * originalNormal[1] > .5)) holeRimNormals[centreId] = smoothNormal;
          else holeRimNormals[centreId] = originalNormal;
        }
        function rimDistance(px, py) {
          var bx = Math.floor(px / rimBinSize), by = Math.floor(py / rimBinSize), best = rimSearch * rimSearch, nearest = -1, nearT = 0;
          for (var oy = -1; oy <= 1; oy++) for (var ox = -1; ox <= 1; ox++) {
            var bin = rimBins[(bx + ox) + ":" + (by + oy)]; if (!bin) continue;
            for (var bi = 0; bi < bin.length; bi++) {
              var ci = bin[bi], candidate = segmentDistance2(rimCuts[ci], px, py);
              if (candidate.distance2 < best) { best = candidate.distance2; nearest = ci; nearT = candidate.t; }
            }
          }
          var distance = Math.sqrt(best); if (distance < 1e-8) distance = 0;
          var angle = Math.acos(clamp(1 - distance / holeRimRadius, 0, 1));
          return { distance: distance, cut: nearest, t: nearT, angle: angle };
        }
        function rimEdgeKey(a, b) { return Math.min(a, b) + ":" + Math.max(a, b); }
        // The final 4 microns of this 1 mm arc differ from the unchanged face
        // by at most 8.1 nanometres. Snap that render-only numerical tail to
        // the R6 face, rather than refining sub-weld slivers or widening the
        // proven support band. The authoritative depth field is unchanged.
        function rimHasEffect(info) { return info.distance < holeRimRadius - .004; }
        function rimDrop(info) { return rimHasEffect(info) ? holeRimRadius * (1 - Math.sin(info.angle)) : 0; }
        var rimFaces = [], rimFinished = [], rimBlocked = Object.create(null), originalRimCornerCount = corners.length;
        for (var rfi = 0; rfi < indices.length; rfi += 6) rimFaces.push([indices[rfi] / 2, indices[rfi + 1] / 2, indices[rfi + 2] / 2]);
        for (var rimKey in edges) if (Object.prototype.hasOwnProperty.call(edges, rimKey) && edges[rimKey].count === 1) rimBlocked[rimEdgeKey(edges[rimKey].a, edges[rimKey].b)] = true;
        for (var rc = 0; rc < corners.length; rc++) {
          var p = corners[rc], frontAt = p.front * STRIDE, backAt = p.back * STRIDE;
          p.rimBase = { z: vertices[frontAt + 2], backZ: vertices[backAt + 2],
            nx: vertices[frontAt + 3], ny: vertices[frontAt + 4], nz: vertices[frontAt + 5],
            bx: vertices[backAt + 3], by: vertices[backAt + 4], bz: vertices[backAt + 5], material: vertices[frontAt + 8] };
          p.holeRim = rimDistance(p.x, p.y);
        }
        // Freeze the original faces outside a conservatively padded local
        // patch. A missing nearest segment is a capped-distance sentinel,
        // not a real 0.4 mm distance: using that value would select the whole
        // product and repeatedly refine hundreds of thousands of far faces.
        var localRimFaces = [], localEdges = Object.create(null);
        for (var pf = 0; pf < rimFaces.length; pf++) {
          var ids = rimFaces[pf];
          if (corners[ids[0]].holeRim.cut < 0 && corners[ids[1]].holeRim.cut < 0 && corners[ids[2]].holeRim.cut < 0) {
            rimFinished.push(ids); continue;
          }
          localRimFaces.push(ids);
          for (var side = 0; side < 3; side++) {
            var localKey = rimEdgeKey(ids[side], ids[(side + 1) % 3]); localEdges[localKey] = (localEdges[localKey] || 0) + 1;
          }
        }
        // Keep the shared interface exact: local subdivision must never add
        // a midpoint to the edge of a frozen original triangle.
        for (var localKey in localEdges) if (Object.prototype.hasOwnProperty.call(localEdges, localKey) && localEdges[localKey] === 1) rimBlocked[localKey] = true;
        rimFaces = localRimFaces;
        function rimFaceSupport(ids, exhaustive) {
          var a = corners[ids[0]], b = corners[ids[1]], c = corners[ids[2]], points = [a, b, c], best = Infinity;
          if (!rimHasEffect(a.holeRim) && !rimHasEffect(b.holeRim) && !rimHasEffect(c.holeRim)) return 0;
          // A distance-to-polyline field is not convex at concave facets.
          // A capsule around ONE segment is convex: if all three vertices
          // fit, the entire triangle fits, including every interpolated
          // position and normal. This is a true support bound, not sampling.
          var candidates = [];
          for (var cp = 0; cp < 3; cp++) {
            var cut = points[cp].holeRim.cut; if (cut < 0) continue;
            // At a contour vertex the nearest segment is tied; include its
            // two neighbours so that an arbitrary tie cannot reject a thin
            // face lying alongside the actual shared boundary segment.
            var adjacent = rimVertexCuts[rimCuts[cut].a].concat(rimVertexCuts[rimCuts[cut].b]);
            for (var ac = 0; ac < adjacent.length; ac++) if (candidates.indexOf(adjacent[ac]) < 0) candidates.push(adjacent[ac]);
          }
          // A rare unresolved raster sliver gets a complete proof before
          // fallback. Do not confuse a bounded candidate search with proof
          // that no other segment's capsule can contain this tiny face.
          if (exhaustive) { candidates = []; for (var ci = 0; ci < rimCuts.length; ci++) candidates.push(ci); }
          for (var ci = 0; ci < candidates.length; ci++) {
            var cut = candidates[ci];
            var farthest = 0;
            for (var vp = 0; vp < 3; vp++) farthest = Math.max(farthest, segmentDistance2(rimCuts[cut], points[vp].x, points[vp].y).distance2);
            best = Math.min(best, Math.sqrt(farthest));
          }
          return best;
        }
        // Refine shared edges only, retaining all original face planes as the
        // undisplaced reference. New vertices therefore cannot flatten or
        // amplify authored detail while adding the local rounded lip.
        for (var rimPass = 0; rimPass < 20; rimPass++) {
          var rimSplits = Object.create(null), rimSplitCount = 0, rimForced = Object.create(null), rimSoftBlocked = Object.create(null);
          for (var gf = 0; gf < rimFaces.length; gf++) {
            var ids = rimFaces[gf], shortest2 = Infinity, longest2 = 0;
            for (var side = 0; side < 3; side++) {
              var a = corners[ids[side]], b = corners[ids[(side + 1) % 3]], ex = a.x - b.x, ey = a.y - b.y;
              shortest2 = Math.min(shortest2, ex * ex + ey * ey); longest2 = Math.max(longest2, ex * ex + ey * ey);
            }
            var a = corners[ids[0]], b = corners[ids[1]], c = corners[ids[2]], area2 = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
            // This is a numeric Float32/weld safeguard, not a physical lip
            // parameter. Preserve the proven absolute threshold as the lip
            // radius changes; scaling it would strand harmless tail slivers.
            if (shortest2 < holeRimMinEdge * holeRimMinEdge || area2 * area2 < longest2 * holeRimMinEdge * holeRimMinEdge) {
              rimBlocked[rimEdgeKey(ids[0], ids[1])] = rimBlocked[rimEdgeKey(ids[1], ids[2])] = rimBlocked[rimEdgeKey(ids[2], ids[0])] = true;
            } else if (shortest2 < holeRimRadius * holeRimRadius * .000001 || area2 * area2 < longest2 * holeRimRadius * holeRimRadius * .000001) {
              // Avoid repeatedly refining visually irrelevant tiny facets.
              // A capsule-support proof may bypass this soft budget, but not
              // the hard Float32 limit or any original/frozen boundary edge.
              rimSoftBlocked[rimEdgeKey(ids[0], ids[1])] = rimSoftBlocked[rimEdgeKey(ids[1], ids[2])] = rimSoftBlocked[rimEdgeKey(ids[2], ids[0])] = true;
            }
          }
          for (var sf = 0; sf < rimFaces.length; sf++) if (rimFaceSupport(rimFaces[sf]) > holeRimRadius + holeRimTolerance) {
            var ids = rimFaces[sf], longest = -1, forcedKey = null;
            for (var side = 0; side < 3; side++) {
              var key = rimEdgeKey(ids[side], ids[(side + 1) % 3]); if (rimBlocked[key]) continue;
              var a = corners[ids[side]], b = corners[ids[(side + 1) % 3]], length2 = (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y);
              if (length2 > longest) { longest = length2; forcedKey = key; }
            }
            if (forcedKey) rimForced[forcedKey] = true;
          }
          function splitRimEdge(ia, ib) {
            var key = rimEdgeKey(ia, ib); if (rimSplits[key] !== undefined) return;
            if (rimBlocked[key]) { rimSplits[key] = -1; return; }
            if (rimSoftBlocked[key] && !rimForced[key]) { rimSplits[key] = -1; return; }
            var a = corners[ia], b = corners[ib], da = a.holeRim, db = b.holeRim;
            var length = Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y));
            if (length < (rimForced[key] ? holeRimMinEdge : holeRimRadius * .001)
              || (!rimForced[key] && Math.min(da.distance, db.distance) - length * .5 >= holeRimRadius)) { rimSplits[key] = -1; return; }
            var mixT = .5, crossesBand = (da.distance < holeRimRadius - 1e-9 && db.distance > holeRimRadius + 1e-9)
              || (db.distance < holeRimRadius - 1e-9 && da.distance > holeRimRadius + 1e-9);
            if (crossesBand) {
              var lo = 0, hi = 1, aInside = da.distance < holeRimRadius;
              for (var bisect = 0; bisect < 28; bisect++) {
                var t = (lo + hi) * .5, query = rimDistance(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
                if ((query.distance < holeRimRadius) === aInside) lo = t; else hi = t;
              }
              mixT = (lo + hi) * .5;
            }
            var mx = a.x + (b.x - a.x) * mixT, my = a.y + (b.y - a.y) * mixT, dm = rimDistance(mx, my);
            if (crossesBand) { dm.distance = holeRimRadius; dm.angle = PI * .5; }
            if ((Math.fround(mx) === Math.fround(a.x) && Math.fround(my) === Math.fround(a.y))
              || (Math.fround(mx) === Math.fround(b.x) && Math.fround(my) === Math.fround(b.y))) { rimSplits[key] = -1; return; }
            var angleChange = Math.max(Math.abs(da.angle - db.angle), Math.abs(da.angle - dm.angle), Math.abs(db.angle - dm.angle));
            var error = Math.abs(rimDrop(dm) - (rimDrop(da) * (1 - mixT) + rimDrop(db) * mixT));
            if (!rimForced[key] && !crossesBand && angleChange <= PI / 36 && error <= .001) { rimSplits[key] = -1; return; }
            var base = {}, fields = ["z", "backZ", "nx", "ny", "nz", "bx", "by", "bz", "material"];
            for (var field = 0; field < fields.length; field++) base[fields[field]] = a.rimBase[fields[field]] * (1 - mixT) + b.rimBase[fields[field]] * mixT;
            rimSplits[key] = corners.length;
            corners.push({ x: mx, y: my, sx: a.sx * (1 - mixT) + b.sx * mixT, sy: a.sy * (1 - mixT) + b.sy * mixT,
              u: a.u * (1 - mixT) + b.u * mixT, v: a.v * (1 - mixT) + b.v * mixT, rimBase: base, holeRim: dm }); rimSplitCount++;
          }
          for (var rft = 0; rft < rimFaces.length; rft++) {
            var tr = rimFaces[rft]; splitRimEdge(tr[0], tr[1]); splitRimEdge(tr[1], tr[2]); splitRimEdge(tr[2], tr[0]);
          }
          if (!rimSplitCount) break;
          var rimRefined = [];
          for (var rft = 0; rft < rimFaces.length; rft++) {
            var tr = rimFaces[rft], va = tr[0], vb = tr[1], vc = tr[2];
            var ab = rimSplits[rimEdgeKey(va, vb)], bc = rimSplits[rimEdgeKey(vb, vc)], ca = rimSplits[rimEdgeKey(vc, va)];
            if (ab >= 0 && bc >= 0 && ca >= 0) rimRefined.push([va, ab, ca], [ab, vb, bc], [ca, bc, vc], [ab, bc, ca]);
            else if (ab >= 0 && bc >= 0) rimRefined.push([vb, bc, ab], [va, ab, vc], [ab, bc, vc]);
            else if (bc >= 0 && ca >= 0) rimRefined.push([vc, ca, bc], [vb, bc, va], [bc, ca, va]);
            else if (ca >= 0 && ab >= 0) rimRefined.push([va, ab, ca], [vc, ca, vb], [ca, ab, vb]);
            else if (ab >= 0) rimRefined.push([va, ab, vc], [ab, vb, vc]);
            else if (bc >= 0) rimRefined.push([vb, bc, va], [bc, vc, va]);
            else if (ca >= 0) rimRefined.push([vc, ca, vb], [ca, va, vb]);
            else rimRefined.push(tr);
          }
          rimFaces = rimRefined; holeRimRefinementPasses++;
        }
        rimFaces = rimFinished.concat(rimFaces);
        function rimWeldKey(point, back) {
          return Math.fround(point.x).toFixed(5) + ":" + Math.fround(point.y).toFixed(5) + ":"
            + Math.fround(back ? point.rimBase.backZ : point.rimBase.z - rimDrop(point.holeRim)).toFixed(5);
        }
        for (var sf = 0; sf < rimFaces.length; sf++) {
          var support = rimFaceSupport(rimFaces[sf]);
          if (support > holeRimRadius + holeRimTolerance + 1e-8) support = rimFaceSupport(rimFaces[sf], true);
          if (support > holeRimRadius + holeRimTolerance + 1e-8) holeRimUnsupportedFaces++;
          holeRimAffectedRadius = Math.max(holeRimAffectedRadius, support);
          var ids = rimFaces[sf];
          if (Math.max(ids[0], ids[1], ids[2]) >= originalRimCornerCount) for (var faceSide = 0; faceSide < 2; faceSide++) {
            var ka = rimWeldKey(corners[ids[0]], faceSide), kb = rimWeldKey(corners[ids[1]], faceSide), kc = rimWeldKey(corners[ids[2]], faceSide);
            if (ka === kb || kb === kc || kc === ka) holeRimDegenerateFaces++;
          }
        }
        if (holeRimUnsupportedFaces || holeRimDegenerateFaces) {
          // Never silently widen the polish when a numerical sliver cannot
          // be refined safely. Keep the complete previously-approved R6 mesh.
          corners.length = originalRimCornerCount; roundedHoleCount = 0; holeRimAffectedRadius = 0;
          for (var loopId in roundedHoleLoops) if (Object.prototype.hasOwnProperty.call(roundedHoleLoops, loopId)) roundedHoleLoops[loopId] = false;
        } else {
        vertices = []; indices = []; maxZ = meshBaseTop;
        for (var rc = 0; rc < corners.length; rc++) {
          var p = corners[rc], base = p.rimBase, info = p.holeRim, drop = rimDrop(info), material = base.material;
          var nx = base.nx, ny = base.ny, nz = base.nz;
          if (info.cut >= 0 && rimHasEffect(info)) {
            var cut = rimCuts[info.cut], an = holeRimNormals[cut.a] || sideNormals[cut.a], bn = holeRimNormals[cut.b] || sideNormals[cut.b];
            var na = normalise(an[0], an[1], 0), nb = normalise(bn[0], bn[1], 0);
            var outward = normalise(na[0] * (1 - info.t) + nb[0] * info.t, na[1] * (1 - info.t) + nb[1] * info.t, 0);
            var lightAngle = info.angle, fitCircle = holeRimCircles[cut.loop];
            if (fitCircle) {
              var circleX = p.x - fitCircle.x, circleY = p.y - fitCircle.y;
              var circleDistance = Math.sqrt(circleX * circleX + circleY * circleY) - fitCircle.radius;
              var bandT = clamp(info.distance / holeRimRadius, 0, 1);
              // Smooth sub-pixel facet ripple in reflection only. Correction
              // is zero at the true bore and at the outer band boundary, so
              // the wall tangent and original face normal remain exact.
              var lightDistance = clamp(info.distance + (circleDistance - info.distance) * 4 * bandT * (1 - bandT), 0, holeRimRadius);
              lightAngle = Math.acos(clamp(1 - lightDistance / holeRimRadius, 0, 1));
              outward = normalise(fitCircle.x - p.x, fitCircle.y - p.y, 0);
            }
            var radial = Math.cos(lightAngle), vertical = Math.sin(lightAngle);
            // Add the arc derivative to the original face slope. Multiplying
            // by its Z component avoids a division near steep authored relief
            // and prevents relief gain from changing the polish radius.
            var normal = normalise(outward[0] * radial * base.nz + base.nx * vertical,
              outward[1] * radial * base.nz + base.ny * vertical, base.nz * vertical);
            nx = normal[0]; ny = normal[1]; nz = normal[2]; material = Math.max(base.material, .4 * clamp((holeRimRadius - info.distance) / .01, 0, 1));
          }
          heightPrecision[0] = base.z - drop; p.z = heightPrecision[0]; maxZ = Math.max(maxZ, p.z);
          p.front = addVertex(p.x, p.y, p.z, nx, ny, nz, p.u, p.v, material);
          p.back = addVertex(p.x, p.y, base.backZ, base.bx, base.by, base.bz, p.u, p.v, 1);
        }
        for (var rf = 0; rf < rimFaces.length; rf++) {
          var tr = rimFaces[rf], a = corners[tr[0]], b = corners[tr[1]], c = corners[tr[2]];
          indices.push(a.front, b.front, c.front, a.back, c.back, b.back);
        }
        }
      }
    }
    for (key in edges) if (Object.prototype.hasOwnProperty.call(edges, key) && edges[key].count === 1) {
      var a = corners[edges[key].a], b = corners[edges[key].b];
      var rawA = sideNormals[edges[key].a], rawB = sideNormals[edges[key].b];
      if (edges[key].hole && roundedHoleLoops[edges[key].holeLoop]) {
        rawA = holeRimNormals[edges[key].a] || rawA; rawB = holeRimNormals[edges[key].b] || rawB;
      }
      var na = normalise(rawA[0], rawA[1], 0), nb = normalise(rawB[0], rawB[1], 0);
      if (ceramic && edges[key].hole) {
        // A drilled wall is vertical at the exact existing XY contour. Its
        // top follows local artwork relief; no zero-height shelf or wide
        // bullnose may displace nearby details. Keep separate face/wall
        // normals: smooth circumference, crisp through-cut (no broad dent).
        var holeBackA = vertices[a.back * STRIDE + 2], holeBackB = vertices[b.back * STRIDE + 2];
        var hba = addVertex(a.x, a.y, holeBackA, na[0], na[1], 0, a.u, a.v, 2);
        var hbb = addVertex(b.x, b.y, holeBackB, nb[0], nb[1], 0, b.u, b.v, 2);
        var hfb = addVertex(b.x, b.y, b.z, nb[0], nb[1], 0, b.u, b.v, 2);
        var hfa = addVertex(a.x, a.y, a.z, na[0], na[1], 0, a.u, a.v, 2);
        indices.push(hba, hbb, hfb, hba, hfb, hfa);
        boundaryEdges++;
        continue;
      }
      if (ceramic) {
        // Exact shared endpoint positions and normals seal the round-over.
        var endpoints = [a, b], normals = [na, nb];
        for (var ep = 0; ep < 2; ep++) {
          var point = endpoints[ep], sideN = normals[ep];
          vertices[point.front * STRIDE + 2] = baseTop - bevelRadius;
          vertices[point.front * STRIDE + 8] = .4;
          vertices[point.back * STRIDE + 2] = -1.5 + bevelRadius;
          for (var component = 0; component < 3; component++) {
            vertices[point.front * STRIDE + 3 + component] = sideN[component];
            vertices[point.back * STRIDE + 3 + component] = sideN[component];
          }
        }
        var cba = addVertex(a.x, a.y, -1.5 + bevelRadius, na[0], na[1], 0, a.u, a.v, 2);
        var cbb = addVertex(b.x, b.y, -1.5 + bevelRadius, nb[0], nb[1], 0, b.u, b.v, 2);
        var cfb = addVertex(b.x, b.y, baseTop - bevelRadius, nb[0], nb[1], 0, b.u, b.v, 2);
        var cfa = addVertex(a.x, a.y, baseTop - bevelRadius, na[0], na[1], 0, a.u, a.v, 2);
        indices.push(cba, cbb, cfb, cba, cfb, cfa);
        boundaryEdges++;
        continue;
      }
      if (edges[key].hole && roundedHoleLoops[edges[key].holeLoop]) {
        // At gain zero the polished lip is just below the unprinted acrylic
        // top, so the white wall must stop at that same lowered vertex. At
        // higher gain retain the established white-body/printed-relief split.
        var bodyA = Math.min(meshBaseTop, a.z), bodyB = Math.min(meshBaseTop, b.z);
        var rba = addVertex(a.x, a.y, -1.5, na[0], na[1], 0, a.u, a.v, 2);
        var rbb = addVertex(b.x, b.y, -1.5, nb[0], nb[1], 0, b.u, b.v, 2);
        var rfb = addVertex(b.x, b.y, bodyB, nb[0], nb[1], 0, b.u, b.v, 2);
        var rfa = addVertex(a.x, a.y, bodyA, na[0], na[1], 0, a.u, a.v, 2);
        indices.push(rba, rbb, rfb, rba, rfb, rfa);
        if (a.z > meshBaseTop || b.z > meshBaseTop) {
          var rua = edgeUV(a), rub = edgeUV(b);
          var rta = addVertex(a.x, a.y, bodyA, na[0], na[1], 0, rua[0], rua[1], 3);
          var rtb = addVertex(b.x, b.y, bodyB, nb[0], nb[1], 0, rub[0], rub[1], 3);
          var rrb = addVertex(b.x, b.y, b.z, nb[0], nb[1], 0, rub[0], rub[1], 3);
          var rra = addVertex(a.x, a.y, a.z, na[0], na[1], 0, rua[0], rua[1], 3);
          if (b.z > meshBaseTop) indices.push(rta, rtb, rrb);
          if (a.z > meshBaseTop) indices.push(rta, rrb, rra);
        }
        boundaryEdges++;
        continue;
      }
      var ba = addVertex(a.x, a.y, -1.5, na[0], na[1], 0, a.u, a.v, 2);
      var bb = addVertex(b.x, b.y, -1.5, nb[0], nb[1], 0, b.u, b.v, 2);
      var fb = addVertex(b.x, b.y, baseTop, nb[0], nb[1], 0, b.u, b.v, 2);
      var fa = addVertex(a.x, a.y, baseTop, na[0], na[1], 0, a.u, a.v, 2);
      indices.push(ba, bb, fb, ba, fb, fa);
      if (a.z > meshBaseTop || b.z > meshBaseTop) {
        var ua = edgeUV(a), ub = edgeUV(b);
        var ta = addVertex(a.x, a.y, baseTop, na[0], na[1], 0, ua[0], ua[1], 3);
        var tb = addVertex(b.x, b.y, baseTop, nb[0], nb[1], 0, ub[0], ub[1], 3);
        var rb = addVertex(b.x, b.y, b.z, nb[0], nb[1], 0, ub[0], ub[1], 3);
        var ra = addVertex(a.x, a.y, a.z, na[0], na[1], 0, ua[0], ua[1], 3);
        // A zero-depth contour endpoint has only one triangle. At gain zero
        // the whole extra wall disappears, without degenerate coplanar faces.
        if (b.z > meshBaseTop) indices.push(ta, tb, rb);
        if (a.z > meshBaseTop) indices.push(ta, rb, ra);
      }
      boundaryEdges++;
    }
    var geometryResult = { vertices: new Float32Array(vertices), indices: new Uint32Array(indices),
      dimensions: dim, sourceBounds: bounds, grid: { width: cols, height: rows },
      widthMm: bw * mm, heightMm: dim.heightMm, occupiedCells: occupied, boundaryEdges: boundaryEdges,
      reliefBoost: reliefBoost, rimBoost: rimBoost, rimTopZ: rimTop, baseTopZ: baseTop, minZ: minZ, maxZ: maxZ,
      separatedSurface: separated, surfaceShoulderPixels: displaySurface ? displaySurface.shoulderPixels : 0,
      approximateSilhouette: ratio < 1, nativeDepth: data.depth instanceof Uint16Array ? "uint16" : "float32",
      maxGrid: limit, contourMethod: "continuous-alpha-triangles", smoothSideNormals: true,
      product_type: productType(data.product_type), bevelRadiusMm: bevelRadius, fixedTemplateShape: ceramic,
      whiteUnprintedBack: ceramic, illustrationOnly: true };
    if (ceramic) geometryResult.roundoverRefinementPasses = roundoverRefinementPasses;
    geometryResult.straightHoleCount = straightHoleCount;
    geometryResult.holeRimRadiusMm = holeRimRadius;
    geometryResult.circularHoleNormalCount = roundedHoleCount ? circularHoleNormalCount : 0;
    geometryResult.holeRimNormalFitMaxErrorMm = roundedHoleCount ? holeRimNormalFitMaxError : 0;
    geometryResult.holeRimToleranceMm = holeRimTolerance;
    geometryResult.holeRimAffectedRadiusMm = holeRimAffectedRadius;
    geometryResult.holeRimUnsupportedFaces = holeRimUnsupportedFaces;
    geometryResult.holeRimDegenerateFaces = holeRimDegenerateFaces;
    geometryResult.holeRimRefinementPasses = holeRimRefinementPasses;
    geometryResult.roundedHoleCount = roundedHoleCount;
    return geometryResult;
  }

  function splitGeometry(geometry, maxVertices) {
    maxVertices = maxVertices || CHUNK_VERTICES;
    if (maxVertices < 3 || maxVertices > 65535) throw new Error("16-bit chunks require between 3 and 65535 vertices.");
    var parts = [], map = Object.create(null), vertices = [], indices = [], used = 0, i, j, old, count;
    function flush() {
      if (indices.length) parts.push({ vertices: new Float32Array(vertices), indices: new Uint16Array(indices) });
      map = Object.create(null); vertices = []; indices = []; used = 0;
    }
    for (i = 0; i < geometry.indices.length; i += 3) {
      count = 0;
      for (j = 0; j < 3; j++) if (map[geometry.indices[i + j]] === undefined) count++;
      if (used + count > maxVertices) flush();
      for (j = 0; j < 3; j++) {
        old = geometry.indices[i + j];
        if (map[old] === undefined) {
          map[old] = used++;
          for (var k = 0; k < STRIDE; k++) vertices.push(geometry.vertices[old * STRIDE + k]);
        }
        indices.push(map[old]);
      }
    }
    flush(); return parts;
  }

  function textureData(data, cap, edgePadding) {
    var ceramic = ceramicData(data);
    edgePadding = edgePadding === undefined ? 0 : edgePadding;
    if (!finite(edgePadding) || edgePadding % 1 || edgePadding < 0 || edgePadding > 32) throw new Error("Texture edge padding must be an integer between 0 and 32.");
    var ratio = Math.min(1, cap / Math.max(data.width, data.height));
    var width = Math.max(1, Math.round(data.width * ratio)), height = Math.max(1, Math.round(data.height * ratio));
    var rgba = new Uint8Array(width * height * 4), x, y, offset;
    // Filter premultiplied colors, then unpremultiply the sampled texel in the
    // shader. Straight-RGBA interpolation against transparent black otherwise
    // makes an opaque dark outline at the 50% alpha cutline. Input is untouched.
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      var sx = clamp((x + .5) * data.width / width - .5, 0, data.width - 1);
      var sy = clamp((y + .5) * data.height / height - .5, 0, data.height - 1);
      var ix = Math.floor(sx), iy = Math.floor(sy), fx = sx - ix, fy = sy - iy;
      var r = 0, g = 0, b = 0, a = 0;
      for (var j = 0; j < 2; j++) for (var i = 0; i < 2; i++) {
        var px = Math.min(ix + i, data.width - 1), py = Math.min(iy + j, data.height - 1), at = py * data.width + px;
        var weight = (i ? fx : 1 - fx) * (j ? fy : 1 - fy), alpha = alphaAt(data, at), cw = weight * alpha / 255;
        if (ceramic || data.shape_alpha) {
          var ink = artworkAlphaAt(data, at) / 255;
          // The shared antialiased cutline already contributes its coverage
          // through cw. Applying that coverage to ink a second time mixes a
          // false white fringe into a fullprint that reaches the edge. Keep
          // genuine interior transparency/white art; normalize only partial
          // template coverage, and never grow or recolor the source image.
          if (alpha > 0 && alpha < 255) ink = Math.min(1, ink * 255 / alpha);
          r += (data.rgba[at * 4] * ink + 255 * (1 - ink)) * cw;
          g += (data.rgba[at * 4 + 1] * ink + 255 * (1 - ink)) * cw;
          b += (data.rgba[at * 4 + 2] * ink + 255 * (1 - ink)) * cw;
        } else {
          r += data.rgba[at * 4] * cw; g += data.rgba[at * 4 + 1] * cw; b += data.rgba[at * 4 + 2] * cw;
        }
        a += alpha * weight;
      }
      offset = (y * width + x) * 4;
      rgba[offset] = Math.round(r); rgba[offset + 1] = Math.round(g); rgba[offset + 2] = Math.round(b); rgba[offset + 3] = Math.round(a);
    }
    if (edgePadding) {
      // Geometry already clips the canonical 50% alpha contour and seals it
      // to the acrylic wall. Repeating discard in the fragment shader uses a
      // different (pixel-centre) coverage test from MSAA and opens dotted gaps
      // in otherwise watertight joins. Extend *display colors* just outside
      // that contour instead; the input alpha and actual mesh never expand.
      // Seeds >= 50% avoid magnifying RGB rounding in almost invisible pixels.
      var count = width * height, queue = new Uint32Array(count), distances = new Uint8Array(count);
      var head = 0, tail = 0, p, next, d, c;
      for (p = 0; p < count; p++) {
        if (rgba[p * 4 + 3] >= 128) { distances[p] = 1; queue[tail++] = p; }
      }
      while (head < tail) {
        p = queue[head++]; d = distances[p]; if (d > edgePadding) continue;
        x = p % width; y = Math.floor(p / width);
        for (var direction = 0; direction < 4; direction++) {
          if (direction === 0) { if (!x) continue; next = p - 1; }
          else if (direction === 1) { if (x + 1 >= width) continue; next = p + 1; }
          else if (direction === 2) { if (!y) continue; next = p - width; }
          else { if (y + 1 >= height) continue; next = p + width; }
          if (distances[next]) continue;
          distances[next] = d + 1; queue[tail++] = next;
          for (c = 0; c < 3; c++) rgba[next * 4 + c] = Math.round(rgba[p * 4 + c] * 255 / rgba[p * 4 + 3]);
          rgba[next * 4 + 3] = 255;
        }
      }
    }
    return { width: width, height: height, rgba: rgba, premultiplied: true, edgePadding: edgePadding };
  }
  function multiply(a, b) {
    var out = new Float32Array(16), row, col, k;
    for (col = 0; col < 4; col++) for (row = 0; row < 4; row++) {
      var value = 0; for (k = 0; k < 4; k++) value += a[k * 4 + row] * b[col * 4 + k]; out[col * 4 + row] = value;
    }
    return out;
  }
  function rotation(yaw, pitch) {
    var cy = Math.cos(yaw), sy = Math.sin(yaw), cx = Math.cos(pitch), sx = Math.sin(pitch);
    return new Float32Array([cy, 0, -sy, 0, sy * sx, cx, cy * sx, 0, sy * cx, -sx, cy * cx, 0, 0, 0, 0, 1]);
  }
  function cameraMatrices(state, geometry, width, height) {
    var model = rotation(state.yaw, state.pitch), aspect = width / height;
    // Frame the fixed maximum display range once. Raising the relief slider
    // must not silently shrink the acrylic XY scale or crop a rotated peak.
    var maxDisplayZ = geometry ? Math.max(1.5, 1.5 + 23 * geometry.dimensions.reliefMm) : 1.5;
    var radius = geometry ? Math.sqrt(geometry.widthMm * geometry.widthMm + geometry.heightMm * geometry.heightMm + 4*maxDisplayZ*maxDisplayZ) / 2 : 60;
    var halfY = radius * 1.12 / state.zoom / Math.min(1, aspect), halfX = halfY * aspect;
    // Keep XY framing stable while the mockup's exaggerated front grows.
    var extraZ = geometry && finite(geometry.maxZ) ? Math.max(3, Math.abs(geometry.maxZ)) : 3;
    var distance = radius * 4 + extraZ + 10, near = 0.1, far = distance + radius * 4 + extraZ + 10;
    var projection = new Float32Array([1 / halfX, 0, 0, 0, 0, 1 / halfY, 0, 0, 0, 0, -2 / (far - near), 0, 0, 0, -(far + near) / (far - near), 1]);
    var viewModel = new Float32Array(model); viewModel[14] = -distance;
    return { model: model, mvp: multiply(projection, viewModel), halfHeight: halfY };
  }
  function fullCameraMatrices(state, geometry, cord, width, height, padding) {
    if (!geometry || !geometry.vertices || !geometry.vertices.length) throw new Error("No product geometry to frame.");
    if (!finite(width) || !finite(height) || width <= 0 || height <= 0) throw new Error("Invalid export frame dimensions.");
    padding = padding === undefined ? .04 : padding;
    if (!finite(padding) || padding < .01 || padding > .25) throw new Error("Full export padding must be between 1% and 25%.");
    var model = rotation(state.yaw, state.pitch), minX = Infinity, minY = Infinity, minZ = Infinity;
    var maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    function include(mesh) {
      if (!mesh || !mesh.vertices) return;
      var vertices = mesh.vertices;
      for (var i = 0; i < vertices.length; i += STRIDE) {
        var x = vertices[i], y = vertices[i + 1], z = vertices[i + 2];
        var rx = model[0] * x + model[4] * y + model[8] * z;
        var ry = model[1] * x + model[5] * y + model[9] * z;
        var rz = model[2] * x + model[6] * y + model[10] * z;
        minX = Math.min(minX, rx); maxX = Math.max(maxX, rx);
        minY = Math.min(minY, ry); maxY = Math.max(maxY, ry);
        minZ = Math.min(minZ, rz); maxZ = Math.max(maxZ, rz);
      }
    }
    // The legacy cord argument is deliberately ignored: exports frame only
    // the product. Hanging cord and background are added later outside this viewer.
    include(geometry);
    var aspect = width / height, cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    var halfY = Math.max((maxY - minY) / 2, (maxX - minX) / (2 * aspect), .001) / (1 - 2 * padding);
    var halfX = halfY * aspect, depthSpan = Math.max(2, maxZ - minZ + 2);
    // Lighting stays in the same rotated world coordinates. Only the export
    // orthographic framing changes, never the relief, surface normals or pose.
    var projection = new Float32Array([1 / halfX, 0, 0, 0, 0, 1 / halfY, 0, 0,
      0, 0, -2 / depthSpan, 0, -cx / halfX, -cy / halfY, (maxZ + minZ) / depthSpan, 1]);
    return { model: model, mvp: multiply(projection, model), halfHeight: halfY,
      bounds: { minX: minX, maxX: maxX, minY: minY, maxY: maxY, minZ: minZ, maxZ: maxZ },
      centerX: cx, centerY: cy, padding: padding };
  }
  function tileCameraMatrices(full, fullWidth, fullHeight, x, y, width, height) {
    var crop = new Float32Array([fullWidth / width, 0, 0, 0, 0, fullHeight / height, 0, 0,
      0, 0, 1, 0, (fullWidth - 2 * x - width) / width, (2 * y + height - fullHeight) / height, 0, 1]);
    return { model: full.model, mvp: multiply(crop, full.mvp), halfHeight: full.halfHeight };
  }
  // Packed 16-bit XY normals retain shallow physical slopes which would be
  // quantized away in a conventional 8-bit normal map. No height exaggeration.
  function normalTextureData(data, physical, cap, preparedSurface, supportAppearance, clearcoatFilter) {
    var dim = dimensions(physical), bounds = boundsOf(data);
    var surface = preparedSurface ? displaySurfaceFor(data, preparedSurface) : null;
    var combinedSupport = surface && surface.rimWeights ? appearance(supportAppearance) : null;
    var ratio = Math.min(1, cap / Math.max(data.width, data.height));
    var width = Math.max(1, Math.round(data.width * ratio)), height = Math.max(1, Math.round(data.height * ratio));
    var heights = new Float32Array(width * height), valid = new Uint8Array(width * height), out = new Uint8Array(width * height * 4);
    var x, y, i, sx, sy, scale = data.depth instanceof Uint16Array ? 65535 : 1;
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      i = y * width + x; sx = Math.min(data.width - 1, Math.floor((x + .5) * data.width / width)); sy = Math.min(data.height - 1, Math.floor((y + .5) * data.height / height));
      heights[i] = surface ? displayHeight(surface, (x + .5) * data.width / width - .5, (y + .5) * data.height / height - .5)
        : data.depth[sy * data.width + sx] / scale;
      if (combinedSupport) heights[i] = heights[i] * combinedSupport.reliefBoost + combinedSupport.rimBoost * displayRimWeight(surface, (x + .5) * data.width / width - .5, (y + .5) * data.height / height - .5);
      valid[i] = alphaAt(data, sy * data.width + sx) >= (surface ? 128 : 250) ? 1 : 0;
    }
    // Legacy callers use a lighting-only overcoat filter. The separated
    // surface path is already smoothed: adding a second blur here would make
    // reflections describe a different surface from the displaced geometry.
    // Resin's transparent overcoat integrates print grain and the tiny sharp
    // depth shoulders. Filter only its lighting field, never canonical heights,
    // geometry, color bytes or the ceramic material's established normal field.
    var filtered = surface && !clearcoatFilter ? null : new Float32Array(heights.length), kernel = [1,4,6,4,1];
    for (var pass = 0; filtered && pass < (clearcoatFilter ? 4 : 2); pass++) {
      for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
        var sum = 0, weight = 0;
        for (var offset = -2; offset <= 2; offset++) {
          var px = pass % 2 === 0 ? clamp(x+offset,0,width-1) : x;
          var py = pass % 2 === 1 ? clamp(y+offset,0,height-1) : y;
          var index = py*width+px;
          if (valid[index]) { sum += heights[index]*kernel[offset+2]; weight += kernel[offset+2]; }
        }
        filtered[y*width+x] = weight ? sum/weight : heights[y*width+x];
      }
      var swap = heights; heights = filtered; filtered = swap;
    }
    var mmX = dim.heightMm / (bounds.y1 - bounds.y0) * data.width / width;
    var mmY = dim.heightMm / (bounds.y1 - bounds.y0) * data.height / height;
    function h(px, py, fallback) {
      px = clamp(px, 0, width - 1); py = clamp(py, 0, height - 1);
      var index = py * width + px;
      if (!valid[index] && surface && surface.boundaryRegions) {
        var regions = surface.boundaryRegions;
        var rx = clamp(Math.floor((px + .5) * surface.width / width), 0, surface.width - 1);
        var ry = clamp(Math.floor((py + .5) * surface.height / height), 0, surface.height - 1);
        var cx = clamp(Math.floor((x + .5) * surface.width / width), 0, surface.width - 1);
        var cy = clamp(Math.floor((y + .5) * surface.height / height), 0, surface.height - 1);
        var voidId = regions.voids[ry * surface.width + rx], component = regions.solids[cy * surface.width + cx];
        // Lighting must not turn the drilled void into a zero-height trench.
        // Normal smoothing is local only; the inner wall stays exactly cut.
        if (voidId && !regions.exterior[voidId] && component && regions.outer[component] !== voidId) return fallback;
      }
      return valid[index] ? heights[index] : (surface && !combinedSupport ? 0 : fallback);
    }
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
      i = y * width + x; var center = heights[i], gx = 0, gy = 0;
      if (valid[i]) {
        gx = (h(x+1,y-1,center)+2*h(x+1,y,center)+h(x+1,y+1,center)-h(x-1,y-1,center)-2*h(x-1,y,center)-h(x-1,y+1,center)) * dim.reliefMm / (8*mmX);
        gy = -(h(x-1,y+1,center)+2*h(x,y+1,center)+h(x+1,y+1,center)-h(x-1,y-1,center)-2*h(x,y-1,center)-h(x+1,y-1,center)) * dim.reliefMm / (8*mmY);
      }
      var n = normalise(-gx, -gy, 1), nx = Math.round((n[0]+1)*32767.5), ny = Math.round((n[1]+1)*32767.5);
      out[i*4] = nx >> 8; out[i*4+1] = nx & 255; out[i*4+2] = ny >> 8; out[i*4+3] = ny & 255;
    }
    return { width: width, height: height, rgba: out };
  }
  // Visibility is sampled from the very same display-height field as the
  // displaced front. Never infer cavities from ink brightness or reuse a
  // design-independent highlight mask. The canonical depth is read-only.
  function reliefTextureData(data, preparedSurface, supportAppearance) {
    var surface = displaySurfaceFor(data, preparedSurface);
    var look = appearance(supportAppearance), combined = !!surface.rimWeights;
    var out = new Uint8Array(surface.width * surface.height * 4);
    for (var y = 0; y < surface.height; y++) for (var x = 0; x < surface.width; x++) {
      var i = y * surface.width + x;
      var sx = (x + .5) * data.width / surface.width - .5, sy = (y + .5) * data.height / surface.height - .5;
      var h = surface.heights[i];
      if (combined) h = (h * look.reliefBoost + look.rimBoost * displayRimWeight(surface, sx, sy)) / 24;
      var value = Math.round(clamp(h, 0, 1) * 65535);
      out[i * 4] = value >> 8; out[i * 4 + 1] = value & 255;
      out[i * 4 + 2] = alphaAt(data, clamp(Math.round(sy), 0, data.height - 1) * data.width + clamp(Math.round(sx), 0, data.width - 1)) >= 128 ? 255 : 0;
      out[i * 4 + 3] = 255;
    }
    return { width: surface.width, height: surface.height, rgba: out, combinedScale: combined ? 24 : 1 };
  }
  var VERTEX_SHADER = [
    "attribute vec3 aPosition; attribute vec3 aNormal; attribute vec2 aUV; attribute float aMaterial;",
    "uniform mat4 uMVP; uniform mat4 uModel; varying vec3 vNormal; varying vec3 vPosition; varying vec2 vUV; varying float vMaterial;",
    "void main(){ vec4 p=uModel*vec4(aPosition,1.0); vPosition=p.xyz; vNormal=mat3(uModel)*aNormal; vUV=aUV; vMaterial=aMaterial; gl_Position=uMVP*vec4(aPosition,1.0); }"
  ].join("\n");
  var FRAGMENT_SHADER = [
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif",
    "uniform sampler2D uSource; uniform sampler2D uDetailNormal; uniform sampler2D uCoatNormal; uniform sampler2D uRelief; uniform vec2 uSpanMm; uniform float uHeightMm; uniform float uReliefTexelMm; uniform mat4 uModel; uniform float uNeutral; uniform float uLightScale; uniform float uReliefBoost; uniform float uGloss; uniform float uCeramic; varying vec3 vNormal; varying vec3 vPosition; varying vec2 vUV; varying float vMaterial;",
    "float heightMm(vec4 h){return dot(h.rg,vec2(65280.0,255.0))/65535.0*uHeightMm;}",
    // Inverse rotation uses matrix columns (GLSL ES 1 has no transpose()).
    // Trace the reflected ray through this design's raised forms. A hair
    // ridge/frame can occlude a recess, while alpha holes never become walls.
    "float reliefVisibility(vec3 worldRay){vec3 ray=vec3(dot(worldRay,uModel[0].xyz),dot(worldRay,uModel[1].xyz),dot(worldRay,uModel[2].xyz));float xy=length(ray.xy);if(xy<0.0001 || uHeightMm<0.0001)return 1.0;vec4 origin=texture2D(uRelief,vUV);if(origin.b<0.5)return 1.0;float start=heightMm(origin);vec2 direction=vec2(ray.x,-ray.y)/xy;float visible=1.0;for(int i=0;i<12;i++){float distance=uReliefTexelMm*1.2*pow(1.6,float(i));vec2 uv=vUV+direction*distance/uSpanMm;if(uv.x>=0.0 && uv.x<=1.0 && uv.y>=0.0 && uv.y<=1.0){vec4 h=texture2D(uRelief,uv);float clearance=heightMm(h)-(start+distance*ray.z/xy);float bias=0.025+distance*0.006;if(h.b>0.95)visible=min(visible,1.0-smoothstep(bias,bias+0.045,clearance));}}return visible;}",
    // Studio softboxes are reflected by the clear coat, not baked onto RGB.
    "float softbox(vec3 r,vec2 center,vec2 size,float edge){vec2 q=abs(r.xy/max(r.z,0.015)-center)-size;float rounded=length(max(q,vec2(0.0)))+min(max(q.x,q.y),0.0);return (1.0-smoothstep(-edge,edge,rounded))*smoothstep(0.08,0.25,r.z);}",
    "vec3 planeRay(vec3 r,vec3 p){float d=uLightScale*3.0;return vec3(r.xy*(1.0-p.z/d)+p.xy*r.z/d,r.z);}",
    // Supplier references show rounded window/softbox patches, not a bank of
    // hairline strips outlining every printed seam. Lights stay world-fixed.
    "float studio(vec3 r,vec3 p,float blur){vec3 f=planeRay(r,p);vec3 s=planeRay(vec3(r.z,r.y,r.x),vec3(p.z,p.y,p.x));vec3 other=planeRay(vec3(-r.z,r.y,-r.x),vec3(-p.z,p.y,-p.x));vec3 rear=planeRay(vec3(-r.x,r.y,-r.z),vec3(-p.x,p.y,-p.z));vec3 top=planeRay(vec3(r.x,-r.z,r.y),vec3(p.x,-p.z,p.y));vec3 bottom=planeRay(vec3(r.x,r.z,-r.y),vec3(p.x,p.z,-p.y));return 0.12+10.0*softbox(f,vec2(-0.32,0.35),vec2(0.26,0.22),blur)+3.2*softbox(f,vec2(0.42,-0.18),vec2(0.13,0.38),blur)+7.0*softbox(s,vec2(0.12,0.22),vec2(0.26,0.32),blur)+7.5*softbox(other,vec2(-0.08,0.26),vec2(0.24,0.36),blur)+5.5*softbox(rear,vec2(-0.15,0.25),vec2(0.27,0.28),blur)+8.0*softbox(top,vec2(0.10,0.18),vec2(0.35,0.23),blur)+3.0*softbox(bottom,vec2(-0.18,0.15),vec2(0.28,0.20),blur);}",
    // Canonical alpha is owned by the watertight front/back/side geometry, not
    // a second per-fragment discard (which punches MSAA holes at the join).
    "void main(){ vec4 tex=texture2D(uSource,vUV); tex.rgb=clamp(tex.rgb/max(tex.a,0.003921569),0.0,1.0);",
    "vec3 n=normalize(vNormal); if(vMaterial<0.5){vec4 encoded=texture2D(uDetailNormal,vUV);vec2 xy=vec2(dot(encoded.rg,vec2(65280.0,255.0)),dot(encoded.ba,vec2(65280.0,255.0)))/65535.0*2.0-1.0;vec3 objectN=normalize(vec3(xy*uReliefBoost,sqrt(max(0.00001,1.0-dot(xy,xy)))));n=normalize(mat3(uModel)*objectN);}",
    "if(vMaterial>0.01 && vMaterial<0.5){n=normalize(mix(n,normalize(vNormal),smoothstep(0.01,0.12,vMaterial)));}",
    "vec3 l=normalize(vec3(-0.5,0.75,1.3)); vec3 fill=normalize(vec3(0.9,-0.1,0.7)); vec3 eye=vec3(0.0,0.0,1.0);",
    "bool printed=vMaterial<0.5 || vMaterial>2.5;vec3 base=printed ? mix(tex.rgb,vec3(0.78),uNeutral) : vec3(1.0); float diffuse=0.76+0.17*max(dot(n,l),0.0)+0.07*max(dot(n,fill),0.0);",
    "if(uCeramic>0.5){diffuse=0.63+0.26*max(dot(n,l),0.0)+0.11*max(dot(n,fill),0.0);}",
    // Every reflection is evaluated in the stationary world studio. Never use
    // an object-space fallback: that paints an immovable bright patch on designs.
    // No coarse 128px overcoat: retain each local convex/concave transition.
    // Light visibility changes with depth AND camera rotation, not source RGB.
    "float blur=mix(0.10,0.018,uGloss);vec3 coatNormal=n;if(vMaterial<0.5){vec4 coatEncoded=texture2D(uCoatNormal,vUV);vec2 coatXY=vec2(dot(coatEncoded.rg,vec2(65280.0,255.0)),dot(coatEncoded.ba,vec2(65280.0,255.0)))/65535.0*2.0-1.0;vec3 coatObject=normalize(vec3(coatXY*uReliefBoost,sqrt(max(0.00001,1.0-dot(coatXY,coatXY)))));coatNormal=normalize(mat3(uModel)*coatObject);if(vMaterial>0.01)coatNormal=normalize(mix(coatNormal,normalize(vNormal),smoothstep(0.01,0.12,vMaterial)));}vec3 reflected=reflect(-eye,coatNormal);float visibility=(vMaterial<0.5 && uGloss>0.0) ? reliefVisibility(reflected) : 1.0;float worldBoxes=studio(reflected,vPosition,blur)*visibility;",
    "float facing=max(dot(coatNormal,eye),0.0);float grazing=0.955*pow(1.0-facing,5.0);float fresnel=0.045+grazing;float coat=(printed ? mix(1.0,0.35,uNeutral) : 0.80)*uGloss;",
    // Clear dielectric coat, no metallic ink/contour fallback. Radiance adds
    // white reflection even on dark colored print; grazing energy rolls off.
    "float edgeEnergy=worldBoxes*grazing;float reflection=worldBoxes*0.045+edgeEnergy/(1.0+edgeEnergy/0.14);vec3 baseLinear=pow(base,vec3(2.2))*diffuse;vec3 linear=baseLinear*(1.0-coat*fresnel*0.4)+vec3(coat*reflection);vec3 color=pow(max(linear,vec3(0.0)),vec3(1.0/2.2)); gl_FragColor=vec4(clamp(color,0.0,1.0),1.0); }"
  ].join("\n");

  // Fired porcelain has its own opaque body and continuous enamel, including
  // the white back and round-over. Keep the acrylic shader above unchanged:
  // its lighting, highlights and rendered pixels belong to a different product.
  var CERAMIC_FRAGMENT_SHADER = [
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif",
    "uniform sampler2D uSource; uniform sampler2D uDetailNormal; uniform sampler2D uCoatNormal; uniform mat4 uModel; uniform float uNeutral; uniform float uLightScale; uniform float uReliefBoost; uniform float uGloss; varying vec3 vNormal; varying vec3 vPosition; varying vec2 vUV; varying float vMaterial;",
    "vec3 reliefNormal(vec4 encoded){vec2 xy=vec2(dot(encoded.rg,vec2(65280.0,255.0)),dot(encoded.ba,vec2(65280.0,255.0)))/65535.0*2.0-1.0;return normalize(mat3(uModel)*normalize(vec3(xy*uReliefBoost,sqrt(max(0.00001,1.0-dot(xy,xy))))));}",
    // The world-space enclosure gives a reflection parallax as the object
    // rotates. Rounded rectangular sources have one coherent broad image;
    // their smaller companions catch the edge and individual raised forms.
    "vec3 porcelainPlane(vec3 r,vec3 p){float d=uLightScale*2.1;return vec3(r.xy*(1.0-p.z/d)+p.xy*r.z/d,r.z);}",
    "float porcelainBox(vec3 ray,vec2 center,vec2 size,float softness){vec2 q=abs(ray.xy/max(ray.z,0.015)-center)-size;float rounded=length(max(q,vec2(0.0)))+min(max(q.x,q.y),0.0);return (1.0-smoothstep(-softness,softness,rounded))*smoothstep(0.08,0.25,ray.z);}",
    "vec3 porcelainStudio(vec3 r,vec3 p,float softness){",
    "vec3 front=porcelainPlane(r,p);vec3 left=porcelainPlane(vec3(-r.z,r.y,-r.x),vec3(-p.z,p.y,-p.x));vec3 right=porcelainPlane(vec3(r.z,r.y,r.x),vec3(p.z,p.y,p.x));vec3 top=porcelainPlane(vec3(r.x,-r.z,r.y),vec3(p.x,-p.z,p.y));vec3 rear=porcelainPlane(vec3(-r.x,r.y,-r.z),vec3(-p.x,p.y,-p.z));vec3 bottom=porcelainPlane(vec3(r.x,r.z,-r.y),vec3(p.x,p.z,-p.y));",
    "vec3 room=mix(vec3(0.13),vec3(0.40),smoothstep(-0.6,0.8,r.y));",
    "room+=vec3(7.0)*porcelainBox(front,vec2(-0.25,0.30),vec2(0.31,0.25),softness);",
    "room+=vec3(6.0)*porcelainBox(front,vec2(0.44,-0.12),vec2(0.065,0.66),softness*0.65);",
    "room+=vec3(6.0)*porcelainBox(left,vec2(0.05,0.20),vec2(0.24,0.48),softness);",
    "room+=vec3(5.8)*porcelainBox(right,vec2(-0.04,0.15),vec2(0.18,0.53),softness);",
    "room+=vec3(6.2)*porcelainBox(top,vec2(-0.10,0.08),vec2(0.48,0.27),softness);",
    "room+=vec3(7.4)*porcelainBox(top,vec2(0.48,-0.34),vec2(0.19,0.42),softness*0.8);",
    "room+=vec3(6.5)*porcelainBox(rear,vec2(-0.22,0.22),vec2(0.29,0.42),softness);",
    "room+=vec3(4.5)*porcelainBox(bottom,vec2(0.05,0.03),vec2(0.24,0.16),softness);",
    // The low horizontal panel also reaches ordinary 20-35 degree tilts;
    // its edge crosses the reflected disc instead of flooding the whole face.
    "room+=vec3(7.4)*porcelainBox(bottom,vec2(0.48,0.34),vec2(0.19,0.42),softness*0.8);return room;}",
    "void main(){bool printed=vMaterial<0.5;vec3 n=normalize(vNormal);vec3 coatNormal=n;",
    // Preserve height detail for the body and the small specular lobe. The
    // wider derivative field stabilizes the main softbox without replacing
    // the print's local relief or the mesh normals on the ceramic bevel.
    "if(printed){float roundover=smoothstep(0.01,0.12,vMaterial);n=normalize(mix(reliefNormal(texture2D(uDetailNormal,vUV)),n,roundover));vec3 smoothN=reliefNormal(texture2D(uCoatNormal,vUV));coatNormal=normalize(mix(n,smoothN,0.78*(1.0-roundover)));}",
    "vec4 tex=texture2D(uSource,vUV);vec3 ink=clamp(tex.rgb/max(tex.a,0.003921569),0.0,1.0);vec3 porcelain=vec3(0.97);vec3 base=printed ? mix(ink*porcelain,vec3(0.78),uNeutral) : porcelain;",
    // Leave headroom above the body white so enamel can produce a visible
    // reflection on unprinted porcelain. Soft wrap light keeps the dense
    // substrate luminous. Neutral studio lights keep the white back achromatic.
    "vec3 eye=vec3(0.0,0.0,1.0);vec3 key=normalize(vec3(-0.5,0.8,1.2));vec3 fill=normalize(vec3(0.85,-0.2,0.8));float keyWrap=clamp((dot(n,key)+0.22)/1.22,0.0,1.0);float fillWrap=max(dot(n,fill),0.0);",
    "vec3 bodyLight=vec3(0.58+0.225*keyWrap+0.10*fillWrap);bodyLight*=0.96+0.04*n.y;vec3 body=pow(base,vec3(2.2))*bodyLight;",
    "float softness=mix(0.15,0.018,uGloss);vec3 reflected=reflect(-eye,coatNormal);vec3 environment=porcelainStudio(reflected,vPosition,softness);float facing=max(dot(coatNormal,eye),0.0);float grazingFresnel=0.955*pow(1.0-facing,5.0);float fresnel=0.045+grazingFresnel;float glaze=uGloss*mix(1.0,0.42,uNeutral);",
    // A restrained tighter lobe uses the detailed normal: light gathers on
    // raised lettering and print shoulders beneath the broad glaze image.
    "float localKey=pow(max(dot(n,normalize(key+eye)),0.0),mix(35.0,180.0,uGloss));float localFill=pow(max(dot(n,normalize(fill+eye)),0.0),mix(50.0,220.0,uGloss));vec3 local=vec3(localKey*0.22+localFill*0.12);",
    // Bright studio radiance and grazing Fresnel otherwise clip the entire
    // print to white at the side preset. Roll off only that extra grazing
    // energy; the normal-facing glaze and its ordinary-angle boxes stay
    // unchanged. The smooth shoulder retains a bright edge without a jump.
    "vec3 grazingReflection=environment*grazingFresnel;vec3 reflection=environment*0.045+grazingReflection/(vec3(1.0)+grazingReflection/0.10);",
    "vec3 linear=body*(1.0-glaze*fresnel*0.45)+glaze*(reflection+local);vec3 color=pow(max(linear,vec3(0.0)),vec3(1.0/2.2));gl_FragColor=vec4(clamp(color,0.0,1.0),1.0);}",
  ].join("\n");

  function create(canvas, options) {
    options = options || {};
    var configuredProduct = options.product_type === undefined ? null : productType(options.product_type);
    var activeProduct = configuredProduct || "acrylic";
    if (!canvas || typeof canvas.getContext !== "function") throw new Error("A canvas is required for the local 3D viewer.");
    var gl = canvas.getContext("webgl", { alpha: true, antialias: true, depth: true, preserveDrawingBuffer: true, premultipliedAlpha: true })
      || canvas.getContext("experimental-webgl", { alpha: true, antialias: true, depth: true, preserveDrawingBuffer: true, premultipliedAlpha: true });
    if (!gl) throw new Error("WebGL is unavailable. Enable GPU rendering to use the local 3D viewer.");
    var disposed = false, lost = false, program = null, programProduct = null, texture = null, normalTexture = null, coatTexture = null, reliefTexture = null, reliefSource = null, chunks = [], data = null, geometry = null, displaySurface = null;
    var lastExportInfo = null;
    var physical = dimensions(options.dimensions), look = appearance(options.appearance), state = { yaw: 0, pitch: 0, zoom: 1, surfaceMode: false }, listeners = [], dragging = null, locations, lastDpr = 1;
    function meshOptions(value, prepared) { return { maxGrid: options.maxGrid, rimBoost: value.rimBoost, reliefBoost: value.reliefBoost, displaySurface: prepared || displaySurface }; }
    var doc = options.document || canvas.ownerDocument || (typeof document !== "undefined" ? document : null);
    var win = options.window || (doc && doc.defaultView) || (typeof window !== "undefined" ? window : null);
    function check() { if (disposed) throw new Error("The 3D viewer has been disposed."); if (lost) throw new Error("The 3D graphics context is unavailable."); }
    function compile(type, source) {
      var shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) { var error = gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error("3D shader failed: " + error); }
      return shader;
    }
    function setup(type) {
      type = type || activeProduct;
      var vs = null, fs = null, nextProgram = null, nextLocations;
      // Compile before replacing a working program or committing the first
      // ceramic payload. An unsupported GPU shader leaves the empty viewer
      // and its original program intact, so another valid load can recover.
      try {
        vs = compile(gl.VERTEX_SHADER, VERTEX_SHADER); fs = compile(gl.FRAGMENT_SHADER, type === "acrylic" ? FRAGMENT_SHADER : CERAMIC_FRAGMENT_SHADER);
        nextProgram = gl.createProgram(); gl.attachShader(nextProgram, vs); gl.attachShader(nextProgram, fs); gl.linkProgram(nextProgram);
        if (!gl.getProgramParameter(nextProgram, gl.LINK_STATUS)) throw new Error("3D material failed: " + gl.getProgramInfoLog(nextProgram));
        nextLocations = { position: gl.getAttribLocation(nextProgram, "aPosition"), normal: gl.getAttribLocation(nextProgram, "aNormal"), uv: gl.getAttribLocation(nextProgram, "aUV"), material: gl.getAttribLocation(nextProgram, "aMaterial"),
          mvp: gl.getUniformLocation(nextProgram, "uMVP"), model: gl.getUniformLocation(nextProgram, "uModel"), source: gl.getUniformLocation(nextProgram, "uSource"), detailNormal: gl.getUniformLocation(nextProgram, "uDetailNormal"), coatNormal: gl.getUniformLocation(nextProgram, "uCoatNormal"), relief: gl.getUniformLocation(nextProgram, "uRelief"), spanMm: gl.getUniformLocation(nextProgram, "uSpanMm"), heightMm: gl.getUniformLocation(nextProgram, "uHeightMm"), reliefTexelMm: gl.getUniformLocation(nextProgram, "uReliefTexelMm"), neutral: gl.getUniformLocation(nextProgram, "uNeutral"), lightScale: gl.getUniformLocation(nextProgram, "uLightScale"), boost: gl.getUniformLocation(nextProgram, "uReliefBoost"), gloss: gl.getUniformLocation(nextProgram, "uGloss"), ceramic: gl.getUniformLocation(nextProgram, "uCeramic") };
      } catch (error) {
        if (nextProgram) gl.deleteProgram(nextProgram);
        throw error;
      } finally {
        if (vs) gl.deleteShader(vs); if (fs) gl.deleteShader(fs);
      }
      if (program) gl.deleteProgram(program);
      program = nextProgram; programProduct = type; locations = nextLocations;
    }
    function releaseMesh(keepTextures) {
      for (var i = 0; i < chunks.length; i++) { gl.deleteBuffer(chunks[i].vertex); gl.deleteBuffer(chunks[i].index); }
      chunks = []; if (keepTextures) return;
      if (texture) gl.deleteTexture(texture); texture = null;
      if (normalTexture) gl.deleteTexture(normalTexture); normalTexture = null;
      if (coatTexture) gl.deleteTexture(coatTexture); coatTexture = null;
      if (reliefTexture) gl.deleteTexture(reliefTexture); reliefTexture = null; reliefSource = null;
    }
    function upload(keepTextures) {
      releaseMesh(keepTextures); if (!geometry) return;
      var parts = splitGeometry(geometry), i;
      for (i = 0; i < parts.length; i++) {
        var vertex = gl.createBuffer(), index = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, vertex); gl.bufferData(gl.ARRAY_BUFFER, parts[i].vertices, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, parts[i].indices, gl.STATIC_DRAW);
        chunks.push({ vertex: vertex, index: index, count: parts[i].indices.length });
      }
      if (keepTextures) return;
      var textureCap = Math.min(MAX_TEXTURE, gl.getParameter(gl.MAX_TEXTURE_SIZE) || MAX_TEXTURE);
      var edgePadding = Math.min(32, 2 + Math.ceil(2 * Math.min(textureCap, Math.max(data.width, data.height)) / (options.maxGrid || DEFAULT_GRID)));
      var source = textureData(data, textureCap, edgePadding);
      texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, source.width, source.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, source.rgba);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      // Acrylic reflections retain the prepared geometry's local resolution;
      // ceramic keeps its established independent glaze field unchanged.
      var coatSource = normalTextureData(data, physical, Math.min(activeProduct === "acrylic" ? 1024 : 128, textureCap), displaySurface, look);
      coatTexture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, coatTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, coatSource.width, coatSource.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, coatSource.rgba);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (activeProduct === "acrylic") {
        reliefSource = reliefTextureData(data, displaySurface, look);
        reliefTexture = exportTexture(reliefSource);
      }
      var normalSource = normalTextureData(data, physical, Math.min(MAX_TEXTURE, gl.getParameter(gl.MAX_TEXTURE_SIZE) || MAX_TEXTURE), displaySurface, look);
      normalTexture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, normalTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, normalSource.width, normalSource.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, normalSource.rgba);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    function draw(width, height, exportMatrices) {
      check(); var matrices = exportMatrices || cameraMatrices(state, geometry, width, height);
      gl.viewport(0, 0, width, height); gl.clearColor(0, 0, 0, 0); gl.clearDepth(1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (!geometry) return;
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK); gl.frontFace(gl.CCW); gl.disable(gl.BLEND);
      gl.useProgram(program); gl.uniformMatrix4fv(locations.mvp, false, matrices.mvp); gl.uniformMatrix4fv(locations.model, false, matrices.model);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture); gl.uniform1i(locations.source, 0);
      gl.activeTexture(gl.TEXTURE0+1); gl.bindTexture(gl.TEXTURE_2D, normalTexture); gl.uniform1i(locations.detailNormal, 1);
      gl.activeTexture(gl.TEXTURE0+2); gl.bindTexture(gl.TEXTURE_2D, coatTexture); gl.uniform1i(locations.coatNormal, 2);
      if (activeProduct === "acrylic") {
        var bounds = boundsOf(data), mm = physical.heightMm / (bounds.y1 - bounds.y0);
        gl.activeTexture(gl.TEXTURE0+3); gl.bindTexture(gl.TEXTURE_2D, reliefTexture); gl.uniform1i(locations.relief, 3);
        gl.uniform2f(locations.spanMm, data.width * mm, data.height * mm);
        gl.uniform1f(locations.heightMm, physical.reliefMm * (reliefSource.combinedScale === 24 ? 24 : look.reliefBoost));
        gl.uniform1f(locations.reliefTexelMm, Math.max(data.width * mm / reliefSource.width, data.height * mm / reliefSource.height));
      }
      gl.uniform1f(locations.neutral, state.surfaceMode ? 1 : 0);
      gl.uniform1f(locations.lightScale, physical.heightMm);
      gl.uniform1f(locations.boost, data && data.support && !ceramicData(data) ? 1 : look.reliefBoost); gl.uniform1f(locations.gloss, look.gloss);
      gl.uniform1f(locations.ceramic, activeProduct === "acrylic" ? 0 : 1);
      for (var i = 0; i < chunks.length; i++) {
        gl.bindBuffer(gl.ARRAY_BUFFER, chunks[i].vertex); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, chunks[i].index);
        var attrs = [[locations.position, 3, 0], [locations.normal, 3, 12], [locations.uv, 2, 24], [locations.material, 1, 32]];
        for (var j = 0; j < attrs.length; j++) { gl.enableVertexAttribArray(attrs[j][0]); gl.vertexAttribPointer(attrs[j][0], attrs[j][1], gl.FLOAT, false, STRIDE * 4, attrs[j][2]); }
        gl.drawElements(gl.TRIANGLES, chunks[i].count, gl.UNSIGNED_SHORT, 0);
      }
    }
    function exportTexture(source) {
      var next = gl.createTexture();
      try {
        if (!next) throw new Error("The GPU could not allocate the native export texture.");
        gl.bindTexture(gl.TEXTURE_2D, next);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, source.width, source.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, source.rgba);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        if (gl.getError() !== gl.NO_ERROR) throw new Error("This GPU has insufficient memory for the native-resolution mockup. No reduced-resolution export was made.");
        return next;
      } catch (error) { if (next) gl.deleteTexture(next); throw error; }
    }
    function exportFullPNG(settings) {
      check(); lastExportInfo = null;
      if (!geometry || !data) throw new Error("Load a depth map before exporting the complete 3D view.");
      settings = settings || {};
      var width = settings.width === undefined ? data.width : settings.width, height = settings.height === undefined ? data.height : settings.height;
      if (!finite(width) || !finite(height) || width % 1 || height % 1 || width < 1 || height < 1 || width > 32767 || height > 32767 || width * height > MAX_PIXELS) {
        throw new Error("Full mockup dimensions must be whole pixels, no more than 40 MP / 32767 px per side. No resizing was performed.");
      }
      var textureLimit = gl.getParameter(gl.MAX_TEXTURE_SIZE), renderLimit = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), viewportLimit = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
      if (!finite(textureLimit) || Math.max(data.width, data.height) > textureLimit) {
        throw new Error("The source is " + data.width + " x " + data.height + " px but this GPU supports textures up to " + textureLimit + " px. Native sharpness cannot be preserved on this device; no downscaled export was made.");
      }
      var tileLimit = Math.floor(Math.min(2048, renderLimit || 2048, viewportLimit ? viewportLimit[0] : 2048, viewportLimit ? viewportLimit[1] : 2048));
      if (tileLimit < 16 || !doc || !doc.createElement) throw new Error("This device cannot create the full-resolution export canvas.");
      var full = fullCameraMatrices(state, geometry, null, width, height, settings.padding);
      var output = doc.createElement("canvas"), outputContext;
      try {
        output.width = width; output.height = height; outputContext = output.getContext("2d");
        if (!outputContext || output.width !== width || output.height !== height) throw new Error("This device cannot allocate the requested native-resolution export. No smaller image was made.");
      } catch (allocationError) { output.width = 1; output.height = 1; throw allocationError; }
      var oldW = canvas.width, oldH = canvas.height, oldTexture = texture, oldNormal = normalTexture;
      var nativeTexture = null, nativeNormal = null, tiles = 0, nativeSize = Math.max(data.width, data.height), gutter = 2, core = tileLimit - gutter * 2;
      try {
        // Drop stale, unrelated GL diagnostics before testing our allocations.
        for (var clear = 0; clear < 8 && gl.getError() !== gl.NO_ERROR; clear++) {}
        nativeTexture = exportTexture(textureData(data, nativeSize, 32));
        nativeNormal = exportTexture(normalTextureData(data, physical, nativeSize, displaySurface, look));
        texture = nativeTexture; normalTexture = nativeNormal;
        outputContext.clearRect(0, 0, width, height);
        for (var y = 0; y < height; y += core) for (var x = 0; x < width; x += core) {
          check();
          var cw = Math.min(core, width - x), ch = Math.min(core, height - y);
          var left = Math.min(gutter, x), top = Math.min(gutter, y), right = Math.min(gutter, width - x - cw), bottom = Math.min(gutter, height - y - ch);
          var tw = cw + left + right, th = ch + top + bottom;
          canvas.width = tw; canvas.height = th;
          if (gl.drawingBufferWidth !== tw || gl.drawingBufferHeight !== th) throw new Error("The GPU cannot render the requested native-resolution tile. Export was stopped, not resized.");
          draw(tw, th, tileCameraMatrices(full, width, height, x - left, y - top, tw, th)); gl.finish();
          if (gl.getError() !== gl.NO_ERROR || (gl.isContextLost && gl.isContextLost())) throw new Error("The graphics device ran out of resources during full export. Please retry after freeing GPU memory.");
          // Copy exact pixels, with overlap discarded. No panel-size screenshot
          // or 2D upscale is involved, and anti-aliasing has neighbour coverage.
          outputContext.drawImage(canvas, left, top, cw, ch, x, y, cw, ch); tiles++;
        }
        var png = output.toDataURL("image/png");
        if (!png || png.indexOf("data:image/png;base64,") !== 0) throw new Error("The device cannot encode a PNG at the requested size. No resized image was made.");
        lastExportInfo = { width: width, height: height, sourceWidth: data.width, sourceHeight: data.height,
          nativeTexture: true, nativeNormals: true, tiles: tiles, padding: full.padding, yaw: state.yaw, pitch: state.pitch,
          cord: "none", holeId: null, bounds: full.bounds };
        return png;
      } finally {
        texture = oldTexture; normalTexture = oldNormal;
        if (!lost) { if (nativeTexture) gl.deleteTexture(nativeTexture); if (nativeNormal) gl.deleteTexture(nativeNormal); }
        canvas.width = oldW; canvas.height = oldH;
        // Release the large assembler even when native upload/encoding failed.
        output.width = 1; output.height = 1;
        if (!disposed && !lost && !(gl.isContextLost && gl.isContextLost())) draw(oldW, oldH);
      }
    }
    function render() {
      check(); var dpr = Math.min(2, options.pixelRatio || (win && win.devicePixelRatio) || 1);
      var width = Math.max(1, Math.round((canvas.clientWidth || canvas.width / lastDpr || 400) * dpr));
      var height = Math.max(1, Math.round((canvas.clientHeight || canvas.height / lastDpr || 400) * dpr));
      if (canvas.width !== width) canvas.width = width; if (canvas.height !== height) canvas.height = height;
      lastDpr = dpr;
      draw(width, height); return api;
    }
    function listen(target, name, callback, opts) {
      if (target && target.addEventListener) { target.addEventListener(name, callback, opts || false); listeners.push([target, name, callback, opts || false]); }
    }
    function down(event) { if (event.button !== undefined && event.button !== 0) return; dragging = { x: event.clientX, y: event.clientY }; if (event.preventDefault) event.preventDefault(); }
    function move(event) {
      if (!dragging || disposed || lost) return;
      state.yaw += (event.clientX - dragging.x) * 0.009; state.pitch += (event.clientY - dragging.y) * 0.009;
      dragging = { x: event.clientX, y: event.clientY }; render();
    }
    function up() { dragging = null; }
    function wheel(event) {
      if (disposed || lost) return; var delta = event.deltaY !== undefined ? event.deltaY : -(event.wheelDelta || 0);
      state.zoom = clamp(state.zoom * Math.exp(-clamp(delta, -1000, 1000) * 0.001), 0.2, 8);
      if (event.preventDefault) event.preventDefault(); render();
    }
    var api = {
      setData: function (value) {
        check();
        var type = value && value.product_type !== undefined ? productType(value.product_type) : (configuredProduct || "acrylic");
        if ((configuredProduct && configuredProduct !== type) || (data && activeProduct !== type)) throw new Error("Dispose the 3D viewer before changing product_type.");
        var normalized = withProduct(value, type), prepared = prepareDisplaySurface(validateData(normalized));
        var next = buildGeometry(normalized, physical, meshOptions(look, prepared));
        if (programProduct !== type) setup(type);
        data = normalized; activeProduct = type; displaySurface = prepared; geometry = next;
        upload(); render(); return geometryInfo();
      },
      setDimensions: function (value) { check(); var next = dimensions(value); var mesh = data ? buildGeometry(data, next, meshOptions(look)) : null; physical = next; geometry = mesh; if (mesh) { upload(); } render(); return geometryInfo(); },
      setAppearance: function (value) {
        check(); value = value || {};
        var next = appearance({ rimBoost: value.rimBoost === undefined ? look.rimBoost : value.rimBoost,
          reliefBoost: value.reliefBoost === undefined ? look.reliefBoost : value.reliefBoost,
          gloss: value.gloss === undefined ? look.gloss : value.gloss }), mesh = null;
        if (data && (next.reliefBoost !== look.reliefBoost || next.rimBoost !== look.rimBoost)) mesh = buildGeometry(data, physical, meshOptions(next));
        look = next; if (mesh) { geometry = mesh; upload(!data.support); }
        render(); return geometryInfo();
      },
      preset: function (name) {
        check(); var poses = { front: [0, 0], back: [PI, 0], side: [PI * 80 / 180, 0], right: [PI / 2, 0], left: [-PI / 2, 0], top: [0, PI / 2] };
        if (!poses[name]) throw new Error("Unknown 3D camera preset."); state.yaw = poses[name][0]; state.pitch = poses[name][1]; render(); return api;
      },
      render: render,
      setView: function (value) {
        check(); value = value || {};
        var yaw = value.yaw === undefined ? state.yaw : value.yaw, pitch = value.pitch === undefined ? state.pitch : value.pitch, zoom = value.zoom === undefined ? state.zoom : value.zoom;
        if (!finite(yaw) || !finite(pitch) || !finite(zoom) || zoom < .2 || zoom > 8) throw new Error("Camera angles must be finite radians; zoom must be between 0.2 and 8.");
        state.yaw = yaw; state.pitch = pitch; state.zoom = zoom; render(); return api;
      },
      getView: function () { return { yaw: state.yaw, pitch: state.pitch, zoom: state.zoom }; },
      // Compatibility with older controllers: stale cord preferences are ignored.
      // These calls never generate geometry, alter the template hole or allocate GPU resources.
      setCord: function () { check(); return api; },
      getCord: function () { return { type: "none", holeId: null }; },
      getHoles: function () { check(); return []; },
      exportFullPNG: exportFullPNG,
      getLastExportInfo: function () { return lastExportInfo ? JSON.parse(JSON.stringify(lastExportInfo)) : null; },
      setSurfaceMode: function (neutral) { check(); if (typeof neutral !== "boolean") throw new Error("Surface mode must be boolean."); state.surfaceMode = neutral; render(); return api; },
      getState: function () { return { yaw: state.yaw, pitch: state.pitch, zoom: state.zoom, surfaceMode: state.surfaceMode, dimensions: dimensions(physical), appearance: appearance(look), geometry: geometryInfo() }; },
      exportPNG: function (settings) {
        check(); if (!geometry) throw new Error("Load a depth map before exporting the 3D view."); settings = settings || {};
        var oldW = canvas.width, oldH = canvas.height, width = settings.width || oldW, height = settings.height || Math.round(width * oldH / oldW);
        if (!finite(width) || !finite(height) || width % 1 || height % 1 || width < 1 || height < 1 || width > 8192 || height > 8192 || width * height > 16777216) throw new Error("PNG dimensions exceed the local export budget (16 MP / 8192 px).");
        var renderLimit = gl.MAX_RENDERBUFFER_SIZE === undefined ? 8192 : gl.getParameter(gl.MAX_RENDERBUFFER_SIZE);
        if (finite(renderLimit) && (width > renderLimit || height > renderLimit)) throw new Error("PNG dimensions exceed this GPU's renderbuffer limit.");
        if (Math.abs(width / height - oldW / oldH) > 1 / Math.min(width, height)) throw new Error("PNG must preserve the current viewport aspect ratio and camera framing.");
        try { canvas.width = width; canvas.height = height; draw(width, height); gl.finish(); return canvas.toDataURL("image/png"); }
        finally { canvas.width = oldW; canvas.height = oldH; if (!disposed && !lost) draw(oldW, oldH); }
      },
      dispose: function () {
        if (disposed) return; dragging = null;
        for (var i = 0; i < listeners.length; i++) listeners[i][0].removeEventListener(listeners[i][1], listeners[i][2], listeners[i][3]); listeners = [];
        if (!lost) { releaseMesh(); if (program) gl.deleteProgram(program); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); }
        data = null; geometry = null; displaySurface = null; program = null; chunks = []; texture = null; normalTexture = null; coatTexture = null; reliefTexture = null; reliefSource = null; disposed = true;
        lastExportInfo = null;
      }
    };
    function geometryInfo() {
      return geometry ? { widthMm: geometry.widthMm, heightMm: geometry.heightMm, totalMm: 3, reliefMm: physical.reliefMm,
        minimumMm: physical.minimumMm, grid: geometry.grid, triangles: geometry.indices.length / 3,
        approximateSilhouette: geometry.approximateSilhouette, nativeDepth: geometry.nativeDepth,
        rimBoost: activeProduct === "acrylic" ? look.rimBoost : 0, reliefBoost: look.reliefBoost,
        acrylicThickness: activeProduct === "acrylic" ? physical.minimumMm : 0, substrateThickness: physical.minimumMm,
        product_type: activeProduct, bevelRadiusMm: geometry.bevelRadiusMm, fixedTemplateShape: geometry.fixedTemplateShape,
        material: activeProduct === "acrylic" ? "acrylic-studio" : "glazed-porcelain", opaqueSubstrate: activeProduct !== "acrylic",
        displayMaxZ: geometry.maxZ, illustrationOnly: true } : null;
    }
    setup(); listen(canvas, "mousedown", down); listen(win || canvas, "mousemove", move); listen(win || canvas, "mouseup", up);
    listen(canvas, "wheel", wheel, { passive: false }); listen(win, "resize", function () { if (!disposed && !lost) render(); });
    listen(canvas, "webglcontextlost", function (event) { if (event.preventDefault) event.preventDefault(); lost = true; dragging = null; });
    listen(canvas, "webglcontextrestored", function () { if (disposed) return; lost = false; program = null; programProduct = null; chunks = []; texture = null; normalTexture = null; coatTexture = null; reliefTexture = null; reliefSource = null; setup(); upload(); render(); });
    render(); return api;
  }
  return { create: create, buildGeometry: buildGeometry, splitGeometry: splitGeometry,
    validateData: validateData, dimensions: dimensions, appearance: appearance, productType: productType, cameraMatrices: cameraMatrices, fullCameraMatrices: fullCameraMatrices, tileCameraMatrices: tileCameraMatrices, prepareDisplaySurface: prepareDisplaySurface,
    textureData: textureData, normalTextureData: normalTextureData, reliefTextureData: reliefTextureData, shaders: { vertex: VERTEX_SHADER, fragment: FRAGMENT_SHADER, ceramicFragment: CERAMIC_FRAGMENT_SHADER },
    constants: { MAX_PIXELS: MAX_PIXELS, MAX_GRID: MAX_GRID, DEFAULT_GRID: DEFAULT_GRID, STRIDE: STRIDE, MAX_TEXTURE: MAX_TEXTURE } };
}));
