// Valida todas as SKILL.md do repositório.
// Uso: node tools/validate-skills.mjs
import { readdir, readFile, stat } from "node:fs/promises";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKILLS_DIR = join(ROOT, "skills");
const ID_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

async function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...(await walk(full)));
    else if (e.name === "SKILL.md") out.push(full);
  }
  return out;
}

function parseFrontmatter(text) {
  if (!text.startsWith("---")) return null;
  const end = text.indexOf("\n---", 3);
  if (end === -1) return null;
  return { block: text.slice(3, end), body: text.slice(end + 4) };
}

const errors = [];
const warnings = [];
const rows = [];

for (const file of await walk(SKILLS_DIR)) {
  const rel = file.replace(ROOT + "\\", "").replace(ROOT + "/", "");
  const dir = basename(dirname(file));
  const id = dir;
  const text = await readFile(file, "utf8");
  const lines = text.split(/\r?\n/).length;
  const size = (await stat(file)).size;

  if (!ID_RE.test(id)) errors.push(`${rel}: ID invalido "${id}" (use kebab-case minusculo)`);

  const fm = parseFrontmatter(text);
  if (!fm) {
    errors.push(`${rel}: sem frontmatter YAML`);
  } else {
    if (!/^name:\s*\S/m.test(fm.block)) errors.push(`${rel}: frontmatter sem "name"`);
    const desc = fm.block.match(/^description:\s*([\s\S]+?)(?=\n[a-zA-Z_-]+:\s|$)/m);
    if (!desc) errors.push(`${rel}: frontmatter sem "description"`);
    else {
      const d = desc[1].trim();
      if (d.length < 40) warnings.push(`${rel}: description curta (${d.length} chars)`);
      if (d.length > 800) warnings.push(`${rel}: description longa (${d.length} chars)`);
    }
  }

  if (!/^#\s+\S/m.test(text)) errors.push(`${rel}: sem titulo H1`);
  if (lines < 200) warnings.push(`${rel}: apenas ${lines} linhas (min 200 recomendado)`);
  if (!/##\s+References/i.test(text)) warnings.push(`${rel}: sem secao References`);

  // Blocos de codigo devem fechar.
  const fences = (text.match(/^```/gm) || []).length;
  if (fences % 2 !== 0) errors.push(`${rel}: bloco de codigo nao fechado (${fences} marcadores)`);

  // Detecta flags/keys suspeitos e caracteres nao-ingleses acidentais.
  const cjk = text.match(/[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/g);
  if (cjk) warnings.push(`${rel}: ${cjk.length} caractere(s) CJK acidental(is)`);

  rows.push({ id, rel, lines, size, category: rel.split(/[\\/]/)[1] });
}

rows.sort((a, b) => a.category.localeCompare(b.category) || a.id.localeCompare(b.id));

const byCat = new Map();
for (const r of rows) byCat.set(r.category, (byCat.get(r.category) ?? 0) + 1);

console.log("\n=== skills por categoria ===");
for (const [cat, n] of [...byCat].sort()) console.log(`  ${cat.padEnd(20)} ${n}`);

console.log(`\n=== total: ${rows.length} skills ===`);
const totalLines = rows.reduce((a, r) => a + r.lines, 0);
console.log(`linhas totais: ${totalLines}`);

console.log("\n=== menores (para revisar) ===");
for (const r of [...rows].sort((a, b) => a.lines - b.lines).slice(0, 10)) {
  console.log(`  ${String(r.lines).padStart(5)}  ${r.rel}`);
}

if (warnings.length) {
  console.log(`\n=== avisos (${warnings.length}) ===`);
  for (const w of warnings) console.log(`  ! ${w}`);
}
if (errors.length) {
  console.log(`\n=== ERROS (${errors.length}) ===`);
  for (const e of errors) console.log(`  x ${e}`);
  process.exitCode = 1;
} else {
  console.log("\nSem erros de validacao.");
}