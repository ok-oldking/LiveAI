# LiveAI

[简体中文](../zh-CN/index.md) | English

**Talk to ChatGPT and share screen context from a desktop overlay.**

LiveAI is a Windows desktop companion that connects to ChatGPT in your browser.
Dictate a question, attach a screenshot of a window or display, and hear the
response while you keep working. A translucent overlay brings together editable
voice input, subtitles, formatted replies, and an animated pet.

LiveAI uses your signed-in ChatGPT browser session through Playwright. No API key
is required; availability and usage limits follow your ChatGPT account. Screen
context is shared as screenshots attached to individual messages.

[Download for Windows](https://github.com/ok-oldking/live-gpt/releases) ·
[Getting started](#getting-started) · [Usage](#usage) ·
[Troubleshooting](#troubleshooting) · [Development](#development)

## Features

- **Voice input:** hold a global shortcut to dictate and send, or use the overlay
  microphone to review your words before sending.
- **Screen context:** capture a display or application window, with an option to
  include the cursor in window screenshots.
- **Spoken responses:** use ChatGPT's browser playback or a local GPT-SoVITS voice.
- **Readable replies:** follow rolling subtitles or expand the response to view
  paragraphs, tables, links, code blocks, and images.
- **Desktop controls:** move, resize, and lock the overlay; use auto-hide and the
  system tray to keep it accessible.
- **Optional local speech:** transcribe with Sherpa-ONNX and configure recording
  independently from playback.
- **Personalization:** choose animated pets and switch between English and
  Simplified Chinese.

## Getting started

### Install on Windows

Choose a package from the [releases page](https://github.com/ok-oldking/live-gpt/releases):

| Package | Setup | App updates / Python packages |
| --- | --- | --- |
| **China installer** | Includes Python and the default pet | CNB / Aliyun |
| **Global installer** | Includes Python and the default pet | GitHub / PyPI |
| **Online installer** | Downloads Python and dependencies during setup | Managed by the launcher |
| **Portable launcher** | Downloads Python and dependencies on first setup | Managed by the launcher |

The pyappify launcher manages Python 3.12, dependencies, and app updates. Optional
voice models are downloaded from LiveAI's settings. Installers and launchers are
unsigned.

### Connect ChatGPT

1. Open Chrome or Edge and sign in to ChatGPT.
2. Start LiveAI. If browser debugging is unavailable, LiveAI opens the browser's
   remote-debugging settings and attempts to enable it.
3. Approve the browser connection request when prompted.
4. Select a ChatGPT tab from the list beside the **LiveAI** heading. If none is
   open, LiveAI opens `chatgpt.com` once for that connection.
5. Select a screenshot source, then type a message or hold **Right Alt** to speak.

The input and microphone become available once a ChatGPT tab is connected. Browser
dictation may also request microphone permission on first use.

LiveAI connects to your existing browser profile through the Chrome DevTools
Protocol (CDP). It does not create a separate profile. If you deny the connection
or setup fails, use **Enable Debugging** to retry.

### Run from source

Use Windows with **Python 3.12 or later**, Git, and a Chrome or Edge browser that
supports remote debugging. Run these commands in PowerShell:

```powershell
git clone https://github.com/ok-oldking/LiveAI.git live-gpt
cd live-gpt
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m live_gpt
```

After activating the virtual environment, you can also launch the app with
`LiveAI`. The `LiveGPT` and `live-gpt` commands are aliases.

## Usage

### Dictate and send

| Control | Action |
| --- | --- |
| Hold **Right Alt** | Dictate and send with the selected screenshot source |
| Hold **Right Ctrl** | Dictate and send without a screenshot |
| Hold the overlay **microphone** | Dictate into the editor for review; send manually |
| **Enter** | Send the current prompt |
| **Shift+Enter** | Insert a newline |
| **With Screenshot** | Send the selected capture, with or without text |
| **No Screenshot** | Send text without changing the selected capture source |
| Editor **×** | Clear the local input and the selected ChatGPT tab's composer |

Global recording shortcuts work while the overlay is hidden or unfocused.
Starting a recording shortcut stops active speech playback. Customize shortcuts
in **Configure → Shortcuts**; left and right modifier keys are distinct. Clear a
shortcut with **×** to disable it. LiveAI observes these keys without consuming
them, so the foreground application still receives the same keystrokes.

With browser recording selected, ChatGPT captures and transcribes your audio;
LiveAI copies the result into its editor. A mouse recording must be held for at
least 0.5 seconds; a global hold shortcut waits 0.3 seconds before starting.
Successful shortcut dictation sends automatically. English (ASCII) voice input
requires at least two characters; non-English voice input can contain one.
Shorter results remain editable, and manually typed one-character messages are
allowed.

### Share a screenshot

Choose a display or a visible window from the screenshot selector. LiveAI
remembers your selection and defaults to **Screenshot desktop** if the saved
source is unavailable. Window choices are matched by executable, window class,
and title; a missing or ambiguous window is not silently replaced by another.
Only windows larger than one eighth of their containing display's area are listed.

Captures are uploaded as lossless WebP images, scaled down to a maximum longest
edge of 1920 pixels. Sending with a screenshot replaces existing ChatGPT image
attachments. **Configure → Screenshots → Capture mouse cursor in window
screenshots** controls cursor inclusion for window captures.

During dictation, LiveAI prepares the screenshot after a 0.5-second hold. Browser
dictation starts once the attachment begins uploading, and sending waits for the
upload to finish. A cancelled short press uploads nothing. Failed uploads are
retried up to three total attempts; if they still fail, the prompt remains in the
editor with an error message.

### Read and hear replies

After sending, the overlay switches to subtitles. The default is four rows;
choose between two and ten in **Configure → Playing → Subtitle rows**.
Subtitles follow audio playback where timing is available, with approximate
timing within sentences.

Hover over the subtitle area to expand the formatted reply:

- Select and copy text, open links, and copy individual code blocks.
- View image thumbnails and galleries. Click an image to open a resizable viewer;
  scroll to zoom, drag to pan, and use **Fit image** to reset the view.
- Move the pointer outside the overlay to return to compact subtitles.
- Close the expanded reply with **×** to stop playback, clear queued speech, and
  return to an empty input. Start dictation to ask another question.

Images and citation badges are excluded from speech and subtitles. Available
source links remain accessible in the expanded reply.

### Manage the overlay

Drag a non-interactive area to move the overlay, or drag an edge or corner to
resize it. The lock button beside **Configure** locks both position and size.
Dragging across monitors transfers the overlay to the screen under the pointer.

When the pointer moves away, the controls and text become transparent while the
pet stays visible. **Auto-hide** hides the entire overlay and lets clicks pass
through. It reappears for dictation, incoming replies, and playback, then hides
five seconds after playback ends. Unsent text and active interactions keep it
visible. Hiding applies only while a ChatGPT tab is connected.

Click or double-click the system tray icon, or choose **Show LiveAI**, to reveal
the overlay and disable auto-hide. Display and scaling changes also trigger a
position check to keep controls reachable. Use **Exit** to close the app.

For broadcast software, add a **Window capture** source and select **LiveAI**.
The executable may appear as `python.exe`, `pythonw.exe`, or `live-gpt.exe`.
Keep the overlay visible and auto-hide disabled while streaming. If capture is
blank, try Windows Graphics Capture; transparency depends on the capture method.

## Settings

Open **Configure** to adjust the application. Valid changes save immediately.

| Page | Options |
| --- | --- |
| **Shortcuts** | Global recording shortcuts, with and without screenshots |
| **Screenshots** | Cursor inclusion in window captures |
| **Language** | English or 简体中文; detected from the system on first launch |
| **Recording** | Browser dictation or local Sherpa-ONNX transcription |
| **Playing** | Browser playback or GPT-SoVITS, plus subtitle row count |
| **Pet** | Pet selection, downloads, and idle animation behavior |

Preferences are stored in `%APPDATA%\Live GPT\config.json`, including overlay
geometry, shortcuts, screenshot source, selected conversation, voice settings,
and pet selection. Missing or invalid configuration falls back to defaults and
is repaired automatically. To detect the system language again on next launch,
set `language` to `""`; explicit values are `"en"` and `"zh"`.

### Local speech recognition

In **Configure → Recording**, select Sherpa-ONNX, install its runtime, and download
a model. The catalog includes Mandarin and English models, stored in the working
directory's `models` folder. Installation supports PyPI, Aliyun, and SJTUG without
changing global pip settings. Use **Record microphone** to test transcription.

Recording and playback are independent: local transcription can be combined with
browser playback, or browser dictation with a local voice. Local transcription
still sends prompts to the selected ChatGPT browser tab.

### Local speech playback with GPT-SoVITS

LiveAI integrates with an existing
[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) installation that includes
`runtime/python.exe` and a CUDA-enabled embedded PyTorch runtime. An NVIDIA GPU
is required for this backend; CPU fallback is disabled.

1. Select GPT-SoVITS in **Configure → Playing** and choose its installation folder.
2. Select reference audio and, optionally, its transcript and the reference/output
   languages.
3. Choose an NVIDIA GPU. The default is GPU 0.
4. Optionally select both a GPT `.ckpt` file and a SoVITS `.pth` file. Leave both
   blank to use the installation's configured models; incomplete pairs are invalid.
5. Use **Play text** to test the voice.

LiveAI copies its speech server into the selected installation and starts it with
the embedded Python runtime. Startup includes model warmup and a silent synthesis;
the first run can take several minutes, with a 240-second startup limit. Models
run on the selected CUDA device in FP16. Changing models or GPUs reloads the
server on the next check or playback.

Sentences enter the synthesis queue while ChatGPT is generating its reply. Server
output and synthesis errors are captured in the logs. If no audio arrives for
30 seconds during playback, LiveAI stops the server and starts a fresh one on the
next attempt.

## Pets

Choose a bundled pet in **Configure → Pet**. The default is
`assets/pets/feibi-jiubi`. Pets animate during dictation, response generation,
playback, errors, and dragging. V2 pets also look toward a nearby pointer while
idle. Idle animation can run continuously, stay on a still pose, or play for a
limited duration each time the pet returns to idle.

To add a pet, paste a public GitHub folder URL into **Download pet**. The folder
must contain `pet.json` and `spritesheet.webp`. Browse
[awesome-codex-pet](https://github.com/legeling/awesome-codex-pet) for pet folders.
Validated downloads are saved under `download/pets/<folder-name>` and selected
immediately. Existing valid downloads are reused; failed downloads preserve the
current selection.

Custom pets support v1 (8 × 9) and v2 (8 × 11) sprite sheets with 192 × 208 pixel
cells. An omitted `spriteVersionNumber` is treated as v1. You can also set
`pet_path` in the configuration to an external pet directory or `pet.json` and
restart. An empty path uses the default pet; invalid custom pets fall back to it.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Input or microphone is disabled | Approve the browser connection and select a ChatGPT tab. |
| Browser connection was denied or setup stopped | Open `chrome://inspect/#remote-debugging` or `edge://inspect/#remote-debugging`, enable **Allow remote debugging for this browser instance**, then click **Enable Debugging** in LiveAI. |
| Browser dictation returns no text | Check browser microphone permission and hold the microphone long enough. Transcription waits up to 20 seconds. |
| Shortcuts fail in a game or elevated app | If LiveAI reports an administrator-window warning, run LiveAI as administrator for that application. |
| Screenshot upload fails | Check the selected source and ChatGPT's upload status. The prompt stays editable after retries are exhausted. |
| ChatGPT reports a usage limit | Follow the displayed limit/reset message and retry when available. Prompts rejected before sending remain in the editor. |
| GPT-SoVITS fails to start | Check the installation, embedded CUDA-enabled PyTorch, selected GPU, reference audio, and paired model paths. Allow time for initial warmup. |
| Overlay is hidden | Use the system tray icon or **Show LiveAI** to reveal it. |

For a specific browser connection, set `LIVE_GPT_CDP_ENDPOINT` to an existing CDP
HTTP or WebSocket endpoint before launching LiveAI:

```powershell
$env:LIVE_GPT_CDP_ENDPOINT = "http://127.0.0.1:9222"
.\.venv\Scripts\python.exe -m live_gpt
```

The endpoint must already be available. LiveAI's normal setup uses the browser's
remote-debugging settings rather than launching it with debugging flags.

## Development

The application uses Python, PySide6, and Playwright. Source code lives in
[`src/live_gpt`](https://github.com/ok-oldking/LiveAI/tree/master/src/live_gpt), with tests in [`tests`](https://github.com/ok-oldking/LiveAI/tree/master/tests).

After the source setup above, install build validation dependencies and run the
test suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Runtime dependencies, including transitive dependencies, are pinned in
[`requirements.txt`](https://github.com/ok-oldking/LiveAI/blob/master/requirements.txt). When changing runtime dependencies in
[`pyproject.toml`](https://github.com/ok-oldking/LiveAI/blob/master/pyproject.toml), update those pins and rerun package validation:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_packaging.py -v
```

### Build Windows releases

The [Windows Release workflow](https://github.com/ok-oldking/LiveAI/blob/master/.github/workflows/build.yml) validates the deployed
application, synchronizes the runtime files listed in [`deploy.txt`](https://github.com/ok-oldking/LiveAI/blob/master/deploy.txt)
to both update repositories, and builds the China and Global installers, online
installer, and portable launcher. Launcher profiles are configured in
[`pyappify.yml`](https://github.com/ok-oldking/LiveAI/blob/master/pyappify.yml); icons come from `icons/`. Source updates run directly
from `src`.

Configure these Actions secrets in the source repository:

| Secret | Purpose |
| --- | --- |
| `CNB_USERNAME` | CNB Git HTTPS username with access to the update repository |
| `CNB_TOKEN` | Permission to push branches and tags to `cnb.cool/ok-oldking/live-gpt-update` |
| `UPDATE_REPO_SECRET` | GitHub token with permission to push branches and tags to `ok-oldking/live-gpt-update` |

Push a version tag such as `v0.1.0` to build and publish a release. Prerelease tags
such as `v0.1.0-beta.1` publish prereleases. The workflow can also be run manually
with an existing tag. Build outputs are uploaded as workflow artifacts and attached
to the corresponding GitHub release. No signing credentials are used.

## Documentation website

The documentation follows the same structure as `ok-wuthering-waves`, with
Chinese as the default README and site language. See the
[contributor guide](../development/contributing.md#documentation-website) for
local preview, strict builds, and GitHub Pages deployment instructions.

## License

LiveAI is licensed under the [GNU Affero General Public License v3.0](https://github.com/ok-oldking/LiveAI/blob/master/LICENSE)
(`AGPL-3.0-only`).
