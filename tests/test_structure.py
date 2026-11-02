"""结构守卫：engine 别再长回上帝类。

拆分之后 engine 只做编排——组上下文在 context.py，斜杠指令在 commands.py，
用户能看到的固定话术在 sayings.py。谁把方法搬回来，这里会拦住。
"""

from pathlib import Path

from qqbot import commands, context, engine, sayings

ENGINE_MAX_LINES = 900


def test_engine_is_built_from_the_pieces():
    assert issubclass(engine.Engine, context.ContextMixin)
    assert issubclass(engine.Engine, commands.CommandsMixin)


def test_engine_no_longer_owns_context_or_commands():
    assert "_build_messages" not in engine.Engine.__dict__
    assert "_recall_section" not in engine.Engine.__dict__
    assert "_handle_command" not in engine.Engine.__dict__
    assert "_memory_command" not in engine.Engine.__dict__


def test_engine_file_stays_small():
    lines = Path(engine.__file__).read_text(encoding="utf-8").splitlines()
    assert len(lines) < ENGINE_MAX_LINES, f"engine.py 又长到 {len(lines)} 行了"


def test_old_names_still_resolve_from_engine():
    # 外部（测试、脚本）一直是 qqbot.engine.HELP_TEXT 这么用的，别断
    assert engine.HELP_TEXT == sayings.HELP_TEXT
    assert engine.FALLBACK_REPLIES == sayings.FALLBACK_REPLIES
    assert engine.RECALL_TITLE == sayings.RECALL_TITLE


def test_help_text_lists_the_main_commands():
    for name in ("/reset", "/memory", "/status", "/draw"):
        assert name in sayings.HELP_TEXT
