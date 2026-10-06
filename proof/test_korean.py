import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from korean import guidance, spoken_endings, normalize_clock, semantic_checks, audience_terms
from natural_voice import select_speed, identity
from sermon_metrics import review_flags

class KoreanTests(unittest.TestCase):
    def test_source_specific_idioms_and_negative_meaning(self):
        prompt = guidance("You're not too far gone. Give yourself some grace.", {})
        self.assertIn("아직 늦지 않았다", prompt)
        self.assertNotIn("물리적으로 멀다", prompt)
        self.assertNotIn('"grace": "은혜"', prompt)
        self.assertIn("너그러워", prompt)
        self.assertNotIn("모자", prompt)
        self.assertIn('웃겨 죽겠어',guidance("He was laughing when he said, 'I'm dead.'",{}))
        self.assertNotIn('웃겨 죽겠어',guidance("He said, 'I'm dead to sin.'",{}))

    def test_short_spoken_endings_and_quotes(self):
        self.assertEqual(spoken_endings("하나님은 여러분을 잊지 않으셨습니다. 늦지 않았어요."), "하나님은 여러분을 잊지 않으셨어요. 늦지 않았어요.")
        for quoted in ('"그곳에 있습니다."', '“그곳에 있습니다.”', "'그곳에 있습니다.'", '「그곳에 있습니다.」'):
            self.assertEqual(spoken_endings(quoted+" 희망이 있습니다."), quoted+" 희망이 있어요.")
        self.assertEqual(spoken_endings("그것은 선물입니다. 중요합니다. 그분께서 말씀하십니다."), "그것은 선물이에요. 중요해요. 그분께서 말씀하세요.")
        self.assertFalse(review_flags("No cap, I was a hot mess.", "솔직히, 저는 정말 엉망이었어요.", "ko"))
        self.assertTrue(review_flags("No cap, God hasn't forgotten you.", "솔직히, 하나님은 여러분을 잊으셨어요.", "ko"))
        self.assertTrue(review_flags("Nothing can earn me more favor.", "아무것도 나를 더 많은 호의를 얻을 수 있어요.", "ko"))

    def test_no_guessed_am_pm(self):
        self.assertEqual(normalize_clock("Meet at 7:30.", "모임은 오후 7시 30분이에요."), "모임은 7시 30분이에요.")
        self.assertEqual(normalize_clock("Meet at 7:30.", "오늘 모임은 오후 7시 30분이에요."), "모임은 7시 30분이에요.")
        self.assertEqual(normalize_clock("Meet at 7:30 pm.", "모임은 오후 7시 30분이에요."), "모임은 오후 7시 30분이에요.")
        self.assertEqual(normalize_clock("Meet at 7:30.", "모임은 오후 7시 30분이에요.", "Tonight we will meet."), "모임은 오후 7시 30분이에요.")
        self.assertFalse(review_flags("Read John 3:16, not John 3:17.", "요한복음 3장 16절이지, 17절은 아니에요.", "ko"))
        self.assertTrue(review_flags("Read John 3:17.", "요한복음 3장 16절, 로마서 9장 17절이에요.", "ko"))

    def test_pacing_only_speeds_up_over_budget_korean(self):
        self.assertEqual(select_speed(5, 6, "ko"), 1.05)
        self.assertEqual(select_speed(40, 30, "es"), 1.25)
        self.assertEqual(select_speed(40, 0, "ko"), 1.05)
        self.assertEqual(select_speed(40, float("nan"), "ko"), 1.05)
        self.assertEqual(select_speed(41, 30, "ko"), 1.33)
        self.assertEqual(select_speed(90, 30, "ko"), 1.35)
        self.assertNotEqual(identity("ko", speed=1.05), identity("ko", speed=1.33))

    def test_concrete_meaning_changes_are_flagged(self):
        self.assertTrue(semantic_checks("Nothing can earn me more favor.", "더 얻을 필요는 없어요."))
        self.assertFalse(semantic_checks("You don't have to earn it.", "그럴 필요는 없어요."))
        self.assertTrue(semantic_checks("That tells me who I am.", "제 정체성을 알려주는 것 같아요."))
        self.assertFalse(semantic_checks("I think that tells me who I am.", "제 정체성을 알려주는 것 같아요."))
        self.assertFalse(semantic_checks("Hopefully it works.", "작동할 것 같아요."))
        self.assertTrue(semantic_checks("Wait for it. It won't be late.", "때가 아직 되지 않았지만, 늦지 않을 거예요."))
        self.assertTrue(semantic_checks("The enemy says it's not, and you messed up.", "원수가 안 될 거야라고 말해요.", "God's word is true."))
        self.assertTrue(semantic_checks("The enemy says it's not and you messed up.", "원수가 안 될 거야라고 말해요.", "God's word is true."))
        self.assertTrue(semantic_checks("Nothing can earn me more favor.","하나님의 은혜를 더 드릴 수 없어요."))
        self.assertFalse(semantic_checks("Nothing can earn me more favor.","은혜를 더 얻을 수 없어요."))
        self.assertTrue(semantic_checks("I'm gonna hold to the truth.", "진리를 붙잡고 싶어요."))
        self.assertFalse(semantic_checks("I want to hold to the truth.", "진리를 붙잡고 싶어요."))
        self.assertTrue(semantic_checks("Read John 3:16, not John 3:17.", "17절이 아니라는 점, 꼭 기억해 주세요."))
        self.assertTrue(semantic_checks("He was laughing, not describing a death.", "농담이었고, 죽음을 뜻한 게 아니었어요."))
        self.assertTrue(semantic_checks("She wasn't talking to the whole church.", "교회 전체에 대해 말씀하신 것이 아니었어요."))

    def test_finding_god_does_not_become_personal_worth(self):
        self.assertTrue(semantic_checks("God brought I found God. No, no, no.", "하나님께 제가 귀한 분임을 깨달았어요. 아니요, 아니요, 아니요."))
        self.assertFalse(semantic_checks("I found God.", "하나님을 만났어요."))
        self.assertFalse(semantic_checks("God says I am valuable.", "하나님께 제가 귀한 분임을 깨달았어요."))

    def test_audience_terms_preserve_quotes_and_personal_dialogue(self):
        self.assertEqual(audience_terms('The enemy tells you, "You failed."', '적이 당신에게 말해요. “당신은 실패했어.”'), '원수가 여러분에게 말해요. “당신은 실패했어.”')
        self.assertEqual(audience_terms("My wife told me she loves you.", "아내가 당신을 사랑한다고 했어요."), "아내가 당신을 사랑한다고 했어요.")
        self.assertEqual(audience_terms("The enemy army advanced.", "적이 진격했어요."), "적이 진격했어요.")
        self.assertEqual(audience_terms("He sent that friend.", "그 친구를 보내셨어요."), "그 친구를 보내셨어요.")
        self.assertEqual(audience_terms("I trust you.", "당신을 믿어요.", "I said to God:"), "당신을 믿어요.")
        self.assertEqual(audience_terms('God is working.','하느님이 일하세요.'),'하나님이 일하세요.')
        self.assertEqual(audience_terms('Catholics use this name for God.','하느님이라고 해요.'),'하느님이라고 해요.')

if __name__ == "__main__":
    unittest.main()
