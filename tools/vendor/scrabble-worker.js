/* No host, filesystem, account or network access: layout search only. */
importScripts("scrabble-layout.js");
self.onmessage = function (event) {
  var value = event.data || {};
  try {
    if (!value || value.op !== "generate" || typeof value.id !== "number" || !isFinite(value.id) || Math.floor(value.id)!==value.id || value.id<0) throw new Error("Yêu cầu bố cục không hợp lệ.");
    var engine = self.UOYScrabbleLayout;
    if (!engine || typeof engine.generate !== "function") throw new Error("Thiếu bộ sắp xếp Scrabble.");
    self.postMessage({ id:value.id, ok:true, result:engine.generate(value.names, value.options || {}) });
  } catch (error) { self.postMessage({id:value.id,ok:false,error:String(error && error.message || error)}); }
};
