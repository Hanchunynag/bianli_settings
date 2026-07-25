export const el = (id) => document.getElementById(id);

export function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
  }[ch]));
}
