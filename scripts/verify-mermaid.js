#!/usr/bin/env node
/**
 * 校验 markdown 里所有 ```mermaid 代码块的语法。
 *
 * 用法:
 *   node scripts/verify-mermaid.js README.md [更多文件.md ...]
 *
 * 依赖（装到临时目录即可，勿污染项目）:
 *   mkdir -p "$TMPDIR/mermaid-verify" && cd "$TMPDIR/mermaid-verify"
 *   npm i mermaid@10 jsdom dompurify
 *   NODE_PATH="$TMPDIR/mermaid-verify/node_modules" node scripts/verify-mermaid.js README.md
 *
 * 为什么需要：mermaid 语法错误在多数渲染器里表现为「整块图不显示」，
 * 肉眼根本看不出错在哪；本脚本直接调 mermaid.parse()，失败会指出块号与原因。
 *
 * 已知陷阱（手写时别踩）：
 *   - CJS 下 `require('mermaid')` 返回 {__esModule, default}，必须取 `.default`。
 *   - 需注入 global.DOMPurify（用 jsdom window 构造）与 CSSStyleSheet polyfill。
 *   - jsdom 无布局引擎（text.getBBox 不存在），只做语法校验，不出图。
 *     要出图用 mermaid-cli：
 *     npx -y @mermaid-js/mermaid-cli@10 -p pptr-config.json -i x.mmd -o x.png -b white --scale 2
 *     （pptr-config.json 里把 executablePath 指到系统 Edge/Chrome，别让 npx 下 Chromium）
 */
const fs = require('fs');

function loadEnv() {
  let mermaid, JSDOM, createDOMPurify;
  try {
    mermaid = require('mermaid');
    JSDOM = require('jsdom').JSDOM;
    const dompurifyMod = require('dompurify');
    // dompurify 3.x 在 CJS 下也是 {default: factory}，同样必须取 .default
    createDOMPurify = dompurifyMod.default || dompurifyMod;
  } catch (e) {
    console.error('[verify-mermaid] 缺少依赖：' + e.message);
    console.error('  按文件头注释装到临时目录，再用 NODE_PATH 指过去。');
    process.exit(2);
  }

  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    pretendToBeVisual: true,
  });
  global.window = dom.window;
  global.document = dom.window.document;
  global.CSSStyleSheet = dom.window.CSSStyleSheet || function () {};

  // ── DOMPurify 的两种用法都要兼容 ──────────────────────────────
  // CJS 下 `require('dompurify')` 返回的是**工厂函数**，但 mermaid 内部按**实例**用法
  // 调用（`DOMPurify.sanitize(...)`），于是报 `DOMPurify.sanitize is not a function`。
  // 症状很有迷惑性：sequenceDiagram 能过（不渲染 HTML），flowchart/gantt 全挂。
  // 解法：把实例方法挂到那个工厂对象本身上——mermaid 内部 require 拿到的是同一个对象。
  const purifyFactory = createDOMPurify;
  const purify = purifyFactory(dom.window);
  for (const m of ['sanitize', 'addHook', 'removeHook', 'setConfig', 'clearConfig', 'isValidAttribute']) {
    if (typeof purify[m] === 'function') purifyFactory[m] = purify[m].bind(purify);
  }
  global.DOMPurify = purify;

  return mermaid.default || mermaid; // 关键：CJS 下必须取 .default
}

/** 抽出所有 ```mermaid 块，返回 [{ startLine, code }] */
function extractBlocks(text) {
  const lines = text.split(/\r?\n/);
  const blocks = [];
  let cur = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (cur === null) {
      if (/^\s*```mermaid\s*$/.test(line)) {
        cur = { startLine: i + 2, code: [] }; // 图的第一行
      }
    } else if (/^\s*```\s*$/.test(line)) {
      blocks.push({ startLine: cur.startLine, code: cur.code.join('\n') });
      cur = null;
    } else {
      cur.code.push(line);
    }
  }
  if (cur !== null) blocks.push({ startLine: cur.startLine, code: cur.code.join('\n'), unterminated: true });
  return blocks;
}

async function main() {
  const files = process.argv.slice(2);
  if (!files.length) {
    console.error('用法: node scripts/verify-mermaid.js <文件.md> [...]');
    process.exit(1);
  }
  const mermaid = loadEnv();
  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

  let total = 0;
  let failed = 0;

  for (const file of files) {
    if (!fs.existsSync(file)) {
      console.log(`✗ ${file} — 文件不存在`);
      failed++;
      continue;
    }
    const blocks = extractBlocks(fs.readFileSync(file, 'utf8'));
    console.log(`\n${file}：发现 ${blocks.length} 个 mermaid 块`);
    for (let i = 0; i < blocks.length; i++) {
      const b = blocks[i];
      total++;
      if (b.unterminated) {
        console.log(`  ✗ 块#${i + 1}（第 ${b.startLine} 行起）— 围栏未闭合`);
        failed++;
        continue;
      }
      try {
        await mermaid.parse(b.code);
        console.log(`  ✓ 块#${i + 1}（第 ${b.startLine} 行起）`);
      } catch (e) {
        console.log(`  ✗ 块#${i + 1}（第 ${b.startLine} 行起）— ${String(e.message || e).split('\n')[0]}`);
        failed++;
      }
    }
  }

  console.log(`\n合计 ${total} 块，失败 ${failed} 块`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error('[verify-mermaid] 运行异常:', e);
  process.exit(3);
});
