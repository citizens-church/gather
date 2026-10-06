"""Korean sermon register and meaning cues, conditional on the English source.

These are authored translation cues, not claims of native-speaker certification.
"""
import json
import re

TERMS = {"God": "하나님", "Jesus": "예수님", "Holy Spirit": "성령", "grace": "은혜", "faith": "믿음", "salvation": "구원", "repentance": "회개", "Habakkuk": "하박국", "the enemy": "원수"}
IDIOMS = {
    "beat yourself up": "스스로를 자책하다 (몸을 때린다는 뜻이 아님)",
    "give yourself some grace": "자신에게 조금 너그러워지다 (은혜를 준다는 직역이 아님)",
    "not too far gone": "아직 돌이킬 수 있다 / 아직 늦지 않았다",
    "too far gone": "돌이키기에는 너무 늦었다는 뜻 (물리적으로 멀다는 뜻이 아님)",
    "isn't done with you": "여러분 안에서 하시는 일이 아직 끝나지 않았다",
    "not done with you": "여러분 안에서 하시는 일이 아직 끝나지 않았다",
    "no cap": "진짜로 / 솔직히 (모자라는 뜻이 아님)",
    "hit different": "남다르게 와닿았다",
    "hot mess": "정말 엉망인 상태 (그저 힘들었다는 뜻으로 약화하지 말 것)",
    "put words in my mouth": "내가 하지 않은 말을 했다고 하다 (화자와 청자의 역할을 바꾸지 말 것)",
    "earn god's love": "하나님의 사랑을 받을 자격을 노력으로 얻다",
    "earn me more favor": "내 노력이나 공로로 하나님의 호의를 더 얻다",
    "living under the lies": "거짓말에 얽매여 살다",
    "hold to the truth": "진리를 붙들다",
    "on fire for god": "하나님을 향한 열정이 뜨겁다 (불에 탄다는 뜻이 아님)",
    "get your act together": "삶을 바로잡다 / 정신을 차리다",
    "drop the ball": "맡은 일을 놓치다 / 실수하다",
    "show up": "나타나다 / 함께하다 (문맥에 맞게)",
    "was laughing": "웃고 있었다 (그저 장난으로 말했다는 뜻으로 바꾸지 말 것)",
    "was kidding": "농담을 했다",
    "wasn't talking to the whole church": "교회 전체를 향해 말한 것이 아니었다 (교회에 대해 말한 것이 아니라는 뜻과 구별)",
}

def guidance(source, options):
    spoken = options.get("korean_register", "spoken") == "spoken"
    style = "따뜻한 존댓말 해요체를 일관되게 쓰세요. 불필요한 격식과 직역투를 피하세요." if spoken else "자연스러운 존댓말 합니다체를 일관되게 쓰세요."
    instruction = (
        "교회 설교의 영어 발화를 한국어로 직접 통역하세요. " + style + " "
        "청중을 부르는 friends와 you는 여러분 또는 생략, 실제 friend는 친구, 신앙 문맥의 the enemy는 원수입니다. "
        "개인 사이의 대화나 하나님께 하는 기도의 you를 청중 여러분으로 바꾸지 마세요. "
        "화자의 1인칭, 시제, 확신의 정도, 부정, 반복, 인용문, 숫자, 성경 구절을 보존하세요. "
        "원문에 없는 설명, 권면, 추측을 덧붙이지 마세요. 번역문만 출력하세요. "
    )
    if options.get("compact_interpreter", True):
        instruction = (
            "영어 설교를 자연스러운 한국어로 직접 통역하세요. " + style + " "
            "짧은 구어 표현을 쓰되 의미와 반복을 빠짐없이 보존하세요. "
            "1인칭, 시제, 부정, 확신, 인용문, 숫자와 성경 구절을 그대로 유지하세요. "
            "청중은 여러분, 개인 대화와 기도의 you는 상대방입니다. "
            "원문에 없는 설명과 권면을 덧붙이지 말고 번역만 출력하세요. "
        )
    lower = source.lower().replace("’", "'")
    if options.get('stream_clause'):
        instruction += "지금도 이어지는 발화의 한 절입니다. 이 절만 옮기고 아직 말하지 않은 뒷부분이나 설명을 만들어 문장을 완성하지 마세요. "
    idioms = {phrase: meaning for phrase, meaning in IDIOMS.items() if phrase in lower}
    if "i'm dead" in lower and re.search(r'\b(?:laugh|laughing|hilarious)\b',lower):
        idioms["I'm dead"]='너무 웃겨서 죽겠다 / 웃겨 죽겠어 (웃음의 과장 표현이며 실제 사망이 아님)'
    # The specific negative idiom wins over its positive substring.
    if "not too far gone" in idioms:
        idioms.pop("too far gone", None)
    terms = {term: value for term, value in TERMS.items() if re.search(r"\b"+re.escape(term)+r"\b", source, re.I)}
    if "give yourself some grace" in lower:
        terms.pop("grace", None)
    if terms:
        instruction += "용어: " + json.dumps(terms, ensure_ascii=False) + ". "
    if idioms:
        instruction += "원문에 있는 관용 표현의 의미(추가 내용이 아님): " + json.dumps(idioms, ensure_ascii=False) + ". "
    context = options.get("context", "")[-350:]
    if re.search(r"\bit['’]?s (?:gonna|going to) (?:be )?work",lower) and re.search(r'\b(?:translator|app|system|software)\b',context,re.I):
        instruction += "이 문맥의 it은 번역 프로그램이고 working은 작동한다는 뜻입니다. fast는 처리 속도가 빠르다는 뜻이지 일이 순조롭다는 뜻이 아닙니다. "
    if re.search(r"\bit['’]s not\s*(?:[,!?.]|\band\b|$)",source,re.I) and re.search(r"\btrue\b",source+' '+context,re.I):
        instruction += "이 문맥의 it's not은 '그건 사실이 아니야'라는 말이며, 불가능하다는 뜻이 아닙니다. "
    if re.search(r"\bi['’]?m done\b", source, re.I) and re.search(r"\b(?:lies|enemy)\b", source+" "+context, re.I):
        instruction += "이 문맥의 I'm done은 '이제 그만이에요'라는 단호한 선언으로, 거짓말에 얽매여 사는 일을 더는 하지 않겠다는 뜻입니다. "
    if context:
        instruction += "앞선 영어 발화는 대명사의 문맥 정보일 뿐이며 번역하거나 반복하지 마세요: " + json.dumps(context, ensure_ascii=False) + ". "
    return instruction

def spoken_endings(text):
    """Normalize only unambiguous, sentence-final polite endings.

    Quoted direct speech retains its register. Other verbs remain model output.
    """
    replacements = {"없습니다": "없어요", "있습니다": "있어요", "아닙니다": "아니에요", "셨습니다": "셨어요", "었습니다": "었어요", "았습니다": "았어요", "했습니다": "했어요", "였습니다": "였어요", "않으십니다": "않으세요", "않습니다": "않아요", "말씀드립니다": "말씀드려요", "바랍니다": "바라요", "것입니다": "거예요", "겁니다": "거예요", "됩니다": "돼요", "하겠습니다": "할게요", "않겠습니다": "않겠어요", "합니다": "해요", "하십니다": "하세요", "주십시오": "주세요", "마십시오": "마세요", "싶습니다": "싶어요", "믿습니다": "믿어요", "줍니다": "줘요"}
    # Don't change any span inside quoted dialogue or quotations from Scripture.
    spans = re.split(r'''("[^"]*"|“[^”]*”|‘[^’]*’|'[^']*'|「[^」]*」|『[^』]*』)''', text)
    for index in range(0, len(spans), 2):
        for original, replacement in replacements.items():
            spans[index] = re.sub(re.escape(original)+r"(?=[.!?。](?:\s|$)|$)", replacement, spans[index])
        def copula(match):
            syllable = match[1]
            return syllable + ("이에요" if (ord(syllable)-0xAC00)%28 else "예요")
        spans[index] = re.sub(r"([가-힣])입니다(?=[.!?。](?:\s|$)|$)", copula, spans[index])
        spans[index] = re.sub(r"십시오(?=[.!?。](?:\s|$)|$)", "세요", spans[index])
    return "".join(spans)

def normalize_clock(source, text, context=""):
    evidence = (source + " " + context).lower()
    explicit_period = re.search(r"\b(?:a\.?m\.?|p\.?m\.?|morning|afternoon|evening|tonight|noon|midnight)\b", evidence)
    if not explicit_period and re.search(r"\b\d{1,2}:\d{2}\b", source):
        text = re.sub(r"(?:오전|오후|아침|저녁)\s*(?=\d{1,2}\s*시\s*\d{1,2}\s*분)", "", text)
    if re.search(r"\b\d{1,2}:\d{2}\b", source) and not re.search(r"\b(?:today|tonight|this morning|this afternoon|this evening)\b", evidence):
        text = re.sub(r"(?<![가-힣])오늘(?:의)?\s*", "", text)
    return text

def semantic_checks(source, target, context=""):
    flags = []
    if re.search(r"\b(?:nothing\b.*\bcan|cannot|can't)\b", source, re.I) and not re.search(r"\b(?:need|have to)\b", source, re.I) and "필요" in target:
        flags.append("Korean changed inability into lack of obligation.")
    if not re.search(r"\b(?:might|maybe|perhaps|seem|seems|think|probably|could|wonder|hopefully|hope|hoping)\b", source+" "+context, re.I) and re.search(r"것\s*같|듯\s*해|아마|지도\s*모르", target):
        flags.append("Korean added uncertainty to a definite statement.")
    if re.search(r"\bwait for it\b.*\bit won['’]?t be late\b", source, re.I) and not re.search(r"\b(?:yet|time)\b", source, re.I) and re.search(r"(?:때|시간).*아직|아직.*(?:때|시간)", target):
        flags.append("Korean added timing commentary absent from the source.")
    if re.search(r"\bit['’]s not\s*(?:[,!?.]|\band\b|$)", source, re.I) and re.search(r"\btrue\b", source+" "+context, re.I) and re.search(r"안\s*되|안\s*될|불가능", target):
        flags.append("Korean changed denial of truth into impossibility.")
    if re.search(r"\bearn me more favor\b",source,re.I) and re.search(r"드릴|드리|베풀|바칠|바치",target):
        flags.append("Korean changed receiving favor into giving it.")
    if re.search(r"\b(?:gonna|going to|will)\b", source, re.I) and not re.search(r"\b(?:want|wish|would like)\b", source, re.I) and "싶" in target:
        flags.append("Korean changed stated intention into a wish.")
    if "remember" not in source.lower() and re.search(r"기억해\s*주세요", target):
        flags.append("Korean added a request to remember absent from the source.")
    if re.search(r"\b(?:was|were) laughing\b", source, re.I) and not re.search(r"웃|깔깔|껄껄|하하", target):
        flags.append("Korean omitted the stated laughing action.")
    if re.search(r"\bi found God\b", source, re.I) and not re.search(r"찾|발견|만나|만났|알게|영접", target):
        flags.append("Korean omitted finding God or substituted a different claim.")
    if re.search(r"wasn['’]t talking to the whole church",source,re.I) and re.search(r"교회\s*전체에?\s*(?:대해|관해)",target):
        flags.append("Korean changed addressing the church into talking about the church.")
    return flags

def audience_terms(source, target, context=""):
    """Keep congregation address consistent outside direct quotations.

    Personal dialogue and prayer are excluded. The noun 'friend' stays a friend.
    """
    personal = re.search(r"\b(?:wife|husband|son|daughter|mother|father|prayer|prayed|praying|told God|said to God|dear God)\b", source+" "+context, re.I)
    military = re.search(r"\b(?:army|troops|soldiers|battlefield)\b", source, re.I)
    spans = re.split(r'''("[^"]*"|“[^”]*”|‘[^’]*’|'[^']*'|「[^」]*」|『[^』]*』)''', target)
    for index in range(0, len(spans), 2):
        if re.search(r'\bGod\b',source,re.I) and not re.search(r'\bCatholic|ha[- ]?neu[- ]?nim|haneunim\b',source+' '+context,re.I):
            spans[index] = spans[index].replace('하느님','하나님')
        if not personal:
            spans[index] = re.sub(r"(?<![가-힣])당신", "여러분", spans[index])
        if not military and re.search(r"\bthe enemy\b", source, re.I):
            for before, after in {"적에게": "원수에게", "적으로": "원수로", "적이": "원수가", "적은": "원수는", "적을": "원수를", "적의": "원수의", "적과": "원수와", "적도": "원수도"}.items():
                spans[index] = re.sub(r"(?<![가-힣])"+before, after, spans[index])
    return "".join(spans)
