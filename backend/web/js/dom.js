// Safe DOM construction. Every piece of text is set with textContent or created as a Text node: API strings
// (resume text, titles, messages) are never parsed as HTML. There is no innerHTML anywhere in this application.

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * h("div.card.is-active", {attrs}, children...)
 * attrs: class, id, text, on<Event> handlers, data-* / aria-* / other attributes, style (object: CSSOM only).
 * children: Node | string | number | null | false | arrays thereof. Strings become Text nodes.
 */
export function h(tag, attrs, ...children) {
  if (attrs === null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.classList.add(...classes);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined && v !== false) el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") {
      for (const c of String(value).split(/\s+/).filter(Boolean)) el.classList.add(c);
    } else if (key === "text") {
      el.textContent = String(value);
    } else if (key === "style" && typeof value === "object") {
      for (const [prop, v] of Object.entries(value)) el.style.setProperty(prop, String(v));
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "html" || key === "innerHTML") {
      throw new Error("raw HTML is not allowed");
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...children) {
  clear(el);
  return append(el, children);
}

let idCounter = 0;
export function uid(prefix = "t") {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}
