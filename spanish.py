"""Spanish routing and meaning notes. Source transcript is always retained verbatim."""
import re
from functools import lru_cache
from lingua import Language, LanguageDetectorBuilder

@lru_cache(maxsize=1)
def detector():
    return LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.SPANISH).build()

def source_language(text, preference="auto"):
    if preference in {"en", "es", "ko"}:
        return preference
    if re.search(r"[가-힣]", text):
        return "ko"
    values = detector().compute_language_confidence_values(text)
    # Ambiguous short phrases use English; the booth can explicitly choose Spanish.
    return "es" if values and values[0].language == Language.SPANISH and values[0].value >= .65 else "en"

def is_mixed(text):
    if source_language(text) == "es" and re.search(r"\b(?:don['’]t|isn['’]t|can['’]t|won['’]t|we['’]re|you['’]re|I['’]m|I['’]ll|we['’]ve)\b", text, re.I):
        return True
    spans = detector().detect_multiple_languages_of(text)
    if len({item.language for item in spans if item.word_count >= 2}) > 1:
        return True
    # Whole-line language detection can miss a Spanish-dominant Spanglish line.
    words = re.findall(r"[\w'’]+", text)
    found = set()
    for index in range(max(0, len(words) - 2)):
        values = detector().compute_language_confidence_values(" ".join(words[index:index+3]))
        threshold = .80 if values and values[0].language == Language.ENGLISH else .90
        if values and values[0].value >= threshold:
            found.add(values[0].language)
    return len(found) > 1

MEANING_NOTES = {
    "no cap": "'no cap' means 'honestly / I'm not exaggerating', not a hat or a limit",
    "hit different": "'hit different' means 'had a different emotional impact', not physical hitting",
    "hits different": "'hits different' means 'has a different emotional impact', not physical hitting",
    "hot mess": "'hot mess' means 'a person whose life is in chaos', not temperature",
    "give yourself some grace": "'give yourself some grace' means 'be patient and compassionate with yourself': 'ten paciencia y compasión contigo mismo'",
    "beat yourself up": "'beat yourself up' means 'criticize or blame yourself harshly', unless the speaker explicitly describes physical violence",
    "done with you": "In 'God isn't done with you', 'done with you' means 'finished working in your life': 'Dios todavía está obrando en tu vida'",
    "too far gone": "'too far gone' means 'beyond hope or recovery', not physical distance",
    "on fire for god": "'on fire for God' means 'passionate about God', not a literal fire",
    "no manches": "Mexican 'no manches' expresses surprise or disbelief, not stains",
    "agüitado": "Mexican 'agüitado' means sad or discouraged, not physically exhausted",
    "chamba": "Mexican 'chamba' means job or work",
    "qué padre": "Mexican 'qué padre' means 'how cool / how great', not a father",
    "échale ganas": "'échale ganas' encourages someone to put in effort and keep going",
    "hagas bolas": "Mexican 'no te hagas bolas' means 'don't get confused / overcomplicate it'",
}

CHURCH_TERMS = {
    "grace": "gracia", "faith": "fe", "salvation": "salvación",
    "Holy Spirit": "Espíritu Santo", "repentance": "arrepentimiento",
    "poor in spirit": "pobres en espíritu", "kingdom of heaven": "reino de los cielos",
    "blessed are": "bienaventurados", "Habakkuk": "Habacuc",
}

def idiom_examples(text, context=""):
    """Short translation examples for idioms actually present in this source."""
    lower = text.lower().replace('’', "'")
    pairs = [
        ('not too far gone', "You're not too far gone.", 'Todavía hay esperanza para ti.'),
        ('too far gone', "You're too far gone.", 'Ya no tienes remedio.'),
        ('give yourself some grace', 'Give yourself some grace.', 'Ten un poco de paciencia contigo mismo.'),
        ("isn't done with you", "God isn't done with you yet.", 'Dios todavía está obrando en tu vida.'),
        ('no cap', 'No cap.', 'De verdad.'),
        ('hit different', 'That hit different.', 'Eso me llegó de otra manera.'),
        ('hot mess', 'I was a hot mess.', 'Mi vida era un desastre.'),
        ('beat yourself up', "Don't beat yourself up.", 'No te castigues tanto.'),
    ]
    selected=[(source,target) for phrase,source,target in pairs if phrase in lower and not (phrase=='too far gone' and 'not too far gone' in lower)]
    if "i'm dead" in lower and re.search(r'\b(?:laugh|laughing|hilarious)\b',lower):
        selected.append(("I'm dead. That's hilarious.",'Me muero de risa. Eso es muy gracioso.'))
    if re.search(r"\bit['’]?s (?:gonna|going to) (?:be )?work",lower) and re.search(r'\b(?:translator|app|system|software)\b',context,re.I):
        selected.append(("This is our translator. It's going to work at church on Sundays.",'Este es nuestro traductor. Va a funcionar en la iglesia los domingos.'))
    return selected

def translation_notes(text, glossary=None):
    lower = text.casefold()
    phrases = [phrase for phrase in MEANING_NOTES if phrase in lower]
    notes = [MEANING_NOTES[phrase] for phrase in phrases]
    literal_text = lower
    for phrase in phrases:
        literal_text = literal_text.replace(phrase, "")
    matches = {key: value for key, value in {**CHURCH_TERMS, **(glossary or {})}.items() if re.search(r"(?<!\w)" + re.escape(key.casefold()) + r"(?!\w)", literal_text)}
    return notes, matches

def validate_translation(source, translated):
    flags = []
    # These are concrete checks, not a confidence or an accuracy percentage.
    for reference in re.findall(r"\b\d{1,3}:\d{1,3}(?:[-–]\d{1,3})?", source):
        numbers = re.findall(r"\d+", reference)
        localized = bool(re.search(re.escape(numbers[0]) + r"\s*(?:장|시)\s*" + re.escape(numbers[1]) + r"\s*(?:절|분)", translated))
        if not localized and len(numbers) == 2:
            # Korean can say 'John chapter 3 verse 16, not verse 17'.
            # Accept the shortened verse only under its nearest preceding chapter.
            for verse in re.finditer(r"(?<!\d)"+re.escape(numbers[1])+r"\s*절", translated):
                chapters = list(re.finditer(r"(?<!\d)(\d{1,3})\s*장", translated[:verse.start()]))
                if chapters and chapters[-1][1] == numbers[0]:
                    localized = True
                    break
        if len(numbers) == 3:
            localized = localized and bool(re.search(re.escape(numbers[2]) + r"\s*절", translated))
        if reference not in translated and not localized:
            flags.append("A Scripture reference or time needs checking: " + reference)
    if re.search(r"<[^>]+>", translated):
        flags.append("Translation contains model control text.")
    if not translated.strip():
        flags.append("Translation is empty.")
    if len(source.split()) >= 12 and len(translated.split()) < len(source.split()) * .35:
        flags.append("Translation may have omitted part of the line.")
    return flags
