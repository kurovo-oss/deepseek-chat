"""表情包收藏：识别、去重、打标、与引擎的接合。"""

import types

import pytest

from qqbot.collect import StickerCollector, is_sticker
from qqbot.config import Settings
from qqbot.engine import Engine
from qqbot.llm import ChatResult, LLMError
from qqbot.onebot import IncomingImage, IncomingMessage
from qqbot.store import Store

PNG = b"\x89PNG\r\n\x1a\n"


class FakeLLM:
    def __init__(self, text='{"tags":[]}', error=None):
        self.text = text
        self.error = error
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return ChatResult(self.text, 10, 5, "fake-model")


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_private(self, user_id, text):
        self.sent.append((user_id, text))
        return True

    def send_private_image(self, user_id, image, mode="path"):
        return True


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        allow_user_id=100,
        model_api_key="k",
        base_dir=tmp_path,
        db_path=tmp_path / "d.db",
        system_prompt_file=tmp_path / "s.md",
        image_dir=tmp_path / "images",
        segment_delay_ms=0,
        reply_max_chars=100,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def ctx(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    llm = FakeLLM()
    collector = StickerCollector(
        settings,
        store,
        settings.image_dir,
        llm=llm,
        tag_prompt="TAG",
        fetch=lambda url: PNG + b"sticker-bytes",
    )
    yield types.SimpleNamespace(
        settings=settings, store=store, llm=llm, collector=collector
    )
    store.close()


# ---------------------------------------------------------------- 识别
@pytest.mark.parametrize(
    "image, expected",
    [
        (IncomingImage(sub_type=1), True),
        (IncomingImage(sub_type=0, summary="[表情]"), True),
        (IncomingImage(sub_type=0, summary="[闪照]"), True),
        (IncomingImage(sub_type=0, summary=""), False),
        (IncomingImage(sub_type=0, summary="[图片]"), False),
    ],
)
def test_is_sticker(image, expected):
    assert is_sticker(image) is expected


# ---------------------------------------------------------------- 收藏
def test_collect_saves_image(ctx):
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    collected = ctx.collector.collect(message)

    assert len(collected) == 1
    assert collected[0]["source"] == "user"
    assert ctx.store.count_images() == 1
    assert (ctx.settings.image_dir / collected[0]["rel_path"]).is_file()


def test_collect_dedupes_same_bytes(ctx):
    image = IncomingImage(url="http://x/1", sub_type=1)
    for i in range(2):
        message = IncomingMessage(user_id=100, message_id=f"m{i}", text="", raw={}, images=(image,))
        ctx.collector.collect(message)
    assert ctx.store.count_images() == 1


def test_collect_ignores_plain_photo(ctx):
    message = IncomingMessage(
        user_id=100,
        message_id="m1",
        text="",
        raw={},
        images=(IncomingImage(url="http://x/1", sub_type=0, summary="[图片]"),),
    )
    assert ctx.collector.collect(message) == []
    assert ctx.store.count_images() == 0


def test_collect_ignores_missing_url(ctx):
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(sub_type=1),)
    )
    assert ctx.collector.collect(message) == []


def test_collect_rejects_non_image_bytes(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    collector = StickerCollector(
        settings, store, settings.image_dir, fetch=lambda url: b"<html>not an image</html>"
    )
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    assert collector.collect(message) == []
    assert store.count_images() == 0
    store.close()


def test_collect_disabled(tmp_path):
    settings = make_settings(tmp_path, image_collect_enabled=False)
    store = Store(settings.db_path)
    collector = StickerCollector(settings, store, settings.image_dir, fetch=lambda url: PNG)
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    assert collector.collect(message) == []
    store.close()


def test_collect_evicts_over_capacity(tmp_path):
    settings = make_settings(tmp_path, image_max_count=2)
    store = Store(settings.db_path)
    counter = {"n": 0}

    def fetch(url):
        counter["n"] += 1
        return PNG + bytes([counter["n"]])

    collector = StickerCollector(settings, store, settings.image_dir, fetch=fetch)
    for i in range(4):
        message = IncomingMessage(
            user_id=100,
            message_id=f"m{i}",
            text="",
            raw={},
            images=(IncomingImage(url=f"http://x/{i}", sub_type=1),),
        )
        collector.collect(message)

    assert store.count_images(status="active") == 2
    assert store.count_images(status="archived") == 2
    store.close()


# ---------------------------------------------------------------- 打标
def test_auto_tag_filters_to_vocabulary(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    llm = FakeLLM('{"tags":["tired","bogus","cute"]}')
    collector = StickerCollector(
        settings, store, settings.image_dir, llm=llm, tag_prompt="TAG", fetch=lambda url: PNG + b"z"
    )
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    collected = collector.collect(message)
    assert set(collected[0]["tags"]) == {"tired", "cute"}  # 入库时排序，不保证顺序
    store.close()


def test_auto_tag_accepts_code_fence(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    llm = FakeLLM('好的\n```json\n{"tags":["sleepy"]}\n```')
    collector = StickerCollector(
        settings, store, settings.image_dir, llm=llm, tag_prompt="TAG", fetch=lambda url: PNG + b"f"
    )
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    assert collector.collect(message)[0]["tags"] == ["sleepy"]
    store.close()


def test_auto_tag_failure_keeps_image(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    llm = FakeLLM(error=LLMError("打标挂了"))
    collector = StickerCollector(
        settings, store, settings.image_dir, llm=llm, tag_prompt="TAG", fetch=lambda url: PNG + b"e"
    )
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    collected = collector.collect(message)
    assert len(collected) == 1
    assert collected[0]["tags"] == []
    store.close()


# ---------------------------------------------------------------- 引擎接合
def build_engine(tmp_path, collector=None, llm=None):
    settings = make_settings(tmp_path)
    store = Store(settings.db_path)
    llm = llm or FakeLLM("好呀。")
    sender = FakeSender()
    engine = Engine(
        settings, store, llm, sender, "你是大肥鱼", sleep=lambda _: None, collector=collector
    )
    return types.SimpleNamespace(settings=settings, store=store, llm=llm, sender=sender, engine=engine)


def test_engine_acknowledges_sticker(tmp_path):
    env_ctx = build_engine(tmp_path)
    ctx_collector = StickerCollector(
        env_ctx.settings,
        env_ctx.store,
        env_ctx.settings.image_dir,
        fetch=lambda url: PNG + b"ack",
    )
    env_ctx.engine.collector = ctx_collector

    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    env_ctx.engine.handle_message(message)

    assert env_ctx.store.count_images() == 1
    assert "存下" in env_ctx.sender.sent[-1][1]
    assert env_ctx.llm.calls == []  # 收表情包不调模型
    env_ctx.store.close()


def test_duplicate_message_is_dropped_before_download(tmp_path):
    """幂等键必须在下载表情包之前写入。

    否则"查重 → 下载 → 写入"之间有几秒窗口，NapCat 超时重推会让同一条
    消息被收藏两遍、回复两遍。
    """
    env_ctx = build_engine(tmp_path)
    downloads = []
    env_ctx.engine.collector = StickerCollector(
        env_ctx.settings,
        env_ctx.store,
        env_ctx.settings.image_dir,
        fetch=lambda url: downloads.append(url) or (PNG + b"dup"),
    )
    message = IncomingMessage(
        user_id=100,
        message_id="m-dup",
        text="",
        raw={},
        images=(IncomingImage(url="http://x/1", sub_type=1),),
    )
    env_ctx.engine.handle_message(message)
    env_ctx.engine.handle_message(message)

    assert len(downloads) == 1
    assert len(env_ctx.sender.sent) == 1
    env_ctx.store.close()


def test_engine_keeps_chatting_after_sticker_with_text(tmp_path):
    env_ctx = build_engine(tmp_path)
    env_ctx.engine.collector = StickerCollector(
        env_ctx.settings, env_ctx.store, env_ctx.settings.image_dir, fetch=lambda url: PNG + b"both"
    )
    message = IncomingMessage(
        user_id=100,
        message_id="m1",
        text="这张好看吗",
        raw={},
        images=(IncomingImage(url="http://x/1", sub_type=1),),
    )
    env_ctx.engine.handle_message(message)

    assert env_ctx.store.count_images() == 1
    assert env_ctx.llm.calls, "有文字时照常聊天"
    env_ctx.store.close()


def test_engine_survives_collector_error(tmp_path):
    class Boom:
        def collect(self, message, recent=None):
            raise RuntimeError("收藏炸了")

    env_ctx = build_engine(tmp_path, collector=Boom())
    message = IncomingMessage(
        user_id=100,
        message_id="m1",
        text="今天好累啊",
        raw={},
        images=(IncomingImage(url="http://x/1", sub_type=1),),
    )
    env_ctx.engine.handle_message(message)
    assert env_ctx.sender.sent[-1][1] == "好呀。"
    env_ctx.store.close()


def test_engine_saves_placeholder_text_for_sticker(tmp_path):
    env_ctx = build_engine(tmp_path)
    env_ctx.engine.collector = StickerCollector(
        env_ctx.settings, env_ctx.store, env_ctx.settings.image_dir, fetch=lambda url: PNG + b"ph"
    )
    message = IncomingMessage(
        user_id=100, message_id="m1", text="", raw={}, images=(IncomingImage(url="http://x/1", sub_type=1),)
    )
    env_ctx.engine.handle_message(message)
    # 最近两条里的第一条才是用户那条（第二条是助手的应答）
    assert env_ctx.store.recent_messages("100", 2)[0]["content"] == "[表情]"
    env_ctx.store.close()


def test_tag_command_labels_latest_sticker(tmp_path):
    env_ctx = build_engine(tmp_path)
    env_ctx.store.add_image("aaa", "aa/aaa.png", "image/png", 10, source="user")
    (env_ctx.settings.image_dir / "aa").mkdir(parents=True, exist_ok=True)
    (env_ctx.settings.image_dir / "aa" / "aaa.png").write_bytes(PNG)

    env_ctx.engine.handle_message(
        IncomingMessage(user_id=100, message_id="m1", text="/tag 开心 猫", raw={})
    )
    latest = env_ctx.store.latest_image(source="user")
    assert set(latest["tags"]) == {"开心", "猫"}
    assert latest["manual"] is True
    env_ctx.store.close()


def test_tag_command_without_name_shows_current(tmp_path):
    env_ctx = build_engine(tmp_path)
    env_ctx.store.add_image("bbb", "bb/bbb.png", "image/png", 10, tags=["cute"], source="user")
    env_ctx.engine.handle_message(
        IncomingMessage(user_id=100, message_id="m1", text="/tag", raw={})
    )
    assert "cute" in "".join(text for _, text in env_ctx.sender.sent)
    env_ctx.store.close()


def test_img_list_and_rm(tmp_path):
    env_ctx = build_engine(tmp_path)
    image_id = env_ctx.store.add_image("ccc", "cc/ccc.png", "image/png", 10, tags=["cute"], source="user")

    env_ctx.engine.handle_message(
        IncomingMessage(user_id=100, message_id="m1", text="/img list", raw={})
    )
    listed = env_ctx.sender.sent[-1][1]
    assert "cute" in listed
    # 编号必须在——/img rm、/img tag 都要靠它
    assert f"{image_id}." in listed

    env_ctx.engine.handle_message(
        IncomingMessage(user_id=100, message_id="m2", text=f"/img rm {image_id}", raw={})
    )
    assert env_ctx.store.get_image(image_id) is None
    env_ctx.store.close()


def test_img_list_keeps_numbering_for_many_images(tmp_path):
    env_ctx = build_engine(tmp_path)
    ids = [
        env_ctx.store.add_image(f"sha{i}", f"aa/{i}.png", "image/png", 10, tags=["cute"], source="user")
        for i in range(3)
    ]
    env_ctx.engine.handle_message(
        IncomingMessage(user_id=100, message_id="m1", text="/img list", raw={})
    )
    listed = "".join(text for _, text in env_ctx.sender.sent)
    for image_id in ids:
        assert f"{image_id}." in listed
    env_ctx.store.close()
