"""知识库管理路由。

POST   /api/knowledge/upload — 上传文件到知识库
DELETE /api/knowledge         — 清空知识库
"""

import os
import shutil
import tempfile
from typing import List

from fastapi import APIRouter, Depends, UploadFile, File

from src.api.dependencies import get_service
from src.api.schemas import ApiResponse, UploadData
from src.services import AgentService

router = APIRouter()


@router.post("/knowledge/upload", summary="上传文件到知识库")
async def upload_files(
    files: List[UploadFile] = File(...),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """将上传的文件导入知识库。

    支持 .txt / .md / .pdf 格式。
    文件先保存到临时目录，导入后自动清理。
    """
    tmp_dir = tempfile.mkdtemp(prefix="agent_upload_")
    file_paths = []

    try:
        # 保存上传文件到临时目录
        for upload_file in files:
            file_path = os.path.join(tmp_dir, upload_file.filename or "unknown")
            with open(file_path, "wb") as f:
                content = await upload_file.read()
                f.write(content)
            file_paths.append(file_path)

        data = service.upload_files(file_paths)
        if data.get("error"):
            return ApiResponse(success=False, error=data["error"])
        return ApiResponse(data=UploadData(**data))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.delete("/knowledge", summary="清空知识库")
def clear_knowledge_base(
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    success = service.clear_knowledge_base()
    if success:
        return ApiResponse(data={"message": "知识库已清空"})
    return ApiResponse(success=False, error="知识库未初始化")
