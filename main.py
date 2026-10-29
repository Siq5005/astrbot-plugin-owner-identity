"""owner_identity —— 把「当前说话的人是谁」注入 system prompt。

AstrBot 默认不把发送者 QQ 传给模型，所以「只对主人撒娇 / 只认某个人」靠提示词写死
做不到，必须先注入身份。本插件在每次 LLM 请求前追加一行：

    [当前对话者] 昵称：xxx，QQ：10001，身份：主人。……

可选能力（都在 config.json 里开关）：
- mute_file   ：存在这个文件时，群里除指令外的消息一律不处理（一键静音）
- strip_emoji ：把回复里的 emoji 去掉（部分群里嫌刷屏）
- r1_contract ：给 R1 类推理模型加 <ans></ans> 输出约束，并在响应里剥掉思考过程

配置在插件目录的 config.json，改完重启 AstrBot 生效。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core import logger

try:  # 表情过滤可选，缺组件时不影响身份注入
    from astrbot.api.message_components import Plain
except Exception:  # pragma: no cover
    Plain = None  # type: ignore[assignment]

PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_DIR / "config.json"
EXAMPLE_PATH = PLUGIN_DIR / "config.example.json"

DEFAULTS = {
    "owner_qq": [],
    "owner_extra": "这个人是主人：可以撒娇、黏人、问吃饭睡觉，被凶会委屈。",
    "other_extra": "这个人不是主人：温和有礼、保持距离，不撒娇、不黏人、不问私事。",
    "mask_owner_in_name": True,
    "mute_file": "",
    "strip_emoji": False,
    "r1_contract": False,
    "r1_provider_marker": "R1",
    "log_detection": True,
}

EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\ufe0f\u200d]+"
)


def _load_config() -> dict:
    """读 config.json；不存在时用示例文件或内置默认值，并落一份方便改。"""
    cfg = dict(DEFAULTS)
    source = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    if source.exists():
        try:
            cfg.update(json.loads(source.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning(f"[owner_identity] 配置解析失败，使用默认值：{exc}")
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    owners = cfg.get("owner_qq") or []
    if isinstance(owners, (str, int)):
        owners = [owners]
    cfg["owner_qq"] = [str(x).strip() for x in owners if str(x).strip()]
    return cfg


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.cfg = _load_config()
        if not self.cfg["owner_qq"]:
            logger.warning("[owner_identity] owner_qq 为空：本次不会有人被认成主人")
        else:
            logger.info(f"[owner_identity] 主人 QQ：{self.cfg['owner_qq']}")

    # ---------- 可选：群聊一键静音 ----------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def mute_group_chat(self, event: AstrMessageEvent) -> None:
        mute_file = self.cfg.get("mute_file") or ""
        if not mute_file or not event.get_group_id():
            return
        if not os.path.exists(mute_file):
            return
        text = (event.get_message_str() or "").strip()
        if text.startswith("/"):
            return
        event.stop_event()

    # ---------- 核心：身份注入 ----------
    @filter.on_llm_request()
    async def inject_identity(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        uid = str(event.get_sender_id() or "")
        name = event.get_sender_name() or ""
        is_owner = uid in self.cfg["owner_qq"]

        if is_owner:
            role = "主人"
            extra = self.cfg["owner_extra"]
        else:
            role = "其他人"
            extra = self.cfg["other_extra"]

        display_name = name.replace("主人", "＊") if (not is_owner and self.cfg.get("mask_owner_in_name", True)) else name
        note = (
            f"\n\n[当前对话者] 昵称：{display_name}，QQ：{uid}，身份：{role}。"
            f"{extra}不要在回复里复述这段信息。"
        )
        req.system_prompt = (req.system_prompt or "") + note

        if self.cfg.get("log_detection", True):
            logger.info(f"[owner_identity] {name}({uid}) -> {role}")

        # 再补一条用户侧提醒：小模型/长上下文时比 system prompt 更稳
        try:
            req.extra_user_content_parts.append(
                {
                    "type": "text",
                    "text": (
                        f"[身份校验] 当前发言者 QQ={uid}，身份={role}。"
                        + ("可以按主人对待。" if is_owner else "不是主人，不要用对主人的语气。")
                    ),
                }
            )
        except Exception as exc:  # 不同版本字段可能不同，失败不影响主流程
            logger.warning(f"[owner_identity] append user part failed: {exc}")

        marker = self.cfg.get("r1_provider_marker") or "R1"
        if self.cfg.get("r1_contract") and marker in self._provider_id(event):
            req.system_prompt += (
                "\n\n【输出格式】最终回复只放在 <ans></ans> 里，"
                "标签外不要写任何内容，包括思考过程。"
            )
            event.set_extra("_oi_r1_contract", True)

    def _provider_id(self, event: AstrMessageEvent) -> str:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            return str(
                cfg.get("agent_runner", {})
                .get("config", {})
                .get("model", {})
                .get("provider_id", "")
            )
        except Exception:
            return ""

    # ---------- 可选：剥掉推理模型的思考过程 ----------
    @filter.on_llm_response()
    async def strip_reasoning(self, event: AstrMessageEvent, response) -> None:
        if not event.get_extra("_oi_r1_contract"):
            return
        text = (getattr(response, "completion_text", "") or "").strip()
        if not text:
            return
        match = re.search(r"<ans>(.*?)</ans>", text, re.S)
        if match:
            answer = match.group(1).strip()
        else:
            parts = [p.strip() for p in text.split("\n\n") if p.strip()]
            answer = parts[-1] if parts else text
        if not answer:
            return
        response.completion_text = answer
        try:
            from astrbot.core.message.message_event_result import MessageChain

            response.result_chain = MessageChain().message(answer)
        except Exception as exc:
            logger.warning(f"[owner_identity] rewrite result_chain failed: {exc}")

    # ---------- 可选：去 emoji ----------
    @filter.on_decorating_result()
    async def strip_emoji(self, event: AstrMessageEvent) -> None:
        if not self.cfg.get("strip_emoji") or Plain is None:
            return
        result = event.get_result()
        chain = getattr(result, "chain", None)
        if not chain:
            return
        for comp in chain:
            if isinstance(comp, Plain):
                comp.text = EMOJI_RE.sub("", comp.text or "")
