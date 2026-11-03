"""抓表情的脚本：分类别被台词带偏。"""

import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "fetch-stickers.py"
    spec = importlib.util.spec_from_file_location("fetch_stickers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetcher = load_module()


def test_tail_type_beats_the_quote():
    """台词里的"元气"不该被判成生气——看的是后面的"元气满满表情包"。"""
    assert (
        fetcher.classify("DeepSeek娘『HI,I'M DEEPSEEK!』元气满满表情包") == "开心"
    )


def test_crying_is_sad():
    assert fetcher.classify("DeepSeek娘『用户别走，我上错模型了』哭着挽留表情包") == "委屈"


def test_angry_is_angry():
    assert fetcher.classify("兽耳AI娘『涨价大模型给我跪下！』愤怒声讨表情包") == "生气"


def test_quote_words_still_count_when_the_tail_is_plain():
    assert fetcher.classify("DeepSeek鲸娘『哼，本大肥鱼最可爱』表情包") == "嘲讽"


def test_unknown_caption_is_skipped():
    assert fetcher.classify("深海档案馆封面图") is None


def test_page_parser_reads_preview_and_caption():
    html = (
        '<div class="ar-card"><a data-preview="https://img/a.webp" '
        'data-alt="DeepSeek娘『笑死』捧腹大笑表情包"></a></div>'
    )

    rows = fetcher.parse_page(html)

    assert rows == [("https://img/a.webp", "DeepSeek娘『笑死』捧腹大笑表情包")]


def test_index_parser_reads_the_whole_gallery():
    data = {
        "gallery": {
            "DeepSeek娘": {
                "images": [
                    {
                        "alt": "DeepSeek娘『真当我是便宜货啊』气鼓鼓表情包",
                        "preview": "https://img/meme/001.webp",
                    }
                ]
            },
            "AI娘化": {
                "images": [{"name": "AI娘表情包", "original": "https://img/meme/1510.jpg"}]
            },
        }
    }

    rows = fetcher.parse_index(data)

    assert ("https://img/meme/001.webp", "DeepSeek娘『真当我是便宜货啊』气鼓鼓表情包") in rows
    assert ("https://img/meme/1510.jpg", "AI娘表情包") in rows


def test_index_bucket_for_a_pout_is_anger():
    assert fetcher.classify("DeepSeek娘『真当我是便宜货啊』气鼓鼓表情包") == "生气"
