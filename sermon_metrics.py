"""Reference comparisons. Similarity and concrete checks are not semantic grades."""
from collections import Counter
import re
import unicodedata
from spanish import validate_translation

def words(text):
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text)

def alignment(reference, hypothesis):
    a, b = words(reference), words(hypothesis)
    rows = [[(j, 0, 0, j) for j in range(len(b)+1)]]
    for i, x in enumerate(a, 1):
        row = [(i, 0, i, 0)]
        for j, y in enumerate(b, 1):
            if x == y:
                row.append(rows[-1][j-1])
            else:
                cost, s, d, n = rows[-1][j-1]
                candidates = [(cost+1, s+1, d, n)]
                cost, s, d, n = rows[-1][j]
                candidates.append((cost+1, s, d+1, n))
                cost, s, d, n = row[-1]
                candidates.append((cost+1, s, d, n+1))
                row.append(min(candidates, key=lambda v: v[0]))
        rows.append(row)
    distance, substitutions, deletions, insertions = rows[-1][-1]
    return {"wer": round(distance/len(a), 4) if a else None, "reference_words": len(a), "substitutions": substitutions, "deletions": deletions, "insertions": insertions,
            "normalization": "NFKC, lowercase, punctuation removed, contractions retained; numbers are not rewritten"}

def character_similarity(reference, hypothesis):
    """chrF-style character n-gram F2, orders 1..6, spaces removed; 0..100."""
    a, b = (re.sub(r"\s+", "", unicodedata.normalize("NFKC", x).lower()) for x in (reference, hypothesis))
    if not a:
        return None
    scores = []
    for n in range(1, min(6, len(a))+1):
        ra = Counter(a[i:i+n] for i in range(len(a)-n+1))
        hb = Counter(b[i:i+n] for i in range(max(0, len(b)-n+1)))
        common = sum((ra & hb).values())
        precision = common/sum(hb.values()) if hb else 0
        recall = common/sum(ra.values())
        scores.append(5*precision*recall/(4*precision+recall) if precision+recall else 0)
    return round(100*sum(scores)/len(scores), 2)

def review_flags(source, target, language):
    flags = validate_translation(source, target)
    if len(target) > max(120, len(source)*2.3):
        flags.append("Translation expanded unusually; check for ideas absent from the source.")
    # 'No cap' means honestly; it is not a negative proposition.
    negative_source = re.sub(r"\bno cap\b", "honestly", source, flags=re.I)
    source_negative = bool(re.search(r"\b(?:not|never|no|nothing|nobody|neither|without|cannot|can't|won't|don't|doesn't|isn't|hasn't|haven't|wasn't|wouldn't|shouldn't)\b", negative_source, re.I))
    target_negative = bool(re.search(r"\b(?:no|nunca|jamás|nada|nadie|ninguno|ninguna|ni|sin|tampoco)\b", target, re.I)) if language == "es" else bool(re.search(r"않|못|없|아니|\b안\b", target))
    if source_negative and not target_negative:
        flags.append("Check negation against the audio; no common negative expression was detected.")
    if not target.strip():
        return list(dict.fromkeys(flags))
    if language == "ko" and not re.search(r"[가-힣]", target):
        flags.append("No Korean Hangul detected.")
    if language == "es" and re.search(r"[가-힣]", target):
        flags.append("Unexpected Korean in Spanish output.")
    return list(dict.fromkeys(flags))
