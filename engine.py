"""Persistent local inference. No credentials, remote calls, or model auto-downloads."""
from pathlib import Path
import os
import re
import time
import numpy as np
import sentencepiece as spm
import ctranslate2
from spanish import source_language, is_mixed, translation_notes, detector, idiom_examples
from korean import guidance as korean_guidance, spoken_endings, normalize_clock, semantic_checks, audience_terms, TERMS as KOREAN_TERMS
from clause_gate import clause_cut, parser as clause_parser
from vocabulary import protect as protect_vocabulary, restore as restore_vocabulary

ROOT = Path(__file__).parent
LANGUAGES = {
    "es": {"name": "Español", "code": "spa_Latn", "voice": "Paulina"},
    "fr": {"name": "Français", "code": "fra_Latn", "voice": "Thomas"},
    "pt": {"name": "Português", "code": "por_Latn", "voice": "Luciana"},
    "ko": {"name": "한국어", "code": "kor_Hang", "voice": "Yuna"},
}

class Engine:
    def __init__(self):
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel
        self.mx, self.get_logmel = mx, get_logmel
        cache = Path.home() / ".cache/huggingface/hub/models--mlx-community--parakeet-tdt-0.6b-v3/snapshots"
        paths = sorted(p for p in cache.glob("*") if (p / "model.safetensors").exists())
        model = os.getenv("PARAKEET_MODEL_PATH") or (str(paths[0]) if paths else "")
        if not model:
            raise RuntimeError("A local Parakeet model is required. See README.md.")
        self.asr = from_pretrained(model)
        self.sp = spm.SentencePieceProcessor(model_file=str(ROOT / "models/nllb/sentencepiece.bpe.model"))
        self.mt = ctranslate2.Translator(str(ROOT / "models/nllb"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
        self.translation_model = "NLLB-600M"
        self.gemma = self.tokenizer = None
        model_path = Path(os.getenv("GATHER_TRANSLATION_MODEL_PATH", str(ROOT / "models/translategemma-12b")))
        if not (model_path / "model-00002-of-00002.safetensors").exists() and not (model_path / "model.safetensors").exists():
            model_path = ROOT / "models/translategemma"
        if any(model_path.glob("*.safetensors")):
            from mlx_lm import load
            self.gemma, self.tokenizer = load(str(model_path))
            self.tokenizer.add_eos_token("<end_of_turn>")
            self.translation_model = "TranslateGemma " + ("12B" if "12b" in str(model_path) else "4B") + " / Spanish + Korean"
        detector().compute_language_confidence_values("Welcome to church. Bienvenidos a la iglesia.")
        clause_parser()("God loves you and he has not forgotten you.")
        # Warm translation; recognition warms with the first actual audio chunk.
        self.translate("Welcome to church.", ["es","ko"], {"source_language":"en"})
        self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe(self, samples):
        begin = time.perf_counter()
        audio = self.mx.array(np.asarray(samples, dtype=np.float32))
        mel = self.get_logmel(audio, self.asr.preprocessor_config)
        result = self.asr.generate(mel)[0]
        return result.text.strip(), round((time.perf_counter() - begin) * 1000)

    def transcribe_window(self, samples, final=True, with_boundary=False):
        begin = time.perf_counter()
        audio = self.mx.array(np.asarray(samples, dtype=np.float32))
        result = self.asr.generate(self.get_logmel(audio, self.asr.preprocessor_config))[0]
        elapsed = round((time.perf_counter() - begin) * 1000)
        kind='phrase' if final else 'sentence'
        def output(text,carry):
            value=(text,elapsed,carry)
            return (*value,kind) if with_boundary else value
        if final:
            return output(result.text.strip(),np.zeros(0,dtype=np.float32))
        duration = len(samples) / 16000
        # Hold the unfinished tail of continuous speech rather than cut a word in two.
        safe_sentences = [s for s in result.sentences if 1 < s.end <= duration - .5 and re.search(r"[.!?]$", s.text.strip())]
        clause=clause_cut(result.tokens,duration)
        if clause and (not safe_sentences or clause<safe_sentences[-1].end):
            cut=clause
            kind='clause'
        elif safe_sentences:
            cut = safe_sentences[-1].end
        else:
            if duration < 12:
                # Spoken translation needs the clause, especially for verb-final Korean.
                # Keep fast provisional captions while waiting for a sentence boundary.
                return output("",samples)
            words = []
            for token in result.tokens:
                if not words or token.text.startswith(" "):
                    words.append([])
                words[-1].append(token)
            safe_words = [word for word in words[:-1] if word[-1].end <= duration - .5]
            cut = safe_words[-1][-1].end if safe_words else 0
        if cut <= 0:
            # Retain the audio to combine with the next window. Never discard speech.
            return output("",samples)
        tokens = [token for token in result.tokens if token.end <= cut + .001]
        following = next((token for token in result.tokens if token.end > cut + .001), None)
        boundary = (cut + max(cut,following.start)) / 2 if following else cut
        return output("".join(token.text for token in tokens).strip(),samples[int(boundary*16000):])

    def translate(self, text, languages, options=None):
        options = options or {}
        if options.get('vocabulary_settings') and not options.get('fast_preview'):
            masked, bindings = protect_vocabulary(text, options['vocabulary_settings'], options.get('context', ''))
            if bindings:
                values, elapsed = self.translate(masked, languages, {**options, 'vocabulary_settings':None, 'protected_vocabulary':bindings})
                return {lang:restore_vocabulary(value, bindings, lang) for lang,value in values.items()}, elapsed
        if self.gemma is None or options.get("fast_preview"):
            return self.translate_fast(text, languages, options)
        begin = time.perf_counter()
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        output = {}
        for lang in languages:
            if lang not in {"es", "ko"}:
                values, _ = self.translate_fast(text, [lang], options)
                output.update(values)
                continue
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
            translated = []
            # Keep Spanish sentences unchanged, including the speaker's regional slang.
            pending = []
            def render_pending():
                if not pending:
                    return
                original = " ".join(pending)
                source = source_language(original, options.get("source_language", "auto"))
                if is_mixed(original):
                    source = "en"
                target = "ko" if lang == "ko" else "es-MX" if options.get("spanish_region", "latin") == "mx" else "es"
                message = [{"role": "user", "content": [{"type": "text", "source_lang_code": source, "target_lang_code": target, "text": original}]}]
                notes, glossary = translation_notes(original, options.get("glossary"))
                examples = []
                protected = options.get('protected_vocabulary', [])
                for binding in protected:
                    marker = binding['marker']
                    examples += [{'role':'user','content':[{'type':'text','source_lang_code':'en','target_lang_code':target,'text':marker}]}, {'role':'assistant','content':marker}]
                    if binding['entry']['term'].casefold()=='peopling' and re.search(r'\b(?:are|is|am|not)\s+'+marker+r'\s+well\b',original,re.I):
                        example='We are not '+marker+' well.'
                        equivalent='우리는 '+marker+'을 잘 실천하고 있지 않아요.' if lang=='ko' else 'No estamos practicando '+marker+' bien.'
                        examples += [{'role':'user','content':[{'type':'text','source_lang_code':'en','target_lang_code':target,'text':example}]}, {'role':'assistant','content':equivalent}]
                if lang == "es":
                    # The native translation template is more reliable than extra sermon instructions.
                    # Short approved examples retain terminology without rewriting the source.
                    glossary = {key: value for key, value in glossary.items() if key in (options.get("glossary") or {}) or key in {"poor in spirit", "kingdom of heaven"}}
                    for english, spanish in idiom_examples(original, options.get("context", "") if options.get("pronoun_context", True) else ""):
                        examples += [{"role":"user","content":[{"type":"text","source_lang_code":"en","target_lang_code":target,"text":english}]},{"role":"assistant","content":spanish}]
                    previous = options.get("previous_translations", {}).get("es")
                    if previous:
                        examples += [{"role": "user", "content": [{"type": "text", "source_lang_code": "en", "target_lang_code": target, "text": previous[0][-350:]}]}, {"role": "assistant", "content": previous[1][-350:]}]
                    for key, value in glossary.items():
                        examples += [{"role": "user", "content": [{"type": "text", "source_lang_code": source, "target_lang_code": target, "text": key}]}, {"role": "assistant", "content": value}]
                prompt = self.tokenizer.apply_chat_template(examples + message, tokenize=False, add_generation_prompt=True)
                vocabulary_hint = ''
                if protected:
                    descriptions = '; '.join(b['marker']+' is the church title or expression '+b['entry']['term']+('. Its meaning is '+b['entry']['meaning'] if b['entry'].get('meaning') else '') for b in protected)
                    vocabulary_hint = ' Copy every GATHERVOCAB token exactly once in the translation, even when it is used as a verb. Do not translate it, omit it, or add a definition. Context: '+descriptions+'. '
                    prompt = prompt.replace('Produce only', vocabulary_hint+'Produce only', 1)
                if options.get('stream_clause') and lang=='es':
                    prompt=prompt.replace('Produce only','This is one clause of ongoing speech. Translate only this clause; do not finish the sentence or add explanations. Produce only',1)
                if lang == "ko":
                    # Translate-only Korean instructions; never ask the model to preach.
                    # Use English context, not previous Korean output as a new example.
                    prompt = prompt.replace("Produce only", korean_guidance(original, options) + "Produce only", 1)
                if len(self.tokenizer.encode(prompt)) > 1800:
                    raise ValueError("Translation context is too long; shorten this line.")
                result = []
                last = None
                for response in stream_generate(self.gemma, self.tokenizer, prompt=prompt, max_tokens=512, sampler=make_sampler(temp=0)):
                    result.append(response.text)
                    last = response
                if last is not None and last.finish_reason == "length":
                    raise ValueError("Translation hit its length limit; review and shorten the line.")
                rendered = "".join(result).strip()
                if lang == "ko":
                    rendered = normalize_clock(original, rendered, options.get("context", ""))
                    if options.get("korean_register", "spoken") == "spoken":
                        rendered = spoken_endings(rendered)
                    flags = semantic_checks(original, rendered, options.get("context", ""))
                    if flags:
                        # Concrete meaning failures get one direct-translation retry.
                        # Preserve matched names without the register/context instruction.
                        retry_examples = []
                        for english, korean in KOREAN_TERMS.items():
                            if re.search(r"\b"+re.escape(english)+r"\b", original, re.I):
                                retry_examples += [{"role": "user", "content": [{"type": "text", "source_lang_code": "en", "target_lang_code": "ko", "text": english}]}, {"role": "assistant", "content": korean}]
                        retry_prompt = self.tokenizer.apply_chat_template(retry_examples+message, tokenize=False, add_generation_prompt=True)
                        retry_prompt = retry_prompt.replace("Produce only", vocabulary_hint+korean_guidance(original, {**options,"compact_interpreter":False}) + "Produce only", 1)
                        retry = list(stream_generate(self.gemma, self.tokenizer, prompt=retry_prompt, max_tokens=512, sampler=make_sampler(temp=0)))
                        if retry and retry[-1].finish_reason == "length":
                            raise ValueError("Korean retry hit its length limit; review this line.")
                        rendered = normalize_clock(original, "".join(r.text for r in retry).strip(), options.get("context", ""))
                        if options.get("korean_register", "spoken") == "spoken":
                            rendered = spoken_endings(rendered)
                        flags = semantic_checks(original, rendered, options.get("context", ""))
                        if flags:
                            raise ValueError("Korean meaning needs review: "+" ".join(flags))
                    rendered = audience_terms(original, rendered, options.get("context", ""))
                if len(rendered) > max(120, len(original)*2.3):
                    raise ValueError("Translation expanded unusually. Review the source wording before publishing.")
                translated.append(rendered)
                pending.clear()
            for sentence in sentences:
                src = source_language(sentence, options.get("source_language", "auto"))
                if src == lang and not is_mixed(sentence):
                    render_pending()
                    translated.append(sentence)
                else:
                    pending.append(sentence)
            render_pending()
            output[lang] = " ".join(translated)
        return output, round((time.perf_counter() - begin) * 1000)

    def translate_fast(self, text, languages, options=None):
        options = options or {}
        begin = time.perf_counter()
        # NLLB is sentence-oriented: sending a paragraph can omit whole sentences.
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
        output = {lang: [None] * len(sentences) for lang in languages}
        pairs = []
        for lang in languages:
            for index, sentence in enumerate(sentences):
                src = source_language(sentence, options.get("source_language", "auto"))
                mixed = is_mixed(sentence)
                if src == lang and not mixed:
                    output[lang][index] = sentence
                else:
                    pairs.append((lang, index, sentence, "en" if mixed else src))
        # The converted model uses the legacy NLLB source layout (language at end).
        sources = [self.sp.encode(sentence, out_type=str) + ["</s>", {"es": "spa_Latn", "ko": "kor_Hang", "en": "eng_Latn"}[src]] for _, _, sentence, src in pairs]
        if not pairs:
            return {lang: " ".join(parts) for lang, parts in output.items()}, round((time.perf_counter() - begin) * 1000)
        results = self.mt.translate_batch(
            sources,
            target_prefix=[[LANGUAGES[lang]["code"]] for lang, _, _, _ in pairs],
            beam_size=2, max_decoding_length=512,
        )
        for (lang, index, _, _), result in zip(pairs, results):
            tokens = [t for t in result.hypotheses[0] if t not in {LANGUAGES[lang]["code"], "</s>", "<s>", "<pad>"}]
            output[lang][index] = self.sp.decode(tokens).strip()
        translations = {lang: " ".join(sentences) for lang, sentences in output.items()}
        return translations, round((time.perf_counter() - begin) * 1000)
