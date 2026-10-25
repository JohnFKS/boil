/* 本地文档转纯文本：docx（zip+xml）与 rtf（控制字剥离）。
 *
 * 只做零依赖能诚实做好的部分：docx 本质是一个 zip 里的一段 XML，rtf 是可剥的控制字，
 * 两者都不需要第三方库。PDF 不在这里假装支持 —— 没有文字层的扫描版需要视觉模型，
 * 有文字层的 PDF 需要一个真正的解析器，浏览器里手搓的结果不可信，不如直接告诉用户怎么办。
 */

const MAX_DOC_BYTES = 12 * 1024 * 1024;

function decodeUtf8(bytes) {
  return new TextDecoder("utf-8").decode(bytes);
}

function decodeEntities(text) {
  return text
    .replace(/&#x([0-9a-f]+);/gi, (_m, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_m, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'").replace(/&amp;/g, "&");
}

async function inflateRaw(bytes) {
  if (typeof DecompressionStream !== "function") {
    throw new Error("这个浏览器不支持解压，docx 请另存为 .txt 再导入");
  }
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** 从 zip 里取出指定条目的字节。只读本地文件头，够用且不必解析整张中央目录。 */
async function zipEntry(fileBytes, wanted) {
  const view = new DataView(fileBytes.buffer, fileBytes.byteOffset, fileBytes.byteLength);
  let offset = 0;
  while (offset + 30 <= fileBytes.length) {
    if (view.getUint32(offset, true) !== 0x04034b50) break;   // 不是本地文件头
    const method = view.getUint16(offset + 8, true);
    const flags = view.getUint16(offset + 6, true);
    let size = view.getUint32(offset + 18, true);
    const nameLen = view.getUint16(offset + 26, true);
    const extraLen = view.getUint16(offset + 28, true);
    const name = decodeUtf8(fileBytes.subarray(offset + 30, offset + 30 + nameLen));
    const dataAt = offset + 30 + nameLen + extraLen;
    if (flags & 0x08) {
      // 流式打包：大小写在数据后面的 data descriptor 里
      const found = scanForDescriptor(view, fileBytes, dataAt);
      if (found === null) break;
      size = found.size;
    }
    if (dataAt + size > fileBytes.length) break;
    const raw = fileBytes.subarray(dataAt, dataAt + size);
    if (name === wanted) return method === 0 ? raw : await inflateRaw(raw);
    offset = dataAt + size;
    if (flags & 0x08) offset += (flags & 0x2000 ? 24 : 16);   // 跳过 data descriptor
  }
  return null;
}

function scanForDescriptor(view, fileBytes, from) {
  const limit = Math.min(fileBytes.length - 8, from + 2_000_000);
  for (let i = from; i < limit; i += 1) {
    if (view.getUint32(i, true) === 0x08074b50) {
      return { size: view.getUint32(i + 6, true), at: i };
    }
  }
  return null;
}

function xmlToText(xml) {
  // 段落结束与换行要变成真空行，否则整篇文档会挤成一行
  let text = xml
    .replace(/<w:tab[^>]*\/>/g, "\t")
    .replace(/<\/w:p>\s*$/g, "")
    .replace(/<\/w:p>/g, "\n\n")
    .replace(/<w:br[^>]*\/>/g, "\n")
    .replace(/<\/w:tc>/g, " | ")
    .replace(/<[^>]*>/g, "");
  return decodeEntities(text).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

export async function docxToText(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length > MAX_DOC_BYTES) throw new Error(`${file.name} 超过 12MB`);
  if (new TextDecoder().decode(bytes.subarray(0, 2)) !== "PK") {
    throw new Error(`${file.name} 不是有效的 .docx（旧 .doc 请先在 Word/WPS 里另存为 .docx）`);
  }
  const main = await zipEntry(bytes, "word/document.xml");
  if (!main) throw new Error("这个 .docx 里找不到正文（word/document.xml）");
  return xmlToText(decodeUtf8(main));
}

/** 这些组的原始内容不是正文（字体表、颜色表、图片二进制…），整组丢掉。 */
const RTF_IGNORE_GROUPS = new Set([
  "fonttbl", "filetbl", "colortbl", "stylesheet", "info", "generator", "header", "footer",
  "pict", "nonshppict", "shppict", "themedata", "colorschememapping", "bkmkstart", "bkmkend",
]);

/** rtf：剥控制字与控制组，保留正文。不管排版 —— 我们要的是能被编译的文字。 */
export function rtfToText(source) {
  let out = "";
  const stack = [];                      // 每组一个布尔：这组要不要整段丢掉
  const skip = () => stack.some(Boolean);
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (ch === "{") { stack.push(false); i += 1; continue; }
    if (ch === "}") { stack.pop(); i += 1; continue; }
    if (ch === "\\") {
      const next = source[i + 1];
      if (next === "\n") { i += 2; continue; }
      if (next === "{" || next === "}" || next === "\\") {
        if (!skip()) out += next;
        i += 2;
        continue;
      }
      if (next === "'") {                 // \'hh 十六进制字节
        const hex = source.substr(i + 2, 2);
        if (!skip() && /^[0-9a-fA-F]{2}$/.test(hex)) out += String.fromCharCode(parseInt(hex, 16));
        i += 4;
        continue;
      }
      if (next === "*") {                 // {\* …} 可忽略的目的组
        if (stack.length) stack[stack.length - 1] = true;
        i += 2;
        continue;
      }
      const word = /^([a-z]+)(-?\d+)? ?/i.exec(source.slice(i + 1));
      if (word) {
        const name = word[1].toLowerCase();
        if (RTF_IGNORE_GROUPS.has(name)) {
          if (stack.length) stack[stack.length - 1] = true;
        } else if (!skip()) {
          if (name === "par" || name === "line" || name === "sect") out += "\n";
          else if (name === "tab") out += "\t";
          else if (name === "u") {        // \uN：可能是带符号的 16 位码点
            const code = parseInt(word[2] || "0", 10);
            if (code) out += String.fromCodePoint(code < 0 ? code + 65536 : code);
          }
        }
        i += 1 + word[0].length;
        continue;
      }
      i += 2;                             // \ 转义符本身
      continue;
    }
    if (!skip() && ch !== "\r" && ch !== "\n") out += ch;
    i += 1;
  }
  return out.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

/** 能直接当文本读的后缀；.docx/.rtf 走各自的解析器。 */
export const TEXT_SUFFIXES = [".txt", ".md", ".markdown", ".csv", ".tsv", ".html", ".htm", ".json"];

// 解析器暴露给测试工具：docx/rtf 的正确性只能在真浏览器里验（解压与实体解码都是浏览器能力）
if (typeof window !== "undefined") {
  window.__boilDocImport = { docxToText, rtfToText, fileToText };
}

/** 把 File 转成可编译的纯文本；返回 {text, via}，失败抛可读的中文原因。 */
export async function fileToText(file) {
  const name = (file.name || "").toLowerCase();
  if (name.endsWith(".docx")) return { text: await docxToText(file), via: "docx" };
  if (name.endsWith(".rtf")) {
    const raw = decodeUtf8(new Uint8Array(await file.arrayBuffer()));
    return { text: rtfToText(raw), via: "rtf" };
  }
  if (name.endsWith(".pdf")) {
    throw new Error("PDF 需要文字层解析或视觉模型：请在浏览器里选中复制正文粘贴进来，或另存为 .txt/.docx");
  }
  if (name.endsWith(".doc")) {
    throw new Error("旧版 .doc 没有可靠的纯前端解析：请在 Word/WPS 里另存为 .docx 再导入");
  }
  if (/\.(png|jpe?g|gif|webp|heic)$/i.test(name)) {
    throw new Error("图片要走视觉模型才能变文字，当前入口只收文本类文件");
  }
  return { text: (await file.text()).replace(/\r\n?/g, "\n"), via: "plain" };
}
