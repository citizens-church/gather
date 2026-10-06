# Third-party software and models

Gather downloads model weights directly from their pinned upstream repositories. It does not include model weights or church recordings in the GitHub repository or host app download. See `host/models.json` for exact revisions, filenames, sizes and SHA-256 checksums.

| Component | Upstream | Terms |
| --- | --- | --- |
| TranslateGemma 12B | [Google](https://huggingface.co/google/translategemma-12b-it), [MLX conversion](https://huggingface.co/mlx-community/translategemma-12b-it-4bit) | Gemma terms |
| Parakeet TDT v3 | [NVIDIA](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [MLX conversion](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3) | CC-BY-4.0 |
| Supertonic 3 | [Archive](https://huggingface.co/supertone-oss-archive/supertonic-3) | OpenRAIL-M weights, MIT SDK |
| NLLB provisional captions | [Meta](https://huggingface.co/facebook/nllb-200-distilled-600M), [INT8 conversion](https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8) | CC-BY-NC-4.0; noncommercial research use |
| Qwen3 4B notes | [MLX conversion](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit) | Apache-2.0 |
| Silero VAD v6.2 | [Source](https://github.com/snakers4/silero-vad/tree/v6.2) | MIT |
| spaCy English pipeline | [Source](https://github.com/explosion/spacy-models) | MIT |

The host app includes the unmodified [uv 0.12.17 executable](https://github.com/astral-sh/uv/releases/tag/0.12.17). uv is dual licensed under [MIT](https://github.com/astral-sh/uv/blob/0.12.17/LICENSE-MIT) and [Apache-2.0](https://github.com/astral-sh/uv/blob/0.12.17/LICENSE-APACHE). Those license files are included with the app. Python and Python dependencies are downloaded at installation time; each retains its upstream license.

The installer downloads FFmpeg and FFprobe directly from the pinned [ffmpeg-static b6.1.1 upstream release](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1), verifies them, and saves the accompanying license and build README in the host computer’s `Gather/notices` directory. These tools are not bundled in the Gather app. FFmpeg's source and build provenance are described in that release's `darwin-arm64.README` and the [upstream build repository](https://github.com/eugeneware/ffmpeg-static). Node.js is downloaded from [nodejs.org](https://nodejs.org/) with its published checksum, and its license is saved locally.

This prototype has been prepared for noncommercial church use. Model-specific restrictions apply independently of Gather’s source.
