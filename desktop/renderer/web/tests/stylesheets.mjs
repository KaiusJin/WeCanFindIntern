import { readdir, readFile } from "node:fs/promises";

// Every stylesheet under web/ui, concatenated, so style assertions do not depend on which feature file holds a rule.
const root = new URL("../ui/", import.meta.url);
const files = (await readdir(root, { recursive: true })).filter((file) => file.endsWith(".css")).sort();
export const stylesSource = (await Promise.all(files.map((file) => readFile(new URL(file, root), "utf8")))).join("\n");
