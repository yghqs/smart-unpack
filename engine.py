#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解压引擎：认内容 · 解一层 · 续解 · 预检 · 摊平/集中 · 超时 · 0字节 · 防越权。
**不 import tkinter、不碰界面** —— 谁都能 import 去用。"""
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

from observe import 报, 人读字节, 去重


class 超限(Exception):
    """累计解出量触顶，全场停止。"""



# ------------------------------------------------------------
# 可调配置：**正本在 config.py**（命令行 > 配置文件 > config.py 里的默认值）。
# 这里只把它读进来当模块常量用 —— 要改默认值请改 config.py 或配置文件，别改这儿。
# 运行中允许被覆盖的（如 `输出后缀`），由入口设 `engine.输出后缀 = ...`。
# ------------------------------------------------------------
import config

配置 = config.载入()[0]
输出后缀 = 配置["输出后缀"]
心跳秒 = 配置["心跳秒"]
条目记账秒 = 配置["条目记账秒"]
整包明细秒 = 配置["整包明细秒"]
停滞秒 = 配置["停滞秒"]
集中模式 = 配置["集中模式"]

try:
    import pyzipper
    有AES = True
except ImportError:
    有AES = False

中间层根 = None          # 由调用方设成 <输出根>\_tmp
中间层清单 = []
中间层统计 = {"个数": 0, "字节": 0}


def 列文件(根, 要递归):
    结果 = []
    if 要递归:
        for 目录, _, 文件们 in os.walk(根):
            for f in 文件们:
                结果.append(os.path.join(目录, f))
    else:
        for f in sorted(os.listdir(根)):
            全 = os.path.join(根, f)
            if os.path.isfile(全):
                结果.append(全)
    return 结果



def 规整后缀(列表):
    """把使用者写的后缀规整成 .xxx 小写：`mp4` / `MP4` / `.mp4` 都当 `.mp4`。"""
    出 = []
    for s in 列表 or []:
        s = (s or "").strip().lower()
        if not s:
            continue
        出.append(s if s.startswith(".") else "." + s)
    return 出



def 匹配后缀(路径, 后缀们):
    """后缀们 为空 = 全部都要。"" （空串）代表「无后缀」的文件，如 Makefile。"""
    if not 后缀们:
        return True
    后缀 = os.path.splitext(路径)[1].lower()
    return 后缀 in [s.lower() for s in 后缀们]



def 输出名(源路径):
    """输出名：原名 + 输出后缀（原名已经是该后缀就不重复加）。"""
    名 = os.path.basename(源路径)
    if 名.lower().endswith(输出后缀.lower()):
        return 名
    return 名 + 输出后缀



def 出目录名(副名):
    """解压落到哪个子目录：把「刚加上的那个后缀」摘掉，用原名当目录名。
    全摘光就退回原名（防 .zip 这种极端名）。"""
    if 副名.lower().endswith(输出后缀.lower()):
        基 = 副名[:len(副名) - len(输出后缀)]
        return 基 or 副名
    return 副名


# ------------------------------------------------------------
# zip 处理
# ------------------------------------------------------------

魔数表 = [(b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip（空包）"), (b"7z\xbc\xaf\x27\x1c", "7z"),
         (b"Rar!\x1a\x07", "RAR"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
         (b"\xfd7zXZ\x00", "xz"), (b"\xff\xd8\xff", "JPEG"), (b"\x89PNG\r\n\x1a\n", "PNG"),
         (b"RIFF", "RIFF（webp/wav）"), (b"ID3", "MP3(ID3)"), (b"\xff\xfb", "MP3"),
         (b"GIF8", "GIF"), (b"%PDF", "PDF"), (b"SQLite", "SQLite"), (b"ustar", "tar")]



带压缩包味的 = ("7z", "RAR", "gzip", "bzip2", "xz", "tar", "zip（空包）")


def 认头部(路径):
    """读头几个字节，认一认这到底是什么。
    用途：**日志要能自己答「这个为什么没解」**（问题该在日志里，不该回头问使用者）——
    认出来是 7z/RAR 之类，日志就能直接说「它是 7z，不是 zip」；认不出就把十六进制原样留证。"""
    try:
        with open(路径, "rb") as f:
            h = f.read(16)
    except OSError as e:
        return "读不出头（%s）" % e
    for 魔, 名 in 魔数表:
        if h.startswith(魔):
            return "%s（%s）" % (名, h[:4].hex())
    return "认不出（头 4 字节 %s）" % h[:4].hex()



def 打开包(路径):
    """返回可读的 zip 对象（优先 pyzipper，退回标准库）。"""
    if 有AES:
        return pyzipper.AESZipFile(路径, "r")
    return zipfile.ZipFile(路径, "r")



def 修中文名(zf, info):
    """ZIP 里没打 UTF-8 标志的名字，标准库按 cp437 解 ⇒ 中文变乱码。
    这里试把它扳回 GBK。扳不动就原样返回。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename



def 需要密码(info):
    return bool(info.flag_bits & 0x1)



def 试密码(zf, 条目们, 密码):
    """拿一个密码去读第一个非空条目的头几个字节：读得动就是对的。"""
    if isinstance(密码, str):
        密码 = 密码.encode("utf-8", "surrogateescape")
    zf.setpassword(密码)
    for info in 条目们:
        if info.file_size == 0:
            continue
        try:
            with zf.open(info) as f:
                f.read(64)
            return True
        except RuntimeError:
            return False
        except Exception:
            return False
    return True  # 全是空文件，没法验，当它过



def 落点(base, 名字):
    """防 zip slip：条目名不许写到 base 之外。越界返回 None。"""
    名字 = 名字.replace("\\", "/").lstrip("/")
    目标 = os.path.normpath(os.path.join(base, 名字))
    base_n = os.path.normpath(base)
    if 目标 == base_n or 目标.startswith(base_n + os.sep):
        return 目标
    return None



def 不重名(路径):
    """目标已存在就改名 -1 -2 …，绝不覆盖已有文件。"""
    if not os.path.exists(路径):
        return 路径
    根, 尾 = os.path.splitext(路径)
    i = 1
    while os.path.exists("%s-%d%s" % (根, i, 尾)):
        i += 1
    return "%s-%d%s" % (根, i, 尾)



def 预检包(路径, 密码们):
    """**只读**地先看一眼：这个包能不能解、要不要密码、给的密码对不对。
    目的：密码对不上就**当场跳过**，不做任何多余的事。
    返回 (能不能继续, 说明, 对上的密码 or None)。**不写任何东西。**"""
    try:
        with 打开包(路径) as zf:
            条目们 = zf.infolist()
            if not 条目们:
                return True, "空包", None
            if not any(需要密码(i) for i in 条目们):
                return True, "无需密码", None
            if not 有AES and any(需要密码(i) and i.compress_type == 99 for i in 条目们):
                return False, "AES 加密但没装 pyzipper，解不了", None
            if not 密码们:
                return False, "需要密码，但没提供密码", None
            for 序, pwd in enumerate(密码们, 1):
                if 试密码(zf, 条目们, pwd):
                    return True, "需密码，第 %d 个密码对上了" % 序, pwd
            return False, "需要密码，给的 %d 个都不对" % len(密码们), None
    except Exception as e:
        if type(e).__name__ in ("BadZipFile", "LargeZipFile"):
            # 别只说「不是 zip」——**把它到底是什么写出来**，日志才能自答
            return False, "不是有效的 ZIP；它的真身是 %s" % 认头部(路径), None
        return False, "打不开：%s: %s" % (type(e).__name__, e), None



def 找7z():
    """找 7z.exe：先看**程序旁边**（打包时跟 exe 放一起），再看 tools\\bin\\。找不到返回 None。"""
    import shutil as _sh
    基 = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    for p in (os.path.join(基, "7z.exe"),
              os.path.join(基, "_internal", "7z.exe"),      # PyInstaller 单目录模式会放这儿
              os.path.join(基, "bin", "7z.exe"),
              os.path.join(os.path.dirname(基), "bin", "7z.exe")):
        if os.path.isfile(p):
            return p
    return _sh.which("7z") or _sh.which("7z.exe")



def 用7z解包(包路径, 目标目录, 密码, 状态, 空表):
    """**ZipCrypto 的包交给 7z 解** —— pyzipper 的 ZipCrypto 是**纯 Python 逐字节**实现，
    实测 **1.74 MB/s**（同一个 200MB 包：pyzipper 114.9 秒 / 7z 1.6 秒，**71 倍**）。
    做法：先解到 `_tmp\\` 下的临时目录，再**摊平**搬进调用方给的目标目录（同盘改名，快），临时目录最后收走。
    返回 (成功?, 说明, 本包字节)；找不到 7z 时返回 None ⇒ 调用方回退 pyzipper。"""
    七 = 找7z()
    if not 七:
        return None
    临时 = 不重名(os.path.join(中间层根 or tempfile.gettempdir(),
                              "7z-" + os.path.basename(包路径)))
    try:
        os.makedirs(临时, exist_ok=True)
        r = subprocess.run([七, "x", "-y", "-bso0", "-bsp0", "-p" + 密码,
                            "-o" + 临时, 包路径],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
    except Exception as e:
        return False, "7z 起不来：%s: %s" % (type(e).__name__, e), 0
    中间层清单.append(临时)                    # 临时目录也算过程产物，收尾一并收走
    if r.returncode != 0:
        return False, "7z 解不开（退出码 %d：多半是密码不对）" % r.returncode, 0
    本包 = 0
    写 = 0
    for 当前, _, 文件们 in os.walk(临时):
        for 名 in sorted(文件们):
            p = os.path.join(当前, 名)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            if s == 0:
                空表[0] += 1
                continue
            if 状态["已解字节"] + s > 状态["上限字节"]:
                raise 超限()
            出 = 不重名(os.path.join(目标目录, 名))     # 摊平：只取文件名
            try:
                os.replace(p, 出)
            except OSError:
                shutil.move(p, 出)
            本包 += s
            状态["已解字节"] += s
            写 += 1
    return True, "（ZipCrypto ⇒ 走 7z 引擎）写出 %d 个，%s" % (写, 人读字节(本包)), 本包



def 摊平搬移(源目录, 目标目录):
    """把 源目录 里的文件**平铺**搬进 目标目录（重名自动加 -1）。
    集中模式用它把「工作目录」里的成品倒进那个唯一的大文件夹。返回 (字节, 个数)。"""
    字节 = 0
    个数 = 0
    for 当前, _, 文件们 in os.walk(源目录):
        for 名 in sorted(文件们):
            p = os.path.join(当前, 名)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            出 = 不重名(os.path.join(目标目录, 名))
            try:
                os.replace(p, 出)
            except OSError:
                shutil.move(p, 出)
            字节 += s
            个数 += 1
    return 字节, 个数



def 量目录(目录):
    """量一个目录：返回 (字节, 文件数)。跳过以 `_` 开头的工具自己写的文件（日志/报告/台账）。"""
    字节 = 0
    个数 = 0
    if os.path.isdir(目录):
        for 当前, 子们, 文件们 in os.walk(目录):
            子们[:] = [d for d in 子们 if not d.startswith("_")]   # 跳过 _记录\_tmp\ 这类工具自己的目录
            for 名 in 文件们:
                if 名.startswith("_"):
                    continue
                个数 += 1
                try:
                    字节 += os.path.getsize(os.path.join(当前, 名))
                except OSError:
                    pass
    return 字节, 个数



def 解一个包(包路径, 目标目录, 密码们, 状态, 预设密码=None, 预设说明=None):
    """逐条目解压；返回 (状态文本, 本条解出字节数)。
    状态复用里的 已解字节 由调用方累加 —— 触顶会抛 超限。"""
    os.makedirs(目标目录, exist_ok=True)
    本包字节 = 0
    try:
        with 打开包(包路径) as zf:
            条目们 = zf.infolist()
            if not 条目们:
                return "空包（无条目）", 0

            加密 = any(需要密码(i) for i in 条目们)
            状态["条目数"] = len(条目们)

            命中说明 = "无密码"
            if 加密:
                if not 有AES and any(需要密码(i) and i.compress_type == 99 for i in 条目们):
                    return "是 AES 加密，但没装 pyzipper ⇒ 解不了（装它：pip install pyzipper）", 0
                对 = 预设密码                     # 预检已经对上过，就别再重试一遍
                if 对 is None:
                    if not 密码们:
                        return "需要密码，但没提供密码（用 --密码 给一个）", 0
                    for 序, pwd in enumerate(密码们, 1):
                        if 试密码(zf, 条目们, pwd):
                            对 = pwd
                            状态["命中"] = 序
                            break
                    if 对 is None:
                        return "需要密码，给的 %d 个都不对" % len(密码们), 0
                    命中说明 = "需密码，第 %d 个密码对上了" % 状态["命中"]
                else:
                    命中说明 = 预设说明 or "需密码（预检时已对上）"
                # ⚠️ **ZipCrypto 交给 7z**：pyzipper 的 ZipCrypto 是纯 Python 逐字节实现，
                #    实测 1.74 MB/s（同一个 200MB 包 pyzipper 114.9 秒 vs 7z 1.6 秒，71 倍）。
                #    判据：加密条目里**没有 compress_type==99**（99 = WinZip AES）⇒ 就是 ZipCrypto。
                if not any(i.compress_type == 99 for i in 条目们 if 需要密码(i)):
                    zf.close()
                    空表 = [0]          # 用 list 承接「0 字节条目几个」这个出参
                    r7 = 用7z解包(包路径, 目标目录, 对, 状态, 空表)
                    if r7 is not None:
                        _好, 述7, 字节7 = r7
                        if 空表[0]:
                            述7 += "；0 字节条目 %d 个（不写出）" % 空表[0]
                        return 述7, 字节7
                    报("          ⚠️ 没找到 7z.exe ⇒ 只能用纯 Python 解 ZipCrypto（会慢几十倍）")
                zf.setpassword(对.encode("utf-8", "surrogateescape") if isinstance(对, str) else 对)

            写了 = 0
            空 = 0
            跳过 = []
            未完成原因 = []      # 有任何一条 ⇒ 这个包是**半成品**，要留标记，下次不许当「已解过」跳过
            for info in 条目们:
                名字 = 修中文名(zf, info)
                if info.is_dir() or 名字.endswith("/"):
                    continue          # 只嵌一层：压缩包内部的目录结构不要了
                # 0 字节的条目不写出空文件 —— 直接不理它
                if info.file_size == 0:
                    空 += 1
                    continue
                # 只嵌一层：条目自带的路径整个丢掉，只用文件名，直接摊进包目录
                出 = 落点(目标目录, os.path.basename(名字.replace("\\", "/")))
                if 出 is None:
                    跳过.append(名字)
                    continue
                出 = 不重名(出)
                try:
                    本条目头 = 本包字节
                    条目头时间 = time.perf_counter()
                    上次报 = 条目头时间
                    上次字节 = 本包字节
                    条目限 = 状态.get("超时秒", 0) or 0
                    停限 = 状态.get("停滞秒", 0) or 0
                    超了 = False
                    块头时间 = time.perf_counter()
                    with zf.open(info) as src, open(出, "wb") as dst:
                        while True:
                            块 = src.read(1024 * 1024)
                            if not 块:
                                break
                            # 先记账、再判顶、最后才落盘 ⇒ 绝不写出上限之外的字节
                            if 状态["已解字节"] + len(块) > 状态["上限字节"]:
                                raise 超限()
                            本包字节 += len(块)
                            状态["已解字节"] += len(块)
                            dst.write(块)
                            现在 = time.perf_counter()
                            间隔 = 现在 - 块头时间       # 这一块**等了多久才到**
                            块头时间 = 现在
                            # 判「卡死」：这一块等了太久 ⇒ 放弃它、继续下一个
                            #   （只看有没有进展，**不看总耗时** —— 慢但一直在写的不动它）
                            if 停限 > 0 and 间隔 >= 停限:
                                超了 = True
                                报("          ⏱ 条目超时（卡住 %.0f 秒没动静）⇒ 放弃它、继续下一个：%s"
                                   % (间隔, 名字))
                                break
                            if 条目限 > 0 and 现在 - 条目头时间 >= 条目限:
                                超了 = True
                                报("          ⏱ 条目超时（总耗时 >%.0f 秒）⇒ 放弃它、继续下一个：%s"
                                   % (条目限, 名字))
                                break
                            # 大条目的心跳：≥20MB 的，最多每 3 秒吭一声（免得屏幕上半天没字、像卡死）
                            if info.file_size >= 20 * 1024 * 1024 and 现在 - 上次报 >= 心跳秒:
                                # ⚠️ 速度要用**这一段新增的字节**算，不能拿「从条目开头累计的字节」
                                #    除以「距上次报的间隔」—— 卡顿后会报出一个虚高的假速度
                                快 = (本包字节 - 上次字节) / (现在 - 上次报) / 1048576
                                报("          ⏳ %s：%s / %s（%.0f MBps，这一段）"
                                   % (名字, 人读字节(本包字节 - 本条目头),
                                      人读字节(info.file_size), 快))
                                上次报 = 现在
                                上次字节 = 本包字节
                    if 超了:
                        try:
                            # 半成品不能留着冒充成品 —— 改名标出来（改名不算删东西）
                            os.replace(出, 出 + ".超时未完成")
                        except OSError:
                            pass
                        跳过.append("%s（超 %.0f 秒已放弃；半成品命名成 .超时未完成）" % (名字, 条目限))
                        未完成原因.append("条目「%s」超过 %.0f 秒被放弃" % (名字, 条目限))
                        状态["超时数"] = 状态.get("超时数", 0) + 1
                        continue
                    写了 += 1
                    费 = time.perf_counter() - 条目头时间
                    if 费 >= 条目记账秒:
                        报("          ⏱ 这个条目单独花了 %.1f 秒：%s（%s，%.1f MBps）"
                           % (费, 名字, 人读字节(本包字节 - 本条目头),
                              (本包字节 - 本条目头) / 1048576.0 / 费 if 费 else 0))
                except 超限:
                    with open(os.path.join(目标目录, "_未完成.txt"), "w", encoding="utf-8") as m:
                        m.write("累计解出触顶，本包解到「%s」为止，后面没解。\n" % 名字)
                    raise
                except Exception as e:
                    跳过.append("%s（%s）" % (名字, e))
                    未完成原因.append("条目「%s」出错：%s: %s" % (名字, type(e).__name__, e))

            # ⚠️ 半成品要**留标记** —— 不然下次「已解过就跳过」会把它当成解完了，这个包就**永远解不完**。
            #    （容易漏：只在容量触顶时写标记不够 —— 超时/出错也必须写，否则这个包永远解不完）
            if 未完成原因:
                状态["本包未完成"] = True      # 台账那边靠这个决定「记不记完成」
                try:
                    with io.open(os.path.join(目标目录, "_未完成.txt"), "w", encoding="utf-8") as m:
                        m.write("⚠️ 这个包**没解完**，下面写原因。\n")
                        m.write("下次跑会自动把它挪走重做，不用你管；要现在就重做加 --全部重来。\n\n")
                        for s in 未完成原因[:30]:
                            m.write("  · %s\n" % s)
                except OSError as e:
                    报("          ⚠️ 半成品标记没写成（%s）" % e)
                else:
                    报("          ⚠️ 本包是**半成品**（%d 条没解成）—— 已留 _未完成.txt，下次会自动重做"
                       % len(未完成原因))

            尾巴 = ""
            if 空:
                尾巴 += "；0 字节条目 %d 个（不写出）" % 空
            if 跳过:
                尾巴 += "；跳过 %d 个：%s" % (len(跳过), "、".join(跳过[:3]) + ("…" if len(跳过) > 3 else ""))
            return "%s；条目 %d，写出 %d 个，%s%s" % (命中说明, len(条目们), 写了, 人读字节(本包字节), 尾巴), 本包字节
    except 超限:
        raise
    except Exception as e:
        # ⚠️ pyzipper 抛的是它**自己**的 BadZipFile，不是标准库那个 ⇒ 只能按类名认
        if type(e).__name__ in ("BadZipFile", "LargeZipFile"):
            return "不是有效的 ZIP（后缀改了也不是）", 0
        return "出错：%s: %s" % (type(e).__name__, e), 本包字节


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
# 中间层（过程产物）的中转与收尾
# ------------------------------------------------------------
def 挪走中间层(p, 基目录):
    """把**已经解开过的**内层压缩包挪去 `_tmp\\`（中间过程集中到 tmp）。
    它只是个中转站 —— 挪进去的路径记进 `中间层清单`，包解完就 `删中间层()` 收走。"""
    if not 中间层根:
        return
    try:
        相对 = os.path.relpath(p, 基目录)
    except ValueError:
        相对 = os.path.basename(p)
    目标 = 不重名(os.path.join(中间层根, 相对))
    try:
        大小 = os.path.getsize(p)
    except OSError:
        大小 = 0
    try:
        os.makedirs(os.path.dirname(目标), exist_ok=True)
        os.replace(p, 目标)
        中间层清单.append(目标)
        中间层统计["个数"] += 1
        中间层统计["字节"] += 大小
        报("          （中间层压缩包挪去 _tmp\\%s，%s）" % (相对, 人读字节(大小)))
    except OSError as e:
        报("          ⚠️ 中间层挪不动（%s）—— 先留在原地，不删" % e)



def 删中间层():
    """把本次挪进 _tmp 的中间层压缩包收走（中转站用完即清，不留）。
    ⚠️ 这是本工具**唯一会删东西**的地方，边界卡死：
       只删 `中间层清单` 里记着的路径（＝我们自己刚挪进去的），别的一律不碰；
       删不掉就留着并出声，**绝不静默失败**。"""
    if not 中间层清单:
        return
    n = 0
    没删掉 = []
    for p in 中间层清单:
        try:
            if os.path.isfile(p):
                os.remove(p)
                n += 1
            elif os.path.isdir(p):
                shutil.rmtree(p)      # 被挪走的「半成品包目录」也属中间过程，一并收走
                n += 1
        except OSError as e:
            没删掉.append("%s（%s）" % (os.path.basename(p), e))
    报("          （中间层收走 %d 个临时压缩包）" % n)
    if 没删掉:
        报("          ⚠️ 有 %d 个中间层没删掉：%s" % (len(没删掉), "、".join(没删掉[:3])))
    中间层清单.clear()
    # _tmp 空了就顺手收掉这个文件夹（只在这一层，不递归）
    try:
        if 中间层根 and os.path.isdir(中间层根) and not os.listdir(中间层根):
            os.rmdir(中间层根)
    except OSError:
        pass



def 再解一层(目录, 剩余, 密码们, 状态, 行们, 标签):
    """把 目录 里「真身是压缩包」的文件再解一层 —— 即「要解压两次」那种嵌套。
    靠**看内容**（`is_zipfile`）判断，不看后缀（外层刚被我们改成 .zip，内层往往还是怪后缀）。
    层数用尽或没得再解就停；触顶照样抛 超限，由上层统一收。"""
    if 剩余 <= 0 or not os.path.isdir(目录):
        return
    # ⚠️ 必须**连子文件夹一起找**：内层压缩包常常不在外层目录里，而是躺在它解出来的某个
    #    子文件夹里（常见布局：外层/里面的mp3文件/要下载的文件.mp3）。只看直接子文件 ⇒ 就停在一层。
    #    先把待办列出来再动手 —— 免得边解边长出来的新目录被 os.walk 半路吃进去。
    待办 = []
    for 当前, _, 文件们 in os.walk(目录):
        for 名 in sorted(文件们):
            待办.append(os.path.join(当前, 名))
    解了 = 0
    别的压缩包 = []
    for p in 待办:
        if not os.path.isfile(p):
            continue
        if not zipfile.is_zipfile(p):
            # ⚠️ 认不出来**也要在日志里交代它到底是什么** —— 不能默默 continue，
            #    否则「内层没解开」在日志里查不到任何原因（问题该在日志里，不该回头问使用者）
            什么 = 认头部(p)
            if any(k in 什么 for k in 带压缩包味的):
                别的压缩包.append("%s  =  %s" % (os.path.relpath(p, 目录), 什么))
            continue
        名 = os.path.basename(p)
        # 只嵌一层：内层的内容**摊进同一个包目录**（不再套子目录）
        述, 字节 = 解一个包(p, 目录, 密码们, 状态)
        报("        ↳ 内层 %s → %s" % (os.path.relpath(p, 目录), 述))
        行们.append((标签 + " / 内层 " + os.path.relpath(p, 目录), 名, 述, 字节))
        挪走中间层(p, 目录)
        解了 += 1
        再解一层(目录, 剩余 - 1, 密码们, 状态, 行们, 标签 + " / " + 名)
    # 本层结论写清楚：看了几个、解了几个、剩下那些为什么没解
    报("        · 本层看过 %d 个文件，其中 %d 个真身是 zip（已解）" % (len(待办), 解了))
    if 别的压缩包:
        报("        · ⚠️ 另有 %d 个**是压缩包但不是 zip**，本工具认不出、开不了 —— 认得的都列在这："
           % len(别的压缩包))
        for s in 别的压缩包[:10]:
            报("            %s" % s)
        if len(别的压缩包) > 10:
            报("            …还有 %d 个" % (len(别的压缩包) - 10))


# 注：密码框不是独立窗口 —— 已**并进 `选后缀和密码()` 一个窗口**，少一次来回。


