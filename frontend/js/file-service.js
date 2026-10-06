window.FileService = {
  async upload(file) {
    const form = new FormData();
    form.append("file", file);
    return this._json("/api/attachments", { method: "POST", body: form });
  },

  async convertPdf(id) {
    return this._json(`/api/attachments/${id}/convert-pdf`, { method: "POST" });
  },

  async previewPdf(id) {
    return this._json(`/api/attachments/${id}/preview-pdf`, { method: "POST" });
  },

  async promote(id, projectId) {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return this._json(`/api/documents/from-attachment/${id}${q}`, { method: "POST" });
  },

  async readText(id) {
    return this._json(`/api/attachments/${id}/text`);
  },

  async summarize(id) {
    return this._json(`/api/attachments/${id}/summarize`, { method: "POST" });
  },

  fileUrl(id, download) {
    return `/api/attachments/${id}/file${download ? "?download=1" : ""}`;
  },

  validate(file) {
    if (!file) return "파일이 없습니다.";
    if (!window.FileTypes.isAllowed(file.name)) {
      return `지원하지 않는 파일입니다. ${window.FileTypes.LABEL}만 업로드할 수 있습니다.`;
    }
    if (file.size > window.FileTypes.MAX_BYTES) {
      return "파일 크기가 제한(20MB)을 초과했습니다.";
    }
    return null;
  },

  async _json(path, options = {}) {
    let res;
    try {
      res = await fetch(path, options);
    } catch {
      throw new Error("서버에 연결하지 못했습니다. 네트워크 상태를 확인하세요.");
    }
    if (!res.ok) {
      let detail = await res.text();
      try {
        const parsed = JSON.parse(detail);
        detail = parsed.detail || detail;
      } catch {
        /* keep */
      }
      if (res.status >= 500) throw new Error(detail || "서버 처리 중 오류가 발생했습니다.");
      throw new Error(detail || "요청을 처리하지 못했습니다.");
    }
    const type = res.headers.get("content-type") || "";
    if (type.includes("application/json")) return res.json();
    return res;
  },
};
