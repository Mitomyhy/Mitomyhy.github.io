/** Inspect bytes: Photoshop files often arrive with an image/* or empty MIME. */
export async function classifyDepthInput(file) {
  if (!(file instanceof Blob)) throw Error('Chọn artwork hoặc template PSD.');
  const head=new Uint8Array(await file.slice(0,26).arrayBuffer());
  const signature=head.length>=6&&head[0]===56&&head[1]===66&&head[2]===80&&head[3]===83;
  if(signature) {
    if(head[4]!==0||head[5]!==1)throw Error('File PSB chưa được hỗ trợ. Lưu template thành PSD RGB 8 bit.');
    if(head.length<26||file.size>256*1024*1024)throw Error('PSD không hợp lệ hoặc vượt 256 MB.');
    return 'psd';
  }
  if(/\.ps[db]$/i.test(file.name||'')||/photoshop/i.test(file.type))throw Error('File không có chữ ký PSD hợp lệ; không đọc template như ảnh phẳng.');
  return 'artwork';
}
