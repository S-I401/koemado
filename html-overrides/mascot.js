/* koemado のデスクトップのキャラ（mascot.html）。
 * 会話の仕組み（AIAvatarClient の WebSocket・口パク・まばたき・表情）は会話画面と同じ。ここに足したのは次の部分。
 *  - 吹き出し（返答・聞き取った言葉・ツールの状況。数秒で消える。表情タグなどは出さない）
 *  - 話しかけ方: hold（キャラを押している間だけ聞く）/ hotkey（ショートカットキーで 1 回聞く）/ always（常に聞く）。聞いている間は耳のマーク
 *  - 文字で話す入力欄、画像のドラッグ＆ドロップ、こちらから話しかけていない返答（外部エージェントの完了報告など）の通知
 *  - ウィンドウ操作（移動・大きさ・右クリックのメニュー）は mascot.py の橋（QWebChannel の window.__kb）に頼む。
 *    ブラウザで開いたときは橋が無いので、それらの機能だけ無効になる（会話はできる）。
 *  - ウィンドウは透明で、クリックできるのはキャラ・吹き出し・入力欄の形だけ（残りは下のウィンドウに通る）。その形をここで計算して mascot.py に知らせる。
 * コンソールのコマンド: mascot.say("こんにちは") / mascot.state() */
(async () => {
    const SIZES = { small: 200, medium: 320, large: 460 };
    const ZONE_H = 190;
    const MIN_W = 340;
    const TOOL_LABELS = {
        get_weather: "天気を調べています", web_search: "検索しています", web_fetch: "ページを読んでいます",
        remember: "覚えています", recall: "思い出しています", ask_agent: "エージェントに頼んでいます",
        check_agent_tasks: "作業の進み具合を確認しています", send_query_to_openclaw: "OpenClaw に頼んでいます",
    };
    const $ = (id) => document.getElementById(id);

    // ---- python との橋（無ければ null）----
    if (window.__kbReady) await Promise.race([window.__kbReady, new Promise((r) => setTimeout(r, 3000))]);
    const py = window.__kb || null;
    const call = (name, ...args) => new Promise((resolve) => {
        if (!py || typeof py[name] !== "function") return resolve(null);
        try { py[name](...args, (r) => resolve(r === undefined ? null : r)); } catch (e) { resolve(null); }
    });
    for (const lv of ["warn", "error"]) {      // 警告・エラーは、管理画面のログ（mascot）にも流す
        const orig = console[lv].bind(console);
        console[lv] = (...a) => { orig(...a); call("log", `[js ${lv}] ` + a.map((x) => (x && x.stack) || String(x)).join(" ")); };
    }
    window.addEventListener("error", (e) => call("log", `[js error] ${e.message} (${e.filename}:${e.lineno})`));
    window.addEventListener("unhandledrejection", (e) => call("log", `[js rejection] ${e.reason && e.reason.stack || e.reason}`));

    // ---- 設定 ----
    const cfgJson = await call("get_config");
    let cfg = Object.assign({ size: "medium", talk: "hold", hotkey: "ctrl+alt+space", interrupt: false, volume: 1, bubble_seconds: 8 }, (() => { try { return JSON.parse(cfgJson); } catch (e) { return {}; } })());

    // ---- アバター（会話画面と同じ組み立て）----
    const faceImage = $("faceImage"), mouthImage = $("mouthImage"), stage = $("stage");
    // 画像が設定されていないキャラ（シルエット）は、背景のない専用のシルエットを使う（透明なウィンドウに浮かべるため）
    let hasImage = true;
    try { hasImage = !!(await (await fetch("../settings/api/avatar", { cache: "no-store" })).json()).has_image; } catch (e) { /* 既定の画像を使う */ }
    const facePath = (n) => (hasImage ? `../character-image/${n}` : "mascot-silhouette.svg");
    if (!hasImage) faceImage.src = facePath("neutral");
    const avatar = new KoemadoImageAvatar({
        faceImage, mouthImage, rmsScale: 1.0,
        lipsyncEngine: new MFCCLipSyncEngine({ profileUrl: "profiles/default-female.json", maxVolume: -0.8 }),
        faceImagePaths: Object.fromEntries(["neutral", "joy", "angry", "sorrow", "fun", "surprised", "eyes_closed"].map((n) => [n, facePath(n)])),
    });
    const aiavatar = new AIAvatarClient({ webSocketUrl: "../ws", ...avatar.aiAvatarOptions });
    aiavatar.setVolume(cfg.volume);
    await avatar.bind(aiavatar);

    const sessionId = crypto.randomUUID();
    let userId = null;
    try { userId = localStorage.getItem("userId"); } catch (e) { /* 保存できなくても動く */ }
    userId = userId || crypto.randomUUID();

    // ---- 状態 ----
    const S = { listening: false, muted: false, serverProcessing: false, bargeInBlocked: false, turnActive: false, started: false };
    let listenTimer = null;

    // ---- 大きさ・配置 ----
    function layout() {
        const h = SIZES[cfg.size] || SIZES.medium;
        const ratio = faceImage.naturalWidth && faceImage.naturalHeight ? faceImage.naturalWidth / faceImage.naturalHeight : 1;
        const w = Math.round(Math.min(h * ratio, h * 1.6));
        document.documentElement.style.setProperty("--char-h", h + "px");
        document.documentElement.style.setProperty("--char-w", w + "px");
        document.documentElement.style.setProperty("--zone-h", ZONE_H + "px");
        call("resize", Math.max(w + 16, MIN_W), h + ZONE_H);
    }
    faceImage.addEventListener("load", layout);
    layout();

    // ---- 吹き出し ----
    const bubble = $("bubble"), bubbleText = $("bubbleText");
    let bubbleTimer = null, aiText = "";
    const clean = (t) => String(t || "").replace(/\[[A-Za-z_]+:[^\]]*\]/g, "").replace(/\[operation:[^\]]*\]/g, "").trim();
    function showBubble(text, kind = "ai", keep = false) {
        text = clean(text);
        clearTimeout(bubbleTimer);
        if (!text) { bubble.hidden = true; return; }
        bubble.className = kind === "ai" ? "" : kind;
        bubbleText.textContent = text;
        bubble.hidden = false;
        if (!keep) bubbleTimer = setTimeout(hideBubble, cfg.bubble_seconds * 1000);
    }
    function hideBubble() {
        if (aiavatar.isAudioPlaying || S.serverProcessing) { bubbleTimer = setTimeout(hideBubble, 1500); return; }   // 話している間は消さない
        bubble.hidden = true;
        aiText = "";
    }
    const scheduleHide = () => { clearTimeout(bubbleTimer); bubbleTimer = setTimeout(hideBubble, cfg.bubble_seconds * 1000); };

    // ---- マイク ----
    const badge = $("badge");
    function micAllowed() { return !S.muted && (cfg.talk === "always" || S.listening); }
    function applyMic() {
        aiavatar._userMuted = !micAllowed();          // ミュート中は、無音を送る（AIAvatarClient の仕組み）
        if (S.muted) { badge.hidden = false; badge.className = ""; badge.textContent = "🔇"; badge.title = "マイクはミュート中"; }
        else if (micAllowed()) { badge.hidden = false; badge.className = "listen"; badge.textContent = "👂"; badge.title = "聞いています"; }
        else badge.hidden = true;
    }
    function setListening(on) {
        clearTimeout(listenTimer);
        S.listening = on;
        if (on && cfg.talk === "hotkey") listenTimer = setTimeout(() => setListening(false), 20000);   // 話しかけなければ 20 秒で閉じる
        applyMic();
    }
    aiavatar.isMicrophoneMuted = () => {
        if (aiavatar.isAudioPlaying) S.serverProcessing = false;
        if (!aiavatar.isAudioPlaying && !aiavatar.isBacklogAudioPlaying && !S.serverProcessing) S.bargeInBlocked = false;
        if (aiavatar.isBacklogAudioPlaying || S.bargeInBlocked) return true;
        if (cfg.interrupt) return false;
        return aiavatar.isAudioPlaying || S.serverProcessing;     // 割り込みオフ: 話し終わるまでマイクを受け付けない
    };
    aiavatar.getStartMetadata = () => ({ barge_in_enabled: !!cfg.interrupt });

    // ---- 返答の処理 ----
    const trace = [];      // 直近の受信（確認用。M.trace）
    aiavatar.onResponseReceived = (r) => {
        trace.push([Date.now() % 100000, r.type, (r.metadata && r.metadata.recognized_text) || r.voice_text || ""]);
        if (trace.length > 60) trace.shift();
        switch (r.type) {
            case "connected":
                userId = r.user_id || userId;
                try { localStorage.setItem("userId", userId); } catch (e) { /* ignore */ }
                break;
            case "accepted":
                S.serverProcessing = true;
                if (r.metadata && r.metadata.block_barge_in) S.bargeInBlocked = true;
                break;
            case "start": {
                const rec = r.metadata && r.metadata.recognized_text;
                if (rec && !rec.startsWith("$")) {          // こちらの声を聞き取れた: 吹き出しに出し、ここからは返答のターン
                    S.turnActive = true;
                    showBubble(rec, "user", true);
                    aiText = "";
                    if (cfg.talk === "hotkey") setListening(false);
                }
                break;
            }
            case "info":
                if (r.metadata && r.metadata.partial_request_text && !r.metadata.partial_request_text.startsWith("$")) showBubble(r.metadata.partial_request_text, "user", true);
                break;
            case "chunk":
                if (r.voice_text) { aiText += r.voice_text; showBubble(aiText, "ai", true); }
                break;
            case "final":
                S.serverProcessing = false;
                if (r.voice_text) {
                    aiText = r.voice_text;
                    showBubble(aiText, "ai", true);
                    if (!S.turnActive) call("notify_hidden", "koemado", clean(aiText).slice(0, 120));   // こちらから話しかけていない返答（隠れているときだけ Windows の通知になる）
                }
                S.turnActive = false;
                scheduleHide();
                break;
            case "canceled": case "error":
                S.serverProcessing = false; S.turnActive = false;
                if (r.type === "error" && r.voice_text) showBubble(r.voice_text, "info");
                else scheduleHide();
                break;
            case "tool_call": {
                const tc = r.metadata && r.metadata.tool_call;
                if (tc && !tc.result) showBubble(TOOL_LABELS[tc.name] || `ツールを使っています: ${tc.name}`, "info", true);
                break;
            }
            case "stop":
                aiText = "";
                break;
        }
        if (r.text != null && String(r.text).indexOf("[operation:hangup]") >= 0) { /* 会話は切らない（キャラは常駐する） */ }
    };

    // ---- 接続（切れたら自動でつなぎ直す）----
    async function connect() {
        if (aiavatar.ws && (aiavatar.ws.readyState === WebSocket.OPEN || aiavatar.ws.readyState === WebSocket.CONNECTING)) return;
        try {
            const p = aiavatar.startListening(sessionId, userId);
            const ws = aiavatar.ws;
            if (ws) ws.addEventListener("close", (e) => {
                call("log", `WebSocket が閉じました（code=${e.code}）`);
                if (S.turnActive) { S.turnActive = false; S.serverProcessing = false; showBubble("接続が切れました。つなぎ直しています。もう一度話しかけてください。", "info"); }
            });
            await p;
            S.started = true; applyMic();
        } catch (e) { console.warn("connect failed", e); }
    }
    await connect();
    setInterval(connect, 4000);

    // ---- 送る ----
    function send(text, imageDataUrl) {
        S.turnActive = true;
        const ok = aiavatar.chat(sessionId, userId, text, imageDataUrl);
        if (ok === false) { S.turnActive = false; showBubble("まだつながっていません。少し待ってからもう一度どうぞ。", "info"); connect(); return false; }
        showBubble(text, "user", true);
        return true;
    }

    // ---- 文字で話す ----
    const inputBox = $("inputBox"), textIn = $("textIn");
    function openInput() { inputBox.hidden = false; textIn.value = ""; call("focus_window"); setTimeout(() => textIn.focus(), 50); }
    function closeInput() { inputBox.hidden = true; textIn.blur(); }
    inputBox.addEventListener("submit", (e) => { e.preventDefault(); const t = textIn.value.trim(); if (t) { send(t); } closeInput(); });
    textIn.addEventListener("keydown", (e) => { if (e.key === "Escape") closeInput(); });

    // ---- キャラを押している間だけ聞く / ドラッグで移動 ----
    let down = null, holdTimer = null;
    stage.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        down = { x: e.clientX, y: e.clientY, moved: false, holding: false };     // 移動の判定は、画面の座標ではなくページの座標で（画面の座標は、最初の 1 回がずれることがある）
        try { stage.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
        if (cfg.talk === "hold") holdTimer = setTimeout(() => { if (down && !down.moved) { down.holding = true; setListening(true); } }, 150);
    });
    stage.addEventListener("pointermove", async (e) => {
        if (!down || down.moved) return;
        if (Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6) return;
        down.moved = true;                       // 動かした = 移動（聞くのはやめる）
        clearTimeout(holdTimer);
        if (down.holding) setListening(false);
        if (!py) { down = null; return; }
        stage.classList.add("grabbing");
        try { stage.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
        await call("drag");                      // OS の移動操作。終わるまで戻らない
        stage.classList.remove("grabbing");
        down = null;
    });
    const endPress = () => {
        clearTimeout(holdTimer);
        if (down && down.holding) setListening(false);   // 離したら閉じる（そのあと無音が送られ、話の区切りになる）
        down = null;
    };
    const ptrace = [];      // 直近のポインター操作（確認用。mascot.ptrace）
    for (const t of ["pointerdown", "pointerup", "pointercancel", "lostpointercapture", "gotpointercapture"]) {
        stage.addEventListener(t, (e) => { ptrace.push([Date.now() % 100000, t, e.button, e.buttons]); if (ptrace.length > 60) ptrace.shift(); });
    }
    stage.addEventListener("pointerup", endPress);
    stage.addEventListener("pointercancel", endPress);
    stage.addEventListener("contextmenu", (e) => { e.preventDefault(); endPress(); call("menu"); });
    document.addEventListener("contextmenu", (e) => e.preventDefault());
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !inputBox.hidden) closeInput(); });

    // ---- クリックできる領域（キャラの形・吹き出し・入力欄）を mascot.py に知らせる ----
    let stageRects = null, stageSig = "", lastRegions = "";
    function computeStageRects() {
        const sr = stage.getBoundingClientRect();
        const full = [[Math.floor(sr.left), Math.floor(sr.top), Math.ceil(sr.width), Math.ceil(sr.height)]];
        try {
            const COLS = 48, cell = sr.width / COLS, ROWS = Math.max(1, Math.round(sr.height / cell));
            const c = document.createElement("canvas");
            c.width = COLS; c.height = ROWS;
            const g = c.getContext("2d", { willReadFrequently: true });
            g.drawImage(faceImage, 0, 0, COLS, ROWS);
            const d = g.getImageData(0, 0, COLS, ROWS).data;
            const solid = (r, k) => r >= 0 && r < ROWS && k >= 0 && k < COLS && d[(r * COLS + k) * 4 + 3] > 16;
            const out = [];
            for (let r = 0; r < ROWS; r++) {
                let start = -1;
                for (let k = 0; k <= COLS; k++) {
                    let on = false;     // 1 マスぶん広げる（動いたときに、はみ出して切れないように）
                    if (k < COLS) for (let dr = -1; dr <= 1 && !on; dr++) for (let dk = -1; dk <= 1 && !on; dk++) on = solid(r + dr, k + dk);
                    if (on && start < 0) start = k;
                    if (!on && start >= 0) {
                        out.push([Math.floor(sr.left + start * cell), Math.floor(sr.top + r * (sr.height / ROWS)), Math.ceil((k - start) * cell), Math.ceil(sr.height / ROWS) + 1]);
                        start = -1;
                    }
                }
            }
            return out.length ? out : full;
        } catch (e) { return full; }
    }
    function publishRegions() {
        if (!py) return;
        const sr = stage.getBoundingClientRect();
        const sig = `${Math.round(sr.width)}x${Math.round(sr.height)}@${Math.round(sr.left)},${Math.round(sr.top)}:${faceImage.getAttribute("src")}:${faceImage.complete}`;
        if (sig !== stageSig && faceImage.complete && faceImage.naturalWidth) { stageSig = sig; stageRects = computeStageRects(); }
        const regs = (stageRects || []).slice();
        for (const el of [bubble, inputBox]) {
            if (el.hidden) continue;
            const r = el.getBoundingClientRect();
            regs.push([Math.floor(r.left) - 4, Math.floor(r.top) - 4, Math.ceil(r.width) + 8, Math.ceil(r.height) + 8]);
        }
        const js = JSON.stringify(regs);
        if (js !== lastRegions) { lastRegions = js; call("set_regions", js); }
    }
    setInterval(publishRegions, 200);

    // ---- 画像のドラッグ＆ドロップ ----
    let visionOn = false;
    async function refreshVision() {
        try { visionOn = !!(await (await fetch("/settings/api/vision", { cache: "no-store" })).json()).enabled; } catch (e) { visionOn = false; }
    }
    await refreshVision();
    function shrink(source, w, h) {            // 長辺 1024px の JPEG に縮小（会話画面の画像パネルと同じ）
        const k = Math.min(1, 1024 / Math.max(w, h));
        const c = document.createElement("canvas");
        c.width = Math.round(w * k); c.height = Math.round(h * k);
        c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
        return c.toDataURL("image/jpeg", 0.85);
    }
    function sendImageFile(file) {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const data = shrink(img, img.naturalWidth, img.naturalHeight);
            URL.revokeObjectURL(url);
            send("この画像について教えて。", data);
        };
        img.onerror = () => { URL.revokeObjectURL(url); showBubble("この画像は読み込めませんでした。", "info"); };
        img.src = url;
    }
    const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
    document.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); document.body.classList.add("dropping"); } });
    document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dropping"); });
    document.addEventListener("drop", async (e) => {
        e.preventDefault();
        document.body.classList.remove("dropping");
        const file = Array.from((e.dataTransfer && e.dataTransfer.files) || []).find((f) => f.type.startsWith("image/"));
        if (!file) { showBubble("画像ファイルをドロップしてください。", "info"); return; }
        await refreshVision();
        if (!visionOn) { showBubble("画像を見るには、設定画面（LLM タブ）で「画像認識」をオンにしてください。", "info"); return; }
        sendImageFile(file);
    });

    // ---- mascot.py から呼ばれる ----
    const M = {
        state: () => ({ listening: S.listening, muted: S.muted, volume: aiavatar.volume, talk: cfg.talk, size: cfg.size, connected: !!(aiavatar.ws && aiavatar.ws.readyState === WebSocket.OPEN),
                        speaking: aiavatar.isAudioPlaying, mouth: mouthImage.dataset.shape || "", face: aiavatar.getCurrentFace(), bubble: bubble.hidden ? "" : bubbleText.textContent }),
        cmd(name, arg) {
            switch (name) {
                case "text": openInput(); break;
                case "mute": S.muted = !S.muted; if (S.muted) S.listening = false; applyMic(); break;
                case "volume": aiavatar.setVolume(Number(arg)); call("set_pref", "volume", String(aiavatar.volume)); break;
            }
        },
        hotkey() {
            if (cfg.talk !== "hotkey") return;
            setListening(!S.listening);
        },
        async reload() {
            let next = null;
            try { next = JSON.parse(await call("get_config")); } catch (e) { /* 読めなければ変えない */ }
            if (!next) return;
            const talkChanged = next.talk !== cfg.talk;
            const intChanged = !!next.interrupt !== !!cfg.interrupt;
            cfg = Object.assign(cfg, next);
            if (talkChanged) { S.listening = false; clearTimeout(listenTimer); }
            if (intChanged) aiavatar.sendConfig(sessionId, { barge_in_enabled: !!cfg.interrupt });
            if (Math.abs(next.volume - aiavatar.volume) > 0.001) aiavatar.setVolume(next.volume);
            applyMic();
            layout();
        },
        say: (t) => send(t),
        client: aiavatar,
        S,
        trace,
        ptrace,
        showBubble,
    };
    window.mascot = M;
    applyMic();
    call("ready");
})();
