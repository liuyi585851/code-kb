from __future__ import annotations

from .web_ui import render_app_shell


def render_qdrant_page() -> str:
    body = """
<div class="metric-grid">
  <div class="metric"><span>Status</span><strong id="status">-</strong></div>
  <div class="metric"><span>Collection</span><strong id="collection">codekb_atoms</strong></div>
  <div class="metric"><span>Points</span><strong id="points">-</strong></div>
  <div class="metric"><span>Vector size</span><strong id="vector-size">-</strong></div>
</div>
<div class="section-grid">
  <section class="panel">
    <h2>Collection</h2>
    <dl>
      <dt>Qdrant version</dt><dd id="version">-</dd>
      <dt>Distance</dt><dd id="distance">-</dd>
      <dt>Indexed vectors</dt><dd id="indexed">-</dd>
      <dt>Segments</dt><dd id="segments">-</dd>
      <dt>Payload fields</dt><dd id="payload-fields">-</dd>
      <dt>API</dt><dd><code>/storage/qdrant/status</code></dd>
    </dl>
  </section>
  <section class="panel">
    <h2>Raw</h2>
    <pre id="raw">{}</pre>
  </section>
</div>"""
    script = """
<script>
  function text(id, value) {
    document.getElementById(id).textContent = value === undefined || value === null || value === "" ? "-" : String(value);
  }
  async function refresh() {
    const status = document.getElementById("status");
    status.textContent = "检查中";
    status.className = "";
    try {
      const response = await fetch("/storage/qdrant/status", { cache: "no-store" });
      const data = await response.json();
      const collection = data.collection || {};
      text("status", data.status);
      status.className = data.status === "ok" ? "ok" : (data.status === "error" ? "error" : "warn");
      text("collection", collection.name || "codekb_atoms");
      text("points", collection.points_count);
      text("vector-size", collection.vector_size);
      text("version", data.version);
      text("distance", collection.distance);
      text("indexed", collection.indexed_vectors_count);
      text("segments", collection.segments_count);
      text("payload-fields", (collection.payload_fields || []).join(", "));
      document.getElementById("raw").textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      status.textContent = "不可用";
      status.className = "error";
      document.getElementById("raw").textContent = String(error);
    }
  }
  document.getElementById("refresh").addEventListener("click", refresh);
  refresh();
</script>"""
    return render_app_shell(
        title="向量库状态",
        subtitle="检查 Code-KB 的 Qdrant collection、向量数量和索引状态。",
        active="qdrant",
        body=body,
        actions=(("/hub", "返回工作台"), ("/storage/readiness", "存储接入"), ("#", "刷新")),
        extra_script=script.replace('href="#"', 'href="#"'),
        max_width="1180px",
    ).replace('<a class="button secondary" href="#">刷新</a>', '<button class="primary" type="button" id="refresh">刷新</button>')
