window.FileTypes = {
  MAX_BYTES: 20 * 1024 * 1024,
  ACCEPT: [".pdf", ".docx", ".doc", ".txt", ".md", ".hwp", ".hwpx", ".xlsx", ".xls", ".csv", ".jpg", ".jpeg", ".png", ".gif", ".webp"],
  LABEL: "PDF, DOCX, TXT, MD, HWP, HWPX, XLSX, XLS, CSV, JPG, PNG",

  kind(name) {
    const ext = String(name || "").split(".").pop().toLowerCase();
    if (ext === "hwp" || ext === "hwpx") return "hwp";
    if (ext === "pdf") return "pdf";
    if (ext === "docx" || ext === "doc") return "docx";
    if (ext === "txt" || ext === "md") return "text";
    if (ext === "xlsx" || ext === "xls" || ext === "csv") return "spreadsheet";
    if (["jpg", "jpeg", "png", "gif", "webp"].includes(ext)) return "image";
    return "other";
  },

  extLabel(name) {
    const ext = String(name || "").split(".").pop().toUpperCase();
    return ext || "FILE";
  },

  isAllowed(name) {
    const ext = "." + String(name || "").split(".").pop().toLowerCase();
    return this.ACCEPT.includes(ext);
  },

  formatSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  },

  isConvertCommand(text) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return /pdf\s*로\s*(변환|바꿔|만들)/i.test(t) || /이거\s*pdf/i.test(t);
  },
};
