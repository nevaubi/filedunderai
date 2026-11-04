import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
import ExcelJS from "exceljs";
import * as docx from "docx";
import JSZip from "jszip";
const sources = [
  "public/lib/evidence.js",
  "public/lib/workspace.js",
  "public/pages/workspace.js",
].map((p) => fs.readFileSync(p, "utf8"));
async function setup({ actualMarkdown = false } = {}) {
  const dom = new JSDOM('<main id="page"></main>', {
    url: "https://research.example/#workbench",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window;
  w.Blob = Blob;
  w.ExcelJS = ExcelJS;
  w.docx = docx;
  w.captured = [];
  w.URL.createObjectURL = (blob) => {
    w.captured.push(blob);
    return "blob:export";
  };
  w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = () => {};
  w.page = w.document.getElementById("page");
  w.ensureCompany = async () => ({});
  w.IDX = { TEST: { price: 10 } };
  w.model = () => ({ periods: () => ["2024-12-31", "2025-12-31"] });
  w.plabel = (t, e) => "FY" + e.slice(0, 4);
  w.toast = () => {};
  w.findCell = (tk, metric, period) => {
    const value = {
      Revenue: period === "FY2024" ? 100e6 : 120e6,
      "Operating income": 24e6,
      "Operating cash flow": 30e6,
      Capex: 6e6,
      "Net income": 20e6,
      "Free cash flow": 24e6,
    }[metric];
    return value == null
      ? null
      : {
          tk,
          label: metric,
          v: value,
          unit: "USD",
          kind: "A",
          end: "2025-12-31",
          rec: { end: "2025-12-31", filed: "2026-02-10", accn: "test-filing" },
        };
  };
  w.chatPanel = (host, cfg) => {
    w.chatConfig = cfg;
    return {
      ask: () => {
        throw Error("No AI calls during tests");
      },
    };
  };
  for (const src of sources) w.eval(src);
  w.evidenceForCell = (cell) =>
    w.FU_EVIDENCE.fromCell(cell, {
      sourceUrl: "https://www.sec.gov/Archives/test-filing",
      period: "FY2025",
    });
  if (actualMarkdown) {
    w.esc = (s) =>
      String(s ?? "").replace(
        /[&<>"']/g,
        (c) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          })[c],
      );
    const app = fs.readFileSync("public/app.js", "utf8");
    w.eval(
      app.slice(
        app.indexOf("function resolveCitation("),
        app.indexOf(
          'document.addEventListener("click", async e => { const b = e.target.closest(".cite',
        ),
      ),
    );
  }
  await w.FU_PAGES.workbench();
  return { dom, w, close: () => dom.window.close() };
}
const click = (w, label) => {
  const n = [...w.document.querySelectorAll("button")].find(
    (n) => n.textContent === label,
  );
  assert.ok(n, "Missing button " + label);
  n.click();
  return n;
};
test("real workspace UI edits selection, stages changes, preserves undo and exports editable XLSX with provenance", async () => {
  const { w, close } = await setup();
  try {
    await w.FU_WORKSPACE.openModel("TEST");
    const formula = w.document.querySelector(".ws-formula");
    w.document
      .querySelector('[data-cell="D5"]')
      .dispatchEvent(new w.MouseEvent("mousedown", { bubbles: true }));
    assert.equal(formula.value, "=C5*(1+D6)");
    w.FU_WORKSPACE.stageProposal({
      summary: "Update forecast driver",
      cells: [{ sheet: "Assumptions", address: "B4", value: 0.1 }],
    });
    assert.equal(w.FU_WORKSPACE.getWorkbook().sheets[1].cells.B4.value, 0);
    click(w, "Apply changes");
    assert.equal(w.FU_WORKSPACE.getWorkbook().sheets[1].cells.B4.value, 0.1);
    assert.equal(
      w.document.querySelector('[data-cell="D5"]').textContent,
      "132",
    );
    click(w, "↶ Undo");
    assert.equal(w.FU_WORKSPACE.getWorkbook().sheets[1].cells.B4.value, 0);
    click(w, "↷ Redo");
    assert.equal(w.FU_WORKSPACE.getWorkbook().sheets[1].cells.B4.value, 0.1);
    const addr = w.document.querySelector(".ws-address");
    addr.value = "B4:C5";
    addr.dispatchEvent(
      new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    assert.equal(w.FU_WORKSPACE.getSelection().range, "B4:C5");
    await w.FU_WORKSPACE.exportXlsx();
    const blob = w.captured.at(-1);
    assert.ok(blob.size > 5000);
    const x = new ExcelJS.Workbook();
    await x.xlsx.load(await blob.arrayBuffer());
    assert.equal(x.getWorksheet("Model").getCell("D5").formula, "C5*(1+D6)");
    assert.equal(x.getWorksheet("Assumptions").getCell("B4").value, 0.1);
    assert.ok(x.getWorksheet("Cell evidence").rowCount > 10);
    assert.ok(x.getWorksheet("Change history").rowCount > 1);
    assert.ok(x.getWorksheet("Model").getCell("C5").note.includes("Evidence:"));
    assert.equal(
      x.getWorksheet("Model").getCell("C5").formula,
      "120000000/1000000",
    );
  } finally {
    close();
  }
});
test("document restore sanitizes active HTML and Word roundtrip retains formatting, tables and citations", async () => {
  const { w, close } = await setup();
  try {
    const fact = w.evidenceForCell(w.findCell("TEST", "Revenue", "LFY"));
    await w.FU_WORKSPACE.openReport("TEST", {
      title: "Test report",
      evidence: [fact],
      markdown: "Test draft",
    });
    const editor = w.document.querySelector(".ws-document");
    editor.innerHTML =
      '<h1>Evidence report</h1><p><strong>Revenue</strong> and <em>margin</em> <a href="https://www.sec.gov/Archives/test-filing" data-evidence-id="' +
      fact.id +
      '">Filed source</a></p><table><tbody><tr><td>Revenue</td><td>120 million</td></tr></tbody></table><ul><li>Risk</li></ul>';
    editor.dispatchEvent(new w.Event("input", { bubbles: true }));
    w.FU_WORKSPACE.getWorkbook();
    const blocks = w.chatConfig.tools(() => ({ done() {}, fail() {} }));
    const context = await blocks
      .find((t) => t.name === "read_workspace")
      .execute({});
    const id = context.document[1].id;
    w.FU_WORKSPACE.stageProposal({
      summary: "Refine one paragraph",
      blocks: [{ id, text: "Reviewed paragraph", type: "p" }],
    });
    click(w, "Reject");
    assert.ok(
      w.FU_WORKSPACE.getWorkbook().document.html.includes("Filed source"),
    );
    w.FU_WORKSPACE.stageProposal({
      summary: "Refine one paragraph",
      blocks: [{ id, text: "Reviewed paragraph", type: "p" }],
    });
    click(w, "Apply changes");
    assert.ok(
      w.FU_WORKSPACE.getWorkbook().document.html.includes("Reviewed paragraph"),
    );
    assert.ok(w.FU_WORKSPACE.getWorkbook().document.html.includes("<table"));
    click(w, "↶ Undo");
    assert.ok(
      w.FU_WORKSPACE.getWorkbook().document.html.includes("Filed source"),
    );
    const safe = w.FU_WORKSPACE.sanitizeHTML(
      '<script>bad()</script><p onclick="bad()"><a href="javascript:bad()">Keep text</a><iframe src="x"></iframe><span style="font-size:16pt;position:fixed">styled</span></p>',
    );
    assert.ok(!safe.includes("script"));
    assert.ok(!safe.includes("onclick"));
    assert.ok(!safe.includes("iframe"));
    assert.ok(!safe.includes("position"));
    assert.ok(safe.includes("font-size:16pt"));
    await w.FU_WORKSPACE.exportDocx();
    const zip = await JSZip.loadAsync(await w.captured.at(-1).arrayBuffer());
    const xml = await zip.file("word/document.xml").async("string");
    const rels = await zip.file("word/_rels/document.xml.rels").async("string");
    assert.ok(xml.includes("Evidence report"));
    assert.ok(xml.includes("<w:b"));
    assert.ok(xml.includes("<w:i"));
    assert.ok(xml.includes("<w:tbl"));
    assert.ok(xml.includes("Sources"));
    assert.ok(rels.includes("https://www.sec.gov/Archives/test-filing"));
  } finally {
    close();
  }
});
test("AI read tool returns valid bounded data with pagination and never applies staged patches automatically", async () => {
  const { w, close } = await setup();
  try {
    const tools = w.chatConfig.tools(() => ({ done() {}, fail() {} }));
    const stage = tools.find((t) => t.name === "propose_workspace_changes"),
      read = tools.find((t) => t.name === "read_workspace");
    await stage.execute({
      summary: "Proposed only",
      cells: Array.from({ length: 80 }, (_, i) => ({
        address: "A" + (i + 1),
        value: "value " + i,
      })),
    });
    assert.equal(w.FU_WORKSPACE.getWorkbook().sheets[0].cells.A1, undefined);
    click(w, "Apply changes");
    const first = await read.execute({});
    assert.equal(first.sheets[0].cells.length, 60);
    assert.equal(first.sheets[0].truncated, true);
    assert.equal(first.sheets[0].nextOffset, 60);
    const next = await read.execute({ cellOffset: 60 });
    assert.equal(next.sheets[0].cells.length, 20);
    assert.equal(next.sheets[0].truncated, false);
    assert.doesNotThrow(() => JSON.parse(w.chatConfig.context()));
  } finally {
    close();
  }
});
test("actual app markdown preserves derived citation IDs and full nested input sources in editor, staged blocks and DOCX", async () => {
  const { w, close } = await setup({ actualMarkdown: true });
  try {
    const common = {
      ticker: "TEST",
      unit: "USD",
      period: "FY2025",
      periodStart: "2025-01-01",
      periodEnd: "2025-12-31",
      filedDate: "2026-02-10",
      periodType: "annual",
    };
    const cash = w.FU_EVIDENCE.capture({
        ...common,
        metric: "Operating cash flow",
        value: 30e6,
        sourceUrl: "https://www.sec.gov/cash-input",
      }),
      capex = w.FU_EVIDENCE.capture({
        ...common,
        metric: "Capex",
        value: 6e6,
        sourceUrl: "https://www.sec.gov/capex-input",
      }),
      revenue = w.FU_EVIDENCE.capture({
        ...common,
        metric: "Revenue",
        value: 120e6,
        sourceUrl: "https://www.sec.gov/revenue-input",
      });
    const fcf = w.FU_EVIDENCE.capture({
        ...common,
        metric: "Free cash flow",
        value: 24e6,
        inputEvidenceIds: [cash.id, capex.id],
        derivation: "Operating cash flow − Capex",
      }),
      ratio = w.FU_EVIDENCE.capture({
        ...common,
        metric: "FCF margin",
        unit: "pct",
        value: 0.2,
        inputEvidenceIds: [fcf.id, revenue.id],
        derivation: "Free cash flow / Revenue",
      });
    await w.FU_WORKSPACE.openReport("TEST", {
      title: "Derived report",
      markdown: "# Derived report\n\nFCF margin: 20% [[E:" + ratio.id + "]]",
      evidence: [ratio],
    });
    const editor = w.document.querySelector(".ws-document");
    assert.ok(
      editor.querySelector('a[data-evidence-id="' + ratio.id + '"]'),
      "derived claim retains its own evidence ID on source links",
    );
    for (const r of [cash, capex, revenue])
      assert.ok(
        [...editor.querySelectorAll("a")].some((a) => a.href === r.sourceUrl),
      );
    assert.ok(!editor.textContent.includes("Unresolved"));
    w.FU_WORKSPACE.stageProposal({
      summary: "Append derived claim",
      blocks: [{ text: "Free cash flow was $24 million [[E:" + fcf.id + "]]" }],
    });
    click(w, "Apply changes");
    assert.ok(
      w.document.querySelector(
        '.ws-document a[data-evidence-id="' + fcf.id + '"]',
      ),
    );
    assert.ok(
      !w.document
        .querySelector(".ws-document")
        .textContent.includes("Unresolved"),
    );
    await w.FU_WORKSPACE.exportDocx();
    const zip = await JSZip.loadAsync(await w.captured.at(-1).arrayBuffer()),
      xml = await zip.file("word/document.xml").async("string"),
      rels = await zip.file("word/_rels/document.xml.rels").async("string");
    for (const r of [ratio, fcf, cash, capex, revenue])
      assert.ok(xml.includes(r.id), "appendix includes " + r.metric);
    assert.ok(xml.includes("Operating cash flow − Capex"));
    assert.ok(xml.includes("Free cash flow / Revenue"));
    for (const r of [cash, capex, revenue])
      assert.ok(rels.includes(r.sourceUrl));
    click(w, "File");
    assert.equal(
      w.document.querySelector(".ws-ribbon").getAttribute("aria-label"),
      "File commands",
    );
  } finally {
    close();
  }
});
