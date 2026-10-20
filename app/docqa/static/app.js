(() => {
  const $ = (id) => document.getElementById(id);
  const tokenInput = $("token");
  try { tokenInput.value = localStorage.getItem("docqa.token") || ""; } catch (_) {}
  tokenInput.addEventListener("change", () => { try { localStorage.setItem("docqa.token", tokenInput.value); } catch (_) {} });
  const headers = () => (tokenInput.value ? { Authorization: `Bearer ${tokenInput.value}` } : {});

  let selected = null;
  let docs = [];

  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
    if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
    return res.status === 204 ? null : res.json();
  }

  function render() {
    const ul = $("docs");
    ul.innerHTML = "";
    for (const d of docs) {
      const li = document.createElement("li");
      li.className = d.id === selected ? "selected" : "";
      li.innerHTML = `<span class="name" title="${d.filename}">${d.filename}</span>
        <span class="status ${d.status}" title="${d.error || ""}">${d.status}${d.page_count ? ` · ${d.page_count}p` : ""}</span>
        <button class="del" title="delete">✕</button>`;
      li.addEventListener("click", () => { if (d.status === "ready") { selected = d.id; $("current").textContent = d.filename; $("askbtn").disabled = false; $("chat").innerHTML = ""; render(); } });
      li.querySelector(".del").addEventListener("click", async (e) => { e.stopPropagation(); if (!confirm(`Delete ${d.filename}?`)) return; await api(`/api/documents/${d.id}`, { method: "DELETE" }); if (selected === d.id) { selected = null; $("askbtn").disabled = true; } await refresh(); });
      ul.appendChild(li);
    }
  }

  async function refresh() {
    try { docs = (await api("/api/documents")).items; render(); } catch (e) { console.error(e); }
  }

  $("upload").addEventListener("submit", (e) => {
    e.preventDefault();
    const file = $("file").files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    const bar = $("progress");
    bar.hidden = false; bar.value = 0;
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) bar.value = Math.round((ev.loaded / ev.total) * 100); };
    xhr.onload = () => { bar.hidden = true; if (xhr.status >= 400) alert(`Upload failed: ${xhr.status} ${xhr.responseText.slice(0, 200)}`); $("file").value = ""; refresh(); };
    xhr.onerror = () => { bar.hidden = true; alert("Upload failed"); };
    xhr.open("POST", "/api/documents");
    for (const [k, v] of Object.entries(headers())) xhr.setRequestHeader(k, v);
    xhr.send(form);
  });

  function addMsg(cls, text) { const div = document.createElement("div"); div.className = `msg ${cls}`; div.textContent = text; $("chat").appendChild(div); $("chat").scrollTop = 1e9; return div; }
  function linkCitations(div, sources) {
    const byPage = Object.fromEntries(sources.map((s) => [s.page, s.excerpt]));
    div.innerHTML = div.textContent.replace(/\[p\.\s*(\d+)\]/g, (m, p) => `<span class="cite" title="${(byPage[p] || "").replace(/"/g, "&quot;")}">[p. ${p}]</span>`);
    if (sources.length) { const s = document.createElement("div"); s.className = "sources"; s.textContent = "Retrieved pages: " + [...new Set(sources.map((x) => x.page))].sort((a, b) => a - b).join(", "); div.appendChild(s); }
  }

  $("askform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("question").value.trim();
    if (!q || !selected) return;
    $("question").value = ""; $("askbtn").disabled = true;
    addMsg("q", q);
    const out = addMsg("a", "");
    let sources = [];
    try {
      const res = await fetch(`/api/documents/${selected}/ask`, { method: "POST", headers: { "Content-Type": "application/json", ...headers() }, body: JSON.stringify({ question: q }) });
      if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        // SSE frames end with a blank line; servers may use \r\n or \n line endings.
        let m;
        while ((m = /\r?\n\r?\n/.exec(buf)) !== null) {
          const frame = buf.slice(0, m.index); buf = buf.slice(m.index + m[0].length);
          let ev = "message", data = "";
          for (const line of frame.split(/\r?\n/)) { if (line.startsWith("event:")) ev = line.slice(6).trim(); else if (line.startsWith("data:")) data += line.slice(5).trim(); }
          if (!data) continue;
          const payload = JSON.parse(data);
          if (ev === "sources") sources = payload.sources;
          else if (ev === "delta") { out.textContent += payload.text; $("chat").scrollTop = 1e9; }
          else if (ev === "error") { out.classList.add("err"); out.textContent += `\n${payload.detail}`; }
        }
      }
      linkCitations(out, sources);
    } catch (err) { out.classList.add("err"); out.textContent = String(err); }
    finally { $("askbtn").disabled = false; }
  });

  refresh();
  setInterval(() => { if (docs.some((d) => d.status === "queued" || d.status === "processing")) refresh(); }, 3000);
})();
