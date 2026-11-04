const state = { products: {}, selectedProductId: null, aiConfigured: false };
const HANDOFF_KEY = "brand-reply-handoff-v1";
let handoffItems = loadHandoff();
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function loadHandoff() {
  try {
    const saved = JSON.parse(localStorage.getItem(HANDOFF_KEY) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch { return []; }
}

function updateHandoffMeta() {
  $("#handoffCount").textContent = `交付清单：${handoffItems.length} 条`;
  $("#exportBtn").disabled = handoffItems.length === 0;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = response.headers.get("content-type")?.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(body.error || `请求失败（${response.status}）`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = "toast"; }, 2800);
}

async function loadState() {
  try {
    const result = await api("/api/state");
    state.products = result.products || {};
    state.aiConfigured = Boolean(result.ai_configured);
    const badge = $("#modelBadge");
    badge.innerHTML = `<i class="${state.aiConfigured ? "ready" : "off"}"></i><span>${state.aiConfigured ? "AI 服务已配置" : "AI 服务未配置"}</span>`;
    renderProductOptions();
    renderProductSettings();
  } catch (error) { toast(error.message, true); }
}

function renderProductOptions() {
  const select = $("#productSelect");
  const entries = Object.entries(state.products);
  const oldValue = select.value;
  select.innerHTML = entries.map(([id, product]) => `<option value="${escapeHtml(id)}">${escapeHtml(product.name || id)}</option>`).join("");
  if (entries.some(([id]) => id === oldValue)) select.value = oldValue;
  else if (entries.length) select.value = entries[0][0];
  if (!entries.length) select.innerHTML = '<option value="">暂无产品，请先添加产品资料</option>';
}

function renderProductSettings() {
  const entries = Object.entries(state.products);
  if (!entries.length) {
    $("#productList").innerHTML = '<div class="editor-empty">暂无产品配置</div>';
    $("#productEditor").innerHTML = '<div class="editor-empty">请先在 config/products.json 中配置产品。</div>';
    return;
  }
  if (!state.selectedProductId || !state.products[state.selectedProductId]) state.selectedProductId = entries[0][0];
  $("#productList").innerHTML = entries.map(([id, product]) => `<button class="product-item ${id === state.selectedProductId ? "selected" : ""}" data-id="${escapeHtml(id)}"><span class="product-symbol">✦</span><span><strong>${escapeHtml(product.name || id)}</strong><small>${(product.approved_points || []).length} 条已核实卖点</small></span></button>`).join("");
  $$(".product-item").forEach(button => button.addEventListener("click", () => {
    state.selectedProductId = button.dataset.id;
    renderProductSettings();
  }));
  const product = state.products[state.selectedProductId];
  $("#productEditor").innerHTML = `<p class="eyebrow">PRODUCT FACTS</p><h2>${escapeHtml(product.name || state.selectedProductId)}</h2><p class="editor-copy">一行填写一条已经确认、适用于对应型号的信息。</p><label for="claimsInput">已核实卖点</label><textarea id="claimsInput" class="claims-input" maxlength="4200" placeholder="填写已审核的产品功能、设计特点等；不要填写未经证实的效果或体验。">${escapeHtml((product.approved_points || []).join("\n"))}</textarea><p class="editor-hint">卖点为空时无法生成。AI 只会匹配这里的内容。</p><button class="button button-primary" id="saveClaims">保存卖点</button>`;
  $("#saveClaims").addEventListener("click", saveClaims);
}

function switchView(view) {
  $$(".view").forEach(section => section.classList.toggle("active", section.id === `view-${view}`));
  $$(".nav-tab").forEach(button => button.classList.toggle("active", button.dataset.view === view));
}

function showResult(result) {
  if (!result.draft) {
    $("#resultPanel").hidden = true;
    $("#emptyOutput").hidden = false;
    $("#emptyOutput").innerHTML = '<div class="empty-icon">✦</div><strong>暂时没有生成内容</strong><span>请调整帖子文字或产品卖点后重试</span>';
    return false;
  }
  $("#emptyOutput").hidden = true;
  $("#resultPanel").hidden = false;
  $("#resultText").value = result.draft || "";
  return true;
}

$("#generatorForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#generateBtn");
  const payload = {
    url: $("#linkInput").value.trim(),
    post_title: $("#titleInput").value.trim(),
    product_id: $("#productSelect").value,
    post_text: $("#textInput").value.trim(),
  };
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    const result = await api("/api/generate", { method: "POST", body: JSON.stringify(payload) });
    const generated = showResult(result);
    toast(generated ? "内容已生成" : "暂时没有生成内容");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = "<span>✦</span> 生成回复"; }
});

$("#clearBtn").addEventListener("click", () => {
  $("#generatorForm").reset();
  $("#emptyOutput").hidden = false;
  $("#emptyOutput").innerHTML = '<div class="empty-icon">✦</div><strong>生成的内容会显示在这里</strong><span>填写左侧内容并点击“生成回复”</span>';
  $("#resultPanel").hidden = true;
  if (Object.keys(state.products).length) $("#productSelect").value = Object.keys(state.products)[0];
});

$("#copyBtn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#resultText").value);
    toast("已复制回复文案");
  } catch { toast("复制失败，请手动选择文案复制", true); }
});

$("#addToHandoffBtn").addEventListener("click", () => {
  const reply = $("#resultText").value.trim();
  const productId = $("#productSelect").value;
  if (!reply) return toast("请先生成回复内容", true);
  handoffItems.push({
    url: $("#linkInput").value.trim(),
    post_title: $("#titleInput").value.trim(),
    product: state.products[productId]?.name || productId,
    reply,
  });
  try { localStorage.setItem(HANDOFF_KEY, JSON.stringify(handoffItems)); }
  catch { handoffItems.pop(); return toast("浏览器无法保存交付清单，请清理存储空间后重试", true); }
  updateHandoffMeta();
  toast("已加入交付清单");
});

$("#exportBtn").addEventListener("click", async () => {
  if (!handoffItems.length) return;
  const button = $("#exportBtn");
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records: handoffItems }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || "Excel 生成失败");
    }
    const file = await response.blob();
    const objectUrl = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "品牌回复交付清单.xlsx";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    toast("Excel 已下载，可转交发帖团队");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = handoffItems.length === 0; button.textContent = "下载 Excel"; }
});

async function saveClaims() {
  const approved_points = $("#claimsInput").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  try {
    await api("/api/products", { method: "POST", body: JSON.stringify({ product_id: state.selectedProductId, approved_points }) });
    state.products[state.selectedProductId].approved_points = approved_points;
    renderProductSettings(); renderProductOptions();
    toast("产品卖点已保存");
  } catch (error) { toast(error.message, true); }
}

$$('[data-view]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
updateHandoffMeta();
loadState();
