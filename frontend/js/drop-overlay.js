window.DropOverlay = {
  init(onFiles) {
    this._onFiles = onFiles;
    this._count = 0;
    this._el = document.getElementById("drop-overlay");
    if (!this._el) return;

    const hasFiles = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");

    window.addEventListener("dragenter", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      this._count += 1;
      this.show();
    });
    window.addEventListener("dragover", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    window.addEventListener("dragleave", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      this._count = Math.max(0, this._count - 1);
      if (this._count === 0) this.hide();
    });
    window.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      this._count = 0;
      this.hide();
      const files = [...(e.dataTransfer.files || [])];
      if (files.length && this._onFiles) this._onFiles(files);
    });
  },

  show() {
    this._el.classList.remove("hidden");
    this._el.setAttribute("aria-hidden", "false");
  },

  hide() {
    this._el.classList.add("hidden");
    this._el.setAttribute("aria-hidden", "true");
  },
};
