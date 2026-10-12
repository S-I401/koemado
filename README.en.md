English | [日本語](README.md)

# koemado

A **one-click, local voice-chat avatar for Windows.**
You talk to it with a microphone (or type), a local LLM thinks up a reply, and Irodori-TTS reads it aloud.
Nothing is sent to the cloud, unless you choose an external inference server yourself.

```
Browser / desktop character (mic) ─ AIAvatarKit ─ STT: speech.cpp (Vulkan; GPU or CPU)
                    │
                    ├─ LLM: llama.cpp llama-server (GGUF)   * can be replaced with an external inference server
                    └─ TTS: Irodori-TTS v4.1 Small (speech.cpp, Vulkan; GGUF Q8_0; the Python version is a fallback)
```

> **A rule shared by every character.**
> No character responds to sexual content, or to sexual topics involving minors.
> The rule lives in `characters/_common.md` (the part shared by all characters), so a character's settings cannot remove it.
> A character's age is optional.
> However, Strata's "refusal suppression (ESP)" can be turned on only while the character in use has an age of 18 or older (it cannot be used when the age is under 18 or not set).
> The bundled sample character, Mio, has no age set, so ESP cannot be used with her.
> Her voice was made with Voice Design (not the voice of a real person).
> The sample character's images are fetched at setup time from the examples of AIAvatarKit (uezo/aiavatarkit, Apache License 2.0).
> They are not included in the koemado repository.

## Language note: the app is Japanese-first

- The app's UI (conversation screen, settings screen, admin console) and the character prompts are written in **Japanese**.
- **The TTS (Irodori) only speaks Japanese.**
  The LLM can understand and answer in English, but the voice can only read Japanese text, so English replies cannot be spoken properly.
- If you do not read Japanese, you can still set everything up with this document, `setup.bat`'s prompts (also Japanese, but short), and your browser's translation feature.
  The settings screen shows Japanese labels; this README gives the English meaning of the ones you need.
- The `config/`, `characters/` and `skills/` files are plain text, so you can edit them directly if you prefer.

## Requirements

| Item | Details |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| GPU | **An NVIDIA GPU is required** (RTX 20 series or newer, 6 GB VRAM or more). Driver **570 or newer** (needed by PyTorch CUDA 12.8) |
| RAM | 16 GB or more (32 GB or more for the standard profile) |
| Vulkan | `vulkan-1.dll`, installed by the GPU driver (used by speech.cpp for STT and TTS). Without it, STT uses faster-whisper (CPU) and TTS uses the Python Irodori-TTS-Server (PyTorch; about 5 GB more) (see "Speech recognition (STT)" and "Speech synthesis (TTS)") |
| Free disk space | About 14 to 29 GB depending on the profile (see the table below; a PC without Vulkan needs about 6 GB more for the Python TTS), on the drive you install to |
| Network | Setup downloads from GitHub / Hugging Face / PyPI (about 8 to 24 GB in total) |

On a PC without an NVIDIA GPU, TTS takes more than ten seconds per sentence and a conversation is not practical, so setup aborts.

## Installation

1. Download this repository (unzip it, or `git clone`). Put it on a **drive with plenty of free space** (the install location defaults to this folder).
2. Double-click `setup.bat`.
   - It inspects the PC (GPU, VRAM, RAM, free disk space, ports in use), picks a matching profile and shows the plan.
     Press **Enter** to start installing.
   - After the profile is chosen, you can **choose the LLM model** (see "Choosing the model" below). If you choose nothing, the profile's default model is installed.
   - If uv (a Python manager) is missing, it is installed into the repository's `tools\` folder.
     Nothing is installed system-wide.
   - If it stops halfway, run `setup.bat` again and it resumes (finished downloads are not fetched again).
3. When setup is done, double-click `start.bat` (or the desktop shortcut).
   **No black console window appears.**
   By default a character appears on your desktop and a "starting..." bubble shows progress (see "Desktop character").
   The services start in the order LLM, TTS, STT, AIAvatarKit, and when everything is ready the character can talk.
   If the display mode is "Browser", your browser opens the "starting..." page (the admin console) as before and switches to the conversation screen automatically.
4. To stop: the character's right-click menu, then Quit; the tray icon of koemado, then Exit; the Exit button in the admin console; or `stop.bat`.

### Confirmation before installing

`setup.bat` prints the detection result and a table of what will be installed (download size and disk use per item, totals, free space on the target drive before and after, and estimated time at 100 Mbps / 1 Gbps).
It starts **only if you answer `y` or `yes` to the prompt `この内容でインストールしますか？ [y/N]:` ("Install with this plan?")**.
Anything else (an empty Enter, `n`, ...) exits without changing anything.
`--yes` skips the prompt but always prints the table.
When resuming, it shows the remaining amount, minus what is already downloaded.

### Using Strata (recommended for PCs with 12 GB+ VRAM and 48 GB+ RAM)

If the PC has **at least 12 GB VRAM and at least 48 GB RAM**, and the disk has room for Strata (about 80 GB), setup offers [Strata](https://github.com/Niko1221/Strata) (Qwen3.8-Flash-Next) regardless of the profile (high / mid / low).
Replies are much better than with the bundled LLM (Gemma 4 26B-A4B for mid).
With 12 to 19 GB of VRAM the expert cache is smaller and speed drops (measured in Strata's README: RTX 5070 12 GB with 64 GB RAM gives about 79 tokens/s with IQ2_XS and about 53 tokens/s with IQ3_S).

- Strata's requirements: NVIDIA (or an AMD GPU supported by Strata, but this project itself is NVIDIA-only), 12 GB+ VRAM, 48 GB+ RAM (with 32 GB RAM only the Coder edition fits, which is weak at Japanese chat, so it is not offered), about 80 GB of disk (about 70 GB download).
- Caveats: it is separate software (licensed by Strata). It handles one request at a time. It makes the PC sluggish for 1 to 3 minutes at startup.
- Choices: **[1]** install Strata and use it (fetched into `<install dir>\strata`, and Strata's installer opens in a separate window; model selection and download happen there) / **[2]** already installed (enter its URL) / **[3]** continue with the bundled LLM (shown with the selected profile name).
- `--yes` never installs the roughly 70 GB on its own (it continues with the selected bundled profile and only prints the guidance). `--llm strata` chooses [1], and `--llm external --llm-url http://127.0.0.1:8080/v1` chooses [2].
- After [1], `start.bat` starts Strata first and waits up to 10 minutes for `/v1/models` to respond (progress is shown in the admin console). The port is read from Strata's `strata-*.json`. `stop.bat` only stops the Strata that `start.bat` started.
- So that Strata does not fill the VRAM with its expert cache, `--vram-reserve-mib 3500` (headroom for TTS) is written **only into the Strata settings of this install**. LLM-to-TTS serialization is on, `reasoning_effort=none`, temperature 0.8.
- The Strata commit is pinned (`82f46a8`). Update Strata yourself with `strata\UPDATE.bat`.
- Strata's installer (START-HERE.bat) may install Python 3.12 into your user profile on PCs without Python (that is Strata's own behavior).

### Refusal suppression for Strata (ESP) and image recognition

Switch these in the LLM tab of the settings screen (when using Strata) or with `configure.bat`.

- **Refusal suppression (Strata's "experimental speed projection")**: off by default. **Despite its name it is not a speed-up.** It uses a control vector shipped with Strata to remove the "refusal direction", so **the model almost never declines a request (its safety behavior is removed)**. It costs only about 0.2 to 0.4 % per token. You are responsible for the output. The vector is specific to Qwen3.8-Flash-Next (it does not work with Swift 1.5 or the Coder edition) and is under the Qwen Community License 1.0. A confirmation dialog appears when you turn it on. ESP can be turned on only while the character in use has an age of 18 or older; otherwise the settings screen and configure.bat show the reason and keep it off. If ESP is on and you try to switch to a character under 18 or with no age set, you are asked whether to turn ESP off and switch, or to cancel the switch. The shared rule against sexual content applies whether or not ESP is on.
  - **Strata started by `start.bat` (`strata\`)**: when on, a `strata-<model>-esp.json` (same format as Strata's setup; the log is named `-esp.log`) is created and used. When off, the original config is used. Strata must be restarted after a change (the screen shows a restart button).
  - **A Strata you started yourself**: its launch arguments cannot be changed, so the app only sends `experimental_speed_projection` (`true` when on, `false` when off) with each request. It only has an effect on a Strata that was started with the vector loaded. Whether it is effective is not exposed in `/props`, so it cannot be detected.
- **Image recognition (vision)**: on the Strata path of `setup.bat` you can choose "GPU (recommended; 0.1 to 0.5 s per image, about 1.4 GB VRAM) / CPU (10 to 30 s per image) / off (default)" with `--vision gpu|cpu|no` (also passed to Strata's installer). With GPU, 4900 MiB (the 3500 MiB TTS headroom plus 1400 MiB for the encoder) is set as `--vram-reserve-mib`, because Strata's setup writes only 700 for both GPU and CPU and does not add the encoder's share automatically.
  - When image recognition is on in the settings screen, an "send an image" panel (file picker, paste, camera) appears on the conversation screen. Images stay on this PC and are **not saved in the conversation history (SQLite)** unless you turn that on in the settings screen.
  - With an external server, it works only if the server has image recognition enabled. The connection test sends a small image to check.
- Future work: the bundled LLMs (Gemma 4 / Qwen3.6) could also handle images with an mmproj, but that is out of scope for now.

### `setup.bat` options

```
setup.bat --dry-run                       inspect and print the plan only (installs nothing)
setup.bat --profile mid --yes             choose the profile and proceed without asking
setup.bat --install-dir D:\aiavatar       choose the install location (default: this folder)
setup.bat --llm external --llm-url http://127.0.0.1:1234/v1 --llm-model MODEL
                                          use an external inference server (no llama.cpp or model installed)
setup.bat --llm strata                    install and use Strata (for high-end PCs; about 70 GB)
setup.bat --model unsloth/Qwen3-8B-GGUF:Qwen3-8B-Q4_K_M.gguf
                                          choose the bundled LLM's model (repository:file; models outside the range are refused)
setup.bat --model <repo>:<file> --hf-token-env   use the HF_TOKEN environment variable (never put a token on the command line)
setup.bat --stt auto|speechcpp|faster-whisper|both   Speech recognition to install (default auto: speech.cpp when Vulkan works, otherwise faster-whisper)
setup.bat --tts auto|speechcpp|python|both   Speech synthesis to install (default auto: speech.cpp's Irodori-TTS when Vulkan works, otherwise the Python version)
setup.bat --dry-run --simulate "vram=8,ram=32,disk=60"   judge as if on another PC (for testing)
```

If the environment variable `HF_TOKEN` is set, or a Hugging Face token is already saved in the settings, `setup.bat` uses it for the model download.

### Choosing the model (during setup)

After the profile is chosen, `setup.bat` asks how to choose the LLM model (with `--yes` it does not ask and uses the default).

- **[1] Recommended (default)**: the profile's default model.
- **[2] Pick from the Hugging Face list** (a token is required): the token is typed without echo (`getpass`). It is verified with `whoami-v2`; if valid, the user name is shown and you are asked whether to save it to `config/settings.json` (shared with the settings screen). If the `HF_TOKEN` environment variable exists, it asks whether to use it. Then you choose a search term, a candidate (sorted by downloads), a repository, and a GGUF file from a list showing sizes, whether each is within range, and the reason and cautions for those outside. Models outside the range cannot be chosen. For a gated model you have not accepted, the URL of the terms page is shown. An invalid token gives an error and lets you type it again.
- **[3] Type the repository and file name** (a token is optional): the size is fetched and checked against the range.
- The chosen model is reflected in the confirmation table (download size, disk use, time estimate).
- Non-interactively, use `--model <repo>:<file>`. If a token is needed, set the `HF_TOKEN` environment variable and add `--hf-token-env`. There is no option to pass a token directly on the command line (it would stay in history and process lists).
- After setup you can still change the model in the settings screen (LLM tab).

### Downloads use `hf download`

All model downloads from Hugging Face (GGUF, Irodori, Whisper and so on) use `hf download` (the `huggingface_hub` CLI, with `hf_xet`). It is installed with pinned versions into `tools\hf-venv` (`setup.bat` and the settings screen use the same `hf.exe`).

- The token is passed to the child process only through the `HF_TOKEN` environment variable, never as a command-line argument (arguments show up in process lists).
- A split GGUF (`-0000N-of-`) is fetched with a single `hf download` covering all parts. If interrupted, running it again resumes where it stopped. In the settings screen, Cancel stops the process.
- **Fallback when slow**: if the 60-second average speed falls below 1 MB/s, `hf download` is stopped and the download switches to 16 parallel Range requests (for environments where the Xet/CDN connection is throttled to a single stream). The switch is shown in the log and on the screen. The file is verified against the SHA256 from HF (the LFS oid). The token is attached only to huggingface.co. The thresholds can be changed with the environment variables `KOEMADO_HF_SLOW_BPS` (bytes per second) and `KOEMADO_HF_SLOW_WINDOW_SEC` (seconds), for testing.

## Profiles

The LLM runs on llama.cpp's `llama-server`. A profile is chosen automatically from the PC's specs (override with `--profile`).
If there is not enough free disk space, it drops one level.

| Profile | Requirement (VRAM / RAM) | LLM (GGUF) | Model size | Free space needed (approx.) |
|---|---|---|---|---|
| high | 20 GB+ / 48 GB+ | Qwen3.6-35B-A3B (UD-Q4_K_M) | 20.6 GiB | about 35 GB |
| mid (standard) | 8 GB+ / 32 GB+ | Gemma 4 26B-A4B (QAT UD-Q4_K_XL) | 13.3 GiB | about 27 GB |
| low | 6 GB+ / 16 GB+ | Gemma 4 E4B (QAT q4_0) | 4.8 GiB | about 19 GB |

- **mid** keeps most of the MoE experts' weights on the CPU side so that VRAM holds about 3.6 GB for the LLM plus about 2.7 GB (peak) for TTS (on an 8 GB GPU, leaving room for the Windows desktop).
- Disk breakdown: llama.cpp about 1.2 GB, the TTS runtime (PyTorch CUDA) about 5.2 GB, the TTS model (Irodori GGUF) about 1.2 GB, STT (speech.cpp about 0.1 GB plus parakeet about 0.7 GB), other environments about 1 GB (on a PC without Vulkan, STT is the faster-whisper environment and model, about 0.8 GB) (the download cache is removed at the end of setup).
- To swap a model, edit the table in `profiles.py`. If you register a Hugging Face token, you can also pick any GGUF from a list in the settings screen, within the range of your profile (see "Model browser" below).

### Pinned versions

llama.cpp `b11443` (CUDA 12 build) / Irodori-TTS v4.1 Small GGUF Q8_0 (`sakasegawa/Irodori-TTS-v4.1-Small-GGUF` `a8d7c01`; the MF one is `...-MF-GGUF` `205ab07`) / Python version (fallback): Irodori-TTS-Server `61012c7` (follows `uv.lock`) and Irodori-TTS v4.1 Small int8 (`Aratako/Irodori-TTS-v4.1-Small-Quantized`) / aiavatar 0.9.1 / fastmcp 4.0.11 / ddgs 9.16.0 / speech.cpp v0.8.4 (`speech-0.8.4-windows-x64-vulkan.zip`, SHA-256 `c35e75e6...`) / parakeet-tdt_ctc-0.6B-ja Q8_0 (`sakasegawa/parakeet-tdt_ctc-0.6b-ja-GGUF` `f7cb538`) / Qwen3-ASR 0.6B and 1.7B Q8_0 (`sakasegawa/Qwen3-ASR-*-GGUF`) / faster-whisper 1.2.1 (small, CPU int8; fallback for PCs without Vulkan) / pystray 0.19.5 / pillow 12.3.0 / PySide6 6.11.2 (Essentials / Addons; Qt WebEngine) / shiboken6 6.11.2. Dependency versions are pinned in `app/requirements.txt` and `stt/requirements.txt`.

## Speech recognition (STT)

Speech to text uses `speech serve` from [speech.cpp](https://github.com/nyosegawa/speech.cpp) (MIT) by default.
It needs no Python and no CUDA: it uses your GPU through **Vulkan** (`vulkan-1.dll`, which the NVIDIA / AMD / Intel GPU driver installs), and it also runs on the CPU.
The old faster-whisper (Whisper small, CPU) stays as a fallback for PCs where Vulkan is not available.

- Install: `setup.bat` unpacks the official release `speech-0.8.4-windows-x64-vulkan.zip` (about 30 MB) into `stt\speechcpp\`.
  The official `install.ps1` is not used because it edits PATH.
  The zip is unpacked only when its SHA-256 matches **both** the release's `SHA256SUMS` and the value pinned in `stt_engine.py`.
  The recognition model (GGUF) is fetched with `hf download` into `models\stt\` at a pinned commit and its SHA-256 is verified too (on a mismatch the file is deleted).
- Start: the supervisor runs `speech serve <model> --host 127.0.0.1 --port <STT port> --device <GPU or CPU> --language ja` without a console window.
  Readiness is checked with `/v1/models`.
  Its output goes to the log of the admin console (`logs\stt.log`).
  The first start can be slow while the GPU driver compiles shaders, so the wait limit is 600 seconds (in practice startup took a few seconds, and only the first recognition sometimes took 5 to 10 seconds).
- Conversation: AIAvatarKit's `OpenAISpeechRecognizer` cannot send `prompt`, so `app/run.py` subclasses it to send one (see "Frequently used words" below).

### Models

| Setting name | Model | Size | CER (published) | Notes | License |
|---|---|---|---|---|---|
| `parakeet-ja` (default) | [parakeet-tdt_ctc-0.6b-ja](https://huggingface.co/sakasegawa/parakeet-tdt_ctc-0.6b-ja-GGUF) (`parakeet-tdt_ctc-0.6B-ja-Q8_0.gguf`) | 661 MB | 7.9% | Fast. Tends to write in kana. No `prompt` | CC-BY-4.0 (c) NVIDIA |
| `qwen3-asr-0.6b` | [Qwen3-ASR 0.6B](https://huggingface.co/sakasegawa/Qwen3-ASR-0.6B-GGUF) (`Qwen3-ASR-0.6B-Q8_0.gguf`) | 842 MB | 11.9% | Light, writes kanji. Accepts hints for proper nouns | Apache-2.0 |
| `qwen3-asr-1.7b` | [Qwen3-ASR 1.7B](https://huggingface.co/sakasegawa/Qwen3-ASR-1.7B-GGUF) (`Qwen3-ASR-1.7B-Q8_0.gguf`) | 2.18 GB | 9.5% | Most accurate. Accepts hints. Uses 2.3 GB of VRAM | Apache-2.0 |
| faster-whisper small | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | about 0.5 GB | - | The old engine. CPU. Fallback when Vulkan is missing | MIT |

CER is the value speech.cpp publishes for the Common Voice 8.0 Japanese test set (4,483 utterances, Q8_0).
The GGUF files were converted by speech.cpp's author (sakasegawa); the repositories and file names are linked above and the commits are pinned in `stt_engine.py`.

### Comparison

We measured it in this repository.
12 Japanese sentences were synthesized with Irodori (short conversational sentences with Sendai, Mio, Hugging Face, Python and numbers; voices `mio_bright` / `mio_a` / `mio_b` / `mio_c` / `mio_d`), converted to 16 kHz mono, and recognized by each setup (`tools/stt_bench.py`).
The PC is a Ryzen 7 7800X3D (8 threads) with an RTX 4090 (Vulkan).

| Setup | CER | CER\* | Reading CER | Exact | Time per utterance (mean, about 4.5 s of speech) |
|---|---|---|---|---|---|
| faster-whisper small (CPU) | 13.5% | 10.2% | 9.8% | 4 / 12 | 1.1 to 1.2 s |
| speech.cpp parakeet-ja (GPU) | 16.9% | 13.6% | 4.6% | 3 / 12 | 0.03 to 0.05 s |
| speech.cpp parakeet-ja (CPU) | 16.9% | 13.6% | 4.6% | 3 / 12 | 0.3 s |
| speech.cpp qwen3-asr-0.6b (GPU) | 6.0% | 6.0% | 4.3% | 8 / 12 | 0.05 to 0.08 s |
| speech.cpp qwen3-asr-0.6b (CPU) | 6.2% | 6.2% | 5.0% | 8 / 12 | 0.9 s |
| speech.cpp qwen3-asr-1.7b (GPU) | 5.1% | 4.2% | 3.6% | 8 / 12 | 0.09 to 0.13 s |
| speech.cpp qwen3-asr-1.7b (CPU) | 4.0% | 3.1% | 2.9% | 9 / 12 | 1.8 s |
| speech.cpp qwen3-asr-1.7b (GPU, hints "Sendai, Mio, Hugging Face, Python, LLM") | 5.0% | 3.1% | 2.9% | 9 / 12 | 0.14 s |

- **CER**: edit distance of the strings (spaces and punctuation removed, NFKC-normalized) divided by the length of the reference.
  Kanji versus kana spelling differences count as errors.
- **CER\***: the same with kanji numerals turned into Arabic numerals (so "七時" equals "7時").
- **Reading CER**: both texts are converted to hiragana readings first (pykakasi; readings of English words are approximate).
- Times are the range of the two quiet runs.
  In a third run another session was using the CPU and the CPU setups were 1.5 to 3 times slower (faster-whisper small 2.2 s, parakeet-ja on CPU 1.0 s).
  CPU times vary a lot with the load of other applications.
- GPU and CPU CERs of the same model differ a little because arithmetic differences change a few sentences.

**Honest results**:
- **parakeet-ja is not clearly better than faster-whisper small.**
  It is much faster (about 30 times on GPU, about 4 times on CPU) and about twice as accurate by reading (reading CER 4.6% vs 9.8%).
  But it is weak at choosing kanji (it writes kana, such as "みお", "ほんとう", "あした", "ばんごはん"), so its spelling CER is worse than faster-whisper small (16.9% vs 13.5%).
  An LLM can often understand kana, so this may not matter much in conversation (not verified here), but proper nouns are harder to tell apart in kana.
- **Qwen3-ASR was more accurate than both in this test** (CER 4 to 6%).
  The 1.7B model reaches 4.0% even on the CPU (about 1.8 s per utterance), in about the same time as faster-whisper small but more accurate.
  With a GPU to spare, the 0.6B model takes under 0.1 s at about 6%.
- The published Common Voice CERs rank them the other way (parakeet 7.9% < Qwen3-ASR 1.7B 9.5%).
  This test is only 12 synthesized sentences (no real human voices, no noisy room), and the difference comes mostly from kanji versus kana spelling.
- **Against the friend's feedback ("recognition is bad"), we recommend qwen3-asr-0.6b or 1.7b rather than the default parakeet-ja.**
  To change the default, edit `DEFAULT_MODEL` in `stt_engine.py` (one line; it can also be changed in the settings screen).

### VRAM and the default device per profile

On the mid setup (simulating 8 GB of VRAM, with the LLM, TTS and STT all running and conversations and recognitions repeated), we measured the per-process dedicated GPU memory ("GPU Process Memory" in Windows) and took the peak.

| STT | LLM | TTS | STT | Peak of the sum | Within 7 GB (7,168 MiB) |
|---|---|---|---|---|---|
| CPU (parakeet) | 3,518 MiB | 2,410 MiB | 0 | 5,929 MiB | yes |
| parakeet-ja (GPU) | 3,518 MiB | 2,424 MiB | 705 MiB | 6,647 MiB | yes |
| qwen3-asr-0.6b (GPU) | 3,518 MiB | 2,408 MiB | 1,041 MiB | 6,967 MiB | yes (small margin) |
| qwen3-asr-1.7b (GPU) | 3,518 MiB | 2,442 MiB | 2,321 MiB | 8,281 MiB | **no (does not fit an 8 GB card)** |

With the device set to "auto" the defaults are:

| Profile | parakeet-ja | qwen3-asr-0.6b | qwen3-asr-1.7b | Reason |
|---|---|---|---|---|
| high | GPU | GPU | GPU | 20 GB or more of VRAM. The margin that the LLM leaves with `--fit-target` is enlarged by the STT's share |
| mid | GPU | GPU | **CPU** | The measurements above. 1.7B does not fit in 8 GB (on CPU it takes about 1.8 s per utterance) |
| low | CPU | CPU | CPU | A 6 GB card is expected to be nearly full with the LLM and TTS (not measured on real hardware). parakeet takes 0.3 s on CPU |
| External LLM | GPU | GPU | GPU | The LLM runs elsewhere, so there is VRAM to spare |
| Strata | GPU | CPU | CPU | Only parakeet fits in the margin (about 1 GB) that Strata leaves for TTS |

### How to switch

- In the **"Speech recognition" tab of the settings screen** (`http://127.0.0.1:8100/settings#stt`) choose the engine (auto / speech.cpp / faster-whisper), the model, the device (auto / GPU / CPU) and frequently used words, then press "Save and apply".
  - If the chosen model is not downloaded yet, the screen shows its file name, size and license and downloads it from Hugging Face **after you confirm** (with a progress bar, cancel and SHA-256 verification).
  - When the engine, model or device changes, **only the STT restarts** (the LLM, TTS and the conversation keep running).
    Frequently used words apply from the next utterance.
  - You can go back to faster-whisper (when it is installed).
- If the settings screen does not open, use `configure.bat` and pick "speech recognition".
  Settings are stored in the `stt` section of `config/settings.json` (`engine` / `model` / `device` / `hints`).
- `setup.bat --stt auto|speechcpp|faster-whisper|both` chooses what to install (the default `auto` installs speech.cpp when Vulkan works and faster-whisper otherwise).
  With `both`, the settings screen can switch to faster-whisper as well.

![Speech recognition](docs/screenshots/settings_stt.png)

### Frequently used words (hints for proper nouns)

With qwen3-asr, the **character's name** and the "frequently used words" registered in the settings screen (separated by commas or line breaks, up to 400 characters) are sent as `prompt`.
The model uses the prompt as background, not as instructions.
In this test, "ミオさん、おはようございます。" came out as "みよさ、おはようございます。" with the 1.7B model and no hints, and correctly as "ミオさん、…" with hints (also confirmed through a real conversation turn in the app).
parakeet does not accept `prompt`, so nothing is sent.

### PCs without Vulkan

On a PC where `vulkan-1.dll` cannot be found, or `speech devices` fails, `setup.bat` installs the old faster-whisper (CPU) and says so.
At run time too, if speech.cpp cannot be used, faster-whisper runs instead and the admin log shows the reason (`start.bat` does not stop).
For testing, the environment variable `KOEMADO_SIMULATE_NO_VULKAN=1` (at run time) or `setup.bat --simulate "vulkan=0"` (at install time) reproduces a PC without `vulkan-1.dll`.
If Vulkan becomes available later, `setup.bat --stt both` adds speech.cpp.

### Credits

- parakeet-tdt_ctc-0.6b-ja: **(c) NVIDIA**, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) (the GGUF is a conversion of [nvidia/parakeet-tdt_ctc-0.6b-ja](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja)).
  This is the attribution the license asks for; it is also shown in the "Speech recognition" tab of the settings screen.
- Qwen3-ASR: (c) Alibaba Qwen, Apache-2.0 ([Qwen/Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)).
- speech.cpp: MIT ([nyosegawa/speech.cpp](https://github.com/nyosegawa/speech.cpp)).

## Speech synthesis (TTS)

Speech synthesis uses **Irodori-TTS v4.1 Small** (GGUF) running on `speech serve` from speech.cpp (MIT) by default.
Like STT, it needs no Python and no PyTorch: it uses your GPU through **Vulkan**.
The old Python version (Irodori-TTS-Server with PyTorch, int8) stays as a fallback for PCs where Vulkan is not available.

- Install: `setup.bat` installs only speech.cpp (shared with STT, in `stt\speechcpp\`) and the Irodori GGUF (by default `irodori-tts` Q8_0, about 1.2 GB).
  **The Python environment (Irodori-TTS-Server, PyTorch, Python 3.10, about 5 GB) is not installed by default.**
  The GGUF is fetched with `hf download` at a pinned commit into `models\tts\` and its SHA-256 is verified.
- Start: the supervisor runs `speech serve <GGUF> --host 127.0.0.1 --port <TTS port> --device <GPU> --add-voice <name>=<WAV> ...` without a console window (every WAV in `tts\voices\` is passed with `--add-voice`).
  It is a **separate process** from STT (see "Why TTS and STT are not one process" below).
- Conversation: `app/run.py` sends `voice`, `steps` and `seed` to `POST /v1/audio/speech` (no `model`).
  The reply is a 48 kHz, 16-bit, mono WAV that AIAvatarKit's `create_instant_synthesizer` plays as it is (expressions and lip-sync work as before).
- Switching voices: it takes effect **from the next utterance with no restart** (the voice is chosen per request among those passed at start).
  When you add or remove a reference voice (upload, saving a Voice Design, delete), speech.cpp reads voices at start, so **the settings screen restarts only the TTS automatically** (about 4 to 5 seconds).
  A broken WAV would stop the TTS from starting, so the WAV is checked before it is added, and the addition is rolled back if the TTS fails to start.

### Models (Irodori-TTS v4.1 Small)

| Setting name | Method | File (Q8_0) | Notes |
|---|---|---|---|
| `irodori-tts` (default) | Rectified Flow. `num_steps` trades quality for speed | `Irodori-TTS-859M-v4.1-Q8_0.gguf` (1.20 GB) | The **same model** as the old Python version |
| `irodori-tts-mf` | MeanFlow. 4 steps is standard | `Irodori-TTS-866M-MF-v4.1-Q8_0.gguf` (1.21 GB) | Fast. A little more read-back errors |

Both are [sakasegawa's GGUF](https://huggingface.co/sakasegawa/Irodori-TTS-v4.1-Small-GGUF) ([MF](https://huggingface.co/sakasegawa/Irodori-TTS-v4.1-Small-MF-GGUF)) of Aratako's [Irodori-TTS-v4.1-Small](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small) ([MF](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small-MF)).
Weights come in F16, Q8_0, Q6_K, Q5_K and Q4_K; this app uses Q8_0 (F16 uses about 0.7 GB more VRAM, and in this test it was no faster and no better on read-back).

### Reference voices, Voice Design and the watermark

- A **reference voice** is a WAV (PCM, up to 120 s).
  `--add-voice NAME=FILE` takes a WAV as it is (it is encoded at start, 0.4 to 0.7 s each).
  Voice files that join several recordings (`*.voice.gguf` made with `speech voice`) are supported by speech.cpp, but this app uses one WAV as one voice.
- **Voice Design** (the "Voice" tab) works as it is.
  The caption goes in `instructions`, the voice is `none`, and the seed is the request's `seed`.
  Saving a candidate WAV as a reference voice works as before (saving restarts the TTS and the voice joins the list).
- **No watermark (SilentCipher) is embedded.**
  speech.cpp's Irodori-TTS does **not support** the watermark (its docs say "Not supported: the watermark").
  The old Python Irodori-TTS-Server embeds a watermark in the audio it generates.
  Audio made with speech.cpp carries none, so there is nothing to tell afterwards that it was made with Irodori.
  Please follow "Ethical restrictions of Irodori-TTS" below whether or not there is a watermark.
- The same seed gives different audio from the official implementation and from the Python version (per speech.cpp's docs).
- A request speaks at most 30 s, a sentence at a time. AIAvatarKit sends one sentence at a time, so this normally does not matter.

### Comparison (Python version / speech.cpp)

The same 8 sentences (short conversational lines with Sendai, numbers and English words) were synthesized in Mio's voice (`mio_bright`, seed 42) and compared (`tools/tts_bench.py`; Ryzen 7 7800X3D + RTX 4090).
Listening page: `I:\koemado-tmp\tts_compare\listen.html` (a path on the development PC; not in the repository).

| Method | Per sentence (about 5 s of speech) | RTF | First sound (stream) | VRAM | F0 median (reference 341 Hz) | F0 spread | Read-back CER\* |
|---|---|---|---|---|---|---|---|
| Python Irodori (int8, 24 steps) | 1.68 s | 0.33 | (not supported) | 2,662 MiB | 315 Hz | 0.69 semitones | 5.7% |
| speech.cpp `irodori-tts-mf` (F16, 4 steps) | 0.14 s | 0.026 | 0.06 s | 2,260 MiB | 375 Hz | 1.75 semitones | 7.5% |
| speech.cpp `irodori-tts` (F16, 16 steps) | 0.33 s | 0.060 | 0.15 s | 2,272 MiB | 357 Hz | 1.60 semitones | 5.7% |
| speech.cpp `irodori-tts` (F16, 24 steps) | 0.45 s | 0.084 | 0.23 s | 2,272 MiB | 370 Hz | 1.53 semitones | 6.2% |
| mf (**Q8_0**, 4 steps) | 0.14 s | 0.027 | 0.05 s | **1,579 MiB** | 367 Hz | 1.59 semitones | 7.5% |
| rf (**Q8_0**, 16 steps) | 0.33 s | 0.061 | 0.15 s | **1,597 MiB** | 378 Hz | 1.75 semitones | 5.7% |
| rf (**Q8_0**, 24 steps) | 0.44 s | 0.081 | 0.22 s | **1,597 MiB** | 371 Hz | 1.77 semitones | 6.2% |

- Times are the median from the request until the whole WAV arrives (AIAvatarKit plays only after receiving the whole WAV), each over 2 runs per sentence.
  "First sound" is the time until the first audio of a `response_format: pcm` stream (the app uses WAV, so it is not used now).
- VRAM is the peak per-process dedicated GPU memory ("GPU Process Memory" in Windows). The Python version measured 2,474 MiB in another run.
- **F0** (pitch) is by pyin: the median and the spread (semitones) of the per-sentence F0 medians (as in `tools/make_voices.py`).
  The reference voice `mio_bright` is 341 Hz.
  The Python version is about 1.4 semitones below it and stable.
  speech.cpp is about 0.8 to 1.8 semitones above it and moves more from sentence to sentence ("うんうん、わかった" rises to about 430 to 445 Hz).
  **Whether it still sounds like Mio is for you to decide by listening** (the listening page).
- **Read-back CER\***: the synthesized audio is transcribed by speech.cpp's qwen3-asr-1.7b and compared with the text (kanji numerals unified with Arabic ones).
  The Python version and `irodori-tts` (rf, 16 steps) are both 5.7%, MeanFlow is 7.5% (with 8 sentences, one sentence moves it).
- Speed: speech.cpp is about 4 times faster than the Python version with the same model (rf, 24 steps), and MeanFlow about 12 times.
  On the first synthesis after start, it sometimes took 1 to 2 seconds once while the GPU driver compiled shaders.
- **The provisional default is `irodori-tts` (rf), Q8_0, num_steps 24** (the same model as the Python version, to keep the voice difference smallest).
  For speed you can switch to `irodori-tts-mf` in the settings screen.
  Which sounds better is for you to decide by listening.

### Why TTS and STT are not one process

One `speech serve` can hold TTS, STT and VAD at once (one port).
This app still runs **TTS and STT as separate processes**.
- **Different devices**: `--device` applies to every model in one process.
  The mid default is "TTS on GPU, STT on GPU for parakeet / 0.6B and on CPU for 1.7B", so on an 8 GB PC the devices cannot be the same.
- **Different restarts**: speech.cpp reads reference voices at start, so the TTS restarts whenever a voice is added.
  In one process the STT would stop each time, and switching the STT model would stop the TTS.
- **VRAM does not change**: the models are loaded separately, so one process does not reduce VRAM (a little per-process context memory might be saved; not measured).
- **Waiting**: speech.cpp handles one request at a time per model.
  TTS and STT are different models, so one process would not make them wait for each other either (GPU compute is shared).
  Separate processes behave the same.

### VRAM (mid, simulating 8 GB; peak with the LLM, TTS and STT all running)

Peak per-process dedicated memory.
With the Python TTS (int8) the previous figure was 5,929 MiB with STT on the CPU.

| STT | LLM | TTS (speech.cpp, Q8_0) | STT | Peak of the sum | Within 7 GB (7,168 MiB) |
|---|---|---|---|---|---|
| CPU (parakeet) | 3,518 MiB | 1,596 MiB | 0 | 5,114 MiB | yes |
| parakeet-ja (GPU) | 3,518 MiB | 1,600 MiB | 705 MiB | 5,823 MiB | yes |
| qwen3-asr-0.6b (GPU) | 3,518 MiB | 1,609 MiB | 1,041 MiB | 6,169 MiB | yes |
| qwen3-asr-1.7b (GPU) | 3,518 MiB | 1,600 MiB | 2,321 MiB | 7,439 MiB | **no (slightly over)** |

- The default combination (TTS on speech.cpp, STT parakeet-ja on GPU) is 5,823 MiB, within 7 GB.
- With qwen3-asr-1.7b on the GPU it exceeds 7 GB, so with the device on "auto" mid keeps it on the CPU (as in the STT table).
  You can still choose the GPU in the settings screen, with a warning.
- The TTS now uses about 0.8 GB less VRAM, so the mid LLM could take about 2 more expert layers (about 0.8 GB); this change leaves the LLM settings alone.

### How to switch

- In the "Speech synthesis engine" block at the top of the settings screen's **"Voice" tab**, choose the engine (auto / speech.cpp / Python version) and the model (`irodori-tts-mf` / `irodori-tts`), then press "Save and apply".
  A model that is not downloaded is fetched after you confirm its size (with progress, cancel and SHA-256 verification).
  Changing them restarts only the TTS.
- `setup.bat --tts auto|speechcpp|python|both`: the default `auto` uses speech.cpp when Vulkan works and the Python version otherwise.
  `both` installs both so that the settings screen can switch.
- Settings are in `config/settings.json` under `tts` (`engine` / `model` / `mf_steps` / `num_steps` / `seed` / `voice`).

![Voice (speech.cpp)](docs/screenshots/settings_voice_speechcpp.png)

### PCs without Vulkan

On a PC where `vulkan-1.dll` is missing or `speech devices` fails, `setup.bat` installs the Python Irodori-TTS-Server (PyTorch, about 5 GB) and says so (STT uses faster-whisper).
At run time too, if speech.cpp cannot be used, the Python version runs instead and the admin log shows the reason.
For testing, `KOEMADO_SIMULATE_NO_VULKAN=1` (run time) or `setup.bat --simulate "vulkan=0"` (setup time) reproduces it.

## Starting and the admin console

`start.bat` launches a resident management process (`supervisor.py`) with no window and closes immediately.
The supervisor starts and watches the LLM (llama-server, or Strata), TTS, STT and AIAvatarKit **without any console windows**, so that non-technical users are not startled by lots of black windows.

- **Admin console**: `http://127.0.0.1:8099/` (if the port is taken, a free one is chosen and saved to `config.json`; it opens even if AIAvatarKit is down; only reachable from 127.0.0.1, with Host / Origin checks).
  - "State": running status, port and uptime per service, VRAM and RAM, restart per service, restart all and quit. While starting it shows progress and moves to the conversation screen automatically when ready (including the long first load of Strata). If startup fails, a button opens the log of the failed service.
  - "Logs": a tab per service plus "All". Auto-updating (SSE), INFO / WARNING / ERROR coloring and filtering, text search, pause auto-scroll, copy, download.
  - **"Download a bundle for bug reports"**: puts all recent logs and environment info (GPU, driver, memory, profile, versions) into one zip. **Ask friends to send this zip when reporting problems.**
  ![State](docs/screenshots/console_status.png)
  ![Logs](docs/screenshots/console_logs.png)
- Logs are also saved to `logs\<service>.log` (rotated at 5 MB, 4 generations). **Anything that looks like an API key or token (the keys you entered in the settings screen, `Bearer` values, `api_key=` and so on, and strings starting with `hf_`) is masked** before being saved, shown or downloaded.
- **A service that exits abnormally is restarted automatically** (up to 3 times in 5 minutes). After that it is left stopped and the console shows a message to check the log.
- Child processes are tied to the supervisor with a Job Object. Even if the supervisor is killed, the OS stops every service (no orphans). It never starts twice (running `start.bat` while it is running just opens the page).
- A **tray icon** appears (menu: show / hide the character / open conversation (browser) / settings / logs and state / restart / quit). If the tray is unavailable, the app keeps running (use the console's Quit button or `stop.bat`).
- The conversation and settings screens have a link to the admin console at the top right.
- At the end of `setup.bat` it asks whether to create "koemado" shortcuts on the desktop and start menu (they start without a window). You can create them later with `app\.venv\Scripts\python.exe ctl.py shortcuts`.

### Switching the display mode

You can switch between desktop character, browser and both at any time.
While koemado is running, the change is applied on the spot (the character is shown or stopped, and the conversation screen is opened in the browser).
While it is not running, it applies from the next start.

| How | What it does |
|---|---|
| `start-mascot.bat` / `start-browser.bat` | Saves the display mode and starts |
| `start.bat --mascot` / `--browser` / `--both` | The same. It can be combined with `--console` (in any order). Without an option, the saved setting is used |
| `display.bat mascot` / `browser` / `both` | Switches only the setting, even when koemado is not running. Without an argument it shows the current mode and lets you choose by number |
| The "Display" tab of the settings screen | Saving applies it on the spot while running |
| "Display" in `configure.bat` | Switches only the setting (applies from the next start) |
| The tray icon, then "Display mode" | Switches on the spot while running |

## Desktop character

When you start with `start.bat`, a **character floating on your desktop** appears by default instead of a browser tab (think of the Office dolphin, Kai).
It is a frameless, transparent window shown above other windows.
Clicks on the transparent parts around the character go through to the window underneath.
Everything behind it (LLM, TTS, STT, AIAvatarKit, the settings screen, the admin console) is the same as the browser version.

![Desktop character (silhouette)](docs/screenshots/mascot_silhouette.png)
![Talking (a test character made of simple shapes)](docs/screenshots/mascot_talk.png)

### Choosing how it is displayed

- You can change it in any of these places.
  - The "Display" tab of the settings screen (`http://127.0.0.1:8100/settings#display`)
  - "Display" in `configure.bat`
  - The last question of `setup.bat` (or `--display mascot|browser|both`)
- The choices are "Desktop character" (default), "Browser (as before)" and "Both".
- While koemado is running, a change applies on the spot (see "Switching the display mode" above). To merely hide the character, use the tray icon's "Show / hide the character".
- If the desktop character cannot be used on this PC (its parts are missing, or Windows is older than 10 version 1809), the browser version starts regardless of the setting. The reason is shown on the "Display" tab and in the admin console log (character).
- While starting, a small bubble above the character shows progress, such as "Starting... TTS (Irodori) (63 s)". When everything is ready, the character can talk. If startup fails, the bubble says so and the admin console opens in the browser.

![The "Display" tab of the settings screen](docs/screenshots/settings_display.png)

### Controls

| What you want | How |
|---|---|
| Talk | Follow "How to talk" (below). While it is listening, an ear mark (👂) appears at the top right of the character |
| Type instead of talking | Right-click, then "Type to talk". A small input box appears (Enter to send, Esc to close) |
| Move | Drag the character. The position is remembered and used next time |
| Size | Right-click, then "Size": small / medium / large. Also remembered |
| Mute the microphone | Right-click, then "Mute (microphone)". A 🔇 mark shows while muted |
| Volume | Right-click, then "Volume" (0 / 25 / 50 / 75 / 100%) |
| Always on top | Right-click, then "Always on top" (on by default) |
| Open settings | Right-click, then "Open settings" (opens the settings screen in the browser) |
| See logs and state | Right-click, then "Open logs and state" (opens the admin console in the browser) |
| Hide | Right-click, then "Hide (to tray)". Bring it back with the koemado tray icon, then "Show / hide the character" (or left-click the icon) |
| Quit | Right-click, then "Quit (stop everything)". The LLM, TTS, STT and AIAvatarKit stop too |
| Show an image | Drag and drop an image file onto the character. Available only while "image recognition" is on in the LLM tab of the settings screen (otherwise the character tells you) |

![Right-click menu](docs/screenshots/mascot_menu.png)

Replies appear in a bubble above the character.
The bubble disappears after a few seconds (not while the character is speaking or thinking; the number of seconds can be changed in the settings screen).
A long reply wraps; if it does not fit, the earlier part is hidden and the end that is being spoken stays visible.
Control tags for expression and motion (such as `[face:joy]`) are not shown in the bubble.

### How to talk

Choose it on the "Display" tab of the settings screen (it applies as soon as you save).

| Mode | What it does |
|---|---|
| Listen only while the character is pressed (default) | The microphone is heard only while you press the character. Releasing counts as the end of what you said. If you move the mouse while pressing, it moves the character instead (nothing is heard) |
| Shortcut key | Default `Ctrl+Alt+Space`. Press it and the ear mark appears for one utterance (it closes by itself after 20 seconds if you say nothing, and pressing again closes it at once). You can change the key in the settings screen: one of Ctrl / Alt / Shift / Win plus one key, such as `ctrl+alt+k` or `ctrl+shift+f9` |
| Always listen | Same as the current browser version. The microphone is always heard (the ear mark stays) |

- "Interrupt" (talk over the avatar to stop it) can also be turned on in the same screen. With speakers the avatar may hear its own voice and stop, so headphones are recommended. While off (default), the microphone is not accepted until the avatar finishes speaking.
- The microphone permission is granted automatically, only for the character page on 127.0.0.1 (no confirmation dialog). In Windows "Settings, Privacy & security, Microphone", "Let desktop apps access your microphone" must be on.

### When the character did not start the conversation

Replies that you did not start, such as a completion report from an external agent (Hermes and the like), are also shown in the bubble and spoken. While the character is hidden, a Windows notification (shown through the tray icon) tells you as well.

### How it works and what it depends on

- The display is the **Qt WebEngine** of PySide6 (Chromium is bundled). The background is truly transparent, and only the shapes of the character, the bubble and the input box can be clicked (the shape is computed from the transparent parts of the character image). No separate runtime such as Edge WebView2 is needed.
- The character page (`app/html/mascot.html`) uses the same conversation machinery as the browser version (WebSocket, lip-sync, blinking, expressions). Mouth shapes and expressions follow each character's assets ("Expression and lip-sync assets"). Single-image characters and the silhouette use the light motion.
- `mascot.py` is started and watched by the supervisor (no window). Its log is in the "Character" tab of the admin console and in `logs\mascot.log`. If it exits abnormally it is restarted automatically (up to 3 times in 5 minutes).
- The position and size are saved in `mascot` of `config/settings.json` (`x` and `y` are the bottom center of the window). If the character ends up off the screen, or the display layout changes, it is moved back to a visible place (bottom right).
- PySide6 takes about 0.7 GB in the install folder. For an existing installation, run `setup.bat` again to add it.

### Troubleshooting (desktop character)

| Symptom | What to do |
|---|---|
| The character does not appear | Open "Logs", then "Character" in the admin console (tray "Logs and state"). If it says it cannot be used, run `setup.bat` again (the browser version starts meanwhile). If it is merely hidden, use the tray icon, then "Show / hide the character" |
| It is off the screen and does not come back | Positions that cannot be seen are moved to the bottom right at startup. To do it by hand, set `x` and `y` of `mascot` in `config/settings.json` to `null` and restart the character (the admin console's "Restart") |
| It does not react when I talk | Check "How to talk" (by default it listens only while pressed). Check that it is not muted (🔇). In Windows "Privacy & security, Microphone", allow desktop apps to use the microphone |
| The shortcut key does not work | If another app uses the same key it cannot be registered (the bubble and the log say so). Choose another key in the settings screen |
| No notification | Check that Windows focus assist (notifications off) is not on. Notifications are shown only while the character is hidden |
| I want the browser version back | "Display" in the settings screen, then "Browser (as before)", or `configure.bat` |
| It appears twice | With "Both", the browser conversation screen and the character hold separate conversations. If you want just one, change the display mode |

## Usage

- Conversation screen: `http://127.0.0.1:8100/static/index.html` (the "starting..." page moves there automatically). Microphone permission is required.
  - The screen is in Japanese (setup rewrites the fetched AIAvatarKit page). The "割り込み" (barge-in) toggle lets you speak and stop the avatar while it is talking; with speakers it can pick up the avatar's own voice and stop, so headphones are recommended. When it is off (the default), the microphone is not accepted until the avatar has finished speaking.
- **Settings screen: `http://127.0.0.1:8100/settings`** (voice, LLM, character, tools; `configure.bat` changes the same settings interactively).
- Admin screen (metrics, logs): `http://127.0.0.1:8100/admin`
- Try it without a browser: `app\.venv\Scripts\python.exe tools\e2e_test.py "こんにちは" --repeat 3` (sends text over WebSocket and prints the reply, expression tags, audio and timing; the sample text means "hello").

All servers listen on `127.0.0.1` only. The settings screen and `/ws` cannot be used from other PCs or other websites (Host / Origin are checked as well).

### What the settings screen can do

![Voice](docs/screenshots/settings_voice.png)

**Voice**: list and preview the reference voices (`tts/voices/`) and switch (**applied from the next utterance, no restart**). Upload a reference voice (wav / flac / mp3 / ogg / opus; m4a / aac / webm cannot be read in this environment). Voice Design (make candidates from a caption plus a seed, preview them and save the one you like). num_steps (speed versus quality).
The upload screen shows a notice that you must not use the voice of a real person without their consent, and asks you to confirm.

![LLM](docs/screenshots/settings_llm.png)

**LLM**:
- **Bundled llama-server**: choose a profile (high / mid / low), or **any GGUF** (a Hugging Face repository plus file name, or the path of a local .gguf). The size of the chosen model is compared with this PC's specs (VRAM / RAM / free disk) and "too heavy" or "not enough disk" warnings are shown. Downloads ask for confirmation and show a progress bar. Temperature, thinking on/off and context length can also be set.
- **External inference server** (OpenAI-compatible): Strata / LM Studio / Ollama / vLLM / llama-server on another PC / a cloud API. Set base_url, API key (optional), model name (choose from `/v1/models`), how to stop thinking (`reasoning_effort` / `chat_template_kwargs` / send nothing), whether to use tool calling, and whether to serialize LLM and TTS. The **connection test** button checks response time, thinking leaking into the answer, and tool calling.
  - Serialization: when the LLM and TTS share one GPU, turn it **on** (prevents fighting over VRAM and compute, and makes the first audio faster). For another PC or the cloud, off is a good default.
  - External servers are not started by `start.bat`. If you start one first, `start.bat` waits up to 2 minutes for `/v1/models` to respond before starting AIAvatarKit.
  - Presets: Strata (`:8080/v1`; handles one request at a time; needs 12 GB+ VRAM and 48 GB+ RAM; see "Using Strata" above, and the next section for refusal suppression and image recognition), LM Studio (`:1234`), Ollama (`:11434/v1`), vLLM, another PC, cloud. The ports are each tool's defaults and can be changed.
  - API keys are stored in `config/settings.json` (git-ignored). They are never shown again on the screen (masked) and never written to logs.
  - If you choose a cloud API (or another network), the screen warns that **your conversation is sent outside**.
- After changing the LLM mode, model, endpoint or context length, press `保存して再起動` ("Save and restart") on the screen, or run `start.bat` again. Temperature and thinking take effect from the next message.

#### Model browser (register a Hugging Face token and pick any LLM)

If you register a Hugging Face (HF) token, you can pick an LLM from the list of GGUF models on HF, **within the range that suits your profile**.
The token is optional. Without it, you can still use the models in the profile table and enter any GGUF by hand (repository plus file name).

- **Token**: register, verify and delete it in the LLM tab (and in `configure.bat`, under the Hugging Face token item). To create one, open the [HF settings page](https://huggingface.co/settings/tokens), choose "Create new token", and a **Read** token is enough.
  - It is stored in `config/settings.json` (git-ignored). The screen shows it masked and never displays it again after saving.
  - The verify button calls only `https://huggingface.co/api/whoami-v2` and shows the user name and whether the token is valid (an invalid token is reported as invalid).
  - **The token is sent to huggingface.co only.** When a download is redirected to a CDN, the token is removed before following the redirect.
  - It is used for search, the file list and downloads (including gated models). `setup.bat` also uses the `HF_TOKEN` environment variable or the saved token.
  - The value never appears in the admin console logs, the bug-report zip or the settings API responses (strings starting with `hf_` are masked too).
  - While no token is registered, list selection is disabled and the screen says "Register a token to choose from the list" (the Japanese message reads: `トークンを登録すると一覧から選べます`).
- **Search**: a search term, sort order (downloads, likes, last updated), "only model families said to be strong at Japanese" (Qwen, Gemma, Swallow, ELYZA and so on; the list is `JA_FAMILIES` in `profiles.py`), and paging.
- **File list**: choosing a repository shows each quantization and its size. A split GGUF (`-00001-of-0000N`) is shown as one model with the total size, and auxiliary files such as mmproj are excluded from the candidates. Each repository and candidate shows download count, likes, last update, license and whether it is gated.
  - If the model is gated and you have not accepted its terms, the screen shows a link and tells you to accept the terms on the HF page (the same message appears when a download gets a 403).
- **Range (lower and upper limit)**: each profile has a range of file sizes you can choose (`MODEL_LIMITS` in `profiles.py`). Files outside the range are shown in gray with the reason and cannot be selected.
  - The range is displayed together with the current profile. If you change the profile, the range changes too.
  - Even inside the range, cautions are shown. A dense (non-MoE) model that greatly exceeds the VRAM left after the TTS headroom will run slowly because much of it lands on the CPU. For MoE models, the screen says whether `--n-cpu-moe` can be computed the same way as for mid.
  - Whether a model is dense or MoE, and its layer count, are read from the GGUF metadata (the first bytes of the file). If that fails, they are guessed from the name (for example `35B-A3B`).
- **Download and switching**: it shows the size and free disk space and asks for confirmation, then downloads (progress, resume from where it stopped, cancel). When finished it restarts llama-server with that model and automatically runs the connection test (short reply, no thinking leaking, tool calling). If the test fails, a "go back to the previous model" button appears.
  - llama-server arguments are decided the same way as for the existing profiles. For a MoE model whose layer count is known, when the installed profile is mid, the number of layers whose experts stay on the CPU (`--n-cpu-moe`) is computed from VRAM. Otherwise `--fit` decides. `--jinja` is always added. The context length is 8192 (4096 for large models close to this PC's limit).
  - There is also a list of downloaded models with deletion (to recover disk space). They are stored in `models\<owner>--<name>\` (keeping the path inside the repository). Downloads work as described in "Downloads use `hf download`" above.

| Profile | Selectable size range | Caution threshold | Basis of the range |
|---|---|---|---|
| high | 8 to 28 GiB | 25 GiB and up | Up to about 1.35 times the default (20.6 GiB), which is what fits in 24 GB VRAM plus 48 GB RAM. The lower bound is about a 12B model at Q4 |
| mid | 4 to 16 GiB | 14 GiB and up | Up to about 1.2 times the default (13.3 GiB), roughly half of 32 GB RAM. The lower bound is about a 7B model at Q4 |
| low | 2 to 6 GiB | 5.4 GiB and up | Up to about 1.25 times the default (4.8 GiB), which is what is left of 16 GB RAM after the OS, TTS and STT. The lower bound is about a 3B model at Q4 |

Models smaller than the lower bound are not selectable, because Japanese conversation and tool calling tend to become unstable.

On top of that range there is an absolute upper limit from the detected hardware: the smaller of **(RAM minus 4 GiB for the OS, 1.5 GiB for STT, 1.5 GiB for the app and 3 GiB for TTS) plus (VRAM minus 2.7 GiB for TTS and 1.5 GiB for the desktop) minus 1 GiB for the KV cache and compute buffers**, and the free disk space (keeping 1 GiB spare).
For example, on a PC with 8 GB VRAM and 32 GB RAM the hardware limit is about 24.8 GiB (the mid range of 16 GiB is smaller, so it applies for mid).

![Model browser](docs/screenshots/settings_llm_browser.png)

![Character](docs/screenshots/settings_char.png)

**Character**: one character is one folder (`characters/<id>/character.yaml` and `image.png`). You can set name, age (optional), first-person pronoun, how to address the user, speech style, personality, likes and dislikes, background, relationship, example lines and the voice to use, and switch between several characters.
- The system prompt is built from `character.yaml` (rules for speech, expression and motion tags, weather and memory are shared in `characters/_common.md`). Long texts such as the background are the same string throughout a conversation, so the LLM's prompt cache works.
- Standing picture: upload a PNG from the settings screen and it is shown on the conversation screen. If expression-specific images such as `image_joy.png` exist (neutral / joy / angry / sorrow / fun / surprised / eyes_closed), they switch with the expression. Without an image a silhouette is shown. The images of the sample character Mio are fetched from AIAvatarKit's official samples at setup time and placed in `characters/mio/` (if the download fails, setup continues and she stays a silhouette; running setup.bat again retries).
- Memory (`app/data/memory_<id>.db`) is separate for each character.
- **Age is optional**: it may be left empty. ESP can be used only with characters whose age is set to 18 or older. Every character declines sexual content and sexual topics involving minors (a shared rule that character settings cannot change).

Settings are stored in `config/settings.json` (voice, LLM) and `characters/` (characters). `.env` (`app/.env`) holds only low-level values such as ports and is rebuilt by `start.bat`.

**Speech recognition**: engine (auto / speech.cpp / faster-whisper), model (parakeet-ja / qwen3-asr-0.6b / qwen3-asr-1.7b), device (auto / GPU / CPU) and frequently used words (for qwen3-asr). A model that is not downloaded yet is fetched after you confirm its size, and only the STT restarts. See the "Speech recognition (STT)" section.

## Expression and lip-sync assets

The avatar on the conversation screen moves according to the files in `characters/<id>/`.
Everything is optional, and anything missing falls back to a simpler motion.
You can also upload them from the Character tab of the settings screen (mouth shapes come with a preview, deletion and a canvas-size warning).

### File names

```
characters/<id>/
  image.png            the base face picture (standing picture)
  image_neutral.png    per-expression face pictures (neutral / joy / angry / sorrow / fun / surprised); a missing expression stays on image.png
  image_eyes_closed.png  a face with closed eyes; used for blinking when present
  mouth/closed.png     mouth shapes: transparent PNGs with the same canvas size as the face picture, drawing only the mouth
  mouth/half.png
  mouth/open.png
  mouth/e.png
  mouth/u.png
```

- Recommended: square transparent PNGs of 512 to 1024 px (the conversation screen shows them inside a round frame). The limit is 10 MB and 4096 px per file.
- **Mouth shapes must have the same canvas size as the face picture.** They are simply stacked on top of the face, so no alignment settings are needed. The settings screen warns you if the sizes differ.
- The five shapes are `closed` (closed mouth), `half` (half open), `open` (a, o), `e` (i, e; wide), and `u` (u; pursed). You do not need all of them. A missing shape is replaced by a close one (`e` and `u` use `half`, `half` uses `open`). If there is no `closed`, nothing is drawn for a closed mouth.
- Expressions switch with tags such as `[face:joy]` at the start of a reply. Blinking happens only when `eyes_closed` exists.

### How it moves

| Assets | Motion on the conversation screen |
|---|---|
| With mouth shapes | The mouth image switches in time with the audio (an MFCC lip-sync that estimates vowels; when it cannot, the volume picks closed / half / open). Expression changes and blinking work too |
| A single image, or expressions only (no mouth shapes) | A light animation that follows the volume: a small bob and slight scaling while speaking, and a slow breathing motion while idle. When the expression changes, the picture fades briefly |
| No image (silhouette) | The same light animation as for a single image |

- The amount of motion can be set in "Avatar motion" on the settings screen: none / subtle (default) / large.
- When the browser's "reduce motion" setting (prefers-reduced-motion) is on, the motion stops (mouth images keep switching).

### How to make mouth shapes

1. Starting from the face picture (`image.png`), prepare five versions in which only the mouth is redrawn.
2. Export each with the **same canvas size as the face picture and everything except the mouth transparent** (in a paint program, hide the face layer and export only the mouth layer).
3. Put them in `mouth/` as `closed.png`, `half.png`, `open.png`, `e.png` and `u.png`, or upload them in the settings screen.

Tips for making them with image generation (inpainting):

- Use the face picture as the source and mask only the area around the mouth. Nothing outside the mask changes, so position and art style stay consistent.
- Use the same seed and the same prompt for all five shapes, changing only the mouth description, to keep the outlines close.
- Cut out only the generated mouth (using the difference from the source, or by hand), place it on a transparent background and export.

### Please note

- If you use pictures drawn by someone else, check the creator's terms of use (secondary use, modification, streaming).
- The sample character Mio's images are fetched from AIAvatarKit's official samples at setup time (see the credit above). AIAvatarKit's mouth shapes are already transparent 512x512 PNGs on the same canvas as the faces, so they only need renaming into koemado's format (`mouth/e.png` and so on). There is no closed-mouth image; "no overlay" (the mouth drawn on the face) is the closed state.
- For testing there is a script, `tools/make_test_avatar.py`, that makes a plain geometric face and mouth shapes (not a sample artwork).

## Troubleshooting

| Symptom | What to do |
|---|---|
| Stops with "the driver is too old" | Update to the latest NVIDIA driver (570 or newer). The script does not update drivers |
| Installation takes a long time | The first run downloads 8 to 24 GB in total (a PC without Vulkan also unpacks PyTorch (CUDA)). On an HDD, or with real-time antivirus scanning, unpacking can take tens of minutes (a few minutes on an SSD). You can stop and rerun to resume |
| "Not enough free space" | Pass `--install-dir` with a drive that has room, or free up space. If space is short it drops one profile automatically |
| It does not react when I speak in the browser | Check the browser's microphone permission (the lock icon left of the address bar). `http://127.0.0.1` is treated as allowed for the microphone. You also need to allow the browser under Windows "Privacy, then Microphone" |
| Out of VRAM (`CUDA out of memory`, or the LLM or TTS does not start) | Close other apps (games, 3D software, browser hardware acceleration). In the LLM tab choose a lighter profile (low), or increase `--n-cpu-moe` (`profiles.py`) to use less VRAM |
| Replies are slow | The bundled LLM processes whatever does not fit in VRAM on the CPU. Use low, or an external inference server. TTS gets faster with a smaller num_steps |
| Recognition is poor (proper nouns, kanji) | In the settings screen's "Speech recognition" tab, switch to qwen3-asr-0.6b or 1.7B. You can also register frequently used words as hints (comparison in the "Speech recognition (STT)" section) |
| Speech recognition does not work, or faster-whisper is in use | `vulkan-1.dll` may be missing (reinstalling the GPU driver restores it) or `stt\speechcpp\speech.exe` may be absent. The reason is in the admin log (stt). When Vulkan works, run `setup.bat --stt both` |
| Port already in use | `setup.bat` and `start.bat` switch to a free port automatically (defaults: LLM 8081 / TTS 8088 / STT 8002 / app 8100; 8000 and 8080 are avoided) |
| Startup fails | Open the admin console (`http://127.0.0.1:8099/`, or the tray's "logs and state") and look at the logs. The files are `logs\llm.log`, `tts.log`, `stt.log`, `app.log` and `supervisor.log`. When reporting, send the zip from "Download a bundle for bug reports" (keys are masked) |
| The admin console does not open, or the supervisor is not running | Run `start.bat --console` (or `debug-start.bat`) to start every service in a **console window** as before (errors are visible directly). There is no admin console or tray in this mode. Stop with `stop.bat` |
| No tray icon | The app is running. Look inside the hidden indicators ("^") on the Windows taskbar. You can also stop it with the console's Quit button or `stop.bat` |
| Thinking leaks into replies | Change "how to stop thinking" in the settings screen (depending on the model, either `reasoning_effort` or `chat_template_kwargs` works) |

## Tools (web search, MCP, skills, external agent)

Configure them in the "Tools" tab of the settings screen (and in `configure.bat`). ![Tools](docs/screenshots/settings_tools.png)

**Tool definitions are fixed at startup** (changing them mid-conversation would break the LLM's prompt cache). Changes take effect after "Save and restart". If you turn off "use tool calling" for an external server in the LLM tab, none of these tools can be used.

### Web search (on by default)

- `web_search(query)`: top 5 titles, URLs and summaries from DuckDuckGo (`ddgs`, no API key). **Search terms are sent to an external service (DuckDuckGo).**
- `web_fetch(url)`: reads a page's body as text. Only http / https. Destinations on this PC or an internal network, such as 127.0.0.1, 10.x, 192.168.x, link-local addresses and `file://`, are rejected, including redirect targets. The body is limited to 1 MB and 15 seconds (name resolution is checked before and after connecting, but protection against DNS rebinding is not perfect).
- Results are summarized briefly in spoken language, and URLs are not read aloud. It is used only when up-to-date information is needed.
- Weather (Open-Meteo) and memory can also be turned off individually in this tab.

### MCP

- `config/mcp.json` (same format as Claude Desktop, so you can paste it as is). See `docs/mcp.example.json` for an example.

  ```json
  {"mcpServers": {
    "demo": {"command": "python", "args": ["tools/mcp_test_server.py"], "allowed_tools": ["roll_dice", "current_time"]},
    "remote": {"url": "http://127.0.0.1:8765/mcp", "headers": {}, "enabled": false}
  }}
  ```

  You can add `enabled` (default true) and `allowed_tools` (all tools allowed if omitted). For stdio use command plus args (`python` is replaced by this app's Python; relative paths are relative to the install dir); for HTTP use a streamable-http URL.
- It connects at startup and registers the tools with the LLM. **A server that fails to connect is skipped** (a warning in `logs\app.log` and a "connection failed" mark in the settings screen) and the app still starts. A server's standard error goes to `logs\mcp_<name>.log`.
- Settings screen: server list (state and tool count), JSON editor, connection test (lists the tools so you can allow them individually), add and delete. env / headers values are shown masked.
- **Caution**: MCP tools are programs running on this PC, and some can operate on files or run commands. **A mis-heard voice command can be executed as is**, so add only servers you trust and narrow down the tools you allow. With many tools a small LLM becomes unreliable at calling them (rule of thumb, including the standard tools: 8 in total for low, 15 for mid, about 30 even for large models).
- `npx` servers need Node.js (it is not installed automatically; the connection test says so if it is missing).
- To try it, use `tools/mcp_test_server.py` (dice, time, addition). For HTTP: `app\.venv\Scripts\python.exe tools\mcp_test_server.py --http 8765`.

### Skills

- `skills/<name>/SKILL.md` (a `name` and `description` in the frontmatter and the steps in the body; a format similar to Claude's Agent Skills). Two are bundled: "news-topics" (today's news topics) and "research" (looking things up).
- Only the **list (name and description)** goes into the system prompt; the LLM reads the body with `use_skill(name)` when it needs it (keeping the prompt short and the cache intact). They are loaded at startup and shared by all characters.
- In the settings screen you can list, turn on or off, create, edit and delete skills.

### External agent (off by default)

Hand only long jobs (file operations, code execution, long research) to a separate worker agent. Weather and simple searches are handled by the avatar's own tools. The conversation continues after the request, and when the job completes the avatar speaks the result (`ask_agent` and, for progress, `check_agent_tasks`; AIAvatarKit's `OpenClawTool` and `use_invoke_queue`; active only while the integration is on).

- Kinds: Hermes Agent / OpenClaw. base_url (up to `/v1`; default `http://127.0.0.1:8642/v1`), API key (stored only in `config/settings.json` and never shown again), timeout (default 600 s). The "connection test" checks `/health` and `/v1/models`.
- **Caution**: Hermes's API grants access to all tools, including terminal execution. **Use it only inside 127.0.0.1 and always set an API key.** Do not loosen Hermes's approval settings.
- Installing Hermes itself is out of scope for this repository. Official install page: <https://hermes-agent.nousresearch.com/docs/>

## Ethical restrictions of Irodori-TTS (summary)

Irodori-TTS (MIT license) has the following usage restrictions in addition to the license. Please follow them in this project too.

1. **No impersonation**: do not clone or imitate the voice of a real individual, such as a voice actor, celebrity or public figure, without their explicit consent.
2. **No disinformation**: do not create synthetic speech (deepfakes) meant to deceive others or spread false information.
3. A voice made from a caption alone (without a reference voice) may happen to resemble a real person. This is a probabilistic effect and there is no intent to reproduce any specific person.
4. The developers take no responsibility for misuse. Using the output in compliance with the laws of your region is the user's responsibility.

The bundled voices (`voices/mio_bright` and `mio_a` to `mio_d`) were made with Voice Design using text captions and fixed seeds, and are not the voice of any real person (captions, seeds and stability are in `voices/voices_meta.json`). The sample character Mio uses `mio_bright` (a high, light and bright voice) by default. The old Python Irodori embeds a watermark (SilentCipher) in the audio it generates. **speech.cpp's Irodori-TTS (the default) does not support the watermark, so none is embedded.** Follow the restrictions whether or not there is a watermark.

## Licenses of the software this depends on

| Software | Purpose | License |
|---|---|---|
| This repository | | MIT |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | LLM inference | MIT |
| [AIAvatarKit](https://github.com/uezo/aiavatarkit) | Voice conversation base, browser UI | Apache-2.0 |
| [Irodori-TTS](https://github.com/Aratako/Irodori-TTS) / [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) | Speech synthesis (the Python version is a fallback) | MIT |
| [Irodori-TTS v4.1 Small (GGUF, sakasegawa)](https://huggingface.co/sakasegawa/Irodori-TTS-v4.1-Small-GGUF) / [MF](https://huggingface.co/sakasegawa/Irodori-TTS-v4.1-Small-MF-GGUF) | TTS model (default; for speech.cpp) | MIT (with ethical restrictions; the codec (DACVAE) is MIT / Apache-2.0) |
| [Irodori-TTS-v4.1-Small (quantized)](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small-Quantized) | TTS model | MIT (with ethical restrictions) |
| [Semantic-DACVAE-Japanese-32dim](https://huggingface.co/Aratako/Semantic-DACVAE-Japanese-32dim) / [DACVAE](https://github.com/facebookresearch/dacvae) | TTS codec | MIT / Apache-2.0 |
| [SilentCipher](https://github.com/sony/silentcipher) | Watermark for generated audio (Python version only) | MIT |
| [speech.cpp](https://github.com/nyosegawa/speech.cpp) | STT and TTS (default; Vulkan) | MIT |
| [parakeet-tdt_ctc-0.6b-ja](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja) (GGUF: sakasegawa) | STT model (default) | **CC-BY-4.0 (c) NVIDIA; attribution required** |
| [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) 0.6B / 1.7B (GGUF: sakasegawa) | STT model (optional) | Apache-2.0 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper small) | STT (fallback for PCs without Vulkan) | MIT |
| [silero-vad](https://github.com/snakers4/silero-vad) | Voice activity detection | MIT |
| [PyTorch](https://pytorch.org/) | Running the TTS | BSD-3-Clause |
| [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) (GGUF: unsloth) | LLM (high) | Apache-2.0 |
| [Gemma 4](https://ai.google.dev/gemma/docs/gemma_4_license) (26B-A4B / E4B) | LLM (mid / low) | The Gemma 4 license (Hugging Face shows Apache-2.0; read the original text before use) |
| [Strata](https://github.com/Niko1221/Strata) (optional) | LLM for high-end PCs | MIT (each model has its own license; follow Strata's guidance) |
| [PySide6 (Qt for Python)](https://doc.qt.io/qtforpython-6/) / Qt WebEngine | Showing the desktop character | LGPL-3.0 (includes Chromium; follows each of its licenses) |
| [uv](https://github.com/astral-sh/uv) / [MinGit](https://github.com/git-for-windows/git) | Setup tools | MIT, Apache-2.0 / GPL-2.0 (installed only when needed) |

The browser UI shows the standing picture of the character chosen in the settings screen (or a silhouette if there is none). The sample character's images are fetched at setup time from the examples of AIAvatarKit (uezo/aiavatarkit, Apache License 2.0); they are not included in the koemado repository.

## File layout

```
setup.bat / setup.py   install (detect specs, choose profile, install automatically)
start.bat / stop.bat   start and stop (start.bat launches the supervisor without a window through koemado.vbs; --console / debug-start.bat give the classic console start)
supervisor.py          resident management process (starts and watches services, auto-restart, logs, admin console, tray)
console_ui/            the admin console (state and logs) pages
koemado.vbs / .ico     windowless launch (shortcuts use it too), icon
mascot.py              the desktop character (a transparent PySide6 / Qt WebEngine window; started and watched by the supervisor)
display.bat / .py      switches the display mode (start-mascot.bat, start-browser.bat and start.bat --mascot use it too)
display_env.py         decides the display mode (character / browser / both); falls back to the browser where the character cannot run
html-overrides/        additions to the conversation page (koemado-avatar.js = lip-sync and expressions, the image panel, the desktop character mascot.html / .js / .css)
configure.bat / .py    interactive CLI for settings (a fallback for the settings screen)
ctl.py                 shared code for start, stop, restart, download and shortcuts (the console start lives here too)
profiles.py            LLM profile table, the size range for choosable models, llama-server arguments
stt_engine.py          STT engine and model tables, Vulkan detection, the speech.cpp command line, automatic device choice
tts_engine.py          TTS engine and model tables, the speech.cpp (Irodori-TTS) command line, the list of voices (--add-voice)
hf_dl.py               Hugging Face downloads (running hf download, progress, cancel; the parallel Range fallback with SHA256 verification)
model_picker.py        model selection (grouping GGUFs, comparing with the range; shared by the settings screen and setup.bat's interactive mode)
hf_hub.py              Hugging Face API (search, file list, GGUF metadata, token check); sends the token only to huggingface.co
settings_store.py      reading and writing settings and characters, and the ESP age check
tools_store.py         MCP config (config/mcp.json) and skills (skills/) reading and writing
skills/                skills (news-topics and research are bundled)
app/                   AIAvatarKit itself (run.py), settings screen (settings_api.py / settings_ui), weather, memory, web search, MCP, skills, external agent
stt/                   faster-whisper STT server (fallback) and speechcpp/ (speech.cpp, unpacked at setup)
voices/                bundled reference voices (adult female voices made with Voice Design)
characters/            characters (_common.md = shared rules, mio/ = sample)
tools/                 e2e_test.py (WebSocket test), stt_bench.py (STT comparison benchmark), tts_bench.py (TTS comparison benchmark) and gpu_sampler.ps1 (VRAM measurement), make_voices.py (make voices with Voice Design), mcp_test_server.py (for checking MCP)
```

Things created by installation (not in Git): `strata/` and `strata-data/` (when you choose Strata), `config.json`, `config/`, `llama.cpp/`, `models/`, `tts/`, `hf-home/`, `torch-home/`, `app/.venv`, `stt/.venv`, `app/html/`, `app/data/`, `logs/` and so on.
