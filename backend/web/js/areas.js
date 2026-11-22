// Responsibility-area picker (Phase 3.1). The user's own explicit choice: areas (each with its plain-language definition
// and examples), optional yes/no scope questions, or "None of these areas". Nothing is pre-selected or inferred; an
// unanswered question stays unanswered ("Not sure"), which Trace never reads as "no".
import { h, uid } from "./dom.js";

export const WHY_CATEGORISE = "Categorising tells Trace which of your experience counts towards a target role's requirements. "
  + "Uncategorised experience is never treated as missing; it just can't be counted yet. Choose \"None of these areas\" for "
  + "lines that don't fit any area.";

/** areas: the taxonomy from GET /taxonomy/responsibility-areas. initial: {areas, none, qualifiers} or null. */
export function areaPicker(areas, initial = null, { legend = "Which areas of responsibility does this show?" } = {}) {
  const chosen = new Set(initial && !initial.none ? initial.areas || [] : []);
  const answers = JSON.parse(JSON.stringify((initial && initial.qualifiers) || {}));
  const noneId = uid("none");
  const none = h("input", { type: "checkbox", id: noneId, checked: Boolean(initial && initial.none) });
  const rows = areas.map((a) => {
    const id = uid("area");
    const box = h("input", { type: "checkbox", id, value: a.code, checked: chosen.has(a.code) });
    const questions = h("div.area-questions");
    const drawQuestions = () => {
      questions.replaceChildren();
      if (!box.checked) return;
      for (const [q, text] of Object.entries(a.questions || {})) {
        const name = uid("q");
        const current = answers[a.code] ? answers[a.code][q] : undefined;
        const opt = (label, value) => {
          const rid = uid("qa");
          const r = h("input", { type: "radio", name, id: rid, checked: current === value });
          r.addEventListener("change", () => {
            if (value === undefined) {
              if (answers[a.code]) delete answers[a.code][q];
            } else {
              answers[a.code] = { ...(answers[a.code] || {}), [q]: value };
            }
          });
          return h("span.check", r, h("label", { for: rid }, label));
        };
        questions.append(h("fieldset.area-question", h("legend", text), opt("Yes", true), opt("No", false), opt("Not sure", undefined)));
      }
    };
    box.addEventListener("change", () => {
      if (box.checked) {
        chosen.add(a.code);
        none.checked = false;
      } else {
        chosen.delete(a.code);
        delete answers[a.code];
      }
      drawQuestions();
    });
    drawQuestions();
    return h("div.area-option",
      h("span.check", box, h("label", { for: id }, h("strong", a.label))),
      a.description ? h("p.hint", a.description) : null,
      a.examples && a.examples.length
        ? h("details.area-examples", h("summary", "Examples"), h("ul", a.examples.map((x) => h("li", x)))) : null,
      questions);
  });
  none.addEventListener("change", () => {
    if (!none.checked) return;
    for (const r of rows) {
      const box = r.querySelector("input[type=checkbox]");
      if (box.checked) {
        box.checked = false;
        box.dispatchEvent(new Event("change"));
      }
    }
  });
  const el = h("fieldset.area-picker", h("legend", legend), rows,
    h("div.area-option.area-none", h("span.check", none, h("label", { for: noneId }, h("strong", "None of these areas")))));
  return {
    el,
    /** null when nothing is chosen (the line stays uncategorised). */
    value() {
      if (none.checked) return { areas: [], none: true, qualifiers: {} };
      if (!chosen.size) return null;
      const qualifiers = {};
      for (const code of chosen) if (answers[code] && Object.keys(answers[code]).length) qualifiers[code] = answers[code];
      return { areas: [...chosen].sort(), none: false, qualifiers };
    },
  };
}
