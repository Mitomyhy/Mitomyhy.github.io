/* Scrabble 20261009-R5: English face values; bounded decorative-name layouts. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.UOYScrabbleLayout = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Standard English face values. Decorative layouts intentionally do not use
  // the real game's letter-frequency bag, dictionary, or premium-square rules.
  var LETTER_SCORES = {
    A:1, B:3, C:3, D:2, E:1, F:4, G:2, H:4, I:1, J:8, K:5, L:1, M:3,
    N:1, O:1, P:3, Q:10, R:1, S:1, T:1, U:1, V:4, W:4, X:8, Y:4, Z:10,
    blank:0
  };
  var scoreValues = {}, scoreKey;
  for (scoreKey in LETTER_SCORES) if (Object.prototype.hasOwnProperty.call(LETTER_SCORES, scoreKey)) scoreValues[scoreKey] = LETTER_SCORES[scoreKey];
  if (typeof Object.freeze === 'function') Object.freeze(scoreValues);

  function letterScore(letter) {
    if (letter === '') return LETTER_SCORES.blank;
    if (typeof letter !== 'string' || !/^[A-Z]$/.test(letter)) throw new Error('Điểm ô chữ chỉ nhận một chữ A–Z hoặc chuỗi rỗng cho ô blank.');
    return LETTER_SCORES[letter];
  }

  var LIMITS = { names: 24, letters: 30, cells: 400, variants: 60 };
  var TARGETS = [1, 2 / 3, 3 / 2], CATEGORIES = ['square', 'portrait', 'landscape'];
  var DIACRITICS = [
    ['A', 'ÀÁÂÃÄÅĀĂĄǍẠẢẤẦẨẪẬẮẰẲẴẶ'], ['C', 'ÇĆĈĊČ'],
    ['D', 'ÐĎĐ'], ['E', 'ÈÉÊËĒĔĖĘĚẸẺẼẾỀỂỄỆ'],
    ['G', 'ĜĞĠĢ'], ['H', 'ĤĦ'], ['I', 'ÌÍÎÏĨĪĬĮİǏỊỈ'],
    ['J', 'Ĵ'], ['K', 'Ķ'], ['L', 'ĹĻĽĿŁ'], ['N', 'ÑŃŅŇ'],
    ['O', 'ÒÓÔÕÖØŌŎŐǑƠỌỎỐỒỔỖỘỚỜỞỠỢ'], ['R', 'ŔŖŘ'],
    ['S', 'ŚŜŞŠ'], ['T', 'ŢŤŦ'], ['U', 'ÙÚÛÜŨŪŬŮŰŲǓƯỤỦỨỪỬỮỰ'],
    ['W', 'Ŵ'], ['Y', 'ÝŶŸỲỴỶỸ'], ['Z', 'ŹŻŽ']
  ];

  function normalizeWord(label) {
    var upper = String(label).toUpperCase(), normalized = upper, i;
    for (i = 0; i < DIACRITICS.length; i += 1) {
      normalized = normalized.replace(new RegExp('[' + DIACRITICS[i][1] + ']', 'g'), DIACRITICS[i][0]);
    }
    if (typeof normalized.normalize === 'function') normalized = normalized.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    normalized = normalized.replace(/\u00c6/g, 'AE').replace(/\u0152/g, 'OE');
    normalized = normalized.replace(/[\s\-\u2010-\u2015'\u2018\u2019]/g, '');
    return normalized;
  }

  function parseNames(text) {
    var parts = String(text == null ? '' : text).split(/[\n\r,;]+/), names = [], warnings = [], errors = [], total = 0, i, label, word;
    for (i = 0; i < parts.length; i += 1) {
      if (/[\x00-\x1f\x7f]/.test(parts[i])) {
        errors.push('Tên thứ ' + (i + 1) + ' có ký tự điều khiển/tab. Dùng mỗi tên một dòng, dấu phẩy hoặc dấu chấm phẩy.');
        continue;
      }
      label = parts[i].replace(/^\s+|\s+$/g, '');
      if (!label) continue;
      if (label.length > 200) {
        errors.push('Nhãn tên thứ ' + (i + 1) + ' vượt quá 200 ký tự; không tự cắt nội dung.');
        continue;
      }
      word = normalizeWord(label);
      if (!/^[A-Z]+$/.test(word)) {
        errors.push('Tên "' + label + '" có ký tự không hỗ trợ. Chỉ nhận chữ A–Z; không nhận số hoặc biểu tượng.');
        continue;
      }
      if (word.length > LIMITS.letters) {
        errors.push('Tên "' + label + '" vượt quá ' + LIMITS.letters + ' chữ.');
        continue;
      }
      if (word !== label.toUpperCase()) warnings.push('Tên "' + label + '" được ghép thành ' + word + ' (bỏ dấu/khoảng trắng/dấu nối).');
      names.push({ id: 'name-' + (names.length + 1), label: label, word: word });
      total += word.length;
    }
    if (!names.length && !errors.length) errors.push('Nhập ít nhất một tên.');
    if (names.length > LIMITS.names) errors.push('Tối đa ' + LIMITS.names + ' tên trong một bố cục; không tên nào được tự cắt bỏ.');
    if (total > LIMITS.cells) errors.push('Tổng độ dài tên vượt ' + LIMITS.cells + ' chữ. Giảm số tên hoặc chia thành nhiều bố cục.');
    return { names: errors.length ? [] : names, warnings: warnings, errors: errors };
  }

  function canonicalNames(input) {
    var parsed, out = [], seen = {}, total = 0, i, n, id, word, label;
    if (typeof input === 'string') {
      parsed = parseNames(input);
      if (parsed.errors.length) throw new Error(parsed.errors.join('\n'));
      input = parsed.names;
    }
    if (!Array.isArray(input) || !input.length || input.length > LIMITS.names) throw new Error('Cần từ 1 đến ' + LIMITS.names + ' tên hợp lệ.');
    for (i = 0; i < input.length; i += 1) {
      n = input[i];
      if (typeof n === 'string') n = { id: 'name-' + (i + 1), label: n, word: normalizeWord(n) };
      id = n && String(n.id == null ? 'name-' + (i + 1) : n.id);
      word = n && String(n.word == null ? normalizeWord(n.label || '') : n.word);
      label = n && String(n.label == null ? word : n.label);
      if (!id || !/^[A-Za-z0-9_\-]{1,64}$/.test(id) || seen['$' + id] || !/^[A-Z]+$/.test(word) || word.length > LIMITS.letters) throw new Error('Tên hoặc mã tên không hợp lệ; mỗi tên phải có mã riêng và 1–30 chữ A–Z.');
      if (label.length > 200 || /[\x00-\x1f\x7f]/.test(label)) throw new Error('Nhãn tên chỉ nhận tối đa 200 ký tự và không có ký tự điều khiển/tab.');
      seen['$' + id] = true;
      out.push({ id: id, label: label, word: word });
      total += word.length;
    }
    if (total > LIMITS.cells) throw new Error('Tổng độ dài tên vượt ' + LIMITS.cells + ' chữ.');
    return out;
  }

  function key(r, c) { return r + ',' + c; }
  function compareText(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function boundsOf(placements) {
    var b = { minR: Infinity, minC: Infinity, maxR: -Infinity, maxC: -Infinity }, i, p;
    for (i = 0; i < placements.length; i += 1) {
      p = placements[i];
      b.minR = Math.min(b.minR, p.row); b.minC = Math.min(b.minC, p.col);
      b.maxR = Math.max(b.maxR, p.row + (p.direction === 'V' ? p.word.length - 1 : 0));
      b.maxC = Math.max(b.maxC, p.col + (p.direction === 'H' ? p.word.length - 1 : 0));
    }
    b.width = b.maxC - b.minC + 1; b.height = b.maxR - b.minR + 1;
    return b;
  }

  function makeState(placements, remaining) {
    var grid = Object.create(null), list = [], i, j, p, r, c, k, cell, crossings = 0;
    for (i = 0; i < placements.length; i += 1) {
      p = placements[i];
      for (j = 0; j < p.word.length; j += 1) {
        r = p.row + (p.direction === 'V' ? j : 0); c = p.col + (p.direction === 'H' ? j : 0); k = key(r, c); cell = grid[k];
        if (!cell) { cell = grid[k] = { row: r, col: c, letter: p.word.charAt(j), mask: 0, owners: [] }; list.push(cell); }
        else crossings += 1;
        cell.mask |= p.direction === 'H' ? 1 : 2; cell.owners.push(i);
      }
    }
    return { placements: placements, remaining: remaining, grid: grid, list: list, bounds: boundsOf(placements), crossings: crossings };
  }

  function allowed(state, word, row, col, direction) {
    var g = state.grid, dr = direction === 'V' ? 1 : 0, dc = direction === 'H' ? 1 : 0, mask = dc ? 1 : 2, i, r, c, cell, crosses = 0;
    if (g[key(row - dr, col - dc)] || g[key(row + dr * word.length, col + dc * word.length)]) return 0;
    for (i = 0; i < word.length; i += 1) {
      r = row + dr * i; c = col + dc * i; cell = g[key(r, c)];
      if (cell) {
        if (cell.letter !== word.charAt(i) || (cell.mask & mask) || cell.owners.length !== 1) return 0;
        crosses += 1;
      } else if (g[key(r - dc, c - dr)] || g[key(r + dc, c + dr)]) return 0;
    }
    return crosses;
  }

  function shapeCost(width, height, targetRatio) {
    var aspect = Math.max(width / height, height / width), target = targetRatio || 1;
    return width * height * 0.45 + Math.abs(Math.log(width / height / target)) * 65 + Math.max(0, aspect - 2) * 75;
  }
  function categoryOf(width, height) {
    var ratio = width / height;
    return ratio < 0.83 ? 'portrait' : ratio > 1.2 ? 'landscape' : 'square';
  }
  function componentsOf(state) {
    var parent = [], groups = {}, result = [], i, j, owner, cell;
    function find(n) { while (parent[n] !== n) { parent[n] = parent[parent[n]]; n = parent[n]; } return n; }
    for (i = 0; i < state.placements.length; i += 1) parent[i] = i;
    for (i = 0; i < state.list.length; i += 1) {
      cell = state.list[i];
      for (j = 1; j < cell.owners.length; j += 1) parent[find(cell.owners[j])] = find(cell.owners[0]);
    }
    for (i = 0; i < state.placements.length; i += 1) {
      owner = '$' + find(i);
      if (!groups[owner]) { groups[owner] = []; result.push(groups[owner]); }
      groups[owner].push(state.placements[i]);
    }
    return result;
  }

  function stateCost(state, target) {
    if (state.componentCount == null) state.componentCount = componentsOf(state).length;
    return (state.componentCount - 1) * 1000000 + shapeCost(state.bounds.width, state.bounds.height, target) - state.crossings * 7;
  }
  function stateSignature(state) {
    var b = state.bounds;
    if (!state.signature) state.signature = state.placements.map(function (p) { return p.nameId + ':' + (p.row - b.minR) + ',' + (p.col - b.minC) + p.direction; }).sort().join('|');
    return state.signature;
  }
  function diverseBeam(candidates, limit) {
    var sorted = TARGETS.map(function (target) { return candidates.slice().sort(function (a, b) { return stateCost(a, target) - stateCost(b, target) || compareText(stateSignature(a), stateSignature(b)); }); }), positions = [0,0,0], result = [], seen = {}, added = true, t, state, signature;
    while (result.length < limit && added) {
      added = false;
      for (t = 0; t < TARGETS.length && result.length < limit; t += 1) {
        while (positions[t] < sorted[t].length) {
          state = sorted[t][positions[t]++]; signature = stateSignature(state);
          if (!seen[signature]) { seen[signature] = true; result.push(state); added = true; break; }
        }
      }
    }
    return result;
  }

  function proposals(state, names, budget, cap) {
    var result = [], seen = Object.create(null), ri, ni, n, ci, cell, j, direction, row, col, signature, crosses, b = state.bounds, w, h;
    for (ri = 0; ri < state.remaining.length; ri += 1) {
      ni = state.remaining[ri]; n = names[ni];
      for (ci = 0; ci < state.list.length; ci += 1) {
        cell = state.list[ci]; if (cell.mask === 3) continue;
        direction = cell.mask === 1 ? 'V' : 'H';
        for (j = 0; j < n.word.length; j += 1) {
          if (n.word.charAt(j) !== cell.letter) continue;
          row = cell.row - (direction === 'V' ? j : 0); col = cell.col - (direction === 'H' ? j : 0);
          signature = ni + ':' + row + ',' + col + direction;
          if (seen[signature]) continue; seen[signature] = true;
          if (budget.checks >= budget.limit) { budget.capped = true; break; }
          budget.checks += 1;
          crosses = allowed(state, n.word, row, col, direction);
          if (!crosses) continue;
          w = Math.max(b.maxC, col + (direction === 'H' ? n.word.length - 1 : 0)) - Math.min(b.minC, col) + 1;
          h = Math.max(b.maxR, row + (direction === 'V' ? n.word.length - 1 : 0)) - Math.min(b.minR, row) + 1;
          result.push({ ni: ni, placement: { nameId: n.id, word: n.word, row: row, col: col, direction: direction }, width: w, height: h, crossings: crosses, key: signature });
        }
        if (budget.capped) break;
      }
      if (budget.capped) break;
    }
    var sorted = TARGETS.map(function (target) { return result.slice().sort(function (a, b2) { return shapeCost(a.width, a.height, target) - a.crossings * 7 - shapeCost(b2.width, b2.height, target) + b2.crossings * 7 || compareText(a.key, b2.key); }); }), positions = [0,0,0], picked = [], used = {}, added = true, t, proposal;
    while (picked.length < cap && added) {
      added = false;
      for (t = 0; t < TARGETS.length && picked.length < cap; t += 1) while (positions[t] < sorted[t].length) {
        proposal = sorted[t][positions[t]++]; if (!used[proposal.key]) { used[proposal.key] = true; picked.push(proposal); added = true; break; }
      }
    }
    return picked;
  }

  function normalized(placements) {
    var b = boundsOf(placements);
    return placements.map(function (p) { return { nameId: p.nameId, word: p.word, row: p.row - b.minR, col: p.col - b.minC, direction: p.direction }; });
  }
  function transpose(placements) {
    return placements.map(function (p) { return { nameId: p.nameId, word: p.word, row: p.col, col: p.row, direction: p.direction === 'H' ? 'V' : 'H' }; });
  }
  function packVariants(placements) {
    var components = componentsOf(makeState(placements, [])), pieces, target, maxTarget = 0, minTarget = 1, best = [null,null,null], fallback = null, order, rowGap, orientationMode, ti, i, item, variants, options, option, x, y, shelfH, placed, b, cost, category;
    if (components.length === 1) return [normalized(placements)];
    pieces = components.map(function (group, index) {
      var p = normalized(group), bb = boundsOf(p);
      maxTarget += Math.max(bb.width, bb.height) + 1; minTarget = Math.max(minTarget, Math.min(bb.width, bb.height));
      return { index: index, p: p, t: transpose(p), width: bb.width, height: bb.height };
    });
    for (order = 0; order < 2; order += 1) {
      pieces.sort(function (a, bb) {
        return (order ? Math.max(bb.width, bb.height) - Math.max(a.width, a.height) : bb.width * bb.height - a.width * a.height) || a.index - bb.index;
      });
      for (orientationMode = 0; orientationMode < 3; orientationMode += 1) for (rowGap = 1; rowGap <= 3; rowGap += 1) for (target = minTarget; target <= maxTarget; target += 1) {
        x = 0; y = 0; shelfH = 0; placed = [];
        for (i = 0; i < pieces.length; i += 1) {
          item = pieces[i]; variants = [ { p: item.p, w: item.width, h: item.height }, { p: item.t, w: item.height, h: item.width } ];
          options = variants.filter(function (v) { return x + v.w <= target; });
          if (!options.length) { x = 0; y += shelfH + rowGap; shelfH = 0; options = variants.filter(function (v) { return v.w <= target; }); }
          if (!options.length) { placed = null; break; }
          options.sort(function (a, bb) {
            if (orientationMode === 1) return a.w - bb.w || a.h - bb.h;
            if (orientationMode === 2) {
              var preferred = i % 2 === 0, aPreferred = preferred ? a.w >= a.h : a.w <= a.h, bPreferred = preferred ? bb.w >= bb.h : bb.w <= bb.h;
              if (aPreferred !== bPreferred) return aPreferred ? -1 : 1;
            }
            return Math.max(shelfH, a.h) - Math.max(shelfH, bb.h) || a.h - bb.h || bb.w - a.w;
          });
          option = options[0];
          option.p.forEach(function (p) { placed.push({ nameId: p.nameId, word: p.word, row: p.row + y, col: p.col + x, direction: p.direction }); });
          x += option.w + 1; shelfH = Math.max(shelfH, option.h);
        }
        if (!placed) continue;
        b = boundsOf(placed); category = categoryOf(b.width, b.height); cost = shapeCost(b.width, b.height, 1);
        if (!fallback || cost < fallback.cost) fallback = { placements: placed, cost: cost };
        for (ti = 0; ti < TARGETS.length; ti += 1) {
          if (category !== CATEGORIES[ti]) continue;
          cost = shapeCost(b.width, b.height, TARGETS[ti]);
          if (!best[ti] || cost < best[ti].cost) best[ti] = { placements: placed, cost: cost };
        }
      }
    }
    if (!best[0]) best[0] = fallback;
    if (!best[0]) throw new Error('Không thể đóng gói bố cục an toàn.');
    return best.filter(function (entry) { return !!entry; }).map(function (entry) { return normalized(entry.placements); });
  }

  function buildLayout(placements, id, names) {
    placements = normalized(placements);
    var state = makeState(placements, []), b = state.bounds, count = componentsOf(state).length, aspect = Math.max(b.width / b.height, b.height / b.width), category = categoryOf(b.width, b.height), target = category === 'portrait' ? 2/3 : category === 'landscape' ? 3/2 : 1, warnings = [], longWord = 0;
    names.forEach(function (n) { longWord = Math.max(longWord, n.word.length); });
    if (count > 1) warnings.push('Có ' + count + ' cụm tên không giao nhau; đã giữ đủ tên và chừa ít nhất một ô trống giữa các cụm.');
    if (aspect > 2 + 1e-9) warnings.push('Bố cục khá dài (tỷ lệ ' + aspect.toFixed(2) + ':1). Thử phương án khác; tool không cắt tên để ép cân đối.');
    if (names.length === 1 && longWord > 1) warnings.push('Một tên đơn phải nằm trên một hàng/cột; không thể tạo bố cục vuông mà vẫn giữ cách ghép Scrabble đúng.');
    return {
      id: id, placements: placements, cells: state.list.map(function (cell) { return { row: cell.row, col: cell.col, letter: cell.letter }; }).sort(function (a, bb) { return a.row - bb.row || a.col - bb.col; }),
      width: b.width, height: b.height, aspect: aspect, ratio: b.width / b.height, category: category, components: count, crossings: state.crossings,
      score: 100000000 - (count - 1) * 1000000 + state.crossings * 7 - shapeCost(b.width, b.height, target), warnings: warnings
    };
  }

  function generate(input, opts) {
    opts = opts || {};
    var names = canonicalNames(input), count = names.length, variants = Math.max(1, Math.min(LIMITS.variants, Math.floor(Number(opts.maxVariants == null ? opts.variants : opts.maxVariants) || 36))), beamSize = Math.max(3, Math.min(24, Math.floor(Number(opts.beamSize) || (variants <= 8 ? 9 : 18)))),
      budget = { checks: 0, limit: Math.max(500, Math.min(160000, Math.floor(Number(opts.maxChecks) || 96000))), capped: false }, order = [], degree = [], states = [], next, i, j, a, bb, seeds, depth, state, props, rem, ni, packed, seen, key2, layouts = [], warnings = [], groups = { square:[], portrait:[], landscape:[] }, discoveredCounts = { square:0, portrait:0, landscape:0 }, counts = { square:0, portrait:0, landscape:0 }, positions = [0,0,0], added, variantsCapped;
    for (i = 0; i < count; i += 1) {
      degree[i] = 0; order.push(i);
      for (j = 0; j < count; j += 1) { if (i !== j && names[j].word.split('').some(function (c) { return names[i].word.indexOf(c) >= 0; })) degree[i] += 1; }
    }
    order.sort(function (aa, b2) { return degree[b2] - degree[aa] || names[b2].word.length - names[aa].word.length || aa - b2; });
    seeds = Math.min(count, Math.max(2, Math.ceil(beamSize / 2)));
    for (i = 0; i < seeds; i += 1) {
      ni = order[i]; rem = order.filter(function (n) { return n !== ni; });
      for (j = 0; j < 2; j += 1) states.push(makeState([{ nameId: names[ni].id, word: names[ni].word, row: 0, col: 0, direction: j ? 'V' : 'H' }], rem));
    }
    for (depth = 1; depth < count; depth += 1) {
      next = []; seen = Object.create(null);
      for (i = 0; i < states.length; i += 1) {
        state = states[i]; props = proposals(state, names, budget, 18);
        if (!props.length) {
          ni = state.remaining[0];
          props = [{ ni: ni, placement: { nameId: names[ni].id, word: names[ni].word, row: 0, col: state.bounds.maxC + 2, direction: 'H' } }, { ni: ni, placement: { nameId: names[ni].id, word: names[ni].word, row: 0, col: state.bounds.maxC + 2, direction: 'V' } }];
        }
        for (j = 0; j < props.length; j += 1) {
          a = props[j]; rem = state.remaining.filter(function (n) { return n !== a.ni; });
          bb = makeState(state.placements.concat([a.placement]), rem); key2 = stateSignature(bb);
          if (!seen[key2]) { seen[key2] = true; next.push(bb); }
        }
      }
      states = diverseBeam(next, beamSize);
    }
    seen = Object.create(null);
    function addLayout(placements) {
      var layout = buildLayout(placements, '', names), signature = layout.cells.map(function (cell) { return cell.row + ',' + cell.col + ':' + cell.letter; }).join('|');
      if (seen[signature]) return; seen[signature] = true;
      var checked = validate(layout, names); if (!checked.ok) throw new Error('Bố cục không vượt kiểm tra an toàn: ' + checked.errors.join('; '));
      layout.signature = signature; groups[layout.category].push(layout); discoveredCounts[layout.category] += 1;
    }
    for (i = 0; i < states.length; i += 1) {
      packed = packVariants(states[i].placements);
      for (j = 0; j < packed.length; j += 1) { addLayout(packed[j]); addLayout(transpose(packed[j])); }
    }
    CATEGORIES.forEach(function (category) { groups[category].sort(function (aa, b2) { return b2.score - aa.score || compareText(aa.signature, b2.signature); }); });
    added = true;
    while (layouts.length < variants && added) {
      added = false;
      for (i = 0; i < CATEGORIES.length && layouts.length < variants; i += 1) {
        if (positions[i] < groups[CATEGORIES[i]].length) { layouts.push(groups[CATEGORIES[i]][positions[i]++]); counts[CATEGORIES[i]] += 1; added = true; }
      }
    }
    variantsCapped = discoveredCounts.square + discoveredCounts.portrait + discoveredCounts.landscape > layouts.length;
    layouts.forEach(function (layout, index) { layout.id = 'layout-' + (index + 1); });
    if (budget.capped) warnings.push('Đã đạt giới hạn tìm kiếm ngoại tuyến; giữ đủ tên nhưng bố cục có thể chưa tối ưu nhất.');
    if (variantsCapped) warnings.push('Đã chọn ' + layouts.length + ' phương án khác nhau theo ba nhóm; còn phương án tìm được vượt giới hạn hiển thị.');
    warnings.push('Đây là các phương án tìm được trong giới hạn tính toán, không phải liệt kê mọi hoán vị toán học.');
    return { layouts: layouts, warnings: warnings, completeEnumeration: false, counts: counts, stats: { names: count, searchChecks: budget.checks, searchBudget: budget.limit, capped: budget.capped, budgetExhausted: budget.capped, variants: layouts.length, requestedVariants: variants, variantsCapped: variantsCapped, completeEnumeration: false, discoveredLayouts: discoveredCounts.square + discoveredCounts.portrait + discoveredCounts.landscape, categoryCounts: counts, discoveredCategoryCounts: discoveredCounts } };
  }

  function validate(layout, input) {
    var errors = [], names, wanted = Object.create(null), used = Object.create(null), grid = Object.create(null), rows = [], placements = layout && layout.placements, i, j, p, r, c, k, cell, neighbor, dims, uniqueCells = Object.create(null), owners, x;
    try { names = canonicalNames(input); } catch (err) { return { ok: false, errors: [err.message] }; }
    names.forEach(function (n) { wanted['$' + n.id] = n.word; });
    if (!Array.isArray(placements) || !placements.length) return { ok: false, errors: ['Thiếu danh sách vị trí tên.'] };
    for (i = 0; i < placements.length; i += 1) {
      p = placements[i];
      if (!p || typeof p.row !== 'number' || typeof p.col !== 'number' || !isFinite(p.row) || !isFinite(p.col) || Math.floor(p.row) !== p.row || Math.floor(p.col) !== p.col || (p.direction !== 'H' && p.direction !== 'V') || !/^[A-Z]+$/.test(p.word || '')) { errors.push('Vị trí/chữ/hướng không hợp lệ ở tên thứ ' + (i + 1)); continue; }
      if (!wanted['$' + p.nameId] || wanted['$' + p.nameId] !== p.word || used['$' + p.nameId]) errors.push('Tên bị thiếu, lặp mã hoặc sai nội dung: ' + p.nameId);
      used['$' + p.nameId] = true;
      for (j = 0; j < p.word.length; j += 1) {
        r = p.row + (p.direction === 'V' ? j : 0); c = p.col + (p.direction === 'H' ? j : 0); k = key(r, c); cell = grid[k];
        if (!cell) { cell = grid[k] = { row: r, col: c, letter: p.word.charAt(j), owners: [] }; rows.push(cell); }
        if (cell.letter !== p.word.charAt(j)) errors.push('Hai chữ khác nhau chồng ô ' + k);
        for (x = 0; x < cell.owners.length; x += 1) if (placements[cell.owners[x]].direction === p.direction) errors.push('Hai tên cùng hướng chồng ô ' + k);
        cell.owners.push(i); if (cell.owners.length > 2) errors.push('Hơn hai tên dùng chung ô ' + k);
      }
    }
    names.forEach(function (n) { if (!used['$' + n.id]) errors.push('Thiếu tên: ' + n.label); });
    function shareWord(cell2, neighbor2, direction) {
      var ci, nj;
      for (ci = 0; ci < cell2.owners.length; ci += 1) for (nj = 0; nj < neighbor2.owners.length; nj += 1) if (cell2.owners[ci] === neighbor2.owners[nj] && placements[cell2.owners[ci]].direction === direction) return true;
      return false;
    }
    for (i = 0; i < rows.length; i += 1) {
      cell = rows[i]; neighbor = grid[key(cell.row, cell.col + 1)];
      if (neighbor && !shareWord(cell, neighbor, 'H')) errors.push('Hai tên chạm cạnh ngang trái quy tắc ở ' + key(cell.row, cell.col));
      neighbor = grid[key(cell.row + 1, cell.col)];
      if (neighbor && !shareWord(cell, neighbor, 'V')) errors.push('Hai tên chạm cạnh dọc trái quy tắc ở ' + key(cell.row, cell.col));
    }
    if (rows.length > LIMITS.cells) errors.push('Bố cục vượt giới hạn ' + LIMITS.cells + ' ô.');
    if (!errors.length) {
      dims = boundsOf(placements);
      if (dims.minR !== 0 || dims.minC !== 0 || layout.width !== dims.width || layout.height !== dims.height) errors.push('Kích thước/điểm gốc bố cục không khớp vị trí tên.');
    }
    if (!Array.isArray(layout.cells)) errors.push('Thiếu danh sách ô.');
    else {
      for (i = 0; i < layout.cells.length; i += 1) {
        cell = layout.cells[i]; k = key(cell.row, cell.col);
        if (uniqueCells[k] || !grid[k] || grid[k].letter !== cell.letter) errors.push('Danh sách ô thừa, lặp hoặc sai chữ ở ' + k);
        uniqueCells[k] = true;
      }
      if (layout.cells.length !== rows.length) errors.push('Danh sách ô thiếu hoặc thừa.');
    }
    return { ok: !errors.length, errors: errors };
  }

  function fit(layout, opts) {
    opts = opts || {};
    var safe = opts.bounds, shape = opts.shape || 'rect', gap = opts.gapRatio == null ? 0.03 : Number(opts.gapRatio), inset = opts.inset == null ? 0 : Number(opts.inset), width, height, margin, cx, cy, rx, ry, ew, eh, tile, i, cell, dx, dy, sx, sy, denominator, left, top;
    if (!Array.isArray(safe) || safe.length !== 4 || safe.some(function (n) { return typeof n !== 'number' || !isFinite(n); }) || safe[2] <= safe[0] || safe[3] <= safe[1]) throw new Error('Safezone phải có bounds [left, top, right, bottom] hợp lệ.');
    if ((shape !== 'rect' && shape !== 'ellipse') || !isFinite(gap) || gap < 0 || gap > 0.15 || !isFinite(inset) || inset < 0 || inset > 0.2) throw new Error('Kiểu safezone, khoảng cách hoặc inset không hợp lệ.');
    if (!layout || !Array.isArray(layout.cells) || !layout.cells.length || layout.cells.length > LIMITS.cells || typeof layout.width !== 'number' || typeof layout.height !== 'number' || !isFinite(layout.width) || !isFinite(layout.height) || Math.floor(layout.width) !== layout.width || Math.floor(layout.height) !== layout.height || layout.width < 1 || layout.height < 1) throw new Error('Thiếu bố cục cần fit.');
    for (i = 0; i < layout.cells.length; i += 1) {
      cell = layout.cells[i];
      if (!cell || typeof cell.row !== 'number' || typeof cell.col !== 'number' || !isFinite(cell.row) || !isFinite(cell.col) || Math.floor(cell.row) !== cell.row || Math.floor(cell.col) !== cell.col || cell.row < 0 || cell.col < 0 || cell.row >= layout.height || cell.col >= layout.width) throw new Error('Ô chữ nằm ngoài kích thước bố cục.');
    }
    width = safe[2] - safe[0]; height = safe[3] - safe[1]; margin = Math.min(width, height) * inset;
    cx = (safe[0] + safe[2]) / 2; cy = (safe[1] + safe[3]) / 2; rx = width / 2 - margin; ry = height / 2 - margin;
    ew = layout.width + (layout.width - 1) * gap; eh = layout.height + (layout.height - 1) * gap;
    tile = Math.min(rx * 2 / ew, ry * 2 / eh);
    if (shape === 'ellipse') {
      for (i = 0; i < layout.cells.length; i += 1) {
        cell = layout.cells[i];
        for (sx = 0; sx <= 1; sx += 1) for (sy = 0; sy <= 1; sy += 1) {
          dx = cell.col * (1 + gap) + sx - ew / 2; dy = cell.row * (1 + gap) + sy - eh / 2;
          denominator = Math.sqrt(dx * dx / (rx * rx) + dy * dy / (ry * ry));
          if (denominator) tile = Math.min(tile, 1 / denominator);
        }
      }
    }
    // Keep boundary arithmetic conservative across JSON/ExtendScript runtimes.
    // The shrink is invisible (<0.00001 px for a 30k canvas), but a zero-tolerance
    // native ellipse check must not reject a mathematically-on-boundary corner.
    tile *= 1 - 1e-10;
    left = cx - ew * tile / 2; top = cy - eh * tile / 2;
    return { tileSize: tile, pitch: tile * (1 + gap), left: left, top: top, bounds: [left, top, left + ew * tile, top + eh * tile], safeBounds: safe.slice(), insetBounds: [safe[0] + margin, safe[1] + margin, safe[2] - margin, safe[3] - margin], shape: shape, gapRatio: gap, inset: inset };
  }

  return { parseNames: parseNames, generate: generate, validate: validate, fit: fit, letterScore: letterScore, scoreValues: scoreValues, limits: { names: LIMITS.names, letters: LIMITS.letters, cells: LIMITS.cells, variants: LIMITS.variants } };
}));
