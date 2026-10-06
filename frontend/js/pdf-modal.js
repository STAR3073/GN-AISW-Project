window.PdfModal = {
  init() {
    this._el = document.getElementById("pdf-modal");
    this._frame = document.getElementById("pdf-frame");
    this._title = document.getElementById("pdf-modal-title");
    document.getElementById("pdf-modal-close")?.addEventListener("click", () => this.hide());
    this._el?.addEventListener("click", (e) => {
      if (e.target === this._el) this.hide();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.hide();
    });
  },

  show(url, title) {
    if (!this._el || !this._frame) return;
    if (this._title) this._title.textContent = title || "PDF 미리보기";
    this._frame.src = url;
    this._el.classList.remove("hidden");
  },

  hide() {
    if (!this._el) return;
    this._el.classList.add("hidden");
    if (this._frame) this._frame.src = "";
  },
};
