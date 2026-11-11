// Which saved page a URL belongs to, and the permission pattern for its site. Pure functions.

// Query parameters that track where a click came from but never change the page.
const TRACKING = /^(utm_[a-z_]+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|mc_cid|mc_eid|_hsenc|_hsmi|igshid|yclid|ref_src)$/i;

/**
 * The key a page's highlights are stored under: origin + path + the query that matters.
 * Drops the #fragment (unless it is a #/ or #! app route), tracking parameters, and sorts the
 * rest so ?a=1&b=2 and ?b=2&a=1 are one page. Returns null for anything but http(s).
 */
export function urlKey(href) {
  let url;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  const params = [...url.searchParams].filter(([name]) => !TRACKING.test(name));
  params.sort(([a, av], [b, bv]) => (a < b ? -1 : a > b ? 1 : av < bv ? -1 : av > bv ? 1 : 0));
  const query = new URLSearchParams(params).toString();
  const route = url.hash.startsWith('#/') || url.hash.startsWith('#!') ? url.hash : '';
  return `${url.origin}${url.pathname}${query ? `?${query}` : ''}${route}`;
}

/** The match pattern that covers a page's site, as used for the opt-in permission. */
export function originPattern(href) {
  let url;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  // Match patterns can't carry a port; this one covers the host on any port.
  return `${url.protocol}//${url.hostname}/*`;
}

/** The host a match pattern covers, for showing to people: "https://example.com/*" → "example.com". */
export function patternHost(pattern) {
  const m = /^(?:\*|https?):\/\/([^/]+)\//.exec(pattern);
  return m ? m[1] : pattern;
}
