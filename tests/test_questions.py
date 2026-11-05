# -*- coding: utf-8 -*-
import unittest
from jarvisd.questions import QuestionBox, Question, Option


class QuestionsTests(unittest.TestCase):
    def test_direct_option_and_ordinal_matching(self):
        qbox = QuestionBox()
        action_called = []
        q = Question(
            qid="test_close",
            text="Photoshop kapatılsın mı?",
            options=[
                Option("kapat", "Kapat", run=lambda: action_called.append("kapat"), words=["kapat", "sonlandır"], polarity="yes"),
                Option("dokunma", "Dokunma", run=lambda: action_called.append("dokunma"), words=["dokunma", "kalsın"], polarity="no")
            ],
            default="dokunma"
        )
        qbox.items.append(q)

        # 1. Doğrudan kelimeyle eşleşme
        key, uncertain = qbox.interpret(q, "lütfen kapat")
        self.assertFalse(uncertain)
        self.assertEqual(key, "kapat")

        # 2. Evet/hayır kutup eşleşmesi
        key, uncertain = qbox.interpret(q, "evet")
        self.assertFalse(uncertain)
        self.assertEqual(key, "kapat")

        key, uncertain = qbox.interpret(q, "hayır istemiyorum")
        self.assertFalse(uncertain)
        self.assertEqual(key, "dokunma")

        # 3. Sıra numarası (1 veya ilk)
        key, uncertain = qbox.interpret(q, "1")
        self.assertFalse(uncertain)
        self.assertEqual(key, "kapat")

        key, uncertain = qbox.interpret(q, "ikincisi")
        self.assertFalse(uncertain)
        self.assertEqual(key, "dokunma")

    def test_answer_executes_action_and_clears_pending(self):
        action_called = []
        qbox = QuestionBox()
        q = Question(
            qid="test_close",
            text="Uygulama kapatılsın mı?",
            options=[
                Option("kapat", "Kapat", run=lambda: action_called.append("kapat"), polarity="yes"),
                Option("dokunma", "Dokunma", run=lambda: action_called.append("dokunma"), polarity="no")
            ]
        )
        qbox.items.append(q)

        handled, reply = qbox.answer("kapat")
        self.assertTrue(handled)
        self.assertIn("Anladım: Kapat", reply)
        self.assertEqual(action_called, ["kapat"])
        self.assertIsNone(qbox.pending())

    def test_ambiguous_answer_leaves_question_safe(self):
        action_called = []
        qbox = QuestionBox()
        q = Question(
            qid="test_close",
            text="Kapatılsın mı?",
            options=[
                Option("kapat", "Kapat", run=lambda: action_called.append("kapat")),
                Option("dokunma", "Dokunma", run=lambda: action_called.append("dokunma"))
            ]
        )
        qbox.items.append(q)

        handled, reply = qbox.answer("bugün hava nasıl?", use_ai=False)
        self.assertTrue(handled)
        self.assertIn("tam anlayamadım", reply)
        self.assertEqual(action_called, [])
        self.assertIsNotNone(qbox.pending())


if __name__ == "__main__":
    unittest.main()
