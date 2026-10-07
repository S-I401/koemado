/* koemado のアバター: キャラクターごとの素材（characters/<id>/）に合わせて動く。
 *  - 口の差分（mouth/closed|half|open|e|u.png。顔画像と同じキャンバスサイズの透過 PNG）があれば、音声に合わせて重ねて切り替える（MFCC。母音が分からないときは音量で closed / half / open）
 *  - 無ければ、音量に合わせた軽いアニメーション（話している間の小さな上下の揺れと拡大縮小、待機中の呼吸のようなゆっくりした動き）。シルエットも同じ
 *  - 表情が変わったときは、顔画像を短くフェードさせる。eyes_closed.png があればまばたきに使う
 *  - 動きの量は設定画面の「アバターの動き」（なし / 控えめ / 大きめ）。ブラウザの「視覚効果を減らす」では止まる
 * image-avatar.js（AIAvatarKit）の ImageAvatar を拡張する。index.html の new ImageAvatar を new KoemadoImageAvatar に置き換えて使う。 */
(() => {
    const reduceMq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false, addEventListener() {} };
    const AMP = { none: 0, subtle: 1, large: 2.2 };

    class KoemadoImageAvatar extends ImageAvatar {
        constructor(options) {
            super({ ...options, mouthPathTemplate: "../character-mouth/{mouth}" });
            this.info = { motion: "subtle", mouth: [], expressions: [], eyes_closed: false, has_image: false };
            this.rms = 0;
            this.env = 0;
            this.lastAudioAt = 0;
            this.layer = null;
            this.aiavatarRef = null;
            this.fadeCount = 0;
            window.koemadoAvatar = this;
        }

        get amp() {
            return reduceMq.matches ? 0 : (AMP[this.info.motion] ?? 1);
        }

        get hasMouth() {
            return this.info.mouth.length > 0;
        }

        async bind(aiavatar) {
            this.aiavatarRef = aiavatar;
            try {
                const r = await fetch("../settings/api/avatar", { cache: "no-store" });
                if (r.ok) this.info = { ...this.info, ...(await r.json()) };
            } catch (e) {
                console.warn("KoemadoImageAvatar: failed to load avatar info", e);
            }
            this.lipsyncEnabled = this.hasMouth;          // 口の差分が無いキャラは、MFCC を使わず音量だけを見る
            this.blinkEnabled = !!this.info.eyes_closed;  // まばたきは eyes_closed.png があるときだけ
            this.setupLayer();
            this.observeFace();
            try {
                await super.bind(aiavatar);
            } catch (e) {
                console.warn("KoemadoImageAvatar: lip-sync engine unavailable, using volume only", e);
                aiavatar.onResetFace = () => this.reset();
                aiavatar.onPlaybackEnd = () => this.stop();
            }
            const prevAudio = aiavatar.onPlaybackAudio;
            aiavatar.onPlaybackAudio = (audio) => {
                this.measureLevel(audio);
                if (prevAudio) {
                    try {
                        prevAudio(audio);
                    } catch (e) {
                        this.applyMouthShape(this.volumeShape());
                    }
                } else if (this.hasMouth) {
                    this.applyMouthShape(this.volumeShape());
                }
            };
            const prevEnd = aiavatar.onPlaybackEnd;
            aiavatar.onPlaybackEnd = () => {
                this.lastAudioAt = 0;
                if (prevEnd) prevEnd();
            };
            reduceMq.addEventListener?.("change", () => { if (this.layer) this.layer.style.transform = ""; });
            requestAnimationFrame((t) => this.tick(t));
        }

        // ---- 口 ----
        async preloadMouths() {
            try {
                await Promise.all(this.info.mouth.filter((m) => m !== "closed").concat(this.info.mouth.includes("closed") ? ["closed"] : []).map((m) => (
                    this.fetchMouth(m).catch((e) => console.warn(`KoemadoImageAvatar: failed to load mouth "${m}"`, e))
                )));
            } finally {
                this.mouthPreloaded = true;
                this.reset();
            }
        }

        measureLevel({ pcm, sampleRate, samplePosition }) {
            const n = Math.max(1, Math.floor(sampleRate * 0.04));
            const start = Math.min(pcm.length - 1, samplePosition || 0);
            const end = Math.min(pcm.length, start + n);
            let sum = 0;
            for (let i = start; i < end; i++) sum += pcm[i] * pcm[i];
            this.rms = end > start ? Math.sqrt(sum / (end - start)) : 0;
            this.lastAudioAt = performance.now();
        }

        volumeShape() {
            return this.rms < 0.015 ? "closed" : this.rms < 0.06 ? "half" : "open";
        }

        applyLipSyncResult(result) {
            // 母音を推定できないとき（無音・不明）は、音量で closed / half / open を決める
            const shape = result && result.mainViseme ? this.selectMouthShape(result) : this.volumeShape();
            this.applyMouthShape(shape);
        }

        applyMouthShape(shape) {
            const avail = this.info.mouth;
            const prefer = { e: ["e", "half", "open"], u: ["u", "half", "open"], half: ["half", "open"], open: ["open", "half"], closed: ["closed"] }[shape] || [];
            const pick = prefer.find((m) => avail.includes(m));
            if (!pick) {
                this.reset();
                return;
            }
            const url = this.mouthCache.get(pick);
            if (!url) return;
            this.showMouth(pick, url);
        }

        showMouth(name, url) {
            this.mouthImage.dataset.shape = name;
            if (this.mouthImage.getAttribute("src") !== url) this.mouthImage.src = url;
            this.mouthImage.style.display = "block";
        }

        reset() {
            const url = this.mouthCache && this.mouthCache.get("closed");
            if (this.info && this.info.mouth.includes("closed") && url) {
                this.showMouth("closed", url);
                return;
            }
            this.mouthImage.dataset.shape = "";
            this.mouthImage.removeAttribute("src");
            this.mouthImage.style.display = "none";
        }

        // ---- 表情のフェード ----
        observeFace() {
            this.lastFaceSrc = this.faceImage.getAttribute("src");
            new MutationObserver(() => {
                const src = this.faceImage.getAttribute("src");
                const prev = this.lastFaceSrc;
                this.lastFaceSrc = src;
                if (src === prev || this.amp === 0) return;
                if (/eyes_closed/.test(src || "") || /eyes_closed/.test(prev || "")) return;   // まばたきはフェードしない
                this.fadeCount++;
                this.faceImage.animate([{ opacity: 0.35 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
            }).observe(this.faceImage, { attributes: true, attributeFilter: ["src"] });
        }

        // ---- 軽いアニメーション（口の差分が無いキャラ）----
        setupLayer() {
            const stage = this.faceImage.parentElement;
            const layer = document.createElement("div");
            layer.id = "motionLayer";
            layer.style.cssText = "position:absolute;inset:0;transform-origin:50% 100%;will-change:transform;";
            stage.insertBefore(layer, this.faceImage);
            layer.appendChild(this.faceImage);
            layer.appendChild(this.mouthImage);
            this.layer = layer;
        }

        applyTransform(ty, scale, rotate) {
            if (this.layer) this.layer.style.transform = `translateY(${ty.toFixed(2)}px) scale(${scale.toFixed(4)}) rotate(${rotate.toFixed(3)}deg)`;
        }

        tick(ts) {
            requestAnimationFrame((t) => this.tick(t));
            const amp = this.amp;
            if (this.hasMouth || amp === 0) {
                if (this.layer && this.layer.style.transform) this.layer.style.transform = "";
                return;
            }
            const t = ts / 1000;
            const speaking = (this.aiavatarRef && this.aiavatarRef.isAudioPlaying) || performance.now() - this.lastAudioAt < 300;
            const target = speaking ? Math.min(1, this.rms * 9) : 0;
            this.env += (target - this.env) * 0.35;
            const breath = Math.sin(t * 2 * Math.PI / 5);                 // 待機中: 5 秒周期の呼吸
            const bounce = Math.sin(t * 14) * this.env;                     // 話している間: 音量に合わせた小さな上下
            const scale = 1 + amp * (0.006 + 0.005 * breath) + amp * 0.018 * this.env;
            const ty = amp * 0.9 * breath - amp * 2.4 * this.env + amp * 1.2 * bounce;
            const rot = amp * 0.5 * Math.sin(t * 6.3) * this.env;
            this.applyTransform(ty, scale, rot);
        }
    }

    window.KoemadoImageAvatar = KoemadoImageAvatar;
})();
