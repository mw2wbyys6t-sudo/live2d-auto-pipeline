"""sanitize_prompt 的契约：提示词是给图像模型的自然语言，不是 shell 命令。

历史问题（v0.10.10 取证）：旧实现用 ASCII 子串黑名单把 ``sh `` / ``del `` /
``format `` 等替换成下划线、并删掉换行，导致
``English style, model girl`` → ``Engli____style, mo___girl``：
用户看到的分层/构图与提示词对不上，却找不到原因。
"""

from core.security import sanitize_prompt


def test_chinese_prompt_passes_through_unchanged():
    prompt = "正面全身立绘，粉色双马尾（可爱），蓝色眼睛！水手服；简洁白色背景。"
    assert sanitize_prompt(prompt) == prompt


def test_english_words_are_not_rewritten():
    prompt = "English style, model girl, formal composition, sh sh, del del"
    assert sanitize_prompt(prompt) == prompt


def test_shell_looking_text_is_kept_as_plain_text():
    # 提示词只进 argv 与 JSON，从不拼进 shell；改写正文只会破坏语义。
    prompt = "hello world; rm -rf /"
    assert sanitize_prompt(prompt) == prompt


def test_newlines_survive_and_crlf_is_normalized():
    assert sanitize_prompt("第一行\r\n第二行\r第三行") == "第一行\n第二行\n第三行"


def test_control_characters_are_removed_but_tab_kept():
    assert sanitize_prompt("前\x00中\x07后\t制表\x1b[31m") == "前中后\t制表[31m"


def test_length_limit_counts_characters_not_bytes():
    prompt = "雪" * 5000
    result = sanitize_prompt(prompt)
    assert len(result) == 4000
    assert result == "雪" * 4000


def test_length_limit_does_not_cut_emoji():
    prompt = "🎀" * 4500
    result = sanitize_prompt(prompt)
    assert len(result) == 4000
    assert set(result) == {"🎀"}


def test_edges_are_stripped_and_empty_stays_empty():
    assert sanitize_prompt("  中文提示词  ") == "中文提示词"
    assert sanitize_prompt("") == ""
    assert sanitize_prompt("   \n\t ") == ""
