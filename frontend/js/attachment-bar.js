window.AttachmentBar = {
  items: [],

  init({ onRemove, onConvert, onInspect, onSummarize, onPreview }) {
    this._onRemove = onRemove;
    this._onConvert = onConvert;
    this._onInspect = onInspect;
    this._onSummarize = onSummarize;
    this._onPreview = onPreview;
    this._root = document.getElementById("attach-bar");
  },

  setItems(items) {
    this.items = items || [];
    this.render();
  },

  hwpItem() {
    return this.items.find((item) => item.kind === "hwp") || null;
  },

  render() {
    if (!this._root) return;
    if (!this.items.length) {
      this._root.innerHTML = "";
      this._root.classList.add("hidden");
      return;
    }
    this._root.classList.remove("hidden");
    this._root.innerHTML = this.items
      .map((item) => {
        const label = item.kind === "spreadsheet" ? "Excel 데이터 파일" : window.FileTypes.extLabel(item.filename);
        const icon = item.kind === "image" ? "🖼" : item.kind === "spreadsheet" ? "📊" : "📄";
        return `
          <article data-preview-card="${item.id}" class="flex items-center gap-3 border border-slate-200 rounded-lg px-3 py-2 bg-slate-50 min-w-0 cursor-pointer hover:border-slate-300">
            <div class="w-8 h-8 shrink-0 rounded-md bg-white border border-slate-200 grid place-items-center text-sm" aria-hidden="true">${icon}</div>
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium truncate" title="${this._esc(item.filename)}">${this._esc(item.filename)}</div>
              <div class="text-[11px] text-slate-500">${label} · ${window.FileTypes.formatSize(item.size_bytes)}</div>
            </div>
            <button type="button" data-preview="${item.id}" class="shrink-0 text-[11px] px-2 py-1 rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-50">미리보기</button>
            <button type="button" data-remove="${item.id}" class="shrink-0 w-7 h-7 rounded-md text-slate-400 hover:bg-white hover:text-slate-700" aria-label="첨부 제거">×</button>
          </article>
          ${
            item.kind === "hwp"
              ? `<div class="flex flex-wrap gap-1.5 mt-2">
                  <button type="button" data-act="convert" data-id="${item.id}" class="text-[11px] px-2 py-1 rounded-md bg-slate-900 text-white">PDF로 변환</button>
                  <button type="button" data-act="inspect" data-id="${item.id}" class="text-[11px] px-2 py-1 rounded-md border border-slate-200 text-slate-600">문서 내용 확인</button>
                  <button type="button" data-act="summarize" data-id="${item.id}" class="text-[11px] px-2 py-1 rounded-md border border-slate-200 text-slate-600">문서 요약</button>
                </div>`
              : ""
          }`;
      })
      .join("");

    this._root.querySelectorAll("[data-preview-card]").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        const item = this.items.find((row) => row.id === card.dataset.previewCard);
        if (item && this._onPreview) this._onPreview(item);
      });
    });
    this._root.querySelectorAll("[data-preview]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const item = this.items.find((row) => row.id === btn.dataset.preview);
        if (item && this._onPreview) this._onPreview(item);
      });
    });
    this._root.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._onRemove && this._onRemove(btn.dataset.remove);
      });
    });
    this._root.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        if (btn.dataset.act === "convert") this._onConvert && this._onConvert(id);
        if (btn.dataset.act === "inspect") this._onInspect && this._onInspect(id);
        if (btn.dataset.act === "summarize") this._onSummarize && this._onSummarize(id);
      });
    });
  },

  _esc(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  },
};
