import asyncio
import json
import traceback
from typing import Tuple, Optional, List, Type

import httpx

from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    ComponentInfo,
    ConfigField,
    BaseCommand,
    generator_api,
    person_api
)

logger = get_logger("like_plugin")


async def send_like(user_id, napcat_host, napcat_port, napcat_token) -> Tuple[bool, int, str]:
    """
    每次请求点赞 times_per_request（固定为 10）
    成功累加，最多累加到 50 次后停止
    遇到非 ok 状态或异常立即停止并返回失败信息
    返回 (success, count, failed_message)
    """
    times_per_request = 10
    max_total = 50

    base_url = f"http://{napcat_host}:{napcat_port}"
    path = "/send_like"
    headers = {"Content-Type": "application/json"}
    if napcat_token:
        headers["Authorization"] = napcat_token

    payload = {"user_id": user_id, "times": times_per_request}
    logger.debug(f"发送点赞请求: {json.dumps(payload)}")

    count = 0
    failed_message = ""

    async with httpx.AsyncClient(base_url=base_url) as client:
        # 循环直到达到上限或出现错误/非 ok 响应
        while True:
            try:
                resp = await client.post(path, json=payload, headers=headers)
            except Exception:
                logger.error(f"点赞请求异常：{traceback.format_exc()}")
                failed_message = "网络请求异常"
                break
            try:
                text = resp.text
                logger.debug(f"点赞响应状态: {resp.status_code}, 内容: {text}")
            except Exception:
                logger.error(f"读取响应失败：{traceback.format_exc()}")
                failed_message = "读取响应失败"
                break
            try:
                data_json = resp.json()
            except Exception:
                logger.error(f"点赞响应解析失败：{traceback.format_exc()}")
                failed_message = "响应解析失败"
                break
            if data_json.get("status") != "ok":
                failed_message = data_json.get("message", None)
                if not failed_message:
                    failed_message = f"未知错误，响应内容: {text}"
                break
            # 累加并判断是否达到上限
            count += times_per_request
            if count >= max_total:
                failed_message = f"已达到单次点赞上限{max_total}次"
                break

    if count > 0:
        return True, count, ""
    return False, 0, failed_message


class LikeCommand(BaseCommand):
    """点赞Command - 响应/like命令"""

    command_name = "qq_like"
    command_description = "给指定QQ用户点赞"

    # === 命令设置（必须填写）===
    command_pattern = r"^(((/|#)(like))|(/|#)?(赞我|麦麦赞我))$"  # 命令可以以 / 或 # 开头

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行时间查询"""
        user_id = self.message.message_info.user_info.user_id
        person_id = person_api.get_person_id('qq', user_id)
        person_name = await person_api.get_person_value(person_id, "person_name")
        if not user_id:
            await self.send_text("无法获取用户ID,点赞失败")
            return False, "无法获取用户ID", 1
        napcat_host = self.get_config("napcat.host")
        napcat_port = self.get_config("napcat.port")
        napcat_token = self.get_config("napcat.token", "")
        if not napcat_port or not napcat_host:
            await self.reply("Napcat服务配置不完整，点赞失败")
            return False, "Napcat服务配置不完整", 1
        success, count, failed_message = await send_like(user_id, napcat_host, napcat_port, napcat_token)
        target = f"为 {person_name} " if person_name else ""
        if success:
            raw_reply = f"已成功{target}点赞 {count} 次"
        else:
            raw_reply = f"{target}点赞失败: {failed_message}"
        reply_result = await self.reply(raw_reply)
        return success, f"{raw_reply} {reply_result}", 1

    async def reply(self, raw_reply) -> str:
        if self.get_config("like_plugin.enable_rewrite_reply"):
            # 构建重写数据
            rewrite_data = {
                "raw_reply": raw_reply,
                "reason": "用户正在请求点赞。你尝试进行了点赞，请根据点赞结果生成回复。",
            }

            # 调用表达器重写
            result_status, data = await generator_api.rewrite_reply(
                chat_stream=self.message.chat_stream,
                reply_data=rewrite_data,
                enable_chinese_typo=global_config.chinese_typo.enable,
                enable_splitter=self.get_config("enable_splitter.enable_splitter", False)
            )

            if result_status:
                # 发送重写后的回复
                for reply_seg in data.reply_set.reply_data:
                    send_data = reply_seg.content
                    await self.send_text(send_data)
                    await asyncio.sleep(0.3)  # 避免消息发送过快顺序错乱
                return "已发送重写后的点赞回复"
            else:
                logger.warning("回复重写失败，发送原始消息")
        # 直接发送原始消息
        await self.send_text(raw_reply)
        return "已发送点赞回复"


# ===== 插件注册 =====


@register_plugin
class LikePlugin(BasePlugin):
    """QQ点赞插件"""

    # 插件基本信息
    plugin_name: str = "qq_like_plugin"  # 内部标识符
    enable_plugin: bool = True
    dependencies: List[str] = []  # 插件依赖列表
    python_dependencies: List[str] = ["httpx"]  # Python包依赖列表
    config_file_name: str = "config.toml"  # 配置文件名

    # 配置节描述
    config_section_descriptions = {
        "like_plugin": "点赞配置",
        "napcat": "Napcat服务配置",
    }

    # 配置Schema定义
    config_schema: dict = {
        "like_plugin": {
            "enable_rewrite_reply": ConfigField(type=bool, default=True, description="是否启用使用bot人设重写回复"),
            "enable_splitter": ConfigField(type=bool, default=False, description="是否启用长回复分段发送"),
        },
        "napcat": {
            "host": ConfigField(type=str, default="127.0.0.1", description="Napcat服务地址"),
            "port": ConfigField(type=int, default=9999, description="Napcat服务端口"),
            "token": ConfigField(type=str, default="", description="Napcat服务认证Token"),
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [(LikeCommand.get_command_info(), LikeCommand)]
