"""人格与输出约束单测（brain/persona.py）。

锁住三件事：
1. 默认人格来自 config.yaml（DESIGN §4.4 纪律：配置项必须被代码真实读取）
2. 教育人格确实带上了"防止 TTS 念出格式噪音"的输出约束（与 text_clean 是一对）
3. 未知人格当场抛错，不静默降级
"""
import pytest

from brain.persona import CS_PROMPT, DEFAULT_PERSONA, PERSONAS, TEACHER_PROMPT, build_system_prompt
from config import load_config


def test_default_persona_follows_config():
    """config.yaml 的 llm.persona 必须是被支持的人格，且被 build_system_prompt 真实读取。"""
    cfg_persona = (load_config().get("llm") or {}).get("persona")
    assert cfg_persona in PERSONAS, f"config.yaml llm.persona={cfg_persona!r} 不是受支持的人格"
    assert build_system_prompt() == PERSONAS[cfg_persona]


def test_default_persona_constant_is_supported():
    assert DEFAULT_PERSONA in PERSONAS


def test_teacher_persona_declares_output_constraints():
    """教育人格必须显式约束输出格式——这是 text_clean 兜底之外的源头治理。"""
    prompt = build_system_prompt("teacher")
    assert "小奈" in prompt
    for must in ("Markdown", "emoji", "两句话以内", "动作或表情说明"):
        assert must in prompt, f"教师人格缺少输出约束：{must}"
    assert "直接念出来" in prompt  # 说清"为什么不能排版"


def test_teacher_persona_answers_concept_questions_first():
    """实测缺陷回归：问"什么是 RAG"时模型只反问不给知识点，还垫客套话。

    修正后人格必须显式要求：概念问题先给解释、禁止只反问、禁止情绪铺垫。
    """
    prompt = build_system_prompt("teacher")
    for must in ("先说清知识点", "不要只反问", "客套"):
        assert must in prompt, f"教师人格缺少该约束：{must}"


def test_cs_persona_still_available():
    """客服人格保留（PRD §1 目标场景仍是企业客服）：切回只需改 config。"""
    assert build_system_prompt("cs") == CS_PROMPT
    assert "小顾" in CS_PROMPT


def test_unknown_persona_raises():
    with pytest.raises(ValueError):
        build_system_prompt("does-not-exist")


def test_persona_override_beats_config():
    assert build_system_prompt("cs") != TEACHER_PROMPT
