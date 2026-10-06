# Gather

English sermon translation into Spanish and Korean, built for Citizens Church.

**Website:** https://citizens-church.github.io/gather/

The public website guides listeners and church operators through setup. A dedicated church computer runs the speech, translation, voice, and notes models. Listeners use a modern browser and need no model downloads.

## Set up a church host

1. Open the website and choose **I’m hosting a service**.
2. Download Gather Host for Mac, unzip it, and move the app to Applications.
3. Open the app. Its browser setup checks hardware, installs the tools, and downloads verified models with visible progress and retry.
4. Open the booth, select Spanish and Korean, and connect a clean sound desk feed, microphone, or livestream.
5. Share the booth’s listening link or QR code. Keep Gather Host running during the service.

The tested inference stack needs **Apple Silicon macOS 14 or later**, at least **16 GB memory**, and about **25 GB free disk space** for a new installation. We recommend 24 GB or more, especially with live notes. These requirements do not guarantee a particular latency on every Mac. Windows, Linux, Intel Macs, phones and tablets can use the website and join an active host; their native AI host builds are not available yet.

The app download is about 17 MB. Core models total approximately **10.2 GB**, plus **2.3 GB** for optional sermon notes. Python and software dependencies need additional storage. Downloads use fixed revisions and SHA-256 checksums. Interrupted files resume when the upstream server supports byte ranges; verified files are reused. First setup requires internet.

The first host app release is ad hoc signed and **not notarized**. If macOS blocks it, use System Settings → Privacy & Security → **Open Anyway** for Gather Host. Source and release checksums are available here.

Data and diagnostics stay under `~/Library/Application Support/Gather/`. The repository and app download contain no church recordings, saved sessions, credentials, or model weights. Replacing the app preserves separately stored transcripts and notes.

## Listening

Open your church’s listening link, select Español or 한국어, and tap **Listen live**. Captions continue when playback is paused. One translated audio stream per language is shared among listeners.

The default host serves the church network. Connect to the same church Wi-Fi. Listening from outside that network needs a separately configured reachable backend. Publishing this website does not expose a private local computer to the internet.

GitHub Pages hosts the public setup website; the AI models and Python backend run on the church computer. [GitHub Pages documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site).

## Translation and church vocabulary

The pipeline uses Parakeet for English recognition, TranslateGemma 12B for translation, Silero for speech detection, and Supertonic 3 for Spanish and Korean speech. Warm male is the default voice. Manual review and automatic publishing are available.

Continuous English speech can be divided at grammatical clause boundaries before a long sentence ends. The unfinished audio tail is retained. Recent published source context and language-specific guidance help translation. Recognition, meaning, and voice delivery can still fail; these controls do not certify accuracy.

Open **Church vocabulary** to define series titles and expressions in the church’s own words. Gather preserves matched names and adds a short explanation on their first mention in each language and service. Later mentions stay short. Listeners can open **Names & expressions** to look up meanings.

“Peopling” means learning how to relate to people, treat them well, and navigate relationships. Its Korean speech uses 피플링 while captions keep the English title. “DETOX” has no church-specific definition yet and receives no invented explanation. Other series titles require a series cue in the source or recent context. Exact aliases can handle known heard spellings; Gather does not silently guess misheard titles.

## Notes during and after a service

With the notes model installed and **Live sermon notes** enabled, Gather organizes published transcript passages into main points and supporting ideas during the message. Unpublished booth drafts stay private. Live notes are shared in the listener room and labeled as AI drafts.

A separate local process handles short source batches with throttled decoding. It attempts updates at intervals of at least 25 seconds during live speech; model load and generation can make them slower. It shares hardware with translation and adds load. Switch live notes off in the booth at any time to release that processing capacity while translation continues.

Search covers the outline and original transcript. Every point links to a source passage. New updates wait behind **Show latest** while someone is searching or reading, so content stays in place. English, Spanish, and Korean notes are available. Live-note translations use the notes model and need review; native-speaking reviewers have not certified them.

After the service, remaining published passages are processed and the notes are saved. Listeners can download the notes and source passages as a Markdown text file, during or after the message. The same notes link stays available afterward. Operators can also create a fuller outline from a completed transcript or Sermon Lab excerpt while audio is paused; those drafts stay private until shared.

## Development and verification

A development checkout needs Python 3.12, `requirements.txt`, FFmpeg, Node for livestream extraction, and models listed in `host/models.json`. `PARAKEET_MODEL_PATH` selects recognition weights; `GATHER_RUNTIME_DIR` selects session storage. Start a prepared checkout with `./start.sh`. The host installer handles these prerequisites for a new supported computer.

GitHub Actions checks setup, mobile layout, live-note updates, source access, and post-service downloads. It also runs a small real Qwen notes-model check and builds the Mac app. Synthetic cases are not an accuracy percentage or production latency benchmark.

Run regression checks without enabling live model generation:

```sh
GATHER_DISABLE_LIVE_NOTES=1 .venv/bin/python -m unittest discover -s proof -p 'test_*.py'
```

Earlier translator tests do not establish latency with the new live-notes process running. A full service with physical listener devices and native Spanish/Korean reviewers is still needed before treating this prototype as a production replacement for human interpretation.

See [THIRD_PARTY.md](THIRD_PARTY.md) for model provenance, licensing, and tool sources. Models download from upstream and are not redistributed here.
