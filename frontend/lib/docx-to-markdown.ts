// Converts a .docx file to Markdown entirely in the browser, so Word
// "Heading 1/2/3" styles become real "#"/"##"/"###" ATX headings. That keeps
// uploaded .docx files compatible with the same structure-aware chunker used
// for the existing .md knowledge base documents.
//
// mammoth's typings only expose `convertToHtml` (its untyped
// `convertToMarkdown` exists at runtime but isn't declared), so we convert
// to HTML first and walk the DOM into Markdown ourselves.
import mammoth from "mammoth";

const HEADING_LEVEL: Record<string, number> = {
  h1: 1,
  h2: 2,
  h3: 3,
  h4: 4,
  h5: 5,
  h6: 6,
};

function inlineToMarkdown(node: ChildNode): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? "";
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";

  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  const inner = Array.from(el.childNodes).map(inlineToMarkdown).join("");

  switch (tag) {
    case "strong":
    case "b":
      return inner.trim() ? `**${inner.trim()}**` : "";
    case "em":
    case "i":
      return inner.trim() ? `*${inner.trim()}*` : "";
    case "br":
      return "\n";
    default:
      return inner;
  }
}

function blockToMarkdown(el: HTMLElement): string {
  const tag = el.tagName.toLowerCase();
  const level = HEADING_LEVEL[tag];

  if (level) {
    const text = Array.from(el.childNodes).map(inlineToMarkdown).join("").trim();
    return text ? `\n${"#".repeat(level)} ${text}\n\n` : "";
  }

  if (tag === "ul" || tag === "ol") {
    const items = Array.from(el.children)
      .filter((child) => child.tagName.toLowerCase() === "li")
      .map((li) => {
        const text = Array.from(li.childNodes).map(inlineToMarkdown).join("").trim();
        return text ? `- ${text}` : "";
      })
      .filter(Boolean);
    return items.length ? `\n${items.join("\n")}\n\n` : "";
  }

  if (tag === "p" || tag === "div") {
    const text = Array.from(el.childNodes).map(inlineToMarkdown).join("").trim();
    return text ? `${text}\n\n` : "";
  }

  // Unknown block element: recurse into children as plain blocks.
  return Array.from(el.children)
    .map((child) => blockToMarkdown(child as HTMLElement))
    .join("");
}

function htmlToMarkdown(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const markdown = Array.from(doc.body.children)
    .map((el) => blockToMarkdown(el as HTMLElement))
    .join("");
  // Collapse 3+ blank lines down to a single blank line between blocks.
  return markdown.replace(/\n{3,}/g, "\n\n").trim();
}

export async function convertDocxToMarkdown(file: File): Promise<string> {
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.convertToHtml({ arrayBuffer });
  return htmlToMarkdown(result.value);
}
