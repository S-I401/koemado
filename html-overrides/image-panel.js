/* 会話画面の「画像を送る」パネル（setup.py が index.html に読み込ませる）。
 * 設定画面で画像認識がオンのときだけ表示する。画像はこの PC の中（127.0.0.1）の AIAvatarKit にだけ送られ、
 * ブラウザにも保存しない。送る前に長辺 1024px の JPEG に縮小する。 */
(async () => {
  let enabled = false;
  try {
    const r = await fetch("/settings/api/vision", { cache: "no-store" });
    enabled = (await r.json()).enabled;
  } catch (e) { return; }
  if (!enabled) return;

  const box = document.createElement("div");
  box.id = "imagePanel";
  box.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:300;background:rgba(255,255,255,.95);color:#222;border:1px solid #bbb;border-radius:10px;padding:8px 10px;font:13px/1.4 system-ui,'Yu Gothic UI',sans-serif;max-width:min(420px,92vw);box-shadow:0 2px 8px rgba(0,0,0,.2)";
  box.innerHTML = `
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <label style="cursor:pointer;border:1px solid #888;border-radius:6px;padding:3px 8px">画像を選ぶ<input type="file" accept="image/*" id="ipFile" hidden></label>
      <button id="ipCam" type="button" style="border:1px solid #888;border-radius:6px;background:#fff;padding:3px 8px;cursor:pointer">カメラで撮る</button>
      <span style="color:#666">貼り付け（Ctrl+V）もできます</span>
    </div>
    <video id="ipVideo" playsinline muted style="display:none;width:100%;margin-top:6px;border-radius:6px"></video>
    <div id="ipPrev" style="display:none;margin-top:6px;align-items:center;gap:8px">
      <img id="ipImg" style="max-height:80px;max-width:120px;border-radius:6px;border:1px solid #ccc">
      <button id="ipClear" type="button" style="border:0;background:none;color:#a00;cursor:pointer">取り消す</button>
    </div>
    <div style="display:flex;gap:6px;margin-top:6px">
      <input id="ipText" type="text" value="この画像について教えて。" style="flex:1;min-width:0;padding:4px 6px;border:1px solid #aaa;border-radius:6px">
      <button id="ipSend" type="button" style="border:1px solid #36c;background:#36c;color:#fff;border-radius:6px;padding:3px 12px;cursor:pointer">送る</button>
    </div>
    <div style="color:#666;margin-top:4px">画像は保存しません（設定でオンにしない限り、会話の記録にも残りません）。</div>`;
  document.body.appendChild(box);
  const $ = (id) => box.querySelector("#" + id);
  let dataUrl = null;

  function setImage(url) {
    dataUrl = url;
    $("ipImg").src = url || "";
    $("ipPrev").style.display = url ? "flex" : "none";
  }

  function shrink(source, w, h) {
    const max = 1024, k = Math.min(1, max / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  }

  function fromBlob(blob) {
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => { setImage(shrink(img, img.naturalWidth, img.naturalHeight)); URL.revokeObjectURL(url); };
    img.onerror = () => { alert("この画像は読み込めませんでした。"); URL.revokeObjectURL(url); };
    img.src = url;
  }

  $("ipFile").onchange = (e) => { if (e.target.files[0]) fromBlob(e.target.files[0]); e.target.value = ""; };
  document.addEventListener("paste", (e) => {
    for (const item of e.clipboardData?.items || []) {
      if (item.type.startsWith("image/")) { fromBlob(item.getAsFile()); e.preventDefault(); return; }
    }
  });
  $("ipClear").onclick = () => setImage(null);

  let stream = null;
  $("ipCam").onclick = async () => {
    const v = $("ipVideo");
    if (!stream) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } } });
      } catch (e) { alert("カメラを使えませんでした（ブラウザの許可を確認してください）。"); return; }
      v.srcObject = stream; v.style.display = "block"; await v.play();
      $("ipCam").textContent = "撮影する";
      return;
    }
    setImage(shrink(v, v.videoWidth, v.videoHeight));
    stream.getTracks().forEach((t) => t.stop()); stream = null;
    v.style.display = "none"; $("ipCam").textContent = "カメラで撮る";
  };

  $("ipSend").onclick = () => {
    if (!dataUrl) { alert("先に画像を選ぶか、貼り付けてください。"); return; }
    if (typeof window.chat !== "function") { alert("会話がまだ始まっていません。「Start」を押してください。"); return; }
    const ok = window.chat($("ipText").value || "この画像について教えて。", dataUrl);
    if (ok === false) { alert("サーバーに接続されていません。「Start」を押してから送ってください。"); return; }
    setImage(null);
  };
})();
