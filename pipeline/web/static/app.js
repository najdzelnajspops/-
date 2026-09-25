// Веб-UI (pipeline/web/) — без сборки/фреймворков, обычный vanilla JS.
// Осознанно без внешних CDN-зависимостей (карты/чартов) — живая презентация
// не должна зависеть от интернета на площадке.

const state = {
  demoId: null,
  territoryCategory: null,
  geometry: null,
  proposals: [], // {id, species_name_ru, life_form, x, y}
  lastReview: null, // Map<planting_id, hasIssue>
  selectedSpecies: { tree: new Set(), shrub: new Set() }, // чек-боксы видов на вкладке автогенерации (2026-09-24)
};

const NS = "http://www.w3.org/2000/svg";

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// Названия категорий территорий — длинные формулировки из ДПиООС (например,
// "магистрали" — почти 150 символов), закрытый select их обрезает (CSS
// text-overflow) — title даёт полный текст по наведению.
function updateSelectTitle(selectEl) {
  const opt = selectEl.options[selectEl.selectedIndex];
  selectEl.title = opt ? opt.text : "";
}

// ---------- инициализация ----------

async function init() {
  const [demos, categories] = await Promise.all([
    fetchJSON("/api/demo-objects"),
    fetchJSON("/api/territory-categories"),
  ]);

  const demoSelect = document.getElementById("demo-select");
  demoSelect.innerHTML = demos.map((d) => `<option value="${d.id}">${d.label_ru}</option>`).join("");
  state.demoId = demos[0].id;
  state.territoryCategory = demos[0].default_territory_category;

  const categorySelect = document.getElementById("category-select");
  categorySelect.innerHTML = categories.map((c) => `<option value="${c.id}">${c.label_ru}</option>`).join("");
  categorySelect.value = state.territoryCategory;
  // Некоторые названия категорий (например, "магистрали") — длинные
  // канцелярские формулировки из ДПиООС, урезаются в закрытом виде select
  // (см. CSS text-overflow) — title показывает полный текст при наведении.
  updateSelectTitle(demoSelect);
  updateSelectTitle(categorySelect);

  demoSelect.addEventListener("change", async () => {
    state.demoId = demoSelect.value;
    updateSelectTitle(demoSelect);
    resetProposals();
    await loadGeometry();
  });
  categorySelect.addEventListener("change", async () => {
    state.territoryCategory = categorySelect.value;
    updateSelectTitle(categorySelect);
    resetProposals();
    await loadGeometry();
  });

  setupTabs();
  setupDragAndDrop();
  setupPrintAndHideRecommendations();
  setupDataSourcesPanel();
  setupPriceListPanel();
  setupUploadPanel();
  setupSpeciesPicker();
  setupLayoutDebugMode();
  document.getElementById("generate-btn").addEventListener("click", runGenerate);
  document.getElementById("generate-btn-2").addEventListener("click", runGenerate);
  document.getElementById("review-btn").addEventListener("click", runReview);

  await loadGeometry();
}

// Режим отладки вёрстки — прямой запрос пользователя (2026-09-24, по образцу
// другого проекта): вместо того чтобы угадывать точные px на глаз, пользователь
// сам перетаскивает/растягивает окно чертежа и присылает финальные координаты,
// которые потом фиксируются в style.css как обычные значения. Полностью
// неактивен без ?debug=layout в адресной строке — на обычную работу сервиса
// не влияет никак.
function setupLayoutDebugMode() {
  if (new URLSearchParams(location.search).get("debug") !== "layout") return;

  const layoutEl = document.querySelector(".layout");

  // Реальный найденный баг первой версии: ручки сидели прямо на самом
  // элементе (правый нижний угол окна чертежа) — при ширине окна 1640px это
  // часто оказывалось ЗА пределами экрана (пользователь физически не мог
  // дотянуться до ручки, выглядело как «ручку убрали»). Теперь все ручки —
  // всегда видимые «чипы» в плавающей панели в углу экрана, а не на самой
  // подложке; куда бы ни уехал элемент — панель управления всегда на месте.
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const wrap = panel.querySelector(".plan-wrap");
    const plan = panel.querySelector('[id^="plan-svg-container"]');
    const sidePanel = panel.querySelector(".side-panel");
    if (wrap) { wrap.style.position = "relative"; wrap.classList.add("layout-debug-target"); }
    if (plan) plan.classList.add("layout-debug-resize-target");
    if (sidePanel) { sidePanel.style.position = "relative"; sidePanel.classList.add("layout-debug-target-side"); }
  });

  function activeElements() {
    const panel = document.querySelector(".tab-panel.active");
    if (!panel) return {};
    return {
      wrap: panel.querySelector(".plan-wrap"),
      plan: panel.querySelector('[id^="plan-svg-container"]'),
      sidePanel: panel.querySelector(".side-panel"),
    };
  }

  const readout = document.createElement("div");
  readout.id = "layout-debug-readout";
  readout.innerHTML =
    '<div class="layout-debug-readout-title">Режим отладки вёрстки</div>' +
    '<pre id="layout-debug-values"></pre>' +
    '<div class="layout-debug-controls">' +
    '<div class="layout-debug-ctrl" data-action="move-plan">✥ окно чертежа — положение</div>' +
    '<div class="layout-debug-ctrl" data-action="resize-plan">⤡ окно чертежа — размер</div>' +
    '<div class="layout-debug-ctrl" data-action="move-side">✥ боковая панель — положение</div>' +
    '<div class="layout-debug-ctrl" data-action="resize-side">⤡ боковая панель — размер</div>' +
    "</div>" +
    '<button type="button" id="layout-debug-copy">Скопировать</button>';
  document.body.appendChild(readout);
  const valuesEl = readout.querySelector("#layout-debug-values");
  readout.querySelector("#layout-debug-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(valuesEl.textContent).catch(() => {});
  });

  function updateReadout() {
    const { wrap, plan, sidePanel } = activeElements();
    if (!wrap || !plan) { valuesEl.textContent = ""; return; }
    const layoutRect = layoutEl.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    const planRect = plan.getBoundingClientRect();
    // Сама ширина окна чертежа теперь резиновая (width:100%) — фиксируется
    // только ПРОПОРЦИЯ (aspect-ratio), поэтому здесь считается именно
    // соотношение сторон, а не абсолютные px, которые не переносятся в CSS.
    const ratio = planRect.width / planRect.height;
    let text =
      `окно чертежа (карточка) относительно .layout: left=${Math.round(wrapRect.left - layoutRect.left)}px top=${Math.round(wrapRect.top - layoutRect.top)}px\n` +
      `окно чертежа — размер сейчас: width=${Math.round(planRect.width)}px height=${Math.round(planRect.height)}px\n` +
      `окно чертежа — ПРОПОРЦИЯ (это и есть значение для aspect-ratio): ${ratio.toFixed(3)}:1`;
    if (sidePanel) {
      const sideRect = sidePanel.getBoundingClientRect();
      text +=
        `\n\nбоковая панель относительно .layout: left=${Math.round(sideRect.left - layoutRect.left)}px top=${Math.round(sideRect.top - layoutRect.top)}px\n` +
        `боковая панель — размер: width=${Math.round(sideRect.width)}px height=${Math.round(sideRect.height)}px`;
    }
    valuesEl.textContent = text;
  }

  // dragMode: "move" двигает left/top, "resize" меняет width/height. getTarget
  // читает АКТУАЛЬНЫЙ элемент активной вкладки на каждый mousedown — так один
  // и тот же чип в панели управляет то вкладкой «Проверка», то «Генерация».
  function bindControl(ctrlEl, getTarget, dragMode) {
    let active = false, startX = 0, startY = 0, base1 = 0, base2 = 0, target = null;
    ctrlEl.addEventListener("mousedown", (e) => {
      target = getTarget();
      if (!target) return;
      e.preventDefault();
      active = true;
      startX = e.clientX;
      startY = e.clientY;
      if (dragMode === "move") {
        base1 = parseFloat(getComputedStyle(target).left) || 0;
        base2 = parseFloat(getComputedStyle(target).top) || 0;
      } else {
        const rect = target.getBoundingClientRect();
        base1 = rect.width;
        base2 = rect.height;
      }
    });
    window.addEventListener("mousemove", (e) => {
      if (!active || !target) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (dragMode === "move") {
        target.style.left = `${base1 + dx}px`;
        target.style.top = `${base2 + dy}px`;
      } else {
        target.style.width = `${Math.max(150, base1 + dx)}px`;
        target.style.height = `${Math.max(100, base2 + dy)}px`;
      }
      updateReadout();
    });
    window.addEventListener("mouseup", () => { active = false; target = null; });
  }

  bindControl(readout.querySelector('[data-action="move-plan"]'), () => activeElements().wrap, "move");
  bindControl(readout.querySelector('[data-action="resize-plan"]'), () => activeElements().plan, "resize");
  bindControl(readout.querySelector('[data-action="move-side"]'), () => activeElements().sidePanel, "move");
  bindControl(readout.querySelector('[data-action="resize-side"]'), () => activeElements().sidePanel, "resize");

  updateReadout();
}

// Прямой запрос пользователя (2026-09-19): «а если на презентации дадут новый
// DWG файл?» — загрузка своего чертежа прямо через браузер, без правки кода/
// демо-реестра. Обработка синхронная (см. pipeline/web/uploads.py) — на
// крупных объектах может занять несколько минут, поэтому кнопка явно уходит
// в состояние "Обрабатываю..." и текст честно предупреждает заранее.
function setupUploadPanel() {
  const panel = document.getElementById("upload-panel");
  document.getElementById("upload-btn").addEventListener("click", () => {
    panel.hidden = false;
  });
  document.getElementById("upload-close").addEventListener("click", () => {
    panel.hidden = true;
  });

  document.getElementById("upload-submit-btn").addEventListener("click", async () => {
    const filesInput = document.getElementById("upload-files");
    const statusEl = document.getElementById("upload-status");
    const submitBtn = document.getElementById("upload-submit-btn");
    if (!filesInput.files || filesInput.files.length === 0) {
      statusEl.textContent = "Выберите хотя бы один файл (.dwg или .dxf).";
      return;
    }
    const formData = new FormData();
    for (const f of filesInput.files) formData.append("files", f);
    const label = document.getElementById("upload-label").value.trim();
    if (label) formData.append("label_ru", label);

    submitBtn.disabled = true;
    submitBtn.textContent = "Обрабатываю... (может занять несколько минут)";
    statusEl.textContent = "";
    try {
      const res = await fetch("/api/uploads", { method: "POST", body: formData });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `${res.status}`);
      }
      const obj = await res.json();
      const demoSelect = document.getElementById("demo-select");
      const option = document.createElement("option");
      option.value = obj.id;
      option.textContent = obj.label_ru;
      demoSelect.appendChild(option);
      demoSelect.value = obj.id;
      state.demoId = obj.id;
      updateSelectTitle(demoSelect);

      const categorySelect = document.getElementById("category-select");
      categorySelect.value = obj.default_territory_category;
      state.territoryCategory = obj.default_territory_category;
      updateSelectTitle(categorySelect);

      resetProposals();
      await loadGeometry();
      statusEl.textContent = `Готово: «${obj.label_ru}» загружен и выбран как текущий объект.`;
      panel.hidden = true;
    } catch (err) {
      statusEl.textContent = `Ошибка: ${err.message}`;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Загрузить и обработать";
    }
  });
}

// Прямой запрос пользователя (2026-09-19): окончательное решение о посадке
// остаётся за пользователем — печатная версия должна давать возможность
// убрать текстовые рекомендации/предупреждения сервиса, оставив только сам
// план и выбор пользователя. Полный ответ сервиса при этом ВСЕГДА сохраняется
// на сервере (см. pipeline/web/audit_log.py) — переключатель влияет только
// на то, что видно/печатается в браузере, не на то, что залогировано.
function setupPrintAndHideRecommendations() {
  document.getElementById("print-generate-btn").addEventListener("click", () => window.print());
  document.getElementById("print-review-btn").addEventListener("click", () => window.print());

  document.getElementById("hide-recs-generate").addEventListener("change", (e) => {
    document.getElementById("generate-panel").classList.toggle("recommendations-hidden", e.target.checked);
  });
  document.getElementById("hide-recs-review").addEventListener("change", (e) => {
    document.getElementById("review-panel").classList.toggle("recommendations-hidden", e.target.checked);
  });
}

// Прямой запрос пользователя (2026-09-19): нормативная база обновляется
// вручную (заменой файла в data/reference/, см. docs/DATA_UPDATE_GUIDE.md) —
// эта панель честно показывает, какая версия (дата изменения файла на диске)
// каждого справочника сейчас реально используется сервисом.
function setupDataSourcesPanel() {
  const panel = document.getElementById("data-sources-panel");
  document.getElementById("data-sources-btn").addEventListener("click", async () => {
    panel.hidden = false;
    const tbody = panel.querySelector("tbody");
    tbody.innerHTML = `<tr><td colspan="3">Загрузка...</td></tr>`;
    try {
      const sources = await fetchJSON("/api/data-sources");
      tbody.innerHTML = sources
        .map(
          (s) => `<tr><td>${escapeHtml(s.label_ru)}</td><td>${escapeHtml(s.file)}</td><td>${escapeHtml(s.updated_at)}</td></tr>`
        )
        .join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="3">Ошибка загрузки: ${escapeHtml(err.message)}</td></tr>`;
    }
  });
  document.getElementById("data-sources-close").addEventListener("click", () => {
    panel.hidden = true;
  });
}

// Прямой запрос пользователя (2026-09-19): «никто так цены обновлять не
// будет! нужна загрузка для прайс-листа с распознаванием растений». Разбор
// (preview) ничего не пишет на диск — показывает таблицу предложений,
// применение (apply) требует явного нажатия и происходит только для строк,
// отмеченных чекбоксом (по умолчанию отмечены все, кроме unclassified —
// см. pipeline/catalog/price_list_ingest.py про логику распознавания).
let lastPriceListProposals = [];

function setupPriceListPanel() {
  document.getElementById("price-list-parse-btn").addEventListener("click", async () => {
    const fileInput = document.getElementById("price-list-file");
    const statusEl = document.getElementById("price-list-status");
    const previewEl = document.getElementById("price-list-preview");
    if (!fileInput.files || fileInput.files.length === 0) {
      statusEl.textContent = "Выберите файл прайс-листа.";
      return;
    }
    statusEl.textContent = "Разбираю...";
    previewEl.hidden = true;
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    try {
      const res = await fetch("/api/price-list/preview", { method: "POST", body: formData });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `${res.status}`);
      }
      const data = await res.json();
      lastPriceListProposals = data.proposals;
      renderPriceListProposals(data);
      statusEl.textContent = `Распознано строк: ${data.rows_found}, не удалось классифицировать: ${data.unclassified_count}.`;
    } catch (err) {
      statusEl.textContent = `Ошибка: ${err.message}`;
    }
  });

  document.getElementById("price-list-apply-btn").addEventListener("click", async () => {
    const statusEl = document.getElementById("price-list-status");
    const checkboxes = document.querySelectorAll("#price-list-table tbody input[type=checkbox]:checked");
    const updates = Array.from(checkboxes).map((cb) => {
      const p = lastPriceListProposals[Number(cb.dataset.index)];
      return {
        category_key: p.category_key,
        label_ru: p.label_ru,
        default_rub: p.proposed_default_rub,
        min_rub: p.proposed_min_rub,
        max_rub: p.proposed_max_rub,
      };
    });
    if (updates.length === 0) {
      statusEl.textContent = "Не отмечено ни одной категории для применения.";
      return;
    }
    statusEl.textContent = "Применяю...";
    try {
      const result = await fetchJSON("/api/price-list/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      });
      statusEl.textContent = `Обновлено категорий: ${result.updated_categories}. Бэкап сохранён: ${result.backup_file}.`;
      document.getElementById("price-list-preview").hidden = true;
      // Обновить дату в таблице источников выше, если она уже открыта.
      const sources = await fetchJSON("/api/data-sources");
      const tbody = document.querySelector("#data-sources-table tbody");
      tbody.innerHTML = sources
        .map((s) => `<tr><td>${escapeHtml(s.label_ru)}</td><td>${escapeHtml(s.file)}</td><td>${escapeHtml(s.updated_at)}</td></tr>`)
        .join("");
    } catch (err) {
      statusEl.textContent = `Ошибка: ${err.message}`;
    }
  });
}

function renderPriceListProposals(data) {
  const previewEl = document.getElementById("price-list-preview");
  const tbody = document.querySelector("#price-list-table tbody");
  tbody.innerHTML = data.proposals
    .map((p, i) => {
      const currentText = p.current_default_rub != null ? `${Math.round(p.current_default_rub).toLocaleString("ru-RU")} ₽` : "— (новая категория)";
      return `<tr>
        <td><input type="checkbox" data-index="${i}" checked></td>
        <td>${escapeHtml(p.label_ru)}${p.is_new ? " <em>(новая)</em>" : ""}</td>
        <td>${currentText}</td>
        <td>${Math.round(p.proposed_default_rub).toLocaleString("ru-RU")} ₽ (${Math.round(p.proposed_min_rub).toLocaleString("ru-RU")}–${Math.round(p.proposed_max_rub).toLocaleString("ru-RU")})</td>
        <td>${p.matched_row_count}</td>
      </tr>`;
    })
    .join("");
  previewEl.hidden = data.proposals.length === 0;
}

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.querySelector(`[data-tab-panel="${btn.dataset.tab}"]`).classList.add("active");
    });
  });
}

function resetProposals() {
  state.proposals = [];
  state.lastReview = null;
  renderProposalList();
  document.getElementById("review-summary").textContent = "Добавьте хотя бы одну посадку и нажмите «Проверить».";
  document.getElementById("review-issues").innerHTML = "";
}

// ---------- геометрия / отрисовка плана ----------

// Индикатор загрузки при смене объекта/категории — прямой запрос пользователя
// (2026-09-24): «нужно окно загрузки, чтобы показать, что сервис не завис».
// На обеих вкладках сразу — loadGeometry() перерисовывает оба плана.
function setLoadingOverlayVisible(visible) {
  for (const id of ["loading-overlay-review", "loading-overlay-generate"]) {
    const el = document.getElementById(id);
    if (el) el.hidden = !visible;
  }
}

async function loadGeometry() {
  setLoadingOverlayVisible(true);
  try {
    const geo = await fetchJSON(
      `/api/demo-objects/${state.demoId}/geometry?territory_category=${state.territoryCategory}`
    );
    state.geometry = geo;
    drawBasePlan("plan-svg-generate", geo);
    drawBasePlan("plan-svg-review", geo);
    renderGenerateMarkers([]);
    renderReviewMarkers();
    await loadSpeciesPicker("tree");
    await loadSpeciesPicker("shrub");
  } finally {
    setLoadingOverlayVisible(false);
  }
}

function ringToPathSegment(points) {
  if (!points.length) return "";
  return "M" + points.map((p) => p.join(",")).join("L") + "Z";
}

function polygonsToPathD(polygons) {
  return polygons.map((poly) => ringToPathSegment(poly.exterior) + poly.holes.map(ringToPathSegment).join("")).join(" ");
}

function drawBasePlan(svgId, geo) {
  const svg = document.getElementById(svgId);
  svg.innerHTML = "";
  if (!geo.bounds) return;

  const [minx, miny, maxx, maxy] = geo.bounds;
  const w = maxx - minx;
  const h = maxy - miny;
  // Небольшой отступ, чтобы точки у самой границы не обрезались.
  const pad = Math.max(w, h) * 0.04;
  svg.setAttribute("viewBox", `${minx - pad} ${miny - pad} ${w + 2 * pad} ${h + 2 * pad}`);
  svg.dataset.baseViewBox = `${minx - pad} ${miny - pad} ${w + 2 * pad} ${h + 2 * pad}`;
  svg.dataset.strokeWidth = String(Math.max(w, h) * 0.002);
  // ВАЖНО (2026-09-19, скриншоты пользователя — два РАЗНЫХ бага под одной
  // жалобой «кружки вылезают за границу»):
  //
  // 1) Радиус маркера считается ДОЛЕЙ ТЕКУЩЕЙ ширины viewBox (не исходной!),
  //    иначе при зуме видимая область уменьшается, а маркер, зафиксированный
  //    в реальных метрах, визуально растёт на экране (раздувание при зуме).
  //    Доли пересчитываются в addMarker()/updateMarkerScale() на каждый тик
  //    зума — так экранный размер маркера примерно постоянен, как у метки
  //    на карте.
  // 2) НО чистая доля экрана без потолка ломается на КРУПНОМ участке: на
  //    старте (весь участок, ширина видa — сотни метров) те же 1.2% давали
  //    РЕАЛЬНЫЙ радиус дерева ~6 м — при типичном требуемом отступе от
  //    границы ~2 м круг математически (а не только визуально) пересекал
  //    границу уже на стартовом виде, независимо от масштаба экрана.
  //    Подтверждено измерением: margin = расстояние_до_границы − радиус
  //    получался отрицательным на BASE-зуме. Поэтому реальный радиус
  //    ДОПОЛНИТЕЛЬНО ограничен потолком в метрах (markerRMax*) — маркер
  //    никогда не рисуется крупнее реалистичного «пятна» дерева/куста,
  //    даже если доля экрана позволяла бы больше. При сильном зуме, когда
  //    доля-от-viewBox уже меньше потолка, работает прежний механизм (доля
  //    экрана), и группы кустарника по-прежнему можно разглядеть по отдельности.
  //
  // Кустарник (743-ПП, «групповая посадка») сажается плотными группами —
  // реальное расстояние между точками одной группы может быть меньше метра —
  // поэтому и доля, и потолок у него заметно меньше, чем у дерева.
  svg.dataset.markerRFactor = "0.012";
  svg.dataset.markerRFactorShrub = "0.006";
  svg.dataset.markerRMaxTree = "0.35";
  svg.dataset.markerRMaxShrub = "0.15";

  const siteBoundary = document.createElementNS(NS, "path");
  siteBoundary.setAttribute("d", polygonsToPathD(geo.site_boundary));
  siteBoundary.setAttribute("fill", "var(--site)");
  siteBoundary.setAttribute("stroke", "#8a9481");
  siteBoundary.setAttribute("stroke-width", svg.dataset.strokeWidth);
  siteBoundary.setAttribute("fill-rule", "evenodd");
  svg.appendChild(siteBoundary);

  // Зона кустарника рисуется ПЕРВОЙ, под зоной дерева: отступы для кустарника
  // (743-ПП, Табл. 3.6.1) почти везде меньше, чем для дерева, поэтому
  // allowed_zone.shrub геометрически шире allowed_zone.tree (проверено на
  // демо-объекте «Песчаный переулок», 2026-09-24: 357.75 м² против 219.14 м²).
  // БАГ, найденный по жалобе пользователя («если ставишь парки и бульвары,
  // при автогенерации он вылезает за пределы разрешённой зоны», 2026-09-24):
  // здесь рисовалась только зона дерева, а точки кустарника накладывались
  // поверх обеих зон — на категории «Парки» (самая высокая норма густоты
  // кустарника, 800-1000 шт/га, см. data/reference/planting_density_per_hectare.yaml)
  // это стало массово заметно как «точки вне зоны», хотя каждая точка на самом
  // деле строго внутри СВОЕЙ (более широкой) допустимой зоны — см.
  // pipeline/placement/generator.py::generate_placement (allowed.contains(pt)).
  const allowedShrub = document.createElementNS(NS, "path");
  allowedShrub.setAttribute("d", polygonsToPathD(geo.allowed_zone.shrub));
  allowedShrub.setAttribute("fill", "var(--allowed-shrub)");
  allowedShrub.setAttribute("fill-opacity", "0.75");
  allowedShrub.setAttribute("fill-rule", "evenodd");
  svg.appendChild(allowedShrub);

  const allowedTree = document.createElementNS(NS, "path");
  allowedTree.setAttribute("d", polygonsToPathD(geo.allowed_zone.tree));
  allowedTree.setAttribute("fill", "var(--allowed-tree)");
  allowedTree.setAttribute("fill-opacity", "0.75");
  allowedTree.setAttribute("fill-rule", "evenodd");
  svg.appendChild(allowedTree);

  const markersGroup = document.createElementNS(NS, "g");
  markersGroup.setAttribute("id", `${svgId}-markers`);
  svg.appendChild(markersGroup);

  setupZoomPan(svg);
}

// Зум колесом + перетаскивание фона — прямой ответ на замечание пользователя
// (2026-09-18): при масштабе всего участка плотная группа кустарника (реальная
// норма — сантиметры между кустами в группе, 743-ПП) неизбежно выглядит одним
// пятном; уменьшать кружки до полной нечитаемости — другая крайность (тогда
// не видно САМИХ кустов). Зум даёт посмотреть и общую форму участка, и состав
// конкретной группы, не жертвуя ни тем, ни другим.
function setupZoomPan(svg) {
  // Не навешивать дважды при повторной перерисовке одного и того же <svg>.
  if (svg.dataset.zoomPanReady) return;
  svg.dataset.zoomPanReady = "1";

  const container = svg.parentElement;
  const vb = svg.viewBox.baseVal;

  container.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const [, , baseW] = svg.dataset.baseViewBox.split(" ").map(Number);
      const pt = svgPointFromEvent(svg, e.clientX, e.clientY);
      const scale = e.deltaY > 0 ? 1.15 : 1 / 1.15;
      const newWidth = vb.width * scale;
      if (newWidth < baseW * 0.01 || newWidth > baseW * 1.2) return;
      const newHeight = vb.height * scale;
      vb.x = pt.x - (pt.x - vb.x) * scale;
      vb.y = pt.y - (pt.y - vb.y) * scale;
      vb.width = newWidth;
      vb.height = newHeight;
      updateMarkerScale(svg);
    },
    { passive: false }
  );

  let panning = false;
  let panStartClient = null;
  let panStartVb = null;
  container.addEventListener("mousedown", (e) => {
    if (e.target.closest(".species-popup")) return;
    panning = true;
    panStartClient = { x: e.clientX, y: e.clientY };
    panStartVb = { x: vb.x, y: vb.y };
    container.style.cursor = "grabbing";
  });
  window.addEventListener("mousemove", (e) => {
    if (!panning) return;
    const rect = svg.getBoundingClientRect();
    const scaleX = vb.width / rect.width;
    const scaleY = vb.height / rect.height;
    vb.x = panStartVb.x - (e.clientX - panStartClient.x) * scaleX;
    vb.y = panStartVb.y - (e.clientY - panStartClient.y) * scaleY;
  });
  window.addEventListener("mouseup", () => {
    panning = false;
    container.style.cursor = "";
  });

  container.addEventListener("dblclick", () => {
    const [x, y, w, h] = svg.dataset.baseViewBox.split(" ").map(Number);
    vb.x = x;
    vb.y = y;
    vb.width = w;
    vb.height = h;
    updateMarkerScale(svg);
  });
}

function svgPointFromEvent(svg, clientX, clientY) {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const local = pt.matrixTransform(ctm.inverse());
  return { x: local.x, y: local.y };
}

// Радиус маркера = доля ТЕКУЩЕЙ ширины viewBox, но не больше реалистичного
// потолка в метрах (markerRMax*) — см. подробное объяснение обоих багов
// (раздувание при зуме + пересечение границы на старте) в drawBasePlan().
function markerRadius(svg, lifeForm) {
  const vb = svg.viewBox.baseVal;
  const isShrub = lifeForm === "shrub";
  const factor = parseFloat(isShrub ? svg.dataset.markerRFactorShrub : svg.dataset.markerRFactor);
  const max = parseFloat(isShrub ? svg.dataset.markerRMaxShrub : svg.dataset.markerRMaxTree);
  return Math.min(vb.width * factor, max);
}

function updateMarkerScale(svg) {
  const group = document.getElementById(`${svg.id}-markers`);
  if (!group) return;
  const rTree = markerRadius(svg, "tree");
  const rShrub = markerRadius(svg, "shrub");
  for (const circle of group.children) {
    circle.setAttribute("r", circle.dataset.lifeForm === "shrub" ? rShrub : rTree);
  }
}

// Правила русского числительного (1 дерево, 2 дерева, 5 деревьев) — тот же
// алгоритм, что и в pipeline/review/placement_review.py::_ru_plural, но на JS,
// т.к. подписи здесь формируются на фронтенде, а не приходят с бэкенда.
function ruPlural(count, one, few, many) {
  const n = Math.abs(count) % 100;
  const n1 = n % 10;
  if (n >= 11 && n <= 14) return many;
  if (n1 === 1) return one;
  if (n1 >= 2 && n1 <= 4) return few;
  return many;
}

// Прямой ответ на замечание пользователя (2026-09-19): при плотной группе
// кустарника маркеры визуально сливаются в "кашу", и на глаз не понять,
// сколько там объектов — под планом всегда показываем точный текстовый счёт.
function updateMarkerCountCaption(captionId, treeCount, shrubCount) {
  const el = document.getElementById(captionId);
  if (!el) return;
  if (treeCount === 0 && shrubCount === 0) {
    el.hidden = true;
    return;
  }
  const parts = [];
  if (treeCount > 0) parts.push(`${treeCount} ${ruPlural(treeCount, "дерево", "дерева", "деревьев")}`);
  if (shrubCount > 0) parts.push(`${shrubCount} ${ruPlural(shrubCount, "кустарник", "кустарника", "кустарников")}`);
  el.textContent = `На плане: ${parts.join(", ")}.`;
  el.hidden = false;
}

function addMarker(svgId, x, y, cssClass, title, lifeForm) {
  const svg = document.getElementById(svgId);
  const r = markerRadius(svg, lifeForm);
  const group = document.getElementById(`${svgId}-markers`);
  const circle = document.createElementNS(NS, "circle");
  circle.setAttribute("cx", x);
  circle.setAttribute("cy", y);
  circle.setAttribute("r", r);
  circle.setAttribute("class", cssClass);
  circle.dataset.lifeForm = lifeForm || "tree";
  if (title) {
    const titleEl = document.createElementNS(NS, "title");
    titleEl.textContent = title;
    circle.appendChild(titleEl);
  }
  group.appendChild(circle);
  return circle;
}

// ---------- вкладка "Автоматическая генерация" ----------

// Чек-боксы выбора видов на вкладке автогенерации — прямой запрос
// пользователя (2026-09-24): «должен быть список растений с чек-боксами,
// чтобы пользователь мог сначала задать перечень растений, которые он хочет
// разместить». Переиспользует тот же /api/species, что и попап выбора вида
// на вкладке «Проверка моего выбора» (pipeline/web/app.py, agent-agnostic
// список: рекомендован для категории + не инвазивен). Ничего не отмечено =
// прежнее однвидовое поведение по умолчанию (см. runGenerate ниже — ключ
// life_form просто не отправляется, если Set пуст).
const speciesPickerCache = { tree: [], shrub: [] };

async function loadSpeciesPicker(lifeForm) {
  const listEl = document.querySelector(`.species-picker-list[data-life-form="${lifeForm}"]`);
  if (!listEl) return;
  // Список видов зависит от категории территории — при смене категории
  // старый выбор мог перестать существовать в новом списке, поэтому сбрасываем.
  state.selectedSpecies[lifeForm] = new Set();
  listEl.innerHTML = `<span class="hint">Загрузка...</span>`;
  try {
    const species = await fetchJSON(`/api/species?territory_category=${state.territoryCategory}&life_form=${lifeForm}`);
    speciesPickerCache[lifeForm] = species;
    if (species.length === 0) {
      listEl.innerHTML = `<span class="hint">Нет рекомендованных видов для этой категории.</span>`;
      return;
    }
    renderSpeciesPickerList(lifeForm, "");
  } catch (err) {
    listEl.innerHTML = `<span class="hint">Ошибка загрузки списка видов.</span>`;
  }
}

function renderSpeciesPickerList(lifeForm, filterText) {
  const listEl = document.querySelector(`.species-picker-list[data-life-form="${lifeForm}"]`);
  if (!listEl) return;
  const filter = filterText.trim().toLowerCase();
  const all = speciesPickerCache[lifeForm];
  const filtered = filter ? all.filter((s) => s.name_ru.toLowerCase().includes(filter)) : all;
  if (filtered.length === 0) {
    listEl.innerHTML = `<span class="hint">Ничего не найдено.</span>`;
    return;
  }
  listEl.innerHTML = filtered
    .map(
      (s) => `<label class="species-picker-item">
        <input type="checkbox" value="${escapeHtml(s.name_ru)}" ${state.selectedSpecies[lifeForm].has(s.name_ru) ? "checked" : ""}>
        <span>${escapeHtml(s.name_ru)}</span>
      </label>`
    )
    .join("");
  listEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) state.selectedSpecies[lifeForm].add(cb.value);
      else state.selectedSpecies[lifeForm].delete(cb.value);
    });
  });
}

function setupSpeciesPicker() {
  document.querySelectorAll(".species-picker-search").forEach((input) => {
    input.addEventListener("input", () => {
      renderSpeciesPickerList(input.dataset.lifeForm, input.value);
    });
  });
}

async function runGenerate() {
  // Вторая рабочая кнопка в блоке «Результат» (2026-09-24, прямой запрос
  // пользователя) — та же кнопка, тот же обработчик; обе переключаются вместе,
  // чтобы не было впечатления, что одна из них не работает во время генерации.
  const buttons = [document.getElementById("generate-btn"), document.getElementById("generate-btn-2")];
  buttons.forEach((b) => {
    b.disabled = true;
    b.textContent = "Генерирую...";
  });
  try {
    // Ключ жизненной формы отправляется, ТОЛЬКО если хоть один вид отмечен —
    // пустой массив означал бы для бэкенда "ни один вид не подходит", а не
    // "пользователь ничего не выбрал, реши сам" (см. pipeline/placement/
    // generator.py::pick_species_multi).
    const selectedSpecies = {};
    for (const lifeForm of ["tree", "shrub"]) {
      if (state.selectedSpecies[lifeForm].size > 0) {
        selectedSpecies[lifeForm] = Array.from(state.selectedSpecies[lifeForm]);
      }
    }
    const body = { territory_category: state.territoryCategory };
    if (Object.keys(selectedSpecies).length > 0) {
      body.selected_species = selectedSpecies;
    }
    const report = await fetchJSON(`/api/demo-objects/${state.demoId}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderGenerateMarkers(report.placements || []);
    renderGenerateSummary(report);
  } catch (err) {
    document.getElementById("generate-summary").innerHTML = `<div class="issue bad">Ошибка: ${escapeHtml(err.message)}</div>`;
  } finally {
    buttons.forEach((b) => {
      b.disabled = false;
      b.textContent = "Сгенерировать план";
    });
  }
}

function renderGenerateMarkers(placements) {
  const svg = document.getElementById("plan-svg-generate");
  const group = document.getElementById("plan-svg-generate-markers");
  if (!group) return;
  group.innerHTML = "";
  let treeCount = 0;
  let shrubCount = 0;
  for (const p of placements) {
    const cls = p.status === "placed" ? (p.planting_kind === "tree" ? "marker-tree" : "marker-shrub") : "marker-no-species";
    const title = p.status === "placed" ? `${p.species_name_ru} (${p.planting_kind})` : `${p.planting_kind}: нет рекомендованного вида`;
    addMarker("plan-svg-generate", p.x, p.y, cls, title, p.planting_kind);
    if (p.planting_kind === "tree") treeCount += 1;
    else if (p.planting_kind === "shrub") shrubCount += 1;
  }
  updateMarkerCountCaption("plan-svg-generate-count", treeCount, shrubCount);
}

function renderGenerateSummary(report) {
  const s = report.summary || {};
  const rows = [];
  rows.push(`<div class="summary-row"><span>Всего предложено</span><span>${s.total_points ?? "—"}</span></div>`);
  rows.push(`<div class="summary-row"><span>Размещено</span><span>${s.placed ?? "—"}</span></div>`);
  rows.push(`<div class="summary-row"><span>Отклонено (нет вида)</span><span>${s.rejected ?? "—"}</span></div>`);
  if (report.total_estimated_cost_rub != null) {
    rows.push(
      `<div class="summary-row"><span>Оценочная стоимость</span><span>${Math.round(report.total_estimated_cost_rub).toLocaleString("ru-RU")} ₽</span></div>`
    );
  }
  document.getElementById("generate-summary").innerHTML = rows.join("");

  // Пользователь верно заметил: после генерации не было видно, какие виды
  // реально предложены — маркеры на плане показывают вид только по наведению
  // (title), что неочевидно. Явный список ниже — прямой ответ на это.
  const speciesHtml = (report.species_choices || [])
    .map((sc) => {
      const icon = sc.planting_kind === "tree" ? "🌳" : "🌿";
      const label = sc.planting_kind === "tree" ? "Дерево" : "Кустарник";
      const names = sc.chosen_species_names_ru && sc.chosen_species_names_ru.length > 0
        ? sc.chosen_species_names_ru
        : (sc.chosen_species_name_ru ? [sc.chosen_species_name_ru] : []);
      if (names.length === 0) {
        return `<div class="summary-row"><span>${icon} ${label}</span><span>нет рекомендованного вида</span></div>`;
      }
      // Несколько видов (чек-боксы, 2026-09-24) — показываем ВСЕ, не только
      // первый, иначе непонятно, что распределение по кругу вообще сработало.
      let extra = "";
      if (sc.eligible_alternatives && sc.eligible_alternatives.length > 0) {
        extra = `<div class="hint">Ещё ${sc.eligible_alternatives.length} вид(ов) были равно допустимы — выбор по алфавиту, не по качеству (${escapeHtml(sc.eligible_alternatives.slice(0, 3).join(", "))}${sc.eligible_alternatives.length > 3 ? "…" : ""}).</div>`;
      }
      if (sc.species_conflict_note) {
        extra += `<div class="hint">${escapeHtml(sc.species_conflict_note)}</div>`;
      }
      return `<div class="summary-row"><span>${icon} ${label}</span><span>${escapeHtml(names.join(", "))}</span></div>${extra}`;
    })
    .join("");
  document.getElementById("generate-species").innerHTML = speciesHtml
    ? `<h2 style="margin-top:8px;">Выбранные виды</h2>${speciesHtml}`
    : "";

  const warnings = [];
  if (report.site_boundary_status && report.site_boundary_status !== "found") {
    warnings.push(`Граница участка определена ненадёжно: ${report.site_boundary_status}`);
  }
  if (report.otk_boundary_plausibility && report.otk_boundary_plausibility.verdict === "implausible") {
    warnings.push(report.otk_boundary_plausibility.explanation);
  }
  if (report.lep_unknown_voltage && report.lep_unknown_voltage.count > 0) {
    warnings.push(
      `${report.lep_unknown_voltage.count} объектов ЛЭП с неопределённым классом напряжения — применён отступ ${report.lep_unknown_voltage.fallback_distance_m} м (см. известные упрощения ниже).`
    );
  }
  document.getElementById("generate-warnings").innerHTML = warnings
    .map((w) => `<div class="issue bad"><span class="kind">внимание</span>${escapeHtml(w)}</div>`)
    .join("");
}

// ---------- вкладка "Проверка моего выбора" (drag-and-drop) ----------

// Рисует ВИДИМЫЙ кружок (цвета маркера), который тащится вместе с курсором —
// используется и как перетаскиваемый "призрак" в setupDragAndDrop(), и как
// сама точка на плане после подтверждения (addMarker()).
function createDragImage(lifeForm) {
  const size = lifeForm === "tree" ? 30 : 20;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const r = size / 2 - 2;
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, r, 0, Math.PI * 2);
  ctx.fillStyle = lifeForm === "tree" ? "rgba(47,107,58,0.85)" : "rgba(111,174,106,0.85)";
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = lifeForm === "tree" ? "#163a1c" : "#2f6b3a";
  ctx.stroke();
  return canvas;
}

// Прямой повторный запрос пользователя (2026-09-20): «наводишь на дерево,
// удерживаешь зажатой кнопку мыши и сразу тащишь кружок, а не абстрактную
// невидимую точку». Первая попытка (нативный HTML5 drag-and-drop +
// `dataTransfer.setDragImage`) по спецификации должна рисовать canvas как
// курсор-призрак, но фактический рендер этого "призрака" — на уровне
// операционной системы/браузера, не гарантирован и не виден программно
// (даже наша headless-проверка не могла подтвердить его на экране) — на деле
// у пользователя кружок не был виден при перетаскивании. Заменено на
// полностью самодельный drag через mousedown/mousemove/mouseup: реальный
// `<canvas>`-элемент добавляется в DOM и двигается вслед за курсором на
// КАЖДЫЙ mousemove — гарантированно видим в любом браузере, не зависит от
// нативного drag-image рендеринга. `draggable="true"` убран из палитры
// (pipeline/web/static/index.html) — иначе браузер параллельно пытался бы
// начать ещё и нативный drag поверх этого.
function setupDragAndDrop() {
  const container = document.getElementById("plan-svg-container-review");
  let dragGhost = null;
  let draggingLifeForm = null;

  function moveGhost(clientX, clientY) {
    if (!dragGhost) return;
    dragGhost.style.left = `${clientX - dragGhost.width / 2}px`;
    dragGhost.style.top = `${clientY - dragGhost.height / 2}px`;
  }

  function onMouseMove(e) {
    moveGhost(e.clientX, e.clientY);
  }

  function onMouseUp(e) {
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
    if (dragGhost) {
      dragGhost.remove();
      dragGhost = null;
    }
    const lifeForm = draggingLifeForm;
    draggingLifeForm = null;
    if (!lifeForm) return;

    const rect = container.getBoundingClientRect();
    const insideContainer =
      e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
    if (!insideContainer) return;

    const svg = document.getElementById("plan-svg-review");
    const point = svgPointFromEvent(svg, e.clientX, e.clientY);
    openSpeciesPopup(container, e.clientX - rect.left, e.clientY - rect.top, lifeForm, point);
  }

  document.querySelectorAll(".palette-item").forEach((item) => {
    item.addEventListener("mousedown", (e) => {
      e.preventDefault(); // не выделять текст палитры при перетаскивании
      draggingLifeForm = item.dataset.lifeForm;
      dragGhost = createDragImage(draggingLifeForm);
      dragGhost.style.position = "fixed";
      dragGhost.style.pointerEvents = "none";
      dragGhost.style.zIndex = "1000";
      document.body.appendChild(dragGhost);
      moveGhost(e.clientX, e.clientY);
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    });
  });
}

async function openSpeciesPopup(container, left, top, lifeForm, point) {
  document.querySelectorAll(".species-popup").forEach((el) => el.remove());

  const popup = document.createElement("div");
  popup.className = "species-popup";
  popup.style.left = `${Math.max(0, left - 120)}px`;
  popup.style.top = `${Math.max(0, top - 10)}px`;
  popup.innerHTML = `<div class="hint" style="margin-bottom:6px;">${lifeForm === "tree" ? "🌳 Дерево" : "🌿 Кустарник"} — выберите вид:</div>
    <select id="species-select"><option>Загрузка...</option></select>
    <div class="popup-actions">
      <button class="secondary" id="species-cancel">Отмена</button>
      <button class="primary" id="species-confirm" style="padding:6px 10px;">Добавить</button>
    </div>`;
  container.appendChild(popup);

  const select = popup.querySelector("#species-select");
  try {
    const species = await fetchJSON(
      `/api/species?territory_category=${state.territoryCategory}&life_form=${lifeForm}`
    );
    if (species.length === 0) {
      select.innerHTML = `<option value="">Нет рекомендованных видов для этой категории</option>`;
    } else {
      select.innerHTML = species.map((s) => `<option value="${escapeHtml(s.name_ru)}">${escapeHtml(s.name_ru)}</option>`).join("");
    }
  } catch (err) {
    select.innerHTML = `<option value="">Ошибка загрузки списка видов</option>`;
  }

  popup.querySelector("#species-cancel").addEventListener("click", () => popup.remove());
  popup.querySelector("#species-confirm").addEventListener("click", () => {
    const speciesName = select.value;
    if (!speciesName) {
      popup.remove();
      return;
    }
    const id = `p${Date.now()}_${Math.round(Math.random() * 1000)}`;
    state.proposals.push({ id, species_name_ru: speciesName, life_form: lifeForm, x: point.x, y: point.y });
    popup.remove();
    renderProposalList();
    renderReviewMarkers();
  });
}

function renderProposalList() {
  const list = document.getElementById("proposal-list");
  list.innerHTML = state.proposals
    .map((p) => {
      const hasIssue = state.lastReview && state.lastReview.has(p.id);
      return `<li data-id="${p.id}" class="${hasIssue ? "has-issue" : ""}">
        <span class="proposal-jump" data-jump="${p.id}" title="Показать на карте">${hasIssue ? "⚠️ " : ""}${p.life_form === "tree" ? "🌳" : "🌿"} ${escapeHtml(p.species_name_ru)}
          <span class="meta">(${p.x.toFixed(1)}, ${p.y.toFixed(1)})</span>
        </span>
        <button class="secondary" data-remove="${p.id}">✕</button>
      </li>`;
    })
    .join("");
  list.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.proposals = state.proposals.filter((p) => p.id !== btn.dataset.remove);
      state.lastReview = null;
      renderProposalList();
      renderReviewMarkers();
    });
  });
  // Прямое замечание пользователя (2026-09-24): «растений могут быть сотни,
  // как узнать, где он накосячил?» — список сам по себе честно помечает
  // проблемные пункты (⚠️ + подсветка), но с сотнями строк искать взглядом
  // всё равно неудобно — клик по пункту приближает карту прямо к этой точке
  // (см. jumpToPointOnMap), не нужно искать её глазами среди других маркеров.
  list.querySelectorAll("[data-jump]").forEach((el) => {
    el.addEventListener("click", () => {
      const p = state.proposals.find((pr) => pr.id === el.dataset.jump);
      if (p) jumpToPointOnMap(p.x, p.y, p.id);
    });
  });
  document.getElementById("review-btn").disabled = state.proposals.length === 0;
}

function renderReviewMarkers() {
  const group = document.getElementById("plan-svg-review-markers");
  if (!group) return;
  group.innerHTML = "";
  const svg = document.getElementById("plan-svg-review");
  let treeCount = 0;
  let shrubCount = 0;
  for (const p of state.proposals) {
    let cls = p.life_form === "tree" ? "marker-tree" : "marker-shrub";
    if (state.lastReview && state.lastReview.has(p.id)) {
      cls = "marker-issue";
    }
    const circle = addMarker("plan-svg-review", p.x, p.y, cls, `${p.species_name_ru} (${p.life_form}) — перетащите, чтобы сдвинуть, кликните, чтобы удалить`, p.life_form);
    circle.dataset.proposalId = p.id;
    attachProposalMarkerInteractions(circle, svg, p.id);
    if (p.life_form === "tree") treeCount += 1;
    else shrubCount += 1;
  }
  updateMarkerCountCaption("plan-svg-review-count", treeCount, shrubCount);
}

// Приближает вид карты (вкладка «Проверка моего выбора») к конкретной точке
// плана и коротко подсвечивает её маркер — используется и из списка «Ваши
// посадки» (клик по помеченной ⚠️ строке), и из списка замечаний после
// проверки (клик по конкретному нарушению). Сохраняет текущее соотношение
// сторон окна просмотра, только уменьшает масштаб при необходимости — не
// раздражает пользователя внезапным сильным приближением, если он и так уже
// смотрит достаточно крупно.
function jumpToPointOnMap(x, y, proposalId) {
  const svg = document.getElementById("plan-svg-review");
  if (!svg || !svg.dataset.baseViewBox) return;
  const [, , baseW] = svg.dataset.baseViewBox.split(" ").map(Number);
  const vb = svg.viewBox.baseVal;
  const aspect = vb.height / vb.width;
  const targetWidth = baseW * 0.12; // масштаб, на котором отдельные точки группы кустарника уже различимы
  const newWidth = Math.min(vb.width, targetWidth);
  const newHeight = newWidth * aspect;
  vb.x = x - newWidth / 2;
  vb.y = y - newHeight / 2;
  vb.width = newWidth;
  vb.height = newHeight;
  updateMarkerScale(svg);

  if (proposalId) {
    const circle = document.querySelector(`#plan-svg-review-markers circle[data-proposal-id="${proposalId}"]`);
    if (circle) {
      circle.classList.add("marker-flash");
      setTimeout(() => circle.classList.remove("marker-flash"), 1600);
    }
  }
}

// Прямое замечание пользователя (2026-09-24): «почему нельзя подвинуть уже
// перемещенные на карту объекты или редактировать (удалить, например)?» —
// раньше единственным способом убрать точку был крестик в списке «Ваши
// посадки», а сдвинуть уже поставленную точку было вообще нельзя (только
// добавить новую через палитру). Здесь — прямое взаимодействие с маркером на
// карте: перетаскивание (сдвиг реальной позиции) и клик без сдвига (удаление,
// то же самое действие, что и крестик в списке, просто ближе к точке, где
// пользователь и так уже смотрит — с сотнями точек искать нужную строку в
// списке неудобно). Порог "клик vs перетаскивание" (MARKER_DRAG_THRESHOLD_PX)
// отличает случайное дрожание руки от намеренного перетаскивания.
const MARKER_DRAG_THRESHOLD_PX = 3;

function attachProposalMarkerInteractions(circle, svg, proposalId) {
  circle.style.cursor = "grab";
  let moved = false;
  let startClient = null;

  function onMouseMove(e) {
    const dx = e.clientX - startClient.x;
    const dy = e.clientY - startClient.y;
    if (Math.hypot(dx, dy) > MARKER_DRAG_THRESHOLD_PX) moved = true;
    const point = svgPointFromEvent(svg, e.clientX, e.clientY);
    circle.setAttribute("cx", point.x);
    circle.setAttribute("cy", point.y);
  }

  function onMouseUp(e) {
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
    circle.style.cursor = "grab";

    if (moved) {
      const point = svgPointFromEvent(svg, e.clientX, e.clientY);
      const proposal = state.proposals.find((p) => p.id === proposalId);
      if (proposal) {
        proposal.x = point.x;
        proposal.y = point.y;
      }
    } else {
      state.proposals = state.proposals.filter((p) => p.id !== proposalId);
    }
    state.lastReview = null; // предыдущая проверка больше не отражает текущие точки
    renderProposalList();
    renderReviewMarkers();
  }

  circle.addEventListener("mousedown", (e) => {
    e.stopPropagation(); // не запускать одновременно панорамирование фона (container.mousedown)
    e.preventDefault();
    moved = false;
    startClient = { x: e.clientX, y: e.clientY };
    circle.style.cursor = "grabbing";
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  });
}

async function runReview() {
  const btn = document.getElementById("review-btn");
  btn.disabled = true;
  btn.textContent = "Проверяю...";
  try {
    const result = await fetchJSON(`/api/demo-objects/${state.demoId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ territory_category: state.territoryCategory, proposals: state.proposals }),
    });
    const withIssue = new Set(result.issues.filter((i) => i.planting_id).map((i) => i.planting_id));
    state.lastReview = withIssue;
    renderReviewMarkers();
    renderProposalList(); // подсветка ⚠️ проблемных пунктов в списке «Ваши посадки»
    renderReviewResult(result);
  } catch (err) {
    document.getElementById("review-summary").innerHTML = `<div class="issue bad">Ошибка: ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Проверить";
  }
}

// Внутренние коды нарушений (из pipeline/review/placement_review.py и
// pipeline/otk/offset_compliance.py) — пользователь верно заметил, что
// "insufficient_offset"/"density_too_low" на экране непонятны без знания
// кода; здесь только читаемая подпись, сама причина — в detail (уже по-русски).
const ISSUE_KIND_LABELS = {
  species_invasive: "Инвазивный вид (369-ПП)",
  unknown_species: "Вид не найден в каталоге",
  species_not_recommended_for_category: "Вид не рекомендован для категории",
  species_conflict: "Конфликт видов (сноска [6])",
  density_too_high: "Слишком высокая плотность посадки (623-ПП)",
  density_too_low: "Слишком низкая плотность посадки (623-ПП)",
  outside_site_boundary: "Точка вне границы участка",
  insufficient_offset: "Недостаточный отступ (743-ПП)",
  spacing_too_close: "Слишком близко к соседнему растению (743-ПП)",
};

function issueKindLabel(kind) {
  return ISSUE_KIND_LABELS[kind] || kind;
}

// Прямой запрос пользователя (2026-09-20): «у нас отсутствует примерный
// подсчёт стоимости посаженных растений при ручном вводе» — та же оценка,
// что и на вкладке автогенерации (pipeline/catalog/planting_cost.py), но
// считается на сервере по факту предложенных пользователем видов (см.
// PlacementReviewReport.total_estimated_cost_rub в pipeline/review/
// placement_review.py). Вид, не найденный в каталоге, честно исключается из
// суммы (уже отдельно диагностирован как unknown_species), не додумывается.
function renderCostSummary(result) {
  if (result.total_estimated_cost_rub == null) return "";
  const costText = `${Math.round(result.total_estimated_cost_rub).toLocaleString("ru-RU")} ₽`;
  const unknownNote =
    result.cost_unknown_count > 0
      ? `<div class="hint">Не учтено в сумме: ${result.cost_unknown_count} ${ruPlural(result.cost_unknown_count, "посадка", "посадки", "посадок")} — вид не найден в каталоге.</div>`
      : "";
  return `<div class="summary-row"><span>Оценочная стоимость</span><span>${costText}</span></div>${unknownNote}`;
}

function renderReviewResult(result) {
  const summaryEl = document.getElementById("review-summary");
  const issuesEl = document.getElementById("review-issues");
  if (result.verdict === "ok") {
    summaryEl.innerHTML = `<div class="issue good">Проверено ${result.checked_count} посадок — замечаний нет.</div>${renderCostSummary(result)}`;
    issuesEl.innerHTML = "";
    return;
  }
  summaryEl.innerHTML = `<div class="issue bad">${escapeHtml(result.explanation)}</div>${renderCostSummary(result)}`;
  // Замечания, привязанные к конкретной точке (planting_id — не густота/
  // общие замечания без привязки), кликабельны — приближают карту к этой
  // точке (см. jumpToPointOnMap), прямой ответ на «сотни точек, как найти,
  // где накосячил».
  issuesEl.innerHTML = result.issues
    .map((i) => {
      const clickable = i.planting_id ? ` data-jump="${i.planting_id}" title="Показать на карте"` : "";
      return `<div class="issue bad${i.planting_id ? " clickable" : ""}"${clickable}>
        <span class="kind">${escapeHtml(issueKindLabel(i.kind))}${i.planting_id ? " · " + escapeHtml(i.planting_id) : ""}</span>
        ${escapeHtml(i.detail)}
      </div>`;
    })
    .join("");
  issuesEl.querySelectorAll("[data-jump]").forEach((el) => {
    el.addEventListener("click", () => {
      const p = state.proposals.find((pr) => pr.id === el.dataset.jump);
      if (p) jumpToPointOnMap(p.x, p.y, p.id);
    });
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = String(str);
  return div.innerHTML;
}

init().catch((err) => {
  document.body.innerHTML = `<pre style="padding:20px;color:#b33f3f;">Ошибка инициализации: ${err.message}</pre>`;
});
