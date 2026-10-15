"""
剪映 AI ToolBox — 全自动生产力工具箱
Built with CustomTkinter for a modern native look.
"""

import webview
import customtkinter as ctk
from tkinter import ttk, filedialog
import tkinter as tk
import ctypes
import threading, sys, time, os, re, json, subprocess, shutil
from pathlib import Path
import pyautogui, requests
from PIL import Image
from pynput import keyboard
from google import genai
from google.genai import types

pyautogui.FAILSAFE = False

# ─── 版本与授权 ─────────────────────────────────────────────
AUTH_URL  = "https://raw.githubusercontent.com/Kirota233/JianYing-AI-Pro/master/auth.json"
VERSION   = "2.0"
CFG_FILE  = "config.json"
DEFAULT_KEY = "AIzaSyDQ4s-9ynGQcJw6oNDF5G2fNewnuF1zkaY"

# ─── 颜色常量 ──────────────────────────────────────────────
C_BG       = "#f0f2f5"
C_CARD     = "#ffffff"
C_PRIMARY  = "#3b82f6"
C_SUCCESS  = "#22c55e"
C_DANGER   = "#ef4444"
C_WARN     = "#f59e0b"
C_TEXT     = "#1e293b"
C_MUTED    = "#94a3b8"
C_LOG_BG   = "#f8fafc"
C_BORDER   = "#e2e8f0"


def _rename_get_episode(name):
    """从文件名中提取集数，优先识别“第 N 集”格式。"""
    cleaned = re.sub(r"\(\d+\)\s*$", "", name).strip()
    match = re.search(r"第\s*(\d+)\s*集", cleaned)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*集", cleaned)
    if match:
        return int(match.group(1))
    matches = re.findall(r"\d+", cleaned)
    return int(matches[-1]) if matches else None


def _rename_is_duplicate_copy(name):
    return re.search(r"\(\d+\)\s*$", name) is not None


def _rename_parse_template(template):
    """将包含数字的示例模板拆成数字前缀和后缀。"""
    match = re.search(r"\d+", template)
    if not match:
        return None
    return template[:match.start()], template[match.end():]


def _rename_plan(folder, prefix, suffix, extensions=(".mp4", ".srt", ".mp3", ".wav", ".mkv")):
    """生成重命名计划，不修改文件。"""
    ext_set = tuple(ext.lower() for ext in extensions)
    files = [
        filename for filename in sorted(os.listdir(folder))
        if os.path.isfile(os.path.join(folder, filename))
        and filename.lower().endswith(ext_set)
    ]
    files.sort(key=lambda filename: _rename_is_duplicate_copy(os.path.splitext(filename)[0]))

    planned = []
    skipped_duplicates = []
    skipped_without_number = []
    used_targets = set()
    for filename in files:
        stem, extension = os.path.splitext(filename)
        number = _rename_get_episode(stem)
        if number is None:
            skipped_without_number.append(filename)
            continue
        new_name = f"{prefix}{number}{suffix}{extension}"
        if new_name in used_targets:
            skipped_duplicates.append((filename, new_name))
            continue
        used_targets.add(new_name)
        planned.append((filename, new_name))
    return planned, skipped_duplicates, skipped_without_number

# ─── 工具函数 ──────────────────────────────────────────────
def fmt_time(us):
    ms = int(us)//1000; s, ms = divmod(ms, 1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class _MsvcString(ctypes.Structure):
    _fields_ = [
        ("storage", ctypes.c_ubyte * 16),
        ("size", ctypes.c_size_t),
        ("capacity", ctypes.c_size_t),
    ]


def _msvc_input_string(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    backing = ctypes.create_string_buffer(raw + b"\0")
    value = _MsvcString()
    if len(raw) < 16:
        value.storage[:len(raw)] = raw
    else:
        ctypes.c_void_p.from_buffer(value.storage).value = ctypes.addressof(backing)
    value.size = len(raw)
    value.capacity = max(15, len(raw))
    return value, backing


def _msvc_output_string(value):
    if value.size > 256 * 1024 * 1024:
        raise ValueError(f"剪映接口返回了异常的数据长度: {value.size}")
    if value.capacity < 16:
        return bytes(value.storage[:value.size])
    pointer = ctypes.c_void_p.from_buffer(value.storage).value
    if not pointer:
        raise ValueError("剪映接口返回了空数据指针")
    return ctypes.string_at(pointer, value.size)


class JianyingDraftIO:
    """使用当前安装的剪映接口读写新版加密 draft_content.json。"""

    FACTORY_CTOR = b"??0DraftIOFactoryBase@draft_store@@QEAA@XZ"
    CREATE_IO = b"?createIO@DraftIOFactoryBase@draft_store@@UEAA?AV?$shared_ptr@UIDraftIO@draft_store@@@std@@PEBV?$array@D$0CA@@4@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@4@@Z"
    CALC_ENTROPY = b"?calcEntropyDynamicString@DraftIOFactoryBase@draft_store@@UEAA?AU?$pair@V?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@V?$array@D$0CA@@2@@std@@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@4@@Z"

    def __init__(self, dll_path=None):
        if os.name != "nt":
            raise RuntimeError("加密草稿读写仅支持 Windows")
        self.dll_path = Path(dll_path or self._find_videoeditor_dll()).resolve()
        self._dll_dir = os.add_dll_directory(str(self.dll_path.parent))
        self.library = ctypes.WinDLL(str(self.dll_path))
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._kernel32.GetProcAddress.restype = ctypes.c_void_p

        self.factory = (ctypes.c_ubyte * 24)()
        constructor = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.FACTORY_CTOR))
        constructor(ctypes.addressof(self.factory))

        self._create_io = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.CREATE_IO))
        self._calc_entropy = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.CALC_ENTROPY))

    @staticmethod
    def _find_videoeditor_dll():
        candidates = []
        override = os.environ.get("JIANYING_VIDEOEDITOR_DLL", "").strip()
        if override:
            candidates.append(Path(override))

        roots = [
            Path.home() / "JianyingPro",
            Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro",
            Path(os.environ.get("ProgramFiles", "")) / "JianyingPro",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "JianyingPro",
            Path("D:/JianyingPro"),
            Path("E:/JianyingPro"),
        ]
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Bytedance\JianyingPro")
            install_dir, _ = winreg.QueryValueEx(key, "installDir")
            if install_dir:
                roots.insert(0, Path(install_dir))
        except Exception:
            pass
        for root in roots:
            if not root.is_dir():
                continue
            direct = root / "videoeditor.dll"
            if direct.is_file():
                candidates.append(direct)
            try:
                candidates.extend(root.glob("*/videoeditor.dll"))
                candidates.extend(root.glob("*/*/videoeditor.dll"))
            except OSError:
                pass

        candidates = list({str(path.resolve()): path.resolve() for path in candidates if path.is_file()}.values())
        if not candidates:
            raise FileNotFoundError(
                "未找到剪映 videoeditor.dll；可设置环境变量 JIANYING_VIDEOEDITOR_DLL 指向该文件"
            )

        def version_key(path):
            numbers = tuple(int(part) for part in re.findall(r"\d+", path.parent.name))
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0
            return numbers, modified

        return max(candidates, key=version_key)

    def _address(self, name):
        result = self._kernel32.GetProcAddress(self.library._handle, name)
        if not result:
            raise RuntimeError(f"当前剪映版本缺少草稿接口: {name!r}")
        return result

    @staticmethod
    def _attachment_path(draft_path):
        draft_path = Path(draft_path).resolve()
        parents = [draft_path.parent]
        parents.extend(list(draft_path.parents)[1:5])
        for parent in parents:
            candidate = parent / "attachment_pc_common.json"
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"找不到 attachment_pc_common.json: {draft_path}")

    def _entropy(self, draft_path):
        attachment = self._attachment_path(draft_path)
        source_text = attachment.read_text(encoding="utf-8-sig")
        source, backing = _msvc_input_string(source_text)
        result = (ctypes.c_ubyte * 64)()
        self._calc_entropy(
            ctypes.addressof(self.factory), ctypes.addressof(result), ctypes.addressof(source)
        )
        entropy = bytes(result[32:64])
        if not any(entropy):
            raise RuntimeError(f"无法计算草稿密钥: {draft_path}")
        return entropy

    def _open(self, draft_path):
        draft_path = str(Path(draft_path).resolve())
        entropy = (ctypes.c_ubyte * 32).from_buffer_copy(self._entropy(draft_path))
        path_value, path_backing = _msvc_input_string(draft_path)
        shared_io = (ctypes.c_void_p * 2)()
        self._create_io(
            ctypes.addressof(self.factory), ctypes.addressof(shared_io),
            ctypes.addressof(entropy), ctypes.addressof(path_value)
        )
        object_pointer = shared_io[0]
        if not object_pointer:
            raise RuntimeError(f"无法打开剪映草稿: {draft_path}")
        return object_pointer

    @staticmethod
    def _virtual(object_pointer, index, argtypes):
        vtable = ctypes.c_void_p.from_address(object_pointer).value
        address = ctypes.c_void_p.from_address(vtable + index * 8).value
        return ctypes.CFUNCTYPE(ctypes.c_void_p, *argtypes)(address)

    def read_json(self, draft_path):
        object_pointer = self._open(draft_path)
        result = (ctypes.c_ubyte * 0x58)()
        read = self._virtual(
            object_pointer, 3, (ctypes.c_void_p, ctypes.c_void_p)
        )
        read(object_pointer, ctypes.addressof(result))
        result_code = ctypes.c_uint32.from_buffer(result, 0x48).value
        detail = _msvc_output_string(_MsvcString.from_buffer(result, 0x28)).decode(
            "utf-8", errors="replace"
        )
        raw = _msvc_output_string(_MsvcString.from_buffer(result, 0x08))
        if result_code:
            raise RuntimeError(f"剪映草稿解密失败({result_code}): {detail}")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"剪映草稿解密后不是有效 JSON: {draft_path}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"剪映草稿根节点不是对象: {draft_path}")
        return data

    def write_json(self, draft_path, data):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        object_pointer = self._open(draft_path)
        value, backing = _msvc_input_string(raw)
        result = (ctypes.c_ubyte * 0x30)()
        write = self._virtual(
            object_pointer, 7,
            (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        )
        write(object_pointer, ctypes.addressof(result), ctypes.addressof(value))
        result_code = ctypes.c_uint32.from_buffer(result, 0x20).value
        detail = _msvc_output_string(_MsvcString.from_buffer(result)).decode(
            "utf-8", errors="replace"
        )
        if result_code:
            raise RuntimeError(f"剪映草稿加密写入失败({result_code}): {detail}")


BANKNOTE_HTML = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>JIANYING AI PRO v2.0.0</title>\n<style>\n  :root {\n    --bg-ivory: #FBF9F5;\n    --bg-audit: #F2EFE9;\n    --bg-card: #FFFFFF;\n    --crimson: #9E1B16;\n    --crimson-hover: #7D1410;\n    --ink: #1A1817;\n    --muted: #6E6A63;\n    --border-line: #D6D1C7;\n    --emerald: #0B6E4F;\n    --amber: #B45309;\n    --danger: #B91C1C;\n    --font-sans: \'Inter\', -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;\n    --font-mono: \'JetBrains Mono\', \'Consolas\', \'SFMono-Regular\', monospace;\n  }\n\n  * {\n    box-sizing: border-box;\n    margin: 0;\n    padding: 0;\n  }\n\n  html, body {\n    width: 100%;\n    height: 100%;\n    overflow: hidden;\n    font-family: var(--font-sans);\n    color: var(--ink);\n    background-color: var(--bg-ivory);\n    background-image:\n      repeating-linear-gradient(\n        0deg,\n        transparent,\n        transparent 3px,\n        rgba(158, 27, 22, 0.035) 3px,\n        rgba(158, 27, 22, 0.035) 4px\n      ),\n      repeating-linear-gradient(\n        90deg,\n        transparent,\n        transparent 23px,\n        rgba(26, 24, 23, 0.02) 23px,\n        rgba(26, 24, 23, 0.02) 24px\n      );\n    user-select: none;\n    -webkit-font-smoothing: antialiased;\n  }\n\n  /* 单栏上下布局：上为主体工作区，下为日志与状态区 */\n  .banknote-shell {\n    width: 112.36vw;\n    height: 112.36vh;\n    zoom: 0.89;\n    border: 2px solid var(--ink);\n    display: flex;\n    flex-direction: column;\n    position: relative;\n    overflow: hidden;\n  }\n\n  /* 一体化可拖拽自定义瑞士顶栏 */\n  .custom-titlebar {\n    height: 30px;\n    background: var(--ink);\n    color: var(--bg-ivory);\n    border-bottom: 1.5px solid var(--ink);\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    padding: 0 0 0 12px;\n    flex-shrink: 0;\n    cursor: move;\n  }\n  .ct-brand {\n    display: flex;\n    align-items: center;\n    gap: 8px;\n    font-family: var(--font-mono);\n    font-size: 11px;\n    font-weight: 800;\n    letter-spacing: 0.06em;\n  }\n  .ct-cross {\n    background: var(--crimson);\n    color: #FFFFFF;\n    width: 16px;\n    height: 16px;\n    display: inline-flex;\n    align-items: center;\n    justify-content: center;\n    font-size: 11px;\n    font-weight: 900;\n  }\n  .ct-controls {\n    display: flex;\n    height: 100%;\n  }\n  .ct-btn {\n    width: 34px;\n    height: 100%;\n    border: none;\n    border-left: 1px solid #33302E;\n    background: transparent;\n    color: var(--bg-ivory);\n    font-family: var(--font-mono);\n    font-size: 12px;\n    font-weight: 700;\n    cursor: pointer;\n    display: flex;\n    align-items: center;\n    justify-content: center;\n    transition: background 0.1s;\n  }\n  .ct-btn:hover {\n    background: #33302E;\n  }\n  .ct-btn.close:hover {\n    background: var(--crimson);\n    color: #FFFFFF;\n  }\n\n  /* =========================================================\n     TOP & MAIN WORKSPACE\n     ========================================================= */\n  .bn-center {\n    padding: 10px 14px 8px 14px;\n    display: flex;\n    flex-direction: column;\n    flex: 1;\n    overflow: hidden;\n  }\n\n  /* Version & Coordinate Status Bar */\n  .sn-bar {\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    margin-bottom: 2px;\n  }\n\n  .sn-code {\n    font-family: var(--font-mono);\n    font-size: 11px;\n    font-weight: 800;\n    letter-spacing: 0.04em;\n    color: var(--crimson);\n  }\n\n  .coord-stamp {\n    font-family: var(--font-mono);\n    font-size: 10px;\n    font-weight: 700;\n    padding: 2px 8px;\n    background: var(--crimson);\n    color: #FFFFFF;\n    border: 1px solid var(--ink);\n    white-space: nowrap;\n  }\n  .coord-stamp.ready {\n    background: var(--emerald);\n  }\n  .coord-stamp.recording {\n    background: var(--amber);\n  }\n\n  .title-bar {\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    padding-bottom: 6px;\n    border-bottom: 2px solid var(--ink);\n  }\n\n  .main-title {\n    font-size: 18px;\n    font-weight: 900;\n    letter-spacing: -0.02em;\n    color: var(--ink);\n  }\n\n  /* 4-Tab Navigation */\n  .bn-tabs {\n    display: grid;\n    grid-template-columns: repeat(4, 1fr);\n    border: 1.5px solid var(--ink);\n    margin: 8px 0;\n    background: var(--ink);\n    gap: 1px;\n  }\n\n  .bn-tab {\n    background: var(--bg-card);\n    color: var(--ink);\n    padding: 6px 4px;\n    font-size: 11px;\n    font-weight: 800;\n    text-align: center;\n    cursor: pointer;\n    transition: all 0.1s ease;\n    font-family: var(--font-sans);\n    border: none;\n    outline: none;\n    white-space: nowrap;\n  }\n  .bn-tab:hover {\n    background: var(--bg-audit);\n  }\n  .bn-tab.active {\n    background: var(--ink);\n    color: var(--bg-ivory);\n  }\n\n  /* Module Stage Panels */\n  .module-panel {\n    display: none;\n    flex: 1;\n    flex-direction: column;\n    justify-content: space-between;\n    overflow: hidden;\n  }\n  .module-panel.active {\n    display: flex;\n  }\n\n  /* Swiss Ledger Box */\n  .ledger-box {\n    border: 1.5px solid var(--ink);\n    background: var(--bg-card);\n    padding: 8px 10px;\n    display: flex;\n    flex-direction: column;\n    box-shadow: 2px 2px 0px rgba(26, 24, 23, 0.06);\n  }\n  .ledger-box.grow {\n    flex: 1;\n    overflow: hidden;\n    margin-bottom: 8px;\n  }\n\n  .ledger-hdr {\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    font-size: 11px;\n    font-weight: 800;\n    color: var(--ink);\n    border-bottom: 1.5px solid var(--ink);\n    padding-bottom: 4px;\n    margin-bottom: 6px;\n  }\n\n  /* Table Container */\n  .table-scroll {\n    flex: 1;\n    overflow-y: auto;\n    overflow-x: hidden;\n  }\n  .table-scroll::-webkit-scrollbar {\n    width: 5px;\n  }\n  .table-scroll::-webkit-scrollbar-track {\n    background: var(--bg-audit);\n  }\n  .table-scroll::-webkit-scrollbar-thumb {\n    background: var(--ink);\n  }\n\n  table.voucher-table {\n    width: 100%;\n    border-collapse: collapse;\n    font-size: 11px;\n  }\n  table.voucher-table th {\n    position: sticky;\n    top: 0;\n    background: var(--bg-audit);\n    color: var(--ink);\n    font-size: 10px;\n    font-weight: 800;\n    text-align: left;\n    padding: 4px 8px;\n    border-bottom: 1px solid var(--ink);\n    z-index: 1;\n    white-space: nowrap;\n  }\n  table.voucher-table td {\n    padding: 5px 8px;\n    border-bottom: 1px dotted #C8C2B8;\n    font-weight: 600;\n    cursor: pointer;\n  }\n  table.voucher-table tr:hover td {\n    background: #F6F2EB;\n  }\n  table.voucher-table tr.selected td {\n    background: var(--crimson);\n    color: #FFFFFF;\n    font-weight: 700;\n    border-bottom-color: var(--crimson);\n  }\n  .empty-hint {\n    padding: 24px 10px;\n    text-align: center;\n    font-size: 11px;\n    color: var(--muted);\n  }\n\n  /* Buttons (strictly single-line no-wrap) */\n  .action-row {\n    display: flex;\n    gap: 6px;\n    align-items: center;\n  }\n\n  .btn-voucher {\n    border: 1.5px solid var(--ink);\n    background: var(--bg-card);\n    color: var(--ink);\n    padding: 6px 10px;\n    font-size: 11px;\n    font-weight: 800;\n    cursor: pointer;\n    transition: all 0.1s ease;\n    font-family: var(--font-sans);\n    display: inline-flex;\n    align-items: center;\n    justify-content: center;\n    gap: 4px;\n    white-space: nowrap;\n    flex-shrink: 0;\n  }\n  .btn-voucher:hover:not(:disabled) {\n    background: var(--ink);\n    color: var(--bg-ivory);\n  }\n  .btn-voucher:disabled {\n    opacity: 0.45;\n    cursor: not-allowed;\n  }\n\n  .btn-voucher.crimson {\n    background: var(--crimson);\n    color: #FFFFFF;\n    box-shadow: 2px 2px 0px var(--ink);\n  }\n  .btn-voucher.crimson:hover:not(:disabled) {\n    background: var(--crimson-hover);\n  }\n\n  .btn-voucher.ink {\n    background: var(--ink);\n    color: var(--bg-ivory);\n  }\n  .btn-voucher.ink:hover:not(:disabled) {\n    background: var(--crimson);\n    color: #FFFFFF;\n  }\n\n  .btn-voucher.flex-1 {\n    flex: 1;\n  }\n\n  /* Inputs */\n  .field-row {\n    display: flex;\n    gap: 6px;\n    align-items: center;\n  }\n  .voucher-input, .voucher-select {\n    flex: 1;\n    height: 28px;\n    border: 1.5px solid var(--ink);\n    background: var(--bg-ivory);\n    color: var(--ink);\n    padding: 0 8px;\n    font-size: 11px;\n    font-family: var(--font-mono);\n    font-weight: 600;\n    outline: none;\n    min-width: 0;\n  }\n  .voucher-input:focus, .voucher-select:focus {\n    background: #FFFFFF;\n    border-color: var(--crimson);\n  }\n\n  /* =========================================================\n     BOTTOM TELEMETRY & STATUS SECTION (置于主体之下)\n     ========================================================= */\n  .bn-bottom {\n    border-top: 2px solid var(--ink);\n    background: var(--bg-audit);\n    padding: 6px 14px 8px 14px;\n    display: flex;\n    flex-direction: column;\n    gap: 5px;\n    height: 108px;\n    flex-shrink: 0;\n  }\n\n  .bottom-meta-row {\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    font-size: 11px;\n  }\n\n  .wm-status {\n    font-weight: 800;\n    color: var(--ink);\n    display: flex;\n    align-items: center;\n    gap: 6px;\n    white-space: nowrap;\n  }\n  .wm-dot {\n    width: 7px;\n    height: 7px;\n    background: var(--crimson);\n    display: inline-block;\n  }\n\n  .spec-inline {\n    font-family: var(--font-mono);\n    font-size: 10px;\n    color: var(--muted);\n    display: flex;\n    gap: 10px;\n    white-space: nowrap;\n  }\n\n  .audit-log-box {\n    flex: 1;\n    border: 1px solid var(--ink);\n    background: #EAE5DC;\n    padding: 5px 8px;\n    font-family: var(--font-mono);\n    font-size: 10px;\n    line-height: 1.45;\n    color: #22201E;\n    overflow-y: auto;\n    white-space: pre-wrap;\n    word-break: break-all;\n    user-select: text;\n  }\n  .audit-log-box::-webkit-scrollbar {\n    width: 4px;\n  }\n  .audit-log-box::-webkit-scrollbar-thumb {\n    background: var(--ink);\n  }\n\n  /* =========================================================\n     MODAL & TOAST OVERLAYS\n     ========================================================= */\n  .modal-backdrop {\n    position: fixed;\n    inset: 0;\n    zoom: 0.89;\n    background: rgba(26, 24, 23, 0.52);\n    display: none;\n    align-items: center;\n    justify-content: center;\n    z-index: 100;\n  }\n  .modal-backdrop.active {\n    display: flex;\n  }\n  .voucher-modal {\n    width: 360px;\n    background: var(--bg-ivory);\n    border: 2px solid var(--ink);\n    box-shadow: 5px 5px 0px var(--ink);\n  }\n  .vm-hdr {\n    background: var(--crimson);\n    color: var(--bg-ivory);\n    padding: 6px 12px;\n    font-size: 11px;\n    font-weight: 700;\n    display: flex;\n    justify-content: space-between;\n    border-bottom: 1.5px solid var(--ink);\n  }\n  .vm-body {\n    padding: 14px 16px;\n  }\n  .vm-title {\n    font-size: 14px;\n    font-weight: 900;\n    margin-bottom: 6px;\n    padding-bottom: 5px;\n    border-bottom: 1px solid var(--ink);\n  }\n  .vm-msg {\n    font-size: 12px;\n    line-height: 1.55;\n    white-space: pre-line;\n    color: var(--ink);\n    margin-bottom: 14px;\n  }\n  .vm-actions {\n    display: flex;\n    gap: 8px;\n    justify-content: flex-end;\n  }\n\n  /* Toast Stamp */\n  .toast-container {\n    position: fixed;\n    right: 12px;\n    bottom: 12px;\n    zoom: 0.89;\n    z-index: 200;\n    display: flex;\n    flex-direction: column;\n    gap: 6px;\n    pointer-events: none;\n  }\n  .toast-card {\n    background: var(--bg-card);\n    border: 2px solid var(--ink);\n    box-shadow: 3px 3px 0px var(--ink);\n    min-width: 210px;\n    max-width: 310px;\n    pointer-events: auto;\n    animation: slideIn 0.14s ease-out;\n  }\n  @keyframes slideIn {\n    from { transform: translateY(10px); opacity: 0; }\n    to { transform: translateY(0); opacity: 1; }\n  }\n  .toast-bar {\n    height: 4px;\n    background: var(--crimson);\n    border-bottom: 1px solid var(--ink);\n  }\n  .toast-content {\n    padding: 8px 10px;\n  }\n  .toast-title {\n    font-size: 11px;\n    font-weight: 800;\n    margin-bottom: 2px;\n  }\n  .toast-msg {\n    font-size: 11px;\n    line-height: 1.4;\n    white-space: pre-line;\n  }\n</style>\n</head>\n<body>\n\n<div class="banknote-shell">\n  <!-- 0. CUSTOM INTEGRATED SWISS TITLEBAR (FRAMELESS DRAGGABLE) -->\n  <div class="custom-titlebar pywebview-drag-region">\n    <div class="ct-brand">\n      <span class="ct-cross">┼</span>\n      <span>JIANYING AI PRO</span>\n    </div>\n    <div class="ct-controls">\n      <button class="ct-btn" title="最小化" onclick="pyCall(\'minimize_win\')">─</button>\n      <button class="ct-btn close" title="关闭" onclick="pyCall(\'close_win\')">✕</button>\n    </div>\n  </div>\n\n  <!-- 1. TOP & CENTER MAIN WORKSPACE -->\n  <div class="bn-center">\n    <div>\n      <div class="sn-bar">\n        <span class="sn-code">v2.0</span>\n        <span id="coord-badge" class="coord-stamp">● 未录制坐标</span>\n      </div>\n      <div class="title-bar">\n        <span class="main-title">JIANYING AI PRO</span>\n      </div>\n\n      <div class="bn-tabs">\n        <button class="bn-tab active" onclick="switchTab(0)">01 · 批量导出</button>\n        <button class="bn-tab" onclick="switchTab(1)">02 · 字幕工程</button>\n        <button class="bn-tab" onclick="switchTab(2)">03 · 双擎改名</button>\n        <button class="bn-tab" onclick="switchTab(3)">04 · 凭证参数</button>\n      </div>\n    </div>\n\n    <!-- ==================== TAB 0: 批量导出 ==================== -->\n    <div id="panel-0" class="module-panel active">\n      <div class="ledger-box grow">\n        <div class="ledger-hdr">\n          <span>首页草稿队列</span>\n          <span id="queue-count" style="font-family:var(--font-mono);font-size:10px;color:var(--muted);">0 项</span>\n        </div>\n        <div class="table-scroll">\n          <table class="voucher-table">\n            <thead>\n              <tr>\n                <th style="width:42px;">序号</th>\n                <th>草稿名称</th>\n                <th style="width:80px;text-align:right;">状态</th>\n              </tr>\n            </thead>\n            <tbody id="queue-tbody">\n              <tr><td colspan="3" class="empty-hint">尚未扫描草稿 · 请点击下方【AI 扫描草稿】解析剪映首页</td></tr>\n            </tbody>\n          </table>\n        </div>\n      </div>\n\n      <div class="action-row">\n        <button class="btn-voucher" onclick="pyCall(\'start_wizard\')">引导录制 [F8]</button>\n        <button class="btn-voucher" onclick="pyCall(\'scan_drafts\')">AI 扫描草稿</button>\n        <button id="btn-start-export" class="btn-voucher crimson flex-1" onclick="triggerExport()">▶ 此处执行</button>\n        <button class="btn-voucher ink" onclick="pyCall(\'stop_export\')">■ 中断</button>\n      </div>\n    </div>\n\n    <!-- ==================== TAB 1: 字幕工程 ==================== -->\n    <div id="panel-1" class="module-panel">\n      <div style="display:flex;flex-direction:column;gap:8px;flex:1;">\n        <div class="ledger-box">\n          <div class="ledger-hdr">\n            <span>剪映草稿根目录</span>\n          </div>\n          <div class="field-row">\n            <input id="inp-draft-dir" class="voucher-input" type="text" value="D:\\JianyingPro Drafts" placeholder="选择剪映草稿目录..." />\n            <button class="btn-voucher ink" onclick="pyCall(\'browse_draft_dir\')">浏览目录</button>\n          </div>\n        </div>\n\n        <div class="ledger-box" style="flex:1;justify-content:center;">\n          <div class="ledger-hdr">\n            <span>字幕提取与无损隐藏</span>\n          </div>\n          <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px;">\n            <button class="btn-voucher crimson" style="padding:10px;" onclick="pyCall(\'trigger_extract_subs\')">\n              ┼ 提取 SRT 字幕 & 无损隐藏轨道 → 桌面/srt\n            </button>\n            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">\n              <button class="btn-voucher ink" style="padding:9px;" onclick="pyCall(\'trigger_fix_fonts\')">\n                ■ 统一字体 (Noto Sans Bold)\n              </button>\n              <button class="btn-voucher" style="padding:9px;" onclick="pyCall(\'trigger_unhide_tracks\')">\n                □ 解除所有字幕隐藏\n              </button>\n            </div>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- ==================== TAB 2: 双擎改名 ==================== -->\n    <div id="panel-2" class="module-panel">\n      <div class="ledger-box" style="margin-bottom:8px;">\n        <div style="display:flex;justify-content:flex-end;gap:6px;margin-bottom:6px;">\n          <button id="mode-regex-btn" class="btn-voucher ink" style="padding:3px 12px;font-size:11px;" onclick="setRenameMode(\'正则匹配重命名\')">正则匹配</button>\n          <button id="mode-ai-btn" class="btn-voucher" style="padding:3px 12px;font-size:11px;" onclick="setRenameMode(\'AI 智能重命名\')">AI语义</button>\n        </div>\n\n        <!-- 正则匹配控制 -->\n        <div id="rename-regex-box">\n          <div class="field-row" style="margin-bottom:6px;">\n            <span style="font-size:11px;font-weight:700;width:38px;flex-shrink:0;">目录:</span>\n            <input id="inp-ren-folder" class="voucher-input" type="text" placeholder="选择包含视频或字幕的文件夹..." />\n            <button class="btn-voucher" onclick="pyCall(\'browse_rename_folder\')">浏览</button>\n          </div>\n          <div class="field-row">\n            <span style="font-size:11px;font-weight:700;width:38px;flex-shrink:0;">模板:</span>\n            <input id="inp-ren-fmt" class="voucher-input" type="text" value="短剧名称_第1集" placeholder="例：短剧名称_第1集" />\n            <button class="btn-voucher crimson" onclick="previewRegexRename()">规则预览</button>\n          </div>\n        </div>\n\n        <!-- AI语义控制 -->\n        <div id="rename-ai-box" style="display:none;">\n          <div class="field-row" style="margin-bottom:6px;">\n            <span style="font-size:11px;font-weight:700;width:38px;flex-shrink:0;">来源:</span>\n            <button class="btn-voucher" onclick="pyCall(\'select_ai_files\')">选择文件</button>\n            <button class="btn-voucher" onclick="pyCall(\'load_folder_for_ai\')">载入文件夹</button>\n            <span id="ai-file-count" style="font-family:var(--font-mono);font-size:10px;font-weight:700;color:var(--crimson);margin-left:4px;white-space:nowrap;">未选择</span>\n          </div>\n          <div class="field-row">\n            <span style="font-size:11px;font-weight:700;width:38px;flex-shrink:0;">格式:</span>\n            <input id="inp-ai-fmt" class="voucher-input" type="text" value="短剧名称_第X集" placeholder="例：短剧名称_第X集" />\n            <button id="btn-ai-preview" class="btn-voucher crimson" onclick="previewAiRename()">AI 预览</button>\n          </div>\n        </div>\n      </div>\n\n      <div class="ledger-box grow">\n        <div class="ledger-hdr">\n          <span>重命名对照表</span>\n          <span id="ren-plan-count" style="font-family:var(--font-mono);font-size:10px;color:var(--muted);">0 个文件</span>\n        </div>\n        <div class="table-scroll">\n          <table class="voucher-table">\n            <thead>\n              <tr>\n                <th>原文件名</th>\n                <th>新文件名</th>\n                <th style="width:72px;text-align:center;">方式</th>\n              </tr>\n            </thead>\n            <tbody id="ren-tbody">\n              <tr><td colspan="3" class="empty-hint">请先配置上方目录与模板，点击【预览】生成重命名列表</td></tr>\n            </tbody>\n          </table>\n        </div>\n      </div>\n\n      <div class="action-row">\n        <button id="btn-exec-rename" class="btn-voucher crimson flex-1" disabled onclick="pyCall(\'trigger_exec_rename\')">\n          ▶ 执行批量重命名\n        </button>\n      </div>\n    </div>\n\n    <!-- ==================== TAB 3: 凭证参数 ==================== -->\n    <div id="panel-3" class="module-panel">\n      <div class="ledger-box grow" style="justify-content:space-between;">\n        <div>\n          <div class="ledger-hdr">\n            <span>密钥与模型配置</span>\n          </div>\n\n          <div style="margin-top:4px;">\n            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">\n              <div>\n                <div style="font-size:12px;font-weight:800;">Gemini API Key</div>\n                <div style="font-size:10px;color:var(--amber);margin-top:2px;">默认为公用 Key，建议填入个人独立 Key 保障稳定</div>\n              </div>\n              <button class="btn-voucher ink" onclick="pyCall(\'open_api_url\')">获取 API Key ↗</button>\n            </div>\n            <div class="field-row">\n              <input id="inp-api-key" class="voucher-input" type="password" placeholder="AIzaSy..." />\n            </div>\n          </div>\n\n          <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border-line);">\n            <div style="display:flex;justify-content:space-between;align-items:center;">\n              <div>\n                <div style="font-size:12px;font-weight:800;">推理模型选择</div>\n                <div style="font-size:10px;color:var(--muted);margin-top:2px;">用于首页草稿封面识别与 AI 批量改名</div>\n              </div>\n              <select id="sel-model" class="voucher-select" style="max-width:190px;">\n                <option value="gemini-3.5-flash">gemini-3.5-flash</option>\n                <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite</option>\n                <option value="gemini-2.5-flash">gemini-2.5-flash</option>\n                <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite</option>\n                <option value="gemini-3-flash">gemini-3-flash</option>\n                <option value="gemini-3.7-flash">gemini-3.7-flash</option>\n                <option value="gemini-3.8-flash">gemini-3.8-flash</option>\n              </select>\n            </div>\n          </div>\n        </div>\n\n        <div style="display:flex;justify-content:space-between;align-items:center;border-top:1.5px solid var(--ink);padding-top:8px;">\n          <span style="font-size:10px;color:var(--muted);">作者: 塔奇 · 修改: 小马</span>\n          <button class="btn-voucher crimson" onclick="saveSettings()">┼ 保存设置</button>\n        </div>\n      </div>\n    </div>\n  </div>\n\n  <!-- 2. BOTTOM TELEMETRY & STATUS SECTION (置于主体之下) -->\n  <div class="bn-bottom">\n    <div class="bottom-meta-row">\n      <div class="wm-status">\n        <span class="wm-dot"></span>\n        <span>状态：</span>\n        <span id="status-text">就绪</span>\n      </div>\n      <div class="spec-inline">\n        <span>分辨率: <strong id="spec-res">2560×1440</strong></span>\n        <span>模型: <strong id="spec-model">GEMINI-3.5</strong></span>\n      </div>\n    </div>\n    <div id="audit-log" class="audit-log-box"></div>\n  </div>\n</div>\n\n<!-- CONFIRM MODAL -->\n<div id="modal-backdrop" class="modal-backdrop">\n  <div class="voucher-modal">\n    <div class="vm-hdr">\n      <span>┼ 操作确认</span>\n      <span>v2.0</span>\n    </div>\n    <div class="vm-body">\n      <div id="modal-title" class="vm-title">确认操作</div>\n      <div id="modal-msg" class="vm-msg">内容</div>\n      <div class="vm-actions">\n        <button id="modal-cancel-btn" class="btn-voucher" onclick="resolveModal(false)">取消</button>\n        <button id="modal-ok-btn" class="btn-voucher crimson" onclick="resolveModal(true)">确认</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n<!-- TOAST CONTAINER -->\n<div id="toast-container" class="toast-container"></div>\n\n<script>\n  let selectedDraftIdx = 0;\n  let currentRenameMode = \'正则匹配重命名\';\n\n  function switchTab(idx) {\n    document.querySelectorAll(\'.bn-tab\').forEach((btn, i) => btn.classList.toggle(\'active\', i === idx));\n    document.querySelectorAll(\'.module-panel\').forEach((p, i) => p.classList.toggle(\'active\', i === idx));\n  }\n\n  function pyCall(method, ...args) {\n    if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {\n      return window.pywebview.api[method](...args);\n    }\n  }\n\n  /* Called from Python on startup */\n  window.initAppState = function(state) {\n    if (state.res) document.getElementById(\'spec-res\').textContent = state.res;\n    if (state.api_key !== undefined) document.getElementById(\'inp-api-key\').value = state.api_key;\n    if (state.api_model) {\n      document.getElementById(\'sel-model\').value = state.api_model;\n      document.getElementById(\'spec-model\').textContent = state.api_model.toUpperCase();\n    }\n    if (state.draft_dir) document.getElementById(\'inp-draft-dir\').value = state.draft_dir;\n    if (state.coords_ready) {\n      updateGuideBadge(\'● 坐标已就绪\', \'ready\');\n    }\n  };\n\n  window.updateGuideBadge = function(text, mode) {\n    const el = document.getElementById(\'coord-badge\');\n    el.textContent = text;\n    el.className = \'coord-stamp\' + (mode ? \' \' + mode : \'\');\n  };\n\n  window.appendLog = function(text) {\n    const box = document.getElementById(\'audit-log\');\n    box.textContent += text;\n    box.scrollTop = box.scrollHeight;\n  };\n\n  window.setStatus = function(text) {\n    document.getElementById(\'status-text\').textContent = text;\n  };\n\n  window.setDraftDirInput = function(path) {\n    document.getElementById(\'inp-draft-dir\').value = path;\n  };\n\n  window.setRenameFolderInput = function(path) {\n    document.getElementById(\'inp-ren-folder\').value = path;\n  };\n\n  window.setAiFileCount = function(text) {\n    document.getElementById(\'ai-file-count\').textContent = text;\n  };\n\n  window.setExportBtnState = function(enabled) {\n    document.getElementById(\'btn-start-export\').disabled = !enabled;\n  };\n\n  window.renderQueue = function(items) {\n    const tbody = document.getElementById(\'queue-tbody\');\n    document.getElementById(\'queue-count\').textContent = items.length + \' 项\';\n    if (!items.length) {\n      tbody.innerHTML = \'<tr><td colspan="3" class="empty-hint">尚未扫描草稿 · 请点击下方【AI 扫描草稿】解析剪映首页</td></tr>\';\n      return;\n    }\n    selectedDraftIdx = 0;\n    tbody.innerHTML = \'\';\n    items.forEach((it, idx) => {\n      const tr = document.createElement(\'tr\');\n      if (idx === selectedDraftIdx) tr.classList.add(\'selected\');\n      tr.id = \'q-row-\' + idx;\n      tr.onclick = () => selectQueueRow(idx);\n      tr.innerHTML = `\n        <td style="font-family:var(--font-mono);">${String(idx + 1).padStart(2, \'0\')}</td>\n        <td>${escapeHtml(it.name)}</td>\n        <td id="q-st-${idx}" style="font-family:var(--font-mono);text-align:right;">${escapeHtml(it.st)}</td>\n      `;\n      tbody.appendChild(tr);\n    });\n  };\n\n  window.updateQueueItemStatus = function(idx, st) {\n    const cell = document.getElementById(\'q-st-\' + idx);\n    if (cell) cell.textContent = st;\n  };\n\n  function selectQueueRow(idx) {\n    selectedDraftIdx = idx;\n    document.querySelectorAll(\'#queue-tbody tr\').forEach((tr, i) => {\n      tr.classList.toggle(\'selected\', i === idx);\n    });\n  }\n\n  function triggerExport() {\n    pyCall(\'request_start_export\', selectedDraftIdx);\n  }\n\n  /* Subtitle inputs */\n  window.getDraftDirValue = function() {\n    return document.getElementById(\'inp-draft-dir\').value.trim();\n  };\n\n  /* Rename Engine */\n  window.setRenameMode = function(mode) {\n    currentRenameMode = mode;\n    const isRegex = (mode === \'正则匹配重命名\');\n    document.getElementById(\'rename-regex-box\').style.display = isRegex ? \'block\' : \'none\';\n    document.getElementById(\'rename-ai-box\').style.display = isRegex ? \'none\' : \'block\';\n    document.getElementById(\'mode-regex-btn\').className = isRegex ? \'btn-voucher ink\' : \'btn-voucher\';\n    document.getElementById(\'mode-ai-btn\').className = !isRegex ? \'btn-voucher ink\' : \'btn-voucher\';\n    pyCall(\'on_rename_mode_change\', mode);\n  };\n\n  function previewRegexRename() {\n    const folder = document.getElementById(\'inp-ren-folder\').value.trim();\n    const tpl = document.getElementById(\'inp-ren-fmt\').value.trim();\n    pyCall(\'regex_preview\', folder, tpl);\n  }\n\n  function previewAiRename() {\n    const folder = document.getElementById(\'inp-ren-folder\').value.trim();\n    const fmt = document.getElementById(\'inp-ai-fmt\').value.trim();\n    pyCall(\'ai_preview\', folder, fmt);\n  }\n\n  window.setAiPreviewBusy = function(busy) {\n    const btn = document.getElementById(\'btn-ai-preview\');\n    btn.disabled = busy;\n    btn.textContent = busy ? \'⏳ 思考中...\' : \'AI 预览\';\n  };\n\n  window.renderRenamePlan = function(plan) {\n    const tbody = document.getElementById(\'ren-tbody\');\n    document.getElementById(\'ren-plan-count\').textContent = plan.length + \' 个文件\';\n    document.getElementById(\'btn-exec-rename\').disabled = (plan.length === 0);\n    if (!plan.length) {\n      tbody.innerHTML = \'<tr><td colspan="3" class="empty-hint">没有可预览的重命名计划</td></tr>\';\n      return;\n    }\n    tbody.innerHTML = \'\';\n    plan.forEach(item => {\n      const tr = document.createElement(\'tr\');\n      tr.innerHTML = `\n        <td>${escapeHtml(item.old_name)}</td>\n        <td style="color:var(--crimson);font-weight:700;">${escapeHtml(item.new_name)}</td>\n        <td style="font-family:var(--font-mono);font-size:10px;text-align:center;">${escapeHtml(item.mode)}</td>\n      `;\n      tbody.appendChild(tr);\n    });\n  };\n\n  function saveSettings() {\n    const key = document.getElementById(\'inp-api-key\').value.trim();\n    const model = document.getElementById(\'sel-model\').value;\n    document.getElementById(\'spec-model\').textContent = model.toUpperCase();\n    pyCall(\'save_settings\', key, model);\n  }\n\n  /* Modal & Toast */\n  window.showVoucherModal = function(title, message, showCancel) {\n    document.getElementById(\'modal-title\').textContent = title;\n    document.getElementById(\'modal-msg\').textContent = message;\n    document.getElementById(\'modal-cancel-btn\').style.display = showCancel ? \'inline-flex\' : \'none\';\n    document.getElementById(\'modal-ok-btn\').textContent = showCancel ? \'确认\' : \'我已知晓\';\n    document.getElementById(\'modal-backdrop\').classList.add(\'active\');\n  };\n\n  function resolveModal(confirmed) {\n    document.getElementById(\'modal-backdrop\').classList.remove(\'active\');\n    pyCall(\'on_modal_result\', confirmed);\n  }\n\n  window.showToast = function(title, message, color, duration) {\n    const container = document.getElementById(\'toast-container\');\n    const card = document.createElement(\'div\');\n    card.className = \'toast-card\';\n    card.innerHTML = `\n      <div class="toast-bar" style="background:${color || \'#9E1B16\'};"></div>\n      <div class="toast-content">\n        <div class="toast-title" style="color:${color || \'#9E1B16\'};">┼ ${escapeHtml(title)}</div>\n        <div class="toast-msg">${escapeHtml(message)}</div>\n      </div>\n    `;\n    container.appendChild(card);\n    setTimeout(() => {\n      card.remove();\n    }, duration || 4000);\n  };\n\n  function escapeHtml(str) {\n    return String(str || \'\')\n      .replace(/&/g, \'&amp;\')\n      .replace(/</g, \'&lt;\')\n      .replace(/>/g, \'&gt;\')\n      .replace(/"/g, \'&quot;\');\n  }\n\n  window.addEventListener(\'pywebviewready\', function() {\n    pyCall(\'ui_ready\');\n  });\n</script>\n</body>\n</html>'

# ─── 主应用 (PyWebView / Windows Edge WebView2 瑞士防伪账本架构) ────────
class _TkProxyWidget:
    """兼容原有代码中对 self.status / self.start_btn / self.guide_lbl 等控件的调用"""
    def __init__(self, app, role):
        self._app = app
        self._role = role
        self._value = ""

    def get(self):
        if self._role == "draft_dir":
            return self._app.draft_dir_val
        if self._role == "api_key":
            return self._app.api_key
        if self._role == "api_model":
            return self._app.api_model
        if self._role == "ren_mode":
            return self._app.ren_mode_val
        if self._role == "rename_folder":
            return self._app.rename_folder_val
        if self._role == "fmt":
            return self._app.fmt_val
        if self._role == "ai_fmt":
            return self._app.ai_fmt_val
        return self._value

    def set(self, val):
        self._value = val

    def configure(self, **kwargs):
        if self._role == "status" and "text" in kwargs:
            self._app._js("setStatus", kwargs["text"])
        elif self._role == "start_btn" and "state" in kwargs:
            self._app._js("setExportBtnState", kwargs["state"] == "normal")
        elif self._role == "guide_lbl" and "text" in kwargs:
            txt = kwargs["text"]
            mode = "ready" if "就绪" in txt or "VERIFIED" in txt else ("recording" if "/" in txt else "")
            self._app._js("updateGuideBadge", txt, mode)
        elif self._role == "file_lbl" and "text" in kwargs:
            self._app._js("setAiFileCount", kwargs["text"])
        elif self._role == "ai_preview_btn":
            if "state" in kwargs:
                self._app._js("setAiPreviewBusy", kwargs["state"] != "normal")


class _JsBridgeApi:
    """专供 pywebview 暴露给前端 JS 的轻量桥接类（不包含 window 引用以避免递归内省）"""
    def __init__(self, app):
        self._app = app

    def ui_ready(self):
        return self._app.ui_ready()

    def on_modal_result(self, confirmed):
        return self._app.on_modal_result(confirmed)

    def start_wizard(self):
        return self._app.start_wizard()

    def scan_drafts(self):
        return self._app.scan_drafts()

    def request_start_export(self, idx):
        return self._app.request_start_export(idx)

    def stop_export(self):
        return self._app.stop_export()

    def browse_draft_dir(self):
        return self._app.browse_draft_dir()

    def trigger_extract_subs(self):
        return self._app.trigger_extract_subs()

    def trigger_fix_fonts(self):
        return self._app.trigger_fix_fonts()

    def trigger_unhide_tracks(self):
        return self._app.trigger_unhide_tracks()

    def on_rename_mode_change(self, mode):
        return self._app.on_rename_mode_change(mode)

    def browse_rename_folder(self):
        return self._app.browse_rename_folder()

    def select_ai_files(self):
        return self._app.select_ai_files()

    def load_folder_for_ai(self):
        return self._app.load_folder_for_ai()

    def regex_preview(self, folder, template):
        return self._app.regex_preview(folder, template)

    def ai_preview(self, folder, fmt):
        return self._app.ai_preview(folder, fmt)

    def trigger_exec_rename(self):
        return self._app.trigger_exec_rename()

    def open_api_url(self):
        return self._app.open_api_url()

    def minimize_win(self):
        if self.window:
            self.window.minimize()

    def close_win(self):
        if self.window:
            self.window.destroy()
        os._exit(0)

    def save_settings(self, key, model):
        return self._app.save_settings(key, model)

    def minimize_win(self):
        return self._app.minimize_win()

    def close_win(self):
        return self._app.close_win()


class App:
    def __init__(self):
        # 瑞士法郎 / 瑞士护照配色常量
        global C_PRIMARY, C_SUCCESS, C_DANGER, C_WARN
        C_PRIMARY = "#9E1B16"
        C_SUCCESS = "#0B6E4F"
        C_DANGER  = "#B91C1C"
        C_WARN    = "#B45309"

        self.window = None
        self._ui_ready = False
        self._modal_cb = None

        # 默认推荐坐标 (基于 2560x1440)
        self.default_res = (2560, 1440)
        self.default_coords = {
            "export": [2420, 19],
            "confirm": [1468, 1028],
            "popup_close": [1548, 951],
            "close_btn": None,
            "draft_close": [2538, 18],
        }
        self.default_color = (99, 149, 200)

        self.sys_res = pyautogui.size()
        if self.sys_res == self.default_res:
            self.coords = self.default_coords.copy()
            self.close_color = self.default_color
        else:
            self.coords = {
                "export": None,
                "confirm": None,
                "popup_close": None,
                "close_btn": None,
                "draft_close": None,
            }
            self.close_color = None

        self.stop_flag = False
        self.rec_state = None
        self.api_key = DEFAULT_KEY
        self.api_model = "gemini-3.5-flash"
        self.drafts = []
        self.sel_files = []
        self.rename_folder = ""
        self.rename_plan = []
        self.first_run = True
        self.first_sub = False

        # 状态值与兼容代理对象
        self.draft_dir_val = r"D:\JianyingPro Drafts"
        self.ren_mode_val = "正则匹配重命名"
        self.rename_folder_val = ""
        self.fmt_val = "短剧名称_第1集"
        self.ai_fmt_val = "短剧名称_第X集"

        self.status = _TkProxyWidget(self, "status")
        self.start_btn = _TkProxyWidget(self, "start_btn")
        self.guide_lbl = _TkProxyWidget(self, "guide_lbl")
        self.file_lbl = _TkProxyWidget(self, "file_lbl")
        self.ai_preview_btn = _TkProxyWidget(self, "ai_preview_btn")
        self.draft_dir = _TkProxyWidget(self, "draft_dir")
        self.key_entry = _TkProxyWidget(self, "api_key")
        self.model_combo = _TkProxyWidget(self, "api_model")
        self.ren_mode_seg = _TkProxyWidget(self, "ren_mode")
        self.rename_folder_entry = _TkProxyWidget(self, "rename_folder")
        self.fmt_entry = _TkProxyWidget(self, "fmt")
        self.ai_fmt_entry = _TkProxyWidget(self, "ai_fmt")

        self._load_cfg()

        # 热键监听
        self.kb = keyboard.Listener(on_release=self._on_key)
        self.kb.start()

    # ─── WebView2 桥接辅助 ───
    def _js(self, func_name, *args):
        if not self.window or not self._ui_ready:
            return
        try:
            args_json = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
            self.window.evaluate_js(f"window.{func_name} && window.{func_name}({args_json});")
        except Exception:
            pass

    def after(self, ms, func, *args):
        if ms <= 0:
            threading.Thread(target=func, args=args, daemon=True).start()
        else:
            t = threading.Timer(ms / 1000.0, func, args=args)
            t.daemon = True
            t.start()

    def _log(self, msg):
        print(msg)

    class _LogWriter:
        def __init__(self, app):
            self.app = app
        def write(self, s):
            if s:
                self.app._js("appendLog", s)
        def flush(self):
            pass

    # ─── JS API 暴露方法 (供前端 WebView2 调用) ───
    def ui_ready(self):
        self._ui_ready = True
        self._snap_to_bottom_right()
        sys.stdout = self._LogWriter(self)
        self._js("initAppState", {
            "res": f"{self.sys_res[0]}×{self.sys_res[1]}",
            "api_key": self.api_key,
            "api_model": self.api_model,
            "draft_dir": self.draft_dir_val,
            "coords_ready": bool(self.coords.get("export")),
        })
        self.after(400, self._show_startup_guide)
        self.after(800, self._check_auth)

    def on_modal_result(self, confirmed):
        cb = self._modal_cb
        self._modal_cb = None
        if confirmed and cb:
            threading.Thread(target=cb, daemon=True).start()

    def start_wizard(self):
        self._start_wizard()

    def scan_drafts(self):
        self._scan_thread()

    def request_start_export(self, idx):
        self._start_export(idx)

    def stop_export(self):
        self._stop()

    def browse_draft_dir(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG, directory=self.draft_dir_val if os.path.isdir(self.draft_dir_val) else "")
        if res and len(res) > 0:
            self.draft_dir_val = res[0]
            self._js("setDraftDirInput", self.draft_dir_val)

    def trigger_extract_subs(self):
        try:
            val = self.window.evaluate_js("window.getDraftDirValue()")
            if val:
                self.draft_dir_val = val
        except Exception:
            pass
        self._sub_thread()

    def trigger_fix_fonts(self):
        try:
            val = self.window.evaluate_js("window.getDraftDirValue()")
            if val:
                self.draft_dir_val = val
        except Exception:
            pass
        self._fix_font_thread()

    def trigger_unhide_tracks(self):
        try:
            val = self.window.evaluate_js("window.getDraftDirValue()")
            if val:
                self.draft_dir_val = val
        except Exception:
            pass
        self._unhide_tracks_thread()

    def on_rename_mode_change(self, mode):
        self.ren_mode_val = mode
        self.rename_plan = []
        self._js("renderRenamePlan", [])

    def browse_rename_folder(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if res and len(res) > 0:
            self.rename_folder_val = res[0]
            self._js("setRenameFolderInput", self.rename_folder_val)
            self.rename_plan = []
            self._js("renderRenamePlan", [])

    def select_ai_files(self):
        file_types = ("视频/字幕文件 (*.mp4;*.mov;*.avi;*.mkv;*.srt;*.ass;*.wav;*.mp3)", "All files (*.*)")
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True, file_types=file_types)
        if res:
            self.sel_files = list(res)
            self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
            self.rename_plan = []
            self._js("renderRenamePlan", [])

    def load_folder_for_ai(self, folder=None):
        if not folder or not os.path.isdir(folder):
            res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if res and len(res) > 0:
                folder = res[0]
        if folder and os.path.isdir(folder):
            self.rename_folder_val = folder
            self._js("setRenameFolderInput", folder)
            valid_exts = ('.mp4', '.mov', '.avi', '.mkv', '.srt', '.ass', '.wav', '.mp3')
            files = [
                os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(valid_exts)
            ]
            if files:
                self.sel_files = files
                self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
                self.rename_plan = []
                self._js("renderRenamePlan", [])
                self._log(f"已从文件夹载入 {len(files)} 个文件用于 AI 重命名")
            else:
                self._show_toast("未找到有效文件", "该文件夹下没有常见的视频或字幕文件", C_WARN)

    def regex_preview(self, folder, template):
        self.rename_folder_val = (folder or "").strip().strip('"')
        self.fmt_val = (template or "").strip()
        self._regex_preview_thread()

    def ai_preview(self, folder, fmt):
        if folder:
            self.rename_folder_val = folder.strip().strip('"')
        if fmt:
            self.ai_fmt_val = fmt.strip()
        self._ai_preview_thread()

    def trigger_exec_rename(self):
        if not self.rename_plan:
            return
        current_mode = self.ren_mode_val
        self._ask_yes_no(
            f"确认【{current_mode}】",
            f"将使用【{current_mode}】修改 {len(self.rename_plan)} 个文件，是否继续？",
            self._do_exec_rename
        )

    def open_api_url(self):
        import webbrowser
        webbrowser.open("https://aistudio.google.com/api-keys")

    def minimize_win(self):
        if self.window:
            self.window.minimize()

    def close_win(self):
        if self.window:
            self.window.destroy()
        os._exit(0)

    def save_settings(self, key, model):
        self.api_key = (key or "").strip() or DEFAULT_KEY
        self.api_model = model or "gemini-3.5-flash"
        self._save_cfg()
        self._show_toast("凭证已保存", "配置已写入本地 config.json", C_SUCCESS)

    # ─── 授权与自毁逻辑 ───
    def _check_auth(self):
        if "placeholder" in AUTH_URL: return
        try:
            api_url = "https://api.github.com/repos/Kirota233/JianYing-AI-Pro/contents/auth.json?ref=master"
            headers = {"Cache-Control": "no-cache"}
            try:
                r = requests.get(api_url, headers=headers, timeout=5).json()
                if "content" in r:
                    import base64
                    d = json.loads(base64.b64decode(r["content"]).decode("utf-8"))
                else:
                    raise Exception("Fallback")
            except:
                d = requests.get(f"{AUTH_URL}?t={time.time()}", headers=headers, timeout=5).json()
                
            if d.get("status") == "destroy":  self._self_destruct()
            if d.get("status") == "blocked":
                self._show_toast("授权失效", "该软件未获授权或已过期", C_DANGER, 5000)
                self.after(5000, os._exit, 0)
            rv, uu = d.get("version", VERSION), d.get("update_url", "")
            if rv != VERSION and uu:
                self._ask_yes_no("发现新版本", f"v{rv} 可用，是否立即更新？", lambda: self._update(uu))
        except Exception: pass

    def _update(self, url):
        import urllib.request
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        if not exe.lower().endswith('.exe'): return
        self._show_toast("更新中", "正在后台下载，完成后将自动重启", C_PRIMARY, 8000)
        new = exe + ".new"
        try:
            urllib.request.urlretrieve(url, new)
            bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "upd.bat")
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
set "NEW={new}"
set /a count=0
:loop
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto replace
set /a count+=1
if %count% geq 60 goto replace
ping 127.0.0.1 -n 2 >nul
goto loop
:replace
move /y "%NEW%" "%EXE%" >nul 2>&1
start "" "%EXE%"
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
            os._exit(0)
        except Exception as e: 
            self._log(f"更新失败: {e}")

    def _self_destruct(self):
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        cfg = os.path.abspath(CFG_FILE)
        bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "rm.bat")
        try:
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
set "CFG={cfg}"
set /a count=0
:loop
del /f /q "%CFG%" >nul 2>&1
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto done
set /a count+=1
if %count% geq 60 goto done
ping 127.0.0.1 -n 2 >nul
goto loop
:done
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
            ps_cmd = f'powershell -WindowStyle Hidden -Command "$p=\\"{exe}\\"; for($i=0;$i -lt 30;$i++){{ if(Test-Path $p){{ try{{ [IO.File]::Delete($p); break }}catch{{ Start-Sleep -Milliseconds 500 }} }}else{{ break }} }}"'
            subprocess.Popen(ps_cmd, shell=True)
        except Exception: pass
        os._exit(0)

    def _show_toast(self, title, message, color=C_PRIMARY, duration=4000):
        self._js("showToast", title, message, color, duration)

    def _ask_yes_no(self, title, message, on_yes, show_cancel=True):
        self._modal_cb = on_yes
        self._js("showVoucherModal", title, message, show_cancel)

    def _client(self):
        k = (self.api_key or "").strip() or DEFAULT_KEY
        return genai.Client(api_key=k)

    def _show_startup_guide(self):
        if self.first_run:
            self.first_run = False
            self._save_cfg()
            
            if self.sys_res == self.default_res:
                self.after(500, lambda: self._show_toast("坐标已自动适配", "检测到您的分辨率与默认配置匹配\n已自动为您填入推荐坐标，无需再次录制！", C_SUCCESS, 6000))
                
            msg = (
                "1. 请不要把本程序窗口遮挡住剪映的草稿封面\n"
                "2. 录制坐标请用废弃草稿进行，避免误操作\n\n"
                "【网络建议】\n"
                "本程序核心 AI 强依赖 Google Gemini。\n"
                "• 强烈建议在设置中填入您自己的 API Key\n"
                "• 使用 AI 功能时必须开启代理环境(科学上网)"
            )
            self._ask_yes_no("首次运行须知", msg, lambda: None, show_cancel=False)

    def _load_cfg(self):
        if os.path.exists(CFG_FILE):
            try:
                with open(CFG_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                loaded_coords = d.get("coords")
                if loaded_coords and loaded_coords.get("export"):
                    if "close_btn" not in loaded_coords:
                        loaded_coords["close_btn"] = None
                    self.coords = loaded_coords
                
                loaded_color = d.get("color")
                if loaded_color:
                    self.close_color = tuple(loaded_color)
                self.api_key = d.get("api_key", DEFAULT_KEY)
                self.first_run = d.get("first_run", True)
                self.first_sub = False
                self.api_model = d.get("api_model", "gemini-3.5-flash")
            except: pass

    def _save_cfg(self):
        try:
            with open(CFG_FILE, "w", encoding='utf-8') as f:
                json.dump({"coords": self.coords, "color": self.close_color,
                        "api_key": (self.api_key or "").strip(),
                        "first_run": self.first_run,
                        "first_sub": False,
                        "api_model": getattr(self, 'api_model', "gemini-3.5-flash")}, f)
        except: pass

    # ═══════════════════════ 坐标向导 ═══════════════════════
    def _start_wizard(self):
        self.rec_state = "export"
        self.guide_lbl.configure(text="[1/5] 鼠标放在【导出】上 → 按 F8")
        self._log("向导开始，请按 F8 录制各按钮位置")
    
    def _capture_btn_template(self, x, y, name):
        """录制时截取按钮区域保存为模板图片"""
        try:
            w, h = 100, 44
            rx, ry = max(0, x - w // 2), max(0, y - h // 2)
            img = pyautogui.screenshot(region=(rx, ry, w, h))
            base = os.path.dirname(os.path.abspath(CFG_FILE)) or "."
            path = os.path.join(base, f"tpl_{name}.png")
            img.save(path)
            self._log(f"  📸 已保存模板 → tpl_{name}.png")
        except Exception as e:
            self._log(f"  ⚠ 截图失败: {e}")

    def _on_key(self, key):
        if self.rec_state:
            if key == keyboard.Key.f8:
                x, y = pyautogui.position()
                self.coords[self.rec_state] = (x, y)
                if self.rec_state == "popup_close":
                    r, g, b = pyautogui.pixel(x, y)
                    self.close_color = (r, g, b)
                    self._capture_btn_template(x, y, "return_home")
                    self._log(f"  ✓ 收起至首页 ({x},{y}) RGB({r},{g},{b})")
                elif self.rec_state == "close_btn":
                    self._capture_btn_template(x, y, "close_popup")
                    self._log(f"  ✓ 关闭弹窗 ({x},{y})")
                else:
                    self._log(f"  ✓ {self.rec_state} ({x},{y})")
                self._next_rec()
            elif key == keyboard.Key.f9 and self.rec_state in ["popup_close", "draft_close"]:
                self.coords[self.rec_state] = None
                self._log(f"  ✓ 跳过 {self.rec_state}")
                self._next_rec()
    
    def _next_rec(self):
        flow = {
            "export":      ("confirm",     "2/5 点导出呼出弹窗 → 鼠标在【确认导出】上 → F8"),
            "confirm":     ("popup_close", "3/5 鼠标在【收起至首页(亮起后)】上 → F8 (跳过按F9)"),
            "popup_close": ("close_btn",   "4/5 鼠标在导出完成后的【关闭(X)】按钮上 → F8"),
            "close_btn":   ("draft_close", "5/5 鼠标在【关闭草稿/返回首页】按钮上 → F8 (跳过按F9)"),
            "draft_close": (None,          "● 坐标已就绪"),
        }
        nxt, txt = flow[self.rec_state]
        self.rec_state = nxt
        self.guide_lbl.configure(text=txt)
        
        if nxt is None:
            self._save_cfg()
            self._log("向导完成，坐标已保存")

    # ═══════════════════════ AI 扫描 ═══════════════════════
    def _get_api_err_msg(self, e):
        err = str(e).lower()
        if "403" in err: return "API Key 无效、无权限或未开启代理(403)"
        elif "400" in err: return "请求参数错误或被拒绝(400)"
        elif "429" in err or "quota" in err or "exhausted" in err: return "API 额度已耗尽或请求过于频繁(429)"
        elif "500" in err: return "Gemini 服务器内部错误(500)"
        elif "closed" in err: return "网络连接被强行关闭 (请检查代理是否稳定)"
        elif "timeout" in err: return "网络请求超时 (请检查代理是否可用)"
        return f"发生错误: {str(e)[:50]}"

    def _scan_thread(self):
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        self._log("截屏并发送 AI 识别...")
        self.status.configure(text="AI 扫描中...")
        pyautogui.screenshot("home_screen.png")
        
        try:
            img = Image.open("home_screen.png")
            prompt = (
                "You are analyzing JianYing video editor home screen.\n"
                "Find EVERY video draft thumbnail visible.\n"
                "For each, return its bounding box [ymin,xmin,ymax,xmax] normalized 0-1000 "
                "and its visible title text.\n"
                "Return ONLY raw JSON array: [{\"box\":[y1,x1,y2,x2],\"name\":\"title\"},...]\n"
                "No markdown."
            )
            client = self._client()
            r = client.models.generate_content(
                model=self.api_model, contents=[img, prompt],
                config=types.GenerateContentConfig(temperature=0.1))
            
            t = r.text.strip()
            for pfx in ["```json", "```"]:
                if t.startswith(pfx): t = t[len(pfx):]
            if t.endswith("```"): t = t[:-3]
            data = json.loads(t.strip())
            
            sw, sh = pyautogui.size()
            raw = []
            for it in data:
                b = it.get("box", [])
                nm = it.get("name", "未知")
                if len(b) == 4:
                    y1, x1, y2, x2 = b
                    raw.append((int((x1+x2)/2/1000*sw), int((y1+y2)/2/1000*sh), nm))
            
            if not raw:
                self._log("⚠  未识别到草稿"); self.status.configure(text="未识别"); return
            
            raw.sort(key=lambda c: c[1])
            rows, cur = [], []
            for c in raw:
                if not cur: cur.append(c)
                else:
                    if abs(c[1] - cur[0][1]) < 100: cur.append(c)
                    else:
                        cur.sort(key=lambda i: i[0]); rows.append(cur); cur = [c]
            if cur: cur.sort(key=lambda i: i[0]); rows.append(cur)
            
            self.drafts = []
            for row in rows: self.drafts.extend(row)
            
            self._fill_queue()
            preview = ", ".join(d[2] for d in self.drafts[:3])
            if len(self.drafts) > 3: preview += "..."
            self._log(f"识别到 {len(self.drafts)} 个: {preview}")
            self.status.configure(text=f"{len(self.drafts)} 个草稿就绪")
        except Exception as e:
            msg = self._get_api_err_msg(e)
            self._log(f"✗ {msg}"); self.status.configure(text="扫描失败")
            self._show_toast("AI 扫描失败", m if 'm' in locals() else msg, C_DANGER, 6000)

    def _fill_queue(self):
        items = [{"name": nm, "st": "待处理"} for _, _, nm in self.drafts]
        self._js("renderQueue", items)

    # ═══════════════════════ 导出核心 ═══════════════════════
    def _stop(self):
        self.stop_flag = True
        self._log("🛑 中断"); self.start_btn.configure(state="normal")
        self.status.configure(text="已中断")

    def _wait_done(self):
        """等待导出完成，返回 'return_home' 或 'close' 表示检测到哪个按钮"""
        rh = self.coords.get('popup_close')       # 收起至首页坐标
        cb = self.coords.get('close_btn')         # 关闭弹窗坐标
        
        self._log("  · 等待导出弹窗 (0.5s)...")
        time.sleep(0.5)
        if self.stop_flag: return None
        
        base = os.path.dirname(os.path.abspath(CFG_FILE)) or "."
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        tpl_rh = os.path.join(base, "tpl_return_home.png")
        tpl_rh_bundled = os.path.join(meipass, "btn_bright.png")
        tpl_close = os.path.join(base, "tpl_close_popup.png")
        
        self._log("  · 等待按钮就绪 (支持自动降级识别)...")
        while not self.stop_flag:
            if rh:
                x_rh, y_rh = rh
                for tpl_path in [tpl_rh, tpl_rh_bundled]:
                    if not os.path.exists(tpl_path): continue
                    try:
                        rx = max(0, x_rh - 100); ry = max(0, y_rh - 60)
                        loc = pyautogui.locateOnScreen(tpl_path, region=(rx, ry, 200, 120), confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【收起至首页】(局部模板匹配)")
                            return "return_home"
                    except Exception: pass
                    try:
                        loc = pyautogui.locateOnScreen(tpl_path, confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【收起至首页】(全屏模板匹配)")
                            return "return_home"
                    except Exception: pass
                
                for ox in [-25, -15, -5, 0, 5, 15, 25]:
                    for oy in [-10, -5, 0, 5, 10]:
                        try:
                            r, g, b = pyautogui.pixel(x_rh + ox, y_rh + oy)
                            if r < 80 and g > 130 and b > 130:
                                self._log("  ✓ 检测到【收起至首页】(亮青色背景特征)")
                                return "return_home"
                        except Exception: pass
            
            close_pos = cb or (rh if not self.coords.get('close_btn') else None)
            if os.path.exists(tpl_close):
                try:
                    if close_pos:
                        rx = max(0, close_pos[0] - 100); ry = max(0, close_pos[1] - 60)
                        loc = pyautogui.locateOnScreen(tpl_close, region=(rx, ry, 200, 120), confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【关闭】按钮 (局部模板匹配)")
                            return "close"
                except Exception: pass
                try:
                    loc = pyautogui.locateOnScreen(tpl_close, confidence=0.82)
                    if loc is not None:
                        self._log("  ✓ 检测到【关闭】按钮 (全屏模板匹配)")
                        return "close"
                except Exception: pass
            
            if close_pos:
                cx, cy = close_pos
                samples = []
                for ox in [-20, -10, 0, 10, 20]:
                    for oy in [-8, -4, 0, 4, 8]:
                        try:
                            samples.append(pyautogui.pixel(cx + ox, cy + oy))
                        except Exception: pass
                if samples:
                    max_b = max(max(r, g, b) for r, g, b in samples)
                    min_b = min(min(r, g, b) for r, g, b in samples)
                    contrast = max_b - min_b
                    if max_b > 160 and contrast > 60:
                        self._log("  ✓ 检测到【关闭】按钮 (结构对比度)")
                        return "close"
                    
            time.sleep(1)
        return None

    def _start_export(self, idx=0):
        req = [self.coords.get("export"), self.coords.get("confirm")]
        if None in req or (not self.coords.get("popup_close") and not self.coords.get("close_btn")):
            self._show_toast("未就绪", "请先完成坐标录制 (至少包含导出、确认与关闭/收起)", C_WARN); return
        if not self.drafts:
            self._show_toast("未就绪", "请先 AI 扫描首页", C_WARN); return
        if idx is None or idx < 0 or idx >= len(self.drafts):
            idx = 0
        
        nm = self.drafts[idx][2]
        n = len(self.drafts) - idx
        
        self._ask_yes_no("确认开始", f"将从【{nm}】(第{idx+1}个) 开始\n共 {n} 个待导出，确认？", lambda: self._start_export_thread(idx))

    def _start_export_thread(self, idx):
        self.stop_flag = False
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._export_loop, args=(idx,), daemon=True).start()

    def _chk(self):
        if self.stop_flag: raise Exception("用户中断")

    def _qst(self, i, st):
        self._js("updateQueueItemStatus", i, st)

    def _export_loop(self, start):
        try:
            total = len(self.drafts); n = total - start
            self._log(f"\n{'─'*32}\n开始导出：共 {n} 个")
            
            for i in range(start, total):
                self._chk()
                dx, dy, nm = self.drafts[i]
                self._qst(i, "▶ 处理中")
                self.status.configure(text=f"导出 {i-start+1}/{n}")
                self._log(f"\n[{i+1}/{total}] {nm}")
                
                pyautogui.click(dx, dy)
                self._log("  · 加载草稿 (8s)")
                for _ in range(8): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['export'])
                self._log("  · 点击导出")
                for _ in range(3): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['confirm'])
                self._log("  · 确认导出")
                time.sleep(1)
                pyautogui.press('enter')
                
                result = self._wait_done()
                self._chk()
                
                if result == "return_home":
                    pyautogui.click(*self.coords['popup_close'])
                    self._log("  · 已收起至首页 (后台导出)")
                    time.sleep(1)
                elif result == "close":
                    cb = self.coords.get('close_btn') or self.coords.get('popup_close')
                    if cb:
                        pyautogui.click(*cb)
                        self._log("  · 点击关闭弹窗")
                    for _ in range(2): self._chk(); time.sleep(1)
                    dc = self.coords.get('draft_close')
                    if dc:
                        self._chk(); pyautogui.click(*dc)
                        self._log("  · 关闭草稿 → 返回首页")
                else:
                    cb = self.coords.get('popup_close') or self.coords.get('close_btn')
                    if cb: pyautogui.click(*cb)
                
                self._qst(i, "✅ 完成")
                self._log("  · 休息 1s")
                for _ in range(2): self._chk(); time.sleep(0.5)
            
            self._log(f"\n🎉 全部完成！共导出 {n} 个")
            self.status.configure(text=f"完成 {n} 个")
        except Exception as e:
            err = str(e)
            if "用户中断" not in err:
                self._show_toast("导出异常终止", f"发生错误: {err[:50]}", C_DANGER, 6000)
            self._log(f"\n⏸ {err}")
        finally:
            self.start_btn.configure(state="normal")

    # ═══════════════════════ 字幕提取 (已移除首次云空间下载提示) ═══════════════════════
    def _sub_thread(self):
        self._ask_yes_no("安全确认", "确定执行？此操作将提取 SRT 并隐藏字幕轨道", lambda: threading.Thread(target=self._extract_subs, daemon=True).start())

    def _fix_font_thread(self):
        self._ask_yes_no("安全确认", "确定执行？此操作将统一字体并解除隐藏", lambda: threading.Thread(target=self._fix_fonts, daemon=True).start())

    def _find_noto_sans_bold(self):
        dirs_to_check = [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        ]
        for d in dirs_to_check:
            if not os.path.exists(d): continue
            for fn in os.listdir(d):
                if not fn.lower().endswith(('.ttf', '.otf', '.ttc')): continue
                clean_name = re.sub(r'[^a-z0-9]', '', fn.lower())
                if 'notosansbold' in clean_name:
                    return os.path.join(d, fn).replace('\\', '/')
        return None

    def _unhide_tracks_thread(self):
        self._ask_yes_no("安全确认", "确定执行？此操作将解除所有草稿的字幕隐藏", lambda: threading.Thread(target=self._unhide_tracks, daemon=True).start())

    def _unhide_tracks(self):
        if self._jianying_is_running():
            self._log("✗ 请先完全退出剪映，再执行解除隐藏")
            self._show_toast("剪映仍在运行", "请完全退出剪映后重试", C_WARN, 6000)
            return
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 草稿根目录不存在"); return
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            if os.path.isdir(fp):
                items.append((fn, fp))
        ok = 0
        for fn, draft_dir in items:
            try:
                subtitle_jsons, _ = self._find_subtitle_jsons(draft_dir)
                if not subtitle_jsons:
                    continue
                track_copies = 0
                for _, data, _ in subtitle_jsons:
                    track_copies += self._set_text_tracks_hidden(data, False)
                backups = self._save_subtitle_jsons(subtitle_jsons)
                self._log(
                    f"  ✓ {fn} 已解除隐藏 "
                    f"(同步 {len(subtitle_jsons)} 份，恢复 {track_copies} 条文字轨道，"
                    f"备份 {len(backups)} 份)"
                )
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {fn} 失败: {e}")
        self._log(f"完成，共解除隐藏 {ok} 个草稿")
        self._show_toast("处理完成", f"成功解除 {ok} 个草稿的隐藏状态", C_SUCCESS, 6000)

    def _fix_fonts(self):
        font_path = self._find_noto_sans_bold()
        if not font_path:
            self._log("✗ 未在系统中找到 Noto Sans Bold 字体文件，请先安装该字体！")
            self._show_toast("缺少字体", "未检测到 Noto Sans Bold，无法统一字体", C_DANGER, 6000)
            return
            
        tpl_font = {"path": font_path, "id": ""}
        self._log(f"将强制所有草稿使用系统匹配字体:\n  {font_path}")
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 草稿根目录不存在"); return
        
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            jp = os.path.join(fp, 'draft_content.json')
            if os.path.isdir(fp) and os.path.exists(jp):
                items.append((fn, jp))
                
        ok = 0
        for fn, jp in items:
            try:
                with open(jp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for t in data.get('materials', {}).get('texts', []):
                    try:
                        c = json.loads(t.get('content', '{}'))
                        styles = c.get('styles', [])
                        if styles:
                            styles[0]['font'] = tpl_font
                            t['content'] = json.dumps(c, ensure_ascii=False)
                    except: pass
                with open(jp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                bak_path = jp + ".bak"
                if os.path.exists(bak_path):
                    with open(bak_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                self._log(f"  ✓ {fn} 字体已统一")
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {fn} 失败: {e}")
                
        self._log(f"完成，共处理 {ok} 个草稿")
        self._show_toast("处理完成", f"成功统一 {ok} 个草稿字体\n使用: {font_path}", C_SUCCESS, 6000)

    def _load_plain_draft_json(self, path):
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _jianying_is_running():
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/NH"],
                capture_output=True, text=True, errors="ignore",
                creationflags=0x08000000, timeout=5
            )
            return "JianyingPro.exe" in result.stdout
        except Exception:
            return False

    def _encrypted_draft_io(self):
        draft_io = getattr(self, "_jianying_draft_io", None)
        if draft_io is None:
            draft_io = JianyingDraftIO()
            self._jianying_draft_io = draft_io
            self._log(f"  · 已加载剪映草稿接口: {draft_io.dll_path}")
        return draft_io

    def _find_subtitle_jsons(self, draft_dir):
        candidates = [os.path.join(draft_dir, 'draft_content.json')]
        timelines_dir = os.path.join(draft_dir, 'Timelines')
        if os.path.isdir(timelines_dir):
            for timeline_id in sorted(os.listdir(timelines_dir)):
                timeline_dir = os.path.join(timelines_dir, timeline_id)
                if not os.path.isdir(timeline_dir):
                    continue
                candidates.append(os.path.join(timeline_dir, 'draft_content.json'))

        subtitle_jsons, encoded_drafts, load_errors, seen = [], [], [], set()
        for path in candidates:
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen or not os.path.isfile(path):
                continue
            seen.add(norm)
            data = self._load_plain_draft_json(path)
            storage = "plain"
            if data is None:
                try:
                    data = self._encrypted_draft_io().read_json(path)
                    storage = "encrypted"
                    encoded_drafts.append(path)
                except Exception as exc:
                    load_errors.append(f"{path}: {exc}")
                    continue
            text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
            if text_tracks and data.get('materials', {}).get('texts'):
                subtitle_jsons.append((path, data, storage))
        if not subtitle_jsons and load_errors:
            raise RuntimeError("；".join(load_errors))
        return subtitle_jsons, encoded_drafts

    def _subtitle_lines(self, data):
        texts = data.get('materials', {}).get('texts', [])
        text_by_id = {t.get('id'): t for t in texts if t.get('id')}
        segments = []
        for track in data.get('tracks', []):
            if track.get('type') == 'text':
                segments.extend(track.get('segments', []))
        segments.sort(key=lambda x: x.get('target_timerange', {}).get('start', 0))

        lines = []
        for segment in segments:
            material = text_by_id.get(segment.get('material_id'))
            if not material:
                continue
            content = material.get('content', {})
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    content = {'text': content}
            text = content.get('text', '') if isinstance(content, dict) else ''
            text = text or material.get('recognize_text', '') or material.get('base_content', '')
            if not isinstance(text, str) or not text.strip():
                continue
            timerange = segment.get('target_timerange', {})
            start = int(timerange.get('start', 0))
            duration = int(timerange.get('duration', 0))
            lines.extend([
                str(len(lines) // 4 + 1),
                f"{fmt_time(start)} --> {fmt_time(start + duration)}",
                text.strip(),
                "",
            ])
        return lines

    def _set_text_tracks_hidden(self, data, hidden=True):
        text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
        for track in text_tracks:
            for segment in track.get('segments', []):
                segment['visible'] = not hidden
        return len(text_tracks)

    def _save_subtitle_jsons(self, subtitle_jsons):
        stamp = time.strftime('%Y%m%d_%H%M%S') + f"_{int(time.time() * 1000) % 1000:03d}"
        backups = []
        try:
            for path, _, _ in subtitle_jsons:
                backup = f"{path}.before_subtitle_{stamp}.bak"
                shutil.copy2(path, backup)
                backups.append((path, backup))
            for path, data, storage in subtitle_jsons:
                if storage == "encrypted":
                    self._encrypted_draft_io().write_json(path, data)
                    verified = self._encrypted_draft_io().read_json(path)
                else:
                    temp_path = f"{path}.subtitle.tmp"
                    try:
                        with open(temp_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
                        os.replace(temp_path, path)
                    finally:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                    verified = self._load_plain_draft_json(path)
                if verified != data:
                    raise RuntimeError(f"写入后校验不一致，已回滚: {path}")
        except Exception:
            for path, backup in backups:
                if os.path.exists(backup):
                    shutil.copy2(backup, path)
            raise
        return [backup for _, backup in backups]

    def _extract_subs(self):
        self._log("提取字幕...")
        if self._jianying_is_running():
            self._log("✗ 请先完全退出剪映，再提取并隐藏字幕")
            self._show_toast("剪映仍在运行", "请完全退出剪映后重试", C_WARN, 6000)
            return
        base = self.draft_dir.get()
        if not os.path.exists(base):
            self._log("✗ 目录不存在")
            return
        out = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(out, exist_ok=True)

        items = []
        for name in os.listdir(base):
            draft_dir = os.path.join(base, name)
            if os.path.isdir(draft_dir) and name != 'srt':
                items.append((name, draft_dir))

        ok = 0
        failed = 0
        for name, draft_dir in items:
            try:
                subtitle_jsons, encoded_drafts = self._find_subtitle_jsons(draft_dir)
                if not subtitle_jsons:
                    continue
                if encoded_drafts:
                    self._log(f"  · {name} 已通过剪映接口解密 {len(encoded_drafts)} 份 draft_content.json")

                lines = self._subtitle_lines(subtitle_jsons[0][1])
                if not lines:
                    raise ValueError("检测到文字轨道，但没有可提取的字幕文本")

                srt_path = os.path.join(out, f"{name}.srt")
                srt_temp = f"{srt_path}.tmp"
                with open(srt_temp, 'w', encoding='utf-8') as f:
                    f.write("\n".join(lines))
                os.replace(srt_temp, srt_path)

                hidden_track_copies = 0
                for _, data, _ in subtitle_jsons:
                    hidden_track_copies += self._set_text_tracks_hidden(data, True)
                backups = self._save_subtitle_jsons(subtitle_jsons)

                self._log(
                    f"  ✓ {name} → {os.path.basename(srt_path)} "
                    f"(同步 {len(subtitle_jsons)} 份，隐藏 {hidden_track_copies} 条文字轨道副本，"
                    f"备份 {len(backups)} 份)"
                )
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {name}: {e}")
                failed += 1
        self._log(f"完成，成功提取并隐藏 {ok} 个，失败 {failed} 个")
        if failed:
            self._show_toast("部分处理失败", f"成功 {ok} 个，失败 {failed} 个\n请查看日志，失败草稿已自动回滚", C_WARN, 8000)
        else:
            self._show_toast("提取完成", f"共处理 {ok} 个草稿\n字幕已保存至桌面/srt，原轨道已隐藏", C_SUCCESS, 6000)

    # ═══════════════════════ 重命名 ═══════════════════════
    def _regex_preview_thread(self):
        folder = self.rename_folder_entry.get().strip().strip('"')
        template = self.fmt_entry.get().strip()
        if not folder:
            self._show_toast("提示", "请先选择目标文件夹", C_WARN)
            return
        if not os.path.isdir(folder):
            self._show_toast("路径错误", "目标文件夹不存在", C_WARN)
            return
        parsed = _rename_parse_template(template)
        if not parsed:
            self._show_toast("模板错误", "模板必须包含一个数字，例如：短剧名称_第1集", C_WARN)
            return

        prefix, suffix = parsed
        self.rename_folder = folder
        plan, skipped_duplicates, skipped_without_number = _rename_plan(folder, prefix, suffix)

        self.rename_plan = []
        for old_name, new_name in plan:
            source = os.path.join(folder, old_name)
            target = os.path.join(folder, new_name)
            self.rename_plan.append({
                "source": source,
                "target": target,
                "old_name": old_name,
                "new_name": new_name,
                "mode": "正则匹配"
            })

        self._js("renderRenamePlan", self.rename_plan)

        if skipped_duplicates:
            self._log("【正则匹配】检测到重复集数，以下文件跳过：")
            for old_name, target in skipped_duplicates:
                self._log(f"  {old_name} -> {target}")
        if skipped_without_number:
            self._log("【正则匹配】以下文件名没有识别到集数，已跳过：")
            for filename in skipped_without_number:
                self._log(f"  {filename}")
        if not self.rename_plan:
            self._show_toast("没有可改名文件", "【正则匹配】没有生成有效的重命名计划", C_WARN)
            return

        self._log(f"✓ 【正则匹配重命名】预览完成，共 {len(self.rename_plan)} 个文件")

    def _ai_preview_thread(self):
        if not getattr(self, "sel_files", None):
            folder = self.rename_folder_entry.get().strip().strip('"')
            if folder and os.path.isdir(folder):
                self.load_folder_for_ai(folder)
            if not getattr(self, "sel_files", None):
                self._show_toast("提示", "请先选择视频/字幕文件或载入文件夹", C_WARN)
                return
        self.ai_preview_btn.configure(state="disabled")
        threading.Thread(target=self._ai_preview, daemon=True).start()

    def _ai_preview(self):
        self._log("【AI 智能重命名】开始分析...")
        fns = [os.path.basename(f) for f in self.sel_files]
        fmt = self.ai_fmt_entry.get().strip() or "短剧名称_第X集"
        prompt = (f"Rename these files to: {fmt}\n"
                  f"Files: {json.dumps(fns, ensure_ascii=False)}\n"
                  f"Return ONLY raw JSON array with 'old' and 'new' keys.")
        try:
            client = self._client()
            r = client.models.generate_content(
                model=self.api_model, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1))
            t = r.text.strip()
            for pfx in ["```json", "```"]:
                if t.startswith(pfx): t = t[len(pfx):]
            if t.endswith("```"): t = t[:-3]
            data = json.loads(t.strip())
            self._fill_ai_ren(data)
        except Exception as e:
            msg = self._get_api_err_msg(e)
            self._log(f"✗ 【AI 重命名失败】{msg}")
            self._show_toast("AI 重命名失败", msg, C_DANGER, 6000)
            self.ai_preview_btn.configure(state="normal")

    def _fill_ai_ren(self, data):
        self.ai_preview_btn.configure(state="normal")
        dm = {os.path.basename(f): os.path.dirname(f) for f in self.sel_files}
        self.rename_plan = []
        for it in data:
            o, n = it.get("old"), it.get("new")
            if o and n and o in dm:
                d = dm[o]
                source = os.path.join(d, o)
                target = os.path.join(d, n)
                self.rename_plan.append({
                    "source": source,
                    "target": target,
                    "old_name": o,
                    "new_name": n,
                    "mode": "AI重命名"
                })
        self._js("renderRenamePlan", self.rename_plan)
        if self.rename_plan:
            self._log(f"✓ 【AI 智能重命名】预览已生成，共 {len(self.rename_plan)} 个文件")
        else:
            self._show_toast("AI 未返回匹配文件", "未能成功生成匹配规则，请检查格式或文件列表", C_WARN)

    def _do_exec_rename(self):
        if not self.rename_plan:
            return
        ok = 0
        skipped = 0
        errors = 0
        total = len(self.rename_plan)
        current_mode = self.ren_mode_val

        for item in self.rename_plan:
            source = item["source"]
            target = item["target"]
            old_name = item["old_name"]
            new_name = item["new_name"]
            if source == target or old_name == new_name:
                continue
            if os.path.exists(target):
                skipped += 1
                self._log(f"  跳过，目标已存在：{new_name}")
                continue
            try:
                os.rename(source, target)
                ok += 1
            except OSError as exc:
                errors += 1
                self._log(f"  ✗ {old_name}: {exc}")

        self._show_toast(
            f"【{current_mode}】完成" if not errors else f"【{current_mode}】部分完成",
            f"成功 {ok}/{total} 个，已存在 {skipped} 个，失败 {errors} 个",
            C_SUCCESS if not errors else C_WARN,
        )
        self.rename_plan = []
        self.sel_files = []
        self.file_lbl.configure(text="未选择")
        self._js("renderRenamePlan", [])

    def _snap_to_bottom_right(self):
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            title = "JIANYING AI PRO"
            hwnd = user32.FindWindowW(None, title)
            if not hwnd:
                return
            wa = wintypes.RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
            wr = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(wr))
            actual_w = wr.right - wr.left
            actual_h = wr.bottom - wr.top
            margin = 14
            nx = max(wa.left + margin, wa.right - actual_w - margin)
            ny = max(wa.top + margin, wa.bottom - actual_h - margin)
            user32.SetWindowPos(hwnd, -1, nx, ny, 0, 0, 0x0001)  # HWND_TOPMOST=-1, SWP_NOSIZE=0x0001
        except Exception:
            pass

    def run(self):
        # 标准大小 (780x540)，自动贴合屏幕右下角工作区
        win_w, win_h = 520, 480
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            wa = wintypes.RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            dpi = user32.GetDpiForSystem()
            scale = dpi / 96.0 if dpi else 1.5
            phys_w = int(win_w * scale)
            phys_h = int(win_h * scale)
            pos_x = max(10, int((wa.right - phys_w - 14) / scale))
            pos_y = max(10, int((wa.bottom - phys_h - 14) / scale))
        except Exception:
            pos_x, pos_y = 600, 300

        self._api = _JsBridgeApi(self)
        self.window = webview.create_window(
            "JIANYING AI PRO",
            html=BANKNOTE_HTML,
            js_api=self._api,
            width=win_w,
            height=win_h,
            x=pos_x,
            y=pos_y,
            resizable=False,
            frameless=True,
            easy_drag=False,
            on_top=True,
            background_color="#FBF9F5",
        )
        webview.start(lambda: self.after(150, self._snap_to_bottom_right), debug=False)


# ─── 启动 ──────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.run()
