export const $ = id => document.getElementById(id);

export const status = t => { $('status').textContent = t; };

export function el(tag, props, children) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  (children || []).forEach(c => e.appendChild(c));
  return e;
}
