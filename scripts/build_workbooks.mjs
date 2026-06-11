import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(scriptDir, "..");
const dataDir = path.join(rootDir, "data");
const exportDir = path.join(rootDir, "exports");

const solutions = JSON.parse(await fs.readFile(path.join(dataDir, "solutions-en.json"), "utf8"));
const toolsByCategory = JSON.parse(await fs.readFile(path.join(dataDir, "industry-tools-en.json"), "utf8"));
const manifest = JSON.parse(await fs.readFile(path.join(dataDir, "manifest.json"), "utf8"));

const headerFormat = {
  fill: "#E5E7EB",
  font: { bold: true, color: "#111827" },
  wrapText: true,
  verticalAlignment: "center",
};

const aboutRows = (title, sourceUrl, countText) => [
  ["Dataset", title],
  ["Purpose", "English source dataset for the independent manufacturing AI solutions website."],
  ["Original Chinese source", sourceUrl],
  ["Translation", "Machine-translated draft prepared for overseas manufacturing audiences; names, URLs, email addresses, phone numbers, and IDs are retained from the source."],
  ["Records", countText],
  ["Generated", manifest.generated_at],
];

function colName(index) {
  let result = "";
  let n = index + 1;
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

function addAboutSheet(workbook, title, sourceUrl, countText) {
  const sheet = workbook.worksheets.add("About");
  const rows = aboutRows(title, sourceUrl, countText);
  sheet.getRange(`A1:B${rows.length}`).values = rows;
  sheet.getRange("A1:A6").format = { ...headerFormat, columnWidth: 180 };
  sheet.getRange("B1:B6").format = { wrapText: true, columnWidth: 520 };
  return sheet;
}

function addDataSheet(workbook, name, rows, fields) {
  const sheet = workbook.worksheets.add(name);
  const values = [fields, ...rows.map(row => fields.map(field => row[field] ?? ""))];
  const endCol = colName(fields.length - 1);
  sheet.getRange(`A1:${endCol}${values.length}`).values = values;
  sheet.getRange(`A1:${endCol}1`).format = headerFormat;
  sheet.getRange(`A1:${endCol}${values.length}`).format.wrapText = true;
  sheet.freezePanes.freezeRows(1);

  fields.forEach((field, index) => {
    let width = 120;
    if (/summary|benefit|tech|pain|cases|fit|overview|pricing/i.test(field)) width = 280;
    if (/url|email|contact information/i.test(field)) width = 220;
    if (/id|no\./i.test(field)) width = 70;
    sheet.getRange(`${colName(index)}:${colName(index)}`).format.columnWidth = width;
  });
  return sheet;
}

async function exportWorkbook(workbook, fileName) {
  await fs.mkdir(exportDir, { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(exportDir, fileName));
}

const solutionWorkbook = Workbook.create();
addAboutSheet(
  solutionWorkbook,
  "Manufacturing AI Solutions Handbook - English Source Data",
  manifest.solution_source,
  `${solutions.length} solutions`,
);
addDataSheet(solutionWorkbook, "Solutions", solutions, Object.keys(solutions[0]));
await exportWorkbook(solutionWorkbook, "Manufacturing_AI_Solutions_Handbook_EN.xlsx");

const toolWorkbook = Workbook.create();
addAboutSheet(
  toolWorkbook,
  "Industry AI Tools Library - English Source Data",
  manifest.tool_source,
  `${manifest.tool_count} tools across ${Object.keys(toolsByCategory).length} categories`,
);
for (const [category, rows] of Object.entries(toolsByCategory)) {
  const fields = Object.keys(rows[0]);
  addDataSheet(toolWorkbook, category.slice(0, 31), rows, fields);
}
await exportWorkbook(toolWorkbook, "Industry_AI_Tools_Library_EN.xlsx");

console.log(JSON.stringify({
  solutions: path.join(exportDir, "Manufacturing_AI_Solutions_Handbook_EN.xlsx"),
  tools: path.join(exportDir, "Industry_AI_Tools_Library_EN.xlsx"),
}, null, 2));
