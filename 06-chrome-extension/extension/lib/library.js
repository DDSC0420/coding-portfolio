// Searching, ordering and exporting saved highlights. Pure functions over the store's pages.

function tokens(query) {
  return String(query ?? '')
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

/**
 * Pages whose highlights match the query, newest page first, highlights in page order.
 * Every word of the query must appear in the highlight's text, the page title or its URL.
 * Pages with no matching highlight are left out.
 */
export function filterPages(pages, query) {
  const words = tokens(query);
  const out = [];
  for (const page of pages) {
    const pageText = `${page.title ?? ''} ${page.url ?? ''}`.toLocaleLowerCase();
    const highlights = page.highlights
      .filter((h) => {
        const text = `${h.exact.toLocaleLowerCase()} ${pageText}`;
        return words.every((w) => text.includes(w));
      })
      .sort((a, b) => a.start - b.start || (a.createdAt < b.createdAt ? -1 : 1));
    if (highlights.length) out.push({ ...page, highlights });
  }
  out.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0));
  return out;
}

export function countHighlights(pages) {
  return pages.reduce((n, p) => n + p.highlights.length, 0);
}

// Backslash-escape the characters that would turn a quote into Markdown formatting or HTML.
function escapeText(text) {
  return text
    .replace(/[\\`*_[\]<>|~&]/g, '\\$&')
    .replace(/^(#{1,6}\s|[-+]\s)/, '\\$1')
    .replace(/^(\d+)([.)]\s)/, '$1\\$2');
}

function linkTarget(url) {
  return /[\s()<>]/.test(url) ? `<${url.replace(/[<>]/g, encodeURIComponent)}>` : url;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** The pages as a Markdown document: one heading per page, one blockquote per highlight. */
export function toMarkdown(pages, { exportedAt = new Date(), query = '' } = {}) {
  const date = exportedAt.toISOString().slice(0, 10);
  const lines = ['# Margin highlights', ''];
  const scope = query.trim() ? ` matching "${query.trim()}"` : '';
  lines.push(`Exported ${date}${scope} · ${plural(pages.length, 'page')} · ${plural(countHighlights(pages), 'highlight')}`, '');
  for (const page of pages) {
    const title = (page.title || page.url).replace(/[\\[\]]/g, '\\$&');
    lines.push(`## [${title}](${linkTarget(page.url)})`, '');
    for (const h of page.highlights) lines.push(`> ${escapeText(h.exact)}`, '');
  }
  return lines.join('\n');
}
