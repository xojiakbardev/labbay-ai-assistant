"""Is the conversation over? Cases for the closing decision (app/ai/closing.py).

Each case is the tail of a conversation and whether its last customer message
ends it. The expensive mistake is calling a conversation over when it isn't —
the customer then gets a reaction instead of an answer.

    python -m evals.run closing
"""
from dataclasses import dataclass


@dataclass
class ClosingCase:
    key: str
    #: (sender, text) pairs, oldest first; sender is "shop" or "customer".
    turns: list[tuple[str, str]]
    finished: bool


CLOSING_CASES = [
    ClosingCase(
        "ok_after_goodbye",
        [("shop", "Rahmat! Hamkasbim tez orada bog'lanadi."), ("customer", "hop")],
        finished=True,
    ),
    ClosingCase(
        "thanks_after_door_left_open",
        [("shop", "Albatta, shoshilmang. Savol tug'ilsa, yozavering."), ("customer", "rahmat")],
        finished=True,
    ),
    ClosingCase(
        "thumbs_up_after_confirmation",
        [("shop", "Buyurtmangiz qabul qilindi, ertaga yetkazamiz."), ("customer", "👍")],
        finished=True,
    ),
    ClosingCase(
        "answers_a_size_question",
        [("shop", "Qaysi razmer kerak? M, L yoki XL?"), ("customer", "M")],
        finished=False,
    ),
    ClosingCase(
        "yes_to_an_offer",
        [("shop", "Premium Black Hoodie 319 000 so'm. Olasizmi?"), ("customer", "ha")],
        finished=False,
    ),
    ClosingCase(
        "backs_out_of_the_purchase",
        [
            ("shop", "Zo'r, M razmer. Hamkasbim shu raqamga qo'ng'iroq qiladi."),
            ("customer", "Ha mayli olgim keme qoldi"),
        ],
        finished=False,
    ),
    ClosingCase(
        "price_objection",
        [("shop", "Nike Air Max 90 — 780 000 so'm."), ("customer", "qimmat ekan")],
        finished=False,
    ),
    ClosingCase(
        "russian_changed_mind",
        [("shop", "Отлично, передам менеджеру."), ("customer", "не надо, передумал")],
        finished=False,
    ),
    ClosingCase(
        "russian_thanks_after_goodbye",
        [("shop", "Спасибо! Менеджер скоро свяжется с вами."), ("customer", "спасибо")],
        finished=True,
    ),
]
