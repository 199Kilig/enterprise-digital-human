"""对话大脑（DESIGN §3.2 `brain/`）：模型供应商抽象 + 流式输出。

P1 实现 DeepSeek（OpenAI 兼容 /chat/completions，V-04 已验证的路径）。
供应商差异收敛在本模块内：上层（api 编排层）只消费 BrainChunk 流。
"""
