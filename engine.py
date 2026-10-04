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
    """**盘上占用**触顶，全场停止（不是「累计写出量」—— 中间层收走会还回额度）。"""



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
不拆后缀 = config.仅处理后缀列表(配置["不拆后缀"])     # 同一个「逗号分隔 → 列表」帮手，别再写一个

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



def 预检包(路径, 密码们, 剩余字节=None):
    """**只读**地先看一眼：这个包能不能解、要不要密码、给的密码对不对。
    目的：密码对不上就**当场跳过**，不做任何多余的事。
    返回 (能不能继续, 说明, 对上的密码 or None)。**不写任何东西。**
    `剩余字节` 给 7z/RAR 那条路用：它整包落完才轮到我们点数，上限得在**解之前**卡。"""
    if 是7z或rar(路径):
        return 预检7z包(路径, 密码们, 剩余字节)
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



# ------------------------------------------------------------
# 7z / RAR：**按内容认，交给 7z.exe 解**
# ------------------------------------------------------------
# ⚠️ 四条实测铁律，改这里之前先读：
#   ① **永远带 `-p`**。7z.exe 在「没给 -p 又遇加密包」时会往 stdin 弹 `Enter password`
#      **死等** —— 批处理时会卡在那一步不动。
#   ② **`-slt` 的键是 7z 自己的英文固定键**（`Type=` / `Size=` / `Encrypted=` / `Path=`），
#      不随系统语言变，可以直接解析。
#   ③ **7z 是整包落完才轮到我们点数**，不像 zip 能边解边熔断 ⇒ 上限必须**解之前**先卡。
#   ④ **凡解析 7z 输出一律带 `-sccUTF-8`**，且判「命中」要**看真读到字节**、不是看退出码 ——
#      不加 `-sccUTF-8` 时 7z 按本地码页吐文件名，按 UTF-8 解成乱码 ⇒ 拿乱码名当探针
#      **匹配不上任何条目** ⇒ 7z 返回 **0**（"没有要处理的文件"也算成功）⇒ 候选里第一条必然假命中。


def 是7z或rar(路径):
    """看**内容**判断，不看后缀 —— 后缀常常不可信（.mp3/.png 里装着压缩包）。
    返回 "7z" / "RAR"；不归这条路管的一律 None。

    ⚠️ **不能只看头 8 个字节**（实测踩到）：**图种**（PNG/JPG/TXT 后面**直接接**一个压缩包，
       网盘分享的常见手法）—— 7z/RAR 的标志**在文件头**，前面垫了张图，标志就**不在头上了**
       ⇒ 只看头的话**整条漏掉**（实测：`PNG+7z`／`PNG+rar` 全漏，
       而 **7-Zip 自己扫整个文件、认得出**）。
       （`PNG+zip` 那种侥幸没事 —— 因为 zip 的标志在**文件尾** EOCD，`zipfile` 是从尾部找的。）
    ⇒ **头没命中就从头分块扫一遍，找到即停。** 开销＝顺序读到标志为止；
       真图片才需要读到底。"""
    try:
        with open(路径, "rb") as f:
            头 = f.read(8)
    except OSError:
        return None
    R = 认这类标志(头)
    if R or 头[:2] == b"PK":         # 头是 zip：交给 zipfile 那条路（它会自己找尾部 EOCD）
        return R
    return 通扫找标志(路径)


def 认这类标志(字节):
    """在一段字节里认 7z / RAR 的标志。返回 "7z" / "RAR" / None。"""
    if b"7z\xbc\xaf\x27\x1c" in 字节:
        return "7z"
    # RAR4 = 52 61 72 21 1A 07 00；RAR5 = 52 61 72 21 1A 07 01 00（第 7 字节不同，分得开）
    if b"Rar!\x1a\x07\x01\x00" in 字节 or b"Rar!\x1a\x07\x00" in 字节:
        return "RAR"
    return None


def 通扫找标志(路径, 块大小=1 << 20):
    """从头按块扫，找 7z / RAR 的标志。**找到即停**。返回 "7z" / "RAR" / None。
    ⚠️ 块与块之间要**留 7 个字节的重叠** —— 标志可能正好跨在两块的交界上，
       不留重叠就会漏（RAR5 的标志是 8 字节，最坏情况整段跨在缝里）。"""
    重叠 = b""
    try:
        with open(路径, "rb") as f:
            while True:
                块 = f.read(块大小)
                if not 块:
                    return None
                合 = 重叠 + 块
                R = 认这类标志(合)
                if R:
                    return R
                重叠 = 合[-7:]
    except OSError:
        return None


# 「只解压缩包」模式的两条判据 —— **任一命中就不拆**（默认就是「不拆」）
# ① 内容特征（主判据，跟「后缀不可信」一致）：包里带这些，就认定它是**交付物**
不拆标志_全等 = (
    "[Content_Types].xml",      # OOXML：docx / xlsx / pptx / docm / xlsm…
    "mimetype",                 # ODF（odt/ods/odp）、epub、Krita(.kra)、ora
    "META-INF/MANIFEST.MF",     # jar（Java 程序/库）
    "AndroidManifest.xml",      # apk
    "project.json",             # sb3（Scratch 作品）
    "extension.vsixmanifest",   # vsix（VS Code 扩展）
    "EGG-INFO/PKG-INFO",        # egg（旧的 Python 包）
)
不拆标志_后缀 = (
    ".dist-info/METADATA",      # whl（Python 包：x-1.0.dist-info/METADATA）
    ".dist-info/WHEEL",
    ".nuspec",                  # nupkg（NuGet 包）
)


def 是文档包(路径):
    """这个是**交付物**、不是待拆的压缩包？**两条判据，任一命中就不拆**：
    ① **内容特征**（主）：包里带某个格式的标志文件 —— 见 `不拆标志_全等` / `不拆标志_后缀`。
    ② **后缀名单**（兜底，可在配置里手改）：见 `config` 的 `不拆后缀`。
    返回 **"内容"** ／ **"后缀"** ／ None —— **说中是哪一条要写进日志**，别让使用者猜。

    ⚠️ 为什么要有这条：`.docx`／`.xlsx`／`.jar`／`.apk` 这类**本身就是 zip**，不挡住的话
       「自动续解」会把它们**拆成零件、本体没了**。
    ⚠️ **魔数分不出它们**：实测 docx / jar / apk / whl / sb3 / 普通 zip 的**头 4 字节全是 `504b0304`**，
       一模一样 —— 所以「只解真压缩包」**不可能只看头几个字节**，必须再看包里装的是什么。
    ⚠️ 两条判据**故意并存、各自有漏**，所以取「或」：内容判据漏 `.jar`／`.apk`／`.whl`／`.sb3`／`.vsix`
       （实测 8 种漏 5 种）；后缀判据漏「被改名的分享包」。**两条都不中才拆** ——
       拿不准就不拆（2026-10-04 用户裁：「**默认不拆**，你不拆的用户大概不会怪你的」）。"""
    if 不拆后缀:
        尾 = os.path.splitext(路径)[1].lower()
        if 尾 and 尾 in [s.lower() for s in 不拆后缀]:
            return "后缀"
    try:
        with zipfile.ZipFile(路径, "r") as z:
            名们 = set(z.namelist())
    except Exception:
        return None
    for 标 in 不拆标志_全等:
        if 标 in 名们:
            return "内容"
    for 名 in 名们:
        for 尾 in 不拆标志_后缀:
            if 名.endswith(尾):
                return "内容"
    return None


强制全拆 = False        # 入口按 `--全部拆` 设；设了就不认上面那两条判据


def 该不拆(路径):
    """要不要**故意不拆**。返回 **"内容"** / **"后缀"** / None（谁说中的，日志里要写出来）。
    唯一的出口 —— 两个调用点（入口主循环、再解一层）都走它，免得两边判据跑偏。"""
    if 强制全拆:
        return None
    return 是文档包(路径)


def 读7z列表(文):
    """把 `7z l -slt` 的文本拆成 [{键: 值}, …]。**第 0 块是压缩包自己**，其后才是条目。"""
    块们 = []
    当前 = None
    for 行 in 文.splitlines():
        行 = 行.rstrip()
        if not 行 or 行.startswith("-"):
            continue          # 空行与 `----------` 分隔线跳过
        if 行.startswith("Path = "):
            当前 = {"Path": 行[7:]}
            块们.append(当前)
            continue
        if 当前 is None:
            continue
        if " = " in 行:
            k, v = 行.split(" = ", 1)
            当前[k.strip()] = v.strip()
    return 块们


def 七z列一遍(路径, 密码=""):
    """**只读**地列一遍 7z/RAR。返回 (能不能列, 说明, 条目数, 解压后总字节, 有没有加密, 探针条目名, 失败因)。
    `解压后总字节` 是**所有条目 Size 之和** —— 上限熔断要在解之前拿它卡（铁律③）。
    `失败因` ∈ None / "加密头" / "打不开" / "没工具" / "超时" —— 加密头要**换密码再列一次**
    （列得出来就是对的），打不开则换多少密码都没用（残包/假头），别白试。"""
    七 = 找7z()
    if not 七:
        return False, "没找到 7z.exe（7z/RAR 要它才能解）", 0, 0, False, None, "没工具"
    try:
        r = subprocess.run([七, "l", "-slt", "-sccUTF-8", "-p" + (密码 or ""), 路径],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=600)
    except subprocess.TimeoutExpired:
        # 别把「列得慢」说成「没装 7z」—— 那句话会把排查引到完全错的方向
        return False, "7z 列目录超时（600 秒没列完）—— 包可能极大，或落在卡住的网络盘上", 0, 0, False, None, "超时"
    except Exception as e:
        return False, "7z 起不来：%s: %s" % (type(e).__name__, e), 0, 0, False, None, "没工具"
    文 = r.stdout.decode("utf-8", "replace")
    块们 = 读7z列表(文)
    # ⚠️ **空包是合法的**：0 条目的 7z/RAR 只会产生 1 个块（包自身）。
    #    拿 `len(块们) < 2` 判「列不出来」的话，空包会被错判成失败。
    if r.returncode != 0 or not 块们:
        # ⚠️ **两种失败退出码都是 2**（实测），只能靠 7z 自己那两句话分：
        #    · `Cannot open encrypted archive. Wrong password?` ⇒ 连文件名都加密了（-mhe），**换密码能开**
        #    · `Cannot open the file as [7z] archive`          ⇒ 残包／假头，**换多少密码都没用**
        if "Cannot open encrypted archive" in 文:
            return (False, "是 7z/RAR，但**连文件名都加密了**（-mhe=on），得先给对密码",
                    0, 0, True, None, "加密头")
        if "Cannot open the file as" in 文:
            return (False, "7z 也打不开它 —— 看着像 7z/RAR，其实是**残包或假头**"
                           "（7z 原话：Cannot open the file as [7z] archive）",
                    0, 0, True, None, "打不开")
        return (False, "7z 列不出内容（退出码 %d）—— 既不像加密头、也不像格式错，原话：%s"
                % (r.returncode, " / ".join(文.splitlines()[-3:])[:200]), 0, 0, True, None, "打不开")
    条目们 = [b for b in 块们[1:] if not b.get("Attributes", "").startswith("D")]
    条目们 = 条目们 or 块们[1:]
    总 = 0
    最小 = None
    最小长 = None
    for b in 条目们:
        try:
            长 = int(b.get("Size", 0) or 0)
        except ValueError:
            长 = 0
        总 += 长
        if 长 > 0 and (最小长 is None or 长 < 最小长):
            最小长 = 长
            最小 = b.get("Path")
    加密 = any(b.get("Encrypted") == "+" for b in 条目们)
    return True, "7z/RAR·%d 条" % len(条目们), len(条目们), 总, 加密, 最小, None


def 试7z密码(路径, 密码, 探针条目=None):
    """试一条密码；**真流出一个字节**才算对。只抽最小那一条，比 `7z t` 整包验一遍便宜得多。
    **永远带 `-p`**（铁律①）＋ **`-sccUTF-8`**（铁律④，否则条目名对不上）。
    🔴 **判据是「读到了字节」，不是「退出码 0」**：7z 在「条目名一个都没匹配上」时**返回 0**
       （＝"没有要处理的文件"也算成功）⇒ 只看退出码的话，改名/编码一错就**必然假命中**。"""
    七 = 找7z()
    if not 七:
        return False
    if not 探针条目:
        # ⚠️ **挑不出探针**（条目全是 0 字节／只有空文件夹）时，**不能**退回「`x -so` 整包」：
        #    那会把整包流出来却**一个字都不吐**（0 字节条目没内容）⇒ 读不到字节 ⇒ 正确密码也被判
        #    「都不对」⇒ 这个包被白白跳过。
        #    改用 `t`（整包**测试**）：这条路**看退出码是安全的** —— 不给条目名就没有
        #    「匹配不上也返回 0」那个坑（实测：密码错 rc=2、对 rc=0）。
        参 = [七, "t", "-sccUTF-8", "-bso0", "-bsp0", "-p" + (密码 or ""), 路径]
        try:
            r = subprocess.run(参, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, timeout=1800)
        except Exception:
            return False
        return r.returncode == 0
    参 = [七, "x", "-so", "-sccUTF-8", "-p" + (密码 or ""), 路径, 探针条目]
    try:
        p = subprocess.Popen(参, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
    except Exception:
        return False
    try:
        头 = p.stdout.read(1)        # 只读 1 个字节就够判「到底有没有东西出来」（也就不会把大条目读进内存）
    except Exception:
        头 = b""
    try:
        p.stdout.close()
    except Exception:
        pass
    try:
        p.terminate()
    except Exception:
        pass
    try:
        p.wait(timeout=15)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    return bool(头)


def 预检7z包(路径, 密码们, 剩余字节=None):
    """**只读**预检 7z/RAR。返回 (能不能继续, 说明, 对上的密码 or None)。
    ⚠️ 总大小**在这里先卡**：7z 整包落完才轮到我们点数，不先卡的话巨型包／压缩炸弹会把盘写满（铁律③）。"""
    候选 = [("第 %d 个密码" % 序, pwd) for 序, pwd in enumerate(密码们, 1)]

    能, 说明, 条目数, 总字节, 加密, 探针, 失败因 = 七z列一遍(路径)
    标签_命中 = None
    密码_命中 = None
    if not 能:
        if 失败因 != "加密头":
            return False, 说明, None        # 残包/假头/没工具/超时 —— 换多少密码都没用，别白试
        # **连文件名都加密了**（-mhe）：换个密码**再列一次**，能列出来 ⇒ 这条就是对的
        for 标签, pwd in 候选:
            能, 说明, 条目数, 总字节, 加密, 探针, _ = 七z列一遍(路径, pwd)
            if 能:
                标签_命中, 密码_命中 = 标签, pwd
                说明 += "·连文件名也加密"
                break
        if not 能:
            if not 候选:
                return False, "是 7z/RAR 且**连文件名都加密了**，但没给密码 ⇒ 试不了", None
            return False, "连文件名都加密了，%d 个密码全列不开" % len(候选), None
    底 = "7z/RAR %d 条，解压后 %s" % (条目数, 人读字节(总字节))
    if 剩余字节 is not None and 总字节 > 剩余字节:
        return (False, "解压后 %s，超过还剩下的额度 %s —— **不解**（7z 没法边解边停，硬解会把盘写满）"
                % (人读字节(总字节), 人读字节(剩余字节)), None)
    if 密码_命中 is not None:          # -mhe：列得出来本身就已经证明密码对了，不必再探
        return True, "%s·需密码，命中%s" % (底, 标签_命中), 密码_命中
    if not 加密:
        return True, 底 + "·无需密码", None
    if not 候选:
        return False, 底 + "·需要密码，但没提供密码", None
    for 标签, pwd in 候选:
        if 试7z密码(路径, pwd, 探针):
            return True, "%s·需密码，命中%s" % (底, 标签), pwd
    return False, "%s·需要密码，给的 %d 个都不对" % (底, len(候选)), None


def 解7z包(包路径, 目标目录, 密码们, 状态, 预设密码=None, 预设说明=None):
    """把 7z/RAR 交给 7z.exe 解。返回 (状态文本, 本条解出字节数)。
    摊平规则与 zip 那条路**完全一致**：只取文件名、不保留包内目录。
    ⚠️ 上限**必须先卡**（走 预检7z包）—— 7z 是整包落完才轮到我们点数，边解边熔断做不到。"""
    对 = 预设密码
    说明 = 预设说明 or ""
    # `条目数` 是**每个包各算各的**：不归零的话会沿用上一个 zip 包的数，日志里「平均每条」那行就错了
    状态["条目数"] = 0
    if 对 is None:
        能, 说明, 对 = 预检7z包(包路径, 密码们, 状态["上限字节"] - 状态["已解字节"])
        if not 能:
            return 说明, 0
    空表 = [0]
    try:
        r7 = 用7z解包(包路径, 目标目录, 对 or "", 状态, 空表, 引擎说明="7z/RAR ⇒ 走 7z 引擎")
    except 超限:
        # 触顶也得留标记（这条路不留的话，下次会被当成「已解过」跳过，永远解不完）
        留未完成标记(目标目录, ["累计解出触顶：本包走 7z 引擎，整包先落临时目录，写到一半就停了"], 状态)
        raise
    if r7 is None:
        return "找不到 7z.exe ⇒ 这个 7z/RAR 解不了", 0
    _好, 述, 字节 = r7
    if 空表[0]:
        述 += "；0 字节条目 %d 个（不写出）" % 空表[0]
    return 述, 字节


def 留未完成标记(目录, 原因们, 状态=None):
    """写 `_未完成.txt` ⇒ 下次跑**不许**当「已解过」跳过。返回写成功没有。
    ⚠️ 半成品**必须**留标记 —— 不留的话下次会被当成解完了，这个包就**永远解不完**。
    ⚠️ 哪条路触顶都要留：只有 zip 逐条目那条路留过，**走 7z 引擎那条漏了**。"""
    if 状态 is not None:
        状态["本包未完成"] = True
    try:
        with io.open(os.path.join(目录, "_未完成.txt"), "w", encoding="utf-8") as m:
            m.write("⚠️ 这个包**没解完**，下面写原因。\n")
            m.write("下次跑会自动把它挪走重做，不用你管。\n\n")
            for s in list(原因们)[:30]:
                m.write("  · %s\n" % s)
    except OSError as e:
        报("          ⚠️ 半成品标记没写成（%s）—— 下次可能被当成已解完，务必手工看一下" % e)
        return False
    return True


def 用7z解包(包路径, 目标目录, 密码, 状态, 空表, 引擎说明="ZipCrypto ⇒ 走 7z 引擎"):
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
    except OSError as e:
        return False, "建不了临时目录：%s: %s" % (type(e).__name__, e), 0
    # ⚠️ **先登记、再跑 7z**：以前是跑完才 append ⇒ 若 `subprocess.run` 抛异常（超时／起不来）
    #    就直接 return，这个临时目录**既没登记、也没人收** ⇒ 盘上留一个可能几 GB 的孤儿，
    #    而且「用完即清」的设计下**静默不清理**（2026-10-04 二审指出）。
    中间层清单.append(临时)                    # 临时目录也算过程产物，收尾一并收走
    try:
        r = subprocess.run([七, "x", "-y", "-bso0", "-bsp0", "-p" + 密码,
                            "-o" + 临时, 包路径],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
    except subprocess.TimeoutExpired:
        # 别把「解得太久」说成「7z 起不来」—— 那句话会把排查引到完全错的方向
        return False, "7z 解超时（3600 秒没解完，包可能极大）—— 临时目录已登记，收尾会一并收走", 0
    except Exception as e:
        return False, "7z 起不来：%s: %s" % (type(e).__name__, e), 0
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
    return True, "（%s）写出 %d 个，%s" % (引擎说明, 写, 人读字节(本包)), 本包



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
    # 7z / RAR：整条交给 7z.exe（按**内容**判定，不看后缀）
    if 是7z或rar(包路径):
        return 解7z包(包路径, 目标目录, 密码们, 状态, 预设密码, 预设说明)
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
                    # 🔴 **先确认真有 7z，再关 zf**：以前是「先 zf.close()、再看 用7z解包 返回 None」——
                    #    可那时 zf 已经关了，下面 `zf.setpassword` / `zf.open` 作用在**已关闭**的对象上
                    #    ⇒ 抛异常被兜住 ⇒ 没装 7z 的机器上凡 ZipCrypto 包**一律报错**，
                    #    而注释与 README 承诺的「慢但能解」回退**根本不存在**。
                    if 找7z():
                        # ⚠️ **上限必须现在卡**：7z 是**整包先落进临时目录**、之后才轮到我们逐文件点数，
                        #    不像纯 Python 那条路能边解边熔断。不先卡 ⇒ 巨型包／压缩炸弹先把盘写满。
                        总需 = sum(int(getattr(i, "file_size", 0) or 0) for i in 条目们)
                        剩 = 状态["上限字节"] - 状态["已解字节"]
                        if 总需 > 剩:
                            return ("包内合计 %s，超过还剩下的额度 %s —— **不解**（ZipCrypto 得交给 7z，"
                                    "而 7z 没法边解边停，硬解会把盘写满）"
                                    % (人读字节(总需), 人读字节(剩))), 0
                        zf.close()
                        空表 = [0]      # 用 list 承接「0 字节条目几个」这个出参
                        try:
                            r7 = 用7z解包(包路径, 目标目录, 对, 状态, 空表)
                        except 超限:
                            # 触顶也得留标记（这条路以前不留 —— 下次会被当成「已解过」跳过，永远解不完）
                            留未完成标记(目标目录, ["累计解出触顶：本包走 7z 引擎，整包先落临时目录，"
                                                    "写到一半就停了"], 状态)
                            raise
                        if r7 is not None:
                            _好, 述7, 字节7 = r7
                            if 空表[0]:
                                述7 += "；0 字节条目 %d 个（不写出）" % 空表[0]
                            return 述7, 字节7
                        # r7 是 None ⇒ `用7z解包` 里那次 找7z() 没找到（与上面这次结果不一致）。
                        # ⚠️ 此时 `zf` **已经关了** ⇒ 绝不能落到下面拿它 setpassword/open。
                        return "7z 引擎这次没找到（前后两次结果不一致？）⇒ 这个包没解", 0
                    else:
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
                    留未完成标记(目标目录, ["累计解出触顶，本包解到「%s」为止，后面没解。" % 名字], 状态)
                    raise
                except Exception as e:
                    跳过.append("%s（%s）" % (名字, e))
                    未完成原因.append("条目「%s」出错：%s: %s" % (名字, type(e).__name__, e))

            # ⚠️ 半成品要**留标记** —— 不然下次「已解过就跳过」会把它当成解完了，这个包就**永远解不完**。
            #    （容易漏：只在容量触顶时写标记不够 —— 超时/出错也必须写，否则这个包永远解不完）
            if 未完成原因:
                if 留未完成标记(目标目录, 未完成原因, 状态):
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



def 收走一个(路径, 状态):
    """把一个**本轮解出来、且已经解开过**的中间层包**当场删掉**，并把它的字节从
    `已解字节` 里**还回去**。返回收走的字节数。
    返回后保证「已解字节」＝**当前盘上还占着的量**（上限要防的正是"盘被撑爆"，不是"一共写出过多少"）。

    ⚠️ **只对「本轮计数过」的中间层调用** —— 别拿去删上一轮留下的半成品目录：
       那些字节不在本轮的 `已解字节` 里，减了就把账搞乱。
    ⚠️ 为什么从「挪去 _tmp 攒着、包解完再一起删」改成「当场删」：
       5 层套娃时**4 个中间包会同时躺在盘上**（实测过程产物 16～26 MB），
       峰值因此是「最终产物 ＋ 全部中间层」；过一个删一个 ⇒ 峰值掉到「最终产物 ＋ 一个」。
       「中间过程集中到 _tmp」那条约定的**目的**是别让中间产物混进成品，**直接删掉同样达到目的、峰值更低**。"""
    if not os.path.exists(路径):
        return 0
    if os.path.isdir(路径):
        大小 = 量目录(路径)[0]
    else:
        try:
            大小 = os.path.getsize(路径)
        except OSError:
            大小 = 0
    try:
        if os.path.isdir(路径):
            shutil.rmtree(路径)
        else:
            os.remove(路径)
    except OSError as e:
        报("          ⚠️ 中间层当场没删掉（%s）—— 先留着，收尾再试" % e)
        return 0
    if 大小:
        # **还回去** —— 文件已经不在盘上了，就不该再占着额度
        状态["已解字节"] = max(0, 状态["已解字节"] - 大小)
        状态["已收走字节"] = 状态.get("已收走字节", 0) + 大小
        # 统计照记（`过程产物` 那行报的是「一共经手过多少中间层」，**不是「现在还剩多少」**）——
        # 不记的话它会报 0，看着像"没有中间层"，而实际是一路收走的
        中间层统计["个数"] += 1
        中间层统计["字节"] += 大小
    return 大小


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
    文档包们 = []
    for p in 待办:
        if not os.path.isfile(p):
            continue
        内层形 = 是7z或rar(p)          # 7z/RAR 与 zip 一样算「真身是压缩包」
        if not 内层形 and not zipfile.is_zipfile(p):
            # ⚠️ 认不出来**也要在日志里交代它到底是什么** —— 不能默默 continue，
            #    否则「内层没解开」在日志里查不到任何原因（问题该在日志里，不该回头问使用者）
            什么 = 认头部(p)
            if any(k in 什么 for k in 带压缩包味的):
                别的压缩包.append("%s  =  %s" % (os.path.relpath(p, 目录), 什么))
            continue
        因 = 该不拆(p)
        if 因:
            # 交付物（docx/xlsx/jar/apk…）**不往里解** —— 它本身就是 zip，拆开只会得到一堆零件
            文档包们.append("%s  =  %s（判据：%s）" % (os.path.relpath(p, 目录), 认头部(p), 因))
            continue
        名 = os.path.basename(p)
        # 只嵌一层：内层的内容**摊进同一个包目录**（不再套子目录）
        述, 字节 = 解一个包(p, 目录, 密码们, 状态)
        报("        ↳ 内层 %s → %s" % (os.path.relpath(p, 目录), 述))
        行们.append((标签 + " / 内层 " + os.path.relpath(p, 目录), 名, 述, 字节))
        # ★ **当场收走**（不再攒到包解完）—— 峰值从「最终产物＋全部中间层」降到「＋一个」
        收收 = 收走一个(p, 状态)
        if 收收:
            报("          （内层压缩包已当场收走，%s —— 额度已还回）" % 人读字节(收收))
        解了 += 1
        再解一层(目录, 剩余 - 1, 密码们, 状态, 行们, 标签 + " / " + 名)
    # 本层结论写清楚：看了几个、解了几个、剩下那些为什么没解
    报("        · 本层看过 %d 个文件，其中 %d 个真身是压缩包（zip/7z/RAR，已解）" % (len(待办), 解了))
    if 文档包们:
        # 出声，别默默跳过 —— 使用者得知道「这个没拆是**故意的**」
        报("        · 另有 %d 个是**交付物**（docx/xlsx/jar/apk… 本身就是 zip），**故意不拆**"
           "（拆了只会得到一堆零件）：" % len(文档包们))
        for s in 文档包们[:10]:
            报("            %s" % s)
        if len(文档包们) > 10:
            报("            …还有 %d 个" % (len(文档包们) - 10))
    if 别的压缩包:
        报("        · ⚠️ 另有 %d 个**是压缩包但不是 zip**，本工具认不出、开不了 —— 认得的都列在这："
           % len(别的压缩包))
        for s in 别的压缩包[:10]:
            报("            %s" % s)
        if len(别的压缩包) > 10:
            报("            …还有 %d 个" % (len(别的压缩包) - 10))


# 注：密码框不是独立窗口 —— 已**并进 `选后缀和密码()` 一个窗口**，少一次来回。


