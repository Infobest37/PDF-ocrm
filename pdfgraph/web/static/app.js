/* Ф2.1: логика чата — отправка вопроса, три вида сообщений, открытие листа.
   Никаких обращений к CDN: PDF.js лежит в /static/pdfjs/. */
"use strict";

const feed = document.getElementById("feed");
const form = document.getElementById("ask-form");
const input = document.getElementById("ask-input");
const viewer = document.getElementById("viewer");
const viewerHint = document.getElementById("viewer-hint");

const LAYER_RU = {
  "base": "базовый",
  "supplement": "дополнение",
  "change": "изменение",
  "unknown": "слой не определён",
};

const SCOPE_RU = {
  "all": "все борта типа",
  "serials": "",
  "equipment": "опция оборудования",
};

function applicabilityRu(value) {
  if (!value) return "—";
  if (value.startsWith("serials:")) {
    return "борта " + value.slice("serials:".length).trim();
  }
  return SCOPE_RU[value] || value;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function citationNode(citation) {
  const box = el("div", "citation");
  const docLine = el("div", "doc-line");
  docLine.append(el("span", "sheet-ref",
    `${citation.document} лист ${citation.page}`));
  if (citation.system_code) {
    docLine.append(el("span", "code", ` · код ${citation.system_code}`));
  }
  docLine.append(" ");
  docLine.append(el("span", "chip " + (citation.ok ? "ok" : "fail"),
    citation.ok ? "подтверждено" : "не подтверждено"));
  box.append(docLine);

  const metaParts = [`слой: ${LAYER_RU[citation.doc_layer] || citation.doc_layer || "—"}`,
    `применимость: ${applicabilityRu(citation.doc_applicability)}`];
  if (citation.doc_revision) metaParts.push(`ревизия: ${citation.doc_revision}`);
  box.append(el("div", "doc-meta", metaParts.join(" · ")));

  const link = el("a", null, "открыть лист →");
  /* Ф2.2: полный просмотр в новой вкладке (масштаб/прокрутка/поиск/
     поворот); клик по номеру листа ведёт туда же. */
  link.href = citation.full_url || citation.viewer_url || citation.link;
  link.target = "_blank";
  link.addEventListener("click", (event) => {
    event.preventDefault();
    renderThumb(citation);
    window.open(link.href, "_blank");
  });
  const sheetRef = box.querySelector(".sheet-ref");
  if (sheetRef) {
    sheetRef.style.cursor = "pointer";
    sheetRef.title = "Открыть лист на весь экран";
    sheetRef.addEventListener("click", () => {
      renderThumb(citation);
      window.open(link.href, "_blank");
    });
  }
  box.append(link);
  return box;
}

/* Ф2.2: миниатюра листа в правой панели (270px) — рендер через
   локальный pdf.js; смещение нумерации то же, что и у вьюера. */
async function renderThumb(citation) {
  viewerHint.hidden = true;
  viewer.hidden = false;
  try {
    const task = pdfjsLib.getDocument(citation.link);
    const pdf = await task.promise;
    const page = await pdf.getPage(
      Math.min(citation.page + 1, pdf.numPages));
    const viewport = page.getViewport({ scale: 0.4 });
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({
      canvasContext: canvas.getContext("2d"), viewport,
    }).promise;
    viewer.replaceChildren(canvas);
    const full = el("button", null, "Открыть на весь экран");
    full.addEventListener("click", () =>
      window.open(citation.full_url || citation.viewer_url, "_blank"));
    viewer.append(full);
  } catch (error) {
    viewer.replaceChildren(el("p", null,
      "Просмотр недоступен: файл PDF не найден"));
  }
}

function neighborsNode(neighbors) {
  if (!neighbors || !neighbors.length) return null;
  const names = neighbors.map((n) => `${n.document} лист ${n.page} — ${n.label}`);
  return el("p", "neighbors", "Соседние листы (может быть продолжение): "
    + names.join("; "));
}

// Ф1.9: запрошенные карты, которых нет в базе — отдельная строка, не цитата.
function techcardMissingNode(items) {
  if (!items || !items.length) return null;
  return el("p", "neighbors", "В базе не найдены: "
    + items.map((key) => `карта ${key}`).join("; "));
}

function renderAnswer(message, payload) {
  for (const claim of payload.claims) {
    message.append(el("p", "answer-text", claim.text));
  }
  for (const citation of payload.citations) {
    message.append(citationNode(citation));
  }
  const warning = payload.applicability_warning;
  if (warning) message.append(el("p", "applicability-warning", warning));
  const neighbors = neighborsNode(payload.neighbors);
  if (neighbors) message.append(neighbors);
  const missing = techcardMissingNode(payload.techcard_missing);
  if (missing) message.append(missing);
}

function renderRefusal(message, payload) {
  const plate = el("p", "reason status-plate fail",
    "Отказ: " + (payload.reason || "нет подтверждений в документах"));
  message.append(plate);
  // Ф1.9: ненайденные карты показываются и при отказе.
  const missing = techcardMissingNode(payload.techcard_missing);
  if (missing) message.append(missing);
}

function renderClarify(message, payload) {
  const box = el("div", "clarify");
  box.append(el("p", null, "Требуется уточнение"));
  const options = el("div", "options");
  for (const option of payload.options || []) {
    const button = el("button", "option");
    button.append(el("span", null, `${option.id} — ${option.label}`));
    const doc = el("span", "option-doc",
      `${option.document || "—"} · раздел ${option.page ?? "—"}`);
    button.append(doc);
    button.addEventListener("click", () => ask(option.label));
    options.append(button);
  }
  box.append(options);
  message.append(box);
}

function renderQuestion(text) {
  const message = el("div", "message");
  message.append(el("p", "question", text));
  feed.append(message);
  return feed.lastElementChild;
}

const SESSION_KEY = "pdfgraph-last-conversation";

function saveConversation(question, payload) {
  try {
    sessionStorage.setItem(SESSION_KEY,
      JSON.stringify({ question, payload }));
  } catch (error) { /* приватный режим — просто не восстанавливаем */ }
}

async function ask(question) {
  const text = (question ?? input.value).trim();
  if (!text) return;
  if (!question) input.value = "";
  const message = renderQuestion(text);
  const pending = el("p", "answer-text", "Ищу в документах…");
  message.append(pending);
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text }),
    });
    const payload = await response.json();
    pending.remove();
    if (payload.status === "answer") renderAnswer(message, payload);
    else if (payload.status === "refusal") renderRefusal(message, payload);
    else renderClarify(message, payload);
    saveConversation(text, payload);
  } catch (error) {
    pending.remove();
    message.append(el("p", "reason status-plate fail",
      "Ошибка среды: сервер недоступен"));
  }
  feed.scrollTop = feed.scrollHeight;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask();
});

/* Ф2.2: «Назад к ответу» возвращает в чат с тем же вопросом —
   последняя переписка хранится в sessionStorage и восстанавливается
   при загрузке страницы (в т.ч. после history.back() из вьюера). */
(function restoreConversation() {
  try {
    const saved = sessionStorage.getItem(SESSION_KEY);
    if (!saved) return;
    const { question, payload } = JSON.parse(saved);
    const message = renderQuestion(question);
    if (payload.status === "answer") renderAnswer(message, payload);
    else if (payload.status === "refusal")
      renderRefusal(message, payload);
    else renderClarify(message, payload);
  } catch (error) { /* повреждённая запись — начинаем с пустой ленты */ }
})();
