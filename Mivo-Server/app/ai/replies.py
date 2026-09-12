"""Ready-made replies sent without the model. Defaults here; a business can
override any of them on the AI settings page (businesses.reply_texts)."""

LANGUAGES = ("uz", "ru", "en")
MAX_REPLY_CHARS = 500

DEFAULT_REPLIES: dict[str, dict[str, str]] = {
    # The conversation goes to a person.
    "handoff": {
        "uz": "Tushunarli, ma'lumotingizni operatorimizga uzatdim — tez orada siz bilan bog'lanishadi.",
        "ru": "Хорошо, я передал(а) информацию нашему оператору — он скоро свяжется с вами.",
        "en": "Got it — I've passed this to our team, they'll reach out to you shortly.",
    },
    # A phone number arrived while a person has the conversation.
    "phone_received": {
        "uz": "Rahmat! Telefon raqamingiz qabul qilindi, operatorimiz tez orada siz bilan bog'lanadi.",
        "ru": "Спасибо! Ваш номер телефона принят, наш оператор скоро свяжется с вами.",
        "en": "Thank you! Your phone number has been received, our team will contact you shortly.",
    },
    # The model mentioned a discount no tool confirmed.
    "discount_unconfirmed": {
        "uz": "Aniq chegirma haqida hozir tasdiqlab bera olmayman, operatorimiz tekshirib sizga ma'lumot beradi.",
        "ru": "Извините, точную информацию о скидках я сейчас подтвердить не могу, наш оператор проверит и обязательно свяжется с вами.",
        "en": "Apologies, I cannot confirm a specific discount right now. Our team will verify and get back to you shortly.",
    },
    # The model quoted a price the catalog doesn't back.
    "price_unconfirmed": {
        "uz": "Aniq narxni operatorimiz tekshirib, tez orada sizga yozadi.",
        "ru": "Извините, точную цену уточнит наш оператор — он скоро с вами свяжется.",
        "en": "Apologies, our team will confirm the exact price and get back to you shortly.",
    },
    # The customer shared a video/Reel the AI can't open.
    "media_unseen": {
        "uz": "Bu videoni ocha olmayapman 🙂 Undagi qaysi mahsulot qiziqtirdi? Nomini yozing yoki rasmini yuboring — darhol tekshirib beraman.",
        "ru": "Не могу открыть это видео 🙂 Какой товар вас заинтересовал? Напишите название или пришлите фото — сразу проверю.",
        "en": "I can't open that video 🙂 Which product caught your eye? Send me its name or a photo and I'll check right away.",
    },
    # The customer's photo couldn't be read.
    "photo_unreadable": {
        "uz": "Kechirasiz, yuborgan rasmingizni ochishda texnik nosozlik bo'ldi. Iltimos, qidirayotgan mahsulotingizni (turi, rangi, modeli) matn ko'rinishida yozib yubora olasizmi?",
        "ru": "Извините, возникла ошибка при обработке фото. Опишите, пожалуйста, нужный товар (тип, цвет, модель) текстом — я сразу проверю наличие!",
        "en": "Apologies, there was an issue viewing the photo. Could you please describe what product (type, color, model) you're looking for in text?",
    },
    # A reply had to be cut entirely (it carried internal text).
    "neutral": {
        "uz": "Tushunarli! Sizga qanday yordam bera olaman?",
        "ru": "Понял! Чем могу помочь?",
        "en": "Got it! How can I help?",
    },
    # Follow-up to a quiet warm/hot lead; {product} is the product's name.
    "follow_up": {
        "uz": "Assalomu alaykum! Siz so'ragan {product} bo'yicha savollaringiz qoldimi? Razmer yoki rangini tanlashda yordam kerak bo'lsa, bemalol yozing 😊",
        "ru": "Здравствуйте! Остались вопросы по {product}? Если нужно помочь с размером или цветом — пишите 😊",
        "en": "Hi! Any questions left about the {product}? Happy to help with the size or colour 😊",
    },
    # The same, when no product is known.
    "follow_up_generic": {
        "uz": "Assalomu alaykum! Mahsulotlarimiz bo'yicha savollaringiz qoldimi? Sizga mos model va o'lchamni tanlashda yordam beraman 😊",
        "ru": "Здравствуйте! Остались вопросы по нашим товарам? Помогу подобрать модель и размер 😊",
        "en": "Hi! Any questions left about our products? Happy to help you pick the right one 😊",
    },
}


def reply_text(business, key: str, lang: str, **values: str) -> str:
    """The business's own text for `key` in `lang`, else the default.
    `values` fill placeholders like {product}."""
    overrides = getattr(business, "reply_texts", None)
    custom = overrides.get(key, {}).get(lang) if isinstance(overrides, dict) else None
    defaults = DEFAULT_REPLIES[key]
    text = custom.strip() if isinstance(custom, str) and custom.strip() else defaults.get(lang, defaults["uz"])
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text
