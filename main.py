"""SunBlacklist 群管黑名单/警告插件（本地黑名单）

功能：
- 管理员使用 `warn @用户` 进行警告；达到2次自动踢出并加入本地黑名单
- 管理员使用 `ban @用户` 立即踢出并加入本地黑名单
- 成员主动退群后，自动加入黑名单，后续进群申请将被拒绝
- 拦截入群申请事件，若在黑名单中则自动拒绝

参考 sunos-sunwelcome 的结构与事件接入方式。
"""

import os
import sqlite3
import time
from typing import Iterable, List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import At


@register(
    "sunblacklist",
    "Akuma",
    "SunBlacklist 警告与黑名单插件",
    "1.0.0",
    "https://github.com/Akuma-real/sunos-sunblacklist",
)
class SunBlacklistPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context

        data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sunos")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "sunos_blacklist.db")
        self._init_database()
        logger.info("SunBlacklist 插件 v1.0.0 初始化完成")

    # ================== 数据库 ==================
    def _init_database(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS warns (
                        group_id TEXT NOT NULL,
                        user_id  TEXT NOT NULL,
                        count    INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (group_id, user_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS blacklist (
                        group_id  TEXT NOT NULL,
                        user_id   TEXT NOT NULL,
                        reason    TEXT,
                        created_at INTEGER,
                        by_user   TEXT,
                        PRIMARY KEY (group_id, user_id)
                    )
                    """
                )
                conn.commit()
        except Exception as e:
            logger.error(f"SunBlacklist 数据库初始化失败: {e}")

    def _get_warn_count(self, group_id: str, user_id: str) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "SELECT count FROM warns WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"读取警告次数失败: {e}")
            return 0

    def _add_warn(self, group_id: str, user_id: str) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cnt = self._get_warn_count(group_id, user_id) + 1
                conn.execute(
                    "INSERT INTO warns (group_id, user_id, count) VALUES (?, ?, ?)\n                     ON CONFLICT(group_id, user_id) DO UPDATE SET count=excluded.count",
                    (group_id, user_id, cnt),
                )
                conn.commit()
                return cnt
        except Exception as e:
            logger.error(f"增加警告失败: {e}")
            return self._get_warn_count(group_id, user_id)

    def _clear_warn(self, group_id: str, user_id: str) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM warns WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"清除警告失败: {e}")

    def _is_blacklisted(self, group_id: str, user_id: str) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM blacklist WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"检查黑名单失败: {e}")
            return False

    def _add_blacklist(self, group_id: str, user_id: str, reason: str = "", by_user: str = "") -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO blacklist (group_id, user_id, reason, created_at, by_user) VALUES (?, ?, ?, ?, ?)\n                     ON CONFLICT(group_id, user_id) DO UPDATE SET reason=excluded.reason, by_user=excluded.by_user",
                    (group_id, user_id, reason, int(time.time()), by_user),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"加入黑名单失败: {e}")

    def _remove_blacklist(self, group_id: str, user_id: str) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM blacklist WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"移除黑名单失败: {e}")

    def _get_blacklist(self, group_id: str) -> List[tuple[str, str, int, str]]:
        """返回 [(user_id, reason, created_at, by_user), ...]"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "SELECT user_id, COALESCE(reason,''), COALESCE(created_at,0), COALESCE(by_user,'') FROM blacklist WHERE group_id=? ORDER BY created_at DESC",
                    (group_id,),
                )
                return [(str(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"读取黑名单失败: {e}")
            return []

    # ================== 工具 ==================
    @staticmethod
    def _get_mentioned_user_ids(event: AstrMessageEvent) -> List[str]:
        ids: List[str] = []
        try:
            for seg in event.get_messages():  # type: ignore[attr-defined]
                if isinstance(seg, At):
                    qq = str(seg.qq)
                    if qq and qq != "all":
                        ids.append(qq)
        except Exception:
            pass
        # 兜底：从纯文本尝试提取 @123456（仅识别纯数字QQ号）
        text = event.message_str or ""
        for token in text.split():
            if token.startswith("@") and token[1:].isdigit():
                ids.append(token[1:])
        # 去重
        return list(dict.fromkeys([i for i in ids if i]))

    async def _has_admin_priv(self, event: AstrMessageEvent) -> bool:
        """检测是否具备管理员或群主权限，必要时向平台查询。"""
        try:
            if getattr(event, "is_admin") and event.is_admin():
                return True
        except Exception:
            pass
        # 尝试通过 bot 查询成员角色
        try:
            gid = int(event.get_group_id())
            uid = int(event.get_sender_id())
            info = await event.bot.get_group_member_info(group_id=gid, user_id=uid)  # type: ignore[attr-defined]
            role = str(info.get("role", "member"))
            return role in ("admin", "owner")
        except Exception:
            return False

    async def _kick_and_block(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        try:
            await event.bot.set_group_kick(  # type: ignore[attr-defined]
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
            return True
        except Exception as e:
            logger.error(f"踢出失败: {e}")
            return False

    async def _is_member(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """检查目标是否在群内，失败或不存在均返回 False。"""
        try:
            info = await event.bot.get_group_member_info(  # type: ignore[attr-defined]
                group_id=int(group_id), user_id=int(user_id)
            )
            return bool(info)
        except Exception:
            return False

    # ================== 指令 ==================
    @filter.command("sunos")
    async def sunos_command(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            return
        if args[1] == "bl":
            async for res in self._handle_bl_commands(event, args):
                yield res

    @filter.command_group("sunos")
    async def sunos_dot_command(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            return
        if args[1] == "bl":
            async for res in self._handle_bl_commands(event, args):
                yield res

    async def _handle_bl_commands(self, event: AstrMessageEvent, args: List[str]):
        if not event.get_group_id():
            yield event.plain_result("仅支持群聊中使用该指令")
            return
        group_id = event.get_group_id()
        if len(args) == 2:
            yield event.plain_result("用法: /sunos bl <help|list|add|del>")
            return

        sub = args[2]
        if sub in ("help", "h", "?"):
            yield event.plain_result(
                "SunBlacklist 指南:\n"
                "- /sunos bl list 查看本地黑名单\n"
                "- /sunos bl add <@用户|QQ号> 手动加入本地黑名单\n"
                "- /sunos bl del <@用户|QQ号> 从本地黑名单移除\n"
                "- 也可使用: warn @用户 / ban @用户（仅踢人并维护本地黑名单）"
            )
            return
        elif sub == "list":
            items = self._get_blacklist(group_id)
            if not items:
                yield event.plain_result("本群黑名单为空")
            else:
                lines = [f"{uid} - {reason or '无原因'}" for uid, reason, _, _ in items[:50]]
                more = "" if len(items) <= 50 else f"\n...共 {len(items)} 人"
                yield event.plain_result("本地黑名单列表:\n" + "\n".join(lines) + more)
            return
        elif sub == "add":
            if not await self._has_admin_priv(event):
                yield event.plain_result("此操作需要管理员权限")
                return
            # 默认加黑并尝试踢出；支持 --no-kick 仅加黑不踢人
            kick_flag = True
            raw_extra = args[3:] if len(args) >= 4 else []
            if any(tok == "--no-kick" for tok in raw_extra):
                kick_flag = False
                raw_extra = [tok for tok in raw_extra if tok != "--no-kick"]
            ids = self._get_mentioned_user_ids(event)
            if len(ids) == 0 and raw_extra:
                ids = [tok for tok in raw_extra if tok.isdigit()]
            if not ids:
                yield event.plain_result("用法: /sunos bl add <@用户|QQ号> [--no-kick]")
                return
            msg_list: List[str] = []
            for uid in ids:
                self._add_blacklist(group_id, uid, reason="手动添加", by_user=str(event.get_sender_id()))
                if kick_flag:
                    # 若目标不在群内，避免误报“并踢出”
                    if not await self._is_member(event, group_id, uid):
                        msg_list.append(f"{uid} 已加入本地黑名单（不在群内，无需踢出）")
                    else:
                        ok = await self._kick_and_block(event, group_id, uid)
                        if ok:
                            msg_list.append(f"{uid} 已加入本地黑名单并踢出")
                        else:
                            msg_list.append(f"{uid} 已加入本地黑名单，但踢出失败(权限不足或不在群内)")
            if not kick_flag:
                yield event.plain_result(f"已加入本地黑名单: {', '.join(ids)}")
            else:
                yield event.plain_result("\n".join(msg_list) or "无有效用户")
            return
        elif sub in ("del", "remove"):
            if not await self._has_admin_priv(event):
                yield event.plain_result("此操作需要管理员权限")
                return
            ids = self._get_mentioned_user_ids(event)
            if len(ids) == 0 and len(args) >= 4:
                ids = [tok for tok in args[3:] if tok.isdigit()]
            if not ids:
                yield event.plain_result("用法: /sunos bl del <@用户|QQ号>")
                return
            for uid in ids:
                self._remove_blacklist(group_id, uid)
            yield event.plain_result(f"已从本地黑名单移除: {', '.join(ids)}")
            return
        else:
            yield event.plain_result("未知子命令，使用 /sunos bl help 查看帮助")
            return
    @filter.command("warn")
    async def cmd_warn(self, event: AstrMessageEvent):
        """warn @用户：管理员警告，被警告两次将踢出并加入本地黑名单"""
        if not event.get_group_id():
            yield event.plain_result("仅支持群聊中使用该指令")
            return
        if not await self._has_admin_priv(event):
            yield event.plain_result("此操作需要管理员权限")
            return
        group_id = event.get_group_id()
        at_ids = self._get_mentioned_user_ids(event)
        if not at_ids:
            yield event.plain_result("用法: warn @用户")
            return
        sender = str(event.get_sender_id())
        for uid in at_ids:
            cnt = self._add_warn(group_id, uid)
            if cnt >= 2:
                self._add_blacklist(group_id, uid, reason="累计两次警告(本地)", by_user=sender)
                ok = await self._kick_and_block(event, group_id, uid)
                if ok:
                    self._clear_warn(group_id, uid)
                    yield event.plain_result(f"{uid} 已达2次警告，已踢出并加入本地黑名单")
                else:
                    yield event.plain_result(f"{uid} 达到2次警告，但踢出失败，请检查权限")
            else:
                yield event.plain_result(f"已警告 {uid}（{cnt}/2）")

    @filter.command("ban")
    async def cmd_ban(self, event: AstrMessageEvent):
        """ban @用户：管理员立即踢出并加入本地黑名单"""
        if not event.get_group_id():
            yield event.plain_result("仅支持群聊中使用该指令")
            return
        if not await self._has_admin_priv(event):
            yield event.plain_result("此操作需要管理员权限")
            return
        group_id = event.get_group_id()
        at_ids = self._get_mentioned_user_ids(event)
        if not at_ids:
            yield event.plain_result("用法: ban @用户")
            return
        sender = str(event.get_sender_id())
        for uid in at_ids:
            self._add_blacklist(group_id, uid, reason="管理员加入本地黑名单", by_user=sender)
            ok = await self._kick_and_block(event, group_id, uid)
            if ok:
                self._clear_warn(group_id, uid)
                yield event.plain_result(f"已将 {uid} 踢出并加入本地黑名单")
            else:
                yield event.plain_result(f"已加入本地黑名单，但踢出 {uid} 失败，请检查权限")

    # ================== 事件监听 ==================
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_group_requests_and_leaves(self, event: AstrMessageEvent):
        """监听入群申请与主动退群事件"""
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            if not isinstance(raw, dict):
                return

            # 入群申请：若在黑名单，自动拒绝
            if (
                raw.get("post_type") == "request"
                and raw.get("request_type") == "group"
                and raw.get("sub_type") == "add"
            ):
                user_id = str(raw.get("user_id") or "")
                group_id = str(raw.get("group_id") or "")
                flag = raw.get("flag", "")
                if group_id and user_id and self._is_blacklisted(group_id, user_id):
                    try:
                        await event.bot.set_group_add_request(  # type: ignore[attr-defined]
                            flag=flag, sub_type="add", approve=False, reason="黑名单用户"
                        )
                        yield event.plain_result("黑名单用户，已自动拒绝进群")
                    except Exception as e:
                        logger.error(f"自动拒绝进群失败: {e}")
                return

            # 主动退群：加入本地黑名单
            if (
                raw.get("post_type") == "notice"
                and raw.get("notice_type") == "group_decrease"
                and raw.get("sub_type") == "leave"
            ):
                group_id = str(raw.get("group_id") or "")
                user_id = str(raw.get("user_id") or "")
                if group_id and user_id and not self._is_blacklisted(group_id, user_id):
                    self._add_blacklist(group_id, user_id, reason="主动退群(本地)", by_user="system")
                    yield event.plain_result(f"{user_id} 主动退群，已加入本地黑名单")
        except Exception as e:
            logger.error(f"处理群事件失败: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_dot_prefix(self, event: AstrMessageEvent):
        """兼容 .sunos 前缀的 bl 子命令。"""
        try:
            msg = event.message_str.strip()
            if not msg.startswith(".sunos"):
                return
            if not event.get_group_id():
                return
            args = msg.split()
            if len(args) >= 2 and args[0].endswith("sunos") and args[1] == "bl":
                async for res in self._handle_bl_commands(event, args):
                    yield res
        except Exception as e:
            logger.error(f".sunos bl 关键词匹配处理失败: {e}")

    async def terminate(self):
        logger.info("SunBlacklist 插件 v1.0.0 已卸载")
