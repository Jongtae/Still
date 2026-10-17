"""Still's Telegram profile for the owner's own bot (#1337).

Still is the default face of Personal AgentOS.  When the owner connects their
own bot, AgentOS gives it Still's name, photo and descriptions so the chat
they open looks like the assistant the homepage introduced.  Everything here
is best-effort: a refused or failed call changes nothing else and never
blocks the connection.  It runs once per bot, so a later rename in BotFather
is never overwritten.  Family instances are not touched: their bot names
identify them as share targets (#957).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)

#: Seconds each best-effort Bot API call may take (as in family_setup.describe_bot).
PROFILE_TIMEOUT = 4
#: #1541: the photo is an upload, not a short call.
PHOTO_TIMEOUT = 20
NAME = 'Still'
AVATAR = Path(__file__).with_name('assets') / 'still-avatar.jpg'

#: Wording from the Still starter identity kit; ``None`` is Telegram's default for any other language.
TEXTS = {
    None: {'short_description': 'Here with you. A personal AI on your own computer, built with Personal AgentOS.',
           'description': "Hi, I'm Still. What would you like a hand with today?\n\n"
                          'I run on your own computer with Personal AgentOS. Ask in plain words, the way you normally talk.'},
    'ko': {'short_description': 'Here with you. 대화로 필요한 일을 함께 풀어가는 Personal AgentOS의 개인 AI.',
           'description': '안녕하세요, Still이에요. 오늘 필요한 일부터 이야기해 주세요.\n\n'
                          '내 컴퓨터에서 동작하는 Personal AgentOS의 개인 AI예요. 평소 말투로 편하게 부탁하세요.'},
    'ja': {'short_description': 'Here with you. 会話で日々のことを一緒に片づける、Personal AgentOS のパーソナル AI。',
           'description': 'こんにちは、Still です。今日はどんなことから手伝いましょうか。\n\n'
                          '自分のコンピューターで動く Personal AgentOS のパーソナル AI です。いつもの言葉で気軽に頼んでください。'},
    'zh': {'short_description': 'Here with you. 通过对话陪你处理日常琐事的 Personal AgentOS 个人 AI。',
           'description': '你好，我是 Still。今天先从哪件事开始？\n\n'
                          '我是运行在你自己电脑上的 Personal AgentOS 个人 AI。用平常的话告诉我你需要什么就好。'},
}


def _step(done, key, run):
    try:
        run()
        done[key] = True
    except Exception as exc:  # best-effort: one refused step never stops the others
        status = getattr(exc, 'status', None)
        LOG.warning('still profile: %s not set (%s%s)', key, type(exc).__name__, f' {status}' if status else '')
        done[key] = False


def apply_photo(channel, *, avatar=AVATAR, timeout=PHOTO_TIMEOUT):
    """Set only Still's photo; True when Telegram accepted it."""
    done = {}
    _step(done, 'photo', lambda: channel.upload(
        'setMyProfilePhoto', {'photo': json.dumps({'type': 'static', 'photo': 'attach://still'})},
        {'still': ('still.jpg', 'image/jpeg', Path(avatar).read_bytes())}, timeout=timeout))
    return done['photo']


def apply(channel, *, avatar=AVATAR, timeout=PROFILE_TIMEOUT, photo_timeout=PHOTO_TIMEOUT):
    """Set the bot's name, descriptions and photo; return which steps Telegram accepted."""
    done = {}

    def step(key, run):
        _step(done, key, run)

    step('name', lambda: channel.call('setMyName', {'name': NAME}, timeout=timeout))
    for lang, texts in TEXTS.items():
        extra = {'language_code': lang} if lang else {}
        tag = lang or 'default'
        step(f'description:{tag}', lambda texts=texts, extra=extra: channel.call(
            'setMyDescription', {'description': texts['description'], **extra}, timeout=timeout))
        step(f'short_description:{tag}', lambda texts=texts, extra=extra: channel.call(
            'setMyShortDescription', {'short_description': texts['short_description'], **extra}, timeout=timeout))
    done['photo'] = apply_photo(channel, avatar=avatar, timeout=photo_timeout)
    return done


#: #1344: Still's first message after pairing (and the /start, /help reply).
#: Warm, short, and a how-to-ask with everyday examples; claims only what the
#: product does today (photos, location, memory notice, payment approval, /ai).
WELCOME = {
    'en': ("Hi, I'm Still. From now on, just talk to me here.\n\n"
           "Ask the way you normally would. For example:\n"
           "· Find a quiet place for dinner near the office this Friday\n"
           "· My cat has been off her food lately. What should I try?\n"
           "· Send a photo and say \"find me something like this\"\n"
           "· Share your location and ask \"anywhere nearby for coffee?\"\n\n"
           "When I learn something about you, I'll remember it and tell you what I kept. "
           "Anything that matters, like a payment, I'll always check with you first. "
           "You can change the AI I think with any time: /ai\n\n"
           "What can I help with today?"),
    'ko': ("안녕하세요, Still이에요. 이제 여기서 편하게 말을 걸어 주세요.\n\n"
           "평소 말투로 부탁하면 돼요. 예를 들면:\n"
           "· 이번 주 금요일 저녁, 회사 근처에 조용한 식당 찾아 줘\n"
           "· 고양이가 요즘 사료를 잘 안 먹어. 어떻게 하면 좋을까?\n"
           "· 사진을 보내고 \"이거랑 비슷한 거 찾아 줘\"\n"
           "· 위치를 보내고 \"근처에 커피 마실 곳 있어?\"\n\n"
           "대화하면서 알게 된 건 제가 기억해 두고, 무엇을 기억했는지 그때그때 말씀드릴게요. "
           "결제처럼 중요한 일은 꼭 먼저 여쭤볼게요. "
           "제가 생각할 때 쓰는 AI는 언제든 바꿀 수 있어요: /ai\n\n"
           "오늘 필요한 일부터 이야기해 주세요."),
    'ja': ("こんにちは、Still です。これからは、ここで気軽に話しかけてください。\n\n"
           "いつもの言葉で頼んでくだされば大丈夫です。たとえば:\n"
           "・今週の金曜の夜、会社の近くで静かなお店を探して\n"
           "・最近、猫がごはんをあまり食べない。どうしたらいい？\n"
           "・写真を送って「これに似たものを探して」\n"
           "・位置情報を送って「近くでコーヒーを飲めるところある？」\n\n"
           "会話の中でわかったことは私が覚えておき、何を覚えたかをその都度お伝えします。"
           "支払いのような大事なことは、必ず先に確認します。"
           "私が考えるときに使う AI は、いつでも替えられます: /ai\n\n"
           "今日はどんなことから手伝いましょうか。"),
    'zh': ("你好，我是 Still。以后在这里随时跟我说就好。\n\n"
           "用平常的话告诉我就行，比如：\n"
           "· 这周五晚上，在公司附近找个安静的餐厅\n"
           "· 我家猫最近不怎么吃饭，该怎么办？\n"
           "· 发一张照片，说“帮我找类似的”\n"
           "· 发送位置，问“附近有喝咖啡的地方吗？”\n\n"
           "聊天中了解到的事情我会记下来，并告诉你我记了什么。"
           "像付款这样重要的事，我一定先问你。"
           "我思考时用的 AI 随时可以更换：/ai\n\n"
           "今天先从哪件事开始？"),
}


def welcome(language_code=None):
    """Still's welcome in the owner's Telegram language; English when unknown."""
    lang = str(language_code or '').lower().split('-')[0]
    return WELCOME.get(lang, WELCOME['en'])
