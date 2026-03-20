"""
飞书文档读写客户端 — Open API

来源：
- launch-report/feishu_doc.py（主体，已验证正确）
- libra-report-tool/report_renderer.py（补充 color_name_to_int 工具方法）

图片插入使用三步流程（已验证）：
1. 创建空图片 block (type=27)
2. 上传图片到飞书 Drive，parent_node = 图片 block ID
3. PATCH replace_image 关联
"""

import os
from typing import Optional

import requests
from PIL import Image


class FeishuDoc:
    """飞书文档读写客户端

    用法：
        doc = FeishuDoc(app_id="cli_xxx", app_secret="xxx")
        doc.auth()

        # 写入已有文档
        doc.document_id = "LHQxdiSJAo7zJXxjw2pl28yqgsf"
        doc.append_text("Hello")
        doc.append_table(["col1", "col2"], [["a", "b"]])
        doc.append_image("/path/to/image.png")

        # 创建新文档
        new_id = doc.create_document("报告标题")
        doc.append_heading("第一章", level=1)
    """

    def __init__(
        self,
        document_id: str = None,
        app_id: str = None,
        app_secret: str = None,
        api_base: str = None,
    ):
        """初始化飞书文档客户端

        Args:
            document_id: 文档 ID，可后续通过 create_document 设置
            app_id: 应用 ID，缺省时读取环境变量 LARK_APP_ID
            app_secret: 应用密钥，缺省时读取环境变量 LARK_APP_SECRET
            api_base: API 基础 URL，缺省时读取环境变量 LARK_API_BASE
        """
        self.document_id = document_id
        self.app_id = app_id or os.getenv("LARK_APP_ID", "")
        self.app_secret = app_secret or os.getenv("LARK_APP_SECRET", "")
        self.api_base = api_base or os.getenv(
            "LARK_API_BASE", "https://open.larksuite.com/open-apis"
        )
        self.token = None

    # ── 认证 ──────────────────────────────────────────────

    def auth(self) -> "FeishuDoc":
        """获取 tenant_access_token

        Returns:
            self（支持链式调用）

        Raises:
            ValueError: 缺少 app_id 或 app_secret
            Exception: 认证失败
        """
        if not self.app_id or not self.app_secret:
            raise ValueError(
                "app_id / app_secret 未提供，请传参或设置环境变量 LARK_APP_ID / LARK_APP_SECRET"
            )

        url = f"{self.api_base}/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"认证失败: {data.get('msg', data)}")
        self.token = data["tenant_access_token"]
        print(f"[feishu_sdk] 认证成功 (token: {self.token[:20]}...)")
        return self

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ── 文档操作 ──────────────────────────────────────────

    def create_document(self, title: str, folder_token: str = None) -> str:
        """创建新文档，自动更新 self.document_id

        Args:
            title: 文档标题
            folder_token: 目标文件夹 token，None 则创建在根目录

        Returns:
            新文档的 document_id
        """
        url = f"{self.api_base}/docx/v1/documents"
        payload = {"title": title}
        if folder_token:
            payload["folder_token"] = folder_token
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"创建文档失败: {data.get('msg', data)}")
        doc_id = data["data"]["document"]["document_id"]
        self.document_id = doc_id
        print(f"[feishu_sdk] 文档已创建: {title} ({doc_id})")
        return doc_id

    def get_document_info(self) -> dict:
        """获取文档基本信息"""
        url = f"{self.api_base}/docx/v1/documents/{self.document_id}"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        return resp.json()

    def get_blocks(self) -> dict:
        """获取文档所有 block"""
        url = f"{self.api_base}/docx/v1/documents/{self.document_id}/blocks"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        return resp.json()

    def get_block(self, block_id: str) -> dict:
        """获取单个 block 详情"""
        url = f"{self.api_base}/docx/v1/documents/{self.document_id}/blocks/{block_id}"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        return resp.json()

    def delete_all_blocks(self):
        """清空文档内容（删除根节点下所有子 block）"""
        blocks_resp = self.get_blocks()
        items = blocks_resp.get("data", {}).get("items", [])
        child_ids = [
            b["block_id"]
            for b in items
            if b["block_id"] != self.document_id
            and b.get("parent_id") == self.document_id
        ]
        if not child_ids:
            print("[feishu_sdk] 文档已经是空的")
            return
        url = (
            f"{self.api_base}/docx/v1/documents/{self.document_id}"
            f"/blocks/{self.document_id}/children/batch_delete"
            f"?document_revision_id=-1"
        )
        resp = requests.delete(
            url,
            json={"start_index": 0, "end_index": len(child_ids)},
            headers=self._headers(),
            timeout=10,
        )
        result = resp.json()
        if result.get("code") == 0:
            print(f"[feishu_sdk] 已清空 {len(child_ids)} 个 block")
        else:
            print(f"[feishu_sdk] 清空失败: {result.get('msg', result)}")
        return result

    # ── Block 写入 ────────────────────────────────────────

    def append_blocks(
        self, parent_block_id: str, children: list, index: int = -1
    ) -> dict:
        """向指定 block 追加子 block

        Args:
            parent_block_id: 父 block ID（文档根节点 = document_id）
            children: block 列表
            index: 插入位置，-1 表示追加到末尾
        """
        url = (
            f"{self.api_base}/docx/v1/documents/{self.document_id}"
            f"/blocks/{parent_block_id}/children?document_revision_id=-1"
        )
        payload = {"children": children}
        if index >= 0:
            payload["index"] = index
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=10)
        result = resp.json()
        if result.get("code") != 0:
            print(f"[feishu_sdk] append_blocks 失败: {result.get('msg', result)}")
        return result

    # ── Block 构建（静态方法）────────────────────────────

    @staticmethod
    def text_block(
        content: str, bold: bool = False, text_color: int = None
    ) -> dict:
        """构建文本段落 block (type=2)

        Args:
            content: 文本内容
            bold: 是否加粗
            text_color: 飞书颜色值（可用 color_name_to_int 转换）
        """
        style = {}
        if bold:
            style["bold"] = True
        if text_color is not None:
            style["text_color"] = text_color
        return {
            "block_type": 2,
            "text": {
                "elements": [
                    {
                        "text_run": {
                            "content": content,
                            "text_element_style": style,
                        }
                    }
                ]
            },
        }

    @staticmethod
    def heading_block(content: str, level: int = 1) -> dict:
        """构建标题 block (type=3~11 对应 H1~H9)

        Args:
            content: 标题文字
            level: 标题级别 1~9
        """
        block_type = level + 2  # H1=3, H2=4, ...
        key_map = {
            3: "heading1", 4: "heading2", 5: "heading3",
            6: "heading4", 7: "heading5", 8: "heading6",
            9: "heading7", 10: "heading8", 11: "heading9",
        }
        return {
            "block_type": block_type,
            key_map.get(block_type, "heading1"): {
                "elements": [
                    {
                        "text_run": {
                            "content": content,
                            "text_element_style": {},
                        }
                    }
                ]
            },
        }

    @staticmethod
    def divider_block() -> dict:
        """构建分割线 block (type=22)"""
        return {"block_type": 22, "divider": {}}

    @staticmethod
    def table_block(row_size: int, col_size: int) -> dict:
        """构建表格 block (type=31)

        Args:
            row_size: 行数（含表头）
            col_size: 列数
        """
        return {
            "block_type": 31,
            "table": {
                "property": {
                    "row_size": row_size,
                    "column_size": col_size,
                }
            },
        }

    # ── 表格单元格 ────────────────────────────────────────

    def write_table_cell(
        self,
        cell_block_id: str,
        content: str,
        bold: bool = False,
        text_color: int = None,
    ):
        """向表格单元格写入文本

        Args:
            cell_block_id: 单元格 block ID
            content: 文本内容
            bold: 是否加粗
            text_color: 飞书颜色值
        """
        self.append_blocks(
            cell_block_id,
            [self.text_block(content, bold=bold, text_color=text_color)],
            index=0,
        )

    def write_table_cell_image(self, cell_block_id: str, image_path: str) -> dict:
        """向表格单元格插入图片（四步流程）

        飞书创建表格时，每个 cell 默认自带一个空文本段落。
        插入图片后需要删掉这个空段落，否则会显示多余空行。

        Args:
            cell_block_id: 单元格 block ID
            image_path: 本地图片路径
        """
        # Step 0: 记录 cell 内现有的默认 block（稍后删除）
        cell_detail = self.get_block(cell_block_id)
        old_children = (
            cell_detail.get("data", {}).get("block", {}).get("children", [])
        )

        # Step 1: 在单元格内创建空图片 block（index=0 插到最前）
        result = self.append_blocks(
            cell_block_id, [{"block_type": 27, "image": {}}], index=0
        )
        if result.get("code") != 0:
            print(f"[feishu_sdk] 表格单元格插图失败: {result.get('msg', result)}")
            return result

        children = result.get("data", {}).get("children", [])
        if not children:
            print("[feishu_sdk] 未能获取单元格内图片 block ID")
            return result
        image_block_id = children[0]["block_id"]

        # Step 2: 上传图片
        file_token, width, height = self.upload_image(
            image_path, parent_node=image_block_id
        )

        # Step 3: 关联图片
        replace_result = self.replace_image(image_block_id, file_token)

        # Step 4: 删除 cell 内原有的默认空段落（消除多余空行）
        if old_children:
            try:
                url = (
                    f"{self.api_base}/docx/v1/documents/{self.document_id}"
                    f"/blocks/{cell_block_id}/children/batch_delete"
                    f"?document_revision_id=-1"
                )
                # 图片在 index=0，默认段落被推到 index=1 开始
                requests.delete(
                    url,
                    json={"start_index": 1, "end_index": 1 + len(old_children)},
                    headers=self._headers(),
                    timeout=10,
                )
            except Exception:
                pass  # 删除失败不影响主流程

        return replace_result

    # ── 图片 ──────────────────────────────────────────────

    def upload_image(
        self, image_path: str, parent_node: str = None
    ) -> tuple:
        """上传图片到飞书 Drive

        Args:
            image_path: 本地图片路径
            parent_node: 关联的 block ID（图片 block 的 ID）

        Returns:
            (file_token, width, height)
        """
        url = f"{self.api_base}/drive/v1/medias/upload_all"
        file_size = os.path.getsize(image_path)
        filename = os.path.basename(image_path)

        with Image.open(image_path) as img:
            width, height = img.size

        node = parent_node or self.document_id
        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                files={
                    "file_name": (None, filename),
                    "parent_type": (None, "docx_image"),
                    "parent_node": (None, node),
                    "size": (None, str(file_size)),
                    "file": (filename, f, "image/png"),
                },
                timeout=30,
            )

        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"图片上传失败: {data.get('msg', data)}")
        token = data["data"]["file_token"]
        print(f"[feishu_sdk] 图片已上传: {filename} -> {token} ({width}x{height})")
        return token, width, height

    def replace_image(self, block_id: str, file_token: str) -> dict:
        """用 PATCH replace_image 将图片关联到已有的图片 block

        Args:
            block_id: 图片 block ID
            file_token: upload_image 返回的 token
        """
        url = (
            f"{self.api_base}/docx/v1/documents/{self.document_id}"
            f"/blocks/{block_id}?document_revision_id=-1"
        )
        payload = {"replace_image": {"token": file_token}}
        resp = requests.patch(url, json=payload, headers=self._headers(), timeout=10)
        result = resp.json()
        if result.get("code") != 0:
            print(f"[feishu_sdk] replace_image 失败: {result.get('msg', result)}")
        return result

    # ── 便捷方法 ──────────────────────────────────────────

    def append_text(self, content: str, **kwargs):
        """直接追加一段文字到文档末尾"""
        return self.append_blocks(
            self.document_id, [self.text_block(content, **kwargs)]
        )

    def append_heading(self, content: str, level: int = 1):
        """直接追加标题到文档末尾"""
        return self.append_blocks(
            self.document_id, [self.heading_block(content, level)]
        )

    def append_divider(self):
        """直接追加分割线到文档末尾"""
        return self.append_blocks(self.document_id, [self.divider_block()])

    def append_image(self, image_path: str) -> dict:
        """插入图片到文档末尾（三步流程）

        1. 创建空图片 block -> 拿到 block_id
        2. 上传图片文件，parent_node 指向图片 block
        3. PATCH replace_image 关联图片到 block

        Args:
            image_path: 本地图片路径
        """
        # Step 1: 创建空图片 block
        result = self.append_blocks(
            self.document_id, [{"block_type": 27, "image": {}}]
        )
        if result.get("code") != 0:
            return result

        children = result.get("data", {}).get("children", [])
        if not children:
            print("[feishu_sdk] 未能获取图片 block ID")
            return result
        image_block_id = children[0]["block_id"]

        # Step 2: 上传图片到该 block
        file_token, width, height = self.upload_image(
            image_path, parent_node=image_block_id
        )

        # Step 3: 关联图片
        return self.replace_image(image_block_id, file_token)

    def append_table(
        self,
        headers: list,
        rows: list,
        header_bold: bool = True,
    ) -> dict:
        """创建表格并填充数据

        Args:
            headers: 表头列表，如 ["指标", "v0", "v1", "变化"]
            rows: 数据行，如 [["Active Days", "5.12", "5.13", "+0.19%"], ...]
            header_bold: 表头是否加粗
        """
        row_count = len(rows) + 1  # +1 表头
        col_count = len(headers)

        # 创建空表格
        result = self.append_blocks(
            self.document_id, [self.table_block(row_count, col_count)]
        )
        if result.get("code") != 0:
            return result

        # 获取表格 block_id
        children = result.get("data", {}).get("children", [])
        if not children:
            print("[feishu_sdk] 未能获取表格 block")
            return result

        table_block_id = children[0]["block_id"]

        # 获取单元格 ID
        table_detail = self.get_block(table_block_id)
        cells = (
            table_detail.get("data", {})
            .get("block", {})
            .get("table", {})
            .get("cells", [])
        )

        if not cells:
            print("[feishu_sdk] 未能获取表格单元格")
            return result

        # cells 是一维数组，按行优先排列
        # 写表头
        for col_idx, header in enumerate(headers):
            cell_idx = col_idx  # 第一行
            if cell_idx < len(cells):
                self.write_table_cell(cells[cell_idx], header, bold=header_bold)

        # 写数据行
        for row_idx, row in enumerate(rows):
            for col_idx, cell_content in enumerate(row):
                cell_idx = (row_idx + 1) * col_count + col_idx
                if cell_idx < len(cells):
                    self.write_table_cell(cells[cell_idx], str(cell_content))

        print(f"[feishu_sdk] 表格已写入: {row_count} 行 x {col_count} 列")
        return result

    TABLE_FULL_WIDTH = 1000  # 表格总宽度（填满页面）

    def create_empty_table(self, row_size: int, col_size: int) -> tuple:
        """创建空表格，返回 (table_block_id, cells)

        列宽自动计算：TABLE_FULL_WIDTH / col_size，确保表格填满页面。

        Args:
            row_size: 行数
            col_size: 列数

        Returns:
            (table_block_id, cells): cells 是一维数组，按行优先排列
        """
        col_width = self.TABLE_FULL_WIDTH // col_size
        block = {
            "block_type": 31,
            "table": {
                "property": {
                    "row_size": row_size,
                    "column_size": col_size,
                    "column_width": [col_width] * col_size,
                }
            },
        }
        result = self.append_blocks(self.document_id, [block])
        if result.get("code") != 0:
            raise Exception(f"创建表格失败: {result.get('msg', result)}")

        children = result.get("data", {}).get("children", [])
        if not children:
            raise Exception("未能获取表格 block")

        table_block_id = children[0]["block_id"]

        table_detail = self.get_block(table_block_id)
        cells = (
            table_detail.get("data", {})
            .get("block", {})
            .get("table", {})
            .get("cells", [])
        )
        if not cells:
            raise Exception("未能获取表格单元格")

        return table_block_id, cells

    # ── 权限管理 ──────────────────────────────────────────

    def add_collaborator(
        self, email: str, perm: str = "full_access"
    ) -> dict:
        """添加用户为文档协作者

        Args:
            email: 用户飞书邮箱
            perm: 权限 - "full_access"(可管理) / "edit"(可编辑) / "view"(可阅读)

        注意：需要应用开通 scope: drive:drive:permission_member:create
        """
        url = (
            f"{self.api_base}/drive/v1/permissions/{self.document_id}"
            f"/members?type=docx&need_notification=true"
        )
        payload = {
            "member_type": "email",
            "member_id": email,
            "perm": perm,
        }
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[feishu_sdk] 已添加协作者: {email} ({perm})")
        else:
            print(f"[feishu_sdk] 添加协作者失败: {result.get('msg', result)}")
        return result

    def set_public_permission(
        self, link_share_entity: str = "tenant_readable"
    ) -> dict:
        """设置文档公开权限

        Args:
            link_share_entity: 链接分享范围
                - "tenant_readable": 组织内获得链接可阅读
                - "tenant_editable": 组织内获得链接可编辑
                - "anyone_readable": 互联网获得链接可阅读
                - "anyone_editable": 互联网获得链接可编辑
        """
        url = (
            f"{self.api_base}/drive/v1/permissions/{self.document_id}"
            f"/public?type=docx"
        )
        payload = {
            "external_access_entity": "open",
            "security_entity": "anyone_can_view",
            "comment_entity": "anyone_can_view",
            "share_entity": "anyone",
            "link_share_entity": link_share_entity,
        }
        resp = requests.patch(url, json=payload, headers=self._headers(), timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[feishu_sdk] 已设置文档权限: {link_share_entity}")
        else:
            print(f"[feishu_sdk] 设置权限失败: {result.get('msg', result)}")
        return result

    # ── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def color_name_to_int(color: str) -> Optional[int]:
        """颜色字符串转飞书颜色值

        Args:
            color: "green" / "red" / "gray"

        Returns:
            飞书颜色整数值，未知颜色返回 None
        """
        color_map = {
            "dark_red": 1,
            "orange": 2,
            "yellow": 3,
            "green": 4,
            "blue": 5,
            "purple": 6,
            "gray": 7,
            "red": 1,  # alias: red -> dark_red
        }
        return color_map.get(color)
