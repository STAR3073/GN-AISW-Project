window.PreviewPanel = {
  _gen: 0,
  _pdfDoc: null,
  _page: 1,
  _scale: 1,
  _fit: true,
  _item: null,
  _pdfUrl: null,
  _downloadUrl: null,

  init() {
    this._root = document.getElementById("preview-panel");
    this._workspace = document.getElementById("workspace");
    this._title = document.getElementById("preview-title");
    this._meta = document.getElementById("preview-meta");
    this._toolbar = document.getElementById("preview-toolbar");
    this._body = document.getElementById("preview-body");
    this._pageLabel = document.getElementById("preview-page");
    this._zoomLabel = document.getElementById("preview-zoom");
    this._download = document.getElementById("preview-download");
    if (window.pdfjsLib) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }
    document.getElementById("preview-close")?.addEventListener("click", () => this.close());
    document.getElementById("preview-prev")?.addEventListener("click", () => this._go(-1));
    document.getElementById("preview-next")?.addEventListener("click", () => this._go(1));
    document.getElementById("preview-zoom-out")?.addEventListener("click", () => this._zoomBy(-0.15));
    document.getElementById("preview-zoom-in")?.addEventListener("click", () => this._zoomBy(0.15));
    document.getElementById("preview-fit")?.addEventListener("click", () => this._fitWidth());
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !this.isOpen()) return;
      this.close();
    });
    window.addEventListener("resize", () => {
      if (this._fit && this._pdfDoc) this._renderPage();
    });
  },

  isOpen() {
    return Boolean(this._root && !this._root.classList.contains("hidden"));
  },

  async open(item) {
    if (!item) return;
    const gen = ++this._gen;
    this._item = item;
    this._show();
    this._setHeader(item.filename, item);
    this._hideToolbar();
    this._setDownload(null);

    const kind = item.kind || window.FileTypes.kind(item.filename);
    try {
      if (kind === "hwp") {
        await this._openHwp(item, gen);
        return;
      }
      if (kind === "pdf" || kind === "preview_pdf") {
        const url = await this._fileUrl(item, gen);
        if (gen !== this._gen) return;
        await this._openPdf(url, item, gen);
        return;
      }
      if (kind === "image") {
        const url = await this._fileUrl(item, gen);
        if (gen !== this._gen) return;
        this._setDownload(this._downloadUrlFor(item, url));
        this._body.innerHTML = `<div class="h-full grid place-items-center p-4"><img src="${url}" alt="${this._esc(item.filename)}" class="max-w-full max-h-full object-contain rounded shadow-sm" /></div>`;
        return;
      }
      if (kind === "text") {
        const url = await this._fileUrl(item, gen);
        if (gen !== this._gen) return;
        this._setDownload(this._downloadUrlFor(item, url));
        const res = await fetch(url);
        if (!res.ok) throw new Error("텍스트를 불러오지 못했습니다.");
        const text = await res.text();
        if (gen !== this._gen) return;
        this._body.innerHTML = `<pre class="p-4 text-xs leading-5 whitespace-pre-wrap break-words text-slate-700">${this._esc(text)}</pre>`;
        return;
      }
      if (kind === "spreadsheet") {
        const size = item.size_bytes ? window.FileTypes.formatSize(item.size_bytes) : "";
        const ext = window.FileTypes.extLabel(item.filename);
        this._setDownload(this._downloadUrlFor(item, window.FileService.fileUrl(item.id, true)));
        this._body.innerHTML = `<div class="p-6 text-sm text-slate-600 space-y-1">
          <p class="font-medium text-slate-800">Excel 데이터 파일</p>
          <p>${this._esc(item.filename || "")}</p>
          <p class="text-xs text-slate-500">${this._esc(ext)}${size ? " · " + size : ""}</p>
          <p class="text-xs text-slate-400 pt-2">스프레드시트 미리보기는 제공하지 않습니다. 채팅에서 분석을 요청하세요.</p>
        </div>`;
        return;
      }
      if (kind === "docx") {
        this._showLoading("문서 미리보기를 준비하고 있습니다...");
        const data = await window.FileService.readText(item.id);
        if (gen !== this._gen) return;
        this._setDownload(window.FileService.fileUrl(item.id, true));
        this._body.innerHTML = `<pre class="p-4 text-xs leading-5 whitespace-pre-wrap break-words text-slate-700">${this._esc(data.text || "")}</pre>`;
        return;
      }
      this._body.innerHTML = `<p class="p-4 text-sm text-slate-500">이 파일 형식은 브라우저에서 미리보기를 지원하지 않습니다.</p>`;
    } catch (err) {
      if (gen !== this._gen) return;
      this._body.innerHTML = `<p class="p-4 text-sm text-slate-600">미리보기를 열지 못했습니다. ${this._esc(err.message || err)}</p>`;
    }
  },

  close() {
    this._gen += 1;
    this._destroyPdf();
    if (this._root) {
      this._root.classList.add("hidden");
      this._root.classList.remove("flex");
    }
    this._workspace?.classList.remove("preview-open");
    if (this._body) this._body.innerHTML = "";
    this._item = null;
    this._pdfUrl = null;
    this._downloadUrl = null;
  },

  _show() {
    this._root?.classList.remove("hidden");
    this._root?.classList.add("flex");
    this._workspace?.classList.add("preview-open");
  },

  _setHeader(filename, item) {
    if (this._title) this._title.textContent = filename || "미리보기";
    if (this._meta) {
      const label = window.FileTypes.extLabel(filename);
      const size = item && item.size_bytes ? ` · ${window.FileTypes.formatSize(item.size_bytes)}` : "";
      this._meta.textContent = `${label}${size}`;
    }
  },

  _hideToolbar() {
    this._toolbar?.classList.add("hidden");
  },

  _showToolbar() {
    this._toolbar?.classList.remove("hidden");
  },

  _setDownload(url) {
    this._downloadUrl = url;
    if (!this._download) return;
    if (url) {
      this._download.href = url;
      this._download.classList.remove("pointer-events-none", "opacity-40");
    } else {
      this._download.href = "#";
      this._download.classList.add("pointer-events-none", "opacity-40");
    }
  },

  _showLoading(message) {
    this._hideToolbar();
    this._body.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center px-6 text-center">
        <div class="indet-track w-48"><div class="indet-bar"></div></div>
        <p class="mt-4 text-sm text-slate-600">${this._esc(message)}</p>
      </div>`;
  },

  async _openHwp(item, gen) {
    this._showLoading("문서 미리보기를 준비하고 있습니다...");
    const pdf = await window.FileService.previewPdf(item.id);
    if (gen !== this._gen) return;
    const url = window.FileService.fileUrl(pdf.id);
    await this._openPdf(url, { ...item, filename: item.filename, size_bytes: pdf.size_bytes }, gen, window.FileService.fileUrl(pdf.id, true));
  },

  async _openPdf(url, item, gen, downloadUrl) {
    if (!window.pdfjsLib) throw new Error("PDF Viewer를 불러오지 못했습니다.");
    this._showLoading("문서를 불러오는 중입니다...");
    this._destroyPdf();
    this._pdfUrl = url;
    this._page = 1;
    this._scale = 1;
    this._fit = true;
    const task = window.pdfjsLib.getDocument({ url, withCredentials: false });
    this._pdfDoc = await task.promise;
    if (gen !== this._gen) return;
        this._setDownload(downloadUrl || this._downloadUrlFor(item, url));
    this._showToolbar();
    await this._renderPage();
  },

  async _renderPage() {
    if (!this._pdfDoc || !this._body) return;
    const page = await this._pdfDoc.getPage(this._page);
    let scale = this._scale;
    if (this._fit) {
      const base = page.getViewport({ scale: 1 });
      const width = Math.max(240, (this._body.clientWidth || 360) - 32);
      scale = Math.max(0.4, Math.min(2.5, width / base.width));
      this._scale = scale;
    }
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.className = "mx-auto bg-white shadow-sm";
    this._body.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "p-4 min-h-full";
    wrap.appendChild(canvas);
    this._body.appendChild(wrap);
    await page.render({ canvasContext: ctx, viewport }).promise;
    if (this._pageLabel) this._pageLabel.textContent = `${this._page} / ${this._pdfDoc.numPages}`;
    if (this._zoomLabel) this._zoomLabel.textContent = `${Math.round(this._scale * 100)}%`;
  },

  _go(delta) {
    if (!this._pdfDoc) return;
    const next = this._page + delta;
    if (next < 1 || next > this._pdfDoc.numPages) return;
    this._page = next;
    this._renderPage();
  },

  _zoomBy(delta) {
    if (!this._pdfDoc) return;
    this._fit = false;
    this._scale = Math.max(0.4, Math.min(2.5, this._scale + delta));
    this._renderPage();
  },

  _fitWidth() {
    if (!this._pdfDoc) return;
    this._fit = true;
    this._renderPage();
  },

  _destroyPdf() {
    if (this._pdfDoc && this._pdfDoc.destroy) {
      try {
        this._pdfDoc.destroy();
      } catch {
        /* ignore */
      }
    }
    this._pdfDoc = null;
  },

  async _fileUrl(item, gen) {
    const attUrl = window.FileService.fileUrl(item.id);
    try {
      const res = await fetch(attUrl, { method: "HEAD" });
      if (res.ok || res.status === 405) return attUrl;
    } catch {
      return attUrl;
    }
    if (gen !== this._gen) return attUrl;
    if (item.documentId) return `/api/documents/${item.documentId}/file`;
    return `/api/documents/${item.id}/file`;
  },

  _downloadUrlFor(_item, url) {
    if (!url) return "#";
    if (url.includes("download=1")) return url;
    return url.includes("?") ? `${url}&download=1` : `${url}?download=1`;
  },

  _esc(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  },
};
