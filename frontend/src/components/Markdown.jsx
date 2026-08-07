/**
 * Minimal markdown renderer for agent answers.
 *
 * The answer text is model-generated, so it is HTML-escaped first and only the
 * small set of constructs the Explanation Agent is told to emit is re-enabled.
 */

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

export function renderMarkdown(source) {
  const lines = escapeHtml(source || "").split("\n");
  const out = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      out.push("</ul>");
      listOpen = false;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (!line.trim()) {
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 4);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    if (bullet) {
      if (!listOpen) {
        out.push("<ul>");
        listOpen = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }

    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();

  return out.join("");
}

export default function Markdown({ children }) {
  return <div dangerouslySetInnerHTML={{ __html: renderMarkdown(children) }} />;
}
