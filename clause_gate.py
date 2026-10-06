"""Local grammatical boundaries for incremental English interpretation.

These are conservative parsing heuristics, not a semantic confidence score.
Audio and source words beyond the chosen boundary remain in the carry buffer.
"""
from functools import lru_cache
import re

@lru_cache(maxsize=1)
def parser():
    import spacy
    return spacy.load('en_core_web_sm', exclude=['ner'])

def clause_cut(tokens, duration):
    if duration < 2.5 or not tokens:
        return 0
    text=''.join(t.text for t in tokens)
    doc=parser()(text)
    offsets=[]
    position=0
    for t in tokens:
        position+=len(t.text)
        offsets.append((position,t.end))
    words=[t for t in doc if not t.is_space and not t.is_punct]
    if len(words)<4:
        return 0
    mandatory={'dobj','pobj','attr','acomp','aux','auxpass','neg','prt','det','prep','compound','amod','nummod','poss','case','ccomp','xcomp','relcl'}
    candidates=[]
    for index,last in enumerate(words[:-1]):
        end=last.idx+len(last)
        following=words[index+1]
        mapped=[seconds for char,seconds in offsets if char==end or (char>end and not text[end:char].strip(' ,;:—–'))]
        if not mapped or mapped[0]>duration-.5 or mapped[0]<1:
            continue
        natural=bool(re.search(r'[,;:—–]',text[end:following.idx])) or following.lower_ in {'and','but','so','yet','because','when','while'}
        # During a long uninterrupted stretch, consider a complete predicate too.
        if not natural and duration<4:
            continue
        if last.pos_ in {'DET','ADP','CCONJ','SCONJ','AUX'} and last.dep_!='prt':
            continue
        if last.lower_ in {'not','never','no','to','that','my','your','our','their'}:
            continue
        if following.lower_ in {'not','never','no','unless','if','except','only'}:
            continue
        left=[t for t in words if t.idx<end]
        if len(left)<3:
            continue
        # A boundary must not split a known subject/predicate, object or modifier.
        if any(t.idx>=end and t.head.idx<end and t.dep_ in mandatory for t in words):
            continue
        if any(t.idx<end and t.head.idx>=end and t.dep_ not in {'cc','punct','mark'} for t in words):
            continue
        prefix=parser()(text[:end])
        roots=[t for t in prefix if t.dep_=='ROOT']
        root=roots[0] if roots else None
        if root is None or root.pos_ not in {'VERB','AUX','ADJ'}:
            continue
        finite=root.tag_ in {'VBP','VBZ','VBD','MD'} or any(c.pos_=='AUX' and c.tag_ in {'VBP','VBZ','VBD','MD'} for c in root.children)
        first=next((t for t in prefix if not t.is_space and not t.is_punct),None)
        imperative=root.tag_=='VB' and first is not None and first.lower_ not in {'to','when','if','because','although'} and not any(t.dep_ in {'nsubj','nsubjpass'}for t in prefix)
        if not finite and not imperative:
            continue
        if not imperative and not any(t.dep_ in {'nsubj','nsubjpass','expl'} for t in prefix):
            continue
        candidates.append(mapped[0])
    # Drain available clauses in small groups, so fast speech cannot accumulate
    # an ever-growing audio tail when multiple clauses arrive per check.
    short=[cut for cut in candidates if cut<=4]
    return max(short) if short else min(candidates) if candidates else 0
