"""
IO Utilities

提供原子文件写入等IO辅助功能。
"""

from pathlib import Path
from typing import Callable, TypeVar, Union

import contextlib
import os
import shutil
import tempfile

_T = TypeVar("_T")


def _path_needs_ascii_bridge(path: Path) -> bool:
    """FAISS 等 C API 在 Windows 上无法可靠打开含非 ASCII 字符的路径。"""
    try:
        os.fsdecode(os.fsencode(str(path))).encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def _replace_file(src: Path, dst: Path) -> None:
    """尽量原子替换；跨卷时回退到 copy+remove。"""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        try:
            os.remove(src)
        except OSError:
            pass


@contextlib.contextmanager
def atomic_write(file_path: Union[str, Path], mode: str = "w", encoding: str = None, **kwargs):
    """
    原子文件写入上下文管理器

    原理：
    1. 写入 .tmp 临时文件
    2. 写入成功后，使用 os.replace 原子替换目标文件
    3. 如果失败，自动删除临时文件

    Args:
        file_path: 目标文件路径
        mode: 打开模式 ('w', 'wb' 等)
        encoding: 编码
        **kwargs: 传给 open() 的其他参数
    """
    path = Path(file_path)
    # 确保父目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    # 临时文件路径
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with open(tmp_path, mode, encoding=encoding, **kwargs) as f:
            yield f

            # 确保写入磁盘
            f.flush()
            os.fsync(f.fileno())

        # 原子替换 (Windows下可能需要先删除目标文件，但 os.replace 在 Py3.3+ 尽可能原子)
        # 注意: Windows 上如果有其他进程占用文件，os.replace 可能会失败
        os.replace(tmp_path, path)

    except Exception:
        # 清理临时文件
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


@contextlib.contextmanager
def atomic_save_path(file_path: Union[str, Path]):
    """
    提供临时路径用于原子保存 (针对只接受路径的API，如Faiss)

    若目标路径含非 ASCII 字符（常见于 Windows 中文目录），临时文件会写到
    系统 ASCII 临时目录，再搬回目标路径，避免 FAISS C API 打不开文件。

    Args:
        file_path: 最终目标路径

    Yields:
        tmp_path: 临时文件路径 (str)
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    bridge_dir = None
    if _path_needs_ascii_bridge(path):
        bridge_dir = tempfile.mkdtemp(prefix="faiss_io_")
        tmp_path = Path(bridge_dir) / f"{path.name}.tmp"
    else:
        tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        yield str(tmp_path)

        if Path(tmp_path).exists():
            _replace_file(Path(tmp_path), path)

    except Exception:
        if Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    finally:
        if bridge_dir is not None:
            shutil.rmtree(bridge_dir, ignore_errors=True)


def call_with_ascii_path(file_path: Union[str, Path], reader: Callable[[str], _T]) -> _T:
    """
    对只接受路径字符串的 C API（如 faiss.read_index）做非 ASCII 路径桥接。

    路径可被 ASCII 编码时直接调用；否则先复制到系统临时目录再读。
    """
    path = Path(file_path)
    if not _path_needs_ascii_bridge(path):
        return reader(str(path))

    bridge_dir = tempfile.mkdtemp(prefix="faiss_io_")
    try:
        tmp_path = Path(bridge_dir) / path.name
        shutil.copy2(path, tmp_path)
        return reader(str(tmp_path))
    finally:
        shutil.rmtree(bridge_dir, ignore_errors=True)
