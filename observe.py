#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基础设施：日志（含路径打码）、计时与单位换算。**不 import 界面**，可单独拿去给别的工具用。"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


日志 = {"句柄们": [], "路径": None, "缓冲": []}   # 日志落点（可能多个）＋ 已写过的行（补开头的用）

# 日志里不留真实路径/文件名 —— 一律换成哈希
# ⇒ 凡是要进日志的文本，**里面的路径/文件名都换成 #<哈希前8位>**。
#    固定盐：防「拿常见路径去反查哈希」。同一个路径永远映到同一个号，所以还能看出「是不是同一个」。

# 路径打码：凡是要进日志的文本，里面的路径/文件名都换成 #<哈希前8位>
路径盐 = "smart-unpack"
路径_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s]+"                        # ① 绝对路径 D:\a\b
    # ② 相对路径 a\b —— 首段**不含冒号**（否则会把「文件夹：D:\x」的标签一起吃掉）、
    #    且**至少 2 个字符**（这样进度号「[1/4]」里那个 1/4 就动不了）
    r"|[^\s\\/（）()：:\[\]]{2,}[\\/][^\s\\/（）()\[\]]+"
    # ③ 带扩展名的名字 xxx.mp3（扩展名须以字母开头，免得把 1.6 这种数字也码掉）
    r"|[^\s\\/（）()：:]+\.[A-Za-z][A-Za-z0-9]{0,6}"
)


def 打码路径(文本):
    """把文本里的路径/文件名换成 `#<哈希8位>`。路径之外的字原样留着（时间、MBps 都还在，照样能排查）。"""
    def _换(m):
        s = m.group(0)
        return "#" + hashlib.md5((路径盐 + s).encode("utf-8", "replace")).hexdigest()[:8]
    try:
        return 路径_RE.sub(_换, 文本)
    except Exception:
        return 文本

def 报(msg=""):
    """**所有**输出都从这里走。两个落点**故意不一样**：
       · **屏幕 → 原文**：给人看的，得知道是哪个文件、哪个目录
       · **日志文件 → 打码后**：日志是「出事要外发」的东西，不许把路径/文件名带出去
    为什么要落日志：窗口一闪就关、程序崩掉时屏幕上什么都没了 —— 不留日志就无从查错。"""
    msg = str(msg)
    print(msg, flush=True)
    干净 = "%s  %s" % (time.strftime("%H:%M:%S"), 打码路径(msg))
    日志["缓冲"].append(干净)
    for h in 日志["句柄们"]:
        try:
            h.write(干净 + "\n")
            h.flush()
        except Exception:
            pass



def 加日志落点(路径):
    """同一个日志再加一个落点（如输出目录里那份）。"""
    try:
        h = io.open(路径, "a", encoding="utf-8")
    except OSError as e:
        报("  ⚠️ 日志写不进 %s：%s" % (路径, e))
        return
    # 新落点要**把开头补上**：它是在程序跑了一段之后才建的，不补的话它缺启动头/时间闸那几行
    for 行 in 日志["缓冲"]:
        try:
            h.write(行 + "\n")
        except Exception:
            pass
    日志["句柄们"].append(h)
    if not 日志["路径"]:
        日志["路径"] = 路径



def 日志文件名(前缀="运行日志"):
    """日志文件名带到**分钟**（先要日期，再要「精确到分钟」）——
    同一天跑两次不会再追加进同一个文件、混成一锅；一眼知道是哪一次跑的。"""
    return "%s-%s.txt" % (前缀, time.strftime("%Y-%m-%d_%H%M"))



def 开日志():
    """日志落**两个点**，先落一个就能保证「后面任何一步崩掉都已有东西可查」。
    ① `%LOCALAPPDATA%\\smart-unpack\\运行日志.txt` —— **稳定落点**。
       exe 若放在 `dist\\` 里，重新打包时整个 `dist\\` 会被清掉，落在「exe 旁边」的日志会跟着消失。
       **主日志必须放在不会被构建清掉的地方。**
    ② `exe/脚本 旁边` 的那份 —— 只是方便顺手看，**会被下次重打清掉，不当正本**。"""
    if 日志["句柄们"]:          # 已经有落点了就直接返回 —— 重复调用会把同一份日志写两遍
        return
    import tempfile
    稳 = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "smart-unpack")
    for d in (稳, tempfile.gettempdir()):
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, 日志文件名())
            日志["句柄们"].append(io.open(p, "a", encoding="utf-8"))
            日志["路径"] = p
            break
        except OSError:
            continue
    基 = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    try:
        日志["句柄们"].append(io.open(
            os.path.join(基, 日志文件名("smart-unpack日志")), "a", encoding="utf-8"))
    except OSError:
        pass
    if not 日志["句柄们"]:
        报("（警告：日志文件没写成功，只有屏幕输出）")



def 关日志():
    for h in 日志["句柄们"]:
        try:
            h.close()
        except Exception:
            pass



def 人读字节(n):
    for 单位 in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or 单位 == "TB":
            return ("%.0f %s" % (n, 单位)) if 单位 == "B" else ("%.2f %s" % (n, 单位))
        n /= 1024.0



def 去重(序列):
    """去重但保序（先来的排前面）。"""
    见过的 = set()
    出 = []
    for x in 序列:
        if x not in 见过的:
            见过的.add(x)
            出.append(x)
    return 出


# ------------------------------------------------------------
# 扫描
# ------------------------------------------------------------

